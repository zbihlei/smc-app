# -*- coding: utf-8 -*-
"""
Журнал угод для Rulebound (етап 2).

- Локальна база SQLite (data/journal.db): угода №n = рядок n+2 аркуша «Журнал» у Google Таблиці.
- Сигнали скринера з results/signals_log.csv (згруповані за парою, напрямком і зоною).
- Автоперевірка угоди: R:R, вхід від середини зони, стоп за зоною, відхилення від рівнів скринера.
- Дублювання в Google Таблицю через Apps Script (Code.gs v2): listTrades / saveTrade / clearTrade.
- Резервні копії: CSV і копія бази в data/backup після кожної зміни.

Лише стандартна бібліотека (+ requests, якщо є: з ним надійніші HTTPS-сертифікати на Mac).
"""
import csv
import datetime as dt
import json
import os
import re
import sqlite3
import threading
import time

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

YES_NO = ("так", "ні", "")
MANAGEMENT = ("Без змін", "BE на 3R", "BE на 3R + частина на 4R", "Закрив самостійно достроково")
ZONE_TYPES = ("4h OB", "Локальний 1h OB", "Підтримка (тіні)")
SHEET_FIELDS = ["date", "instrument", "dir", "entry", "stop", "target", "rules", "result", "management", "choch",
                "note", "zone_type", "support4h", "mfe", "by_screener"]
LOCAL_FIELDS = ["risk", "signal_id", "signal_json", "checks_json"]
ALIASES = {"ZCASH": "ZEC", "SOLANA": "SOL", "CARDANO": "ADA", "CHAINLINK": "LINK", "DOGECOIN": "DOGE",
           "POLKADOT": "DOT", "RIPPLE": "XRP", "BITCOIN": "BTC", "ETHEREUM": "ETH", "INJECTIVE": "INJ"}

_db_lock = threading.Lock()


# ----------------------------------------------------------------------------
# Дрібні утиліти
# ----------------------------------------------------------------------------
def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def num(x):
    """Число з поля форми чи таблиці: '1,231' -> 1.231; '' / None -> None."""
    if x is None:
        return None
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if s in ("", "-", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"«{x}» — не число")


def parse_date(x):
    """'2026-11-04' / '04.11.2026' / '4.11.2026' -> '2026-11-04'."""
    if x is None or str(x).strip() == "":
        return None
    s = str(x).strip()[:10]
    for f in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return dt.datetime.strptime(s, f).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"дата «{x}» не розпізнана (потрібно ДД.ММ.РРРР)")


def base_of(sym):
    s = re.sub(r"[^A-Za-z0-9]", "", str(sym or "")).upper()
    if s.endswith("USDT") and len(s) > 4:
        s = s[:-4]
    return ALIASES.get(s, s)


def fmt(x):
    """Ціна в українському форматі: 0.07533 -> '0,07533'."""
    if x is None:
        return "-"
    return f"{x:.6g}".replace(".", ",")


def c2(x):
    return f"{x:.2f}".replace(".", ",")


def cg(x):
    return f"{x:g}".replace(".", ",")


def pct(x):
    return c2(x) + "%"


def clean_instrument(x):
    """'WLDUSDT' / 'wld/usdt' -> 'WLD', як у решті журналу. Назви на кшталт 'Zcash' не чіпає."""
    s = str(x or "").strip()
    m = re.fullmatch(r"([A-Za-z0-9]+?)[\s/_-]*USDT(\.P)?", s, re.I)
    return m.group(1).upper() if m else s


def rr_of(entry, stop, target):
    if None in (entry, stop, target) or entry == stop:
        return None
    return round(abs(target - entry) / abs(entry - stop), 2)


# ----------------------------------------------------------------------------
# База
# ----------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
  n INTEGER PRIMARY KEY,
  date TEXT, instrument TEXT NOT NULL, dir TEXT,
  entry REAL, stop REAL, target REAL,
  rules TEXT DEFAULT '', result REAL, management TEXT DEFAULT '', choch TEXT DEFAULT '', note TEXT DEFAULT '',
  zone_type TEXT DEFAULT '', support4h TEXT DEFAULT '', mfe REAL, by_screener TEXT DEFAULT '',
  risk REAL DEFAULT 1, signal_id TEXT, signal_json TEXT, checks_json TEXT,
  source TEXT DEFAULT 'app', sheet_status TEXT DEFAULT 'pending', sheet_error TEXT, sheet_synced_at TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS lists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,   -- номер видаленого списку не використовується вдруге
  name TEXT NOT NULL, market TEXT NOT NULL, mode TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active', sheet INTEGER NOT NULL DEFAULT 0,
  created_at TEXT, archived_at TEXT
);
"""

# Списки журналу: 4 розділи (ринок × режим). У кожному розділі — щонайбільше один поточний список, решта в архіві.
MARKETS = {"crypto": "Крипто", "forex": "Форекс"}
MODES = {"paper": "Пейпер", "live": "Лайв"}
SECTIONS = [(m, md) for m in MARKETS for md in MODES]
FIRST_LIST = "Перший список"   # назва списку, куди при оновленні потрапляють угоди, що вже були в журналі


def section_label(market, mode):
    return f"{MARKETS.get(market, market)} · {MODES.get(mode, mode)}"


def clean_list_name(name):
    s = re.sub(r"\s+", " ", str(name or "")).strip()
    if not s:
        raise ValueError("впиши назву списку")
    if len(s) > 60:
        raise ValueError("назва списку — до 60 символів")
    return s


class Journal:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "journal.db")
        self.backup_dir = os.path.join(data_dir, "backup")
        os.makedirs(data_dir, exist_ok=True)
        self.migrate()

    def _con(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def migrate(self):
        """Схема бази + перехід на списки (v2.1): угоди без списку потрапляють у «Крипто · Пейпер → Перший список».
        Перед зміною структури — копія бази в backup/journal_before_lists_….db. Викликається й після відновлення з копії."""
        with _db_lock:
            with self._con() as con:
                con.executescript(SCHEMA)
                cols = [r[1] for r in con.execute("PRAGMA table_info(trades)")]
                has_trades = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] > 0
            if "list_id" not in cols:
                if has_trades:
                    os.makedirs(self.backup_dir, exist_ok=True)
                    self._copy_db(os.path.join(self.backup_dir, f"journal_before_lists_{dt.datetime.now():%Y-%m-%d_%H%M%S}.db"))
                with self._con() as con:
                    con.execute("ALTER TABLE trades ADD COLUMN list_id INTEGER")
            with self._con() as con:
                if con.execute("SELECT COUNT(*) FROM trades WHERE list_id IS NULL").fetchone()[0]:
                    lid = self._active_id(con, "crypto", "paper")
                    if lid is None:
                        lid = con.execute("INSERT INTO lists (name, market, mode, status, sheet, created_at) VALUES (?,?,?,?,?,?)",
                                          (FIRST_LIST, "crypto", "paper", "active", 1, now_iso())).lastrowid
                    con.execute("UPDATE trades SET list_id=? WHERE list_id IS NULL", (lid,))

    # ---------------- списки
    @staticmethod
    def _active_id(con, market, mode):
        r = con.execute("SELECT id FROM lists WHERE market=? AND mode=? AND status='active' ORDER BY id DESC LIMIT 1",
                        (market, mode)).fetchone()
        return r[0] if r else None

    def lists(self):
        """Усі списки з короткою статистикою (угод, закритих, накопичено, період)."""
        with self._con() as con:
            rows = con.execute("""SELECT l.*, COUNT(t.n) AS count, COUNT(t.result) AS closed,
                                         ROUND(COALESCE(SUM(t.result), 0), 2) AS cum, MIN(t.date) AS first_date, MAX(t.date) AS last_date
                                  FROM lists l LEFT JOIN trades t ON t.list_id = l.id GROUP BY l.id ORDER BY l.id""").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["section"] = section_label(d["market"], d["mode"])
            d["sheet"] = bool(d["sheet"])
            out.append(d)
        return out

    def get_list(self, lid):
        try:
            lid = int(lid)
        except (TypeError, ValueError):
            return None
        return next((x for x in self.lists() if x["id"] == lid), None)

    def active_list(self, market, mode):
        with self._con() as con:
            lid = self._active_id(con, market, mode)
        return self.get_list(lid) if lid else None

    def _retire(self, con, lid):
        """Поточний список іде в архів; порожній — просто видаляється (у ньому нічого берегти)."""
        if con.execute("SELECT COUNT(*) FROM trades WHERE list_id=?", (lid,)).fetchone()[0]:
            con.execute("UPDATE lists SET status='archived', archived_at=? WHERE id=?", (now_iso(), lid))
            return "archived"
        con.execute("DELETE FROM lists WHERE id=?", (lid,))
        return "deleted"

    def create_list(self, name, market, mode):
        """Новий поточний список розділу. Попередній поточний — в архів (порожній видаляється)."""
        name = clean_list_name(name)
        if market not in MARKETS or mode not in MODES:
            raise ValueError("невідомий розділ журналу")
        retired = None
        with _db_lock, self._con() as con:
            old = self._active_id(con, market, mode)
            if old is not None:
                retired = {"id": old, "name": con.execute("SELECT name FROM lists WHERE id=?", (old,)).fetchone()[0],
                           "how": self._retire(con, old)}
            lid = con.execute("INSERT INTO lists (name, market, mode, status, created_at) VALUES (?,?,?,?,?)",
                              (name, market, mode, "active", now_iso())).lastrowid
        return lid, retired

    def rename_list(self, lid, name):
        name = clean_list_name(name)
        with _db_lock, self._con() as con:
            if not con.execute("UPDATE lists SET name=? WHERE id=?", (name, int(lid))).rowcount:
                raise ValueError("списку не знайдено")

    def archive_list(self, lid):
        """«Завершити» без нового списку. Повертає 'archived' або 'deleted' (порожній)."""
        with _db_lock, self._con() as con:
            r = con.execute("SELECT status FROM lists WHERE id=?", (int(lid),)).fetchone()
            if not r:
                raise ValueError("списку не знайдено")
            if r[0] != "active":
                raise ValueError("список уже в архіві")
            return self._retire(con, int(lid))

    def unarchive_list(self, lid):
        """Повернути з архіву: стає поточним; той, що був поточним у розділі, іде в архів (порожній — видаляється)."""
        with _db_lock, self._con() as con:
            r = con.execute("SELECT market, mode, status FROM lists WHERE id=?", (int(lid),)).fetchone()
            if not r:
                raise ValueError("списку не знайдено")
            if r[2] == "active":
                raise ValueError("список і так поточний")
            retired = None
            old = self._active_id(con, r[0], r[1])
            if old is not None:
                retired = {"id": old, "name": con.execute("SELECT name FROM lists WHERE id=?", (old,)).fetchone()[0],
                           "how": self._retire(con, old)}
            con.execute("UPDATE lists SET status='active', archived_at=NULL WHERE id=?", (int(lid),))
        return retired

    def sheet_list(self):
        """Список, який дублюється в Google Таблицю (лише один — той, куди потрапили угоди до появи списків)."""
        with self._con() as con:
            r = con.execute("SELECT id FROM lists WHERE sheet=1 ORDER BY id LIMIT 1").fetchone()
            if r:
                return r[0]
        with _db_lock, self._con() as con:
            lid = self._active_id(con, "crypto", "paper")
            if lid is None:
                lid = con.execute("INSERT INTO lists (name, market, mode, status, created_at) VALUES (?,?,?,?,?)",
                                  (FIRST_LIST, "crypto", "paper", "active", now_iso())).lastrowid
            con.execute("UPDATE lists SET sheet=1 WHERE id=?", (lid,))
            return lid

    # ---------------- читання
    def rows(self, list_id=None):
        with self._con() as con:
            if list_id is None:
                return [dict(r) for r in con.execute("SELECT * FROM trades ORDER BY n")]
            return [dict(r) for r in con.execute("SELECT * FROM trades WHERE list_id=? ORDER BY n", (list_id,))]

    def get(self, n):
        with self._con() as con:
            r = con.execute("SELECT * FROM trades WHERE n=?", (n,)).fetchone()
            return dict(r) if r else None

    def list(self, list_id=None):
        """Угоди списку з похідними колонками, як у таблиці: № у списку (ln), R:R план, накопичено, просадка від піку,
        серія збитків. Без list_id — усі угоди (для резервної CSV), тоді похідні рахуються в межах кожного списку."""
        out, acc = [], {}
        for t in self.rows(list_id):
            a = acc.setdefault(t["list_id"], {"ln": 0, "cum": 0.0, "peak": 0.0, "streak": 0})
            a["ln"] += 1
            t["ln"] = a["ln"]
            cum, peak, streak = a["cum"], a["peak"], a["streak"]
            t["rr_plan"] = rr_of(t["entry"], t["stop"], t["target"])
            if t["result"] is None:
                t["cum"] = t["dd"] = t["streak"] = None
            else:
                cum = round(cum + t["result"], 4)
                peak = max(peak, cum)
                streak = streak + 1 if t["result"] < 0 else 0
                t["cum"], t["dd"], t["streak"] = round(cum, 2), round(peak - cum, 2), streak
            a.update(cum=cum, peak=peak, streak=streak)
            for k in ("signal_json", "checks_json"):
                try:
                    t[k[:-5]] = json.loads(t[k]) if t[k] else None
                except ValueError:
                    t[k[:-5]] = None
            out.append(t)
        return out

    def summary(self, trades=None):
        trades = trades if trades is not None else self.list()
        closed = [t for t in trades if t["result"] is not None]
        last = closed[-1] if closed else None
        wins = [t for t in closed if t["result"] > 0]
        return {"count": len(trades), "closed": len(closed), "open": len(trades) - len(closed),
                "cum": last["cum"] if last else 0.0, "dd": last["dd"] if last else 0.0,
                "streak": last["streak"] if last else 0,
                "winrate": round(len(wins) / len(closed) * 100) if closed else None,
                "pending_sync": sum(1 for t in trades if t["sheet_status"] not in ("ok", "off"))}

    def next_n(self):
        with self._con() as con:
            m = con.execute("SELECT MAX(n) FROM trades").fetchone()[0]
        return (m or 0) + 1

    def last_n(self, list_id):
        """Внутрішній номер останньої угоди списку (видаляти можна лише її)."""
        with self._con() as con:
            return con.execute("SELECT MAX(n) FROM trades WHERE list_id=?", (list_id,)).fetchone()[0]

    # ---------------- запис
    def insert(self, t, list_id=None):
        """Нова угода в список. Внутрішній n — наскрізний (для Google Таблиці: рядок n+2), у списку показується свій №.
        Без list_id — у список, що дублюється в таблицю (як до появи списків)."""
        if list_id is None:
            list_id = self.sheet_list()
        with _db_lock, self._con() as con:
            sheet = con.execute("SELECT sheet FROM lists WHERE id=?", (list_id,)).fetchone()
            if sheet is None:
                raise ValueError("списку не знайдено")
            n = (con.execute("SELECT MAX(n) FROM trades").fetchone()[0] or 0) + 1
            t = dict(t, n=n, list_id=list_id, created_at=now_iso(), updated_at=now_iso())
            cols = ["n", "list_id"] + SHEET_FIELDS + LOCAL_FIELDS + ["source", "sheet_status", "created_at", "updated_at"]
            t.setdefault("source", "app")
            t.setdefault("sheet_status", "pending" if sheet[0] else "off")
            con.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [t.get(c) for c in cols])
        return n

    def update(self, n, t):
        cols = [c for c in SHEET_FIELDS + LOCAL_FIELDS if c in t]
        with _db_lock, self._con() as con:
            con.execute(f"UPDATE trades SET {','.join(c + '=?' for c in cols)}, updated_at=?, "
                        "sheet_status=CASE WHEN sheet_status='off' THEN 'off' ELSE 'pending' END WHERE n=?",
                        [t[c] for c in cols] + [now_iso(), n])

    def set_sync(self, n, ok, error=None):
        with _db_lock, self._con() as con:
            con.execute("UPDATE trades SET sheet_status=?, sheet_error=?, sheet_synced_at=COALESCE(?, sheet_synced_at) WHERE n=?",
                        ("ok" if ok else "error", None if ok else str(error)[:500], now_iso() if ok else None, n))

    def delete(self, n):
        with _db_lock, self._con() as con:
            con.execute("DELETE FROM trades WHERE n=?", (n,))

    # ---------------- імпорт із таблиці
    def import_sheet(self, sheet_trades):
        """sheet_trades: [{row, values: [A..T]}] з Apps Script. Угоди з таблиці перезаписують поля таблиці в базі;
        локальні поля (ризик, сигнал) зберігаються. Нічого не видаляє."""
        added = updated = 0
        problems = []
        sheet_ns = set()
        lid = self.sheet_list()
        last_sheet_cum = None
        for item in sheet_trades:
            v = list(item.get("values") or []) + [""] * 20
            row = item.get("row")
            try:
                n = int(num(v[0])) if num(v[0]) is not None else int(row) - 2
                if row and n != int(row) - 2:
                    problems.append(f"рядок {row}: № {n} не відповідає рядку (очікувався №{int(row) - 2}), беру №{int(row) - 2}")
                    n = int(row) - 2
                t = {
                    "date": parse_date(v[1]), "instrument": str(v[2]).strip(), "dir": str(v[3]).strip(),
                    "entry": num(v[4]), "stop": num(v[5]), "target": num(v[6]),
                    "rules": str(v[8]).strip(), "result": num(v[9]), "management": str(v[10]).strip(),
                    "choch": str(v[11]).strip(), "note": str(v[12]).strip(), "zone_type": str(v[16]).strip(),
                    "support4h": str(v[17]).strip(), "mfe": num(v[18]), "by_screener": str(v[19]).strip(),
                }
                if num(v[13]) is not None:
                    last_sheet_cum = num(v[13])
            except ValueError as e:
                problems.append(f"рядок {row}: {e} — пропущено")
                continue
            sheet_ns.add(n)
            existing = self.get(n)
            if existing and existing["list_id"] != lid:
                problems.append(f"рядок {row}: №{n} у додатку зайнятий угодою іншого списку — пропущено")
                continue
            with _db_lock, self._con() as con:
                if existing:
                    if existing["instrument"].strip().upper() != t["instrument"].upper():
                        problems.append(f"№{n}: у базі {existing['instrument']}, у таблиці {t['instrument']} — взято з таблиці")
                    con.execute(f"UPDATE trades SET {','.join(c + '=?' for c in SHEET_FIELDS)}, sheet_status='ok', sheet_error=NULL, "
                                "sheet_synced_at=?, updated_at=? WHERE n=?",
                                [t[c] for c in SHEET_FIELDS] + [now_iso(), now_iso(), n])
                    updated += 1
                else:
                    risk = 0.5 if re.search(r"[Рр]изик\s*0[,.]5\s*R", t["note"] or "") else 1.0
                    cols = ["n", "list_id"] + SHEET_FIELDS + ["risk", "source", "sheet_status", "sheet_synced_at", "created_at", "updated_at"]
                    vals = [n, lid] + [t[c] for c in SHEET_FIELDS] + [risk, "import", "ok", now_iso(), now_iso(), now_iso()]
                    con.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
                    added += 1
        local_only = [t["n"] for t in self.rows(lid) if t["n"] not in sheet_ns]
        s = self.summary(self.list(lid))
        return {"added": added, "updated": updated, "problems": problems, "local_only": local_only,
                "sheet_cum": last_sheet_cum, "app_cum": s["cum"],
                "cum_match": last_sheet_cum is None or abs((last_sheet_cum or 0) - s["cum"]) < 0.011}

    # ---------------- резервні копії
    CSV_HEAD = ["№", "Дата", "Інструмент", "Напрямок", "Вхід", "Стоп", "Ціль", "R:R план", "За правилами?", "Результат (R)",
                "Ведення угоди", "Був CHoCH на 1h?", "Нотатка", "Накопичено (R)", "Просадка (R)", "Серія збитків", "Тип зони",
                "Підтримка 4h поряд", "Макс. пройдений R (MFE)", "По скринеру", "Ризик (R)", "Сигнал скринера", "Таблиця",
                "Розділ", "Список"]

    def csv_rows(self, list_id=None):
        """№ — номер у списку; останні колонки — розділ і назва списку."""
        names = {x["id"]: (x["section"], x["name"]) for x in self.lists()}
        out = []
        for t in self.list(list_id):
            d = dt.date.fromisoformat(t["date"]).strftime("%d.%m.%Y") if t["date"] else ""
            out.append([t["ln"], d, t["instrument"], t["dir"], t["entry"], t["stop"], t["target"], t["rr_plan"], t["rules"],
                        t["result"], t["management"], t["choch"], t["note"], t["cum"], t["dd"], t["streak"], t["zone_type"],
                        t["support4h"], t["mfe"], t["by_screener"], t["risk"], t["signal_id"] or "", t["sheet_status"],
                        *names.get(t["list_id"], ("", ""))])
        return out

    def write_csv(self, path):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(self.CSV_HEAD)
            for r in self.csv_rows():
                w.writerow(["" if x is None else x for x in r])

    # Резервні копії (у data/backup і, за бажанням, у додаткову папку, напр. iCloud Drive):
    #   journal_latest.db / .csv        — завжди актуальний стан (після кожної зміни);
    #   journal_РРРР-ММ-ДД.db           — стан на ПОЧАТОК дня (до першої зміни цього дня), зберігаються keep_days;
    #   journal_manual_ДАТА_ЧАС.db      — кнопка «Зробити копію зараз», не видаляються;
    #   journal_before_restore_….db     — стан перед відновленням з копії, не видаляються.
    def _copy_db(self, dst_path):
        tmp = dst_path + ".tmp"
        src = self._con()
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        os.replace(tmp, dst_path)

    def _to_extra(self, extra_dir, names):
        if not extra_dir:
            return None
        import shutil
        try:
            d = os.path.abspath(os.path.expanduser(extra_dir))
            os.makedirs(d, exist_ok=True)
            for n in names:
                p = os.path.join(self.backup_dir, n)
                if os.path.exists(p):
                    shutil.copy2(p, os.path.join(d, n))
            return None
        except Exception as e:
            return f"додаткова папка: {e}"

    def daily_snapshot(self, extra_dir=None, keep_days=30):
        """Копія стану на початок дня — викликати ПЕРЕД зміною. Якщо за сьогодні вже є, нічого не робить."""
        try:
            os.makedirs(self.backup_dir, exist_ok=True)
            name = f"journal_{dt.date.today().isoformat()}.db"
            path = os.path.join(self.backup_dir, name)
            if os.path.exists(path) or not self.rows():
                return None
            self._copy_db(path)
            limit = time.time() - keep_days * 86400
            for n in os.listdir(self.backup_dir):
                p = os.path.join(self.backup_dir, n)
                if n.startswith("journal_20") and os.path.getmtime(p) < limit:
                    os.remove(p)
            return self._to_extra(extra_dir, [name])
        except Exception as e:  # резервна копія не має ламати збереження
            return str(e)

    def backup(self, extra_dir=None):
        """Після зміни: актуальна копія бази й CSV."""
        try:
            os.makedirs(self.backup_dir, exist_ok=True)
            self._copy_db(os.path.join(self.backup_dir, "journal_latest.db"))
            self.write_csv(os.path.join(self.backup_dir, "journal_latest.csv"))
            return self._to_extra(extra_dir, ["journal_latest.db", "journal_latest.csv"])
        except Exception as e:
            return str(e)

    def manual_backup(self, extra_dir=None):
        os.makedirs(self.backup_dir, exist_ok=True)
        name = f"journal_manual_{dt.datetime.now():%Y-%m-%d_%H%M%S}.db"
        self._copy_db(os.path.join(self.backup_dir, name))
        self.backup(extra_dir)
        err = self._to_extra(extra_dir, [name])
        return name, err

    @staticmethod
    def _count(path):
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                n, last = con.execute("SELECT COUNT(*), MAX(n) FROM trades").fetchone()
                cum = con.execute("SELECT ROUND(COALESCE(SUM(result), 0), 2) FROM trades").fetchone()[0]
            finally:
                con.close()
            return {"trades": n, "last_n": last, "cum": cum}
        except Exception:
            return {"trades": None, "last_n": None, "cum": None}

    def list_backups(self):
        out = []
        if not os.path.isdir(self.backup_dir):
            return out
        for n in os.listdir(self.backup_dir):
            if not (n.startswith("journal_") and n.endswith((".db", ".csv"))):
                continue
            p = os.path.join(self.backup_dir, n)
            st = os.stat(p)
            kind = ("latest" if "latest" in n else "manual" if "manual" in n else
                    "before_restore" if "before_restore" in n else "daily")
            item = {"name": n, "size": st.st_size, "mtime": dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                    "kind": kind, "type": n.rsplit(".", 1)[1]}
            if n.endswith(".db"):
                item.update(self._count(p))
            out.append(item)
        out.sort(key=lambda x: x["mtime"], reverse=True)
        return out

    def backup_path(self, name):
        if not re.fullmatch(r"journal_[\w.\-]+\.(db|csv)", name or ""):
            return None
        p = os.path.join(self.backup_dir, name)
        return p if os.path.isfile(p) else None

    def restore(self, name, extra_dir=None):
        """Відновлює базу з копії .db. Поточний стан спершу зберігається як journal_before_restore_….db."""
        src = self.backup_path(name)
        if not src or not name.endswith(".db"):
            raise ValueError("копію не знайдено")
        info = self._count(src)
        if info["trades"] is None:
            raise ValueError("файл копії пошкоджений або це не база журналу")
        safety = f"journal_before_restore_{dt.datetime.now():%Y-%m-%d_%H%M%S}.db"
        self._copy_db(os.path.join(self.backup_dir, safety))
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        with _db_lock:
            d = self._con()
            try:
                s.backup(d)
            finally:
                d.close()
                s.close()
        self.migrate()   # копія могла бути зроблена до появи списків
        self.backup(extra_dir)
        self._to_extra(extra_dir, [safety])
        return {"restored": name, "safety": safety, **info}


# ----------------------------------------------------------------------------
# Нормалізація угоди з форми
# ----------------------------------------------------------------------------
def normalize(raw):
    """Повертає (угода, помилки). Помилки — список рядків для форми."""
    errors, t = [], {}

    def take(key, fn, label, required=False):
        try:
            v = fn(raw.get(key))
        except ValueError as e:
            errors.append(f"{label}: {e}")
            return None
        if required and (v is None or v == ""):
            errors.append(f"{label}: обов'язкове поле")
        return v

    t["date"] = take("date", parse_date, "Дата", True)
    t["instrument"] = take("instrument", clean_instrument, "Інструмент", True)
    t["dir"] = take("dir", lambda x: str(x or "").strip(), "Напрямок", True)
    if t["dir"] and t["dir"] not in ("Long", "Short"):
        errors.append("Напрямок: Long або Short")
    for k, label in (("entry", "Вхід"), ("stop", "Стоп"), ("target", "Ціль")):
        t[k] = take(k, num, label, True)
        if t[k] is not None and t[k] <= 0:
            errors.append(f"{label}: має бути більше 0")
    t["result"] = take("result", num, "Результат (R)")
    t["mfe"] = take("mfe", num, "MFE")
    t["risk"] = take("risk", num, "Ризик") or 1.0
    if not 0 < t["risk"] <= 2:
        errors.append("Ризик: від 0 до 2 R")
    for k, label in (("rules", "За правилами"), ("choch", "CHoCH 1h"), ("support4h", "Підтримка 4h"), ("by_screener", "По скринеру")):
        v = str(raw.get(k) or "").strip()
        if v not in YES_NO:
            errors.append(f"{label}: так / ні")
        t[k] = v
    for k in ("management", "zone_type", "note"):
        t[k] = str(raw.get(k) or "").strip()
    t["signal_id"] = str(raw.get("signal_id") or "").strip() or None
    t["signal_json"] = None  # знімок сигналу підставляє сервер із логу (не з форми)
    return t, errors


def sheet_payload(t, n, overwrite=False):
    p = {"n": n, "overwrite": bool(overwrite)}
    for k in SHEET_FIELDS:
        p[k] = t.get(k)
    return p


# ----------------------------------------------------------------------------
# Сигнали скринера з signals_log.csv
# ----------------------------------------------------------------------------
_sig_cache = {"key": None, "groups": None}


def _parse_time(s):
    try:
        return dt.datetime.strptime(str(s).strip()[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def load_signals(results_dir):
    """Усі групи сигналів з логу (кеш за mtime). Група = символ + напрямок + нижня межа зони."""
    path = os.path.join(results_dir, "signals_log.csv")
    try:
        st = os.stat(path)
    except OSError:
        return [], path
    key = (path, st.st_mtime, st.st_size)
    if _sig_cache["key"] == key:
        return _sig_cache["groups"], path
    groups = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            t = _parse_time(r.get("time"))
            sym = (r.get("symbol") or "").strip()
            if not t or not sym:
                continue
            gid = f"{sym}|{r.get('dir', '')}|{r.get('zone_low', '')}"
            kind = r.get("kind") or ""
            g = groups.get(gid)
            if g is None:
                g = groups[gid] = {"id": gid, "symbol": sym, "base": base_of(sym), "dir": r.get("dir", ""),
                                   "first": t, "last": t, "scans": 0, "touched_at": None, "rejected_rows": 0,
                                   "listed_rows": 0, "history": []}
            g["first"], g["last"] = min(g["first"], t), max(g["last"], t)
            if kind.startswith("відсіяно"):
                g["rejected_rows"] += 1
            elif kind.startswith("наживо"):
                g["touched_at"] = g["touched_at"] or t
            else:
                g["listed_rows"] += 1
            try:
                lv = {"time": t, "kind": kind, "status": r.get("status", ""), "score": r.get("score", ""),
                      "price": num(r.get("price")), "zone_low": num(r.get("zone_low")), "zone_high": num(r.get("zone_high")),
                      "entry": num(r.get("entry")), "stop": num(r.get("stop")), "target": num(r.get("target")),
                      "rr": num(r.get("rr")), "struct4h": r.get("struct4h", ""), "against": r.get("against", ""),
                      "bos_time": r.get("bos_time", ""), "knife": r.get("knife", ""), "warnings": r.get("warnings", "")}
            except ValueError:
                continue
            g["history"].append(lv)
    out = []
    for g in groups.values():
        hist = g.pop("history")
        if not hist:
            continue
        g["rejected"] = g["listed_rows"] == 0 and g["touched_at"] is None
        g["_hist"] = hist
        g.update({k: v for k, v in hist[-1].items() if k not in ("time", "kind")})
        if g["rejected"] or g["touched_at"]:
            g["status"] = "відсіяно: проти структури 4h" if g["rejected"] else "ціна заходила в зону"
        out.append(g)
    out.sort(key=lambda g: g["last"], reverse=True)
    _sig_cache.update(key=key, groups=out)
    return out, path


def signal_public(g, at=None):
    """Сигнал для інтерфейсу/збереження (рівні — останні до моменту at, якщо задано)."""
    hist = g["_hist"]
    lv = hist[-1]
    if at:
        before = [h for h in hist if h["time"] <= at]
        lv = before[-1] if before else hist[0]
    fmt_t = lambda x: x.strftime("%Y-%m-%d %H:%M") if x else None
    return {"id": g["id"], "symbol": g["symbol"], "base": g["base"], "dir": g["dir"],
            "first": fmt_t(g["first"]), "last": fmt_t(g["last"]), "touched_at": fmt_t(g["touched_at"]),
            "scans": g["listed_rows"], "rejected": g["rejected"], "levels_time": fmt_t(lv["time"]),
            "zone_low": lv["zone_low"], "zone_high": lv["zone_high"], "entry": lv["entry"], "stop": lv["stop"],
            "target": lv["target"], "rr": lv["rr"], "score": lv["score"], "status": g.get("status") or lv["status"],
            "struct4h": lv["struct4h"], "against": lv["against"], "bos_time": lv["bos_time"], "knife": lv["knife"],
            "warnings": lv["warnings"]}


def recent_signals(results_dir, days=7, include_rejected=False, limit=300):
    groups, path = load_signals(results_dir)
    since = dt.datetime.now() - dt.timedelta(days=days)
    out = [signal_public(g) for g in groups if g["last"] >= since and (include_rejected or not g["rejected"])]
    return out[:limit], path


def find_signal(results_dir, sid, trade_date=None):
    groups, _ = load_signals(results_dir)
    for g in groups:
        if g["id"] == sid:
            at = None
            if trade_date:
                at = dt.datetime.fromisoformat(trade_date) + dt.timedelta(hours=23, minutes=59)
            return signal_public(g, at)
    return None


def find_matches(results_dir, t, limit=3):
    """Сигнали скринера, схожі на ручну угоду: та сама монета й напрямок, вхід біля зони, дати поруч."""
    if not t.get("instrument") or not t.get("dir") or t.get("entry") is None or not t.get("date"):
        return []
    groups, _ = load_signals(results_dir)
    base = base_of(t["instrument"])
    d = dt.date.fromisoformat(t["date"])
    out = []
    for g in groups:
        if g["base"] != base or g["dir"] != t["dir"]:
            continue
        if not (g["first"].date() - dt.timedelta(days=1) <= d <= g["last"].date() + dt.timedelta(days=3)):
            continue
        lv = g["_hist"][-1]
        if lv["zone_low"] is None or not (lv["zone_low"] * 0.99 <= t["entry"] <= lv["zone_high"] * 1.01):
            continue
        out.append(signal_public(g, dt.datetime.combine(d, dt.time(23, 59))))
    return out[:limit]


# ----------------------------------------------------------------------------
# Автоперевірка угоди
# ----------------------------------------------------------------------------
ORDER = {"error": 0, "warn": 1, "info": 2, "ok": 3}


def check_trade(t, sig=None, matches=None, market="crypto"):
    items = []
    add = lambda level, text: items.append({"level": level, "text": text})
    e, s, tg, d = t.get("entry"), t.get("stop"), t.get("target"), t.get("dir")
    res = {"items": items, "rr": None, "stop_pct": None, "rules_suggest": None, "rules_reasons": [], "matches": matches or []}
    if None in (e, s, tg) or d not in ("Long", "Short"):
        add("info", "Заповни напрямок, вхід, стоп і ціль — тоді з'явиться перевірка.")
        return res
    if (d == "Long" and not (s < e < tg)) or (d == "Short" and not (tg < e < s)):
        add("error", "Для Long стоп має бути нижче входу, а ціль вище." if d == "Long" else
            "Для Short стоп має бути вище входу, а ціль нижче.")
        return res
    risk_dist = abs(e - s)
    rr = abs(tg - e) / risk_dist
    stop_pct = risk_dist / e * 100
    res.update(rr=round(rr, 2), stop_pct=round(stop_pct, 3))
    rules_ok, reasons = True, []
    if rr < 3:
        add("warn", f"R:R план {c2(rr)} < 3 — нижче мінімуму стратегії (урок №20).")
        rules_ok = False
        reasons.append("R:R < 3")
    else:
        add("ok", f"R:R план {c2(rr)} ≥ 3.")
    if stop_pct < 1.5 and market != "forex":   # на форексі стопи в % завжди вузькі — підказка не про це
        add("info", f"Стоп {pct(stop_pct)} від входу — вузький (гіпотеза: збитки купчаться на стопах < 1,5%).")
    risk = t.get("risk") or 1.0
    if risk != 1:
        add("info", f"Ризик {cg(risk)}R: стоп = −{cg(risk)}R, результат вводь у R пропорційно.")
    r = t.get("result")
    if r is not None:
        if r < -risk * 1.05:
            add("warn", f"Результат {cg(r)}R більший за ризик угоди ({cg(risk)}R) — перевір.")
        if r > rr * risk * 1.05:
            add("warn", f"Результат {cg(r)}R більший за плановий R:R × ризик ({c2(rr * risk)}R) — перевір.")
    mfe = t.get("mfe")
    if mfe is not None and r is not None and mfe < r - 0.05:
        add("warn", f"MFE {cg(mfe)}R менший за результат {cg(r)}R — MFE має бути не менше результату.")

    if sig:
        zl, zh = sig.get("zone_low"), sig.get("zone_high")
        se, ss, st = sig.get("entry"), sig.get("stop"), sig.get("target")
        if sig.get("dir") and sig["dir"] != d:
            add("error", f"Напрямок угоди ({d}) не збігається із сигналом скринера ({sig['dir']}).")
        if base_of(t.get("instrument")) and base_of(t.get("instrument")) != sig.get("base"):
            add("warn", f"Інструмент «{t.get('instrument')}» не збігається із сигналом ({sig.get('symbol')}).")
        if sig.get("rejected"):
            add("warn", "Сигнал був відсіяний скринером: проти структури 4h.")
            rules_ok = False
            reasons.append("проти структури 4h")
        elif sig.get("struct4h"):
            add("ok", f"Структура 4h за скринером: {sig['struct4h']}.")
        if zl is not None and zh is not None and zh > zl:
            mid = (zl + zh) / 2
            pos = (e - zl) / (zh - zl)
            if e < zl or e > zh:
                add("warn", f"Вхід {fmt(e)} поза зоною скринера {fmt(zl)}–{fmt(zh)}.")
                rules_ok = False
                reasons.append("вхід поза зоною")
            elif abs(pos - 0.5) <= 0.15:
                add("ok", f"Вхід від середини зони ({pos * 100:.0f}% висоти; середина {fmt(mid)}).")
            else:
                part = "верхній" if pos > 0.5 else "нижній"
                add("warn", f"Вхід у {part} частині зони ({pos * 100:.0f}% висоти), а не від середини {fmt(mid)}.")
                rules_ok = False
                reasons.append("вхід не від середини")
            beyond = s < zl if d == "Long" else s > zh
            if beyond:
                add("ok", "Стоп за межею зони.")
            else:
                edge = zl if d == "Long" else zh
                add("warn", f"Стоп {fmt(s)} усередині зони ({'низ' if d == 'Long' else 'верх'} зони {fmt(edge)}) — не за її межею.")
                rules_ok = False
                reasons.append("стоп усередині зони")
        if se and ss:
            ur, sr = stop_pct, abs(se - ss) / se * 100
            if ur < 0.8 * sr:
                add("warn", f"Стоп вужчий за скринерний: {pct(ur)} проти {pct(sr)} (стоп скринера {fmt(ss)}).")
            elif ur > 1.25 * sr:
                add("info", f"Стоп ширший за скринерний: {pct(ur)} проти {pct(sr)} (стоп скринера {fmt(ss)}).")
            else:
                add("ok", f"Стоп близький до скринерного ({pct(ur)} / {pct(sr)}).")
        if st:
            closer = tg < st * 0.999 if d == "Long" else tg > st * 1.001
            further = tg > st * 1.001 if d == "Long" else tg < st * 0.999
            if closer:
                add("warn", f"Ціль {fmt(tg)} ближче за скринерну {fmt(st)} (урок №20).")
            elif further:
                add("info", f"Ціль {fmt(tg)} далі за скринерну {fmt(st)}.")
        w = sig.get("warnings") or ""
        for key, text in (("ніж", "різкі свічки («ніж»): стоп можуть зняти гнотом"), ("тісний стоп", "тісний стоп"),
                          ("глибокий відкат", "глибокий відкат"), ("середина зони вже пройдена", "середина зони вже пройдена")):
            if key in w:
                add("info", f"Скринер попереджав: {text}.")
        if t.get("date") and sig.get("first"):
            td = dt.date.fromisoformat(t["date"])
            first = dt.date.fromisoformat(sig["first"][:10])
            last = dt.date.fromisoformat(sig["last"][:10])
            if td < first:
                add("warn", f"Дата угоди {td:%d.%m} раніше за першу появу сигналу ({first:%d.%m}).")
            elif td > last + dt.timedelta(days=3):
                add("info", f"Сигнал востаннє був у списку {last:%d.%m}.")
        res["rules_suggest"] = "так" if rules_ok else "ні"
    else:
        if matches:
            m = matches[0]
            add("info", f"Схоже на сигнал скринера: {m['symbol']} {m['dir']}, зона {fmt(m['zone_low'])}–{fmt(m['zone_high'])} "
                        f"({m['first'][5:16]} – {m['last'][5:16]}). Можна прив'язати.")
        else:
            add("info", "Сигнал скринера не прив'язано: вхід від середини і стоп за зоною перевір сам.")
        if not rules_ok:
            res["rules_suggest"] = "ні"
    res["rules_reasons"] = reasons
    items.sort(key=lambda i: ORDER[i["level"]])
    return res


# ----------------------------------------------------------------------------
# Google Таблиця (Apps Script Code.gs v2)
# ----------------------------------------------------------------------------
class SheetError(Exception):
    pass


_ver_cache = {"url": None, "version": None, "at": 0}
MIN_VERSION = 2      # нижче — додаток у таблицю не пише
LATEST_VERSION = 3   # v3: дата без зсуву через часові пояси


def sheet_config():
    return os.environ.get("SMC_SHEET_URL", "").strip(), os.environ.get("SMC_SHEET_TOKEN", "").strip()


def _http(method, url, body=None, timeout=40):
    if requests is not None:
        try:
            if method == "GET":
                r = requests.get(url, timeout=timeout)
            else:
                r = requests.post(url, json=body, timeout=timeout)
        except requests.RequestException as e:
            raise SheetError(f"немає зв'язку з Google ({e.__class__.__name__})")
        text, code = r.text, r.status_code
    else:  # pragma: no cover
        import urllib.request
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text, code = resp.read().decode("utf-8", "replace"), resp.status
        except Exception as e:
            raise SheetError(f"немає зв'язку з Google ({e})")
    try:
        return json.loads(text)
    except ValueError:
        hint = " Перевір, що веб-застосунок розгорнуто з доступом «Усі»." if "<html" in text[:300].lower() else ""
        raise SheetError(f"таблиця відповіла не JSON (HTTP {code}).{hint}")


def sheet_status(force=False):
    """{configured, ok, version, error} — без запису в таблицю (GET)."""
    url, token = sheet_config()
    if not url or not token:
        return {"configured": False, "ok": False, "version": None,
                "error": "не задано SMC_SHEET_URL / SMC_SHEET_TOKEN (див. README)"}
    if not force and _ver_cache["url"] == url and time.time() - _ver_cache["at"] < 600 and _ver_cache["version"]:
        return {"configured": True, "ok": _ver_cache["version"] >= MIN_VERSION, "version": _ver_cache["version"], "error": None,
                "latest": LATEST_VERSION}
    try:
        d = _http("GET", url, timeout=25)
    except SheetError as e:
        return {"configured": True, "ok": False, "version": None, "error": str(e)}
    v = d.get("version") or 1
    _ver_cache.update(url=url, version=v, at=time.time())
    if v < MIN_VERSION:
        return {"configured": True, "ok": False, "version": v,
                "error": "у Google Apps Script стара версія Code.gs: онови її (див. README, розділ «Оновлення Code.gs»)"}
    return {"configured": True, "ok": True, "version": v, "error": None, "latest": LATEST_VERSION}


def sheet_call(action, **payload):
    """Виклик дії журналу. Спершу перевіряє версію: стара Code.gs сприйняла б будь-який POST як оновлення «Скринера»."""
    st = sheet_status()
    if not st["ok"]:
        raise SheetError(st["error"])
    url, token = sheet_config()
    d = _http("POST", url, dict(payload, token=token, action=action))
    if not d.get("ok"):
        raise SheetError(d.get("error") or "таблиця повернула помилку")
    return d
