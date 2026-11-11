#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Автотест інтерфейсу (Playwright + Chromium) на фейковому скринері, без мережі Binance.
Запуск:  python3 tests/test_ui.py [папка_для_скріншотів]"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fake_binance as FB  # noqa: E402  — свічки 1h/15m для лайв-графіків без мережі
FB.serve(8798)
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
APP = os.path.dirname(HERE)
PORT = 8799
URL = f"http://127.0.0.1:{PORT}"
shots = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
data_dir = tempfile.mkdtemp(prefix="smcapp_data_")
res_dir = tempfile.mkdtemp(prefix="smcapp_results_")
os.makedirs(shots, exist_ok=True)
with open(os.path.join(data_dir, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"screener_path": os.path.join(HERE, "fake_screener.py"), "results_dir": res_dir, "watch": 1,
               "popup": False, "voice": False, "tv_button": False}, f)

ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


def start_app():
    env = dict(os.environ, SMC_APP_DATA=data_dir, SMC_BT_FAPI="http://127.0.0.1:8798")
    p = subprocess.Popen([sys.executable, os.path.join(APP, "app.py"), "--port", str(PORT), "--no-browser"],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(50):
        try:
            urllib.request.urlopen(URL + "/api/status", timeout=1)
            return p
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("додаток не стартував")


def post(path, headers=None):
    req = urllib.request.Request(URL + path, data=b"{}", method="POST", headers=headers or {})
    try:
        return urllib.request.urlopen(req, timeout=5).status
    except urllib.error.HTTPError as e:
        return e.code


app = start_app()
errors = []
try:
    check("POST без заголовка X-SMC-App відхиляється (403)", post("/api/start") == 403)
    check("POST з чужим Host відхиляється (403)", post("/api/start", {"X-SMC-App": "1", "Host": "evil.example"}) == 403)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        pg.add_init_script("window.SMC_NO_WS = true;")
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "400 (Bad Request)" not in m.text else None)  # 400 — навмисні перевірки валідації
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        pg.goto(URL)
        expect(pg.locator("#pillText")).to_have_text("Зупинено")
        expect(pg.locator("#staleNote")).to_contain_text("Ще немає результатів")
        check("старт: статус «Зупинено», результатів ще немає", True)

        pg.click("#btnRun")
        expect(pg.locator("#pillText")).to_have_text("Працює", timeout=10000)
        expect(pg.locator("#tbody tr")).to_have_count(6, timeout=15000)
        check("після «Старт»: скринер працює, у таблиці 6 кандидатів", True)
        check("рядок «ціна в зоні» червоний (1 шт.)", pg.locator("#tbody tr.s0").count() == 1)
        check("банер «Ціна вже в зоні» видно", pg.locator("#zoneAlert").is_visible())
        check("назва вкладки з 🟢", pg.title().startswith("🟢 В ЗОНІ: SOL"))
        expect(pg.locator("#statusLine")).to_contain_text("останній скан", timeout=10000)
        check("рядок статусу показує останній скан", True)
        pg.wait_for_selector("#ch0 canvas", timeout=10000)
        pg.wait_for_timeout(600)
        check("графік першого кандидата намальовано (canvas)", pg.locator("#ch0 canvas").count() > 0)
        check("на графіку є зона/ризик/ціль (оверлеї)", pg.locator("#ch0 > div[style*='position: absolute']").count() >= 4)
        check("latest_app.json записано перехоплювачем", os.path.exists(os.path.join(res_dir, "latest_app.json")))
        pg.screenshot(path=os.path.join(shots, "1_screener.png"), full_page=False)

        n_t = pg.locator("#tiles .tile").count()
        scores = pg.eval_on_selector_all("#tiles .tile", "els => els.map(e => e.classList.contains('hot'))")
        check("за замовчуванням — блоки (6 шт.), таблиця прихована", n_t == 6 and pg.locator("#tblWrap").is_hidden())
        check("бали ≥ 2: рамка й ★ (позначка, а не місце у списку)", any(scores) and pg.locator("#tiles .tile.hot .t-hot").count() == sum(scores), scores)
        secs = pg.eval_on_selector_all("#tiles .tsec", "els => els.map(e => [e.dataset.g, e.querySelectorAll('.tile').length, e.querySelectorAll('.tile.s0').length])")
        check("секції: «В зоні» першою (лише блоки в зоні), далі «Нові»; порожніх секцій немає",
              [x[0] for x in secs] == ["0", "1"] and secs[0][1] == secs[0][2] >= 1 and secs[1][2] == 0 and secs[0][1] + secs[1][1] == 6, secs)
        pg.click("#filterChips [data-f=hot]")
        check("фільтр ★: лише сильні сетапи, лічильник збігається",
              pg.locator("#tiles .tile").count() == sum(scores) == int(pg.inner_text("#cHot")) and pg.locator("#tiles .tile:not(.hot)").count() == 0)
        pg.click("#filterChips [data-f=all]")
        check("BTC спокійний → банера про біткоїн немає", pg.locator("#btcAlert").is_hidden())
        pg.evaluate("() => { S.rows.reverse(); renderAll(); }")      # порядок у списку ≠ номер сигналу (як після сортування секціями)
        pg.wait_for_timeout(2000)
        mm = pg.evaluate("""() => [...LV.mounts.entries()].map(([i, m]) => [i, m.symbol, m.r.dir,
            (document.querySelector('#c' + i + ' .hd') || {}).textContent || ''])""")
        check("графіки в картці — саме тієї монети й напряму, що в заголовку (навіть коли список пересортовано)",
              len(mm) >= 1 and all(m[1] in m[3] and m[2] in m[3] for m in mm), [m[:3] + [m[3][:40]] for m in mm])
        pg.evaluate("() => { S.rows.reverse(); renderAll(); }")
        pg.wait_for_timeout(800)
        pg.click("#tiles .tile[data-i='3'] .t-st")
        pg.wait_for_timeout(900)
        check("клік по блоку прокручує до його графіка", pg.locator("#c3").is_visible()
              and pg.evaluate("document.getElementById('c3').getBoundingClientRect().top") < 300)
        pg.click("#viewChips [data-v=table]")
        check("перемикач «Таблиця»: таблиця видна, блоки сховані", pg.locator("#tblWrap").is_visible() and pg.locator("#tiles").is_hidden())
        pg.click("#tbody tr[data-i='3'] td:nth-child(4)")
        pg.wait_for_timeout(900)
        check("клік по рядку прокручує до його графіка", pg.locator("#c3").is_visible() and pg.evaluate("document.getElementById('c3').getBoundingClientRect().top") < 300)
        pg.wait_for_selector("#ch3 canvas", timeout=5000)
        pg.screenshot(path=os.path.join(shots, "2_chart.png"), full_page=False)

        r = pg.request.post(URL + "/api/scan_now", headers={"X-SMC-App": "1"}, data="{}")
        check("«Сканувати зараз»: скринер отримує запит (файл-прапорець)", r.ok and os.path.exists(os.path.join(res_dir, "scan_now.flag")))
        pg.click("#filterChips [data-f=zone]")
        check("фільтр «У зоні»: 1 рядок і 1 графік", pg.locator("#tbody tr").count() == 1 and pg.locator("#charts .card").count() == 1)
        pg.click("#filterChips [data-f=all]")
        pg.click("#dirChips [data-d=Short]")
        check("фільтр Short: 3 рядки", pg.locator("#tbody tr").count() == 3)
        pg.click("#dirChips [data-d=all]")
        pg.fill("#search", "doge")
        check("пошук «doge»: 1 рядок", pg.locator("#tbody tr").count() == 1)
        pg.fill("#search", "")

        pg.click("#logBox summary")
        expect(pg.locator("#log")).to_contain_text("у списку: 6", timeout=8000)
        check("лог скринера показується", True)

        pg.click(".tab[data-tab=settings]")
        expect(pg.locator("#cmdPreview")).to_contain_text("--watch 1")
        check("мін. обсяг торгів: за замовчуванням 30 млн $ у списку й у команді",
              pg.input_value("select[name=min_volume]") == "30" and "--min-volume 30" in pg.locator("#cmdPreview").inner_text())
        pg.fill("input[name=approach]", "5")
        pg.select_option("select[name=min_volume]", "50")
        pg.click("#setForm button[type=submit]")
        expect(pg.locator("#setMsg")).to_contain_text("Збережено")
        check("налаштування зберігаються, команда оновлюється", "--approach 5" in pg.locator("#cmdPreview").inner_text())
        check("вибір обсягу 50 млн $ → --min-volume 50", "--min-volume 50" in pg.locator("#cmdPreview").inner_text()
              and pg.input_value("select[name=min_volume]") == "50")
        st = json.load(urllib.request.urlopen(URL + "/api/status", timeout=5))
        check("«Зберегти й застосувати»: скринер сам перезапустився з новими параметрами (без «Стоп» → «Старт»)",
              "перезапущено" in pg.locator("#setMsg").inner_text() and st["running"] and "--min-volume 50" in (st["command"] or "")
              and "--approach 5" in (st["command"] or ""), (pg.locator("#setMsg").inner_text(), st.get("command")))
        pid1 = st["pid"]
        pg.click("input[name=spot_auto]")
        pg.click("#setForm button[type=submit]")
        expect(pg.locator("#setMsg")).to_contain_text("працює далі", timeout=10000)
        check("зміна, що не стосується скринера, — без перезапуску", json.load(urllib.request.urlopen(URL + "/api/status", timeout=5))["pid"] == pid1)
        pg.click("input[name=spot_auto]")
        pg.click("#setForm button[type=submit]")
        expect(pg.locator("#setMsg")).to_contain_text("працює далі", timeout=10000)
        bad = pg.evaluate("""fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-SMC-App': '1'},
                             body: JSON.stringify({settings: {watch: 0, top: 'abc'}})}).then(r => r.json())""")
        check("сервер відхиляє некоректні значення з поясненням", not bad["ok"] and len(bad["errors"]) == 2)
        check("у формі браузер теж не дасть зберегти 0 хв", pg.evaluate("(() => { const i = document.querySelector('input[name=watch]'); i.value = '0'; const v = i.checkValidity(); i.value = '1'; return v; })()") is False)
        pg.fill("input[name=extra_args]", "--sheet-token abc")
        pg.click("#setForm button[type=submit]")
        expect(pg.locator("#setMsg")).to_contain_text("змінними середовища")
        check("токен таблиці не можна вписати в параметри", True)
        pg.fill("input[name=extra_args]", "")
        pg.click("#setForm button[type=submit]")
        pg.click("#btnSelftest")
        expect(pg.locator("#selftestOut")).to_contain_text("Усі перевірки пройдено", timeout=20000)
        check("кнопка selftest працює", True)
        pg.screenshot(path=os.path.join(shots, "3_settings.png"), full_page=True)

        # перезапуск додатка: скринер має працювати далі й підхопитися
        app.terminate(); app.wait(5)
        app = start_app()
        pg.click(".tab[data-tab=screener]")
        pg.reload()
        expect(pg.locator("#pillText")).to_have_text("Працює", timeout=8000)
        check("після перезапуску додатка скринер підхоплено (працює далі)", True)

        pg.click("#btnRun")
        expect(pg.locator("#pillText")).to_have_text("Зупинено", timeout=20000)
        expect(pg.locator("#staleNote")).to_contain_text("останні збережені результати")
        check("«Стоп» зупиняє скринер, результати лишаються з позначкою", True)
        pg.click("#logBox summary")
        expect(pg.locator("#log")).to_contain_text("Зупинено.", timeout=8000)
        check("скринер завершився чисто (як Ctrl+C)", True)
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.wait_for_timeout(400)
        pg.screenshot(path=os.path.join(shots, "4_narrow.png"), full_page=False)
        sw = pg.evaluate("document.documentElement.scrollWidth")
        check("вузьке вікно без горизонтальної прокрутки сторінки", sw <= 392)
        pg.set_viewport_size({"width": 1440, "height": 1000})
        r = pg.request.post(URL + "/api/scan_now", headers={"X-SMC-App": "1"}, data="{}")
        check("«Сканувати зараз», коли скринер зупинено, — пояснення «натисни Старт»", r.status == 409 and "Старт" in r.json()["message"])
        pg.click("#btnClear")   # підтвердження приймає загальний обробник діалогів
        expect(pg.locator("#tiles .tile")).to_have_count(0, timeout=8000)
        check("«Очистити список»: блоків і графіків немає, у файлі порожньо",
              json.load(open(os.path.join(res_dir, "latest_app.json")))["candidates"] == [] and pg.locator("#charts .card").count() == 0)
        b.close()
    check("помилок у консолі браузера немає", not errors)
    if errors:
        print("  ", errors[:5])
finally:
    app.terminate()
    try:
        app.wait(5)
    except Exception:
        app.kill()
    # прибрати фейковий скринер, якщо тест упав посередині
    try:
        st = json.load(open(os.path.join(data_dir, "state.json")))
        if st.get("pid"):
            os.killpg(st["pid"], 9)
    except Exception:
        pass

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"), "| скріншоти:", shots)
sys.exit(0 if ok else 1)
