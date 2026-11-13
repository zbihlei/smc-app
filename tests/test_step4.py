#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Етап 4: вкладка «Стратегія», збирач Rulebound.app, лаунчер (зі стабами open/osascript) і «Закрити додаток».
Запуск: python3 tests/test_step4.py [папка_скріншотів]   (macOS не потрібен)"""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
shots = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
os.makedirs(shots, exist_ok=True)
ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


def up(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1)
        return True
    except Exception:
        return False


def wait(cond, sec=15):
    end = time.time() + sec
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


def post(port, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"X-SMC-App": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ================================================================ 1. стратегія: API
PORT = 8792
data_dir = tempfile.mkdtemp(prefix="smc4_")
extra = tempfile.mkdtemp(prefix="smc4_extra_")
with open(os.path.join(data_dir, "settings.json"), "w") as f:
    json.dump({"screener_path": "x", "results_dir": tempfile.mkdtemp(), "backup_extra_dir": extra}, f)
app = subprocess.Popen([sys.executable, os.path.join(APP, "app.py"), "--port", str(PORT), "--no-browser"],
                       env=dict(os.environ, SMC_APP_DATA=data_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
wait(lambda: up(PORT))
try:
    c, st = get(PORT, "/api/strategy")
    check("стратегія: перший запуск бере Strategy_v0.5.1 з Google Doc", c == 200 and st["title"] == "v0.5.1"
          and "Правила входу" in st["text"] and os.path.exists(os.path.join(data_dir, "strategy.md")))
    c, r = post(PORT, "/api/strategy", {"text": st["text"] + "\n## Нове правило\n\n- тест\n", "base_mtime": st["mtime"]})
    check("збереження: стара версія йде в історію, копія в другу папку", c == 200 and len(r["versions"]) == 1
          and os.path.exists(os.path.join(extra, "strategy.md")), r.get("error"))
    c, r2 = post(PORT, "/api/strategy", {"text": "застаріле", "base_mtime": st["mtime"]})
    check("захист від затирання змін з іншого вікна (409)", c == 409)
    c, v = get(PORT, "/api/strategy/version?name=" + r["versions"][0]["name"])
    check("стару версію можна прочитати", c == 200 and v["text"] == st["text"])
    c, _ = get(PORT, "/api/strategy/version?name=../settings.json")
    check("довільні файли через «версії» не читаються", c == 404)

    # ================================================================ 2. стратегія: інтерфейс
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("dialog", lambda d: d.accept())
        pg.goto(f"http://127.0.0.1:{PORT}")
        pg.click(".tab[data-tab=strategy]")
        expect(pg.locator("#stView h1")).to_have_text("v0.5.1")
        check("документ відмальовано: заголовки, списки, жирний", pg.locator("#stView h2").count() >= 6
              and pg.locator("#stView ul li").count() >= 15 and pg.locator("#stView b").count() >= 10)
        check("зміст збоку з посиланнями на розділи", pg.locator("#stToc a").count() >= 10)
        pg.click("#stToc a:has-text('Ризик')")
        pg.wait_for_timeout(700)
        check("клік у змісті прокручує до розділу", pg.evaluate("document.querySelector('#stView h2[id*=\"ризик\"]').getBoundingClientRect().top") < 300)
        pg.screenshot(path=os.path.join(shots, "st1_view.png"))
        pg.click("#stEdit")
        txt = pg.input_value("#stText")
        check("редактор відкривається з поточним текстом", "## Нове правило" in txt)
        pg.fill("#stText", txt + "\n## Чек-ліст перед ордером\n\n1. R:R ≥ 3\n2. Стоп **за зоною**\n  - вкладений пункт\n")
        pg.keyboard.press("Control+s")
        expect(pg.locator("#stMsg")).to_contain_text("Збережено")
        check("Ctrl/⌘+S зберігає; нумерований і вкладений список відмальовано",
              pg.locator("#stView ol li").count() >= 2 and pg.locator("#stView ol ul li").count() == 1)
        pg.click("#stHistBtn")
        expect(pg.locator("#stHist [data-ver]")).to_have_count(2)
        pg.locator("#stHist [data-ver]").last.click()
        expect(pg.locator("#stView .note-box")).to_contain_text("стара версія")
        check("перегляд старої версії (без «Нове правило»)", "Нове правило" not in pg.locator("#stView").inner_text())
        pg.click("#stRestore")
        expect(pg.locator("#stMsg")).to_contain_text("Збережено")
        check("відновлення старої версії; поточна теж пішла в історію", "Нове правило" not in pg.locator("#stView").inner_text()
              and len(get(PORT, "/api/strategy")[1]["versions"]) == 3)
        pg.evaluate("document.querySelector('#stView').dataset.x = 1")
        bad = post(PORT, "/api/strategy", {"text": "# T\n\n<script>window.hacked=1</script> [x](javascript:alert(1)) **ok**"})
        pg.click(".tab[data-tab=journal]"); pg.click(".tab[data-tab=strategy]")
        expect(pg.locator("#stView h1")).to_have_text("T")
        check("HTML і javascript:-посилання в документі не виконуються", pg.evaluate("window.hacked") is None
              and pg.locator("#stView a").count() == 0 and pg.locator("#stView b").count() == 1)
        pg.set_viewport_size({"width": 390, "height": 844})
        check("вузьке вікно без горизонтальної прокрутки", pg.evaluate("document.documentElement.scrollWidth") <= 392)
        pg.set_viewport_size({"width": 1440, "height": 1000})
        # ---------- «Закрити додаток»
        pg.click(".tab[data-tab=settings]")
        pg.click("#btnShutdown")
        expect(pg.locator(".closed")).to_contain_text("Rulebound закрито")
        check("«Закрити додаток» зупиняє сервер", wait(lambda: not up(PORT), 8))
        check("помилок у консолі браузера немає", not [e for e in errors if "Failed to fetch" not in e], errors[:3])
        b.close()
finally:
    app.terminate()

# ================================================================ 3. збирач .app
shell = shutil.which("zsh") or shutil.which("bash")
dest = tempfile.mkdtemp(prefix="smc4_apps_")
out = subprocess.run([sys.executable, os.path.join(APP, "make_app.py"), "--dest", dest], capture_output=True, text=True,
                     env=dict(os.environ, SMC_APP_SHELL=shell))
bundle = os.path.join(dest, "Rulebound.app")
exe = os.path.join(bundle, "Contents", "MacOS", "Rulebound")
launcher = os.path.join(bundle, "Contents", "Resources", "launcher.sh")
check("make_app.py створює Rulebound.app", out.returncode == 0 and os.path.isdir(bundle), out.stdout + out.stderr)
check("виконуваний файл — скомпільована програма (не скрипт)", open(exe, "rb").read(4) in (b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")
      and "скомпільовано" in out.stdout, out.stdout)
with open(os.path.join(bundle, "Contents", "Info.plist"), "rb") as f:
    info = plistlib.load(f)
check("Info.plist коректний (виконуваний файл, id, без іконки в Dock)", info["CFBundleExecutable"] == "Rulebound"
      and info["CFBundlePackageType"] == "APPL" and info["LSUIElement"] is True)
check("launcher.sh у Resources знає папку додатка й Python", os.access(launcher, os.X_OK)
      and f'APP_DIR="{APP}"' in open(launcher).read() and sys.executable in open(launcher).read())

# ================================================================ 4. програма + лаунчер на стабах (open, osascript)
stub = tempfile.mkdtemp(prefix="smc4_stub_")
calls = os.path.join(stub, "calls.txt")
for name, body in (("open", f'echo "open $*" >> {calls}; exit 0'), ("osascript", f'echo "osascript $*" >> {calls}; exit 0')):
    p = os.path.join(stub, name)
    with open(p, "w") as f:
        f.write("#!/bin/bash\n" + body + "\n")
    os.chmod(p, 0o755)
src = open(launcher).read().replace("/usr/bin/open", os.path.join(stub, "open")).replace("/usr/bin/osascript", os.path.join(stub, "osascript"))
with open(launcher, "w") as f:
    f.write(src)
data4 = tempfile.mkdtemp(prefix="smc4_data2_")
with open(os.path.join(data4, "settings.json"), "w") as f:
    json.dump({"screener_path": "x", "results_dir": tempfile.mkdtemp()}, f)
env = dict(os.environ, HOME=tempfile.mkdtemp(), SMC_APP_DATA=data4)
first = subprocess.Popen([exe], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
check("програма запускає сервер на 8765", wait(lambda: up(8765), 20))
check("відкриває окреме вікно Chrome (--app=http://127.0.0.1:8765)", wait(lambda: os.path.exists(calls) and "--app=http://127.0.0.1:8765" in open(calls).read(), 10),
      open(calls).read() if os.path.exists(calls) else "")
time.sleep(1)
check("програма лишається «батьком» сервера, поки він працює", first.poll() is None)
open(calls, "w").close()
second = subprocess.run([exe], env=env, capture_output=True, text=True, timeout=30)
procs = subprocess.run(["pgrep", "-f", "app.py --port 8765"], capture_output=True, text=True).stdout.split()
check("повторний запуск не стартує другий сервер, лише відкриває вікно й одразу завершується",
      second.returncode == 0 and len(procs) == 1 and "--app=" in open(calls).read(),
      [second.returncode, subprocess.run(["ps", "-eo", "pid,ppid,args"], capture_output=True, text=True).stdout[-900:]])
post(8765, "/api/shutdown", {})
check("«Закрити додаток»: сервер зупинився, програма теж завершилась", wait(lambda: not up(8765), 8) and wait(lambda: first.poll() is not None, 8))
check("у launcher.log записано «сервер закрито»", "сервер закрито" in open(os.path.join(APP, "data", "launcher.log")).read())
r = subprocess.run([sys.executable, os.path.join(APP, "make_app.py"), "--dest", dest, "--remove"], capture_output=True, text=True)
check("make_app.py --remove прибирає застосунок", not os.path.exists(bundle))
# запасний варіант без компілятора
noc = subprocess.run([sys.executable, os.path.join(APP, "make_app.py"), "--dest", dest], capture_output=True, text=True,
                     env=dict(os.environ, PATH="/nonexistent", SMC_APP_SHELL=shell))
check("без компілятора: ярлик-скрипт і зрозуміле попередження", noc.returncode == 0 and open(exe, "rb").read(2) == b"#!"
      and "не скомпільовано" in noc.stdout, noc.stdout + noc.stderr)
shutil.rmtree(bundle, ignore_errors=True)

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"), "| скріншоти:", shots)
sys.exit(0 if ok else 1)
