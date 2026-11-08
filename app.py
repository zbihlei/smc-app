#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SMC App — локальний додаток для скринера й журналу (етап 1: каркас + вкладка «Скринер»).

Запуск:  python3 app.py            (відкриє http://127.0.0.1:8765 у браузері)
         python3 app.py --port 8800 --no-browser

Лише стандартна бібліотека Python (окрім requests, який потрібен самому скринеру).
Сервер слухає тільки 127.0.0.1: з інших пристроїв мережі до нього не достукатись.
"""
import argparse
import datetime as dt
import json
import mimetypes
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
import csv
import io
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import backtest_data
import backtest_signals
import backtest_trades
import gitsync
import spot_scan
import ltf
import journal
import tg
import stats

APP_VERSION = "1.5.1"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
DATA_DIR = os.environ.get("SMC_APP_DATA", os.path.join(APP_DIR, "data"))
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
LOG_PATH = os.path.join(DATA_DIR, "screener.log")
RUNNER = os.path.join(APP_DIR, "screener_runner.py")
LOG_MAX_BYTES = 2_000_000

DEFAULTS = {
    "screener_path": os.path.join(APP_DIR, "smc_screener.py"),
    "results_dir": "~/Desktop/results",
    "watch": 30,
    "approach": 4,
    "top": 100,
    "min_volume": 30,             # мін. обсяг торгів за 24 год, млн $ (скринер: --min-volume; монети з меншим обсягом не скануються)
    "price_check": 15,
    "min_stop_atr": 0.6,
    "entry_tol": 25,              # допуск входу, % висоти зони (скринер v2.7+: --entry-tol)
    "trend": "strict",
    "popup": True,
    "voice": True,
    "tv_button": True,
    "stop_wick": True,
    "caffeinate": True,
    "sheet_sync": False,          # записувати угоди в Google Таблицю (вимкнено: журнал живе в додатку)
    "backup_extra_dir": "",       # друга папка для резервних копій журналу (напр. iCloud Drive)
    "tg_enabled": True,           # дублювати алерти в Telegram (якщо підключено)
    "tg_chat": "",                # chat id (не секрет; токен — лише в SMC_TG_TOKEN)
    "tg_name": "",
    "extra_args": "",
    "git_autopush": True,         # автозбереження коду в GitHub (лише якщо папку підключено до git)
    "breakers": True,             # шукати брейкер-блоки (скринер v2.6+); вимкнено -> --no-breakers
    "spot_auto": True,            # вкладка «Спот»: сканувати раз на добу, поки додаток відкритий
    "ltf_enabled": True,          # уточнення входу на 15m + алерт «УТОЧНЕННЯ 15m»
}
# межі для числових полів: (мін, макс, тип)
LIMITS = {
    "watch": (1, 240, float), "approach": (0.1, 50, float), "top": (1, 500, int), "min_volume": (1, 1000, float),
    "price_check": (0, 600, float), "min_stop_atr": (0, 5, float), "entry_tol": (0, 50, float),
}
TRENDS = ("strict", "flag", "bos")
FLAGS = ("popup", "voice", "tv_button", "stop_wick", "caffeinate", "sheet_sync", "tg_enabled", "git_autopush", "breakers", "spot_auto", "ltf_enabled")
ICLOUD_DIR = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs")


# ----------------------------------------------------------------------------
# Налаштування
# ----------------------------------------------------------------------------
def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            s.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Увага: не вдалося прочитати {SETTINGS_PATH}: {e}. Беру значення за замовчуванням.")
    return s


def validate_settings(new):
    """Повертає (налаштування, помилки). Невідомі ключі ігноруються."""
    s = load_settings()
    errors = []
    for k, v in new.items():
        if k not in DEFAULTS:
            continue
        if k in LIMITS:
            lo, hi, tp = LIMITS[k]
            try:
                x = tp(str(v).replace(",", "."))
            except (TypeError, ValueError):
                errors.append(f"{k}: потрібне число")
                continue
            if not lo <= x <= hi:
                errors.append(f"{k}: від {lo:g} до {hi:g}")
                continue
            s[k] = x
        elif k in FLAGS:
            s[k] = bool(v)
        elif k == "trend":
            if v not in TRENDS:
                errors.append("trend: strict / flag / bos")
                continue
            s[k] = v
        elif k == "extra_args":
            try:
                shlex.split(str(v))
            except ValueError as e:
                errors.append(f"додаткові параметри: {e}")
                continue
            if re.search(r"--sheet-(url|token)", str(v)):
                errors.append("додаткові параметри: url/токен таблиці задавай лише змінними середовища SMC_SHEET_URL / SMC_SHEET_TOKEN")
                continue
            s[k] = str(v).strip()
        else:  # шляхи
            s[k] = str(v).strip()
    return s, errors


def save_settings(s):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_PATH)


def results_dir(s):
    return os.path.abspath(os.path.expanduser(s["results_dir"]))


def screener_path(s):
    return os.path.abspath(os.path.expanduser(s["screener_path"]))


def build_command(s):
    """Команда запуску скринера. URL і токен таблиці беруться скринером зі змінних середовища (не з командного рядка)."""
    num = lambda x: f"{x:g}" if isinstance(x, float) else str(x)
    cmd = [sys.executable, "-u", RUNNER, screener_path(s),
           "--watch", num(s["watch"]), "--approach", num(s["approach"]), "--top", str(int(s["top"])),
           "--min-volume", num(float(s["min_volume"])),
           "--price-check", num(s["price_check"]), "--min-stop-atr", num(s["min_stop_atr"]),
           "--trend", s["trend"], "--out", results_dir(s)]
    for flag, opt in (("popup", "--popup"), ("voice", "--voice"), ("tv_button", "--tv-button"), ("stop_wick", "--stop-wick")):
        if s[flag]:
            cmd.append(opt)
    if not s.get("breakers", True):
        cmd.append("--no-breakers")
    if float(s.get("entry_tol") or 0) > 0:
        cmd += ["--entry-tol", num(float(s["entry_tol"]))]
    cmd += shlex.split(s["extra_args"])
    caff = shutil.which("caffeinate") if s["caffeinate"] else None
    if caff:
        cmd = [caff, "-i"] + cmd
    return cmd


def display_command(cmd):
    """Коротка, читабельна версія команди для інтерфейсу."""
    out = []
    for x in cmd:
        if x == sys.executable:
            x = "python3"
        elif x.endswith("screener_runner.py") or x.endswith(".py"):
            x = os.path.basename(x)
        elif x.endswith("caffeinate"):
            x = "caffeinate"
        out.append(shlex.quote(x))
    return " ".join(out)


# ----------------------------------------------------------------------------
# Процес скринера
# ----------------------------------------------------------------------------
def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def pid_is_ours(pid):
    """Перевірка, що за цим pid досі наш скринер (а не випадковий процес, що отримав той самий номер)."""
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return pid_alive(pid)
    return "screener_runner.py" in out


class Screener:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.state = self._load_state()
        self.last_exit = None  # {"code": .., "at": ..} якщо процес завершився сам

    # стан між перезапусками додатка: скринер працює окремо й не зупиняється разом із вікном/терміналом додатка
    def _load_state(self):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                st = json.load(f)
            if st.get("pid") and pid_alive(st["pid"]) and pid_is_ours(st["pid"]):
                return st
        except Exception:
            pass
        return {}

    def _save_state(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False)

    def running(self):
        if self.proc is not None:
            code = self.proc.poll()
            if code is None:
                return True
            self._finished(code)
            return False
        pid = self.state.get("pid")
        if pid and pid_alive(pid) and pid_is_ours(pid):
            return True
        if pid:
            self._finished(None)
        return False

    def _finished(self, code):
        if self.state.get("pid") and not self.state.get("stopping"):
            self.last_exit = {"code": code, "at": dt.datetime.now().strftime("%H:%M:%S")}
            log_line(f"Скринер завершився сам (код {code}). Подивись повідомлення вище.")
        self.proc = None
        self.state = {}
        self._save_state()

    def start(self, s):
        with self.lock:
            if self.running():
                return False, "Скринер уже працює"
            sp = screener_path(s)
            if not os.path.exists(sp):
                return False, f"Не знайдено скринер: {sp}. Вкажи шлях у «Налаштуваннях»."
            os.makedirs(results_dir(s), exist_ok=True)
            cmd = build_command(s)
            rotate_log()
            os.makedirs(DATA_DIR, exist_ok=True)
            log = open(LOG_PATH, "a", encoding="utf-8")
            log.write(f"\n===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} старт: {display_command(cmd)}\n")
            log.flush()
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                       SMC_TG_CHAT=tg_chat(s))
            if not s.get("sheet_sync"):  # запис у Google Таблицю вимкнено: скринер не оновлює й аркуш «Скринер»
                env["SMC_SHEET_URL"] = ""
                env["SMC_SHEET_TOKEN"] = ""
            self.proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         cwd=APP_DIR, env=env, start_new_session=True)
            log.close()  # дочірній процес тримає свою копію дескриптора
            self.last_exit = None
            self.state = {"pid": self.proc.pid, "started_at": dt.datetime.now().isoformat(timespec="seconds"),
                          "command": display_command(cmd), "results_dir": results_dir(s)}
            self._save_state()
            return True, "Скринер запущено"

    def stop(self):
        with self.lock:
            if not self.running():
                return False, "Скринер не працює"
            pid = self.state["pid"]
            self.state["stopping"] = True
            # як Ctrl+C у терміналі: скринер чисто завершує роботу; потім жорсткіше, якщо не послухався
            for sig, wait in ((signal.SIGINT, 8), (signal.SIGTERM, 4), (signal.SIGKILL, 2)):
                try:
                    os.killpg(pid, sig)
                except ProcessLookupError:
                    break
                except PermissionError:
                    os.kill(pid, sig)
                end = time.time() + wait
                while time.time() < end:
                    if self.proc is not None:
                        if self.proc.poll() is not None:
                            break
                    elif not pid_alive(pid):
                        break
                    time.sleep(0.2)
                else:
                    continue
                break
            log_line(f"===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} зупинено з додатка")
            self.proc = None
            self.state = {}
            self._save_state()
            return True, "Скринер зупинено"

    def status(self):
        with self.lock:
            run = self.running()
            return {"running": run, "pid": self.state.get("pid") if run else None,
                    "started_at": self.state.get("started_at") if run else None,
                    "command": self.state.get("command") if run else None,
                    "results_dir": self.state.get("results_dir") if run else None,
                    "last_exit": self.last_exit}


def log_line(text):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass


def rotate_log():
    try:
        if os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            os.replace(LOG_PATH, LOG_PATH + ".1")
    except OSError:
        pass


def tail(path, n):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            data = f.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return []
    return data.splitlines()[-n:]


SCAN_RE = re.compile(r"^\[(\d\d:\d\d)\] у списку: (\d+), нових: (\d+)(?:, помилок: (\d+))?")
ERR_RE = re.compile(r"^\[(\d\d:\d\d)\] помилка скану: (.*)")


STALE_EXTRA_MIN = 10   # скан вважається пропущеним, якщо його немає довше за інтервал + стільки хвилин


def scan_age(st=None, s=None):
    """Скільки секунд тому був останній скан: за часом зміни results/latest_app.json (пишеться після кожного скану).
    Повертає (вік_с або None, пропущено_скан?)."""
    s = s or load_settings()
    st = st or SCREENER.status()
    if not st.get("running"):
        return None, False
    try:
        mtime = os.path.getmtime(os.path.join(results_dir(s), "latest_app.json"))
    except OSError:
        mtime = None
    started = None
    try:
        started = dt.datetime.fromisoformat(st["started_at"]).timestamp()
    except Exception:
        pass
    ref = max(x for x in (mtime, started) if x is not None) if (mtime or started) else None
    if ref is None:
        return None, False
    age = max(0.0, time.time() - ref)
    limit = float(s.get("watch") or 30) * 60 + STALE_EXTRA_MIN * 60
    return (age if mtime and (not started or mtime >= started) else None), age > limit


def tg_chat(s=None):
    """Chat id, якщо Telegram підключено й увімкнено; інакше порожньо."""
    s = s or load_settings()
    return str(s.get("tg_chat") or "") if s.get("tg_enabled") and tg.token() else ""


def notify_mac(title, text):
    """Сповіщення macOS (банер) + Telegram, якщо підключено. Не на Mac — лише рядок у лозі."""
    log_line(f"===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} сповіщення: {title}: {text}")
    chat = tg_chat()
    if chat:
        import html as _h
        tg.send_bg(chat, f"⚠️ <b>{_h.escape(title)}</b>\n{_h.escape(text)}", log=log_line)
    if sys.platform != "darwin":
        return
    esc_ = lambda x: str(x).replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.Popen(["osascript", "-e", f'display notification "{esc_(text)}" with title "{esc_(title)}" sound name "Basso"'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def watchdog(interval=60, wake_gap=90, grace=240):
    """Фоновий наглядач: сповіщає, якщо скринер завершився сам або давно не сканував.
    Після сну Mac дає скринеру grace секунд на скан після пробудження (без хибних тривог)."""
    last_tick = time.time()
    quiet_until = time.time() + grace
    told_exit, told_stale = None, False
    while True:
        time.sleep(interval)
        now = time.time()
        if now - last_tick - interval > wake_gap:
            quiet_until = now + grace  # Mac щойно прокинувся
        last_tick = now
        try:
            st = SCREENER.status()
            ex = st.get("last_exit")
            if ex and ex != told_exit:
                told_exit = ex
                notify_mac("SMC App: скринер зупинився", f"Скринер завершився сам о {ex['at']}. Відкрий SMC App і натисни «Старт».")
            if not st.get("running"):
                told_stale = False
                continue
            age, stale = scan_age(st)
            if stale and not told_stale and now >= quiet_until:
                told_stale = True
                mins = int((age or 0) // 60)
                notify_mac("SMC App: немає сканів", (f"Останній скан {mins} хв тому. " if age else "")
                           + "Перевір мережу; подробиці — у лозі скринера в додатку.")
            elif not stale:
                told_stale = False
        except Exception as e:
            log_line(f"наглядач: {e}")


def last_scan_info():
    """Останній рядок скану з логу (час — як його пише скринер, тобто час Mac)."""
    info = {"last_scan": None, "last_error": None}
    for line in reversed(tail(LOG_PATH, 400)):
        if line.startswith("===== ") and "старт:" in line:
            break
        if info["last_scan"] is None:
            m = SCAN_RE.match(line)
            if m:
                info["last_scan"] = {"time": m.group(1), "rows": int(m.group(2)), "new": int(m.group(3)),
                                     "errors": int(m.group(4) or 0)}
        if info["last_error"] is None:
            m = ERR_RE.match(line)
            if m:
                info["last_error"] = {"time": m.group(1), "text": m.group(2)[:300]}
        if info["last_scan"] and info["last_error"]:
            break
    return info


def read_results(s, since=None):
    d = results_dir(s)
    for name, source in (("latest_app.json", "app"), ("latest.json", "legacy")):
        p = os.path.join(d, name)
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        if since is not None and abs(mtime - since) < 1e-6:
            return {"changed": False, "mtime": mtime}
        for _ in range(3):  # файл могли саме переписувати
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
                break
            except (ValueError, OSError):
                time.sleep(0.2)
        else:
            return {"changed": True, "source": "error", "mtime": mtime, "error": f"Не вдалося прочитати {p}"}
        return {"changed": True, "source": source, "mtime": mtime, "path": p,
                "age_s": max(0, time.time() - mtime), "data": data}
    return {"changed": True, "source": "none", "mtime": None, "dir": d}


def run_selftest(s):
    sp = screener_path(s)
    if not os.path.exists(sp):
        return {"ok": False, "output": f"Не знайдено скринер: {sp}"}
    try:
        r = subprocess.run([sys.executable, "-u", RUNNER, sp, "--selftest"], capture_output=True, text=True,
                           timeout=120, cwd=APP_DIR, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "selftest не завершився за 120 с"}
    out = (r.stdout + r.stderr).strip()
    n_ok = len(re.findall(r"^OK ", out, re.M))
    n_fail = len(re.findall(r"^FAIL ", out, re.M))
    return {"ok": r.returncode == 0, "code": r.returncode, "passed": n_ok, "failed": n_fail, "output": out[-6000:]}


def environment_info(s):
    return {
        "app_version": APP_VERSION,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "caffeinate": bool(shutil.which("caffeinate")),
        "screener_found": os.path.exists(screener_path(s)),
        "screener_path": screener_path(s),
        "results_dir": results_dir(s),
        "sheet_sync": bool(s.get("sheet_sync")),
        "sheet_url_set": bool(os.environ.get("SMC_SHEET_URL")),
        "sheet_token_set": bool(os.environ.get("SMC_SHEET_TOKEN")),
        "requests": _has_requests(),
        "tg_token_set": bool(tg.token()),
        "tg_chat": s.get("tg_chat") or "",
        "tg_name": s.get("tg_name") or "",
        "tg_enabled": bool(s.get("tg_enabled")),
    }


def _has_requests():
    try:
        import requests  # noqa: F401
        return True
    except ImportError:
        return False



# ----------------------------------------------------------------------------
# Журнал (етап 2)
# ----------------------------------------------------------------------------
JOURNAL = None
SYNC_LOCK = threading.Lock()


# ----------------------------------------------------------------------------
# Стратегія (документ у data/strategy.md; кожна попередня версія — у data/strategy_history)
# ----------------------------------------------------------------------------
STRATEGY_PATH = os.path.join(DATA_DIR, "strategy.md")
STRATEGY_HISTORY = os.path.join(DATA_DIR, "strategy_history")
STRATEGY_SEED = os.path.join(STATIC_DIR, "strategy_seed.md")


def strategy_title(text):
    for line in (text or "").splitlines():
        if line.strip():
            return line.lstrip("# ").strip()[:80]
    return ""


def strategy_load():
    if not os.path.exists(STRATEGY_PATH) and os.path.exists(STRATEGY_SEED):
        os.makedirs(DATA_DIR, exist_ok=True)
        shutil.copyfile(STRATEGY_SEED, STRATEGY_PATH)
    try:
        with open(STRATEGY_PATH, encoding="utf-8") as f:
            text = f.read()
        mtime = os.path.getmtime(STRATEGY_PATH)
    except FileNotFoundError:
        text, mtime = "", None
    versions = []
    if os.path.isdir(STRATEGY_HISTORY):
        for n in sorted(os.listdir(STRATEGY_HISTORY), reverse=True):
            if re.fullmatch(r"strategy_[\d_-]+\.md", n):
                p = os.path.join(STRATEGY_HISTORY, n)
                with open(p, encoding="utf-8") as f:
                    head = f.read(400)
                versions.append({"name": n, "title": strategy_title(head),
                                 "mtime": dt.datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds")})
    return {"text": text, "title": strategy_title(text), "path": STRATEGY_PATH, "versions": versions,
            "mtime": mtime, "updated_at": dt.datetime.fromtimestamp(mtime).isoformat(timespec="seconds") if mtime else None}


def strategy_save(text, base_mtime=None):
    text = str(text or "").replace("\r\n", "\n")
    if len(text) > 1_000_000:
        return 400, {"ok": False, "error": "задовгий документ"}
    cur = strategy_load()
    if base_mtime and cur["mtime"] and abs(float(base_mtime) - cur["mtime"]) > 1e-3:
        return 409, {"ok": False, "error": "документ змінився в іншому вікні — онови сторінку, щоб не затерти зміни"}
    if text == cur["text"]:
        return 200, dict(strategy_load(), ok=True, unchanged=True)
    os.makedirs(STRATEGY_HISTORY, exist_ok=True)
    if cur["text"]:
        name = f"strategy_{dt.datetime.now():%Y-%m-%d_%H%M%S_%f}.md"  # мікросекунди: два збереження за секунду не затирають одне одного
        with open(os.path.join(STRATEGY_HISTORY, name), "w", encoding="utf-8") as f:
            f.write(cur["text"])
    tmp = STRATEGY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, STRATEGY_PATH)
    ed = extra_dir()
    if ed:  # копія в другу папку (напр. iCloud Drive) разом із журналом
        try:
            d = os.path.abspath(os.path.expanduser(ed))
            os.makedirs(d, exist_ok=True)
            shutil.copy2(STRATEGY_PATH, os.path.join(d, "strategy.md"))
        except Exception as e:
            return 200, dict(strategy_load(), ok=True, extra_error=str(e))
    return 200, dict(strategy_load(), ok=True)


# ----------------------------------------------------------------------------
# Історія змін (data/changelog.json; попередні версії — у data/changelog_history)
# ----------------------------------------------------------------------------
CHANGELOG_PATH = os.path.join(DATA_DIR, "changelog.json")
CHANGELOG_HISTORY = os.path.join(DATA_DIR, "changelog_history")
CHANGELOG_SEED = os.path.join(STATIC_DIR, "changelog_seed.json")
CHANGELOG_COMPONENTS = ("Скринер", "Стратегія", "Журнал", "Додаток", "Інструменти")
CHANGELOG_FIELDS = ("date", "approx", "component", "version", "title", "what", "why", "result", "trades")


def changelog_load():
    if not os.path.exists(CHANGELOG_PATH) and os.path.exists(CHANGELOG_SEED):
        os.makedirs(DATA_DIR, exist_ok=True)
        shutil.copyfile(CHANGELOG_SEED, CHANGELOG_PATH)
    try:
        with open(CHANGELOG_PATH, encoding="utf-8") as f:
            entries = json.load(f).get("entries", [])
        mtime = os.path.getmtime(CHANGELOG_PATH)
    except (FileNotFoundError, ValueError):
        entries, mtime = [], None
    return {"entries": entries, "mtime": mtime, "components": CHANGELOG_COMPONENTS, "path": CHANGELOG_PATH}


def changelog_clean(raw):
    """Перевірка й нормалізація записів. Повертає (записи, помилки)."""
    out, errors, seen = [], [], set()
    if not isinstance(raw, list):
        return [], ["очікувався список записів"]
    for i, e in enumerate(raw, 1):
        if not isinstance(e, dict):
            errors.append(f"запис {i}: не об'єкт")
            continue
        c = {k: e.get(k) for k in CHANGELOG_FIELDS}
        c["id"] = str(e.get("id") or "").strip() or f"u{int(time.time() * 1000)}{i}"
        if c["id"] in seen:
            c["id"] += f"_{i}"
        seen.add(c["id"])
        c["date"] = str(c["date"] or "").strip()
        if c["date"]:
            try:
                c["date"] = journal.parse_date(c["date"])
            except ValueError as ex:
                errors.append(f"запис «{e.get('title') or i}»: {ex}")
        c["approx"] = bool(c["approx"])
        for k in ("component", "version", "title", "what", "why", "result"):
            c[k] = str(c[k] or "").strip()
        if c["component"] not in CHANGELOG_COMPONENTS:
            errors.append(f"запис «{c['title'] or i}»: компонент — один із {', '.join(CHANGELOG_COMPONENTS)}")
        if not c["title"]:
            errors.append(f"запис {i}: потрібна назва")
        tr = c["trades"] or []
        if isinstance(tr, str):
            tr = re.findall(r"\d+", tr)
        try:
            c["trades"] = sorted({int(x) for x in tr})
        except (TypeError, ValueError):
            errors.append(f"запис «{c['title']}»: номери угод — числа")
            c["trades"] = []
        out.append(c)
    return out, errors


def changelog_save(raw, base_mtime=None):
    entries, errors = changelog_clean(raw)
    if errors:
        return 400, {"ok": False, "errors": errors}
    cur = changelog_load()
    if base_mtime and cur["mtime"] and abs(float(base_mtime) - cur["mtime"]) > 1e-3:
        return 409, {"ok": False, "errors": ["історію змінено в іншому вікні — онови сторінку, щоб не затерти зміни"]}
    os.makedirs(CHANGELOG_HISTORY, exist_ok=True)
    if os.path.exists(CHANGELOG_PATH):
        shutil.copy2(CHANGELOG_PATH, os.path.join(CHANGELOG_HISTORY, f"changelog_{dt.datetime.now():%Y-%m-%d_%H%M%S_%f}.json"))
    tmp = CHANGELOG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"entries": entries}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CHANGELOG_PATH)
    ed = extra_dir()
    res = dict(changelog_load(), ok=True)
    if ed:
        try:
            d = os.path.abspath(os.path.expanduser(ed))
            os.makedirs(d, exist_ok=True)
            shutil.copy2(CHANGELOG_PATH, os.path.join(d, "changelog.json"))
        except Exception as e:
            res["extra_error"] = str(e)
    return 200, res


# ----------------------------------------------------------------------------
# Бектест (етап a: дані й кеш свічок у data/backtest/klines.db)
# ----------------------------------------------------------------------------
BT = {"dl": None, "sig": None}


def bt_status():
    dl = BT["dl"]
    s = load_settings()
    months = dl.months if dl else 12
    cache = dl.cache if dl else backtest_data.Cache(DATA_DIR)
    start_ms, end_ms = backtest_data.period(months)
    summ = cache.summary(start_ms)
    have = [x for x in summ["symbols"] if x["in_list"] and x["1h"] and x["4h"]]
    return {"progress": dl.progress() if dl else {"status": "idle"}, "cache": summ, "runs": cache.runs(8),
            "period": {"months": months, "start": start_ms, "end": end_ms,
                       "start_txt": backtest_data.fmt_ms(start_ms), "end_txt": backtest_data.fmt_ms(end_ms)},
            "ready": len(have), "screener_found": os.path.exists(screener_path(s)), "signals": sig_status(),
            "log_missing": log_missing(s, {x["symbol"] for x in summ["symbols"] if x["in_list"]})}


_LOGM = {"key": None, "rows": []}


def log_missing(s, have):
    """Монети з живого signals_log.csv («у списку» / «наживо»), яких немає серед монет бектесту."""
    path = os.path.join(results_dir(s), "signals_log.csv")
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return []
    if _LOGM["key"] != key:
        cnt = {}
        try:
            with open(path, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    k = row.get("kind") or ""
                    if k == "у списку" or k.startswith("наживо"):
                        sym = row.get("symbol") or ""
                        c = cnt.setdefault(sym, {"symbol": sym, "rows": 0, "last": ""})
                        c["rows"] += 1
                        c["last"] = max(c["last"], row.get("time") or "")
        except Exception:
            return []
        _LOGM.update(key=key, rows=sorted(cnt.values(), key=lambda x: x["last"], reverse=True))
    return [x for x in _LOGM["rows"] if x["symbol"] and x["symbol"] not in have]


def bt_add(body):
    """Додати монети в кеш бектесту (і докачати лише їх)."""
    s = load_settings()
    if BT["dl"] and BT["dl"].thread and BT["dl"].thread.is_alive():
        return 409, {"ok": False, "error": "завантаження вже йде"}
    if sig_running()[0]:
        return 409, {"ok": False, "error": "зараз іде пошук сигналів — дочекайся або зупини"}
    sp = screener_path(s)
    if not os.path.exists(sp):
        return 400, {"ok": False, "error": f"не знайдено скринер: {sp}"}
    syms = backtest_data.norm_symbols(body.get("symbols"))
    if not syms:
        return 400, {"ok": False, "error": "впиши монети, напр.: DOT, HBAR, TRXUSDT"}
    if len(syms) > 50:
        return 400, {"ok": False, "error": "за раз — до 50 монет"}
    cache = backtest_data.Cache(DATA_DIR)
    runs = [r for r in cache.runs(20) if r["status"] in ("done", "done_errors")]
    months = int(runs[0]["months"]) if runs else 12
    src = "з живого логу" if body.get("source") == "log" else "вручну"
    BT["dl"] = backtest_data.Downloader(DATA_DIR, sp, months=months, add=syms, add_source=src)
    BT["dl"].start()
    return 200, {"ok": True, "symbols": syms}


def bt_remove(body):
    if BT["dl"] and BT["dl"].thread and BT["dl"].thread.is_alive():
        return 409, {"ok": False, "error": "зачекай завершення завантаження"}
    n = backtest_data.Cache(DATA_DIR).remove_extra(str(body.get("symbol") or "").upper())
    return 200, {"ok": bool(n)}


def bt_start(body):
    s = load_settings()
    if BT["dl"] and BT["dl"].thread and BT["dl"].thread.is_alive():
        return 409, {"ok": False, "error": "завантаження вже йде"}
    sp = screener_path(s)
    if not os.path.exists(sp):
        return 400, {"ok": False, "error": f"не знайдено скринер: {sp}"}
    try:
        months = int(body.get("months") or 12)
        top = int(body.get("top") or 100)
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "місяці й кількість монет — числа"}
    if sig_running()[0]:
        return 409, {"ok": False, "error": "зараз іде пошук сигналів — дочекайся або зупини"}
    if months not in (6, 12, 24) or not 5 <= top <= 200:
        return 400, {"ok": False, "error": "період 6 / 12 / 24 міс., монет від 5 до 200"}
    BT["dl"] = backtest_data.Downloader(DATA_DIR, sp, months=months, top=top)
    BT["dl"].start()
    return 200, {"ok": True}


# ---------------------------------------------------------------- етап b: replay сигналів (окремий процес)
SIG_SCRIPT = os.path.join(APP_DIR, "backtest_signals.py")
SIG_LOG = os.path.join(DATA_DIR, "backtest", "signals.log")


def sig_running():
    """(працює?, прогрес). Процес може пережити перезапуск додатка — тоді дивимось за pid із файлу прогресу."""
    store = backtest_signals.Store(DATA_DIR)
    p = store.read_progress() or {"status": "idle"}
    if BT.get("sig") is not None and BT["sig"].poll() is not None:
        BT["sig"] = None  # прибрати завершений дочірній процес (інакше «зомбі» виглядає живим)
    alive = p.get("status") == "running" and bool(p.get("pid")) and pid_alive(p["pid"])
    if p.get("status") == "running" and not alive:
        p["status"] = "stopped"
        store.mark_dead()
    return alive, p


def sig_args(s):
    """Аргументи скринера рівно ті, з якими його запускає додаток (без caffeinate, runner і шляху)."""
    cmd = build_command(s)
    sp = screener_path(s)
    return cmd[cmd.index(sp) + 1:]


def sig_start(body):
    s = load_settings()
    if BT["dl"] and BT["dl"].thread and BT["dl"].thread.is_alive():
        return 409, {"ok": False, "error": "спершу дочекайся завершення завантаження даних"}
    if sig_running()[0]:
        return 409, {"ok": False, "error": "прохід уже йде"}
    sp = screener_path(s)
    if not os.path.exists(sp):
        return 400, {"ok": False, "error": f"не знайдено скринер: {sp}"}
    cache = backtest_data.Cache(DATA_DIR)
    runs = [r for r in cache.runs(20) if r["status"] in ("done", "done_errors")]
    if not runs:
        return 400, {"ok": False, "error": "спершу завантаж дані (крок 1)"}
    months = int(runs[0]["months"] or 12)
    cmd = [sys.executable, "-u", SIG_SCRIPT, "run", "--data", DATA_DIR, "--screener", sp, "--months", str(months)]
    if body.get("symbols"):
        cmd += ["--symbols", ",".join(re.findall(r"[A-Z0-9]+", str(body["symbols"]).upper()))]
    cmd += ["--"] + sig_args(s)
    caff = shutil.which("caffeinate") if s.get("caffeinate") else None
    if caff:
        cmd = [caff, "-i"] + cmd
    os.makedirs(os.path.dirname(SIG_LOG), exist_ok=True)
    store = backtest_signals.Store(DATA_DIR)
    with open(SIG_LOG, "a", encoding="utf-8") as logf:
        logf.write(f"===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} {display_command(cmd)}\n")
        logf.flush()
        proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                cwd=APP_DIR, start_new_session=True)
    BT["sig"] = proc
    # поки прохід не записав свій pid — тримаємо pid обгортки (caffeinate), щоб статус не «згас»
    store = backtest_signals.Store(DATA_DIR)
    p = store.read_progress() or {}
    if p.get("status") != "running" or not p.get("pid") or not pid_alive(p["pid"]):
        store.write_progress({"status": "running", "pid": proc.pid, "done": 0, "total": 0, "phase": "запуск", "events": 0,
                              "errors": [], "started": time.time()})
    return 200, {"ok": True}


def sig_stop():
    running, p = sig_running()
    if not running:
        return 200, {"ok": True}
    pids = {p.get("pid")} | ({BT["sig"].pid} if BT.get("sig") else set())
    for pid in filter(None, pids):
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    return 200, {"ok": True}


def sig_status():
    running, p = sig_running()
    store = backtest_signals.Store(DATA_DIR)
    if p.get("started") and running:
        p["elapsed_s"] = round(time.time() - p["started"])
    runs = store.runs(8)
    for r in runs:
        r.pop("params", None)
    return {"progress": p, "running": running, "runs": runs, "last_done": store.last_done()}


def sig_result(rid=None):
    """Підсумок запуску replay + звірка з живим signals_log.csv."""
    store = backtest_signals.Store(DATA_DIR)
    rid = rid or store.last_done()
    run = store.run(rid) if rid else None
    if not run:
        return 404, {"ok": False, "error": "ще немає завершеного проходу"}
    ev = store.events(rid)
    res = {"ok": True, "run": run, "params": json.loads(run.get("params") or "{}"),
           "summary": backtest_signals.summarize(ev, run["start_ms"], run["end_ms"]),
           "period_txt": f"{backtest_data.fmt_ms(run['start_ms'])} – {backtest_data.fmt_ms(run['end_ms'])}"}
    lst = sorted((e for e in ev if e["kind"] == "list"), key=lambda e: -(e["first_list_ms"] or e["first_ms"]))
    res["events"] = [{**{k: e[k] for k in ("symbol", "dir", "first_ms", "last_ms", "first_list_ms", "zone_ms", "touch_ms", "zone_low",
                                           "zone_high", "entry", "stop", "target", "rr", "score", "first_status", "appearances", "knife")},
                      **{"flags": (e["snap"] or {}).get("flags") or [], "first_txt": backtest_data.fmt_ms(e["first_list_ms"] or e["first_ms"]),
                        "zone_txt": backtest_data.fmt_ms(min(x for x in (e["zone_ms"], e["touch_ms"]) if x)) if (e["zone_ms"] or e["touch_ms"]) else "",
                         "hours": round((e["last_ms"] - e["first_ms"]) / backtest_signals.H1) + 1}}
                     for e in lst[:400]]
    log_path = os.path.join(results_dir(load_settings()), "signals_log.csv")
    if os.path.exists(log_path):
        try:
            cached = [x["symbol"] for x in backtest_data.Cache(DATA_DIR).symbols()]
            res["compare"] = backtest_signals.compare(ev, log_path, cached, run["end_ms"])
        except Exception as e:
            res["compare"] = {"ok": False, "error": f"не вдалося прочитати signals_log.csv: {e}"}
    else:
        res["compare"] = {"ok": False, "error": f"не знайдено {log_path}"}
    res["compare"]["path"] = log_path
    return 200, res


# ---------------------------------------------------------------- етап c: угоди (швидко, прямо в запиті)
TR_LOCK = threading.Lock()


def _tr_public(res):
    if res:
        ts = backtest_trades.TradeStore(DATA_DIR)
        res["runs"] = ts.runs(10)
        res["cancel_txt"] = backtest_trades.CANCEL_TXT
        res["slices"] = backtest_trades.slices(res.get("trades") or [])
        res["summary"] = backtest_trades.summary(res)
        res["slices_summary"] = backtest_trades.slices_summary(res["slices"])
        res.setdefault("filters_text", backtest_trades.filters_text(res.get("params") or {}))
        res["oos_log"] = ts.oos_log()
    return res


def oos_open(body):
    if not TR_LOCK.acquire(blocking=False):
        return 409, {"ok": False, "error": "прогін уже йде"}
    try:
        res = backtest_trades.open_oos(DATA_DIR, backtest_signals.Store(DATA_DIR), int(body.get("base_id") or 0))
    except (ValueError, TypeError) as e:
        return 400, {"ok": False, "error": str(e)}
    finally:
        TR_LOCK.release()
    return oos_get(res["id"])


def oos_get(rid=None):
    ts = backtest_trades.TradeStore(DATA_DIR)
    res = ts.get(rid, window="oos")
    log = ts.oos_log()
    if not res or res.get("window") != "oos":
        return 200, {"ok": False, "empty": True, "log": log}
    base = ts.get(res.get("base_id"))
    res = dict(res, ok=True, log=log, slices=backtest_trades.slices(res.get("trades") or []), summary=backtest_trades.summary(res))
    res["base"] = {"id": base["id"], "metrics": base["metrics"], "filters_text": base.get("filters_text")} if base else None
    res.pop("trades_full", None)
    return 200, res


def tr_run(body):
    if sig_running()[0]:
        return 409, {"ok": False, "error": "зараз іде пошук сигналів — дочекайся кінця"}
    store = backtest_signals.Store(DATA_DIR)
    sid = body.get("sig_run") or store.last_done()
    if not sid:
        return 400, {"ok": False, "error": "спершу «Знайти сигнали» (крок 2)"}
    if not TR_LOCK.acquire(blocking=False):
        return 409, {"ok": False, "error": "прогін уже йде"}
    try:
        res = backtest_trades.run(DATA_DIR, store, int(sid), body.get("params") or {})
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    finally:
        TR_LOCK.release()
    return 200, dict(_tr_public(res), ok=True)


def tr_get(rid=None):
    res = backtest_trades.TradeStore(DATA_DIR).get(rid)
    if not res:
        return 200, {"ok": False, "empty": True, "defaults": backtest_trades.DEFAULTS}
    return 200, dict(_tr_public(res), ok=True, defaults=backtest_trades.DEFAULTS)


# ---------------------------------------------------------------- GitHub: автозбереження коду
GIT = gitsync.GitSync(os.environ.get("SMC_GIT_DIR") or APP_DIR, os.path.join(DATA_DIR, "git_state.json"),
                      enabled=lambda: bool(load_settings().get("git_autopush")), version=APP_VERSION,
                      interval=float(os.environ.get("SMC_GIT_INTERVAL") or 60))


# ---------------------------------------------------------------- 4h + 1h + 15m: лайв-графіки й уточнення входу на 15m
def ltf_rows():
    """Сигнали «у списку» з останнього скану (не старші за 3 години — інакше скринер, імовірно, не працює)."""
    if not load_settings().get("ltf_enabled", True):
        return []
    res = read_results(load_settings())
    if res.get("source") != "app" or res.get("age_s", 1e9) > 3 * 3600:
        return []
    return [r for r in (res.get("data") or {}).get("candidates", []) if r.get("symbol") and r.get("zone_low")]


LTF = ltf.LtfWatcher(DATA_DIR, ltf_rows, lambda t, x: notify_mac(t, x),
                     get_tol=lambda: float(load_settings().get("entry_tol") or 0),
                     interval=float(os.environ.get("SMC_LTF_INTERVAL") or 60))


def klines_api(q):
    sym = ((q.get("symbol") or [""])[0]).upper()
    tf = (q.get("tf") or ["15m"])[0]
    try:
        limit = int((q.get("limit") or ["200"])[0])
    except ValueError:
        limit = 200
    if not re.fullmatch(r"[A-Z0-9]{2,30}", sym):
        return 400, {"ok": False, "error": "некоректна монета"}
    try:
        return 200, {"ok": True, "symbol": sym, "tf": tf, "bars": ltf.fetch_klines(sym, tf, limit)}
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    except Exception as e:
        # 200, а не 502: інтерфейс сам покаже «немає свічок» (без червоних помилок у консолі браузера)
        return 200, {"ok": False, "error": f"Binance недоступний: {e}"}


# ---------------------------------------------------------------- Спот: «шансові» монети (денні свічки)
SPOT = spot_scan.SpotScanner(DATA_DIR)


def spot_state():
    lat = SPOT.latest()
    return {"latest": lat, "progress": SPOT.progress, "running": SPOT.running(), "defaults": spot_scan.DEFAULTS,
            "auto": bool(load_settings().get("spot_auto"))}


def spot_start(body):
    try:
        params = body.get("params")
        if params is None:
            lat = SPOT.latest()
            params = (lat or {}).get("params") or {}
        started = SPOT.start(params)
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    if not started:
        return 409, {"ok": False, "error": "скан уже йде"}
    return 200, dict(spot_state(), ok=True)


def spot_auto_loop(every=3600, max_age_h=24):
    """Раз на годину: якщо автоскан увімкнено, а останньому скану понад добу — сканувати з тими ж критеріями."""
    while True:
        time.sleep(every)
        try:
            if not load_settings().get("spot_auto") or SPOT.running():
                continue
            lat = SPOT.latest()
            age = None
            if lat and lat.get("generated"):
                age = (dt.datetime.now() - dt.datetime.fromisoformat(lat["generated"])).total_seconds() / 3600
            if lat is None or age is None or age >= max_age_h:
                SPOT.start((lat or {}).get("params") or {})
        except Exception:
            pass


def extra_dir():
    return load_settings().get("backup_extra_dir") or None


def sync_pending(overwrite_n=None):
    """Дописує в Google Таблицю всі угоди, які ще не там (по порядку номерів, щоб не було пропусків)."""
    if not load_settings().get("sheet_sync"):
        return {"ok": True, "disabled": True, "configured": True, "synced": []}
    url, token = journal.sheet_config()
    if not url or not token:
        return {"ok": False, "configured": False, "synced": [],
                "error": "запис у таблицю вимкнено: не задано SMC_SHEET_URL / SMC_SHEET_TOKEN"}
    synced = []
    with SYNC_LOCK:
        for t in JOURNAL.rows():
            if t["sheet_status"] == "ok":
                continue
            overwrite = t["n"] == overwrite_n or t["source"] == "import" or bool(t["sheet_synced_at"])
            try:
                journal.sheet_call("saveTrade", trade=journal.sheet_payload(t, t["n"], overwrite))
            except journal.SheetError as e:
                JOURNAL.set_sync(t["n"], False, e)
                return {"ok": False, "configured": True, "synced": synced, "n": t["n"], "error": str(e)}
            JOURNAL.set_sync(t["n"], True)
            synced.append(t["n"])
    return {"ok": True, "configured": True, "synced": synced}


def journal_save(s, body):
    rd = results_dir(s)
    n_edit = body.get("n")
    t, errors = journal.normalize(body.get("trade") or {})
    if errors:
        return 400, {"ok": False, "errors": errors}
    existing = None
    if n_edit is not None:
        existing = JOURNAL.get(int(n_edit))
        if not existing:
            return 404, {"ok": False, "errors": [f"угоду №{n_edit} не знайдено"]}
    sig = None
    if t["signal_id"]:
        sig = journal.find_signal(rd, t["signal_id"], t["date"])
        if sig is None and existing and existing.get("signal_json") and existing.get("signal_id") == t["signal_id"]:
            sig = json.loads(existing["signal_json"])  # лог могли почистити — лишаємо збережений знімок
        if sig is None:
            return 400, {"ok": False, "errors": ["сигнал не знайдено в signals_log.csv"]}
    t["signal_json"] = json.dumps(sig, ensure_ascii=False) if sig else None
    matches = [] if sig else journal.find_matches(rd, t)
    chk = journal.check_trade(t, sig, matches)
    hard = [i["text"] for i in chk["items"] if i["level"] == "error"]
    if hard:
        return 400, {"ok": False, "errors": hard}
    t["checks_json"] = json.dumps(chk["items"], ensure_ascii=False)
    snap_err = JOURNAL.daily_snapshot(extra_dir())  # копія стану ДО зміни (раз на день)
    if existing:
        n = existing["n"]
        JOURNAL.update(n, t)
    else:
        n = JOURNAL.insert(t)
    sync = sync_pending(overwrite_n=n if existing else None)
    return 200, {"ok": True, "n": n, "sync": sync, "backup_error": JOURNAL.backup(extra_dir()) or snap_err, "checks": chk}


LINK_DISMISSED = os.path.join(DATA_DIR, "link_dismissed.json")


def _dismissed():
    try:
        with open(LINK_DISMISSED, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def link_suggestions(s):
    """Угоди без прив'язаного сигналу, для яких у signals_log.csv є схожий сигнал (монета, напрям, вхід біля зони, дати)."""
    rd = results_dir(s)
    dis = _dismissed()
    out, checked = [], 0
    for t in JOURNAL.rows():
        if t.get("signal_id"):
            continue
        checked += 1
        ms = [m for m in journal.find_matches(rd, t) if f"{t['n']}|{m['id']}" not in dis]
        if ms:
            out.append({"n": t["n"], "date": t["date"], "instrument": t["instrument"], "dir": t["dir"],
                        "entry": t["entry"], "stop": t["stop"], "target": t["target"], "result": t["result"],
                        "by_screener": t["by_screener"], "matches": ms})
    return {"suggestions": out, "checked": checked}


def journal_link_clear(s):
    """«Очистити список»: усі поточні пропозиції позначаються «Не те». Для нових угод пошук працює як раніше."""
    cur = link_suggestions(s)["suggestions"]
    d = _dismissed()
    pairs = {f"{t['n']}|{m['id']}" for t in cur for m in t["matches"]}
    d |= pairs
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LINK_DISMISSED, "w", encoding="utf-8") as f:
        json.dump(sorted(d), f, ensure_ascii=False)
    return 200, {"ok": True, "cleared": len(cur)}


def journal_link(s, body):
    n = int(body.get("n") or 0)
    sid = str(body.get("signal_id") or "")
    t = JOURNAL.get(n)
    if not t:
        return 404, {"ok": False, "error": f"угоди №{n} немає"}
    if body.get("dismiss"):
        d = _dismissed()
        d.add(f"{n}|{sid}")
        with open(LINK_DISMISSED, "w", encoding="utf-8") as f:
            json.dump(sorted(d), f, ensure_ascii=False)
        return 200, {"ok": True, "dismissed": True}
    sig = journal.find_signal(results_dir(s), sid, t["date"])
    if not sig:
        return 404, {"ok": False, "error": "сигнал не знайдено в signals_log.csv"}
    chk = journal.check_trade(t, sig)
    fields = {"signal_id": sid, "signal_json": json.dumps(sig, ensure_ascii=False),
              "checks_json": json.dumps(chk["items"], ensure_ascii=False)}
    if body.get("by_screener") in ("так", "ні"):
        fields["by_screener"] = body["by_screener"]
    JOURNAL.daily_snapshot(extra_dir())
    JOURNAL.update(n, fields)
    return 200, {"ok": True, "n": n, "signal": sig, "backup_error": JOURNAL.backup(extra_dir())}


def journal_delete(body):
    n = int(body.get("n") or 0)
    t = JOURNAL.get(n)
    if not t:
        return 404, {"ok": False, "error": f"угоди №{n} немає"}
    if n != JOURNAL.next_n() - 1:
        return 400, {"ok": False, "error": "видаляти можна лише останню угоду (щоб номери в додатку й таблиці не розійшлися)"}
    if load_settings().get("sheet_sync") and (t["sheet_status"] == "ok" or t["sheet_synced_at"]):
        try:
            with SYNC_LOCK:
                journal.sheet_call("clearTrade", n=n, instrument=t["instrument"])
        except journal.SheetError as e:
            if not body.get("local_only"):
                return 502, {"ok": False, "error": f"не вдалося очистити рядок у таблиці: {e}", "can_local": True}
    JOURNAL.daily_snapshot(extra_dir())
    JOURNAL.delete(n)
    return 200, {"ok": True, "n": n, "backup_error": JOURNAL.backup(extra_dir())}


def journal_import():
    try:
        with SYNC_LOCK:
            d = journal.sheet_call("listTrades")
    except journal.SheetError as e:
        return 502, {"ok": False, "error": str(e)}
    JOURNAL.daily_snapshot(extra_dir())
    res = JOURNAL.import_sheet(d.get("trades") or [])
    res["backup_error"] = JOURNAL.backup(extra_dir())
    res["ok"] = True
    return 200, res


def journal_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(journal.Journal.CSV_HEAD)
    for r in JOURNAL.csv_rows():
        w.writerow(["" if x is None else x for x in r])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
SCREENER = None
SERVER = None


class Handler(BaseHTTPRequestHandler):
    server_version = "SMCApp/" + APP_VERSION.split()[0]

    def log_message(self, fmt, *args):  # тиша в терміналі, крім помилок
        if args and str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # захист: лише localhost (від DNS rebinding) і власний заголовок на POST (від чужих сайтів у браузері)
    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ValueError("завеликий запит")
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "лише localhost"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        s = load_settings()
        if u.path == "/api/status":
            st = SCREENER.status()
            st.update(last_scan_info())
            age, stale = scan_age(st, s)
            st["last_scan_age_s"] = None if age is None else round(age)
            st["stale"] = stale
            st["env"] = environment_info(s)
            return self._send(200, st)
        if u.path == "/api/results":
            since = None
            if q.get("since"):
                try:
                    since = float(q["since"][0])
                except ValueError:
                    pass
            return self._send(200, read_results(s, since))
        if u.path == "/api/log":
            n = min(2000, max(10, int((q.get("n") or ["300"])[0] or 300)))
            return self._send(200, {"lines": tail(LOG_PATH, n), "path": LOG_PATH})
        if u.path == "/api/journal":
            trades = JOURNAL.list()
            url, token = journal.sheet_config()
            return self._send(200, {"trades": trades, "summary": JOURNAL.summary(trades), "next_n": JOURNAL.next_n(),
                                    "options": {"management": journal.MANAGEMENT, "zone_types": journal.ZONE_TYPES},
                                    "sheet_configured": bool(url and token), "db": JOURNAL.path,
                                    "sheet_sync": bool(s.get("sheet_sync")), "backup_extra_dir": s.get("backup_extra_dir") or "",
                                    "backup_dir": JOURNAL.backup_dir})
        if u.path == "/api/stats":
            period = (q.get("period") or ["all"])[0]
            if period not in ("all", "7", "30", "90", "month"):
                period = "all"
            return self._send(200, stats.build(JOURNAL.list(), period))
        if u.path == "/api/backtest/status":
            return self._send(200, bt_status())
        if u.path == "/api/klines":
            code, res = klines_api(q)
            return self._send(code, res)
        if u.path == "/api/ltf":
            return self._send(200, {"state": LTF.state, "last_run": LTF.last_run, "error": LTF.error,
                                    "enabled": bool(load_settings().get("ltf_enabled", True))})
        if u.path == "/api/spot":
            return self._send(200, spot_state())
        if u.path == "/api/git/status":
            return self._send(200, GIT.status())
        if u.path == "/api/backtest/oos":
            try:
                rid = int((q.get("run") or ["0"])[0]) or None
            except ValueError:
                rid = None
            code, res = oos_get(rid)
            return self._send(code, res)
        if u.path == "/api/backtest/trades":
            try:
                rid = int((q.get("run") or ["0"])[0]) or None
            except ValueError:
                rid = None
            code, res = tr_get(rid)
            return self._send(code, res)
        if u.path == "/api/backtest/signals":
            try:
                rid = int((q.get("run") or ["0"])[0]) or None
            except ValueError:
                rid = None
            code, res = sig_result(rid)
            return self._send(code, res)
        if u.path == "/api/changelog":
            return self._send(200, changelog_load())
        if u.path == "/api/strategy":
            return self._send(200, strategy_load())
        if u.path == "/api/strategy/version":
            name = (q.get("name") or [""])[0]
            if not re.fullmatch(r"strategy_[\d_-]+\.md", name) or not os.path.isfile(os.path.join(STRATEGY_HISTORY, name)):
                return self._send(404, {"error": "версію не знайдено"})
            with open(os.path.join(STRATEGY_HISTORY, name), encoding="utf-8") as f:
                return self._send(200, {"name": name, "text": f.read()})
        if u.path == "/api/journal/link_suggestions":
            return self._send(200, link_suggestions(s))
        if u.path == "/api/journal/backups":
            ed = s.get("backup_extra_dir") or ""
            return self._send(200, {"backups": JOURNAL.list_backups(), "dir": JOURNAL.backup_dir,
                                    "extra_dir": os.path.abspath(os.path.expanduser(ed)) if ed else "",
                                    "icloud": os.path.isdir(ICLOUD_DIR),
                                    "icloud_suggest": os.path.join(ICLOUD_DIR, "SMC_App_backup")})
        if u.path == "/api/journal/backup_file":
            name = (q.get("name") or [""])[0]
            p = JOURNAL.backup_path(name)
            if not p:
                return self._send(404, {"error": "копію не знайдено"})
            with open(p, "rb") as f:
                data = f.read()
            ctype = "text/csv; charset=utf-8" if name.endswith(".csv") else "application/octet-stream"
            return self._send(200, data, ctype, {"Content-Disposition": f'attachment; filename="{name}"'})
        if u.path == "/api/journal/sheet":
            return self._send(200, journal.sheet_status(force=bool(q.get("force"))))
        if u.path == "/api/journal/signals":
            try:
                days = max(0.5, min(60.0, float((q.get("days") or ["7"])[0])))
            except ValueError:
                days = 7.0
            rej = (q.get("rejected") or ["0"])[0] == "1"
            sigs, path = journal.recent_signals(results_dir(s), days, rej)
            return self._send(200, {"signals": sigs, "path": path, "exists": os.path.exists(path)})
        if u.path == "/api/journal/export.csv":
            name = f"journal_{dt.date.today().isoformat()}.csv"
            return self._send(200, journal_csv(), "text/csv; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="{name}"'})
        if u.path == "/api/settings":
            return self._send(200, {"settings": s, "defaults": DEFAULTS, "command": display_command(build_command(s)),
                                    "env": environment_info(s)})
        return self._static(u.path)

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-SMC-App") != "1":
            return self._send(403, {"error": "заборонено"})
        u = urlparse(self.path)
        try:
            body = self._json_body()
        except Exception as e:
            return self._send(400, {"ok": False, "error": f"некоректний запит: {e}"})
        s = load_settings()
        if u.path == "/api/start":
            ok, msg = SCREENER.start(s)
            return self._send(200 if ok else 409, {"ok": ok, "message": msg})
        if u.path == "/api/stop":
            ok, msg = SCREENER.stop()
            return self._send(200 if ok else 409, {"ok": ok, "message": msg})
        if u.path == "/api/settings":
            new, errors = validate_settings(body.get("settings") or {})
            if errors:
                return self._send(400, {"ok": False, "errors": errors})
            save_settings(new)
            note = "Збережено." + (" Нові параметри діятимуть після перезапуску скринера." if SCREENER.status()["running"] else "")
            return self._send(200, {"ok": True, "message": note, "settings": new, "command": display_command(build_command(new))})
        if u.path == "/api/settings/reset":
            save_settings(dict(DEFAULTS))
            return self._send(200, {"ok": True, "settings": DEFAULTS})
        if u.path == "/api/selftest":
            return self._send(200, run_selftest(s))
        if u.path == "/api/telegram/connect":
            try:
                chat, name = tg.find_chat()
                if not chat:
                    return self._send(400, {"ok": False, "error": "бот ще не бачив твого повідомлення: відкрий бота в Telegram, натисни Start (або напиши йому будь-що) і спробуй ще раз"})
                bot = tg.bot_name()
                tg.send(chat, f"✅ <b>SMC App підключено</b>\nСюди приходитимуть алерти скринера (НОВА ПАРА, ЦІНА В ЗОНІ) і службові сповіщення.")
            except tg.TgError as e:
                return self._send(400, {"ok": False, "error": str(e)})
            new = dict(s, tg_chat=str(chat), tg_name=name or "", tg_enabled=True)
            save_settings(new)
            run = SCREENER.status()["running"]
            return self._send(200, {"ok": True, "chat": str(chat), "name": name, "bot": bot, "screener_running": run})
        if u.path == "/api/telegram/test":
            chat = tg_chat(s)
            if not chat:
                return self._send(400, {"ok": False, "error": "Telegram не підключено або вимкнено"})
            try:
                tg.send(chat, tg.format_alert("SMC-скринер", "НОВА ПАРА: TESTUSDT Short | бали 2 | підхід до зони | вхід 1.000 стоп 1.020 ціль 0.930 R:R 3.5 | УВАГА: це тестове повідомлення", "new", "BINANCE:BTCUSDT.P"))
            except tg.TgError as e:
                return self._send(400, {"ok": False, "error": str(e)})
            return self._send(200, {"ok": True})
        if u.path == "/api/telegram/disconnect":
            save_settings(dict(s, tg_chat="", tg_name=""))
            return self._send(200, {"ok": True})
        if u.path == "/api/journal/link_clear":
            code, res = journal_link_clear(s)
            return self._send(code, res)
        if u.path == "/api/journal/link":
            code, res = journal_link(s, body)
            return self._send(code, res)
        if u.path == "/api/journal/check":
            t, _ = journal.normalize(body.get("trade") or {})
            rd = results_dir(s)
            sig = journal.find_signal(rd, t["signal_id"], t["date"]) if t["signal_id"] else None
            matches = [] if sig else journal.find_matches(rd, t)
            return self._send(200, journal.check_trade(t, sig, matches))
        if u.path == "/api/journal/save":
            code, res = journal_save(s, body)
            return self._send(code, res)
        if u.path == "/api/journal/sync":
            return self._send(200, sync_pending())
        if u.path == "/api/journal/import":
            code, res = journal_import()
            return self._send(code, res)
        if u.path == "/api/journal/backup":
            try:
                name, err = JOURNAL.manual_backup(extra_dir())
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})
            return self._send(200, {"ok": True, "name": name, "extra_error": err})
        if u.path == "/api/backtest/data/start":
            code, res = bt_start(body)
            return self._send(code, res)
        if u.path == "/api/spot/scan":
            code, res = spot_start(body)
            return self._send(code, res)
        if u.path == "/api/git/push":
            GIT.sync(force=True)
            return self._send(200, GIT.status())
        if u.path == "/api/backtest/oos/open":
            code, res = oos_open(body)
            return self._send(code, res)
        if u.path == "/api/backtest/trades/run":
            code, res = tr_run(body)
            return self._send(code, res)
        if u.path == "/api/backtest/signals/start":
            code, res = sig_start(body)
            return self._send(code, res)
        if u.path == "/api/backtest/signals/stop":
            code, res = sig_stop()
            return self._send(code, res)
        if u.path == "/api/backtest/data/add":
            code, res = bt_add(body)
            return self._send(code, res)
        if u.path == "/api/backtest/data/remove":
            code, res = bt_remove(body)
            return self._send(code, res)
        if u.path == "/api/backtest/data/stop":
            if BT["dl"]:
                BT["dl"].stop()
            return self._send(200, {"ok": True})
        if u.path == "/api/changelog":
            code, res = changelog_save(body.get("entries"), body.get("base_mtime"))
            return self._send(code, res)
        if u.path == "/api/strategy":
            code, res = strategy_save(body.get("text"), body.get("base_mtime"))
            return self._send(code, res)
        if u.path == "/api/shutdown":
            # закрити сам додаток (сервер); скринер, якщо працює, НЕ зупиняється
            threading.Timer(0.3, SERVER.shutdown).start()
            return self._send(200, {"ok": True, "screener_running": SCREENER.status()["running"]})
        if u.path == "/api/journal/restore":
            try:
                res = JOURNAL.restore(str(body.get("name") or ""), extra_dir())
            except Exception as e:
                return self._send(400, {"ok": False, "error": str(e)})
            return self._send(200, dict(res, ok=True))
        if u.path == "/api/journal/delete":
            code, res = journal_delete(body)
            return self._send(code, res)
        return self._send(404, {"error": "немає такого шляху"})

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.normpath(os.path.join(STATIC_DIR, path.lstrip("/")))
        if not full.startswith(STATIC_DIR + os.sep) or not os.path.isfile(full):
            return self._send(404, "Не знайдено", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)


def main():
    global SCREENER, JOURNAL, SERVER
    p = argparse.ArgumentParser(description="SMC App: скринер + журнал на localhost")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="не відкривати браузер автоматично")
    a = p.parse_args()
    mimetypes.add_type("application/javascript", ".js")
    os.makedirs(DATA_DIR, exist_ok=True)
    SCREENER = Screener()
    JOURNAL = journal.Journal(DATA_DIR)
    ed = load_settings().get("backup_extra_dir") or None
    for err in (JOURNAL.daily_snapshot(ed), JOURNAL.backup(ed) if JOURNAL.rows() else None):
        if err:
            print(f"Увага: резервна копія журналу: {err}")
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    except OSError as e:
        print(f"Порт {a.port} зайнятий ({e}). Можливо, додаток уже запущено: відкрий http://127.0.0.1:{a.port}")
        if not a.no_browser:
            webbrowser.open(f"http://127.0.0.1:{a.port}")
        sys.exit(1)
    srv.daemon_threads = True
    SERVER = srv
    url = f"http://127.0.0.1:{a.port}"
    print(f"SMC App {APP_VERSION}: {url}  (зупинити додаток: Ctrl+C)")
    if SCREENER.status()["running"]:
        print(f"Скринер уже працює з попереднього запуску (pid {SCREENER.state.get('pid')}), підхоплюю його.")
    threading.Thread(target=watchdog, daemon=True).start()
    GIT.start()
    threading.Thread(target=spot_auto_loop, daemon=True).start()
    LTF.start()
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        if SCREENER.status()["running"]:
            print("\nДодаток закрито. Скринер ПРОДОВЖУЄ працювати у фоні; зупинити: знову відкрий додаток → «Стоп».")
        else:
            print("\nДодаток закрито.")


if __name__ == "__main__":
    main()
