#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Вкладка «Спот»: «шансові» монети на споті (фейковий Binance). Запуск: python3 tests/test_spot.py [папка_скріншотів]"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
sys.path.insert(0, HERE)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
shots = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
os.makedirs(shots, exist_ok=True)

import fake_binance as FB  # noqa: E402
import spot_scan as SS  # noqa: E402

PORT_B = 8779
FB.serve(PORT_B)
BASE = f"http://127.0.0.1:{PORT_B}"
ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


# ================================================================ 1. логіка
P = SS.clean({"skip_top": 2})
u = [s for s, _ in SS.universe(BASE, P)]
check("відбір: без топу, стейблів, токенів з плечем і дрібних монет", sorted(u) == ["ACCUSDT", "FALUSDT", "NEWUSDT", "PLAUSDT", "PMPUSDT"], u)
found = {}
for s in u:
    for r in SS.analyze(s, SS.klines_1d(BASE, s), P):
        found[s] = r
check("накопичення: ціна стоїть, обсяг ×3 → у списку", found.get("ACCUSDT", {}).get("kind") == "накопичення"
      and found["ACCUSDT"]["vol_ratio"] >= 2.5, found.get("ACCUSDT"))
check("новий лістинг: відкат −69% і 2+ тижні без нового мінімуму → у списку", found.get("NEWUSDT", {}).get("kind") == "новий лістинг"
      and found["NEWUSDT"]["drawdown"] > 60, found.get("NEWUSDT"))
check("вже вистрілила (+80% за 5 днів) — ні; досі падає — ні; звичайна — ні", not {"PMPUSDT", "FALUSDT", "PLAUSDT"} & set(found), list(found))
check("незакрита денна свічка не враховується", all(k[0] + SS.DAY <= time.time() * 1000 for k in SS.klines_1d(BASE, "ACCUSDT")))
check("база (мінімум) нижче ціни — рівень, де ідея зламана", found["ACCUSDT"]["base_low"] < found["ACCUSDT"]["price"])
try:
    SS.clean({"vol_min": 50, "vol_max": 10})
    check("некоректні критерії відхиляються", False)
except ValueError:
    check("некоректні критерії відхиляються", True)
P2 = SS.clean({"skip_top": 2, "acc_vol_ratio": 5})
check("суворіший поріг обсягу → накопичення не знайдено", not SS.analyze("ACCUSDT", SS.klines_1d(BASE, "ACCUSDT"), P2))

d = tempfile.mkdtemp()
sc = SS.SpotScanner(d, BASE)
sc.start({"skip_top": 2})
while sc.running():
    time.sleep(0.05)
lat = sc.latest()
check("скан у фоні: результат у файлі, 2 знахідки, «нова сьогодні»", lat and len(lat["found"]) == 2 and all(r["new_today"] for r in lat["found"]), sc.progress)
sc.start({"skip_top": 2})
while sc.running():
    time.sleep(0.05)
check("повторний скан пам'ятає дату першої появи", all(r["first_seen"] for r in sc.latest()["found"]))

# ================================================================ 2. у додатку
names = {}
for fn in sorted(os.listdir(os.path.join(APP, "static"))):
    if fn.endswith(".js") and not fn.startswith("lightweight"):
        src = open(os.path.join(APP, "static", fn), encoding="utf-8").read()
        for n in re.findall(r"^(?:async\s+)?function\s+(\w+)|^(?:const|let)\s+(\w+)\s*=", src, re.M):
            names.setdefault(n[0] or n[1], []).append(fn)
dup = {k: v for k, v in names.items() if len(v) > 1}
check("у скриптах вкладок немає однакових глобальних імен", not dup, dup)

from playwright.sync_api import sync_playwright, expect  # noqa: E402

PORT = 8778
URL = f"http://127.0.0.1:{PORT}"
data = tempfile.mkdtemp(prefix="smcspot_")
with open(os.path.join(data, "settings.json"), "w") as f:
    json.dump({"screener_path": "x", "results_dir": tempfile.mkdtemp()}, f)
app = subprocess.Popen([sys.executable, os.path.join(APP, "app.py"), "--port", str(PORT), "--no-browser"],
                       env=dict(os.environ, SMC_APP_DATA=data, SMC_SPOT_API=BASE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(60):
    try:
        urllib.request.urlopen(URL + "/api/status", timeout=1)
        break
    except Exception:
        time.sleep(0.1)
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1400, "height": 1000})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto(URL)
        pg.click(".tab[data-tab=spot]")
        expect(pg.locator("#spSt")).to_contain_text("ще не сканували")
        pg.click("#spCritBox summary")
        pg.fill("#spForm input[name=skip_top]", "2")
        pg.click("#spForm button[type=submit]")
        expect(pg.locator("#spBody tr")).to_have_count(2, timeout=30000)
        check("скан з вкладки: 2 монети, причина простими словами, посилання на TradingView",
              "ACC" in pg.locator("#spBody").inner_text() and "обсяг за 2 тижні" in pg.locator("#spBody").inner_text()
              and pg.locator("#spBody a[href*='tradingview']").count() == 2)
        check("статус: час скану, перевірено, знайдено", "знайдено 2" in pg.locator("#spSt").inner_text())
        pg.click("#spFilt [data-k='новий лістинг']")
        check("фільтр «нові лістинги»", pg.locator("#spBody tr").count() == 1 and "NEW" in pg.locator("#spBody").inner_text())
        pg.click("#spFilt [data-k='all']")
        pg.check("#spOnlyNew")
        check("«лише нові сьогодні»", pg.locator("#spBody tr").count() == 2)
        pg.screenshot(path=os.path.join(shots, "spot.png"), full_page=True)
        pg.fill("#spForm input[name=acc_vol_ratio]", "5")
        pg.click("#spForm button[type=submit]")
        expect(pg.locator("#spBody tr")).to_have_count(1, timeout=30000)
        check("свої критерії (обсяг ×5) → накопичення відсіяно, критерії запам'ятовано",
              pg.input_value("#spForm input[name=acc_vol_ratio]") == "5")
        r = pg.request.post(URL + "/api/spot/scan", headers={"X-SMC-App": "1"}, data=json.dumps({"params": {"vol_min": 50, "vol_max": 1}}))
        check("некоректні критерії → 400", r.status == 400)
        pg.click(".tab[data-tab=settings]")
        check("у «Налаштуваннях» — автоскан споту раз на добу (увімкнено)", pg.locator("input[name=spot_auto]").is_checked())
        pg.click(".tab[data-tab=spot]")
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.wait_for_timeout(300)
        check("вузьке вікно без горизонтальної прокрутки", pg.evaluate("document.documentElement.scrollWidth") <= 392,
              pg.evaluate("""[...document.querySelectorAll('#tab-spot *')].filter(e => e.getBoundingClientRect().right > 392 && !e.closest('.table-wrap')).slice(0,4).map(e => e.tagName + '.' + e.className)"""))
        errors = [e for e in errors if "status of 400" not in e]
        check("помилок у консолі немає", not errors, errors[:3])
        b.close()
finally:
    app.terminate()

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"), "| скріншоти:", shots)
sys.exit(0 if ok else 1)
