# -*- coding: utf-8 -*-
"""
Бектест, етап (c): «прогнати угоди» по сигналах етапу (b) на 1h-свічках.

Правила (з «Стратегії» v0.5.1, без 1h-логіки):
- Сигнал = момент, коли пара з'явилась «у списку» скринера (рівні — на цей момент). Відсіяні «проти 4h» не торгуємо.
- Лімітка від середини зони виставляється одразу (з наступної години). Якщо ціна вже пройшла середину — пропуск
  (у живій торгівлі тут потрібне 1h-уточнення, а його в цьому прогоні немає).
- Скасування до входу: 4h-свічка закрилась за зоною (правило 8 стратегії); опційно — ціна дійшла до цілі без входу;
  опційно — термін лімітки в годинах (0 = без терміну).
- Після входу: стоп і ціль — з сигналу. Порядок усередині години невідомий, тому песимістично:
  стоп і ціль в одній свічці = стоп; у свічці входу ціль не зараховується, а стоп — так.
- Два варіанти ведення на тих самих входах: «до цілі або стопа» і «за стратегією»: при 3R стоп у беззбиток
  (вхід + комісії), при 4R закрити 50%, якщо ціль далі за 4R.
- Комісії (без фандінгу): вхід і ціль — maker, стоп — taker; у R = комісія у % ÷ ширина стопа у %.
- Одна позиція на монету: якщо по монеті вже відкрита угода, новий вхід пропускається.
- Перші 8 міс. — розробка; останні 4 міс. приховано до етапу «перевірка поза вибіркою».
"""
import datetime as dt
import json
import os
import sqlite3
import time
from bisect import bisect_left

import backtest_data as BD

H1 = BD.TF_MS["1h"]
H4 = BD.TF_MS["4h"]
MONTH_MS = int(30.4375 * 86400_000)
DEFAULTS = {"expiry_h": 0, "cancel_on_target": True, "one_per_symbol": True, "maker": 0.02, "taker": 0.05,
            "dev_months": 8, "be_at": 3.0, "part_at": 4.0, "part_share": 0.5,
            # фільтри сигналів (етап 4): None / False = без фільтра
            "min_score": None, "max_rr": None, "no_knife": False, "no_tight": False, "dirs": "both", "zones": "all",
            # допуск входу, % висоти зони: None = як у скринері (рівень лімітки з сигналу), число — перерахувати від середини
            "entry_tol": None, "min_rr": 3.0, "rr_at_limit": False}
FILTER_KEYS = ("min_score", "max_rr", "no_knife", "no_tight", "dirs", "zones")
CANCEL_TXT = {"mid_passed": "середина зони вже пройдена на момент сигналу",
              "zone_close": "4h закрилась за зоною до входу",
              "target_first": "ціна дійшла до цілі без входу",
              "expiry": "минув термін лімітки",
              "no_data": "не виконано до кінця даних",
              "busy": "по монеті вже відкрита угода",
              "bad_levels": "некоректні рівні"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tr_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, sig_run_id INTEGER, created_at TEXT, params TEXT, result TEXT, elapsed_s REAL
);
CREATE TABLE IF NOT EXISTS oos_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, base_id INTEGER, oos_id INTEGER, sig_run_id INTEGER, rules TEXT
);
"""


def clean_params(p):
    out = dict(DEFAULTS)
    p = p or {}
    try:
        out["expiry_h"] = max(0, min(24 * 90, int(float(p.get("expiry_h", out["expiry_h"]) or 0))))
        out["maker"] = max(0.0, min(0.5, float(p.get("maker", out["maker"]))))
        out["taker"] = max(0.0, min(0.5, float(p.get("taker", out["taker"]))))
    except (TypeError, ValueError):
        raise ValueError("термін і комісії — числа")
    for k in ("cancel_on_target", "one_per_symbol", "no_knife", "no_tight", "rr_at_limit"):
        if k in p:
            out[k] = bool(p[k])
    try:
        ms = p.get("min_score")
        out["min_score"] = None if ms in (None, "", "all") else max(-10, min(10, int(float(ms))))
        mr = p.get("max_rr")
        out["max_rr"] = None if mr in (None, "", 0, "0") else max(3.0, min(50.0, float(mr)))
    except (TypeError, ValueError):
        raise ValueError("мін. бали й макс. R:R — числа")
    out["dirs"] = p.get("dirs") if p.get("dirs") in ("Long", "Short") else "both"
    out["zones"] = p.get("zones") if p.get("zones") in ("OB", "брейкер") else "all"
    try:
        et = p.get("entry_tol")
        out["entry_tol"] = None if et in (None, "") else max(0.0, min(50.0, float(et)))
    except (TypeError, ValueError):
        raise ValueError("допуск входу — число від 0 до 50")
    return out


def is_tight(flags):
    return any(str(f).startswith("тісний стоп") for f in (flags or []))


def passes(lv, d, P):
    """Чи проходить сигнал фільтри етапу 4 (рівні — на момент появи в списку)."""
    if P["min_score"] is not None and (lv.get("score") is None or lv["score"] < P["min_score"]):
        return False
    if P["max_rr"] is not None and lv.get("rr") is not None and lv["rr"] > P["max_rr"]:
        return False
    if P["no_knife"] and lv.get("knife"):
        return False
    if P["no_tight"] and is_tight(lv.get("flags")):
        return False
    if P["dirs"] != "both" and d != P["dirs"]:
        return False
    if P.get("zones", "all") != "all" and (lv.get("zone_type") or "OB") != P["zones"]:
        return False
    return True


def filters_text(P):
    out = []
    if P.get("min_score") is not None:
        out.append(f"бали ≥ {P['min_score']}")
    if P.get("max_rr") is not None:
        out.append(f"R:R ≤ {P['max_rr']:g}")
    if P.get("no_knife"):
        out.append("без «ножа»")
    if P.get("no_tight"):
        out.append("без тісного стопу")
    if P.get("dirs") in ("Long", "Short"):
        out.append(f"лише {P['dirs']}")
    if P.get("zones") in ("OB", "брейкер"):
        out.append("лише ордерблоки" if P["zones"] == "OB" else "лише брейкери")
    if P.get("entry_tol") is not None:
        out.append(f"допуск входу {P['entry_tol']:g}%")
    if P.get("rr_at_limit"):
        out.append("R:R ≥ 3 від лімітки")
    return ", ".join(out) or "без фільтрів"


# ============================================================================ одна угода
def _sim_event(e, lv, c1, t1, P):
    """Повертає dict угоди або скасування. c1 — 1h-свічки монети [(t,o,h,l,c,v)], t1 — їх час."""
    d = 1 if e["dir"] == "Long" else -1
    E, S, TG = lv["entry"], lv["stop"], lv["target"]
    zl, zh = lv["zone_low"], lv["zone_high"]
    if P.get("entry_tol") is not None:      # свій допуск у прогоні: лімітка від середини на N% висоти зони до краю
        E = (zl + zh) / 2 + d * P["entry_tol"] / 100 * (zh - zl)
    T = e["first_list_ms"]
    base = {"symbol": e["symbol"], "dir": e["dir"], "signal_ms": T, "entry": E, "stop": S, "target": TG,
            "zone_low": zl, "zone_high": zh, "rr": lv["rr"], "score": lv.get("score"), "knife": bool(lv.get("knife")),
            "flags": lv.get("flags") or [], "status_at_signal": lv.get("status"), "sweep": lv.get("sweep"),
            "fib_ok": lv.get("fib_ok"), "vol_conf": lv.get("vol_conf"), "target_type": lv.get("target_type"),
            "atr_pct": lv.get("atr_pct"), "zone_type": lv.get("zone_type") or "OB"}
    risk = abs(E - S)
    if risk <= 0 or (d == 1 and not (S < E < TG)) or (d == -1 and not (TG < E < S)):
        return dict(base, outcome="cancel", reason="bad_levels")
    px = lv.get("price")
    if px is not None and ((d == 1 and px < E) or (d == -1 and px > E)):
        return dict(base, outcome="cancel", reason="mid_passed")
    risk_pct = risk / E * 100
    rr = abs(TG - E) / risk
    mid = (zl + zh) / 2
    rr_check = rr if P.get("rr_at_limit") else (abs(TG - mid) / abs(mid - S) if abs(mid - S) > 0 else 0.0)
    if rr_check < P.get("min_rr", 3.0) - 1e-9:   # як у скринері: за замовч. R:R для відбору — від середини зони
        return dict(base, entry=E, outcome="cancel", reason="rr_low")
    i = bisect_left(t1, T)
    exp_ms = T + P["expiry_h"] * H1 if P["expiry_h"] else None
    fill_i = None
    while i < len(c1):
        t, o, h, l, c, v = c1[i]
        if exp_ms and t >= exp_ms:
            return dict(base, outcome="cancel", reason="expiry", cancel_ms=t)
        if (l <= E) if d == 1 else (h >= E):
            fill_i = i
            break
        if P["cancel_on_target"] and ((h >= TG) if d == 1 else (l <= TG)):
            return dict(base, outcome="cancel", reason="target_first", cancel_ms=t)
        if (t + H1) % H4 == 0 and ((c < zl) if d == 1 else (c > zh)):
            return dict(base, outcome="cancel", reason="zone_close", cancel_ms=t + H1)
        i += 1
    if fill_i is None:
        return dict(base, outcome="cancel", reason="no_data")

    fee_in = P["maker"]
    fR = lambda fee_out: (fee_in + fee_out) / risk_pct           # комісії входу+виходу в R
    fav = lambda h, l: ((h - E) if d == 1 else (E - l)) / risk     # пройдено в мій бік, R
    adv = lambda h, l: ((E - l) if d == 1 else (h - E)) / risk     # проти мене, R
    be_px = E * (1 + d * (P["maker"] + P["taker"]) / 100)          # беззбиток з урахуванням комісій
    be_R = (be_px - E) / risk * d
    fill_t = c1[fill_i][0]
    # ---- варіант A: до цілі або стопа
    A = None
    mfe, mae = 0.0, 0.0
    # ---- варіант B: за стратегією (3R → беззбиток, 4R → 50%)
    B = None
    b_be, b_part = False, 0.0
    part_ok = rr > P["part_at"]
    j = fill_i
    while j < len(c1):
        t, o, h, l, c, v = c1[j]
        first = j == fill_i
        a_ = adv(h, l)
        f_ = 0.0 if first else fav(h, l)
        if A is None:
            mae = max(mae, min(a_, 1.0))
            if a_ >= 1:
                A = {"r": -1 - fR(P["taker"]), "exit_ms": t, "exit": "stop"}
            elif f_ >= rr:
                A = {"r": rr - fR(P["maker"]), "exit_ms": t, "exit": "target"}
            mfe = max(mfe, min(f_, rr))
        if B is None:
            if b_be and ((l <= be_px) if d == 1 else (h >= be_px)):
                B = {"r": b_part * (P["part_at"] - fR(P["maker"])) + (1 - b_part) * (be_R - fR(P["taker"])),
                     "exit_ms": t, "exit": "be"}
            elif not b_be and a_ >= 1:
                B = {"r": b_part * (P["part_at"] - fR(P["maker"])) + (1 - b_part) * (-1 - fR(P["taker"])),
                     "exit_ms": t, "exit": "stop"}
            else:
                if part_ok and not b_part and f_ >= P["part_at"]:
                    b_part = P["part_share"]
                if f_ >= rr:
                    B = {"r": b_part * (P["part_at"] - fR(P["maker"])) + (1 - b_part) * (rr - fR(P["maker"])),
                         "exit_ms": t, "exit": "target"}
                elif f_ >= P["be_at"]:
                    b_be = True   # діє з наступної години (порядок усередині свічки невідомий)
        if A is not None and B is not None:
            break
        j += 1
    out = dict(base, outcome="trade", fill_ms=fill_t, rr=rr, risk_pct=round(risk_pct, 3),
               hours_to_fill=(fill_t - T) // H1, mfe=round(mfe, 2), mae=round(mae, 2),
               fee_r=round(fR(P["taker"]), 3))
    if A is None:
        out.update(state="open", last_ms=c1[-1][0])
    else:
        out.update(state="closed", r_a=round(A["r"], 3), exit_a=A["exit"], exit_ms=A["exit_ms"],
                   hours=(A["exit_ms"] - fill_t) // H1 + 1)
    if B is not None:
        out.update(r_b=round(B["r"], 3), exit_b=B["exit"], exit_b_ms=B["exit_ms"])
    return out


# ============================================================================ прогін
def simulate(cache, events, params, start_ms, end_ms, window="dev"):
    """events — події етапу (b) (dict з list_snap). window: "dev" — перші 8 міс., "oos" — решта.
    Повертає (угоди й скасування, межа розробки ms, параметри)."""
    P = clean_params(params)
    split = start_ms + P["dev_months"] * MONTH_MS
    split -= split % H4
    by_sym = {}
    filtered = 0
    for e in events:
        t = e["first_list_ms"]
        if e["kind"] != "list" or not t or ((t >= split) if window == "dev" else (t < split)):
            continue
        if not passes(e.get("list_snap") or e.get("snap") or {}, e["dir"], P):
            filtered += 1
            continue
        by_sym.setdefault(e["symbol"], []).append(e)
    P["_filtered"] = filtered
    rows = []
    for sym, evs in by_sym.items():
        c1 = cache.candles(sym, "1h", start_ms - H1, None)
        t1 = [x[0] for x in c1]
        cand = []
        for e in sorted(evs, key=lambda x: x["first_list_ms"]):
            lv = e.get("list_snap") or e.get("snap")
            cand.append(_sim_event(e, lv, c1, t1, P))
        if P["one_per_symbol"]:
            busy_until = 0
            for r in sorted(cand, key=lambda x: x.get("fill_ms") or 0):
                if r["outcome"] == "trade":
                    if r["fill_ms"] < busy_until:
                        r.update(outcome="cancel", reason="busy")
                        continue
                    busy_until = r.get("exit_ms") or float("inf")
        rows += cand
    rows.sort(key=lambda r: r["signal_ms"])
    return rows, split, P


def _series_stats(vals):
    """vals — R угод у порядку закриття."""
    n = len(vals)
    if not n:
        return {"n": 0}
    wins = [x for x in vals if x > 0.05]
    losses = [x for x in vals if x < -0.05]
    cum = peak = dd = 0.0
    streak = best = 0
    for x in vals:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        streak = streak + 1 if x < -0.05 else 0
        best = max(best, streak)
    gl = -sum(losses)
    return {"n": n, "wins": len(wins), "losses": len(losses), "be": n - len(wins) - len(losses),
            "winrate": round(len(wins) / n * 100, 1), "avg": round(sum(vals) / n, 3), "sum": round(sum(vals), 2),
            "pf": round(sum(wins) / gl, 2) if gl > 0 else None, "max_dd": round(dd, 2), "max_streak": best}


def metrics(rows, split, start_ms, filtered=0, end_ms=None):
    """Для «dev»: start_ms..split; для «oos»: split..end_ms (передається як start_ms=split, split=end_ms)."""
    trades = [r for r in rows if r["outcome"] == "trade"]
    closed = [r for r in trades if r["state"] == "closed"]
    cancels = {}
    for r in rows:
        if r["outcome"] == "cancel":
            cancels[r["reason"]] = cancels.get(r["reason"], 0) + 1
    a = [r["r_a"] for r in sorted(closed, key=lambda r: r["exit_ms"])]
    bb = [r for r in trades if "r_b" in r]
    b = [r["r_b"] for r in sorted(bb, key=lambda r: r["exit_b_ms"])]
    months = {}
    for r in closed:
        m = months.setdefault(dt.datetime.fromtimestamp(r["exit_ms"] / 1000).strftime("%Y-%m"), {"n": 0, "a": 0.0, "b": 0.0, "wins": 0})
        m["n"] += 1
        m["a"] += r["r_a"]
        m["b"] += r.get("r_b", 0.0)
        m["wins"] += r["r_a"] > 0
    mfe = [{"k": k, "share": round(sum(1 for r in closed if r["mfe"] >= k) / len(closed) * 100, 1) if closed else 0,
            "losers": sum(1 for r in closed if r["mfe"] >= k and r["r_a"] < 0)} for k in (1, 2, 3, 4, 5)]
    side = {}
    for dname in ("Long", "Short"):
        side[dname] = _series_stats([r["r_a"] for r in sorted(closed, key=lambda r: r["exit_ms"]) if r["dir"] == dname])
    fills = sorted(r["hours_to_fill"] for r in trades)
    return {"signals": len(rows), "filtered": filtered, "cancels": [{"reason": k, "text": CANCEL_TXT.get(k, k), "n": v}
                                                for k, v in sorted(cancels.items(), key=lambda x: -x[1])],
            "trades": len(trades), "open": len(trades) - len(closed),
            "a": _series_stats(a), "b": _series_stats(b), "side": side,
            "months": [dict(month=k, n=v["n"], wins=v["wins"], a=round(v["a"], 2), b=round(v["b"], 2)) for k, v in sorted(months.items())],
            "mfe": mfe,
            "fill_h_median": fills[len(fills) // 2] if fills else None, "fill_h_max": fills[-1] if fills else None,
            "rr_median": round(sorted(r["rr"] for r in trades)[len(trades) // 2], 2) if trades else None,
            "dev_from": BD.fmt_ms(start_ms), "dev_to": BD.fmt_ms(split), "period_days": round((split - start_ms) / 86400_000, 1)}


# ============================================================================ сховище запусків
class TradeStore:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "backtest", "signals.db")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with self._con() as con:
            con.executescript(SCHEMA)

    def _con(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def save(self, sig_run_id, params, result, elapsed):
        with self._con() as con:
            return con.execute("INSERT INTO tr_runs (sig_run_id, created_at, params, result, elapsed_s) VALUES (?,?,?,?,?)",
                               (sig_run_id, dt.datetime.now().isoformat(timespec="seconds"), json.dumps(params, ensure_ascii=False),
                                json.dumps(result, ensure_ascii=False), elapsed)).lastrowid

    def get(self, rid=None, window="dev"):
        """rid — конкретний прогін; без rid — останній прогін вікна window ("dev" — розробка, "oos" — поза вибіркою)."""
        with self._con() as con:
            q = "SELECT id, sig_run_id, created_at, params, result, elapsed_s FROM tr_runs "
            if rid:
                rows = con.execute(q + "WHERE id=?", (rid,)).fetchall()
            else:
                rows = con.execute(q + "ORDER BY id DESC").fetchall()
        for r in rows:
            res = json.loads(r[4])
            if rid or res.get("window", "dev") == window:
                res.setdefault("window", "dev")
                return {"id": r[0], "sig_run_id": r[1], "created_at": r[2], "params": json.loads(r[3]), **res, "elapsed_s": r[5]}
        return None

    def runs(self, n=10, window="dev"):
        with self._con() as con:
            out = []
            for rid, sid, ca, p, res in con.execute("SELECT id, sig_run_id, created_at, params, result FROM tr_runs ORDER BY id DESC"):
                res = json.loads(res)
                if res.get("window", "dev") != window:
                    continue
                m = res["metrics"]
                P = json.loads(p)
                out.append({"id": rid, "sig_run_id": sid, "created_at": ca, "params": P, "filters_text": filters_text(P),
                            "base_id": res.get("base_id"), "trades": m["trades"], "sum_a": m["a"].get("sum"),
                            "sum_b": m["b"].get("sum"), "avg_a": m["a"].get("avg"), "avg_b": m["b"].get("avg")})
                if len(out) >= n:
                    break
            return out

    def log_oos(self, base_id, oos_id, sig_run_id, rules):
        with self._con() as con:
            con.execute("INSERT INTO oos_log (created_at, base_id, oos_id, sig_run_id, rules) VALUES (?,?,?,?,?)",
                        (dt.datetime.now().isoformat(timespec="seconds"), base_id, oos_id, sig_run_id, rules))

    def oos_log(self):
        with self._con() as con:
            return [dict(zip(("id", "created_at", "base_id", "oos_id", "sig_run_id", "rules"), r))
                    for r in con.execute("SELECT id, created_at, base_id, oos_id, sig_run_id, rules FROM oos_log ORDER BY id")]


TRADE_KEEP = ("symbol", "dir", "signal_ms", "fill_ms", "exit_ms", "outcome", "reason", "state", "entry", "stop", "target", "rr",
              "r_a", "r_b", "exit_a", "exit_b", "mfe", "mae", "hours", "hours_to_fill", "score", "knife", "risk_pct", "fee_r",
              "flags", "sweep", "fib_ok", "vol_conf", "target_type", "atr_pct", "status_at_signal", "zone_type")


def run(data_dir, sig_store, sig_run_id, params, window="dev", base_id=None):
    """Повний прогін для запуску сигналів sig_run_id. window="oos" — перевірка поза вибіркою. Повертає збережений результат."""
    t0 = time.time()
    run_ = sig_store.run(sig_run_id)
    if not run_:
        raise ValueError("немає такого проходу сигналів")
    ev = sig_store.events(sig_run_id)
    if any(e["kind"] == "list" and not e.get("list_snap") for e in ev):
        raise ValueError("цей прохід сигналів зроблено попередньою версією (без рівнів на момент появи в списку) — "
                         "натисни «Знайти сигнали» ще раз (≈2 хв)")
    rows, split, P = simulate(BD.Cache(data_dir), ev, params, run_["start_ms"], run_["end_ms"], window)
    filtered = P.pop("_filtered", 0)
    if window == "oos":
        m = metrics(rows, run_["end_ms"], split, filtered)
    else:
        m = metrics(rows, split, run_["start_ms"], filtered)
    trades = [{k: r.get(k) for k in TRADE_KEEP} for r in rows if r["outcome"] == "trade"]
    result = {"metrics": m, "trades": trades, "split_ms": split, "window": window, "base_id": base_id,
              "filters_text": filters_text(P)}
    el = round(time.time() - t0, 2)
    rid = TradeStore(data_dir).save(sig_run_id, P, result, el)
    return TradeStore(data_dir).get(rid)


# ============================================================================ розрізи (етап 4)
def _band(x, edges, labels):
    if x is None:
        return None
    for e, lab in zip(edges, labels):
        if x < e:
            return lab
    return labels[-1]


SLICES = [
    ("Тип зони", lambda t: t.get("zone_type") or "OB", ["OB", "брейкер"]),
    ("Бали скринера", lambda t: None if t.get("score") is None else ("≤ −1" if t["score"] <= -1 else "≥ 3" if t["score"] >= 3 else str(t["score"])),
     ["≤ −1", "0", "1", "2", "≥ 3"]),
    ("R:R сигналу", lambda t: _band(t.get("rr"), (3.5, 4.5, 6), ("3–3,5", "3,5–4,5", "4,5–6", "> 6")), ["3–3,5", "3,5–4,5", "4,5–6", "> 6"]),
    ("Напрям", lambda t: t.get("dir"), ["Long", "Short"]),
    ("«Ніж» (різка свічка)", lambda t: None if t.get("knife") is None else ("так" if t["knife"] else "ні"), ["так", "ні"]),
    ("Тісний стоп (< 0,6 середньої свічки)", lambda t: None if t.get("flags") is None else ("так" if is_tight(t["flags"]) else "ні"), ["так", "ні"]),
    ("Свіп ліквідності перед BOS", lambda t: None if "sweep" not in t or t.get("flags") is None else ("так" if t.get("sweep") else "ні"), ["так", "ні"]),
    ("Вхід у Фібо 0,5–0,79", lambda t: None if t.get("fib_ok") is None else ("так" if t["fib_ok"] else "ні"), ["так", "ні"]),
    ("Підтверджень обсягом", lambda t: None if t.get("vol_conf") is None else ("≥ 3" if t["vol_conf"] >= 3 else str(t["vol_conf"])), ["0", "1", "2", "≥ 3"]),
    ("Відкат на сильному обсязі", lambda t: None if t.get("flags") is None else ("так" if any("сильному обсязі" in str(f) for f in t["flags"]) else "ні"), ["так", "ні"]),
    ("Стоп за гнотом", lambda t: None if t.get("flags") is None else ("так" if any(str(f).startswith("стоп за гнотом") for f in t["flags"]) else "ні"), ["так", "ні"]),
    ("Ширина стопа, % ціни", lambda t: _band(t.get("risk_pct"), (1, 2, 3), ("< 1", "1–2", "2–3", "≥ 3")), ["< 1", "1–2", "2–3", "≥ 3"]),
    ("Годин до входу", lambda t: _band(t.get("hours_to_fill"), (7, 25, 73), ("≤ 6", "7–24", "25–72", "> 72")), ["≤ 6", "7–24", "25–72", "> 72"]),
]
MIN_N = 30   # менше угод у групі — позначаємо «мало даних»


def slices(trades):
    closed = [t for t in trades if t.get("state") == "closed"]
    out = []
    for name, f, order in SLICES:
        groups = {}
        missing = 0
        for t in closed:
            k = f(t)
            if k is None:
                missing += 1
                continue
            groups.setdefault(k, []).append(t)
        rows = []
        for k in order:
            g = groups.get(k) or []
            if not g:
                continue
            a = [t["r_a"] for t in g]
            b = [t["r_b"] for t in g if t.get("r_b") is not None]
            gl = -sum(x for x in a if x < 0)
            rows.append({"group": k, "n": len(g), "winrate": round(sum(1 for x in a if x > 0) / len(g) * 100, 1),
                         "avg_a": round(sum(a) / len(g), 3), "sum_a": round(sum(a), 1),
                         "avg_b": round(sum(b) / len(b), 3) if b else None,
                         "pf": round(sum(x for x in a if x > 0) / gl, 2) if gl > 0 else None, "small": len(g) < MIN_N})
        out.append({"name": name, "rows": rows, "missing": missing})
    return out


def open_oos(data_dir, sig_store, base_id):
    """Перевірка поза вибіркою: ті самі правила прогону base_id на останніх 4 міс. Кожне відкриття записується в журнал."""
    ts = TradeStore(data_dir)
    base = ts.get(base_id)
    if not base or base.get("window") != "dev":
        raise ValueError("обери прогін угод на періоді розробки (без перевірки поза вибіркою)")
    res = run(data_dir, sig_store, base["sig_run_id"], base["params"], window="oos", base_id=base_id)
    ts.log_oos(base_id, res["id"], base["sig_run_id"], base.get("filters_text") or filters_text(base["params"]))
    return res


# ============================================================================ висновок простими словами
def _r(x):
    return f"{x:+.2f}".replace(".", ",") + "R"


def _n(x, d=1):
    return f"{x:.{d}f}".replace(".", ",")


def _plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def summary(res, risk_pct=1.0):
    """Висновок для людини за результатом прогону (без моделей: прості правила над метриками й розрізами).
    Повертає {"tone": "pos"|"neu"|"neg"|"few", "verdict": str, "points": [str]}."""
    m, P = res["metrics"], res.get("params") or {}
    A, B = m["a"], m["b"]
    n = A.get("n") or 0
    oos = res.get("window") == "oos"
    pts = []
    if n == 0:
        return {"tone": "few", "verdict": "Жодної закритої угоди — висновку немає.", "points": [
            "Можливо, фільтри надто суворі: послаб їх або прожени без фільтрів."]}
    avg, pf = A["avg"], A.get("pf") or 0
    best_avg = max(avg, B.get("avg") if B.get("n") else -9)
    if n < 30:
        tone, verdict = "few", f"Угод замало ({n}) — будь-який висновок тут випадковий."
    elif best_avg >= 0.1 and (pf >= 1.15 or (B.get("pf") or 0) >= 1.15):
        tone, verdict = "pos", f"Правила дали перевагу: у середньому {_r(best_avg)} на угоду після комісій."
    elif best_avg > -0.05:
        tone, verdict = "neu", f"Результат близький до нуля ({_r(avg)} на угоду): правила майже не заробляють і не втрачають — переваги не видно."
    else:
        tone, verdict = "neg", f"Правила збиткові: у середньому {_r(avg)} на угоду після комісій."
    if oos:
        verdict = "Поза вибіркою: " + verdict[0].lower() + verdict[1:]

    # гроші й частота
    weeks = (m.get("period_days") or 0) / 7
    freq = f"≈ {_n(n / weeks)} угоди на тиждень, " if weeks >= 1 else ""
    k = round(n / A["wins"]) if A.get("wins") else None
    pts.append(f"{n} {_plural(n, 'угода', 'угоди', 'угод')} ({freq}виграє {_n(A['winrate'])}%"
               + (f", тобто приблизно 1 з {k}" if k and k > 1 else "") + ").")
    tot = round(A["sum"] * risk_pct)
    dd_pct = round(A["max_dd"] * risk_pct)
    pts.append(f"Якщо ризикувати {_n(risk_pct, 0)}% депозиту на угоду, за період це ≈ {'+' if tot > 0 else ''}{tot}% депозиту "
               f"(без складного відсотка), а найгірша просадка — ≈ {dd_pct}%"
               + (" — такої просадки депозит не витримав би." if dd_pct >= 50 else "."))
    pts.append(f"Найдовша серія збитків — {A['max_streak']} поспіль. На неї треба бути готовим психологічно й за розміром ризику.")
    if n < 30:
        pts.append("Що далі: послаб фільтри — для висновку потрібно щонайменше 30, а краще 100+ угод.")
        return {"tone": tone, "verdict": verdict, "points": pts}
    # ведення
    if B.get("n"):
        db, dd = B["avg"] - A["avg"], A["max_dd"] - B["max_dd"]
        if abs(db) < 0.02:
            t = "середній R майже той самий"
        else:
            t = f"середній R {'кращий' if db > 0 else 'гірший'} на {_n(abs(db), 2)}R"
        tdd = "просадка така сама" if abs(dd) < 1 else f"просадка {'менша' if dd > 0 else 'більша'} на {_n(abs(dd), 0)}R"
        pts.append(f"Ведення «3R → беззбиток, 4R → 50%» замість «до цілі або стопа»: {t}, {tdd}, "
                   f"серія збитків {B['max_streak']} замість {A['max_streak']}.")
    # входи
    sig = m.get("signals") or 0
    tf = next((c["n"] for c in m.get("cancels", []) if c["reason"] == "target_first"), 0)
    if sig and tf / sig >= 0.2:
        pts.append(f"{round(tf / sig * 100)}% сигналів ціна дійшла до цілі, так і не дійшовши до рівня лімітки: такі угоди "
                   "пропущено. Це не збиток, але й не заробіток; більший допуск входу ловить частину з них, але зменшує R:R.")
    if m.get("rr_median") and m["rr_median"] >= 5 and n >= 30:
        mfe3 = next((x["share"] for x in m.get("mfe", []) if x["k"] == 3), None)
        pts.append(f"Цілі здебільшого далекі (медіана R:R {_n(m['rr_median'])}), а до 3R доходить лише "
                   f"{_n(mfe3 or 0, 0)}% угод — частина виграшів «розвертається» до стопу.")
    # розрізи: де найкраще / найгірше (лише групи ≥ MIN_N угод і лише при помітній різниці)
    if n >= 60:
        cand = []
        for g in slices(res.get("trades") or []):
            rows = [x for x in g["rows"] if not x["small"]]
            if len(rows) < 2:
                continue
            hi, lo = max(rows, key=lambda x: x["avg_a"]), min(rows, key=lambda x: x["avg_a"])
            if hi["avg_a"] - lo["avg_a"] >= 0.3:
                cand.append((hi["avg_a"] - lo["avg_a"], g["name"], hi, lo))
        cand.sort(key=lambda x: -x[0])
        if cand:
            pts.append("Найбільша різниця між групами: " + "; ".join(
                f"{name} — «{hi['group']}» {_r(hi['avg_a'])} ({hi['n']}) проти «{lo['group']}» {_r(lo['avg_a'])} ({lo['n']})"
                for _, name, hi, lo in cand[:3]) + ". Це підказки для фільтрів, а не доведені правила.")
    nf = sum(1 for k in FILTER_KEYS if P.get(k) not in (None, False, "both", "all"))
    if nf >= 3:
        pts.append(f"Увімкнено {nf} фільтри: що більше фільтрів, то легше випадково «підігнати» правила під минуле.")
    # що далі
    if oos:
        nxt = ("Перевага підтвердилась на даних, які не брали участі в підборі. Наступний крок — паперові угоди за цими правилами."
               if tone == "pos" else "Те, що було видно на розробці, не повторилось — імовірно, це було підібрано під минуле.")
    elif tone == "pos":
        nxt = "Що далі: якщо ці правила мають логіку зі стратегії — один раз відкрий для них останні 4 місяці («Детальніше» → «Перевірка поза вибіркою»)."
    elif tone == "few":
        nxt = "Що далі: послаб фільтри — для висновку потрібно щонайменше 30, а краще 100+ угод."
    else:
        nxt = "Що далі: подивись «Детальніше» → «Розрізи» і спробуй 1–2 логічні фільтри у «Фільтрах»; інший шлях — додати 1h-уточнення входу (окремий етап)."
    pts.append(nxt)
    pts.append("Пам'ятай: це спрощена модель на 1h-свічках без 1h-уточнення; результат бектесту — гіпотеза, яку підтверджують паперові угоди.")
    return {"tone": tone, "verdict": verdict, "points": pts}


# ============================================================================ висновок по розрізах
SLICE_HINTS = {   # назва розрізу -> (фільтр у кроці 3 або None, логіка зі стратегії або None)
    "Тип зони": ("«Тип зони»", "за стратегією брейкер допускається, але це окремий тип входу — його варто оцінювати окремо від ордерблоків"),
    "Бали скринера": ("«Мін. бали»", "бали — це зведена якість сигналу: підтвердження обсягом, свіп, Фібо мінус ризики"),
    "R:R сигналу": ("«Макс. R:R»", "за стратегією, далекі цілі часто не доходять"),
    "Напрям": ("«Напрям»", None),
    "«Ніж» (різка свічка)": ("«без ножа»", "за стратегією, уникаю різких інструментів — гніт знімає стоп"),
    "Тісний стоп (< 0,6 середньої свічки)": ("«без тісного стопу»", "звичайний шум ціни вибиває тісний стоп"),
    "Свіп ліквідності перед BOS": (None, "за стратегією, вхід після зняття ліквідності надійніший"),
    "Вхід у Фібо 0,5–0,79": (None, None),
    "Підтверджень обсягом": (None, "за стратегією, підтвердження обсягом"),
    "Відкат на сильному обсязі": (None, "за стратегією, ретест на зростаючому обсязі — радше продовження руху"),
    "Стоп за гнотом": (None, None),
    "Ширина стопа, % ціни": (None, "при вузькому стопі комісії з'їдають більшу частку R"),
    "Годин до входу": ("«Термін лімітки»", None),
}


def slices_summary(sl):
    """Висновок по розрізах: які ознаки найсильніше ділять угоди на гірші й кращі, і що з цим робити."""
    found, small = [], 0
    for g in sl:
        rows = [x for x in g["rows"] if not x["small"]]
        small += sum(1 for x in g["rows"] if x["small"])
        if len(rows) < 2:
            continue
        hi, lo = max(rows, key=lambda x: x["avg_a"]), min(rows, key=lambda x: x["avg_a"])
        if hi["avg_a"] - lo["avg_a"] >= 0.3:
            found.append((hi["avg_a"] - lo["avg_a"], g["name"], hi, lo))
    found.sort(key=lambda x: -x[0])
    if not sl or not any(g["rows"] for g in sl):
        return None
    if not found:
        return {"tone": "neu", "verdict": "Помітної різниці між групами немає: жодна ознака не відділяє кращі угоди від гірших (різниця < 0,3R).",
                "points": ["Фільтри за цими ознаками навряд чи допоможуть. Інший шлях — змінити саме правило входу (напр., 1h-уточнення)."]}
    names = [f[1] for f in found[:3]]
    pts = []
    for d, name, hi, lo in found[:5]:
        flt, why = SLICE_HINTS.get(name, (None, None))
        pts.append(f"{name}: «{hi['group']}» {_r(hi['avg_a'])} на угоду ({hi['n']}) проти «{lo['group']}» {_r(lo['avg_a'])} ({lo['n']}). "
                   + (f"Чому: {why}. " if why else "Чіткої логіки немає — може бути випадковістю. ")
                   + (f"Фільтр: {flt} у «Фільтрах»." if flt else "Окремого фільтра поки немає."))
    both = [n for n in names if n in ("Тісний стоп (< 0,6 середньої свічки)", "Ширина стопа, % ціни")]
    if len(both) == 2:
        pts.append("«Тісний стоп» і «ширина стопа» — по суті про одне: досить одного з них.")
    if small:
        pts.append(f"Груп із менш ніж 30 угодами: {small}. Їх не враховано — там легко побачити випадковість.")
    pts.append("Як діяти: обери 1–2 ознаки, де є і логіка, і фільтр, увімкни їх у «Фільтрах» і прожени угоди ще раз. "
               "Не вмикай усе підряд — так правила підганяються під минуле.")
    return {"tone": "neu", "verdict": "Найсильніше на результат впливають: " + ", ".join(f"«{n}»" for n in names) + ".", "points": pts}
