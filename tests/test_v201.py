#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.0.1: папку додатка перейменували (~/smc_app → ~/Rulebound) — шляхи з settings.json підхоплюються з нової папки.
Запуск: python3 tests/test_v201.py"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
tmp = tempfile.mkdtemp()
os.environ["SMC_APP_DATA"] = tmp
sys.path.insert(0, APP)
import app  # noqa: E402

ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


gone = os.path.join(tmp, "smc_app_old_gone")  # такої папки немає
with open(app.SETTINGS_PATH, "w", encoding="utf-8") as f:
    json.dump({"screener_path": os.path.join(gone, "smc_screener.py"), "results_dir": os.path.join(gone, "results")}, f)
os.makedirs(os.path.join(app.APP_DIR, "results"), exist_ok=True)
s = app.load_settings()
check("скринер зі старої (зниклої) папки → з папки додатка", s["screener_path"] == os.path.join(app.APP_DIR, "smc_screener.py"), s["screener_path"])
check("results зі старої папки → з папки додатка", s["results_dir"] == os.path.join(app.APP_DIR, "results"), s["results_dir"])

kept = os.path.join(tmp, "my_results")  # батьківська папка існує — свідомий вибір, не чіпаємо
with open(app.SETTINGS_PATH, "w", encoding="utf-8") as f:
    json.dump({"results_dir": kept, "screener_path": os.path.join(tmp, "інший_скринер.py")}, f)
s = app.load_settings()
check("шлях в існуючій папці не змінюється", s["results_dir"] == kept, s["results_dir"])
check("інший файл скринера в існуючій папці не змінюється", s["screener_path"] == os.path.join(tmp, "інший_скринер.py"))

check("версія 2.0.1", app.APP_VERSION == "2.0.1")
src = open(os.path.join(APP, "gitsync.py"), encoding="utf-8").read() + open(os.path.join(APP, "static", "app.js"), encoding="utf-8").read()
check("підказки git без ~/smc_app", "~/smc_app" not in src and "cd ~/Rulebound" in src)

print("\nУСЕ ГАРАЗД" if ok else "\nЄ ПОМИЛКИ")
sys.exit(0 if ok else 1)
