#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.6.1: сповіщення Mac по черзі (без «хору»), пачка за скан — одним зведеним; Telegram — кожен окремо.
Запуск: python3 tests/test_alerts.py"""
import os
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import screener_runner as R  # noqa: E402
import tg  # noqa: E402

ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


mac, spoken, tgs = [], [], []
busy = {"n": 0, "max": 0}
lock = threading.Lock()


def mac_notify(title, text, kind="new", speak="", ticker=""):
    with lock:
        busy["n"] += 1
        busy["max"] = max(busy["max"], busy["n"])
    mac.append((time.time(), title, text, kind, speak, ticker))
    time.sleep(0.02)
    with lock:
        busy["n"] -= 1


def say(text):
    spoken.append((time.time(), text))
    time.sleep(0.1)  # «говорить» 0.1 с


def alert_new(rows, seen_path):
    for sym, kind in rows:
        title = "ЦІНА В ЗОНІ" if kind == "zone" else "НОВА ПАРА"
        mod.notify("SMC-скринер", f"{title}: {sym} Long | бали 1 | вхід 1", kind, f"New pair {sym} long", "BINANCE:" + sym)
    return len(rows)


mod = types.SimpleNamespace(notify=mac_notify, alert_new=alert_new, ALERT={"voice": True}, save_json=lambda *a: None,
                            append_log=lambda *a, **k: None, MKT={"futures": True})
os.environ["SMC_TG_CHAT"] = "123"
tg.send_bg = lambda chat, html, **k: tgs.append(html)
R.GAP_S = 0.05
R.install_hooks(mod)
mod._mac_queue.gap = 0.05
mod._mac_queue.say = say

# ---- 1. один-два алерти за скан: кожен окремо, по черзі
mod.alert_new([("EURUSDT", "new"), ("ETHUSDT", "zone")], "x")
mod._mac_queue.wait_idle()
check("2 алерти: на Mac обидва окремо, з назвою пари і кнопкою TradingView", len(mac) == 2 and "EURUSDT" in mac[0][2]
      and mac[1][3] == "zone" and mac[0][5] == "BINANCE:EURUSDT", mac)
check("голос — для кожного, але не одночасно (наступний — після кінця попереднього)",
      len(spoken) == 2 and spoken[1][0] - spoken[0][0] >= 0.1, spoken)
check("Mac ніколи не показує два сповіщення в ту саму мить", busy["max"] == 1)
check("Telegram — кожен алерт окремим повідомленням", len(tgs) == 2)

# ---- 2. пачка за скан: одне зведене сповіщення на Mac, у Telegram — усі
mac.clear(), spoken.clear(), tgs.clear()
syms = [("EURUSD", "new"), ("GBPJPY", "new"), ("AUDCAD", "zone"), ("USDJPY", "new"), ("CHFJPY", "new"), ("NZDCAD", "new"),
        ("EURGBP", "new"), ("EURJPY", "new"), ("GBPUSD", "new"), ("CADJPY", "new")]
mod.alert_new(syms, "x")
mod._mac_queue.wait_idle()
check("10 алертів за скан → на Mac ОДНЕ зведене сповіщення", len(mac) == 1, [m[2] for m in mac])
t = mac[0][2]
check("у ньому: скільки, скільки в зоні, перші пари, «і ще 2»", t.startswith("Нових сигналів: 10 (у зоні: 1)") and "EURUSD Long" in t
      and "і ще 2" in t, t)
check("голос один: «10 new signals, 1 in zone»", [x[1] for x in spoken] == ["10 new signals, 1 in zone"], spoken)
check("Telegram — усі 10 окремо (як і було)", len(tgs) == 10)

# ---- 3. живі алерти «ціна в зоні» між сканами — теж по черзі
mac.clear(), spoken.clear()
ths = [threading.Thread(target=mod.notify, args=("SMC-скринер", f"ЦІНА В ЗОНІ: P{i}USDT Long", "zone", f"Price in zone P{i}", "")) for i in range(3)]
[x.start() for x in ths]
[x.join() for x in ths]
mod._mac_queue.wait_idle()
check("живі алерти з різних потоків — по одному", len(mac) == 3 and busy["max"] == 1
      and all(spoken[i + 1][0] - spoken[i][0] >= 0.1 for i in range(2)), (len(mac), busy))

# ---- 4. голос вимкнено — не говорить
mac.clear(), spoken.clear()
mod.ALERT["voice"] = False
mod.alert_new([("XRPUSDT", "new")], "x")
mod._mac_queue.wait_idle()
check("голос вимкнено в «Налаштуваннях» → лише банер", len(mac) == 1 and not spoken)
check("на Mac голос ніколи не запускається напряму з банера (лише з черги)", all(m[4] == "" for m in mac))

# ---- 5. крипто й форекс (два окремі скринери) — теж не одночасно
mac.clear(), spoken.clear()
busy.update(n=0, max=0)
mod.ALERT["voice"] = True
q2 = R.MacQueue(mod, mac_notify, say=say, gap=0.05)
for i in range(3):
    mod._mac_queue.put("SMC-скринер", f"НОВА ПАРА: C{i}USDT Long", "new", f"New pair C{i}", "")
    q2.put("SMC-скринер ФОРЕКС", f"НОВА ПАРА: EURUSD{i} Long", "new", f"New pair EURUSD{i}", "")
mod._mac_queue.wait_idle(), q2.wait_idle()
sp = sorted(t for t, _ in spoken)
check("крипто + форекс одночасно → голоси все одно по черзі (спільний замок)", len(spoken) == 6 and busy["max"] == 1
      and all(sp[i + 1] - sp[i] >= 0.1 for i in range(5)), (len(spoken), busy))

# ---- 6. «ЦІНА В ЗОНІ»: один алерт на зону (ціна «тремтить» біля краю зони між сканами) — справжній скринер
import tempfile  # noqa: E402
APP = os.path.dirname(HERE)
scr = R.load_screener(os.path.join(APP, "smc_screener.py"))
R.FIRED.update(path=None, data={})
got = []
scr.notify = lambda title, text, kind="new", speak="", ticker="": got.append(text.split(" | ")[0])
scr.append_log = lambda *a, **k: None
out = tempfile.mkdtemp()
seen = os.path.join(out, "seen.json")
row = {"symbol": "WLFIUSDT", "dir": "Short", "status": "підхід до зони", "rank": 1, "score": 1, "price": 0.0463,
       "zone_low": 0.04635, "zone_high": 0.04726, "entry": 0.04658, "stop": 0.04733, "target": 0.04351, "rr": 4.1,
       "dist": 0.1, "flags": [], "sweep": "", "volume": "-"}
ticks = iter([0.04630, 0.04635, 0.04633, 0.04636, 0.04631, 0.04635] * 20)
scr.fetch_prices = lambda: {"WLFIUSDT": next(ticks)}
lw = R.make_live_wait(scr)
a = types.SimpleNamespace(watch=0.003, price_check=0.02, out=out)
for _ in range(3):  # три цикли між сканами: кожного разу ціна знову торкається 0.04635
    lw(a, [dict(row)], seen)
check("живий «ЦІНА В ЗОНІ» по тій самій зоні — лише один раз за кілька циклів", got.count("ЦІНА В ЗОНІ (наживо): WLFIUSDT Short") == 1, got)
R.FIRED.update(path=None, data={})
lw(a, [dict(row)], seen)
check("і після перезапуску скринера (пам'ять у results/live_fired.json) — без повтору", len(got) == 1, got)
got.clear()
scr.alert_new([dict(row, status="у зоні (1-й дотик)", rank=0)], seen)
check("повний скан: «у зоні» по зоні, де вже був живий алерт, — без повторного «ЦІНА В ЗОНІ»", got == [], got)
scr.alert_new([dict(row, status="у зоні (1-й дотик)", rank=0, zone_low=0.0450, zone_high=0.0455)], seen)
check("нова зона тієї ж монети — алерт приходить", len(got) == 1 and "WLFIUSDT" in got[0], got)

# ---- 6b. різкий рух BTC: попередження в живому алерті (свіже, на момент входу в зону)
scr._BTC.update(t=0.0, v=None)
scr.fetch_klines = lambda sym, itv, lim: [{"t": i, "o": 100, "h": 100, "l": 100, "c": c, "v": 1, "closed": True}
                                          for i, c in enumerate([100, 100, 100, 100, 100, 97.6])]
full = []
scr.notify = lambda title, text, kind="new", speak="", ticker="": full.append(text)
scr.fetch_prices = lambda: {"XYZUSDT": 1.0}
lw(a, [dict(row, symbol="XYZUSDT", dir="Long", zone_low=0.99, zone_high=1.01, entry=1.0, stop=0.98, target=1.1, flags=[])], seen)
check("BTC −2.4% за 1 год → у живому алерті Long «BTC різко падає … ПРОТИ угоди»",
      len(full) == 1 and "BTC різко падає: -2.4% за 1 год — ПРОТИ угоди" in full[0], full)
scr._BTC.update(t=0.0, v=None)
scr.fetch_klines = lambda sym, itv, lim: [{"t": i, "o": 100, "h": 100, "l": 100, "c": 100.3, "v": 1, "closed": True} for i in range(6)]
rr = [dict(row, flags=["BTC різко падає: старе", "тісний стоп"])]
scr.apply_btc(rr)
check("BTC спокійний → старе попередження прибрано, інші лишаються", rr[0]["flags"] == ["тісний стоп"], rr[0]["flags"])

# ---- 7. «Сканувати зараз»: прапорець від додатка перериває очікування між сканами
open(os.path.join(out, R.SCAN_NOW_FILE), "w").close()
t0 = time.time()
lw(types.SimpleNamespace(watch=1, price_check=15, out=out), [], seen)
check("прапорець scan_now.flag → скан одразу (не через 1 хв), прапорець прибрано",
      time.time() - t0 < 4 and not os.path.exists(os.path.join(out, R.SCAN_NOW_FILE)), time.time() - t0)

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"))
sys.exit(0 if ok else 1)
