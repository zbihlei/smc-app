#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Автотест вкладки «Підсумок» (Playwright) на 20 справжніх угодах. Запуск: python3 tests/test_summary_ui.py [папка_скріншотів]"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from fixtures import seed_real_journal  # noqa: E402

PORT = 8794
URL = f"http://127.0.0.1:{PORT}"
shots = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
os.makedirs(shots, exist_ok=True)
data_dir = tempfile.mkdtemp(prefix="smcs_data_")
seed_real_journal(data_dir)
with open(os.path.join(data_dir, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"screener_path": os.path.join(HERE, "fake_screener.py"), "results_dir": tempfile.mkdtemp()}, f)

ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


app = subprocess.Popen([sys.executable, os.path.join(APP, "app.py"), "--port", str(PORT), "--no-browser"],
                       env=dict(os.environ, SMC_APP_DATA=data_dir, SMC_SHEET_URL="", SMC_SHEET_TOKEN=""),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
for _ in range(60):
    try:
        urllib.request.urlopen(URL + "/api/status", timeout=1)
        break
    except Exception:
        time.sleep(0.1)
errors = []
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(URL)
        pg.click(".tab[data-tab=summary]")
        expect(pg.locator("#sKpi")).to_contain_text("+15,96R", timeout=8000)
        k = pg.locator("#sKpi").inner_text()
        check("картки: 20 угод, вінрейт 40%, сума +15,96R", "20" in k and "40%" in k)
        check("макс. просадка 3,30R, найдовша серія 3", "3,30R" in k and "Найдовша серія збитків\n3" in k, k)
        check("беззбитковий вінрейт 20,9% при R:R 3,78", "20,9%" in k and "3,78" in k)
        check("крива накопиченого R: 20 точок", pg.locator("#sEq circle").count() == 20)
        check("гіпотез 7", pg.locator("#sHyp .hyp").count() == 7)
        choch = pg.locator("#sHyp .hyp:has-text('CHoCH до входу')").inner_text()
        check("CHoCH: +1,48R (6) проти +0,51R (14), «поки на користь»", "+1,48R" in choch and "+0,51R" in choch and "поки на користь" in choch, choch)
        check("далекі цілі: «поки проти», сценарій 3R +10,50R", "поки проти" in pg.locator("#sHyp .hyp:has-text('далі за 4R')").inner_text()
              and "+10,50R" in pg.locator("#sMfe").inner_text())
        check("вузькі стопи: «різниці не видно» (обидві групи +0,80R)", "різниці не видно" in pg.locator("#sHyp .hyp:has-text('Вузькі стопи')").inner_text())
        check("розрізів 10", pg.locator("#sGroups .gcard").count() == 10)
        zone = pg.locator("#sGroups .gcard:has-text('Тип зони')").inner_text()
        check("тип зони: 4h OB 11 угод +11,09R, 1h OB +0,24R", "+11,09R" in zone and "+0,24R" in zone, zone)
        mgmt = pg.locator("#sGroups .gcard:has-text('Ведення угоди')").inner_text()
        check("ведення: «BE на 3R» є в списку (0 угод)", "BE на 3R" in mgmt)
        check("MFE: 6 угод, середній 3,48R", "Угод з MFE: 6" in pg.locator("#sMfe").inner_text() and "3,48R" in pg.locator("#sMfe").inner_text())
        check("скринер: підказка, поки немає прив'язаних сигналів", "Ще немає закритих угод" in pg.locator("#sSig").inner_text())
        pg.screenshot(path=os.path.join(shots, "s1_summary.png"), full_page=True)

        pg.click("#sPeriod [data-p='7']")
        expect(pg.locator("#sNote")).to_contain_text("останні 7 днів")
        check("період «7 днів» перемикається", True)
        pg.click("#sPeriod [data-p='all']")
        expect(pg.locator("#sKpi")).to_contain_text("+15,96R")

        # нова закрита угода оновлює підсумок
        r = pg.request.post(URL + "/api/journal/save", headers={"X-SMC-App": "1"}, data=json.dumps({"trade": {
            "date": "2026-11-04", "instrument": "WLD", "dir": "Short", "entry": "0,398", "stop": "0,403", "target": "0,3817",
            "result": "3,26", "mfe": "6", "rules": "ні", "choch": "ні", "zone_type": "Локальний 1h OB", "support4h": "так",
            "by_screener": "так", "management": "Без змін", "risk": "1"}}))
        check("нова угода WLD збережена через API", r.ok and r.json()["n"] == 21, r.text()[:200])
        pg.click(".tab[data-tab=journal]")
        pg.click(".tab[data-tab=summary]")
        expect(pg.locator("#sKpi")).to_contain_text("+19,22R")
        check("підсумок оновився: +19,22R, 21 угода, 1h OB тепер 5 угод",
              "+19,22R" in pg.locator("#sKpi").inner_text() and "5" in pg.locator("#sGroups .gcard:has-text('Тип зони') tr:has-text('Локальний')").inner_text())
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.wait_for_timeout(300)
        check("вузьке вікно без горизонтальної прокрутки", pg.evaluate("document.documentElement.scrollWidth") <= 392)
        b.close()
    check("помилок у консолі браузера немає", not errors, errors[:3])
finally:
    app.terminate()
    try:
        app.wait(5)
    except Exception:
        app.kill()

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"), "| скріншоти:", shots)
sys.exit(0 if ok else 1)
