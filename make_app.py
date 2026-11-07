#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Створює на Mac застосунок «SMC App.app» — запуск додатка без Терміналу (етап 4).

    cd ~/Desktop/smc_app && python3 make_app.py            # у ~/Applications (видно в Launchpad і Spotlight)
    python3 make_app.py --dest ~/Desktop                   # або в іншу папку
    python3 make_app.py --remove                           # прибрати

Що робить «SMC App» при запуску:
  1) підвантажує ~/.zshrc (там SMC_SHEET_URL / SMC_SHEET_TOKEN для аркуша «Скринер»);
  2) якщо сервер додатка ще не працює — запускає його у фоні (`python3 app.py --no-browser`, лог у data/app.log);
  3) відкриває додаток окремим вікном Chrome без вкладок і адресного рядка (`--app`), а без Chrome — у браузері.
Закрити сам додаток: «Налаштування → Закрити додаток» (скринер, якщо працює, не зупиняється).

Застосунок створюється тут, на твоєму Mac, тому macOS не блокує його як «завантажений з інтернету».
Якщо перенесеш папку smc_app в інше місце — просто запусти make_app.py ще раз.

Чому всередині маленька програма на C, а не просто скрипт: дозвіл macOS «Повний доступ до диска» (потрібен,
щоб писати копії журналу в iCloud Drive) тримається лише за справжньою підписаною програмою. Тому виконуваний
файл застосунку — крихітна програма (компілюється тут же через clang з Command Line Tools і підписується
codesign), яка запускає скрипт `Resources/launcher.sh` і лишається «батьком» сервера, поки він працює.
Після кожної перезбірки дозвіл треба надати заново (у списку прибрати старий «SMC App» і додати новий).
"""
import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile

APP_DIR = os.path.dirname(os.path.abspath(__file__))
NAME = "SMC App"
BUNDLE_ID = "local.smcapp.launcher"
PORT = 8765

LAUNCHER = r'''#!/bin/zsh
# SMC App: запуск без Терміналу. Згенеровано make_app.py — не редагуй руками, перезбери: python3 make_app.py
APP_DIR=__APP_DIR__
PY=__PYTHON__
PORT=__PORT__
URL="http://127.0.0.1:$PORT"
LOG="$APP_DIR/data/launcher.log"

# змінні SMC_SHEET_URL / SMC_SHEET_TOKEN та PATH — як у Терміналі
[ -f "$HOME/.zprofile" ] && source "$HOME/.zprofile" >/dev/null 2>&1
[ -f "$HOME/.zshrc" ] && source "$HOME/.zshrc" >/dev/null 2>&1
export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

alert() {
  /usr/bin/osascript -e "display alert \"SMC App\" message \"$1\" as critical" >/dev/null 2>&1
}
up() { /usr/bin/curl -s -o /dev/null --max-time 1 "$URL/api/status"; }

if [ ! -f "$APP_DIR/app.py" ]; then
  alert "Не знайдено папку додатка: $APP_DIR. Перезбери ярлик: у новій папці виконай python3 make_app.py"
  exit 1
fi
mkdir -p "$APP_DIR/data"
echo "$(date '+%F %T') запуск ярлика" >> "$LOG"

SERVER_PID=""
if ! up; then
  cd "$APP_DIR" || exit 1
  "$PY" -u app.py --port "$PORT" --no-browser >> "$APP_DIR/data/app.log" 2>&1 &
  SERVER_PID=$!
  for i in {1..60}; do up && break; sleep 0.25; done
  if ! up; then
    alert "Додаток не запустився. Подробиці: $APP_DIR/data/app.log"
    exit 1
  fi
  echo "$(date '+%F %T') сервер запущено" >> "$LOG"
fi

if /usr/bin/open -Ra "Google Chrome" >/dev/null 2>&1; then
  /usr/bin/open -na "Google Chrome" --args --app="$URL" --window-size=1500,950
else
  /usr/bin/open "$URL"
fi

# Якщо сервер запустили ми — чекаємо, поки його закриють («Налаштування → Закрити додаток»).
# Так програма SMC App лишається «батьком» сервера, і дозволи macOS (iCloud Drive) діють і для нього.
if [ -n "$SERVER_PID" ]; then
  wait "$SERVER_PID"
  echo "$(date '+%F %T') сервер закрито" >> "$LOG"
fi
'''


STUB_C = r'''/* SMC App: виконуваний файл застосунку. Запускає Resources/launcher.sh і чекає на нього,
   щоб дозволи macOS, надані «SMC App», діяли й для сервера додатка. Згенеровано make_app.py. */
#include <limits.h>
#include <libgen.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#ifndef SHELL_PATH
#define SHELL_PATH "/bin/zsh"
#endif
extern char **environ;

int main(int argc, char *argv[]) {
    char exe[PATH_MAX], script[PATH_MAX + 64];
    pid_t pid;
    int st = 0;
    (void)argc;
    if (!realpath(argv[0], exe)) return 1;
    snprintf(script, sizeof script, "%s/../Resources/launcher.sh", dirname(exe));
    char *args[] = {SHELL_PATH, script, NULL};
    if (posix_spawn(&pid, SHELL_PATH, NULL, NULL, args, environ) != 0) return 1;
    while (waitpid(pid, &st, 0) < 0) {}
    return WIFEXITED(st) ? WEXITSTATUS(st) : 1;
}
'''


def compile_stub(exe, shell="/bin/zsh"):
    """Компілює C-обгортку. Повертає None, якщо вдалося, або текст помилки."""
    cc = shutil.which("clang") or shutil.which("cc")
    if not cc:
        return "немає компілятора (clang / cc)"
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "smc_app_stub.c")
    with open(src, "w", encoding="utf-8") as f:
        f.write(STUB_C)
    try:
        r = subprocess.run([cc, "-O2", f'-DSHELL_PATH="{shell}"', "-o", exe, src], capture_output=True, text=True)
        if r.returncode != 0:
            return (r.stderr or r.stdout or "помилка компіляції").strip()[-300:]
        os.chmod(exe, 0o755)
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def zq(s):
    """Рядок у подвійних лапках для zsh (шляхи з пробілами тощо)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"'


def make_icns(png, resources):
    """Іконка .icns з PNG через вбудовані в macOS sips і iconutil. Без них — застосунок буде зі стандартною іконкою."""
    if not (shutil.which("sips") and shutil.which("iconutil") and os.path.exists(png)):
        return False
    tmp = tempfile.mkdtemp()
    iconset = os.path.join(tmp, "AppIcon.iconset")
    os.makedirs(iconset)
    try:
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = size * scale
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                subprocess.run(["sips", "-z", str(px), str(px), png, "--out", os.path.join(iconset, name)],
                               check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", os.path.join(resources, "AppIcon.icns")],
                       check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, OSError):
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build(dest, python):
    app = os.path.join(dest, NAME + ".app")
    if os.path.exists(app):
        shutil.rmtree(app)
    macos = os.path.join(app, "Contents", "MacOS")
    resources = os.path.join(app, "Contents", "Resources")
    os.makedirs(macos)
    os.makedirs(resources)
    exe = os.path.join(macos, NAME)
    script = (LAUNCHER.replace("__APP_DIR__", zq(APP_DIR)).replace("__PYTHON__", zq(python))
              .replace("__PORT__", str(PORT)))
    launcher = os.path.join(resources, "launcher.sh")
    with open(launcher, "w", encoding="utf-8") as f:
        f.write(script)
    os.chmod(launcher, 0o755)
    shell = os.environ.get("SMC_APP_SHELL") or "/bin/zsh"  # SMC_APP_SHELL — лише для тестів не на Mac
    stub_err = compile_stub(exe, shell)
    if stub_err:  # запасний варіант: скрипт як виконуваний файл (працює, але без дозволу на iCloud Drive)
        shutil.copyfile(launcher, exe)
        os.chmod(exe, 0o755)
    has_icon = make_icns(os.path.join(APP_DIR, "static", "app_icon.png"), resources)
    info = {
        "CFBundleName": NAME, "CFBundleDisplayName": NAME, "CFBundleExecutable": NAME,
        "CFBundleIdentifier": BUNDLE_ID, "CFBundlePackageType": "APPL", "CFBundleVersion": "0.4",
        "CFBundleShortVersionString": "0.4", "LSMinimumSystemVersion": "10.13", "NSHighResolutionCapable": True,
        "LSUIElement": True,  # без іконки в Dock: програма лише тримає сервер у фоні
    }
    if has_icon:
        info["CFBundleIconFile"] = "AppIcon"
    with open(os.path.join(app, "Contents", "Info.plist"), "wb") as f:
        plistlib.dump(info, f)
    signed = False
    if not stub_err and shutil.which("codesign"):
        signed = subprocess.run(["codesign", "--force", "--sign", "-", app], capture_output=True).returncode == 0
    return app, has_icon, stub_err, signed


def main():
    p = argparse.ArgumentParser(description="Створити «SMC App.app» для запуску без Терміналу")
    p.add_argument("--dest", default="~/Applications", help="куди покласти застосунок (~/Applications)")
    p.add_argument("--remove", action="store_true", help="видалити створений застосунок")
    a = p.parse_args()
    dest = os.path.abspath(os.path.expanduser(a.dest))
    app = os.path.join(dest, NAME + ".app")
    if a.remove:
        if os.path.exists(app):
            shutil.rmtree(app)
            print(f"Видалено: {app}")
        else:
            print(f"Немає: {app}")
        return
    if sys.platform != "darwin":
        print("Увага: це не macOS — застосунок створю, але запускати його можна лише на Mac.")
    os.makedirs(dest, exist_ok=True)
    app, has_icon, stub_err, signed = build(dest, sys.executable)
    print(f"Готово: {app}" + ("" if has_icon else "  (без власної іконки: немає sips/iconutil)"))
    if stub_err:
        print(f"Увага: програму-обгортку не скомпільовано ({stub_err}). Ярлик працює, але копії журналу в iCloud Drive "
              "можуть не писатись. Встанови Command Line Tools: xcode-select --install, і запусти make_app.py ще раз.")
    else:
        print("Програма-обгортка: скомпільовано" + (", підписано (codesign)" if signed else "") + ".")
        print("Якщо «SMC App» уже є в «Повному доступі до диска» — прибери його там (−) і додай знову (+): "
              "після перезбірки старий дозвіл не діє.")
    print(f"Папка додатка: {APP_DIR}")
    print(f"Python: {sys.executable}")
    print("Відкрити: Launchpad / Spotlight → «SMC App». Щоб був у Dock — перетягни його з Finder у Dock.")
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", app], check=False)  # показати у Finder


if __name__ == "__main__":
    main()
