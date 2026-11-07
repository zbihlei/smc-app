#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Автозбереження коду в GitHub (gitsync.py) + блок «GitHub» у налаштуваннях. Запуск: python3 tests/test_git.py"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
import gitsync  # noqa: E402

ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True).stdout.strip()


def make_repo():
    remote = tempfile.mkdtemp(prefix="remote_") + "/smc-app.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", remote], capture_output=True)
    repo = tempfile.mkdtemp(prefix="repo_")
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test"); git(repo, "config", "user.email", "t@t")
    open(os.path.join(repo, "app.py"), "w").write("v = 1\n")
    open(os.path.join(repo, ".gitignore"), "w").write("data/\n")
    os.makedirs(os.path.join(repo, "data"))
    open(os.path.join(repo, "data", "journal.db"), "w").write("секрет")
    git(repo, "add", "."); git(repo, "commit", "-m", "перший")
    git(repo, "remote", "add", "origin", remote)
    git(repo, "push", "-u", "origin", "main")
    return repo, remote


# ================================================================ 1. логіка
check("посилання на GitHub з https і ssh адрес", gitsync.GitSync.web_url("https://github.com/zbihlei/smc-app.git") == "https://github.com/zbihlei/smc-app"
      and gitsync.GitSync.web_url("git@github.com:zbihlei/smc-app.git") == "https://github.com/zbihlei/smc-app")
check("зрозумілі пояснення помилок (токен, мережа, відхилено)",
      "токен" in gitsync.GitSync.explain("fatal: Authentication failed for 'https://github.com/x'")
      and "зв'язку" in gitsync.GitSync.explain("Could not resolve host: github.com")
      and "git pull" in gitsync.GitSync.explain("! [rejected] main -> main (fetch first)"))

repo, remote = make_repo()
gs = gitsync.GitSync(repo, os.path.join(tempfile.mkdtemp(), "git_state.json"), version="1.0.3")
open(os.path.join(repo, "app.py"), "w").write("v = 2\n")
gs.sync()
check("перша перевірка: зміну помічено, але коміт ще ні (чекаю, поки файли перестануть мінятись)",
      git(remote, "log", "--oneline").count("\n") == 0 and gs.state.get("last_commit") is None)
open(os.path.join(repo, "app.py"), "w").write("v = 3\n")
gs.sync()
check("файли ще мінялись — коміту немає", gs.state.get("last_commit") is None)
gs.sync()
log = git(remote, "log", "--format=%s")
check("стабільні зміни → коміт і пуш на GitHub", "Автозбереження v1.0.3: app.py" in log and gs.state["ok"] and gs.state.get("last_push"), log)
open(os.path.join(repo, "data", "journal.db"), "w").write("нові угоди")
gs.sync(); gs.sync()
check("дані з .gitignore (журнал) не потрапляють у git", "journal.db" not in git(remote, "ls-tree", "-r", "--name-only", "main"))
open(os.path.join(repo, "new.py"), "w").write("x = 1\n")
gs.sync(force=True)
check("«Запушити зараз» — одразу, без очікування", "new.py" in git(remote, "ls-tree", "-r", "--name-only", "main"))
git(repo, "remote", "set-url", "origin", "/nonexistent/repo.git")
open(os.path.join(repo, "app.py"), "w").write("v = 4\n")
st = gs.sync(force=True)
check("пуш не вдався → коміт лишається, «не запушено: 1», зрозуміла помилка", st["ok"] is False and st["error"]
      and gs.status()["ahead"] == 1, (st, gs.status().get("ahead")))
git(repo, "remote", "set-url", "origin", remote)
gs.sync()
check("коли зв'язок повернувся — запушено пропущене", gs.state["ok"] and gs.status()["ahead"] == 0)
check("стан збережено у файлі (переживає перезапуск)", json.load(open(gs.state_path))["last_push"])
norepo = gitsync.GitSync(tempfile.mkdtemp(), os.path.join(tempfile.mkdtemp(), "s.json"))
check("папка без git — пояснення, а не падіння", norepo.sync()["ok"] is False and not norepo.status()["repo"])

# ================================================================ 2. у додатку
from playwright.sync_api import sync_playwright, expect  # noqa: E402

repo2, remote2 = make_repo()
git(repo2, "remote", "set-url", "origin", remote2)
PORT = 8782
URL = f"http://127.0.0.1:{PORT}"
data = tempfile.mkdtemp(prefix="smcgit_")
with open(os.path.join(data, "settings.json"), "w") as f:
    json.dump({"screener_path": "x", "results_dir": tempfile.mkdtemp()}, f)
app = subprocess.Popen([sys.executable, os.path.join(APP, "app.py"), "--port", str(PORT), "--no-browser"],
                       env=dict(os.environ, SMC_APP_DATA=data, SMC_GIT_DIR=repo2, SMC_GIT_INTERVAL="1"),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(60):
    try:
        urllib.request.urlopen(URL + "/api/status", timeout=1)
        break
    except Exception:
        time.sleep(0.1)
try:
    open(os.path.join(repo2, "app.py"), "w").write("v = 99\n")
    for _ in range(40):
        if "Автозбереження" in git(remote2, "log", "--format=%s"):
            break
        time.sleep(0.25)
    check("додаток сам комітить і пушить зміни коду (автозбереження за замовчуванням увімкнено)",
          "Автозбереження" in git(remote2, "log", "--format=%s"))
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1300, "height": 1000})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto(URL)
        pg.click(".tab[data-tab=settings]")
        expect(pg.locator("#gitState")).to_contain_text("останній пуш", timeout=10000)
        check("блок GitHub: ✓, гілка, останній пуш", "гілка main" in pg.locator("#gitState").inner_text())
        check("перемикач автозбереження увімкнено", pg.locator("input[name=git_autopush]").is_checked())
        pg.uncheck("input[name=git_autopush]")
        pg.click("#setForm button[type=submit]")
        expect(pg.locator("#gitState")).to_contain_text("автозбереження вимкнено", timeout=10000)
        open(os.path.join(repo2, "app.py"), "w").write("v = 100\n")
        time.sleep(3)
        check("вимкнено — нічого не пушиться само", "v = 100" not in subprocess.run(["git", "show", "main:app.py"], cwd=remote2,
                                                                                   capture_output=True, text=True).stdout)
        pg.click("#gitPush")
        expect(pg.locator("#gitMsg")).to_contain_text("Збережено", timeout=15000)
        check("«Запушити зараз» працює й при вимкненому автозбереженні", "v = 100" in subprocess.run(
            ["git", "show", "main:app.py"], cwd=remote2, capture_output=True, text=True).stdout)
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.wait_for_timeout(300)
        check("вузьке вікно без горизонтальної прокрутки", pg.evaluate("document.documentElement.scrollWidth") <= 392)
        check("помилок у консолі немає", not errors, errors[:3])
        b.close()
finally:
    app.terminate()

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"))
sys.exit(0 if ok else 1)
