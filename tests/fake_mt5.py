# -*- coding: utf-8 -*-
"""Фейкові файли робота SMC_Bridge (MT5) для тестів форексу: status.json + PAIR_H4/H1/M15.csv у папці.
Час у файлах — час сервера брокера (UTC+2), як у RoboForex взимку."""
import json
import math
import os
import time

OFFSET = 7200  # сервер = UTC+2
STEP = {"H4": 14400, "H1": 3600, "M15": 900}


def _sine(pair, tf, n, now):
    seed = sum(map(ord, pair))
    step = STEP[tf]
    last = int(now) - int(now) % step
    base = 1.0 + (seed % 50) / 100 if not pair.endswith("JPY") else 100 + seed % 60
    rows = []
    for i in range(n):
        t = last - (n - 1 - i) * step
        x = t / 3600
        c = base * (1 + 0.004 * math.sin(x / 37 + seed) + 0.002 * math.sin(x / 5))
        o = base * (1 + 0.004 * math.sin((x - step / 3600) / 37 + seed) + 0.002 * math.sin((x - 1) / 5))
        rows.append((t, o, max(o, c) * 1.0004, min(o, c) * 0.9996, c, 500 + (seed * i) % 300))
    return rows


def demo_bullish(now, scale=0.01):
    """Сетап із самоперевірки скринера (лонг від ордерблока), у масштабі валютної пари; остання свічка — поточна."""
    closes = [100, 101, 102, 100, 98, 99.5, 101, 102.5, 104, 103, 102.5, 101.5, 100.5, 99.8, 99.0,
              101.5, 103.8, 105.5, 106.5, 107.0, 106.0, 105.0, 104.0, 103.0, 102.0, 101.0, 100.4]
    last = int(now) - int(now) % 14400
    rows, prev = [], closes[0]
    for i, cl in enumerate(closes):
        t = last - (len(closes) - 1 - i) * 14400
        o = prev
        rows.append((t, o * scale, (max(o, cl) + 0.3) * scale, (min(o, cl) - 0.3) * scale, cl * scale, 1000))
        prev = cl
    return rows


def write(d, pairs, now=None, missing=(), demo=(), status_age=0, bars=600):
    """pairs — список пар; missing — яких «немає в брокера»; demo — пари з готовим лонг-сетапом."""
    now = time.time() if now is None else now
    os.makedirs(d, exist_ok=True)
    items = []
    for p in pairs:
        ok = p not in missing
        name = p + ".r" if ok else ""
        if ok:
            for tf in ("H4", "H1", "M15"):
                rows = demo_bullish(now) if (p in demo and tf == "H4") else _sine(p, tf, bars, now)
                dg = 3 if p.endswith("JPY") else 5
                with open(os.path.join(d, f"{p}_{tf}.csv"), "w") as f:
                    for t, o, h, l, c, v in rows:
                        f.write(f"{t + OFFSET},{o:.{dg}f},{h:.{dg}f},{l:.{dg}f},{c:.{dg}f},{v}\n")
            last = rows[-1][4]
        items.append({"pair": p, "name": name, "ok": ok, "digits": 5, "bid": round(last, 5) if ok else 0,
                      "ask": round(last * 1.00005, 5) if ok else 0, "tick_time": int(now) + OFFSET if ok else 0})
    st = {"v": 1, "ea_version": "1.00", "gmt": int(now), "server_time": int(now) + OFFSET, "server": "RoboForex-ECN",
          "company": "RoboForex Ltd", "bars": bars, "pairs": items}
    p = os.path.join(d, "status.json")
    with open(p, "w") as f:
        json.dump(st, f)
    if status_age:
        os.utime(p, (time.time() - status_age, time.time() - status_age))
    return d
