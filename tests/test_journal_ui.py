#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Автотест вкладки «Журнал» (Playwright + фейкова Google Таблиця). Запуск: python3 tests/test_journal_ui.py [папка_скріншотів]"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from fixtures import write_signals_log  # noqa: E402

PORT, SHEET_PORT, OLD_PORT = 8797, 8796, 8795
URL = f"http://127.0.0.1:{PORT}"
SHEET = f"http://127.0.0.1:{SHEET_PORT}"
shots = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
os.makedirs(shots, exist_ok=True)
data_dir = tempfile.mkdtemp(prefix="smcj_data_")
res_dir = tempfile.mkdtemp(prefix="smcj_res_")
write_signals_log(res_dir)
with open(os.path.join(data_dir, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"screener_path": os.path.join(HERE, "fake_screener.py"), "results_dir": res_dir, "sheet_sync": True}, f)
ENV = dict(os.environ, SMC_APP_DATA=data_dir, SMC_SHEET_URL=SHEET + "/exec", SMC_SHEET_TOKEN="test-token",
           NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")

ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


def wait_http(url):
    for _ in range(60):
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("не стартував " + url)


def start_sheet(port=SHEET_PORT, version=2):
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_sheet.py"), str(port), str(version)])
    wait_http(f"http://127.0.0.1:{port}/")
    return p


def sheet_state(port=SHEET_PORT):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/state", timeout=3) as r:
        return json.loads(r.read())


procs = []
sheet = start_sheet()
app = subprocess.Popen([sys.executable, os.path.join(APP, "app.py"), "--port", str(PORT), "--no-browser"], env=ENV,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
procs += [app]
wait_http(URL + "/api/status")
errors = []
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "40" not in m.text and "50" not in m.text else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        dialogs = []
        pg.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
        pg.goto(URL)
        pg.click(".tab[data-tab=journal]")
        expect(pg.locator("#jEmpty")).to_be_visible()
        expect(pg.locator("#jSheet")).to_contain_text("підключена (Code.gs v2)", timeout=10000)
        check("порожній журнал: кнопка імпорту, таблиця підключена (v2)", True)

        pg.click("#jImport2")
        expect(pg.locator("#jMsg")).to_contain_text("збігається з таблицею", timeout=10000)
        expect(pg.locator("#jBody tr")).to_have_count(5)
        check("імпорт: 5 угод, накопичене збігається з таблицею", pg.locator("#jBody tr").count() == 5)
        check("підсумок: накопичено 4,43R, серія 1", "4,43R" in pg.locator("#jStats").inner_text() and "Серія збитків\n1" in pg.locator("#jStats").inner_text())
        check("ризик 0,5R видно в рядку №5", "0,5R" in pg.locator("#jBody tr[data-n='5']").inner_text())

        # ---------- угода зі скринера
        pg.click("#jAddSignal")
        expect(pg.locator("#sigList .sig")).to_have_count(2, timeout=5000)
        check("список сигналів: DOGE і SUI (відсіяний ETH прихований)", "DOGE" in pg.locator("#sigList").inner_text() and "ETH" not in pg.locator("#sigList").inner_text())
        pg.check("#sigRejected")
        expect(pg.locator("#sigList .sig")).to_have_count(3)
        check("прапорець «відсіяні» показує ETH", "відсіяно" in pg.locator("#sigList").inner_text())
        pg.uncheck("#sigRejected")
        expect(pg.locator("#sigList .sig")).to_have_count(2)
        pg.click("#sigList .sig:has-text('DOGE')")
        expect(pg.locator("input[name=entry]")).to_have_value("0,07533")
        check("вибір сигналу підставляє інструмент, напрямок, рівні", pg.input_value("input[name=instrument]") == "DOGE"
              and pg.is_checked("input[name=dir][value=Short]") and pg.input_value("input[name=stop]") == "0,07577")
        expect(pg.locator("#checks")).to_contain_text("Вхід від середини зони")
        check("перевірка: вхід від середини, стоп за зоною", "Стоп за межею зони" in pg.locator("#checks").inner_text())
        check("«За правилами» = так (авто), «По скринеру» = так", pg.input_value("select[name=rules]") == "так" and pg.input_value("select[name=by_screener]") == "так")
        pg.screenshot(path=os.path.join(shots, "j2_form_signal.png"))
        pg.fill("input[name=entry]", "0,07521")
        expect(pg.locator("#checks")).to_contain_text("не від середини")
        check("вхід біля краю -> попередження і «за правилами» = ні", pg.input_value("select[name=rules]") == "ні")
        pg.screenshot(path=os.path.join(shots, "j3_form_warn.png"))
        pg.fill("input[name=entry]", "0,07533")
        expect(pg.locator("#checks")).to_contain_text("Вхід від середини зони")
        pg.fill("input[name=result]", "3,05")
        pg.fill("textarea[name=note]", "тестова угода")
        pg.click("#dSave")
        expect(pg.locator("#jMsg")).to_contain_text("записано в Google Таблицю", timeout=10000)
        st = sheet_state()
        row8 = st["rows"].get("8")
        check("угода №6 записана в рядок 8 таблиці (DOGE, по скринеру = так)", row8 and row8[2] == "DOGE" and row8[19] == "так" and row8[9] == 3.05, row8)
        expect(pg.locator("#jBody tr")).to_have_count(6)
        expect(pg.locator("#jStats")).to_contain_text("7,48R")
        check("у списку 6 угод, накопичено 7,48R", True)

        # ---------- редагування
        pg.click("#jBody tr[data-n='6']")
        expect(pg.locator("#dTitle")).to_contain_text("Угода №6")
        check("редагування: прив'язаний сигнал видно", pg.locator("#sigChosen").is_visible() and "DOGEUSDT" in pg.locator("#sigChosen").inner_text())
        pg.fill("textarea[name=note]", "виправлена нотатка")
        pg.click("#dSave")
        expect(pg.locator("#jMsg")).to_contain_text("оновлено", timeout=10000)
        check("виправлення переписує рядок у таблиці", sheet_state()["rows"]["8"][12] == "виправлена нотатка")

        expect(pg.locator("#jSheet")).to_contain_text("Онови Code.gs до v3")
        check("стара Code.gs v2: працює, але є підказка оновити до v3", True)

        # ---------- калькулятор результату й MFE (DOGE Short: вхід 0,07533, стоп 0,07577, ціль 0,07399)
        pg.click("#jBody tr[data-n='6']")
        pg.click(".qb[data-res=stop]")
        check("кнопка «стоп» -> результат −1,00", pg.input_value("input[name=result]") == "-1,00")
        pg.click(".qb[data-res=target]")
        check("кнопка «ціль» -> результат = R:R (3,05)", pg.input_value("input[name=result]") == "3,05")
        pg.fill("#exitPx", "0,07400")
        check("ціна виходу 0,07400 -> результат 3,02", pg.input_value("input[name=result]") == "3,02")
        pg.fill("#mfePx", "0,0722")
        check("найкраща ціна 0,0722 -> MFE 7,11", pg.input_value("input[name=mfe]") == "7,11")
        pg.check("input[name=risk][value='0.5']", force=True)
        pg.click(".qb[data-res=stop]")
        check("ризик 0,5R + «стоп» -> результат −0,50", pg.input_value("input[name=result]") == "-0,50")
        pg.keyboard.press("Escape")
        expect(pg.locator("#drawer")).to_be_hidden()

        # ---------- ручна угода з підказкою сигналу
        pg.click("#jAddManual")
        check("ручна форма без списку сигналів", not pg.locator("#sigBox").is_visible())
        pg.fill("input[name=instrument]", "SUIUSDT")
        pg.check("input[name=dir][value=Short]", force=True)
        pg.fill("input[name=entry]", "0,90325")
        pg.fill("input[name=stop]", "0,9104")
        pg.fill("input[name=target]", "0,8722")
        expect(pg.locator("#matchBanner")).to_contain_text("SUIUSDT", timeout=5000)
        check("ручна угода: знайдено схожий сигнал скринера", True)
        check("до прив'язки «По скринеру» = ні (авто)", pg.input_value("select[name=by_screener]") == "ні")
        pg.click("#matchBanner [data-match='0']")
        expect(pg.locator("#sigChosen")).to_contain_text("SUIUSDT")
        expect(pg.locator("#checks")).to_contain_text("ніж")
        check("після прив'язки «По скринеру» = так, видно попередження «ніж»", pg.input_value("select[name=by_screener]") == "так")
        pg.click("#dSave")
        expect(pg.locator("#jMsg")).to_contain_text("№7", timeout=10000)
        expect(pg.locator("#jBody tr")).to_have_count(7)
        check("ручна угода №7 збережена (відкрита) і записана як «SUI» (без USDT)", pg.locator("#jBody tr").count() == 7 and sheet_state()["rows"]["9"][2] == "SUI")

        # ---------- помилка: порожні поля
        pg.click("#jAddManual")
        pg.click("#dSave")
        expect(pg.locator("#dErr")).to_be_visible()
        check("без обов'язкових полів — зрозумілі помилки, нічого не збережено", "Інструмент" in pg.locator("#dErr").inner_text() and pg.locator("#jBody tr").count() == 7)
        pg.keyboard.press("Escape")

        # ---------- видалення останньої
        pg.click("#jBody tr[data-n='7']")
        check("кнопка «Видалити» лише в останньої угоди", pg.locator("#dDelete").is_visible())
        pg.click("#dDelete")
        expect(pg.locator("#jMsg")).to_contain_text("видалено", timeout=10000)
        expect(pg.locator("#jBody tr")).to_have_count(6)
        check("№7 видалено в додатку й у таблиці", pg.locator("#jBody tr").count() == 6 and "9" not in sheet_state()["rows"])
        pg.click("#jBody tr[data-n='5']")
        check("в не останньої угоди кнопки «Видалити» немає", not pg.locator("#dDelete").is_visible())
        pg.keyboard.press("Escape")

        # ---------- таблиця недоступна -> зберігається локально, потім «Дописати»
        urllib.request.urlopen(SHEET + "/toggle?down=1", timeout=3)
        pg.click("#jAddManual")
        pg.fill("input[name=instrument]", "PENG")
        pg.check("input[name=dir][value=Short]", force=True)
        pg.fill("input[name=entry]", "54,83")
        pg.fill("input[name=stop]", "55,74")
        pg.fill("input[name=target]", "53,05")
        pg.fill("input[name=result]", "1,96")
        try:
            expect(pg.locator("#checks")).to_contain_text("R:R план 1,96 < 3")
        except AssertionError:
            print("DEBUG checks:", pg.locator("#checks").inner_text(), "| dir:", pg.evaluate("(document.querySelector('[name=dir]:checked')||{}).value"),
                  "| fields:", [pg.input_value(f"input[name={k}]") for k in ("instrument", "entry", "stop", "target", "result")])
            pg.screenshot(path=os.path.join(shots, "debug_fail.png"))
            raise
        check("R:R < 3 попереджено (урок №20)", True)
        pg.click("#dSave")
        expect(pg.locator("#jMsg")).to_contain_text("не записано", timeout=15000)
        expect(pg.locator("#jBody tr[data-n='7'] .warnc")).to_have_count(1)
        check("без зв'язку з таблицею угода збережена в додатку з позначкою «!»", pg.locator("#jBody tr[data-n='7'] .warnc").count() == 1)
        urllib.request.urlopen(SHEET + "/toggle?down=0", timeout=3)
        pg.click(".tab[data-tab=screener]"); pg.click(".tab[data-tab=journal]")
        expect(pg.locator("#jSync")).to_be_visible(timeout=10000)
        pg.click("#jSync")
        expect(pg.locator("#jMsg")).to_contain_text("Дописано", timeout=15000)
        expect(pg.locator("#jBody .warnc")).to_have_count(0)
        st = sheet_state()
        check("«Дописати» записує пропущену угоду №7 у рядок 9", st["rows"].get("8", [0, 0, ""])[2] == "DOGE" and st["rows"].get("9", [0, 0, ""])[2] == "PENG", list(st["rows"]))
        check("після дописування позначок «!» немає", pg.locator("#jBody .warnc").count() == 0)
        pg.screenshot(path=os.path.join(shots, "j1_journal.png"))

        # ---------- запис у таблицю вимкнено (як у Michel): усе живе в додатку
        pg.click(".tab[data-tab=settings]")
        expect(pg.locator("#cmdPreview")).to_contain_text("--watch")  # форма налаштувань уже заповнена
        expect(pg.locator("#envList")).to_contain_text("скринер")
        pg.uncheck("input[name=sheet_sync]")
        extra = tempfile.mkdtemp(prefix="smcj_extra_")
        pg.fill("input[name=backup_extra_dir]", extra)
        pg.click("#setForm button[type=submit]")
        expect(pg.locator("#setMsg")).to_contain_text("Збережено")
        pg.click(".tab[data-tab=journal]")
        expect(pg.locator("#jSheet")).to_contain_text("зберігається в додатку")
        check("без запису в таблицю: колонки «Табл.» не видно", not pg.locator("#jTbl th:nth-child(18)").is_visible())
        calls_before = len(sheet_state()["calls"])
        pg.click("#jAddManual")
        pg.fill("input[name=instrument]", "WLDUSDT")
        pg.check("input[name=dir][value=Short]", force=True)
        pg.fill("input[name=entry]", "0,398")
        pg.fill("input[name=stop]", "0,403")
        pg.fill("input[name=target]", "0,3817")
        pg.click(".qb[data-res=target]")
        pg.fill("#mfePx", "0,368")
        check("WLD: «ціль» -> +3,26R, MFE з ціни 0,368 -> 6,00", pg.input_value("input[name=result]") == "3,26" and pg.input_value("input[name=mfe]") == "6,00")
        pg.click("#dSave")
        expect(pg.locator("#jMsg")).to_contain_text("Угоду №8 збережено ✓", timeout=10000)
        expect(pg.locator("#jBody tr")).to_have_count(8)
        check("угоду збережено як «WLD», у таблицю нічого не надсилалось", "WLD" in pg.locator("#jBody tr[data-n='8']").inner_text()
              and len(sheet_state()["calls"]) == calls_before)

        # ---------- резервні копії
        pg.click("#bkBox summary")
        expect(pg.locator("#bkBody")).to_contain_text("щоденна")
        check("щоденна копія (стан до першої зміни) і актуальна є", "актуальна" in pg.locator("#bkBody").inner_text())
        pg.click("#bkNow")
        expect(pg.locator("#bkBody")).to_contain_text("ручна")
        check("«Зробити копію зараз»: ручна копія з 8 угодами", "8" in pg.locator("#bkBody tr:has-text('ручна')").first.inner_text())
        check("копії продубльовано в другу папку", any(n.startswith("journal_manual_") for n in os.listdir(extra))
              and os.path.exists(os.path.join(extra, "journal_latest.db")))
        pg.click("#jBody tr[data-n='8']")
        pg.click("#dDelete")
        expect(pg.locator("#jBody tr")).to_have_count(7, timeout=10000)
        pg.click("#bkBody tr:has-text('ручна') [data-restore]")
        expect(pg.locator("#jMsg")).to_contain_text("Журнал відновлено", timeout=10000)
        expect(pg.locator("#jBody tr")).to_have_count(8)
        check("відновлення з ручної копії повертає видалену угоду №8", True)
        expect(pg.locator("#bkBody")).to_contain_text("перед відновленням")
        check("стан перед відновленням теж збережено", True)
        r = pg.request.get(URL + "/api/journal/backup_file?name=../journal.db")
        check("завантажити можна лише файли копій (не довільні шляхи)", r.status == 404)
        pg.screenshot(path=os.path.join(shots, "j4_backups.png"), full_page=True)

        # ---------- CSV
        r = pg.request.get(URL + "/api/journal/export.csv")
        body = r.text()
        check("CSV: завантажується, 8 угод + заголовок", r.ok and body.count("\n") == 9 and "Накопичено (R)" in body, body[:120])
        check("резервна копія на диску", os.path.exists(os.path.join(data_dir, "backup", "journal_latest.csv")))
        b.close()

    # ---------- стара Code.gs (v1): додаток не надсилає жодного POST (інакше стара версія очистила б «Скринер»)
    old = start_sheet(OLD_PORT, 1)
    procs.append(old)
    code = ("import journal, json; print(json.dumps(journal.sheet_status(force=True), ensure_ascii=False));"
            "\ntry:\n    journal.sheet_call('listTrades')\nexcept journal.SheetError as e:\n    print('ERR', e)")
    out = subprocess.run([sys.executable, "-c", code], cwd=APP, capture_output=True, text=True,
                         env=dict(ENV, SMC_SHEET_URL=f"http://127.0.0.1:{OLD_PORT}/exec")).stdout
    check("стара Code.gs: зрозуміла помилка «онови Code.gs»", "стара версія Code.gs" in out, out)
    check("стара Code.gs: додаток не зробив жодного POST", sheet_state(OLD_PORT)["calls"] == [], sheet_state(OLD_PORT)["calls"])
    check("помилок у консолі браузера немає", not errors, errors[:3])
finally:
    for p in procs + [sheet]:
        p.terminate()
        try:
            p.wait(5)
        except Exception:
            p.kill()

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"), "| скріншоти:", shots)
sys.exit(0 if ok else 1)
