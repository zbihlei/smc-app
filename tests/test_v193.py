#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.9.3: «підхід» у середніх свічках у команді скринера; копія історії MT5 для бектесту; причина вибуття.
Запуск: python3 tests/test_v193.py"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ["SMC_APP_DATA"] = tempfile.mkdtemp(prefix="smc193_")
import app  # noqa: E402
import fake_mt5  # noqa: E402
import screener_runner as R  # noqa: E402

ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


s = dict(app.DEFAULTS, screener_path="smc_screener.py")
cmd = app.build_command(s)
check("за замовчуванням у команді «--approach-atr 3» (крипто)", "--approach-atr" in cmd and cmd[cmd.index("--approach-atr") + 1] == "3", cmd)
fx = app.build_fx_command(s)
check("і у форекс-команді теж", "--approach-atr" in fx and fx[fx.index("--approach-atr") + 1] == "3")
check("0 = вимкнено: параметра в команді немає", "--approach-atr" not in app.build_command(dict(s, approach_atr=0)))

d = fake_mt5.write(tempfile.mkdtemp(), ["EURUSD", "GBPUSD"], bars=700)
r = app.fx_export_history(dict(s, fx_dir=d, fx_pairs="EURUSD,GBPUSD"))
out = os.path.join(os.environ["SMC_APP_DATA"], "fx_history")
names = sorted(os.listdir(out))
check("історія MT5: скопійовано H4 і H1 обох пар + status.json (без M15)", r["files"] == 4 and names == [
    "EURUSD_H1.csv", "EURUSD_H4.csv", "GBPUSD_H1.csv", "GBPUSD_H4.csv", "status.json"], (r, names))
check("у відповіді — скільки свічок у файлах", r["bars"] == {"H1": 700, "H4": 700}, r["bars"])

sys.argv = ["x", "--approach", "4", "--approach-atr", "3"]
p = {"symbol": "EURUSD", "dir": "Short", "zone_low": 1.1254, "zone_high": 1.126, "stop": 1.129, "target": 1.10, "status": "підхід до зони", "atr_pct": 0.24}
why = R.drop_reason(p, [], [], 1.1056, 4.0)
check("причина вибуття: «відійшла … це 7.5 середньої свічки (показую до 3 свічок)»", "7.5 середньої свічки (показую до 3 свічок)" in why, why)

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"))
sys.exit(0 if ok else 1)
