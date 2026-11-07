#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Бектест, етап (c): симуляція угод на 1h + розділ «3. Угоди й метрики».
Запуск: python3 tests/test_backtest_trades.py ШЛЯХ_ДО_smc_screener.py [папка_скріншотів]"""
import json
import os
import random
import sqlite3
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

import backtest_data as BD  # noqa: E402
import backtest_signals as BS  # noqa: E402
import backtest_trades as BT  # noqa: E402

ok = True
H1, H4 = BT.H1, BT.H4
P = BT.clean_params({})
T0 = 1_780_000_000_000 - 1_780_000_000_000 % H4      # сигнал на межі 4h


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


def path(points, start=T0):
    """Свічки 1h з (high, low, close) — по одній на годину від start."""
    return [(start + i * H1, (h + l) / 2, h, l, c, 1.0) for i, (h, l, c) in enumerate(points)]


def sim(points, lv=None, params=None, d="Long"):
    lv = lv or {"entry": 100.0, "stop": 98.0, "target": 106.0, "zone_low": 99.0, "zone_high": 101.0, "rr": 3.0, "price": 103.0}
    c1 = path(points)
    e = {"symbol": "X", "dir": d, "first_list_ms": T0}
    return BT._sim_event(e, lv, c1, [x[0] for x in c1], BT.clean_params(params or {}))


fee_win = (0.02 + 0.02) / 2.0      # risk 2% від входу 100
fee_loss = (0.02 + 0.05) / 2.0

# ================================================================ 1. одна угода, Long: вхід 100, стоп 98, ціль 106 (3R)
r = sim([(103, 102, 102.5), (102, 99.8, 100.5), (103, 100.2, 102), (106.5, 103, 106)])
check("вхід лімітом від середини, ціль → +3R мінус комісії maker+maker", r["outcome"] == "trade" and r["hours_to_fill"] == 1
      and abs(r["r_a"] - (3 - fee_win)) < 1e-6 and r["exit_a"] == "target", r)
r = sim([(103, 102, 102.5), (101, 97.5, 98)])
check("вхід і стоп в одній свічці → −1R мінус maker+taker", r["outcome"] == "trade" and abs(r["r_a"] - (-1 - fee_loss)) < 1e-6, r)
r = sim([(103, 99.9, 101), (106.5, 97.9, 100)])
check("стоп і ціль в одній свічці після входу → стоп (песимістично)", r["exit_a"] == "stop", r)
r = sim([(103, 99.9, 106.5)] + [(101, 99.5, 100)] * 3)
check("у свічці входу ціль не зараховується", r["state"] == "open", r)
r = sim([(103, 102, 102.5)] * 3 + [(103, 98.5, 98.8)])
check("закриття 4h за зоною неможливе без проходу середини: лімітка вже виконана → це угода", r["outcome"] == "trade" and r["hours_to_fill"] == 3, r)
r = sim([(103, 102, 98.8)] + [(103, 102, 102.5)] * 5)
check("закриття 1h за зоною не на межі 4h — не скасування", r["outcome"] == "cancel" and r["reason"] == "no_data", r)
r = sim([(106.5, 102, 105), (105, 99.5, 100)])
check("ціна дійшла до цілі без входу → скасування (опція)", r["reason"] == "target_first", r)
r = sim([(106.5, 102, 105), (105, 99.5, 100)], params={"cancel_on_target": False})
check("…а без опції лімітка лишається й виконується", r["outcome"] == "trade", r)
r = sim([(103, 102, 102.5)] * 30 + [(102, 99.5, 100)], params={"expiry_h": 24})
check("термін лімітки 24 год → скасування", r["reason"] == "expiry", r)
r = sim([(101, 99.5, 100)], lv={"entry": 100.0, "stop": 98.0, "target": 106.0, "zone_low": 99.0, "zone_high": 101.0, "rr": 3.0, "price": 99.5})
check("середина зони вже пройдена на момент сигналу → пропуск", r["reason"] == "mid_passed", r)
r = sim([(97, 96, 96.5), (101, 99.5, 100), (101, 93.9, 94)], d="Short",
        lv={"entry": 100.0, "stop": 102.0, "target": 94.0, "zone_low": 99.0, "zone_high": 101.0, "rr": 3.0, "price": 97.0})
check("Short: дзеркально, ціль знизу → +3R", r["outcome"] == "trade" and r["exit_a"] == "target" and r["r_a"] > 2.9, r)

# ================================================================ 2. ведення «за стратегією»
r = sim([(103, 99.9, 101), (106.2, 101, 106), (106, 99.9, 100.5), (101, 97.9, 98)],
        lv={"entry": 100.0, "stop": 98.0, "target": 110.0, "zone_low": 99.0, "zone_high": 101.0, "rr": 5.0, "price": 103.0})
check("3R → беззбиток: повернення до входу = 0R (комісії покрито), а «до цілі» = −1R",
      r["exit_b"] == "be" and abs(r["r_b"]) < 1e-6 and r["exit_a"] == "stop" and abs(r["mfe"] - 3.1) < 1e-6, r)
r = sim([(103, 99.9, 101), (108.2, 101, 108), (110.5, 106, 110)],
        lv={"entry": 100.0, "stop": 98.0, "target": 110.0, "zone_low": 99.0, "zone_high": 101.0, "rr": 5.0, "price": 103.0})
exp_b = 0.5 * (4 - fee_win) + 0.5 * (5 - fee_win)
check("4R → 50% закрито, решта в ціль: 0,5×4R + 0,5×5R", abs(r["r_b"] - exp_b) < 1e-6 and abs(r["r_a"] - (5 - fee_win)) < 1e-6, r)
r = sim([(103, 99.9, 101), (108.2, 101, 108), (108, 99.9, 100)],
        lv={"entry": 100.0, "stop": 98.0, "target": 110.0, "zone_low": 99.0, "zone_high": 101.0, "rr": 5.0, "price": 103.0})
check("4R → 50%, потім беззбиток: ≈ +2R", abs(r["r_b"] - 0.5 * (4 - fee_win)) < 1e-6, r)

# ================================================================ 3. метрики
st = BT._series_stats([1, -1, -1, -1, 3, -1, 2])
check("сума, середнє, частка, PF, просадка, серія збитків", st["sum"] == 2 and st["wins"] == 3 and st["max_streak"] == 3
      and st["max_dd"] == 3 and st["pf"] == 1.5, st)
try:
    BT.clean_params({"maker": "абв"})
    check("некоректні параметри відхиляються", False)
except ValueError:
    check("некоректні параметри відхиляються", True)

# ================================================================ 4. повний прогін на кеші (етап b → c)
sys.argv = [sys.argv[0], SCREENER]
from test_backtest_signals_helpers import make_cache, SYMS, ARGS  # noqa: E402

dA = tempfile.mkdtemp(prefix="smctr_")
start, end = make_cache(dA, SYMS, months=12)
sid = BS.run(dA, SCREENER, 12, ARGS, workers=2)
res = BT.run(dA, BS.Store(dA), sid, {})
m = res["metrics"]
check(f"прогін: сигналів {m['signals']}, входів {m['trades']}, закрито {m['a'].get('n')}", m["trades"] > 5 and m["a"]["n"] > 3, m)
tr = res["trades"]
check("лише сигнали перших 8 міс. (останні 4 приховано)", all(t["signal_ms"] < res["split_ms"] for t in tr)
      and res["split_ms"] < end - 3 * BT.MONTH_MS)
check("одна угода на монету: позиції по монеті не перекриваються", all(
    not (a["symbol"] == b["symbol"] and a is not b and a["fill_ms"] <= b["fill_ms"] < (a["exit_ms"] or 1e30))
    for a in tr for b in tr if a["state"] == "closed"))
check("R угоди «до цілі» лише −1−комісії або RR−комісії", all(abs(t["r_a"] - (-1 - t["fee_r"])) < 1e-3 or t["r_a"] > 2.5
                                                              for t in tr if t["state"] == "closed"))
check("сума по місяцях = сумі R", abs(sum(x["a"] for x in m["months"]) - m["a"]["sum"]) < 0.05)
res2 = BT.run(dA, BS.Store(dA), sid, {"one_per_symbol": False})
check("без обмеження «одна на монету» входів не менше", res2["metrics"]["trades"] >= m["trades"])
check("запуски збережено", len(BT.TradeStore(dA).runs()) == 2)
sm = BT.summary(res)
check("висновок: вердикт, тон і пункти простими словами", sm["tone"] in ("pos", "neu", "neg", "few") and sm["verdict"]
      and any("депозиту" in x for x in sm["points"]) and any(x.startswith("Що далі") for x in sm["points"]), sm)
fake = {"metrics": {"a": {"n": 12, "wins": 3, "winrate": 25.0, "avg": 0.5, "sum": 6, "pf": 1.6, "max_dd": 3, "max_streak": 4},
                    "b": {"n": 12, "avg": 0.4, "pf": 1.4, "max_dd": 2, "max_streak": 3}, "signals": 30, "cancels": [], "period_days": 70},
        "params": {}, "trades": []}
ss = BT.slices_summary(BT.slices(res["trades"]))
check("висновок по розрізах: вердикт і порада", ss and ss["verdict"] and ss["points"]
      and (any("1–2" in x for x in ss["points"]) or "немає" in ss["verdict"]), ss)
check("без помітних різниць → так і сказано", "немає" in BT.slices_summary([{"name": "Напрям", "missing": 0, "rows": [
    {"group": "Long", "n": 50, "avg_a": 0.1, "small": False}, {"group": "Short", "n": 50, "avg_a": 0.0, "small": False}]}])["verdict"])
check("мало угод → «замало», без поспішних висновків", BT.summary(fake)["tone"] == "few" and "замало" in BT.summary(fake)["verdict"].lower())
fake["metrics"]["a"].update(n=200, wins=40, avg=0.2, sum=40, pf=1.3, max_dd=20)
check("перевага → «дали перевагу» і порада відкрити 4 міс. один раз", BT.summary(fake)["tone"] == "pos"
      and any("один раз" in x for x in BT.summary(fake)["points"]))
fake["metrics"]["a"].update(avg=-0.3, sum=-60, pf=0.7, max_dd=80); fake["metrics"]["b"].update(avg=-0.25, pf=0.75)
s2 = BT.summary(fake)
check("збиток → «збиткові», просадка > 50% депозиту позначена", s2["tone"] == "neg" and any("не витримав" in x for x in s2["points"]))
fake["window"] = "oos"
check("поза вибіркою — свій вердикт", BT.summary(fake)["verdict"].startswith("Поза вибіркою"))

# ================================================================ 4b. фільтри, розрізи, поза вибіркою
lv = {"score": 1, "rr": 4.0, "knife": False, "flags": ["тісний стоп: 0.5% = 0.4× середньої свічки"]}
Pf = BT.clean_params({"min_score": 2})
check("фільтр «мін. бали»", not BT.passes(lv, "Long", Pf) and BT.passes(lv, "Long", BT.clean_params({"min_score": 1})))
check("фільтри «макс. R:R», «без тісного стопу», «лише Short»", not BT.passes(lv, "Long", BT.clean_params({"max_rr": 3.5}))
      and not BT.passes(lv, "Long", BT.clean_params({"no_tight": True})) and not BT.passes(lv, "Long", BT.clean_params({"dirs": "Short"})))
check("текст фільтрів", BT.filters_text(BT.clean_params({"min_score": 1, "no_knife": True})) == "бали ≥ 1, без «ножа»")
sl = BT.slices(res["trades"])
check("розрізи: у кожній ознаці сума угод по групах = закритим угодам",
      all(sum(x["n"] for x in g["rows"]) + g["missing"] == m["a"]["n"] for g in sl), [(g["name"], sum(x["n"] for x in g["rows"])) for g in sl])
check("розрізи: середній R групи «бали» узгоджений із сумою", all(abs(x["avg_a"] * x["n"] - x["sum_a"]) < 0.2 for x in sl[0]["rows"]))
resf = BT.run(dA, BS.Store(dA), sid, {"min_score": 1})
check("прогін із фільтром: відфільтровані враховано, у всіх угод бали ≥ 1", resf["metrics"]["filtered"] > 0
      and all(t["score"] >= 1 for t in resf["trades"]), resf["metrics"]["filtered"])
oos = BT.open_oos(dA, BS.Store(dA), resf["id"])
check("поза вибіркою: ті самі правила, лише сигнали після межі 8 міс.", oos["window"] == "oos" and oos["base_id"] == resf["id"]
      and all(t["signal_ms"] >= oos["split_ms"] and t["score"] >= 1 for t in oos["trades"]) and oos["params"]["min_score"] == 1)
check("відкриття записано в журнал; у списку прогонів розробки його немає",
      len(BT.TradeStore(dA).oos_log()) == 1 and all(x["id"] != oos["id"] for x in BT.TradeStore(dA).runs()))
try:
    BT.open_oos(dA, BS.Store(dA), oos["id"])
    check("відкрити поза вибіркою можна лише для прогону розробки", False)
except ValueError:
    check("відкрити поза вибіркою можна лише для прогону розробки", True)

# ================================================================ 5. вкладка
from playwright.sync_api import sync_playwright, expect  # noqa: E402

PORT = 8783
URL = f"http://127.0.0.1:{PORT}"
appdata = tempfile.mkdtemp(prefix="smctr_app_")
make_cache(appdata, SYMS, months=12)
sid_old = BS.run(appdata, SCREENER, 12, ARGS, workers=2)
with sqlite3.connect(os.path.join(appdata, "backtest", "signals.db")) as con:   # «старий» прохід без list_snap
    con.execute("UPDATE sig_events SET list_snap=NULL")
with open(os.path.join(appdata, "settings.json"), "w") as f:
    json.dump({"screener_path": SCREENER, "results_dir": tempfile.mkdtemp(), "caffeinate": False}, f)
app = subprocess.Popen([sys.executable, os.path.join(APP, "app.py"), "--port", str(PORT), "--no-browser"],
                       env=dict(os.environ, SMC_APP_DATA=appdata), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        expect(pg.locator("#btTrRun")).to_be_enabled()
        check("до першого прогону результатів немає", pg.locator("#btTrRes").is_hidden())
        pg.click("#btTrRun")
        expect(pg.locator("#btTrMsg")).to_contain_text("Знайти сигнали", timeout=20000)
        check("старий прохід сигналів → зрозуміле прохання перезапустити крок 2", True)
        pg.click("#btSigStart")
        expect(pg.locator("#btSigStop")).to_be_visible(timeout=5000)
        check("під час пошуку сигналів «Прогнати угоди» вимкнено", pg.locator("#btTrRun").is_disabled())
        expect(pg.locator("#btSigStop")).to_be_hidden(timeout=240000)
        expect(pg.locator("#btSigProgTxt")).to_contain_text("готово")
        pg.click("#btTrRun")
        expect(pg.locator("#btTrMsg")).to_contain_text("Готово", timeout=60000)
        expect(pg.locator("#btTrRes")).to_be_visible()
        v = pg.locator("#btTrVar").inner_text()
        check("порівняння двох варіантів ведення: середній R, PF, просадка, серія", "Середній R" in v and "Profit factor" in v
              and "просадка" in v and "серія" in v, v[:200])
        check("висновок простими словами під таблицею угод", pg.locator("#btTrSummary .verdict").count() == 1
              and pg.locator("#btTrSummary li").count() >= 4)
        check("воронка: сигналів → входів → причини скасування", "Входів" in pg.locator("#btTrFunnel").inner_text())
        check("MFE: 5 рядків", pg.locator("#btTrMfe tr").count() == 5)
        pg.click("#btTrListBox summary")
        n_all = pg.locator("#btTrBody tr").count()
        pg.select_option("#btTrFilter", "loss")
        n_loss = pg.locator("#btTrBody tr").count()
        check("список угод і фільтр «у мінус»", n_all > 0 and 0 < n_loss < n_all and pg.locator("#btTrBody .rneg").count() >= n_loss, (n_all, n_loss))
        pg.select_option("#btTrFilter", "")
        pg.screenshot(path=os.path.join(shots, "bt3_trades.png"), full_page=True)
        pg.fill("#btTrForm input[name=expiry_h]", "48")
        pg.click("#btTrRun")
        expect(pg.locator("#btTrMsg")).to_contain_text("Готово", timeout=60000)
        pg.click("#btTrRunsBox summary")
        check("другий прогін з терміном 48 год — в історії прогонів", pg.locator("#btTrRuns .bt-run").count() == 2
              and "48 год" in pg.locator("#btTrRuns").inner_text())
        pg.locator("#btTrRuns [data-trrun]").last.click()
        expect(pg.locator("#btTrPeriod")).to_contain_text("№1")
        check("відкриття попереднього прогону повертає його параметри у форму", pg.input_value("#btTrForm input[name=expiry_h]") == "0")
        # ---- етап 4: розрізи, фільтри
        check("висновок над таблицею розрізів", pg.locator("#btSlSummary .verdict").count() == 1)
        check("розрізи: таблиця з 12 ознаками", pg.locator("#btSlBox tr.sl-hd").count() >= 10, pg.locator("#btSlBox tr.sl-hd").count())
        pg.select_option("#btTrForm select[name=min_score]", "1")
        pg.check("#btTrForm input[name=no_knife]")
        pg.click("#btTrRun")
        old_v = pg.locator("#btTrSummary").inner_text()
        expect(pg.locator("#btTrFunnel")).to_contain_text("Відфільтровано", timeout=60000)
        check("висновок оновився після нового прогону", pg.locator("#btTrSummary").inner_text() != old_v)
        check("фільтри: «бали ≥ 1, без «ножа»» у заголовку прогону й розрізів", "бали ≥ 1, без «ножа»" in pg.locator("#btTrPeriod").inner_text()
              and "бали ≥ 1" in pg.locator("#btSlRun").inner_text())
        check("у розрізі «Бали» лише групи ≥ 1", "≤ −1" not in pg.locator("#btSlBox").inner_text())
        # ---- етап 5: поза вибіркою
        check("правила для перевірки видно перед відкриттям", "бали ≥ 1" in pg.locator("#btOosRules").inner_text())
        pg.on("dialog", lambda dlg: dlg.accept())
        pg.click("#btOosOpen")
        expect(pg.locator("#btOosRes")).to_be_visible(timeout=60000)
        t5 = pg.locator("#btOosTbl").inner_text()
        check("розробка поруч із поза вибіркою: середній R, PF, просадка", "Середній R" in t5 and "Profit factor" in t5, t5[:150])
        check("висновок перевірки поза вибіркою", pg.locator("#btOosSummary .verdict").inner_text().startswith("Поза вибіркою"))
        check("журнал відкриттів: 1", "(1)" in pg.locator("#btOosCount").inner_text())
        pg.click("#btOosOpen")
        expect(pg.locator("#btOosCount")).to_contain_text("(2)", timeout=60000)
        check("повторне відкриття — попередження про знецінення перевірки", "відкривали 2 рази" in pg.locator("#btOosNote").inner_text())
        pg.screenshot(path=os.path.join(shots, "bt45_slices_oos.png"), full_page=True)
        pg.reload()
        pg.click(".tab[data-tab=backtest]")
        expect(pg.locator("#btTrRes")).to_be_visible(timeout=10000)
        check("після перезавантаження останній прогін показується одразу", True)
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.wait_for_timeout(300)
        sw = pg.evaluate("document.documentElement.scrollWidth")
        wide = pg.evaluate("""[...document.querySelectorAll('#tab-backtest *')].filter(e => e.getBoundingClientRect().right > 392).slice(0, 4).map(e => e.tagName + '.' + e.className + '#' + e.id)""")
        check("вузьке вікно без горизонтальної прокрутки", sw <= 392, (sw, wide))
        errors = [e for e in errors if "status of 400" not in e]   # 400 — очікувана відповідь на «старий прохід»
        check("помилок у консолі немає", not errors, errors[:3])
        b.close()
finally:
    app.terminate()

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"), "| скріншоти:", shots)
sys.exit(0 if ok else 1)
