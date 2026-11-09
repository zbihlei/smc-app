#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.5: уточнення входу на 15m (злам структури в зоні 4h) + лайв-графіки 4h/1h/15m у вкладці «Скринер».
Фейковий Binance, без мережі. Запуск: python3 tests/test_ltf.py [папка_скріншотів]"""
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

import fake_binance as FB  # noqa: E402
import ltf  # noqa: E402

PORT_B = 8781
FB.serve(PORT_B)
BASE = f"http://127.0.0.1:{PORT_B}"
ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


# ================================================================ 1. логіка уточнення (лонг; шорт — дзеркально)
def series(closes, t0=1_700_000_000_000):
    out, o = [], closes[0]
    for i, c in enumerate(closes):
        out.append([t0 + i * 900_000, o, max(o, c) + 0.5, min(o, c) - 0.5, c, 1.0, True])
        o = c
    return out


DOWN = [110, 108, 106, 107, 109, 106, 104, 102, 101]          # ще над зоною
INTO = DOWN + [99, 97, 96, 98, 99, 100]                        # зайшла в зону 95–100, мінімум 95.5, відскок без зламу
CHOCH = INTO + [103, 105, 108, 110]                            # закриття 110 > свінгу 109.5 → злам
FILL = CHOCH + [107, 104, 101, 98, 97.4, 100, 105]             # відкат до лімітки 97.0 → вхід
MISS = CHOCH + [113, 116, 119, 121]                            # одразу до цілі 120 без відкату
STOPPED = FILL + [99, 96, 95.2]                                # після входу — нижче стопа 15m
SIG = {"symbol": "TESTUSDT", "dir": "Long", "zone_low": 95.0, "zone_high": 100.0, "stop": 94.0, "target": 120.0}


def REF(c, sig, tol=25, **k):
    """Логіка структури — без правила мінімального стопа (його перевіряємо окремо нижче)."""
    return ltf.refine(c, sig, tol, **dict({"min_stop_atr": 0}, **k))


r = REF(series(DOWN), SIG, 25)
check("ціна ще над зоною → «чекаю зону»", r["status"] == "wait_zone", r)
r = REF(series(INTO), SIG, 25)
check("у зоні без зламу → «чекаю злам 15m», рівень зламу = останній свінг-максимум 109.5",
      r["status"] == "wait_choch" and r["trigger"] == 109.5 and r["ext"] == 95.5, r)
r = REF(series(CHOCH), SIG, 25)
check("злам 15m: лімітка від 15m-ордерблока (середина 96.5 + 25% висоти = 97.0)",
      r["status"] == "choch" and abs(r["entry"] - 97.0) < 1e-9 and (r["ob_low"], r["ob_high"]) == (95.5, 97.5), r)
check("стоп 15m — за екстремумом 95.5 з буфером 0.05%, R:R до цілі 4h", abs(r["stop"] - 95.5 * 0.9995) < 1e-9
      and abs(r["rr"] - round(23 / (97 - 95.5 * 0.9995), 2)) < 1e-9 and r["target"] == 120.0, r)
check("стоп 15m тісніший за стоп 4h → R:R вищий, ніж від середини зони 4h", r["rr"] > (120 - 97.5) / (97.5 - 94))
r0 = REF(series(CHOCH), SIG, 0)
check("без допуску — лімітка рівно в середині 15m-ордерблока", abs(r0["entry"] - 96.5) < 1e-9, r0)
# --- стоп і ціль «за графіком»: стоп не ближче за 0.6× середньої 15m-свічки, ціль 4h не підганяється
avg = sum(x[2] - x[3] for x in series(CHOCH)) / len(series(CHOCH))
r = ltf.refine(series(CHOCH), SIG, 25)
check("стоп 15m ближче за 0.6× середньої свічки → відсунуто до 0.6× (за замовчуванням, як на 4h)",
      r["status"] == "choch" and r["stop_widened"] and abs(r["stop"] - (97.0 - 0.6 * avg)) < 1e-9 and "відсунуто" in r["why"], r)
check("R:R рахується від лімітки до тієї самої цілі 4h (ціль не змінено)", r["target"] == 120.0
      and abs(r["rr"] - round(23 / (0.6 * avg), 2)) < 1e-9 and r["rr"] < REF(series(CHOCH), SIG)["rr"], r)
r = ltf.refine(series(CHOCH), SIG, 25, min_stop_atr=0.3)
check("стоп за екстремумом уже далі за мінімум → лишається за графіком", not r["stop_widened"] and abs(r["stop"] - 95.5 * 0.9995) < 1e-9, r)
r = ltf.refine(series(CHOCH), dict(SIG, target=101.0), 25)
check("ціль за графіком близько → R:R < 3 → «пропуск», без лімітки в статусі й без підгонки цілі",
      r["status"] == "rr_low" and r["target"] == 101.0 and "ціль не підганяю" in r["why"], r)
r = ltf.refine(series(CHOCH), SIG, 25, min_stop_atr=2.0, min_rr=5)
check("мін. R:R із налаштувань (напр. 5): стоп 2× свічки → R:R 4.1 → пропуск", r["status"] == "rr_low" and 4 < r["rr"] < 5, r)

r = REF(series(FILL), SIG, 25)
check("відкат до лімітки → «вхід 15m відбувся»", r["status"] == "filled" and r.get("fill_t"), r)
r = REF(series(MISS), SIG, 25)
check("ціль без відкату → «ціль без входу 15m»", r["status"] == "missed", r)
r = REF(series(STOPPED), SIG, 25)
check("після входу ціна нижче стопа 15m → «сценарій зламано»", r["status"] == "broken", r)
r = REF(series(INTO[:-3] + [93, 96, 98]), SIG, 25)
check("ціна пробила стоп 4h → «сценарій зламано» (без входу)", r["status"] == "broken" and "4h" in r["why"], r)


# --- лише свічки після підтвердження сетапу (BOS на 4h): імпульс до BOS не є ні дотиком, ні пробоєм стопа
PRE = [100, 97, 93.2, 96, 99, 104, 108, 110]            # до BOS: ціна ходила нижче стопа 4h (94)
cc = series(PRE + CHOCH)
since = cc[len(PRE)][0]
check("без межі в часі: свічки до сетапу дають хибне «сценарій зламано»", REF(cc, SIG)["status"] == "broken")
check("з межею (закриття BOS на 4h): ті самі свічки ігноруються → злам 15m, лімітка", REF(cc, SIG, since_ms=since)["status"] == "choch",
      REF(cc, SIG, since_ms=since))
liv = [cc[-1][0] + 900_000, 99, 99.5, 99.2, 99.3, 1.0, False]          # поточна 15m-свічка: ціна вже в зоні 95–100
r = REF(series(DOWN), SIG, live=liv)
check("ціна в зоні, а 15m-свічка ще не закрилась → одразу «у зоні — чекаю злам», без «ще не в зоні»",
      r["status"] == "wait_choch" and "не закрилась" in r["why"] and r.get("trigger") is not None, r)
check("незакрита свічка поза зоною → як і було, «ціна ще не в зоні»",
      REF(series(DOWN), SIG, live=[liv[0], 105, 106, 104, 105, 1, False])["status"] == "wait_zone")
row4 = {"chart": [[1_000_000 + i * 14_400_000, 1, 1, 1, 1] for i in range(10)], "bos_bars_ago": 3, "tf_ms": 14_400_000}
check("момент BOS із даних сигналу: остання закрита 4h − bos_bars_ago, + 4h (закриття свічки BOS)",
      ltf.sig_since(row4) == 1_000_000 + 9 * 14_400_000 - 3 * 14_400_000 + 14_400_000 and ltf.sig_since({}) is None)


def mirror(c):
    return [[x[0], 200 - x[1], 200 - x[3], 200 - x[2], 200 - x[4], x[5], x[6]] for x in c]


SIGS = {"symbol": "TESTUSDT", "dir": "Short", "zone_low": 100.0, "zone_high": 105.0, "stop": 106.0, "target": 80.0}
r = REF(mirror(series(CHOCH)), SIGS, 25)
check("шорт (дзеркально): злам 15m, лімітка 103.0, стоп за максимумом 104.5", r["status"] == "choch" and abs(r["entry"] - 103.0) < 1e-9
      and abs(r["stop"] - 104.5 * 1.0005) < 1e-9, r)
check("шорт: «чекаю злам» і «вхід» дзеркально", REF(mirror(series(INTO)), SIGS, 25)["status"] == "wait_choch"
      and REF(mirror(series(FILL)), SIGS, 25)["status"] == "filled")

up = series([100 + i + (3 if i % 6 == 3 else 0) for i in range(40)])
check("структура 1h: зростання → BOS угору; падіння → униз", ltf.structure(up, 2) == 1 and ltf.structure(mirror(up), 2) == -1)

# ================================================================ 2. наглядач: алерт рівно один раз на кожен злам
d = tempfile.mkdtemp()
alerts = []
orig = ltf.fetch_klines
ltf.fetch_klines = lambda sym, tf, limit=200, base=None, ttl=20: series(CHOCH) if tf == "15m" else up
try:
    w = ltf.LtfWatcher(d, lambda: [dict(SIG)], lambda t, x: alerts.append((t, x)), get_tol=lambda: 25)
    st = w.run_once()
    w.run_once()
    check("злам 15m → алерт «УТОЧНЕННЯ 15m» з ліміткою, стопом, ціллю, R:R і структурою 1h",
          len(alerts) == 1 and alerts[0][0] == "УТОЧНЕННЯ 15m" and "лімітка 97" in alerts[0][1] and "структура 1h: вгору" in alerts[0][1], alerts)
    check("стан для інтерфейсу: статус, пояснення, контекст 1h", st[ltf.sig_key(SIG)]["status_txt"] == "злам 15m — лімітка"
          and st[ltf.sig_key(SIG)]["ctx_1h"] == 1)
    ltf.LtfWatcher(d, lambda: [dict(SIG)], lambda t, x: alerts.append((t, x)), get_tol=lambda: 25).run_once()
    check("після перезапуску додатка той самий злам не сповіщається вдруге", len(alerts) == 1, alerts)
    w2 = ltf.LtfWatcher(tempfile.mkdtemp(), lambda: [dict(SIG, target=101.0)], lambda t, x: alerts.append((t, x)), get_tol=lambda: 25)
    st2 = w2.run_once()
    check("R:R замалий → алерту немає, статус «пропуск»", len(alerts) == 1 and list(st2.values())[0]["status"] == "rr_low", alerts)
    w3 = ltf.LtfWatcher(tempfile.mkdtemp(), lambda: [dict(SIG)], lambda t, x: None, get_tol=lambda: 25,
                        get_opts=lambda: {"min_stop_atr": 0, "min_rr": 3})
    check("правила стопа/R:R беруться з налаштувань (get_opts)", not list(w3.run_once().values())[0]["stop_widened"])
    ltf.fetch_klines = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 451"))
    st = w.run_once()
    check("Binance недоступний → статус «немає даних», без падіння", st[ltf.sig_key(SIG)]["status"] == "error")
finally:
    ltf.fetch_klines = orig

bars = ltf.fetch_klines("BTCUSDT", "15m", 50, BASE, ttl=0)
check("свічки з біржі: 50 штук по 15m, остання — поточна (незакрита)", len(bars) == 50 and not bars[-1][6] and all(b[6] for b in bars[:-1])
      and bars[1][0] - bars[0][0] == 900_000, bars[-2:])
try:
    ltf.fetch_klines("BTCUSDT", "5m", 50, BASE)
    check("невідомий таймфрейм відхиляється", False)
except ValueError:
    check("невідомий таймфрейм відхиляється", True)

# ================================================================ 3. у додатку: 3 графіки в ряд + рядок уточнення
from playwright.sync_api import sync_playwright, expect  # noqa: E402

PORT = 8782
URL = f"http://127.0.0.1:{PORT}"
data = tempfile.mkdtemp(prefix="smcltf_")
res = tempfile.mkdtemp(prefix="smcltf_res_")
with open(os.path.join(data, "settings.json"), "w") as f:
    json.dump({"screener_path": os.path.join(HERE, "fake_screener.py"), "results_dir": res}, f)
now = int(time.time() * 1000)
t4 = now - now % 14_400_000 - 20 * 14_400_000
c4 = FB.candle("BTCUSDT", "4h", t4)
zl, zh = float(c4[3]), float(c4[2])
row = {"symbol": "BTCUSDT", "dir": "Long", "status": "підхід до зони", "rank": 1, "score": 1, "price": zh * 1.03,
       "zone_low": zl, "zone_high": zh, "entry": (zl + zh) / 2, "stop": zl * 0.99, "target": zh * 1.2, "rr": 4.1, "dist": 2.0,
       "zone_t": t4, "tf_ms": 14_400_000, "zone_type": "OB",
       "flags": ["BTC різко падає: -2.4% за 1 год — ПРОТИ угоди — альти зазвичай ідуть за ним"]}
with open(os.path.join(res, "latest_app.json"), "w", encoding="utf-8") as f:
    json.dump({"generated": "08.11 12:00", "count": 1, "candidates": [row]}, f)
app = subprocess.Popen([sys.executable, os.path.join(APP, "app.py"), "--port", str(PORT), "--no-browser"],
                       env=dict(os.environ, SMC_APP_DATA=data, SMC_BT_FAPI=BASE, SMC_LTF_INTERVAL="1"),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(60):
    try:
        urllib.request.urlopen(URL + "/api/status", timeout=1)
        break
    except Exception:
        time.sleep(0.1)
try:
    k = json.load(urllib.request.urlopen(URL + "/api/klines?symbol=BTCUSDT&tf=1h&limit=30", timeout=5))
    check("/api/klines: свічки 1h для графіка", k["ok"] and len(k["bars"]) == 30 and k["bars"][1][0] - k["bars"][0][0] == 3_600_000)
    try:
        urllib.request.urlopen(URL + "/api/klines?symbol=../etc&tf=1h", timeout=5)
        check("/api/klines: некоректна монета → 400", False)
    except urllib.error.HTTPError as e:
        check("/api/klines: некоректна монета → 400", e.code == 400)
    for _ in range(50):
        st = json.load(urllib.request.urlopen(URL + "/api/ltf", timeout=5))
        if st["state"]:
            break
        time.sleep(0.2)
    check("/api/ltf: додаток сам рахує уточнення для сигналів «у списку»", list(st["state"]) == [ltf.sig_key(row)], st)

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        pg.add_init_script("window.SMC_NO_WS = true;")  # у тесті немає мережі — перевіряємо запасне оновлення опитуванням
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto(URL)
        for cid in ("#ch0", "#ch1h0", "#ch150"):
            pg.wait_for_selector(cid + " canvas", timeout=15000)
        boxes = [pg.locator(c).bounding_box() for c in ("#ch0", "#ch1h0", "#ch150")]
        check("три графіки в ряд: 4h, 1h, 15m", abs(boxes[0]["y"] - boxes[1]["y"]) < 2 and abs(boxes[1]["y"] - boxes[2]["y"]) < 2
              and boxes[0]["x"] < boxes[1]["x"] < boxes[2]["x"] and min(x["width"] for x in boxes) > 350, boxes)
        check("підписи таймфреймів", "4h" in pg.locator("#c0 .tf3").inner_text() and "15m" in pg.locator("#c0 .tf3").inner_text())
        check("на кожному графіку розмічено зону, середину, ризик і ціль",
              all(pg.locator(c + " > div[style*='position: absolute']").count() >= 4 for c in ("#ch0", "#ch1h0", "#ch150")))
        expect(pg.locator("#lt0")).to_contain_text("15m: ціна ще не в зоні 4h", timeout=40000)
        check("рядок уточнення під графіками: статус 15m", True)
        expect(pg.locator("#lb1h0")).to_contain_text("структура", timeout=5000)
        check("над 1h — контекст структури 1h", True)
        expect(pg.locator("#liveBadge")).to_contain_text("15 с", timeout=5000)
        check("без WebSocket — оновлення опитуванням кожні 15 с (видно в заголовку)", True)
        pg.screenshot(path=os.path.join(shots, "ltf_charts.png"), full_page=True)
        check("блок: попередження про різкий рух BTC (₿ … проти угоди)", "проти угоди" in pg.inner_text("#tiles .tile .t-btc"))
        pg.click("#btnRefresh")
        pg.wait_for_timeout(800)
        check("«⟳ Оновити»: список і графіки на місці, без помилок", pg.locator("#tiles .tile").count() == 1 and not errors, errors[:2])
        pg.click("#c0 .cfold")
        check("картку графіків можна згорнути (лишається заголовок)", "folded" in pg.get_attribute("#c0", "class") and pg.locator("#ch0").is_hidden()
              and pg.locator("#c0 .hd").is_visible())
        pg.reload()
        pg.wait_for_selector("#c0", timeout=10000)
        check("згорнута картка лишається згорнутою після оновлення сторінки", "folded" in pg.get_attribute("#c0", "class"))
        pg.click("[data-foldall='0']")
        pg.wait_for_selector("#ch0 canvas", timeout=15000)
        check("«Розгорнути всі» — графіки знову видно з шириною", pg.locator("#ch0").bounding_box()["width"] > 300)
        pg.click(".tab[data-tab=settings]")
        expect(pg.locator("input[name=ltf_enabled]")).to_be_checked(timeout=5000)
        pg.uncheck("input[name=ltf_enabled]")
        pg.click("#setForm button[type=submit]")
        pg.wait_for_timeout(500)
        pg.click(".tab[data-tab=screener]")
        pg.wait_for_timeout(500)
        check("вимкнено в «Налаштуваннях» → як раніше, один графік 4h", pg.locator("#c0 .tf3").count() == 0 and pg.locator("#ch0").count() == 1)
        st = json.load(urllib.request.urlopen(URL + "/api/ltf", timeout=5))
        check("вимкнено → уточнення 15m не рахується", st["enabled"] is False)
        pg.click(".tab[data-tab=settings]")
        pg.check("input[name=ltf_enabled]")
        pg.click("#setForm button[type=submit]")
        pg.wait_for_timeout(500)
        pg.click(".tab[data-tab=screener]")
        pg.wait_for_selector("#ch150 canvas", timeout=15000)
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.wait_for_timeout(500)
        b1, b2 = pg.locator("#ch0").bounding_box(), pg.locator("#ch150").bounding_box()
        check("вузьке вікно: графіки один під одним, без горизонтальної прокрутки",
              b2["y"] > b1["y"] + 100 and pg.evaluate("document.documentElement.scrollWidth") <= 392)
        check("помилок у консолі немає", not errors, errors[:3])
        b.close()
finally:
    app.terminate()

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"), "| скріншоти:", shots)
sys.exit(0 if ok else 1)
