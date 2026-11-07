#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Фейковий скринер для тестів додатка без мережі: той самий інтерфейс (MKT, save_json, append_log, main),
що й smc_screener.py, але кандидати генеруються з випадкових свічок. Скан кожні --watch хвилин (можна дробові)."""
import argparse
import csv
import datetime as dt
import json
import os
import random
import sys
import time

MKT = {"futures": True}


def save_json(rows, path, stamp):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"generated": stamp, "count": len(rows),
                   "candidates": [{k: v for k, v in r.items() if k != "chart"} for r in rows]}, f, ensure_ascii=False)


def append_log(out_dir, stamp, rows, rejects, live=None):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "signals_log.csv"), "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow([stamp, "у списку", r["symbol"], r["dir"], r["status"]])


def make_row(sym, d, status, rank, seed):
    rnd = random.Random(seed)
    t0 = int(time.time() // 14400 * 14400 * 1000) - 150 * 14_400_000
    px = rnd.uniform(1, 200)
    chart = []
    for i in range(150):
        o = px
        c = o * (1 + rnd.gauss(0.0015 * d, 0.012))
        h = max(o, c) * (1 + abs(rnd.gauss(0, 0.004)))
        l = min(o, c) * (1 - abs(rnd.gauss(0, 0.004)))
        chart.append([t0 + i * 14_400_000, o, h, l, c, rnd.uniform(1e5, 5e5)])
        px = c
    k = 120
    zl, zh = chart[k][3], chart[k][2]
    entry = (zl + zh) / 2
    stop = zl * 0.998 if d == 1 else zh * 1.002
    risk = abs(entry - stop)
    target = entry + d * risk * rnd.uniform(3.2, 6)
    price = (zh + zl) / 2 if rank == 0 else (zh * 1.02 if d == 1 else zl * 0.98)
    return {"symbol": sym, "dir": "Long" if d == 1 else "Short", "status": status, "rank": rank, "score": rnd.randint(-1, 3),
            "price": price, "zone_low": zl, "zone_high": zh, "entry": entry, "stop": stop, "target": target,
            "target_type": "свінг", "rr": abs(target - entry) / risk, "dist": 0.0 if rank == 0 else 2.0, "fib": 0.62, "fib_ok": True,
            "fvg": 1, "sweep": "свіп мінімуму" if rank == 0 else None, "volume": "імпульс ×1.6 ✓ | відкат ×0.5 ✓", "vol_conf": 2,
            "atr_pct": 2.1, "knife": False, "flags": ["тісний стоп: 0.9% = 0.4× середньої свічки (2.1%)"] if rank == 1 else [],
            "bos_time": "03.11 12:00", "bos_bars_ago": 8, "struct": d, "against": False,
            "chart": chart, "zone_t": chart[k][0], "tf_ms": 14_400_000}


def scan(n):
    base = [("SOLUSDT", 1, "у зоні (1-й дотик)", 0), ("DOGEUSDT", -1, "підхід до зони", 1), ("SUIUSDT", 1, "підхід до зони", 1),
            ("ZECUSDT", -1, "підхід до зони", 1), ("INJUSDT", 1, "підхід до зони", 1), ("TRUMPUSDT", -1, "підхід до зони", 1)]
    return [make_row(s, d, st, rk, i) for i, (s, d, st, rk) in enumerate(base[:n])]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--watch", type=float, default=0)
    p.add_argument("--out", default="results")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--fake-rows", type=int, default=6)
    a, _ = p.parse_known_args()
    if a.selftest:
        print("OK   фейк 1\nOK   фейк 2\n\nПІДСУМОК: усі перевірки пройдено")
        sys.exit(0)
    print(f"Режим стеження: скан кожні {a.watch} хв. Зупинити: Ctrl+C.")
    try:
        while True:
            stamp = dt.datetime.now().strftime("%H:%M")
            rows = scan(a.fake_rows)
            append_log(a.out, stamp, rows, [])
            print(f"[{stamp}] у списку: {len(rows)}, нових: {len(rows)}")
            if rows:
                save_json(rows, os.path.join(a.out, "latest.json"), stamp)
            time.sleep(max(1, a.watch * 60))
    except KeyboardInterrupt:
        print("\nЗупинено.")


if __name__ == "__main__":
    main()
