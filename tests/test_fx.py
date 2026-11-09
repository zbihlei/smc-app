#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.6: форекс-скринер — дані з MT5 (робот SMC_Bridge → файли), та сама стратегія, перемикач «Крипто | Форекс».
Без MT5: фейкові файли робота (tests/fake_mt5.py). Запуск: python3 tests/test_fx.py [папка_скріншотів]"""
import calendar
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
shots = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
os.makedirs(shots, exist_ok=True)

import fake_mt5  # noqa: E402
import fx_source as FX  # noqa: E402

ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


PAIRS = FX.parse_pairs(FX.DEFAULT_PAIRS)[0]
# ================================================================ 1. джерело даних
check("за замовчуванням — 28 валютних пар з 8 валют", len(PAIRS) == 28 and len({p[:3] for p in PAIRS} | {p[3:] for p in PAIRS}) == 8)
pp, bad = FX.parse_pairs("eurusd, GBPJPY;XAUUSD US30 EURUSD")
check("список пар: лише валютні (золото, індекси відхиляються), без повторів", pp == ["EURUSD", "GBPJPY"] and bad == ["XAUUSD", "US30"], (pp, bad))

home = tempfile.mkdtemp()
auto = os.path.join(home, "Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files/SMC")
fake_mt5.write(auto, PAIRS, missing=("NZDCAD",), demo=("EURUSD",))
old_home = os.environ.get("HOME")
os.environ["HOME"] = home
try:
    check("папку робота знайдено автоматично (MT5 для macOS, спільна папка Common/Files/SMC)", FX.find_dir() == auto, FX.find_dir())
finally:
    os.environ["HOME"] = old_home
src = FX.Source(auto)
st = src.status()
check("час сервера брокера: UTC+2 визначено зі status.json", st["offset_s"] == 7200 and st["fresh"])
k = src.klines("GBPUSD", "4h", 50)
raw = open(os.path.join(auto, "GBPUSD_H4.csv")).read().splitlines()[-1].split(",")
check("свічки: час переведено в UTC (мінус 2 год), 50 штук, остання — поточна (незакрита)",
      len(k) == 50 and k[-1][0] == (int(raw[0]) - 7200) * 1000 and not k[-1][6] and all(x[6] for x in k[:-1]) and k[1][0] - k[0][0] == 14_400_000)
check("1h і 15m теж є", src.klines("GBPUSD", "1h", 10)[1][0] - src.klines("GBPUSD", "1h", 10)[0][0] == 3_600_000
      and len(src.klines("GBPUSD", "15m", 300)) == 300)
check("ціни (bid) зі status.json; пари, яких немає в брокера, — пропускаються",
      abs(src.prices()["GBPUSD"] - src.klines("GBPUSD", "15m", 1)[-1][4]) < 1e-4 and "NZDCAD" not in src.available() and len(src.available()) == 27)
try:
    src.klines("NZDCAD", "4h")
    check("немає файла свічок → зрозуміла помилка", False)
except FX.FxError as e:
    check("немає файла свічок → зрозуміла помилка", "NZDCAD" in str(e))
stale = fake_mt5.write(tempfile.mkdtemp(), ["EURUSD"], status_age=600)
try:
    FX.Source(stale).status(check_fresh=True)
    check("MT5 закритий (status.json старий) → помилка «термінал закритий»", False)
except FX.FxError as e:
    check("MT5 закритий (status.json старий) → помилка «термінал закритий»", "термінал закритий" in str(e))
ts = lambda y, mo, d, h: calendar.timegm((y, mo, d, h, 0, 0))
check("вихідні: сб і нд до 21:00 UTC закрито, пт після 21:00 закрито, будні відкрито",
      not FX.market_open(ts(2026, 11, 7, 12)) and not FX.market_open(ts(2026, 11, 8, 20)) and FX.market_open(ts(2026, 11, 8, 22))
      and not FX.market_open(ts(2026, 11, 6, 22)) and FX.market_open(ts(2026, 11, 11, 9)))
d = FX.describe(auto)
check("опис для «Налаштувань»: сервер, свіжість, якої пари немає, фактичні назви з суфіксом",
      d["ok"] and d["server"] == "RoboForex-ECN" and d["missing"] == ["NZDCAD"] and d["names"]["EURUSD"] == "EURUSD.r")
check("немає папки → пояснення, що запустити", "SMC_Bridge" in FX.describe(os.path.join(home, "nope"))["error"])

# ================================================================ 2. скринер на даних MT5 (та сама стратегія)
import fx_runner  # noqa: E402
import screener_runner as SR  # noqa: E402

out = tempfile.mkdtemp()
r = subprocess.run([sys.executable, os.path.join(APP, "fx_runner.py"), os.path.join(APP, "smc_screener.py"),
                    "--approach", "10", "--min-rr", "1", "--out", out], env=dict(os.environ, SMC_FX_DIR=auto),
                   capture_output=True, text=True, timeout=180)
data = json.load(open(os.path.join(out, "latest_app.json")))
check("скан форексу: результат у latest_app.json (market=forex), знайдено сетапи", data["market"] == "forex" and data["count"] > 0,
      r.stdout[-500:] + r.stderr[-500:])
check("у скан не потрапила пара, якої немає в брокера; у лозі — про це", all(c["symbol"] != "NZDCAD" for c in data["candidates"])
      and "Немає в брокера" in r.stdout and "NZDCAD" in r.stdout)
check("графік і рівні в масштабі валют (ціни ~1.x / ~100+ для JPY)", all((c["price"] < 5) != c["symbol"].endswith("JPY") for c in data["candidates"]))
check("список для TradingView — FX:EURUSD…", open(os.path.join(out, "tv_watchlist.txt")).read().count("FX:") == data["count"])
check("фільтр обсягу в $ для форексу не застосовується (--min-volume ігнорується)", "Відсіяно як не-крипто" not in r.stdout)

mod = SR.load_screener(os.path.join(APP, "smc_screener.py"))
sent = []
mod.notify = lambda title, text, kind="new", speak="", ticker="": sent.append((title, speak, ticker))
fx_runner.install_fx(mod, FX.Source(stale))
mod.notify("SMC-скринер", "НОВА ПАРА: EURUSD Long", "new", "New pair EU long", mod.tv_ticker("EURUSD"))
check("алерт: «SMC-скринер ФОРЕКС», голос називає пару повністю, TradingView FX:EURUSD",
      sent == [("SMC-скринер ФОРЕКС", "New pair EURUSD long", "FX:EURUSD")], sent)
try:
    mod.fetch_top_symbols(100)
    check("MT5 закритий → скан не вдається (повтор за 2 хв), а не порожній список", False)
except FX.FxError:
    check("MT5 закритий → скан не вдається (повтор за 2 хв), а не порожній список", True)

# ================================================================ 3. у додатку
from playwright.sync_api import sync_playwright, expect  # noqa: E402

PORT = 8784
URL = f"http://127.0.0.1:{PORT}"
data_dir = tempfile.mkdtemp(prefix="smcfx_")
res = tempfile.mkdtemp(prefix="smcfx_res_")
with open(os.path.join(data_dir, "settings.json"), "w") as f:
    json.dump({"screener_path": os.path.join(APP, "smc_screener.py"), "results_dir": res, "caffeinate": False,
               "popup": False, "voice": False, "tv_button": False, "approach": 10, "extra_args": "--min-rr 1"}, f)
app = subprocess.Popen([sys.executable, os.path.join(APP, "app.py"), "--port", str(PORT), "--no-browser"],
                       env=dict(os.environ, SMC_APP_DATA=data_dir, SMC_LTF_INTERVAL="1", HOME=home),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(60):
    try:
        urllib.request.urlopen(URL + "/api/status", timeout=1)
        break
    except Exception:
        time.sleep(0.1)


def post(path, body):
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode(), headers={"X-SMC-App": "1", "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        return dict(json.load(e), _code=e.code)


try:
    check("форекс вимкнено → «Старт» форексу відмовляє з поясненням", post("/api/start?market=fx", {}).get("_code") == 409)
    eb = urllib.request.urlopen(URL + "/download/SMC_Bridge.mq5", timeout=5)
    body = eb.read().decode("utf-8-sig")
    check("робота SMC_Bridge.mq5 можна завантажити з «Налаштувань» (без функцій торгівлі)",
          "attachment" in eb.headers.get("Content-Disposition", "") and "OnTimer" in body and "OrderSend" not in body and "CTrade" not in body)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        pg.add_init_script("window.SMC_NO_WS = true;")
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto(URL)
        pg.wait_for_timeout(800)
        check("форекс вимкнено → перемикача «Крипто | Форекс» немає", pg.locator("#mkSwitch").is_hidden())
        pg.click(".tab[data-tab=settings]")
        expect(pg.locator("#fxState")).to_contain_text("MT5 підключено", timeout=5000)
        check("«Налаштування → Форекс»: MT5 знайдено автоматично, сервер і якої пари немає",
              "RoboForex-ECN" in pg.inner_text("#fxState") and "NZDCAD" in pg.inner_text("#fxState"))
        pg.fill("input[name=fx_pairs]", "EURUSD, XAUUSD")
        pg.check("input[name=fx_enabled]")
        pg.click("#setForm button[type=submit]")
        expect(pg.locator("#setMsg")).to_contain_text("XAUUSD", timeout=5000)
        check("золото в списку пар → помилка «не валютні пари»", True)
        pg.fill("input[name=fx_pairs]", FX.DEFAULT_PAIRS)
        pg.click("#setForm button[type=submit]")
        expect(pg.locator("#setMsg")).to_contain_text("Збережено", timeout=10000)
        check("список пар записано для робота (pairs.txt у папці MT5)", open(os.path.join(auto, "pairs.txt")).read().startswith("EURUSD,GBPUSD"))
        pg.click(".tab[data-tab=screener]")
        expect(pg.locator("#mkSwitch")).to_be_visible(timeout=8000)
        check("форекс увімкнено → перемикач «Крипто | Форекс»", True)
        pg.click("#mkSwitch [data-mk=fx]")
        expect(pg.locator("#statusLine")).to_contain_text("Форекс-скринер зупинено", timeout=8000)
        check("«Форекс»: статус MT5 (сервер, свіжість) і власний «Старт»", "RoboForex-ECN" in pg.inner_text("#statusLine")
              and pg.inner_text("#pillText") == "Форекс: зупинено")
        pg.click("#btnRun")
        expect(pg.locator("#pillText")).to_have_text("Форекс: працює", timeout=15000)
        expect(pg.locator("#tbody tr").first).to_be_visible(timeout=60000)
        n = pg.locator("#tbody tr").count()
        check("форекс-скринер запущено, сетапи у таблиці", n > 0, n)
        href = pg.locator("#tbody tr a.sym").first.get_attribute("href")
        check("посилання на TradingView — FX:пара", "FX%3A" in href, href)
        pg.wait_for_selector("#ch150 canvas", timeout=20000)
        check("3 графіки 4h/1h/15m зі свічками MT5", all(pg.locator(f"{c} canvas").count() > 0 for c in ("#ch0", "#ch1h0", "#ch150")))
        expect(pg.locator("#lt0")).not_to_contain_text("рахую", timeout=30000)
        check("уточнення 15m для форексу рахується (свічки 15m з MT5)", "15m:" in pg.inner_text("#lt0"), pg.inner_text("#lt0"))
        st = json.load(urllib.request.urlopen(URL + "/api/status?market=fx", timeout=5))
        check("крипто-скринер не запускався (форекс — окремий процес)", st["markets"] == {"crypto": False, "fx": True}, st.get("markets"))
        check("на перемикачі видно, що працює форекс (зелена крапка)", "run" in pg.get_attribute("#mkDotF", "class")
              and "run" not in pg.get_attribute("#mkDotC", "class"))
        pg.click("#logBox summary")
        expect(pg.locator("#log")).to_contain_text("форекс-скринер", timeout=8000)
        check("окремий лог форексу", "дані — MT5" in pg.inner_text("#log"))
        pg.screenshot(path=os.path.join(shots, "fx.png"), full_page=True)
        pg.click("#mkSwitch [data-mk='']")
        expect(pg.locator("#pillText")).to_have_text("Зупинено", timeout=8000)
        check("назад на «Крипто»: свій статус (зупинено), без форекс-пар", pg.locator("#tbody tr").count() == 0
              or "USD" not in pg.inner_text("#tbody"))
        pg.click("#mkSwitch [data-mk=fx]")
        expect(pg.locator("#pillText")).to_have_text("Форекс: працює", timeout=8000)
        pg.once("dialog", lambda dlg: dlg.accept())
        pg.click("#btnRun")
        expect(pg.locator("#pillText")).to_have_text("Форекс: зупинено", timeout=20000)
        check("«Стоп» зупиняє форекс-скринер", True)
        r = post("/api/settings", {"settings": {"fx_enabled": False}})
        check("вимкнути форекс у «Налаштуваннях» → збережено", r.get("ok"))
        pg.reload()
        expect(pg.locator("#mkSwitch")).to_be_hidden(timeout=8000)
        check("форекс вимкнено → вкладка повертається на «Крипто»", pg.inner_text("#pillText") in ("Зупинено", "Працює"))
        errors = [e for e in errors if "status of 409" not in e and "status of 400" not in e]  # 400 — очікувано: XAUUSD у списку
        check("помилок у консолі немає", not errors, errors[:3])
        b.close()
finally:
    post("/api/stop?market=fx", {})
    app.terminate()

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"), "| скріншоти:", shots)
sys.exit(0 if ok else 1)
