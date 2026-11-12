#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Вкладка «Історія». Запуск: python3 tests/test_history.py [папка з копією справжніх data/journal.db] [папка скріншотів]"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
REAL = sys.argv[1] if len(sys.argv) > 1 else None
shots = sys.argv[2] if len(sys.argv) > 2 else tempfile.mkdtemp()
os.makedirs(shots, exist_ok=True)
PORT = 8787
URL = f"http://127.0.0.1:{PORT}"
ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


data = tempfile.mkdtemp(prefix="smch_")
if REAL and os.path.exists(os.path.join(REAL, "data", "journal.db")):
    shutil.copy(os.path.join(REAL, "data", "journal.db"), data)
else:
    from fixtures import seed_real_journal
    seed_real_journal(data)
extra = tempfile.mkdtemp()
with open(os.path.join(data, "settings.json"), "w") as f:
    json.dump({"screener_path": "x", "results_dir": tempfile.mkdtemp(), "backup_extra_dir": extra}, f)
app = subprocess.Popen([sys.executable, os.path.join(APP, "app.py"), "--port", str(PORT), "--no-browser"],
                       env=dict(os.environ, SMC_APP_DATA=data), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(60):
    try:
        urllib.request.urlopen(URL + "/api/status", timeout=1)
        break
    except Exception:
        time.sleep(0.1)


def post(path, body):
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode(), method="POST", headers={"X-SMC-App": "1"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


try:
    st = json.loads(urllib.request.urlopen(URL + "/api/changelog").read())
    n0 = len(st["entries"])
    check("перший запуск: історія з changelog.md (60 записів) у data/changelog.json", n0 == 60 and os.path.exists(os.path.join(data, "changelog.json")))
    c, r = post("/api/changelog", {"entries": st["entries"] + [{"title": "", "component": "Щось"}], "base_mtime": st["mtime"]})
    check("сервер відхиляє запис без назви й з невідомим компонентом", c == 400 and len(r["errors"]) == 2, r)
    c, r = post("/api/changelog", {"entries": st["entries"][:1], "base_mtime": 1.0})
    check("захист від затирання змін з іншого вікна (409)", c == 409)

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("dialog", lambda d: d.accept())
        pg.goto(URL)
        pg.click(".tab[data-tab=history]")
        expect(pg.locator("#hList .t-item")).to_have_count(60)
        days = pg.locator("#hList .t-day").all_inner_texts()
        check("хронологія: новіші вгорі, «Дата невідома» внизу, «≈» для приблизних",
              days[0].endswith("листопада 2026") and days[-1] == "Дата невідома" and any(d.startswith("≈") for d in days), days)
        check("компоненти з лічильниками", "Скринер 12" in pg.locator("#hComp").inner_text().replace("\n", " "), pg.locator("#hComp").inner_text())
        pg.click("#hComp [data-c='Скринер']")
        check("фільтр «Скринер»: 12 записів", pg.locator("#hList .t-item").count() == 12)
        pg.click("#hComp [data-c='all']")
        pg.fill("#hSearch", "16")
        check("пошук «16» знаходить записи з угодою №16 (тісний стоп, прив'язка)", pg.locator("#hList .t-item").count() == 2
              and "тісний стоп" in pg.locator("#hList").inner_text())
        pg.fill("#hSearch", "")
        item = pg.locator("#hList .t-item[data-id=e010]")
        check("запис v2.5: «Чому» з угодою-приводом №16, код у форматі", "Чому:" in item.inner_text() and "№16 INJ" in item.inner_text()
              and item.locator("code").count() >= 1)
        pg.screenshot(path=os.path.join(shots, "h1_history.png"))
        pg.click("#hList .t-item[data-id=e010] [data-trade='16']")
        expect(pg.locator("#drawer")).to_be_visible()
        expect(pg.locator("#dTitle")).to_contain_text("Угода №16")
        check("клік по №16 відкриває угоду в журналі", True)
        pg.keyboard.press("Escape")
        pg.click(".tab[data-tab=history]")
        pg.click("#hAdd")
        pg.fill("#hForm input[name=title]", "Тестовий запис: бектест етап (a)")
        pg.select_option("#hForm select[name=component]", "Інструменти")
        pg.fill("#hForm input[name=version]", "bt-0.1")
        pg.fill("#hForm textarea[name=why]", "Перевірка правил на історії, див. **бриф**")
        pg.fill("#hForm input[name=trades]", "21, 22")
        pg.click("#hForm button[type=submit]")
        expect(pg.locator("#hMsg")).to_contain_text("Запис додано")
        expect(pg.locator("#hList .t-item")).to_have_count(61)
        new = pg.locator("#hList .t-item:has-text('бектест етап')")
        check("новий запис: сьогоднішня дата вгорі, жирний текст, угоди №21 №22", new.locator("b").count() >= 2
              and new.locator("[data-trade]").count() == 2)
        check("копія історії в другій папці", os.path.exists(os.path.join(extra, "changelog.json")))
        new.locator("[data-edit]").click()
        pg.fill("#hForm textarea[name=result]", "Етап пройдено")
        pg.click("#hForm button[type=submit]")
        expect(pg.locator("#hMsg")).to_contain_text("оновлено")
        check("редагування: результат з'явився", "Етап пройдено" in pg.locator("#hList .t-item:has-text('бектест етап')").inner_text())
        pg.locator("#hList .t-item:has-text('бектест етап') [data-edit]").click()
        pg.click("#hDelete")
        expect(pg.locator("#hList .t-item")).to_have_count(60)
        hist = os.listdir(os.path.join(data, "changelog_history"))
        check("видалення; кожна попередня версія збережена в changelog_history", len(hist) == 3, hist)
        pg.fill("#hSearch", "<script>")
        check("пошук зі спецсимволами не ламає сторінку", True)
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.wait_for_timeout(300)
        pg.fill("#hSearch", "")
        sw = pg.evaluate("document.documentElement.scrollWidth")
        wide = pg.evaluate("""[...document.querySelectorAll('#tab-history *')].filter(e => e.getBoundingClientRect().right > 392).slice(0, 4).map(e => e.tagName + '.' + e.className + '#' + e.id)""")
        check("вузьке вікно без горизонтальної прокрутки", sw <= 392, (sw, wide))
        check("помилок у консолі немає", not errors, errors[:3])
        b.close()
finally:
    app.terminate()

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"), "| скріншоти:", shots)
sys.exit(0 if ok else 1)
