#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Звірка вкладки «Підсумок» з аркушем «Підсумок» Google Таблиці на 20 справжніх угодах (стан 04.11.2026).
Запуск: python3 tests/test_stats.py"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import journal as J  # noqa: E402
import stats as S  # noqa: E402

from fixtures import REAL_TRADES as T, seed_real_journal  # noqa: E402

ok = True


def check(name, got, want):
    global ok
    good = got == want
    print(("OK   " if good else "FAIL ") + f"{name}: {got}" + ("" if good else f" (у таблиці {want})"))
    ok = ok and good


jr = seed_real_journal(tempfile.mkdtemp())
st = S.build(jr.list(), "all")
g = st["general"]
print("— Загальне (аркуш «Підсумок», стовпці A:B)")
check("Угод", g["n"], 20)
check("Прибуткових", g["wins"], 8)
check("Збиткових", g["losses"], 11)
check("Беззбиткових", g["be"], 1)
check("Частка прибуткових, %", g["winrate"], 40.0)
check("Сума результатів (R)", g["sum"], 15.96)
check("Середній R на угоду", g["avg"], 0.8)
check("Середній виграш (R)", g["avg_win"], 3.37)
check("Середній збиток (R)", g["avg_loss"], -1.0)
check("Максимальна просадка (R)", g["max_dd"], 3.3)
check("Найдовша серія збитків", g["longest_streak"], 3)
check("Середній R:R плану", g["avg_rr"], 3.78)
check("Беззбитковий % при цьому R:R", g["be_winrate"], 20.9)

grp = st["groups"]
find = lambda key, label: next(r for r in grp[key] if r["label"] == label)
print("— За правилами / ведення / CHoCH")
check("За правилами: угод, середній R", (find("rules", "так")["n"], find("rules", "так")["avg"]), (8, 2.09))
check("Поза правилами: угод, середній R", (find("rules", "ні")["n"], find("rules", "ні")["avg"]), (12, -0.06))
check("Без змін: угод, середній R", (find("management", "Без змін")["n"], find("management", "Без змін")["avg"]), (18, 0.71))
check("BE на 3R: угод", find("management", "BE на 3R")["n"], 0)
check("З CHoCH: угод, середній R", (find("choch", "так")["n"], find("choch", "так")["avg"]), (6, 1.48))
check("Без CHoCH: угод, середній R", (find("choch", "ні")["n"], find("choch", "ні")["avg"]), (14, 0.51))
print("— За типом зони / підтримка 4h (стовпці E:H)")
check("4h OB: угод, середній, сума", tuple(find("zone", "4h OB")[k] for k in ("n", "avg", "sum")), (11, 1.01, 11.09))
check("Локальний 1h OB", tuple(find("zone", "Локальний 1h OB")[k] for k in ("n", "avg", "sum")), (4, 0.24, 0.94))
check("Підтримка (тіні)", tuple(find("zone", "Підтримка (тіні)")[k] for k in ("n", "avg", "sum")), (1, -1.0, -1.0))
check("Інше", find("zone", "Інше")["n"], 0)
check("Підтримка 4h поряд = так", tuple(find("support", "так")[k] for k in ("n", "avg", "sum")), (2, 1.02, 2.04))
check("Підтримка 4h поряд = ні", tuple(find("support", "ні")[k] for k in ("n", "avg", "sum")), (5, 0.09, 0.44))
print("— MFE")
m = st["mfe"]
check("Угод із заповненим MFE", m["n"], 6)
check("Середній MFE (R)", m["avg"], 3.48)
check("Дійшли до 3R", m["reached3"], 4)
check("З них закрито в 0 або мінус", m["reached3_closed_le0"], 1)
check("Дійшли до 4R", m["reached4"], 2)
check("Ціль взята (MFE ≥ R:R плану)", m["target_taken"], 3)
print("— Останні 7 днів (на 04.11.2026)")
w = S.build(jr.list(), "7", today=__import__("datetime").date(2026, 11, 4))
cl7 = w["general"]
check("Угод", cl7["n"], 10)
check("Сума (R)", cl7["sum"], 8.05)
check("Середній R", cl7["avg"], 0.81)
check("Угод за правилами", find_rules := next(r for r in w["groups"]["rules"] if r["label"] == "так")["n"], 5)
print("— Додаткове")
check("Сценарій «ціль 3R» на угодах з MFE", next(s["sum"] for s in m["scenarios"] if s["label"] == "Фіксована ціль 3R"), 10.5)
check("Гіпотез сформовано", len(st["hypotheses"]) >= 7, True)
check("Крива накопиченого R: остання точка = сума", st["equity"][-1]["cum"], 15.96)

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"))
sys.exit(0 if ok else 1)
