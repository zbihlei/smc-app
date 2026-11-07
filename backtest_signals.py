# -*- coding: utf-8 -*-
"""
Бектест, етап (b): replay скринера по історії з кешу свічок.

Як це працює (без підглядання в майбутнє):
- Налаштування скринера — ТОЧНО ті, що в живому запуску: беремо ту саму команду, яку будує додаток,
  і проганяємо її через main() скринера, перехопивши момент перед стеженням (watch). Отримуємо той самий cfg.
- Крок — 1 година. На кроці T функція скринера analyze_symbol() бачить лише 4h-свічки, що ЗАКРИЛИСЬ до T
  (останні 499, як у живому скані: 500 свічок мінус незакрита), а «поточна ціна» — закриття 1h-свічки, що закінчилась у T.
- Повертає те саме, що живий скан: до 1 кандидата «у списку» на монету + усі «відсіяні: проти структури 4h».
- Подія = ключ як у живих алертах (символ | напрям | низ зони). Для кожної події: коли вперше з'явилась,
  скільки трималась, коли статус став «у зоні», коли ціна торкнулась зони всередині години (аналог живого алерту
  «ЦІНА В ЗОНІ (наживо)» для пар «підхід до зони»), рівні на момент появи.
- Звірка з signals_log.csv: ті самі пари/напрями/зони за дні, коли працював живий скринер.

Запускається окремим процесом (щоб не гальмувати додаток) і рахує монети паралельно на кількох ядрах:
    python3 backtest_signals.py run --data DATA_DIR --screener ШЛЯХ --months 12 -- <аргументи скринера>
"""
import csv
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
import time
import traceback
from bisect import bisect_right
from concurrent.futures import ProcessPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backtest_data as BD  # noqa: E402

H1 = BD.TF_MS["1h"]
H4 = BD.TF_MS["4h"]
WINDOW = 499            # закритих 4h-свічок у вікні (живий скан: --limit 500, остання ще не закрита)
SNAP_KEYS = ("status", "rank", "price", "zone_low", "zone_high", "entry", "stop", "target", "target_type", "rr", "dist",
             "score", "vol_conf", "volume", "sweep", "flags", "fib", "fib_ok", "atr_pct", "knife", "bos_bars_ago",
             "bos_time", "struct", "against", "zone_t", "retr_now", "mid_passed", "wick_used", "fvg", "zone_type")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sig_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, status TEXT,
  start_ms INTEGER, end_ms INTEGER, months INTEGER, symbols INTEGER, steps INTEGER, events INTEGER,
  params TEXT, command TEXT, screener_md5 TEXT, elapsed_s REAL, errors TEXT, pid INTEGER
);
CREATE TABLE IF NOT EXISTS sig_events (
  run_id INTEGER, key TEXT, symbol TEXT, dir TEXT, kind TEXT,
  first_ms INTEGER, last_ms INTEGER, first_list_ms INTEGER, zone_ms INTEGER, touch_ms INTEGER,
  n_list INTEGER, n_reject INTEGER, appearances INTEGER,
  zone_low REAL, zone_high REAL, entry REAL, stop REAL, target REAL, rr REAL, score REAL,
  struct INTEGER, against INTEGER, knife INTEGER, first_status TEXT, snap TEXT, zone_snap TEXT, list_snap TEXT,
  PRIMARY KEY (run_id, key)
);
CREATE INDEX IF NOT EXISTS sig_events_run ON sig_events (run_id, first_ms);
"""


class Captured(BaseException):
    """Перехоплення main() скринера перед стеженням: BaseException, щоб її не «з'їв» жоден except Exception."""

    def __init__(self, a, cfg):
        self.a, self.cfg = a, cfg


def md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def live_cfg(screener_path, args):
    """cfg рівно такий, як у живому скринері з цими аргументами (через його ж main())."""
    mod = BD.load_screener_module(screener_path)

    def grab(a, cfg):
        raise Captured(a, cfg)

    mod.watch = grab
    mod.scan = lambda a, cfg, verbose=True: grab(a, cfg)
    import contextlib
    import io
    old = sys.argv
    sys.argv = ["smc_screener.py"] + list(args)
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            mod.main()
    except Captured as c:
        cfg = dict(c.cfg)
        cfg.pop("stats", None)
        cfg.pop("rejects", None)
        return cfg, {"exclude": c.a.exclude, "limit": c.a.limit, "interval": c.a.interval, "top": c.a.top}
    except SystemExit as e:
        msg = (err.getvalue().strip().splitlines() or [str(e)])[-1]
        raise ValueError(f"скринер не прийняв аргументи: {msg}")
    finally:
        sys.argv = old
    raise ValueError("не вдалося отримати налаштування скринера (main() завершився без скану)")


# ============================================================================ робота над однією монетою (у процесі-воркері)
_MOD = {}


def _screener(path):
    if path not in _MOD:
        _MOD[path] = BD.load_screener_module(path)
    return _MOD[path]


def _snap(r):
    return {k: r.get(k) for k in SNAP_KEYS}


def key_of(r):
    return f"{r['symbol']}|{r['dir']}|{r['zone_low']:.8g}"


def replay_symbol(job):
    """Повертає {symbol, events: [...], steps, error, elapsed}. Подія — dict для таблиці sig_events."""
    t0 = time.time()
    sym, data_dir, spath, cfg, start_ms, end_ms = (job[k] for k in ("symbol", "data", "screener", "cfg", "start", "end"))
    out = {"symbol": sym, "events": [], "steps": 0, "error": None}
    try:
        mod = _screener(spath)
        cache = BD.Cache(data_dir)
        c4 = [{"t": t, "o": o, "h": h, "l": l, "c": c, "v": v, "closed": True}
              for t, o, h, l, c, v in cache.candles(sym, "4h", start_ms - (WINDOW + 5) * H4, end_ms)]
        c1 = cache.candles(sym, "1h", start_ms - H1, end_ms)
        if not c4 or not c1:
            out["error"] = "немає свічок у кеші"
            return out
        t4 = [x["t"] for x in c4]
        cfg = dict(cfg)
        ev = {}
        prev_keys, prev_approach = set(), {}
        for t, o, h, l, c, v in c1:
            T = t + H1                       # момент «скану»: щойно закрилась ця 1h-свічка
            if T <= start_ms:
                continue
            # 1) живий алерт між сканами: пара «підхід до зони» з попереднього кроку, і ціна зайшла в зону за цю годину
            for k, (zl, zh) in prev_approach.items():
                e = ev[k]
                if e["touch_ms"] is None and l <= zh and h >= zl:
                    e["touch_ms"] = t
            # 2) скан: лише закриті до T свічки 4h
            n = bisect_right(t4, T - H4)     # свічки з t + 4h <= T
            if n < 2:
                continue
            win = c4[max(0, n - WINDOW):n]
            cfg["rejects"] = []
            rows = mod.analyze_symbol(sym, win, c, cfg)
            out["steps"] += 1
            now_keys, approach = set(), {}
            for kind, lst in (("list", rows), ("reject", cfg.get("rejects") or [])):
                for r in lst:
                    k = key_of(r)
                    if k in now_keys:
                        continue
                    now_keys.add(k)
                    e = ev.get(k)
                    if e is None:
                        e = ev[k] = {"key": k, "symbol": sym, "dir": r["dir"], "kind": kind, "first_ms": T, "last_ms": T,
                                     "first_list_ms": None, "zone_ms": None, "touch_ms": None, "n_list": 0, "n_reject": 0,
                                     "appearances": 0, "zone_low": r["zone_low"], "zone_high": r["zone_high"],
                                     "entry": r["entry"], "stop": r["stop"], "target": r["target"], "rr": r["rr"],
                                     "score": r["score"], "struct": r["struct"], "against": int(bool(r["against"])),
                                     "knife": int(bool(r["knife"])), "first_status": r["status"], "snap": _snap(r), "zone_snap": None,
                                     "list_snap": None}
                    if k not in prev_keys:
                        e["appearances"] += 1
                    e["last_ms"] = T
                    e["n_" + kind] += 1
                    if kind == "list":
                        if e["first_list_ms"] is None:
                            e["first_list_ms"] = T
                            e["list_snap"] = _snap(r)   # рівні на момент, коли пара з'явилась у списку (тоді ставиться лімітка)
                        if r["rank"] == 1:
                            approach[k] = (r["zone_low"], r["zone_high"])
                    if r["rank"] == 0 and e["zone_ms"] is None:
                        e["zone_ms"] = T
                        e["zone_snap"] = _snap(r)
            prev_keys, prev_approach = now_keys, approach
        for e in ev.values():
            e["kind"] = "list" if e["n_list"] else "reject"
            out["events"].append(e)
    except Exception as e:  # одна монета не має зупиняти весь прохід
        out["error"] = f"{e.__class__.__name__}: {e}"
        out["trace"] = traceback.format_exc(limit=3)
    out["elapsed"] = round(time.time() - t0, 1)
    return out


# ============================================================================ сховище
class Store:
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "backtest")
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, "signals.db")
        self.progress_path = os.path.join(self.dir, "signals_progress.json")
        with self._con() as con:
            con.executescript(SCHEMA)
            cols = [r[1] for r in con.execute("PRAGMA table_info(sig_events)")]
            if "list_snap" not in cols:   # база з першої версії етапу 2
                con.execute("ALTER TABLE sig_events ADD COLUMN list_snap TEXT")

    def _con(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def run_start(self, **kw):
        kw["started_at"] = dt.datetime.now().isoformat(timespec="seconds")
        kw["status"] = "running"
        with self._con() as con:
            return con.execute(f"INSERT INTO sig_runs ({','.join(kw)}) VALUES ({','.join('?' * len(kw))})", list(kw.values())).lastrowid

    def run_end(self, rid, **kw):
        kw["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
        with self._con() as con:
            con.execute(f"UPDATE sig_runs SET {','.join(k + '=?' for k in kw)} WHERE id=?", [*kw.values(), rid])

    def mark_dead(self, alive_pid=None):
        """Запуски зі статусом running, процес яких уже не живий (зупинено/закрито додаток) — позначити."""
        with self._con() as con:
            for rid, pid in con.execute("SELECT id, pid FROM sig_runs WHERE status='running'").fetchall():
                if pid != alive_pid:
                    con.execute("UPDATE sig_runs SET status='stopped', finished_at=? WHERE id=?",
                                (dt.datetime.now().isoformat(timespec="seconds"), rid))

    def add_events(self, rid, events):
        cols = ("key", "symbol", "dir", "kind", "first_ms", "last_ms", "first_list_ms", "zone_ms", "touch_ms", "n_list",
                "n_reject", "appearances", "zone_low", "zone_high", "entry", "stop", "target", "rr", "score", "struct",
                "against", "knife", "first_status", "snap", "zone_snap", "list_snap")
        rows = [(rid, *[json.dumps(e[c], ensure_ascii=False) if c in ("snap", "zone_snap", "list_snap") and e[c] is not None else e[c]
                        for c in cols]) for e in events]
        with self._con() as con:
            con.executemany(f"INSERT OR REPLACE INTO sig_events (run_id,{','.join(cols)}) VALUES ({','.join('?' * (len(cols) + 1))})", rows)

    def runs(self, n=10):
        with self._con() as con:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute("SELECT * FROM sig_runs ORDER BY id DESC LIMIT ?", (n,))]

    def run(self, rid):
        with self._con() as con:
            con.row_factory = sqlite3.Row
            r = con.execute("SELECT * FROM sig_runs WHERE id=?", (rid,)).fetchone()
            return dict(r) if r else None

    def last_done(self):
        with self._con() as con:
            r = con.execute("SELECT id FROM sig_runs WHERE status IN ('done','done_errors') ORDER BY id DESC LIMIT 1").fetchone()
            return r[0] if r else None

    def events(self, rid, start_ms=None, end_ms=None):
        q, a = "SELECT * FROM sig_events WHERE run_id=?", [rid]
        if start_ms is not None:
            q += " AND last_ms>=?"; a.append(start_ms)
        if end_ms is not None:
            q += " AND first_ms<=?"; a.append(end_ms)
        with self._con() as con:
            con.row_factory = sqlite3.Row
            out = []
            for r in con.execute(q + " ORDER BY first_ms", a):
                d = dict(r)
                for k in ("snap", "zone_snap", "list_snap"):
                    d[k] = json.loads(d[k]) if d.get(k) else None
                out.append(d)
            return out

    def write_progress(self, p):
        tmp = self.progress_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False)
        os.replace(tmp, self.progress_path)

    def read_progress(self):
        try:
            with open(self.progress_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None


# ============================================================================ підсумки
def month_key(ms):
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m")


def summarize(events, start_ms=None, end_ms=None):
    lst = [e for e in events if e["kind"] == "list"]
    rej = [e for e in events if e["kind"] == "reject"]
    zone = [e for e in lst if e["zone_ms"] or e["touch_ms"]]
    months = {}
    for e in lst:
        m = months.setdefault(month_key(e["first_list_ms"] or e["first_ms"]), {"list": 0, "zone": 0, "long": 0, "short": 0})
        m["list"] += 1
        m["zone"] += 1 if (e["zone_ms"] or e["touch_ms"]) else 0
        m["long" if e["dir"] == "Long" else "short"] += 1
    days = max(1, ((end_ms or 0) - (start_ms or 0)) / 86400_000) if start_ms and end_ms else None
    return {"list": len(lst), "reject": len(rej), "zone": len(zone),
            "long": sum(e["dir"] == "Long" for e in lst), "short": sum(e["dir"] == "Short" for e in lst),
            "symbols": len({e["symbol"] for e in lst}),
            "per_week": round(len(lst) / days * 7, 1) if days else None,
            "zone_per_week": round(len(zone) / days * 7, 1) if days else None,
            "months": [dict(month=k, **v) for k, v in sorted(months.items())]}


# ============================================================================ звірка з signals_log.csv
LOG_KIND = {"у списку": "list", "наживо: ціна в зоні": "live"}


def parse_log(path):
    """signals_log.csv -> (події за ключем, відсортовані моменти сканів у ms). Час у файлі — місцевий час Mac."""
    events, scans = {}, set()
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                ms = int(dt.datetime.strptime(row["time"], "%Y-%m-%d %H:%M").timestamp() * 1000)
                zl = float(row["zone_low"])
            except (KeyError, ValueError, TypeError):
                continue
            kind = LOG_KIND.get(row.get("kind", ""), "reject" if row.get("kind", "").startswith("відсіяно") else None)
            if kind is None:
                continue
            if kind != "live":
                scans.add(ms)
            key = f"{row['symbol']}|{row['dir']}|{zl:.8g}"
            e = events.setdefault(key, {"key": key, "symbol": row["symbol"], "dir": row["dir"], "zone_low": zl,
                                        "zone_high": float(row["zone_high"] or 0), "first_ms": ms, "last_ms": ms,
                                        "kinds": set(), "live_ms": None, "first_status": row.get("status", ""), "first_by": {},
                                        "entry": row.get("entry"), "stop": row.get("stop"), "target": row.get("target"),
                                        "rr": row.get("rr"), "score": row.get("score")})
            e["first_ms"] = min(e["first_ms"], ms)
            e["last_ms"] = max(e["last_ms"], ms)
            e["kinds"].add(kind)
            e["first_by"][kind] = min(e["first_by"].get(kind, ms), ms)
            if kind == "live" and e["live_ms"] is None:
                e["live_ms"] = ms
    for e in events.values():
        if "live" in e["first_by"] and "list" not in e["first_by"]:
            e["first_by"]["list"] = e["first_by"]["live"]
        e["kind"] = "list" if ("list" in e["kinds"] or "live" in e["kinds"]) else "reject"
        e["kinds"] = sorted(e["kinds"])
    return events, sorted(scans)


def _covered(scans, a, b, slack=45 * 60_000):
    """Чи був хоч один живий скан у проміжку [a - slack, b + slack]."""
    i = bisect_right(scans, a - slack - 1)
    return i < len(scans) and scans[i] <= b + slack


def _same(a, b, tol=0.002):
    return a["symbol"] == b["symbol"] and a["dir"] == b["dir"] and abs(a["zone_low"] - b["zone_low"]) <= tol * abs(b["zone_low"])


def compare(replay, log_path, cached_symbols, replay_end_ms=None):
    """Порівняння подій replay з живим логом за ті самі дні. Окремо для «у списку» і «відсіяно»."""
    log, scans = parse_log(log_path)
    if not scans:
        return {"ok": False, "error": "у signals_log.csv немає рядків сканів"}
    a, b = scans[0], scans[-1]
    if replay_end_ms:
        b = min(b, replay_end_ms)
    cached = set(cached_symbols)
    rep = [e for e in replay if e["last_ms"] >= a - H1 and e["first_ms"] <= b + H1]
    logs = [e for e in log.values() if e["first_ms"] <= b]
    out = {"ok": True, "from_ms": a, "to_ms": b, "from_txt": BD.fmt_ms(a), "to_txt": BD.fmt_ms(b), "scans": len(scans)}
    for kind in ("list", "reject"):
        L = [e for e in logs if e["kind"] == kind]
        # «у списку» в живому логу може бути «відсіяно» в replay і навпаки — шукаємо пару серед усіх подій replay
        R_all = rep
        R = [e for e in rep if e["kind"] == kind]
        matched, only_log = [], []
        used = set()
        for le in L:
            m = next((re for re in R_all if _same(re, le)), None)
            if m is None:
                reason = "монети немає в кеші бектесту" if le["symbol"] not in cached else "replay не знайшов"
                only_log.append({**_pub_log(le), "reason": reason})
                continue
            used.add(m["key"])
            # момент появи порівнюємо в межах свого типу: «у списку» з «у списку», «відсіяно» з «відсіяно»
            log_first = le["first_by"].get(kind) or le["first_ms"]
            rep_first = (m["first_list_ms"] or m["first_ms"]) if kind == "list" else m["first_ms"]
            before = rep_first < a - H1          # подія жила ще до першого рядка логу — момент появи не порівняти
            lag = None if before else round((rep_first - log_first) / H1, 1)
            # replay побачив раніше, а живих сканів між цими моментами не було — Mac спав, це не розбіжність
            slept = lag is not None and lag < -1 and not _covered(scans, rep_first + 45 * 60_000, log_first - 46 * 60_000, slack=0)
            matched.append({**_pub_log(le), "first_txt": BD.fmt_ms(log_first), "replay_first_ms": rep_first,
                            "replay_first_txt": BD.fmt_ms(rep_first), "replay_kind": m["kind"], "lag_h": lag, "before_log": before, "slept": slept,
                            "same_kind": m["kind"] == kind,
                            "replay_zone_txt": BD.fmt_ms(m["zone_ms"] or m["touch_ms"]) if (m["zone_ms"] or m["touch_ms"]) else "",
                            "log_zone_txt": BD.fmt_ms(le["live_ms"]) if le["live_ms"] else ""})
        only_rep = []
        for re in R:
            if re["key"] in used or any(_same(re, le) for le in logs):
                continue
            seen_live = _covered(scans, re["first_ms"], re["last_ms"])
            only_rep.append({"symbol": re["symbol"], "dir": re["dir"], "zone_low": re["zone_low"], "zone_high": re["zone_high"],
                             "first_txt": BD.fmt_ms(re["first_ms"]), "last_txt": BD.fmt_ms(re["last_ms"]),
                             "hours": round((re["last_ms"] - re["first_ms"]) / H1) + 1,
                             "reason": "живий скринер у цей час не сканував (Mac спав / вимкнено)" if not seen_live
                             else ("подія коротша за годину між сканами" if re["last_ms"] == re["first_ms"] else "у живому лозі немає")})
        lags = sorted(m["lag_h"] for m in matched if m["lag_h"] is not None and not m["slept"])
        out[kind] = {"log": len(L), "replay": len(R), "matched": len(matched), "log_cached": sum(1 for e in L if e["symbol"] in cached), "only_log": only_log, "only_replay": only_rep,
                     "matched_rows": matched, "same_kind": sum(m["same_kind"] for m in matched),
                     "lag_median_h": lags[len(lags) // 2] if lags else None,
                     "lag_within_1h": sum(1 for x in lags if abs(x) <= 1), "lag_n": len(lags),
                     "explained_only_replay": sum(1 for x in only_rep if "не сканував" in x["reason"])}
    return out


def _pub_log(e):
    return {"symbol": e["symbol"], "dir": e["dir"], "zone_low": e["zone_low"], "zone_high": e["zone_high"],
            "first_txt": BD.fmt_ms(e["first_ms"]), "last_txt": BD.fmt_ms(e["last_ms"]), "kinds": e["kinds"]}


# ============================================================================ прохід (окремий процес)
def run(data_dir, screener_path, months, args, workers=None, symbols=None):
    store = Store(data_dir)
    cache = BD.Cache(data_dir)
    t0 = time.time()
    prog = {"status": "running", "pid": os.getpid(), "done": 0, "total": 0, "symbol": "", "events": 0, "steps": 0,
            "errors": [], "started": t0, "eta_s": None, "phase": "налаштування скринера"}
    store.write_progress(prog)
    rid = None
    try:
        cfg, meta = live_cfg(screener_path, args)
        top = [s["symbol"] for s in cache.symbols()]
        excl = {x.strip().upper() for x in (meta.get("exclude") or "").split(",") if x.strip()}
        syms = [s for s in (symbols or top) if s[:-4] not in excl and s not in excl]
        syms = [s for s in syms if cache.span(s, "1h")[2] and cache.span(s, "4h")[2]]
        ends = [cache.span(s, "1h")[1] + H1 for s in syms]
        end_ms = max(ends) if ends else BD.now_ms()
        start_ms = BD.period(months, end_ms)[0]
        rid = store.run_start(start_ms=start_ms, end_ms=end_ms, months=months, symbols=len(syms), params=json.dumps(cfg, ensure_ascii=False),
                              command=" ".join(args), screener_md5=md5(screener_path), pid=os.getpid())
        prog.update(total=len(syms), run_id=rid, phase="прохід по історії", start_ms=start_ms, end_ms=end_ms)
        store.write_progress(prog)
        jobs = [{"symbol": s, "data": data_dir, "screener": screener_path, "cfg": cfg, "start": start_ms, "end": end_ms} for s in syms]
        workers = workers or max(1, min(8, (os.cpu_count() or 2) - 1))
        steps = n_ev = 0
        errors = []
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(replay_symbol, j): j["symbol"] for j in jobs}
            for f in as_completed(futs):
                r = f.result()
                if r["error"]:
                    errors.append(f"{r['symbol']}: {r['error']}")
                store.add_events(rid, r["events"])
                steps += r["steps"]
                n_ev += len(r["events"])
                prog["done"] += 1
                el = time.time() - t0
                prog.update(symbol=r["symbol"], events=n_ev, steps=steps, errors=errors[-10:],
                            eta_s=round(el / prog["done"] * (prog["total"] - prog["done"])) if prog["done"] else None)
                store.write_progress(prog)
        status = "done" if not errors else ("done_errors" if len(errors) < len(syms) else "error")
        store.run_end(rid, status=status, steps=steps, events=n_ev, elapsed_s=round(time.time() - t0, 1),
                      errors="\n".join(errors) or None)
        prog.update(status=status, phase="готово", elapsed_s=round(time.time() - t0, 1), eta_s=None)
        store.write_progress(prog)
        return rid
    except BaseException as e:  # noqa: B902 — і Ctrl+C, і помилки
        prog.update(status="error", phase="помилка", errors=prog["errors"] + [f"{e.__class__.__name__}: {e}"])
        store.write_progress(prog)
        if rid:
            store.run_end(rid, status="error", errors=f"{e.__class__.__name__}: {e}")
        raise


def main(argv=None):
    import argparse
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    p = argparse.ArgumentParser(description="Replay скринера по кешу свічок")
    p.add_argument("cmd", choices=["run"])
    p.add_argument("--data", required=True)
    p.add_argument("--screener", required=True)
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--symbols", default="")
    a = p.parse_args(argv)
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()] or None
    rid = run(a.data, a.screener, a.months, extra, workers=a.workers, symbols=syms)
    print(f"готово: запуск {rid}")


if __name__ == "__main__":
    main()
