#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v0.5: скан після сну Mac і повтор після збою мережі (screener_runner), вік скану й наглядач (app.py),
прив'язка сигналів скринера до угод (API + інтерфейс).
Запуск: python3 tests/test_v05.py ШЛЯХ_ДО_smc_screener.py [папка з копією справжніх data/journal.db і results/signals_log.csv]"""
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
sys.path.insert(0, HERE)
SCREENER = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(APP, "smc_screener.py")
REAL = sys.argv[2] if len(sys.argv) > 2 else None
ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


# ================================================================ 1. screener_runner: сон і повтор (фальшивий годинник)
import screener_runner as R  # noqa: E402

mod = R.load_screener(SCREENER)
R.install_hooks(mod)
check("обгортка підмінила live_wait і scan справжнього скринера", mod.live_wait.__name__ == "live_wait"
      and mod.live_wait.__module__ == "screener_runner" and mod.scan.__name__ == "scan")


class FakeClock:
    """time.time() і time.sleep(): сон Mac = годинник стрибає вперед під час одного sleep()."""
    def __init__(self):
        self.now = 1_000_000.0
        self.slept = []
        self.jump_at = None  # (номер виклику sleep, на скільки секунд стрибнути)

    def time(self):
        return self.now

    def sleep(self, sec):
        self.slept.append(sec)
        self.now += sec
        if self.jump_at and len(self.slept) == self.jump_at[0]:
            self.now += self.jump_at[1]


lines = []
R.log = lambda m: lines.append(m)
a = types.SimpleNamespace(watch=30, price_check=15)

fc = FakeClock()
R.time = fc
R.STATE["scan_failed"] = False
mod.live_wait(a, [], "seen.json")
check("без сну: чекає повні 30 хв короткими кроками", abs(sum(fc.slept) - 1800) < 1e-6 and max(fc.slept) <= 15)

fc = FakeClock()
fc.jump_at = (3, 6 * 3600)  # на 3-му кроці Mac проспав 6 годин
R.time = fc
lines.clear()
t0 = fc.now
mod.live_wait(a, [], "seen.json")
check("сон 6 год: одразу після пробудження вихід на скан (з паузою 20 с на мережу)",
      len(fc.slept) == 4 and fc.slept[-1] == R.WAKE_DELAY_S and any("прокинувся" in x for x in lines), (fc.slept, lines))

fc = FakeClock()
fc.jump_at = (2, 60)  # затримка 1 хв (не сон) — не вважається пробудженням
R.time = fc
lines.clear()
mod.live_wait(a, [], "seen.json")
check("коротка затримка (< 90 с) — не пробудження, чекає до кінця", not any("прокинувся" in x for x in lines))

orig = R.STATE.copy()
R.STATE["scan_failed"] = True
fc = FakeClock()
R.time = fc
lines.clear()
mod.live_wait(a, [], "seen.json")
check("після збою скану повтор через 2 хв, а не 30", abs(sum(fc.slept) - 120) < 1e-6 and any("повтор через 2 хв" in x for x in lines))
R.STATE.update(orig)

calls = []
mod_fetch, mod_touch, mod_fire = mod.fetch_prices, mod.live_touches, mod.fire_live
mod.fetch_prices = lambda: (calls.append("p"), {"AAAUSDT": 101.0})[1]
fired = []
mod.fire_live = lambda a_, r, px, sp, st: fired.append((r["symbol"], px))
refreshed = []
fc = FakeClock()
R.time = fc
row = dict(symbol="AAAUSDT", dir="Long", zone_low=100.0, zone_high=102.0)
mod.live_wait(a, [row], "seen.json", refresh=lambda: refreshed.append(1))
check("жива перевірка ціни кожні 15 с і алерт «ціна в зоні» працюють як раніше",
      len(calls) == 120 and fired == [("AAAUSDT", 101.0)] and refreshed == [1] and row["rank"] == 0)
mod.fetch_prices, mod.live_touches, mod.fire_live = mod_fetch, mod_touch, mod_fire

boom = {"n": 0}


def bad_scan(*args, **kw):
    boom["n"] += 1
    raise RuntimeError("Failed to resolve 'fapi.binance.com'")


R2 = importlib.reload(R)
mod2 = R2.load_screener(SCREENER)
mod2.scan = bad_scan
R2.install_hooks(mod2)
try:
    mod2.scan(None, None)
except RuntimeError:
    pass
check("збій мережі в скані позначається для повтору", R2.STATE["scan_failed"] is True)
mod2_scan_ok = mod2.scan
R2.STATE["scan_failed"] = True

# ================================================================ 2. app.py: вік скану, «пропущені скани», наглядач
import app as A  # noqa: E402

res_dir = tempfile.mkdtemp()
s = {"results_dir": res_dir, "watch": 30}
started = time.time() - 3 * 3600


class FakeScr:
    name = "крипто"
    log_path = None

    def __init__(self, st):
        self.st = st

    @staticmethod
    def out_fn(s):
        return A.results_dir(s)

    def status(self):
        return dict(self.st)


A.SCREENER = FakeScr({"running": True, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)), "last_exit": None})
p = os.path.join(res_dir, "latest_app.json")
open(p, "w").write("{}")
os.utime(p, (time.time() - 5 * 60, time.time() - 5 * 60))
age, stale = A.scan_age(None, s)
check("скан 5 хв тому: не пропущено", not stale and 280 < age < 320, (age, stale))
os.utime(p, (time.time() - 70 * 60, time.time() - 70 * 60))
age, stale = A.scan_age(None, s)
check("скан 70 хв тому при інтервалі 30 хв: «скани пропущено»", stale and 69 * 60 < age < 71 * 60, (age, stale))
A.SCREENER = FakeScr({"running": False, "last_exit": None})
check("скринер зупинено — попередження немає", A.scan_age(None, s) == (None, False))

notes = []
A.notify_mac = lambda t, x: notes.append((t, x))
A.SCREENER = FakeScr({"running": True, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)), "last_exit": None})
A.load_settings = lambda: dict(A.DEFAULTS, **s)
th = threading.Thread(target=A.watchdog, kwargs={"interval": 0.2, "wake_gap": 90, "grace": 0}, daemon=True)
th.start()
time.sleep(1.0)
check("наглядач: одне сповіщення «немає сканів» (без повторів)", len([n for n in notes if "немає сканів" in n[0]]) == 1, notes)
A.SCREENER.st = {"running": False, "last_exit": {"code": 1, "at": "12:00:00"}}
time.sleep(0.6)
check("наглядач: сповіщення «скринер зупинився»", any("зупинився" in n[0] for n in notes), notes)

# ================================================================ 3. прив'язка сигналів (на копії справжнього журналу й логу)
if REAL:
    from playwright.sync_api import sync_playwright, expect
    PORT = 8791
    work = tempfile.mkdtemp(prefix="smc05_")
    shutil.copytree(os.path.join(REAL, "data"), os.path.join(work, "data"))
    shutil.copytree(os.path.join(REAL, "results"), os.path.join(work, "results"))
    with open(os.path.join(work, "data", "settings.json"), "w") as f:
        json.dump({"screener_path": SCREENER, "results_dir": os.path.join(work, "results")}, f)
    srv = subprocess.Popen([sys.executable, os.path.join(APP, "app.py"), "--port", str(PORT), "--no-browser"],
                           env=dict(os.environ, SMC_APP_DATA=os.path.join(work, "data")), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/status", timeout=1)
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
            pg.goto(f"http://127.0.0.1:{PORT}")
            pg.click(".tab[data-tab=journal]")
            expect(pg.locator("#jLinkCount")).to_have_text("8", timeout=8000)
            check("справжній журнал: 8 угод можна прив'язати до сигналів (№16–19, 21–24)", True)
            pg.click("#jLinkBtn")
            expect(pg.locator("#lnkBox .lnk-item")).to_have_count(8)
            sol = pg.locator("#lnkBox .lnk-item:has-text('№24')").inner_text()
            check("№24 SOL: видно, що скринер відсіяв сигнал (проти структури 4h)", "відсіяв" in sol and "SOLUSDT" in sol, sol)
            pg.screenshot(path=os.path.join(work, "links.png"))
            pg.click("#lnkBox [data-link='22'][data-scr]")
            expect(pg.locator("#jMsg")).to_contain_text("№22: сигнал RUNEUSDT прив'язано")
            expect(pg.locator("#jLinkCount")).to_have_text("7")
            check("№22 RUNE прив'язано, «По скринеру» = так, лічильник 8 → 7",
                  "так" in pg.locator("#jBody tr[data-n='22']").inner_text())
            pg.click("#lnkBox [data-link='21']:not([data-scr])")
            expect(pg.locator("#jLinkCount")).to_have_text("6")
            check("«Лише прив'язати» не змінює «По скринеру» (№21 лишився «ні»)",
                  "ні" in pg.locator("#jBody tr[data-n='21'] td:nth-child(17)").inner_text())
            pg.click("#lnkBox [data-dismiss='24']")
            expect(pg.locator("#jLinkCount")).to_have_text("5")
            check("«Не те» прибирає пропозицію назавжди", "№24" not in pg.locator("#lnkBox").inner_text())
            pg.on("dialog", lambda d: d.accept())
            pg.click("#lnkBox [data-lclear]")
            expect(pg.locator("#jMsg")).to_contain_text("Список очищено (5)")
            expect(pg.locator("#jLinkCount")).to_be_hidden()
            check("«Очистити список»: усі 5 пропозицій прибрано, лічильник зник", "Немає угод без прив" in pg.locator("#lnkBox").inner_text())
            check("очищення не змінює угод: №23 без сигналу, «По скринеру» = ні",
                  "ні" in pg.locator("#jBody tr[data-n='23'] td:nth-child(17)").inner_text())
            r = pg.request.post(f"http://127.0.0.1:{PORT}/api/journal/save", headers={"X-SMC-App": "1"}, data=json.dumps({"trade": {
                "date": "2026-11-05", "instrument": "SUI", "dir": "Short", "entry": "0,9499", "stop": "0,9632", "target": "0,9095",
                "result": "1", "risk": "1"}}))
            pg.click(".tab[data-tab=summary]"); pg.click(".tab[data-tab=journal]")
            expect(pg.locator("#jLinkCount")).to_have_text("1")
            check("нова угода після очищення знову отримує пропозицію сигналу", r.ok)
            pg.click(".tab[data-tab=summary]")
            expect(pg.locator("#sSig")).to_contain_text("Угод із прив'язаним сигналом: 2")
            check("«Підсумок»: блок відхилень від скринера ожив", True)
            pg.click(".tab[data-tab=journal]")
            pg.click("#jBody tr[data-n='22']")
            expect(pg.locator("#sigChosen")).to_contain_text("RUNEUSDT")
            check("у формі угоди №22 видно прив'язаний сигнал", True)
            pg.keyboard.press("Escape")
            pg.click("#jAddManual")
            pg.fill("input[name=instrument]", "INJ")
            pg.check("input[name=dir][value=Short]", force=True)
            pg.fill("#tForm input[name=date]", "2026-11-03")
            pg.fill("input[name=entry]", "7,203")
            pg.fill("input[name=stop]", "7,275")
            pg.fill("input[name=target]", "6,973")
            expect(pg.locator("#matchBanner")).to_contain_text("Схоже на сигнал скринера")
            check("ручна угода: помітний банер «Схоже на сигнал скринера — прив'язати?»", pg.locator("#matchBanner .btn").count() >= 1)
            pg.click("#matchBanner [data-match='0']")
            expect(pg.locator("#sigChosen")).to_contain_text("INJUSDT")
            expect(pg.locator("#matchBanner")).to_be_hidden()
            check("кнопка в банері прив'язує, банер зникає", True)
            pg.keyboard.press("Escape")
            check("помилок у консолі немає", not errors, errors[:3])
            b.close()
    finally:
        srv.terminate()

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"))
sys.exit(0 if ok else 1)
