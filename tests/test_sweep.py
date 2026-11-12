#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.9: «стоп уже пробивало» і «схоже на зняття ліквідності» — попередження, обсяг, свічковий патерн, sweeps_log.csv.
Дані — справжні 15m-свічки ARIAUSDT 12.11.2026 (кейс Michel). Запуск: python3 tests/test_sweep.py"""
import csv
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import screener_runner as R  # noqa: E402

ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


T0 = 1794484800000
RAW = [(.17624, .17999, .17561, .17769, 6.43), (.17775, .17879, .17269, .17429, 5.84), (.17431, .17437, .16510, .16540, 10.75),
       (.16546, .17129, .16146, .16752, 19.55), (.16746, .16859, .16210, .16234, 9.93), (.16236, .16409, .15115, .15290, 18.09),
       (.15288, .15354, .14734, .14910, 19.11), (.14903, .14939, .13439, .13721, 32.43), (.13715, .14592, .13135, .13884, 40.57),
       (.13885, .14020, .13110, .13410, 25.38), (.13410, .13543, .12321, .12348, 26.21), (.12348, .14894, .10980, .14348, 128.55),
       (.14339, .17432, .13902, .16619, 79.58), (.16620, .16937, .15801, .16076, 9.01)]
ARIA = [{"t": T0 + i * 900000, "o": o, "h": h, "l": l, "c": c, "v": v, "closed": i < len(RAW) - 1} for i, (o, h, l, c, v) in enumerate(RAW)]
ROW = {"symbol": "ARIAUSDT", "dir": "Long", "status": "підхід до зони", "score": -3, "price": 0.16114, "zone_low": 0.15207,
       "zone_high": 0.16056, "entry": 0.1584375, "stop": 0.14978499, "target": 0.32384, "flags": ["глибокий відкат 94% імпульсу"]}
NOW = T0 + 14 * 900000

s = R.stop_sweep(ARIA, ROW, NOW)
check("ARIA: стоп пробивало, крайня точка 0,1098 — на 26,7% нижче стопа", bool(s) and s["price"] == 0.1098 and abs(s["depth"] - 26.7) < 0.1, s)
check("обсяг свічки пробою ×6,6 до середнього попередніх", s["vol"] is not None and abs(s["vol"] - 6.6) < 0.1, s["vol"])
check("патерн: довгий гніт 35% + поглинання", any("довгий гніт (35%" in p for p in s["pattern"]) and "поглинання попередньої свічки" in s["pattern"], s["pattern"])
check("15m закрилась над зоною → «схоже на зняття ліквідності»", s["reclaim"] == "над зоною" and s["sweep_like"])
fl = R.sweep_flags(ROW, s)
check("текст: «стоп уже пробивало: мін. 0.1098 (26.7% нижче стопа) …»", fl[0].startswith("стоп уже пробивало: мін. 0.1098 (26.7% нижче стопа)")
      and "закрилась би по стопу" in fl[0], fl[0])
check("текст: обсяг, свічка, повернення", fl[1].startswith("схоже на зняття ліквідності: обсяг на пробої ×6.6 до середнього (підвищений)")
      and "15m закрилась над зоною" in fl[1], fl[1])

check("стоп не чіпали → нічого", R.stop_sweep(ARIA[:6], ROW, NOW) is None)
s2 = R.stop_sweep(ARIA[:11], ROW, NOW)
check("ціна ще під стопом (падіння триває) → не «зняття ліквідності», а «після пробою стопа … повернення немає»",
      bool(s2) and not s2["sweep_like"] and s2["reclaim"] == "" and "повернення немає" in R.sweep_flags(ROW, s2)[1], s2)

# дзеркально для шорта
SH = [{"t": T0 + i * 900000, "o": 10, "h": 10.1, "l": 9.9, "c": 10, "v": 100, "closed": True} for i in range(25)]
SH.append({"t": T0 + 25 * 900000, "o": 10.0, "h": 11.2, "l": 9.95, "c": 10.05, "v": 500, "closed": True})
SH.append({"t": T0 + 26 * 900000, "o": 10.05, "h": 10.1, "l": 9.6, "c": 9.7, "v": 150, "closed": True})
srow = {"symbol": "XUSDT", "dir": "Short", "zone_low": 10.2, "zone_high": 10.5, "entry": 10.3, "stop": 10.6, "target": 8.0, "flags": []}
s3 = R.stop_sweep(SH, srow, T0 + 27 * 900000)
check("шорт: макс. 11,2 вище стопа, пін-бар, обсяг ×5, закриття під зоною", bool(s3) and s3["price"] == 11.2 and abs(s3["vol"] - 5) < 0.01
      and s3["pattern"] and s3["pattern"][0].startswith("пін-бар") and s3["reclaim"] == "під зоною" and s3["sweep_like"], s3)
check("шорт: у тексті «макс. … вище стопа»", "макс. 11.2 (5.7% вище стопа)" in R.sweep_flags(srow, s3)[0], R.sweep_flags(srow, s3)[0])

# у скринері: flags, поле для додатка, лог — один раз
out = tempfile.mkdtemp()
calls = []


def fetch_klines(sym, itv, lim):
    calls.append((sym, itv, lim))
    if sym == "BADUSDT":
        raise RuntimeError("немає мережі")
    return ARIA if sym == "ARIAUSDT" else ARIA[:5]


mod = types.SimpleNamespace(fetch_klines=fetch_klines)
rows = [dict(ROW, flags=list(ROW["flags"])), dict(ROW, symbol="OKUSDT", flags=["x"]), dict(ROW, symbol="BADUSDT", flags=[])]
n = R.annotate_sweeps(mod, out, rows)
check("скан: попередження першими у flags лише в ARIA; інші прапорці на місці", n == 1 and rows[0]["flags"][0].startswith("стоп уже пробивало")
      and rows[0]["flags"][1].startswith("схоже на зняття ліквідності") and rows[0]["flags"][2] == "глибокий відкат 94% імпульсу"
      and rows[1]["flags"] == ["x"] and rows[2]["flags"] == [], [r["flags"] for r in rows])
check("15m-свічки за добу; помилка мережі по одній парі не заважає іншим", calls[0] == ("ARIAUSDT", "15m", 96) and len(calls) == 3)
check("поле stop_sweep для додатка", rows[0].get("stop_sweep", {}).get("sweep_like") is True and "stop_sweep" not in rows[1])
L = list(csv.DictReader(open(os.path.join(out, R.SWEEP_FILE), encoding="utf-8-sig")))
check("sweeps_log.csv: один рядок з рівнями, глибиною, обсягом, патерном", len(L) == 1 and L[0]["symbol"] == "ARIAUSDT" and L[0]["ext_price"] == "0.1098"
      and L[0]["depth_pct"] == "26.69" and L[0]["vol_ratio"] == "6.6" and L[0]["reclaim"] == "над зоною" and "поглинання" in L[0]["pattern"], L)
n = R.annotate_sweeps(mod, out, rows)
L = list(csv.DictReader(open(os.path.join(out, R.SWEEP_FILE), encoding="utf-8-sig")))
check("наступний скан: у лог не дублюється, попередження не подвоюються", n == 0 and len(L) == 1
      and sum(f.startswith("стоп уже пробивало") for f in rows[0]["flags"]) == 1, rows[0]["flags"])

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"))
sys.exit(0 if ok else 1)
