#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.1.0: списки журналу — 4 розділи (Крипто/Форекс × Пейпер/Лайв), назви, «Завершити й почати новий», архів лише
для перегляду, повернення з архіву; свій № у кожному списку; «Підсумок» по вибраному списку.
Запуск: python3 tests/test_v210.py [папка_скріншотів]"""
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
import journal as J  # noqa: E402

shots = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
os.makedirs(shots, exist_ok=True)
ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


def trade(sym="BTC", d="Long", e=100.0, s=98.0, g=106.0, res=None):
    return {"date": "2026-11-10", "instrument": sym, "dir": d, "entry": e, "stop": s, "target": g, "result": res, "risk": 1.0}


# ================================================================ 1. перехід старої бази на списки
old = tempfile.mkdtemp(prefix="smc210_old_")
con = sqlite3.connect(os.path.join(old, "journal.db"))
con.executescript(J.SCHEMA.split("CREATE TABLE IF NOT EXISTS lists")[0])   # стара схема: лише trades, без list_id
for i, r in enumerate((1.0, -1.0, 3.0), 1):
    con.execute("INSERT INTO trades (n, date, instrument, dir, entry, stop, target, result, sheet_status) VALUES (?,?,?,?,?,?,?,?,?)",
                (i, "2026-11-0%d" % i, "SOL", "Long", 100, 98, 106, r, "ok"))
con.commit()
con.close()
j = J.Journal(old)
ls = j.lists()
check("стара база: створено «Перший список» у Крипто · Пейпер, усі угоди в ньому, він пишеться в таблицю",
      len(ls) == 1 and ls[0]["name"] == J.FIRST_LIST and (ls[0]["market"], ls[0]["mode"]) == ("crypto", "paper")
      and ls[0]["count"] == 3 and ls[0]["cum"] == 3.0 and ls[0]["sheet"] and ls[0]["status"] == "active", ls)
check("перед зміною структури — копія бази journal_before_lists_….db",
      any(n.startswith("journal_before_lists_") for n in os.listdir(os.path.join(old, "backup"))))
J.Journal(old)
check("повторне відкриття нічого не дублює", len(j.lists()) == 1 and len(j.rows()) == 3)
L1 = ls[0]["id"]
tl = j.list(L1)
check("№ у списку 1..3, накопичено як раніше", [t["ln"] for t in tl] == [1, 2, 3] and tl[-1]["cum"] == 3.0)

# ================================================================ 2. списки: створити, завершити, повернути
fx, ret = j.create_list("  EURUSD   тест ", "forex", "paper")
check("новий список у порожньому розділі; назву очищено від зайвих пробілів",
      ret is None and j.get_list(fx)["name"] == "EURUSD тест" and j.get_list(fx)["section"] == "Форекс · Пейпер")
n1 = j.insert(trade("EURUSD", e=1.085, s=1.082, g=1.095, res=3.0), fx)
check("угода форекс-списку: свій №1, у Google Таблицю не пишеться (sheet_status off)",
      j.list(fx)[0]["ln"] == 1 and j.get(n1)["sheet_status"] == "off" and j.summary(j.list(fx))["pending_sync"] == 0)
j.update(n1, {"note": "правка"})
check("правка угоди не робить її «не записаною в таблицю»", j.get(n1)["sheet_status"] == "off")
fx2, ret = j.create_list("Грудень", "forex", "paper")
check("«Завершити й почати новий»: старий список з угодами — в архів", ret and ret["id"] == fx and ret["how"] == "archived"
      and j.get_list(fx)["status"] == "archived" and j.get_list(fx)["archived_at"] and j.active_list("forex", "paper")["id"] == fx2)
fx3, ret = j.create_list("Січень", "forex", "paper")
check("порожній поточний список при заміні просто видаляється; його номер не використовується вдруге",
      ret["how"] == "deleted" and j.get_list(fx2) is None and fx3 > fx2, (fx2, fx3))
ret = j.unarchive_list(fx)
check("повернення з архіву: список знову поточний, порожній поточний — видалено",
      j.get_list(fx)["status"] == "active" and ret["how"] == "deleted" and j.get_list(fx3) is None)
check("в одному розділі — один поточний список",
      sum(1 for x in j.lists() if (x["market"], x["mode"]) == ("forex", "paper") and x["status"] == "active") == 1)
for bad, why in (("", "порожня"), ("x" * 61, "задовга")):
    try:
        j.rename_list(fx, bad)
        check(f"назва {why} — відхиляється", False)
    except ValueError:
        check(f"назва {why} — відхиляється", True)
try:
    j.create_list("Тест", "stocks", "paper")
    check("невідомий розділ — відхиляється", False)
except ValueError:
    check("невідомий розділ — відхиляється", True)
j.rename_list(fx, "EURUSD — перевірка")
check("перейменування", j.get_list(fx)["name"] == "EURUSD — перевірка")
check("наскрізний внутрішній № не змінюється, у списку — свій", n1 == 4 and j.last_n(fx) == 4 and j.last_n(L1) == 3)

# ================================================================ 3. CSV, перевірка, відновлення
rows = j.csv_rows(fx)
check("CSV списку: № у списку + розділ і назва списку", len(rows) == 1 and rows[0][0] == 1 and rows[0][-2:] == ["Форекс · Пейпер", "EURUSD — перевірка"]
      and J.Journal.CSV_HEAD[-2:] == ["Розділ", "Список"])
tf = trade("EURUSD", e=1.085, s=1.082, g=1.095)
check("форекс: без підказки «вузький стоп» (стопи в % там завжди малі); крипто — з підказкою",
      not any("вузький" in i["text"] for i in J.check_trade(tf, market="forex")["items"])
      and any("вузький" in i["text"] for i in J.check_trade(tf)["items"]))
j.manual_backup()
bk = sorted(n for n in os.listdir(os.path.join(old, "backup")) if n.startswith("journal_before_lists_"))[0]
j.restore(bk)
check("відновлення з копії до появи списків: структура оновлюється сама, угоди знову в «Першому списку»",
      [x["name"] for x in j.lists()] == [J.FIRST_LIST] and len(j.rows(j.lists()[0]["id"])) == 3)

# ================================================================ 4. сервер + інтерфейс
try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    print("Немає playwright: pip3 install --user playwright && python3 -m playwright install chromium")
    sys.exit(1)

PORT = 8772
URL = f"http://127.0.0.1:{PORT}"
data_dir = tempfile.mkdtemp(prefix="smc210_data_")
res_dir = tempfile.mkdtemp(prefix="smc210_res_")
os.makedirs(os.path.join(res_dir, "forex"))
now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
HEAD = "time,kind,symbol,dir,status,score,price,zone_low,zone_high,entry,stop,target,rr,dist,struct4h,against,bos_time,knife,warnings\n"
with open(os.path.join(res_dir, "forex", "signals_log.csv"), "w", encoding="utf-8") as f:
    f.write(HEAD + f"{now},у списку,EURUSD,Long,підхід до зони,3,1.09,1.084,1.086,1.085,1.082,1.095,3.3,1.0,вгору,ні,,ні,\n")
with open(os.path.join(res_dir, "signals_log.csv"), "w", encoding="utf-8") as f:
    f.write(HEAD + f"{now},у списку,BTCUSDT,Long,підхід до зони,3,101,99,101,100,98,106,3,1.0,вгору,ні,,ні,\n")
with open(os.path.join(data_dir, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"screener_path": os.path.join(HERE, "fake_screener.py"), "results_dir": res_dir, "sheet_sync": False,
               "backup_extra_dir": ""}, f)
ENV = dict(os.environ, SMC_APP_DATA=data_dir, SMC_TG_TOKEN="")


def api(path, body=None):
    req = urllib.request.Request(URL + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-SMC-App": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read()) if "json" in r.headers.get("Content-Type", "") else r
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


app = subprocess.Popen([sys.executable, os.path.join(APP, "app.py"), "--port", str(PORT), "--no-browser"], env=ENV,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
for _ in range(80):
    try:
        urllib.request.urlopen(URL + "/api/status", timeout=1)
        break
    except Exception:
        time.sleep(0.1)
try:
    c, d = api("/api/journal")
    check("нова база: одразу є «Перший список» (Крипто · Пейпер) і 4 розділи",
          d["list"]["name"] == J.FIRST_LIST and len(d["sections"]) == 4 and d["next_n"] == 1, d.get("list"))
    LC = d["list"]["id"]
    c, r = api("/api/journal/save", {"trade": trade("BTC", res=3.0), "list_id": LC})
    c, r2 = api("/api/journal/save", {"trade": trade("ETH", res=-1.0), "list_id": LC})
    check("збереження в список: № у списку 1, 2", r["ok"] and r["ln"] == 1 and r2["ln"] == 2, (r, r2))
    c, r = api("/api/journal/lists", {"action": "create", "name": "Форекс лайв", "market": "forex", "mode": "live"})
    LF = r["id"]
    c, s = api(f"/api/journal/signals?days=7&list={LF}")
    check("сигнали для форекс-списку — з логу форекс-скринера", [x["symbol"] for x in s["signals"]] == ["EURUSD"], s)
    c, s = api(f"/api/journal/signals?days=7&list={LC}")
    check("сигнали для крипто-списку — з логу крипто-скринера", [x["symbol"] for x in s["signals"]] == ["BTCUSDT"], s)
    c, r = api("/api/journal/save", {"trade": dict(trade("EURUSD", e=1.085, s=1.082, g=1.095, res=3.0),
                                                   signal_id="EURUSD|Long|1.084"), "list_id": LF})
    check("форекс-угода з прив'язкою до форекс-сигналу: №1 у своєму списку", c == 200 and r["ln"] == 1, r)
    c, d = api(f"/api/journal?list={LF}")
    check("журнал списку: лише його угоди", [t["instrument"] for t in d["trades"]] == ["EURUSD"] and d["list"]["section"] == "Форекс · Лайв")
    c, st = api(f"/api/stats?list={LC}")
    check("«Підсумок» по вибраному списку", st["general"]["n"] == 2 and st["general"]["sum"] == 2.0 and st["list"]["id"] == LC, st["general"])
    c, d = api(f"/api/journal?list={LC}")
    first = d["trades"][0]["n"]
    c, r = api("/api/journal/delete", {"n": first})
    check("видалити можна лише останню угоду списку", c == 400 and "останню" in r["error"], r)
    c, r = api("/api/journal/lists", {"action": "archive", "id": LF})
    check("«Завершити»: список з угодами — в архів", r["ok"] and r["how"] == "archived")
    c, d = api(f"/api/journal?list={LF}")
    nf = d["trades"][0]["n"]
    c, r = api("/api/journal/save", {"n": nf, "trade": trade("EURUSD", e=1.085, s=1.082, g=1.095, res=1.0)})
    check("архівний список: правка угоди відхиляється сервером", c == 400 and "в архіві" in r["errors"][0], r)
    c, r = api("/api/journal/save", {"trade": trade("EURUSD", e=1.085, s=1.082, g=1.095), "list_id": LF})
    check("архівний список: нову угоду додати не можна", c == 400 and "в архіві" in r["errors"][0], r)
    c, r = api("/api/journal/delete", {"n": nf})
    check("архівний список: видалити угоду не можна", c == 400 and "в архіві" in r["error"], r)
    c, r = api("/api/journal/lists", {"action": "unarchive", "id": LF})
    check("повернення з архіву", r["ok"] and api(f"/api/journal?list={LF}")[1]["list"]["status"] == "active")
    c, r = api("/api/journal/lists", {"action": "rename", "id": LF, "name": ""})
    check("порожня назва — 400 з поясненням", c == 400 and "назву" in r["error"], r)
    with urllib.request.urlopen(urllib.request.Request(URL + f"/api/journal/export.csv?list={LF}"), timeout=10) as resp:
        cd, body = resp.headers.get("Content-Disposition", ""), resp.read().decode("utf-8-sig")
    check("CSV списку: латинська назва файлу, лише угоди списку", f"journal_forex_live_{LF}_" in cd and len(body.strip().splitlines()) == 2
          and "Форекс лайв" in body, cd)

    errors = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        dialogs = []
        pg.on("dialog", lambda dlg: (dialogs.append(dlg.message), dlg.accept()))
        pg.goto(URL)
        pg.evaluate("localStorage.setItem('smc_jlist', '%d')" % LC)
        pg.reload()
        pg.click(".tab[data-tab=journal]")
        expect(pg.locator("#jSec .chip.on")).to_contain_text("Крипто · Пейпер")
        check("розділи журналу: 4 кнопки, поточний розділ підсвічено, біля поточних — к-сть угод",
              pg.locator("#jSec .chip").count() == 4 and "2" in pg.locator("#jSec .chip.on").inner_text())
        check("таблиця: № у списку", pg.locator("#jBody tr").first.locator("td").first.inner_text() == "2")
        pg.click("#jSec [data-sec='crypto|live']")
        expect(pg.locator("#jListForm")).to_be_visible()
        check("порожній розділ: форма «назва першого списку», кнопки нових угод вимкнені",
              "Крипто · Лайв" in pg.inner_text("#jFormLbl") and pg.locator("#jAddManual").is_disabled()
              and pg.locator("#jTbl").is_hidden())
        pg.fill("#jListName", "Листопад 2026")
        pg.click("#jListOk")
        expect(pg.locator("#jMsg")).to_contain_text("Почато новий список «Листопад 2026»")
        check("перший список розділу створено, він поточний", "Листопад 2026" in pg.locator("#jListSel option:checked").inner_text()
              and "поточний" in pg.inner_text("#jListTag") and pg.locator("#jAddManual").is_enabled())
        pg.click("#jAddManual")
        expect(pg.locator("#dTitle")).to_contain_text("Нова угода №1 вручну · «Листопад 2026» (Крипто · Лайв)")
        pg.click("#drawer .drawer-ft [data-close]")
        pg.click("#jSec [data-sec='crypto|paper']")
        expect(pg.locator("#jListSel option:checked")).to_contain_text(J.FIRST_LIST)
        pg.click("#jNewList")
        pg.fill("#jListName", "Грудень 2026")
        pg.click("#jListOk")
        expect(pg.locator("#jMsg")).to_contain_text("перенесено в архів")
        check("«Завершити й почати новий»: попередження перед дією, старий список — в архів",
              any("буде завершено" in m for m in dialogs) and pg.locator("#jListSel optgroup[label^='Архів'] option").count() == 1)
        pg.select_option("#jListSel", str(LC))
        expect(pg.locator("#jListTag")).to_contain_text("архів · лише перегляд")
        check("архівний список: нові угоди вимкнені, є «Повернути з архіву», немає «Завершити»",
              pg.locator("#jAddManual").is_disabled() and pg.locator("#jUnarchive").is_visible() and pg.locator("#jFinish").is_hidden())
        pg.locator("#jBody tr").first.click()
        expect(pg.locator("#dTitle")).to_contain_text("архів, лише перегляд")
        check("архівна угода: лише перегляд (немає «Зберегти» і «Видалити»)",
              pg.locator("#dSave").is_hidden() and pg.locator("#dDelete").is_hidden())
        pg.click("#drawer .drawer-ft [data-close]")
        pg.click(".tab[data-tab=summary]")
        expect(pg.locator("#sNote")).to_contain_text("«Перший список» (архів)")
        check("«Підсумок» показує той самий вибраний список (у т. ч. архівний)", pg.locator("#sKpi .stat b").first.inner_text() == "2")
        pg.select_option("#sList", str(LF))
        expect(pg.locator("#sNote")).to_contain_text("Форекс · Лайв")
        pg.click(".tab[data-tab=journal]")
        expect(pg.locator("#jSec .chip.on")).to_contain_text("Форекс · Лайв")
        check("вибір списку в «Підсумку» = вибір у журналі", True)
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.wait_for_timeout(300)
        wide = pg.evaluate("[...document.querySelectorAll('#jLists *')].filter(e => e.getBoundingClientRect().right > 392).map(e => e.tagName + '#' + e.id)")
        pg.screenshot(path=os.path.join(shots, "v210_narrow.png"))
        check("вузьке вікно: панель списків не вилазить за екран", not wide, wide)
        check("помилок JS немає", not errors, errors[:3])
        b.close()
finally:
    app.terminate()

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"), "| скріншоти:", shots)
sys.exit(0 if ok else 1)
