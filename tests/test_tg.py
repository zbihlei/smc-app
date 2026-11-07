#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram-алерти: фейковий Bot API на localhost, справжній smc_screener.py (через --test-alert), інтерфейс.
Запуск: python3 tests/test_tg.py ШЛЯХ_ДО_smc_screener.py"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright, expect

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SCREENER = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(APP, "smc_screener.py")
TOKEN = "123:TEST"
SENT, STATE = [], {"updates": []}
ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


class Bot(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode()).items()}
        path = self.path
        if not path.startswith(f"/bot{TOKEN}/"):
            return self._out(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
        m = path.rsplit("/", 1)[1]
        if m == "getMe":
            return self._out(200, {"ok": True, "result": {"username": "smc_test_bot"}})
        if m == "getUpdates":
            return self._out(200, {"ok": True, "result": STATE["updates"]})
        if m == "sendMessage":
            SENT.append(form)
            return self._out(200, {"ok": True, "result": {"message_id": len(SENT)}})
        return self._out(404, {"ok": False, "description": "no"})

    def _out(self, code, o):
        b = json.dumps(o).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


srv = ThreadingHTTPServer(("127.0.0.1", 8789), Bot)
threading.Thread(target=srv.serve_forever, daemon=True).start()
API = "http://127.0.0.1:8789"
ENV = dict(os.environ, SMC_TG_API=API, SMC_TG_TOKEN=TOKEN, NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    ENV.pop(k, None)
os.environ.update({k: v for k, v in ENV.items() if k.startswith(("SMC_", "NO_", "no_"))})
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(k, None)

# ================================================================ 1. модуль tg
sys.path.insert(0, APP)
import tg  # noqa: E402
tg.API = API
txt = tg.format_alert("SMC-скринер", "ЦІНА В ЗОНІ (наживо): SOLUSDT Short | ціна 95.1 | вхід 94.84 стоп 97.13 ціль 87 R:R 3.4 | УВАГА: ніж: різка свічка <x>",
                      "zone", "BINANCE:SOLUSDT.P")
check("формат: 🔴, жирний заголовок, ⚠️ для «УВАГА», HTML екрановано, посилання на 4h TradingView",
      txt.startswith("🔴 <b>ЦІНА В ЗОНІ (наживо): SOLUSDT Short</b>") and "⚠️ УВАГА" in txt and "&lt;x&gt;" in txt
      and "symbol=BINANCE%3ASOLUSDT.P&amp;interval=240" not in txt and "symbol=BINANCE%3ASOLUSDT.P&interval=240" in txt, txt)
try:
    tg.find_chat()
    check("немає повідомлень боту -> find_chat None", tg.find_chat() == (None, None))
except Exception as e:
    check("find_chat без повідомлень", False, e)
os.environ["SMC_TG_TOKEN"] = "bad"
try:
    tg.send(1, "x")
    check("невірний токен -> зрозуміла помилка", False)
except tg.TgError as e:
    check("невірний токен -> зрозуміла помилка", "невірний токен" in str(e), str(e))
os.environ["SMC_TG_TOKEN"] = TOKEN

# ================================================================ 2. справжній скринер: --test-alert іде в Telegram
SENT.clear()
r = subprocess.run([sys.executable, os.path.join(APP, "screener_runner.py"), SCREENER, "--test-alert"],
                   env=dict(ENV, SMC_TG_CHAT="555"), capture_output=True, text=True, timeout=60)
time.sleep(1.5)
check("скринер (--test-alert): 2 алерти пішли в Telegram (НОВА ПАРА і ЦІНА В ЗОНІ)",
      len(SENT) == 2 and SENT[0]["chat_id"] == "555" and "🆕" in SENT[0]["text"] and "🔴" in SENT[1]["text"]
      and SENT[0]["parse_mode"] == "HTML", (r.stdout[-300:], r.stderr[-300:], SENT))
SENT.clear()
subprocess.run([sys.executable, os.path.join(APP, "screener_runner.py"), SCREENER, "--test-alert"],
               env=dict(ENV, SMC_TG_CHAT=""), capture_output=True, text=True, timeout=60)
time.sleep(1)
check("без SMC_TG_CHAT скринер у Telegram нічого не шле", SENT == [])
r = subprocess.run([sys.executable, os.path.join(APP, "screener_runner.py"), SCREENER, "--test-alert"],
                   env=dict(ENV, SMC_TG_CHAT="555", SMC_TG_API="http://127.0.0.1:9"), capture_output=True, text=True, timeout=60)
check("Telegram недоступний -> скринер не падає, лише рядок у лозі", r.returncode == 0 and "[Telegram] не вдалося" in r.stdout, r.stdout[-400:])

# ================================================================ 3. додаток: підключення в інтерфейсі, тест, наглядач
PORT = 8788
data = tempfile.mkdtemp()
with open(os.path.join(data, "settings.json"), "w") as f:
    json.dump({"screener_path": SCREENER, "results_dir": tempfile.mkdtemp()}, f)
app = subprocess.Popen([sys.executable, os.path.join(APP, "app.py"), "--port", str(PORT), "--no-browser"],
                       env=dict(ENV, SMC_APP_DATA=data), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        pg.on("dialog", lambda d: d.accept())
        pg.goto(f"http://127.0.0.1:{PORT}")
        pg.click(".tab[data-tab=settings]")
        expect(pg.locator("#tgState")).to_contain_text("натисни Start")
        check("токен є, чату ще немає: підказка натиснути Start", pg.locator("#tgTest").is_disabled())
        pg.click("#tgConnect")
        expect(pg.locator("#tgMsg")).to_contain_text("бот ще не бачив")
        check("Підключити до Start: зрозуміла помилка", True)
        STATE["updates"] = [{"update_id": 1, "message": {"chat": {"id": 777, "type": "private", "first_name": "Michel"}, "text": "/start"}}]
        SENT.clear()
        pg.click("#tgConnect")
        expect(pg.locator("#tgMsg")).to_contain_text("Підключено ✓")
        expect(pg.locator("#tgState")).to_contain_text("Michel")
        check("Підключити: знайдено чат 777, надіслано «SMC App підключено»", SENT and SENT[-1]["chat_id"] == "777" and "підключено" in SENT[-1]["text"])
        st = json.load(open(os.path.join(data, "settings.json")))
        check("у налаштуваннях лише chat id, токена там немає", st["tg_chat"] == "777" and TOKEN not in json.dumps(st))
        pg.click("#tgTest")
        expect(pg.locator("#tgMsg")).to_contain_text("Тест надіслано")
        check("Надіслати тест: приклад алерту з посиланням на TradingView", "TESTUSDT" in SENT[-1]["text"] and "tradingview" in SENT[-1]["text"])
        pg.screenshot(path=os.path.join(data, "tg_settings.png"))
        pg.uncheck("input[name=tg_enabled]")
        pg.click("#setForm button[type=submit]")
        expect(pg.locator("#setMsg")).to_contain_text("Збережено")
        expect(pg.locator("#tgState")).to_contain_text("надсилання вимкнено")
        check("вимкнення надсилання через галочку", pg.locator("#tgTest").is_disabled())
        pg.check("input[name=tg_enabled]")
        pg.click("#setForm button[type=submit]")
        expect(pg.locator("#tgState")).not_to_contain_text("вимкнено")
        # скринер, запущений з додатка, отримує chat id
        cmd_env = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import app; print(app.tg_chat({'tg_enabled': True, 'tg_chat': '777'}))" % APP],
                                 env=ENV, capture_output=True, text=True).stdout.strip()
        check("скринер з додатка отримає SMC_TG_CHAT=777", cmd_env == "777", cmd_env)
        check("помилок у консолі немає", not errors, errors[:3])
        b.close()
    # сповіщення наглядача теж ідуть у Telegram
    sys.path.insert(0, APP)
    import app as A
    A.DATA_DIR = data
    A.SETTINGS_PATH = os.path.join(data, "settings.json")
    A.LOG_PATH = os.path.join(data, "screener.log")
    SENT.clear()
    A.notify_mac("SMC App: скринер зупинився", "Скринер завершився сам о 12:00:00.")
    time.sleep(1.5)
    check("службове сповіщення «скринер зупинився» дублюється в Telegram", SENT and "⚠️ <b>SMC App: скринер зупинився</b>" in SENT[-1]["text"], SENT)
finally:
    app.terminate()
    srv.shutdown()

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"))
sys.exit(0 if ok else 1)
