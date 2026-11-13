#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.9.5: «Перезапустити додаток» — сервер перезапускається в тому самому процесі, сторінка відкривається знову.
Запуск: python3 tests/test_restart.py"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
PORT = 8791
URL = f"http://127.0.0.1:{PORT}"
ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


def get(path):
    with urllib.request.urlopen(URL + path, timeout=3) as r:
        return json.loads(r.read().decode())


def wait_up(t=20):
    end = time.time() + t
    while time.time() < end:
        try:
            return get("/api/status")
        except Exception:
            time.sleep(0.2)
    return None


data = tempfile.mkdtemp(prefix="smcrs_")
with open(os.path.join(data, "settings.json"), "w") as f:
    json.dump({"caffeinate": False, "git_autopush": False, "spot_auto": False, "ltf_enabled": False}, f)
p = subprocess.Popen([sys.executable, os.path.join(APP, "app.py"), "--port", str(PORT), "--no-browser"],
                     env=dict(os.environ, SMC_APP_DATA=data, HOME=data), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    check("додаток запустився", wait_up() is not None)
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page()
        pg.goto(URL)
        pg.click("nav [data-tab='settings']") if pg.locator("nav [data-tab='settings']").count() else pg.get_by_text("Налаштування", exact=True).first.click()
        expect(pg.locator("#btnRestart")).to_be_visible()
        pg.evaluate("window.__old = 1")
        pg.on("dialog", lambda d: d.accept())
        pg.click("#btnRestart")
        pg.wait_for_function("() => window.__old === undefined && !!document.querySelector('#appFoot') && document.querySelector('#appFoot').textContent.includes('Rulebound v')", timeout=30000)
        check("після натискання сторінка сама відкрилась заново (свіже завантаження), версія в підвалі", "/?r=" in pg.url, pg.url)
        b.close()
    check("процес той самий (exec, а не новий) і працює", p.poll() is None and wait_up() is not None)
finally:
    p.terminate()
    try:
        out = p.communicate(timeout=10)[0]
    except Exception:
        p.kill()
        out = ""
check("у лозі сервера — «Перезапуск додатка…» і другий старт", "Перезапуск додатка" in out and out.count("Rulebound 2.") >= 2, out[-400:])

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"))
sys.exit(0 if ok else 1)
