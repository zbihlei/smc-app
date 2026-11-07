#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Бектест, етап (a): завантаження й кеш (фейковий Binance) + вкладка «Бектест».
Запуск: python3 tests/test_backtest_data.py ШЛЯХ_ДО_smc_screener.py [папка_скріншотів]"""
import json
import os
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
SCREENER = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(APP, "smc_screener.py")
shots = sys.argv[2] if len(sys.argv) > 2 else tempfile.mkdtemp()
os.makedirs(shots, exist_ok=True)

import fake_binance as FB  # noqa: E402
import backtest_data as BD  # noqa: E402

PORT_B = 8786
FB.serve(PORT_B)
BASE = f"http://127.0.0.1:{PORT_B}"
ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


def wait_done(dl, sec=120):
    end = time.time() + sec
    while dl.thread.is_alive() and time.time() < end:
        time.sleep(0.1)


# ================================================================ 1. список монет — функціями скринера
items = BD.top_symbols(SCREENER, 100, 30.0, BASE)
syms = [s for s, _ in items]
check("топ за обсягом тими ж фільтрами, що скринер: без стейблів, металів і дрібних", syms == ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "NEWUSDT"], syms)

# ================================================================ 2. перше завантаження
data = tempfile.mkdtemp(prefix="smcbt_")
pauses = []
dl = BD.Downloader(data, SCREENER, months=6, top=100, base=BASE, sleep=lambda s: pauses.append(s))
start_ms, end_ms = BD.period(6)
dl.start(); wait_done(dl)
p = dl.progress()
check("завантаження завершено без помилок", p["status"] == "done" and p["done"] == 5, p)
c = dl.cache
lo4, hi4, n4 = c.span("BTCUSDT", "4h")
lo1, hi1, n1 = c.span("BTCUSDT", "1h")
check("4h з розігрівом на 500 свічок до початку періоду", lo4 <= start_ms - 500 * BD.TF_MS["4h"] + BD.TF_MS["4h"], (BD.fmt_ms(lo4), BD.fmt_ms(start_ms)))
check("1h від початку періоду, без дірок", lo1 <= start_ms + BD.TF_MS["1h"] and c.gaps("BTCUSDT", "1h") == 0)
check("лише закриті свічки: остання 1h почалась щонайменше годину тому", hi1 + BD.TF_MS["1h"] <= BD.now_ms())
check("кінець періоду — межа 4h-свічки", end_ms % BD.TF_MS["4h"] == 0)
exp1 = (min(end_ms, BD.now_ms()) - start_ms) // BD.TF_MS["1h"]
check("1h: кількість свічок ≈ період/1 год (багато запитів по 1500)", abs(n1 - exp1) <= 2, (n1, exp1))
lo_new = c.span("NEWUSDT", "1h")[0]
s_new = [x for x in c.summary(start_ms)["symbols"] if x["symbol"] == "NEWUSDT"][0]
check("пізній лістинг: історія з дати появи, позначка «late»", lo_new > start_ms and s_new["late"])
rq1 = dl.client.requests

# ================================================================ 3. повторний запуск — лише докачування
dl2 = BD.Downloader(data, SCREENER, months=6, top=100, base=BASE, sleep=lambda s: None)
FB.S.calls.clear()
dl2.start(); wait_done(dl2)
kl = [q for pth, q in FB.S.calls if pth == "/fapi/v1/klines"]
check("повторний запуск: нічого не качає заново (дата лістингу NEWUSDT запам'ятована)", dl2.progress()["status"] == "done"
      and dl2.client.requests == 0, (dl2.client.requests, rq1, [(q["symbol"], q["interval"], BD.fmt_ms(int(q["startTime"]))) for q in kl]))
check("дублів немає (INSERT OR REPLACE)", c.span("BTCUSDT", "1h")[2] == n1 or c.span("BTCUSDT", "1h")[2] == n1 + 1)

# ================================================================ 4. 429, дірка в історії, зупинка
data2 = tempfile.mkdtemp(prefix="smcbt2_")
FB.S.calls.clear()
FB.S.fail_429_at = {5, 6}
FB.S.gap = ("ETHUSDT", "1h", start_ms + 10 * 86400_000, start_ms + 11 * 86400_000)
pauses.clear()
dl3 = BD.Downloader(data2, SCREENER, months=6, top=100, base=BASE, sleep=lambda s: pauses.append(s))
dl3.start(); wait_done(dl3)
check("429 від Binance: пауза за Retry-After і повтор, без втрати даних",
      dl3.progress()["status"] == "done" and pauses.count(1) >= 2 and dl3.cache.gaps("BTCUSDT", "1h") == 0, (pauses[:5], dl3.progress()["status"]))
check("дірка в історії біржі виявляється (24 свічки 1h)", dl3.cache.gaps("ETHUSDT", "1h") == 24, dl3.cache.gaps("ETHUSDT", "1h"))
FB.S.fail_429_at = set(); FB.S.gap = None
data3 = tempfile.mkdtemp(prefix="smcbt3_")


def slow_sleep(s):
    time.sleep(0.01)


FB.S.delay = 0.05
dl4 = BD.Downloader(data3, SCREENER, months=6, top=100, base=BASE, sleep=slow_sleep)
dl4.start(); time.sleep(0.4); dl4.stop(); wait_done(dl4)
FB.S.delay = 0.0
st4 = dl4.progress()
check("зупинка: статус «зупинено», частина в кеші, запуск записано", st4["status"] == "stopped" and dl4.cache.runs(1)[0]["status"] == "stopped", st4)

# ================================================================ 4b. додати монети в кеш (поза топом)
check("нормалізація: «dot, HBARUSDT 1000cat dot» → з USDT, без повторів",
      BD.norm_symbols("dot, HBARUSDT 1000cat dot") == ["DOTUSDT", "HBARUSDT", "1000CATUSDT"])
FB.S.calls.clear()
dl5 = BD.Downloader(data, SCREENER, months=6, base=BASE, sleep=lambda s: None, add=BD.norm_symbols("tiny, xau, nope"), add_source="з живого логу")
dl5.start(); wait_done(dl5)
kl5 = {q["symbol"] for pth, q in FB.S.calls if pth == "/fapi/v1/klines"}
p5 = dl5.progress()
check("додано TINY (замалий обсяг, але торгується); XAU (метал) і NOPE відхилено з поясненням",
      [x["symbol"] for x in c.extras()] == ["TINYUSDT"] and len(p5["errors"]) == 2 and "XAUUSDT" in p5["errors"][0], p5)
check("качаються лише додані монети", kl5 == {"TINYUSDT"}, kl5)
s5 = {x["symbol"]: x for x in c.summary(start_ms)["symbols"]}
check("у списку бектесту: топ + додана (позначка «додано з живого логу»)", s5["TINYUSDT"]["in_list"] and s5["TINYUSDT"]["extra"]
      and not s5["TINYUSDT"]["in_top"] and s5["TINYUSDT"]["source"] == "з живого логу" and s5["BTCUSDT"]["in_list"])
check("цикл replay бачить додану монету", "TINYUSDT" in [x["symbol"] for x in c.symbols()])
FB.S.calls.clear()
dl6 = BD.Downloader(data, SCREENER, months=6, top=100, base=BASE, sleep=lambda s: None)
dl6.start(); wait_done(dl6)
check("звичайне «докачати» оновлює топ і не губить додану монету", "TINYUSDT" in [x["symbol"] for x in dl6.cache.symbols()]
      and dl6.progress()["total"] == 6, dl6.progress())
check("прибрати додану монету", c.remove_extra("TINYUSDT") == 1 and "TINYUSDT" not in [x["symbol"] for x in c.symbols()])

# ================================================================ 5. вкладка «Бектест» у додатку
from playwright.sync_api import sync_playwright, expect  # noqa: E402

PORT = 8785
URL = f"http://127.0.0.1:{PORT}"
appdata = tempfile.mkdtemp(prefix="smcbt_app_")
res_dir = tempfile.mkdtemp()
with open(os.path.join(res_dir, "signals_log.csv"), "w", encoding="utf-8-sig") as f:
    f.write("time,kind,symbol,dir,status,score,price,zone_low,zone_high\n"
            "2026-11-05 10:00,у списку,TINYUSDT,Long,підхід до зони,1,1,1,1\n"
            "2026-11-05 10:00,у списку,BTCUSDT,Long,підхід до зони,1,1,1,1\n"
            "2026-11-05 10:00,відсіяно: проти структури 4h,XAUUSDT,Long,підхід до зони,1,1,1,1\n")
with open(os.path.join(appdata, "settings.json"), "w") as f:
    json.dump({"screener_path": SCREENER, "results_dir": res_dir}, f)
app = subprocess.Popen([sys.executable, os.path.join(APP, "app.py"), "--port", str(PORT), "--no-browser"],
                       env=dict(os.environ, SMC_APP_DATA=appdata, SMC_BT_FAPI=BASE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(60):
    try:
        urllib.request.urlopen(URL + "/api/status", timeout=1)
        break
    except Exception:
        time.sleep(0.1)
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto(URL)
        pg.click(".tab[data-tab=backtest]")
        expect(pg.locator("#btSum")).to_contain_text("Кеш порожній")
        check("порожній кеш: підказка, період 12 міс. за замовчуванням", "12 міс." in pg.locator("#btPeriod").inner_text())
        pg.select_option("#btMonths", "6")
        pg.fill("#btTop", "5")
        pg.click("#btStart")
        expect(pg.locator("#btProgTxt")).to_contain_text("готово", timeout=60000)
        expect(pg.locator("#btSum")).to_contain_text("Монет у кеші")
        check("завантаження з вкладки: 5 монет, свічки, розмір кешу", "5" in pg.locator("#btSum .stat").first.inner_text()
              and pg.locator("#btBar").get_attribute("class") == "ok")
        pg.click("#btTblBox summary")
        expect(pg.locator("#btBody tr")).to_have_count(5)
        check("таблиця монет: діапазони 4h і 1h з кількістю свічок", "BTCUSDT" in pg.locator("#btBody").inner_text())
        pg.click("#btRunsBox summary")
        check("запуск записано в історію завантажень", "топ-5" in pg.locator("#btRuns").inner_text() and "готово" in pg.locator("#btRuns").inner_text())
        check("підказка: TINY є в живому лозі, але не в кеші (відсіяні й ті, що в кеші, — ні)",
              pg.locator("#btLogMiss [data-addsym]").count() == 1 and "TINY" in pg.locator("#btLogMiss").inner_text())
        pg.click("#btLogMiss [data-addsym]")
        check("клік по монеті вписує її в поле", pg.input_value("#btAddSyms") == "TINY")
        pg.fill("#btAddSyms", "TINY, nope")
        pg.click("#btAddBtn")
        expect(pg.locator("#btLogMiss [data-addsym]")).to_have_count(0, timeout=60000)
        expect(pg.locator("#btProgTxt")).to_contain_text("готово", timeout=60000)
        check("додано з поля: TINY у таблиці з позначкою, NOPE — помилка з поясненням",
              "NOPEUSDT" in pg.locator("#btMsg").inner_text() and pg.locator("#btBody tr:has-text('TINYUSDT') .tag").count() >= 1)
        check("запуск «додано монет: 2» в історії завантажень", "додано монет: 2" in pg.locator("#btRuns").inner_text())
        r = pg.request.post(URL + "/api/backtest/data/add", headers={"X-SMC-App": "1"}, data=json.dumps({"symbols": " ,, "}))
        check("порожній список монет відхиляється", r.status == 400)
        pg.on("dialog", lambda dlg: dlg.accept())
        pg.click("#btBody [data-rmsym='TINYUSDT']")
        expect(pg.locator("#btLogMiss [data-addsym]")).to_have_count(1)
        check("«прибрати» повертає монету в підказки", True)
        pg.click("#btAddLog")
        expect(pg.locator("#btLogMiss [data-addsym]")).to_have_count(0, timeout=60000)
        check("«Додати всі» з живого логу", True)
        expect(pg.locator("#btProgTxt")).to_contain_text("готово", timeout=60000)
        pg.screenshot(path=os.path.join(shots, "bt1_data.png"), full_page=True)
        r = pg.request.post(URL + "/api/backtest/data/start", headers={"X-SMC-App": "1"}, data=json.dumps({"months": 7, "top": 5}))
        check("некоректний період відхиляється", r.status == 400)
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.wait_for_timeout(300)
        check("вузьке вікно без горизонтальної прокрутки", pg.evaluate("document.documentElement.scrollWidth") <= 392)
        check("помилок у консолі немає", not errors, errors[:3])
        b.close()
finally:
    app.terminate()

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"), "| скріншоти:", shots)
sys.exit(0 if ok else 1)
