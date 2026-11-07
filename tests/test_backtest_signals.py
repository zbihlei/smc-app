#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Бектест, етап (b): replay скринера по кешу + звірка з логом + розділ «2. Сигнали» у вкладці.
Запуск: python3 tests/test_backtest_signals.py ШЛЯХ_ДО_smc_screener.py [папка_скріншотів]"""
import csv
import datetime as dt
import json
import os
import random
import shutil
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

ok = True
H1, H4 = BS.H1, BS.H4


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


from test_backtest_signals_helpers import make_cache, SYMS, ARGS  # noqa: E402

# ================================================================ 1. cfg = як у живому скринері
cfg, meta = BS.live_cfg(SCREENER, ARGS)
check("налаштування з main() скринера: approach 4, strict, min-stop-atr 0.6, стоп за гнотом", cfg["approach"] == 4.0
      and cfg["trend"] == "strict" and cfg["min_stop_atr"] == 0.6 and cfg["wick"] is True and cfg["min_rr"] == 3.0, cfg)
cfg2, _ = BS.live_cfg(SCREENER, [x for x in ARGS if x != "--stop-wick"])
check("без --stop-wick → wick=False (той самий розбір аргументів)", cfg2["wick"] is False)
try:
    BS.live_cfg(SCREENER, ["--approach", "абв"])
    check("некоректні аргументи — зрозуміла помилка", False)
except ValueError as e:
    check("некоректні аргументи — зрозуміла помилка", "аргументи" in str(e))

# ================================================================ 2. прохід
dA = tempfile.mkdtemp(prefix="smcsig_")
start, end = make_cache(dA, SYMS)
t = time.time()
rid = BS.run(dA, SCREENER, 2, ARGS, workers=2)
el = time.time() - t
store = BS.Store(dA)
run = store.run(rid)
ev = store.events(rid)
lst = [e for e in ev if e["kind"] == "list"]
check("прохід завершено, кожна година = крок", run["status"] == "done" and run["steps"] >= 3 * ((end - start) // H1 - 5), (run["status"], run["steps"]))
check("знайдено сигнали «у списку» й відсіяні", len(lst) > 3 and any(e["kind"] == "reject" for e in ev), (len(lst), len(ev)))
check(f"швидкість: {run['steps']} кроків за {el:.0f} с", el < 240)
check("кожна подія: зона, вхід = середина, стоп за зоною, R:R ≥ 3",
      all(abs(e["entry"] - (e["zone_low"] + e["zone_high"]) / 2) < 1e-9 * e["entry"] and e["rr"] >= 3
          and ((e["stop"] < e["zone_low"]) if e["dir"] == "Long" else (e["stop"] > e["zone_high"])) for e in lst))
check("час події — межа години, у межах періоду", all(e["first_ms"] % H1 == 0 and start < e["first_ms"] <= end for e in ev))
zt = [e for e in lst if e["touch_ms"]]
check("торкання зони всередині години (аналог живого алерту) — після появи", zt and all(e["touch_ms"] >= e["first_ms"] for e in zt), len(zt))

# точна перевірка однієї події: на кроці першої появи функція скринера з тими самими даними дає ту саму пару
mod = BD.load_screener_module(SCREENER)
e = lst[len(lst) // 2]
T = e["first_list_ms"]
c4 = [{"t": a, "o": o, "h": h, "l": l, "c": c, "v": v, "closed": True} for a, o, h, l, c, v in BD.Cache(dA).candles(e["symbol"], "4h", None, T - H4 + 1)]
px = BD.Cache(dA).candles(e["symbol"], "1h", T - H1, T)[0][4]
cc = dict(cfg, rejects=[])
rows = mod.analyze_symbol(e["symbol"], c4[-BS.WINDOW:], px, cc)
check("перевірка вручну: на момент появи скринер бачить саме цю пару (лише закриті 4h + ціна години)",
      rows and BS.key_of(rows[0]) == e["key"], (e["key"], [BS.key_of(r) for r in rows]))
rows_prev = mod.analyze_symbol(e["symbol"], [x for x in c4 if x["t"] + H4 <= T - H1][-BS.WINDOW:],
                               BD.Cache(dA).candles(e["symbol"], "1h", T - 2 * H1, T - H1)[0][4], dict(cfg, rejects=[]))
check("годиною раніше цієї пари в списку ще не було", e["appearances"] > 1 or all(BS.key_of(r) != e["key"] for r in rows_prev))

# ================================================================ 3. без підглядання в майбутнє
X = start + (end - start) // 2
X -= X % H4
dB = tempfile.mkdtemp(prefix="smcsigB_")
make_cache(dB, SYMS, alter_after=X)
ridB = BS.run(dB, SCREENER, 2, ARGS, workers=2)
evB = {e["key"]: e for e in BS.Store(dB).events(ridB)}
before = [e for e in ev if e["first_ms"] <= X]
same = [e for e in before if e["key"] in evB and evB[e["key"]]["first_ms"] == e["first_ms"]
        and evB[e["key"]]["first_status"] == e["first_status"] and abs(evB[e["key"]]["stop"] - e["stop"]) < 1e-12]
check(f"інше «майбутнє» після {BD.fmt_ms(X)} не змінює подій, що з'явились раніше ({len(same)} з {len(before)})",
      before and len(same) == len(before))
after = [e for e in ev if e["first_ms"] > X + H1]
check("а події після зміни — інші (тест справді щось перевіряє)", any(e["key"] not in evB for e in after) or not after)

# ================================================================ 4. звірка з живим логом
log_dir = tempfile.mkdtemp()
log_path = os.path.join(log_dir, "signals_log.csv")
a_ms = end - 10 * 86400_000
win = [e for e in lst if e["first_list_ms"] >= a_ms]
hdr = ["time", "kind", "symbol", "dir", "status", "score", "price", "zone_low", "zone_high", "entry", "stop", "target", "rr",
       "dist", "struct4h", "against", "bos_time", "knife", "warnings"]


def lt(ms):
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


with open(log_path, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(hdr)
    for ms in range(a_ms, end, 1800_000):   # «живі» скани кожні 30 хв, крім «сну» Mac у першу добу вікна
        if ms < a_ms + 86400_000:
            continue
        w.writerow([lt(ms), "у списку", "BTCUSDT", "Long", "підхід до зони", 0, 1, 1, 1.1, 1.05, 0.9, 2, 3, 1, "", "ні", "", "ні", ""])
    for e in win[1:]:   # першу подію «пропустимо» — її живий лог не бачив
        ms = e["first_list_ms"] + 1800_000 * random.Random(1).choice([-1, 0, 1])
        w.writerow([lt(ms), "у списку", e["symbol"], e["dir"], e["first_status"], e["score"], 1, repr(e["zone_low"]), repr(e["zone_high"]),
                    e["entry"], e["stop"], e["target"], e["rr"], 0, "", "ні", "", "ні", ""])
    w.writerow([lt(end - 5 * 3600_000), "у списку", "AAAUSDT", "Short", "підхід до зони", 0, 1, 123.456, 130, 126, 131, 100, 3, 1, "", "ні", "", "ні", ""])
cmp = BS.compare(lst + [e for e in ev if e["kind"] == "reject"], log_path, SYMS + ["XYZUSDT"], end)
L = cmp["list"]
miss_first = win and win[0]["last_ms"] < a_ms + 86400_000 - 45 * 60_000   # уся подія припала на «сон» Mac
check(f"звірка: збіглось {L['matched']} з {L['log']}, запізнення ±0,5 год", L["matched"] == len(win) - 1 and abs(L["lag_median_h"] or 0) <= 0.5,
      (L["matched"], len(win), L["lag_median_h"]))
check("лише в лозі: вигадана зона AAAUSDT і BTCUSDT (немає в кеші)",
      sorted((x["symbol"], x["reason"]) for x in L["only_log"]) == [("AAAUSDT", "replay не знайшов"), ("BTCUSDT", "монети немає в кеші бектесту")],
      L["only_log"])
check("лише в replay: пропущена подія розпізнана (і пояснена, якщо Mac «спав»)", any(x["symbol"] == win[0]["symbol"] for x in L["only_replay"])
      and (L["explained_only_replay"] >= 1) == bool(miss_first), (L["only_replay"][:3], miss_first))
summ = BS.summarize(ev, start, end)
check("підсумок по місяцях сходиться із загальним", sum(m["list"] for m in summ["months"]) == summ["list"] == len(lst))

# ================================================================ 5. у додатку: кнопка «Знайти сигнали»
import re  # noqa: E402
names = {}
for fn in sorted(os.listdir(os.path.join(APP, "static"))):
    if fn.endswith(".js") and not fn.startswith("lightweight"):
        src = open(os.path.join(APP, "static", fn), encoding="utf-8").read()
        for n in re.findall(r"^(?:async\s+)?function\s+(\w+)|^(?:const|let)\s+(\w+)\s*=", src, re.M):
            names.setdefault(n[0] or n[1], []).append(fn)
dup = {k: v for k, v in names.items() if len(v) > 1}
check("у скриптах вкладок немає однакових глобальних імен (інакше одна вкладка ламає іншу)", not dup, dup)

from playwright.sync_api import sync_playwright, expect  # noqa: E402


def open_bt(pg):
    """Розкрити всі згорнуті блоки вкладки «Бектест» (спрощений вигляд ховає деталі)."""
    pg.evaluate("document.querySelectorAll('#tab-backtest details').forEach(d => d.open = true)")

PORT = 8784
URL = f"http://127.0.0.1:{PORT}"
appdata = tempfile.mkdtemp(prefix="smcsig_app_")
make_cache(appdata, SYMS)
with open(os.path.join(appdata, "settings.json"), "w") as f:
    json.dump({"screener_path": SCREENER, "results_dir": log_dir, "caffeinate": False}, f)
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
        pg.click(".tab[data-tab=backtest]"); open_bt(pg)
        expect(pg.locator("#btSigStart")).to_be_enabled()
        check("до першого проходу результатів немає", pg.locator("#btSigRes").is_hidden())
        pg.click("#btSigStart")
        expect(pg.locator("#btSigStop")).to_be_visible(timeout=5000)
        r = pg.request.post(URL + "/api/backtest/data/start", headers={"X-SMC-App": "1"}, data=json.dumps({"months": 6, "top": 5}))
        check("під час проходу завантаження даних заблоковано (409)", r.status == 409)
        expect(pg.locator("#btSigProgTxt")).to_contain_text("готово", timeout=240000)
        expect(pg.locator("#btSigRes")).to_be_visible(timeout=10000)
        txt = pg.locator("#btSigSum").inner_text()
        check("підсумок: сигнали на тиждень, торкання зони, Long/Short", "на тиждень" in txt and "Торкнулись" in txt, txt)
        cm = pg.locator("#btCmp").inner_text()
        check("звірка з логом у вкладці: збіглось N з M, лише в лозі / в replay", "збіглось" in cm and "Лише в живому лозі" in cm, cm[:200])
        open_bt(pg)
        check("таблиця сигналів заповнена", pg.locator("#btSigEv tr").count() >= 3)
        open_bt(pg)
        check("видно налаштування проходу і версію скринера", "approach=4" in pg.locator("#btSigPar").inner_text()
              and "md5" in pg.locator("#btSigPar").inner_text())
        pg.screenshot(path=os.path.join(shots, "bt2_signals.png"), full_page=True)
        # зупинка посеред проходу
        pg.click("#btSigStart")
        expect(pg.locator("#btSigStop")).to_be_visible(timeout=5000)
        pg.wait_for_timeout(1500)
        pg.click("#btSigStop")
        expect(pg.locator("#btSigProgTxt")).to_contain_text("зупинено", timeout=15000)
        open_bt(pg)
        check("зупинений прохід позначено, попередній результат лишається", "зупинено" in pg.locator("#btSigRuns").inner_text()
              and pg.locator("#btSigRes").is_visible())
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.wait_for_timeout(300)
        sw = pg.evaluate("document.documentElement.scrollWidth")
        wide = pg.evaluate("""[...document.querySelectorAll('#tab-backtest *')].filter(e => e.getBoundingClientRect().right > 392).slice(0, 4).map(e => e.tagName + '.' + e.className + '#' + e.id)""")
        check("вузьке вікно без горизонтальної прокрутки", sw <= 392, (sw, wide))
        check("помилок у консолі немає", not errors, errors[:3])
        b.close()
finally:
    app.terminate()

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"), "| скріншоти:", shots)
sys.exit(0 if ok else 1)
