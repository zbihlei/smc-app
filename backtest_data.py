# -*- coding: utf-8 -*-
"""
Бектест, етап (a): завантаження й локальний кеш свічок Binance USDT-M (4h і 1h).

- Список монет — тими самими функціями й фільтрами, що й живий скринер (`fetch_top_symbols` із smc_screener.py):
  лише безстрокові USDT-M на крипті, без стейблів і металів, мін. обсяг 30 млн USDT/добу, топ-N за обсягом.
  Упередження «вижилих»: це топ «зараз», а не рік тому — позначаємо у звіті.
- Свічки — /fapi/v1/klines з startTime/endTime (до 1500 за запит). Лише ЗАКРИТІ свічки.
- Кеш — SQLite `data/backtest/klines.db`. Повторний запуск докачує лише те, чого бракує (з початку періоду
  до першої збереженої свічки й від останньої до «зараз»).
- Ліміти Binance: стежимо за заголовком X-MBX-USED-WEIGHT-1M; на 429/418 — пауза (Retry-After).
- 4h качаємо з «розігрівом»: на 500 свічок раніше за початок періоду (скринер дивиться на 500 свічок).
"""
import datetime as dt
import os
import sqlite3
import threading
import time

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

TF_MS = {"4h": 4 * 3600_000, "1h": 3600_000}
WARMUP_4H = 500          # свічок 4h до початку періоду (як вікно живого скринера)
LIMIT = 1500             # свічок за запит (максимум fapi)
WEIGHT_PAUSE_AT = 1800   # з 2400 ваги за хвилину — далі чекаємо нову хвилину
DEFAULT_BASE = os.environ.get("SMC_BT_FAPI", "https://fapi.binance.com")  # SMC_BT_FAPI — лише для тестів

SCHEMA = """
CREATE TABLE IF NOT EXISTS klines (
  symbol TEXT NOT NULL, tf TEXT NOT NULL, t INTEGER NOT NULL,
  o REAL, h REAL, l REAL, c REAL, v REAL,
  PRIMARY KEY (symbol, tf, t)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS symbols (
  symbol TEXT PRIMARY KEY, rank INTEGER, volume REAL, listed_ms INTEGER, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS extra_symbols (
  symbol TEXT PRIMARY KEY, added_at TEXT, source TEXT, listed_ms INTEGER
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, months INTEGER, top INTEGER,
  start_ms INTEGER, end_ms INTEGER, symbols INTEGER, requests INTEGER, candles INTEGER, errors TEXT, status TEXT
);
"""


class BinanceError(Exception):
    pass


def now_ms():
    return int(time.time() * 1000)


def fmt_ms(ms):
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%d.%m.%Y %H:%M") if ms else "–"


class Cache:
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "backtest")
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, "klines.db")
        with self._con() as con:
            con.executescript(SCHEMA)

    def _con(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def span(self, symbol, tf):
        with self._con() as con:
            return con.execute("SELECT MIN(t), MAX(t), COUNT(*) FROM klines WHERE symbol=? AND tf=?", (symbol, tf)).fetchone()

    def insert(self, symbol, tf, rows):
        with self._con() as con:
            con.executemany("INSERT OR REPLACE INTO klines VALUES (?,?,?,?,?,?,?,?)", [(symbol, tf, *r) for r in rows])

    def candles(self, symbol, tf, start_ms=None, end_ms=None):
        """[(t, o, h, l, c, v), ...] за зростанням часу."""
        q, a = "SELECT t,o,h,l,c,v FROM klines WHERE symbol=? AND tf=?", [symbol, tf]
        if start_ms is not None:
            q += " AND t>=?"; a.append(start_ms)
        if end_ms is not None:
            q += " AND t<?"; a.append(end_ms)
        with self._con() as con:
            return con.execute(q + " ORDER BY t", a).fetchall()

    def set_symbols(self, items):
        """Поточний топ. Дата лістингу (перша свічка на біржі), якщо вже відома, зберігається."""
        with self._con() as con:
            listed = dict(con.execute("SELECT symbol, listed_ms FROM symbols"))
            con.execute("DELETE FROM symbols")
            con.executemany("INSERT INTO symbols VALUES (?,?,?,?,?)",
                            [(s, i + 1, v, listed.get(s), dt.datetime.now().isoformat(timespec="seconds")) for i, (s, v) in enumerate(items)])

    def listed(self, symbol):
        with self._con() as con:
            r = con.execute("SELECT listed_ms FROM symbols WHERE symbol=? UNION ALL "
                            "SELECT listed_ms FROM extra_symbols WHERE symbol=?", (symbol, symbol)).fetchall()
            return next((x[0] for x in r if x[0] is not None), None)

    def set_listed(self, symbol, ms):
        with self._con() as con:
            con.execute("UPDATE symbols SET listed_ms=? WHERE symbol=?", (ms, symbol))
            con.execute("UPDATE extra_symbols SET listed_ms=? WHERE symbol=?", (ms, symbol))

    def add_extra(self, symbols, source="вручну"):
        """Монети поза топом, які користувач додав у бектест (напр., бували в живому топі в будні)."""
        now = dt.datetime.now().isoformat(timespec="seconds")
        with self._con() as con:
            for sym in symbols:
                con.execute("INSERT OR IGNORE INTO extra_symbols (symbol, added_at, source) VALUES (?,?,?)", (sym, now, source))

    def remove_extra(self, symbol):
        with self._con() as con:
            return con.execute("DELETE FROM extra_symbols WHERE symbol=?", (symbol,)).rowcount

    def extras(self):
        with self._con() as con:
            return [dict(zip(("symbol", "added_at", "source", "listed_ms"), r))
                    for r in con.execute("SELECT symbol, added_at, source, listed_ms FROM extra_symbols ORDER BY added_at, symbol")]

    def symbols(self):
        """Монети бектесту: поточний топ + додані вручну (extra=True)."""
        with self._con() as con:
            top = [dict(zip(("symbol", "rank", "volume", "listed_ms"), r), extra=False)
                   for r in con.execute("SELECT symbol, rank, volume, listed_ms FROM symbols ORDER BY rank")]
        have = {x["symbol"] for x in top}
        return top + [{"symbol": e["symbol"], "rank": None, "volume": None, "listed_ms": e["listed_ms"], "extra": True,
                       "source": e["source"]} for e in self.extras() if e["symbol"] not in have]

    def gaps(self, symbol, tf, start_ms=None, end_ms=None):
        """Скільки свічок бракує всередині збереженого діапазону (дірки в історії біржі чи завантаження)."""
        step = TF_MS[tf]
        ts = [r[0] for r in self.candles(symbol, tf, start_ms, end_ms)]
        return sum(max(0, (b - a) // step - 1) for a, b in zip(ts, ts[1:]))

    def summary(self, start_ms=None):
        """Підсумок кешу по монетах для інтерфейсу."""
        out = []
        listed = {s["symbol"]: s for s in self.symbols()}
        with self._con() as con:
            rows = con.execute("SELECT symbol, tf, MIN(t), MAX(t), COUNT(*) FROM klines GROUP BY symbol, tf").fetchall()
        by = {}
        for sym, tf, a, b, n in rows:
            by.setdefault(sym, {})[tf] = {"from": a, "to": b, "n": n}
        for sym in sorted(set(by) | set(listed), key=lambda s: (listed.get(s, {}).get("rank") or 999, not listed.get(s), s)):
            d = by.get(sym, {})
            info = listed.get(sym, {})
            item = {"symbol": sym, "rank": info.get("rank"), "in_top": sym in listed and not info.get("extra"),
                    "extra": bool(info.get("extra")), "source": info.get("source"), "in_list": sym in listed}
            for tf in ("4h", "1h"):
                x = d.get(tf)
                item[tf] = None if not x else {"from": x["from"], "to": x["to"], "n": x["n"],
                                               "from_txt": fmt_ms(x["from"]), "to_txt": fmt_ms(x["to"])}
            # монета з'явилась пізніше за початок періоду (менше історії)
            item["late"] = bool(start_ms and d.get("1h") and d["1h"]["from"] > start_ms + 2 * 86400_000)
            out.append(item)
        size = sum(os.path.getsize(os.path.join(self.dir, f)) for f in os.listdir(self.dir) if f.startswith("klines.db"))
        return {"symbols": out, "size_mb": round(size / 1e6, 1), "path": self.path}

    def runs(self, n=10):
        with self._con() as con:
            cols = ("id", "started_at", "finished_at", "months", "top", "start_ms", "end_ms", "symbols", "requests", "candles", "errors", "status")
            return [dict(zip(cols, r)) for r in con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (n,))]

    def run_start(self, months, top, start_ms, end_ms):
        with self._con() as con:
            cur = con.execute("INSERT INTO runs (started_at, months, top, start_ms, end_ms, status) VALUES (?,?,?,?,?,?)",
                              (dt.datetime.now().isoformat(timespec="seconds"), months, top, start_ms, end_ms, "running"))
            return cur.lastrowid

    def run_end(self, rid, **kw):
        kw["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
        with self._con() as con:
            con.execute(f"UPDATE runs SET {','.join(k + '=?' for k in kw)} WHERE id=?", [*kw.values(), rid])


class Client:
    """Мінімальний клієнт fapi з урахуванням ваги запитів і повторами."""

    def __init__(self, base=None, stop_flag=None, sleep=time.sleep):
        self.base = (base or DEFAULT_BASE).rstrip("/")
        self.requests = 0
        self.stop_flag = stop_flag or threading.Event()
        self.sleep = sleep
        self.last_weight = 0

    def get(self, path, params=None, retries=5):
        if requests is None:
            raise BinanceError("не встановлено пакет requests")
        last = "невідома помилка"
        for attempt in range(retries):
            if self.stop_flag.is_set():
                raise BinanceError("зупинено")
            try:
                r = requests.get(self.base + path, params=params, timeout=20)
            except requests.RequestException as e:
                last = f"мережа: {e.__class__.__name__}"
                self.sleep(min(30, 2 * (attempt + 1)))
                continue
            self.requests += 1
            try:
                self.last_weight = int(r.headers.get("X-MBX-USED-WEIGHT-1M") or 0)
            except ValueError:
                self.last_weight = 0
            if r.status_code in (429, 418):
                wait = int(r.headers.get("Retry-After") or 60)
                last = f"ліміт Binance (HTTP {r.status_code}), пауза {wait} с"
                self.sleep(min(wait, 300))
                continue
            if r.status_code != 200:
                raise BinanceError(f"HTTP {r.status_code}: {r.text[:150]}")
            if self.last_weight >= WEIGHT_PAUSE_AT:  # ввічливо чекаємо нову хвилину
                self.sleep(61 - time.time() % 60)
            return r.json()
        raise BinanceError(last)

    def klines(self, symbol, tf, start_ms, end_ms):
        """Генератор пачок закритих свічок [(t,o,h,l,c,v)] від start_ms до end_ms (не включно)."""
        step = TF_MS[tf]
        t = start_ms
        while t < end_ms:
            raw = self.get("/fapi/v1/klines", {"symbol": symbol, "interval": tf, "startTime": t,
                                               "endTime": end_ms - 1, "limit": LIMIT})
            if not raw:
                return
            cutoff = now_ms()
            rows = [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
                    for k in raw if int(k[6]) < cutoff and int(k[0]) < end_ms]
            if rows:
                yield rows
            nxt = int(raw[-1][0]) + step
            if nxt <= t:
                return
            t = nxt
            if len(raw) < LIMIT:
                return


def load_screener_module(screener_path):
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("smc_screener_bt", screener_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["smc_screener_bt"] = mod
    spec.loader.exec_module(mod)
    return mod


def norm_symbols(raw):
    """«dot, HBARUSDT 1000cat» -> ["DOTUSDT", "HBARUSDT", "1000CATUSDT"] (без повторів)."""
    import re
    out = []
    for x in re.findall(r"[A-Za-z0-9]+", str(raw or "")):
        x = x.upper()
        if not x.endswith("USDT"):
            x += "USDT"
        if x not in out and len(x) <= 24:
            out.append(x)
    return out


def check_symbols(screener_path, symbols, base=None):
    """Які з монет є серед безстрокових USDT-M на крипті — тим самим фільтром, що в скринері. (good, bad)"""
    mod = load_screener_module(screener_path)
    mod.MKT["bases"] = [(base or DEFAULT_BASE).rstrip("/")]
    tradable, _ = mod.fetch_tradable()
    if tradable is None:
        raise BinanceError("не вдалося отримати список пар з Binance (exchangeInfo)")
    skip = set(getattr(mod, "STABLES", ())) | set(getattr(mod, "NON_CRYPTO", ()))
    good = [s for s in symbols if s in tradable and s[:-4] not in skip]
    return good, [s for s in symbols if s not in good]


def top_symbols(screener_path, n, min_volume_musd=30.0, base=None):
    """Топ-N тими самими функціями й фільтрами, що й живий скринер. Повертає [(symbol, quoteVolume)]."""
    mod = load_screener_module(screener_path)
    mod.MKT["bases"] = [(base or DEFAULT_BASE).rstrip("/")]
    syms = mod.fetch_top_symbols(n, min_volume_musd * 1e6)
    vols = {t["symbol"]: float(t.get("quoteVolume") or 0) for t in mod.http_get(mod.MKT["ticker"])}
    return [(s, vols.get(s, 0.0)) for s in syms]


def period(months, end_ms=None):
    """(start_ms, end_ms): кінець — початок поточної 4h-свічки (лише закриті), початок — на months місяців раніше."""
    end_ms = end_ms or now_ms()
    end_ms -= end_ms % TF_MS["4h"]
    start_ms = end_ms - int(months * 30.4375 * 86400_000)
    start_ms -= start_ms % TF_MS["4h"]  # теж на межі 4h-свічки, інакше «голову» щоразу перепитуємо
    return start_ms, end_ms


class Downloader:
    """Фонове завантаження: progress() — для інтерфейсу; stop() — зупинити (кеш лишається, наступний запуск докачає)."""

    def __init__(self, data_dir, screener_path, months=12, top=100, min_volume=30.0, base=None, sleep=time.sleep, add=None,
                 add_source="вручну"):
        self.add = add            # список монет для режиму «додати в кеш» (без оновлення топу)
        self.add_source = add_source
        self.cache = Cache(data_dir)
        self.screener_path = screener_path
        self.months, self.top, self.min_volume = months, top, min_volume
        self.base = base
        self.stop_flag = threading.Event()
        self.client = Client(base, self.stop_flag, sleep)
        self.state = {"status": "idle", "phase": "", "symbol": "", "done": 0, "total": 0, "candles": 0,
                      "errors": [], "started_at": None, "finished_at": None, "eta_s": None}
        self.thread = None

    def progress(self):
        st = dict(self.state, requests=self.client.requests, weight=self.client.last_weight)
        st["errors"] = list(self.state["errors"])[-20:]
        return st

    def start(self):
        if self.thread and self.thread.is_alive():
            return False
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.stop_flag.set()

    def _run(self):
        st = self.state
        st.update(status="running", phase="список монет", errors=[], done=0, total=0, candles=0, symbol="",
                  started_at=dt.datetime.now().isoformat(timespec="seconds"), finished_at=None, eta_s=None)
        start_ms, end_ms = period(self.months)
        rid = self.cache.run_start(self.months, -len(self.add) if self.add else self.top, start_ms, end_ms)
        t0 = time.time()
        try:
            if self.add:
                st["phase"] = "перевірка монет"
                good, bad = check_symbols(self.screener_path, self.add, self.base)
                for b in bad:
                    st["errors"].append(f"{b}: немає серед безстрокових USDT-M на крипті (Binance)")
                if not good:
                    raise BinanceError("жодної монети не додано")
                self.cache.add_extra(good, self.add_source)
                items = [(x, None) for x in good]
            else:
                items = top_symbols(self.screener_path, self.top, self.min_volume, self.base)
                if not items:
                    raise BinanceError("порожній список монет")
                self.cache.set_symbols(items)
                have = {x for x, _ in items}
                items += [(e["symbol"], None) for e in self.cache.extras() if e["symbol"] not in have]  # додані вручну — теж докачуємо
            st.update(total=len(items), phase="свічки")
            warm = start_ms - WARMUP_4H * TF_MS["4h"]
            for i, (sym, _) in enumerate(items):
                if self.stop_flag.is_set():
                    break
                st["symbol"] = sym
                try:
                    for tf, a in (("4h", warm), ("1h", start_ms)):
                        self._fill(sym, tf, a, end_ms)
                    # якщо біржа не дала свічок раніше за першу збережену — це дата лістингу; наступного разу не питаємо
                    if self._head_empty and self.cache.listed(sym) is None:
                        self.cache.set_listed(sym, min(x for x in (self.cache.span(sym, "4h")[0], self.cache.span(sym, "1h")[0]) if x))
                except BinanceError as e:
                    if self.stop_flag.is_set():
                        break
                    st["errors"].append(f"{sym}: {e}")
                st["done"] = i + 1
                el = time.time() - t0
                st["eta_s"] = round(el / st["done"] * (st["total"] - st["done"])) if st["done"] else None
            st["status"] = "stopped" if self.stop_flag.is_set() else ("done_errors" if st["errors"] else "done")
        except Exception as e:
            st["errors"].append(str(e))
            st["status"] = "stopped" if self.stop_flag.is_set() else "error"
        finally:
            st.update(phase="", symbol="", finished_at=dt.datetime.now().isoformat(timespec="seconds"), eta_s=None)
            self.cache.run_end(rid, symbols=st["done"], requests=self.client.requests, candles=st["candles"],
                               errors="\n".join(st["errors"])[:4000], status=st["status"])

    def _fill(self, sym, tf, start_ms, end_ms):
        """Докачати відсутнє: [start, перша збережена) і (остання збережена, end)."""
        if tf == "4h":
            self._head_empty = False
        lo, hi, n = self.cache.span(sym, tf)
        listed = self.cache.listed(sym)
        ranges = [(start_ms, end_ms)] if not n else []
        if n:
            if start_ms < lo and not (listed and lo <= listed + TF_MS[tf]):
                ranges.append((start_ms, lo))
            if hi + TF_MS[tf] < end_ms:
                ranges.append((hi + TF_MS[tf], end_ms))
        for a, b in ranges:
            got = 0
            for rows in self.client.klines(sym, tf, a, b):
                self.cache.insert(sym, tf, rows)
                got += len(rows)
                self.state["candles"] += len(rows)
            if n and a == start_ms and not got:
                self._head_empty = True  # раніше за першу збережену свічки немає
        if not n:
            lo2 = self.cache.span(sym, tf)[0]
            if lo2 and lo2 > start_ms + TF_MS[tf]:
                self._head_empty = True  # з'явилась пізніше за початок періоду
