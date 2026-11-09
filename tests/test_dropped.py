#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.5.4: чому пара вибула зі списку скринера («<<< ВИБУЛА: ... — причина» у лозі + results/dropped_log.csv).
Запуск: python3 tests/test_dropped.py"""
import contextlib
import csv
import io
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


SH = {"symbol": "SUIUSDT", "dir": "Short", "status": "підхід до зони", "zone_low": 0.8558, "zone_high": 0.8627,
      "entry": 0.857525, "stop": 0.8829, "target": 0.6773}
LG = {"symbol": "ERAUSDT", "dir": "Long", "status": "підхід до зони", "zone_low": 0.069, "zone_high": 0.0705,
      "entry": 0.0698, "stop": 0.0685, "target": 0.08504}

# ================================================================ 1. причини
why = R.drop_reason(SH, [], [], 0.80, 4)
check("шорт: ціна пішла вниз від зони далі за поріг підходу → «відійшла на X%»", "відійшла на 7.0%" in why and "до 4%" in why, why)
why = R.drop_reason(LG, [], [], 0.075, 4)
check("лонг: ціна пішла вгору від зони → «відійшла на 6.0%»", "відійшла на 6.0%" in why, why)
check("ціна вище стопа шорту → «сценарій зламано»", "сценарій зламано" in R.drop_reason(SH, [], [], 0.89, 4))
check("ціна дійшла до цілі без входу", "до цілі" in R.drop_reason(SH, [], [], 0.67, 4))
check("ціна в зоні / був дотик → «зону відпрацьовано»", "відпрацьовано" in R.drop_reason(SH, [], [], 0.86, 4)
      and "о 04:10" in R.drop_reason(SH, [], [], 0.84, 4, touched_at="04:10")
      and "відпрацьовано" in R.drop_reason(dict(SH, status="у зоні (1-й дотик)"), [], [], 0.84, 4))
rej = [dict(SH, zone_low=0.85)]
check("та сама пара тепер у відсіяних → «проти структури»", "проти структури" in R.drop_reason(SH, [], rej, 0.84, 4))
other = [dict(SH, zone_low=0.83, zone_high=0.835)]
why = R.drop_reason(SH, other, [], 0.82, 4)
check("та сама пара з іншою зоною → «нова структура 4h; нова зона»", "нова зона 0.83–0.835" in why, why)
why = R.drop_reason(SH, [], [], 0.84, 4)
check("ціна близько, але зони вже немає → «перестала проходити умови»", "перестала проходити умови" in why, why)
check("ціни немає → пояснення без падіння", "немає ціни" in R.drop_reason(SH, [], [], None, 4))

# ================================================================ 2. після скану: лог і CSV
out_dir = tempfile.mkdtemp()
calls = []
mod = types.SimpleNamespace(save_json=lambda *a: None, append_log=lambda *a, **k: calls.append(a),
                            fetch_prices=lambda: {"SUIUSDT": 0.80, "ERAUSDT": 0.0702, "LINKUSDT": 10.5}, MKT={"futures": True})
R.install_hooks(mod)
R.DROP.update(prev=None, touched={})
sys.argv = ["smc_screener.py", "--watch", "30", "--approach", "4"]
LINK = {"symbol": "LINKUSDT", "dir": "Short", "status": "підхід до зони", "zone_low": 10.776, "zone_high": 10.96,
        "entry": 10.822, "stop": 11.05, "target": 10.05}
for r in (SH, LG, LINK):
    r.update(score=-1, price=0, rr=3, dist=1, flags=[], bos_time="", rank=1)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    mod.append_log(out_dir, "09.11 02:57", [SH, LG, LINK], [])
check("перший скан після старту — порівнювати нема з чим, тиша", "ВИБУЛА" not in buf.getvalue(), buf.getvalue())
with contextlib.redirect_stdout(buf):
    mod.append_log(out_dir, "09.11 04:13", [SH, LG, LINK], [], live=[(LG, 0.0703)])  # живий дотик ERA о 04:xx
    mod.append_log(out_dir, "09.11 08:33", [LINK], [])
txt = buf.getvalue()
check("у лозі скринера: «<<< ВИБУЛА: SUIUSDT Short ... відійшла на 7.0%»",
      "<<< ВИБУЛА: SUIUSDT Short (зона 0.8558–0.8627) — ціна 0.8 відійшла на 7.0%" in txt, txt)
check("ERA, де був живий дотик, — «зону відпрацьовано»", "ВИБУЛА: ERAUSDT Long" in txt and "відпрацьовано" in txt.split("ERAUSDT")[1], txt)
check("пара, що лишилась у списку, не згадується", "ВИБУЛА: LINKUSDT" not in txt)
rows = list(csv.DictReader(open(os.path.join(out_dir, "dropped_log.csv"), encoding="utf-8-sig")))
check("results/dropped_log.csv: 2 рядки з часом, ціною й причиною", len(rows) == 2 and rows[0]["time"].endswith("08:33")
      and rows[0]["price"] == "0.8" and "відійшла" in rows[0]["reason"], rows)
with contextlib.redirect_stdout(buf):
    mod.append_log(out_dir, "09.11 09:03", [LINK], [])
check("наступний скан без змін — нових рядків немає", len(list(csv.DictReader(open(os.path.join(out_dir, "dropped_log.csv"), encoding="utf-8-sig")))) == 2)
mod.fetch_prices = lambda: (_ for _ in ()).throw(RuntimeError("мережа"))
with contextlib.redirect_stdout(buf):
    mod.append_log(out_dir, "09.11 09:33", [], [])
check("мережа недоступна — причина все одно записана, скринер не падає", "ВИБУЛА: LINKUSDT" in buf.getvalue() and "немає ціни" in buf.getvalue())
check("оригінальний append_log скринера викликається щоразу (signals_log.csv як раніше)", len(calls) == 5, len(calls))

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"))
sys.exit(0 if ok else 1)
