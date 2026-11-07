# -*- coding: utf-8 -*-
"""
Telegram-алерти для SMC App: бот лише НАДСИЛАЄ повідомлення тобі (без команд).

Токен бота — у змінній середовища SMC_TG_TOKEN (рядок у ~/.zshrc), не в коді й не в налаштуваннях.
Номер чату (chat id) не секретний: додаток знаходить його сам («Налаштування → Telegram → Підключити»)
і передає скринеру через змінну SMC_TG_CHAT.

Надсилання — у фоновому потоці з тайм-аутом: збій Telegram чи мережі ніколи не зупиняє скринер.
"""
import html
import json
import os
import threading
import time
import urllib.parse
import urllib.request

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

API = os.environ.get("SMC_TG_API", "https://api.telegram.org")  # SMC_TG_API — лише для тестів
_last_err = {"text": None, "at": 0}


class TgError(Exception):
    pass


def token():
    return os.environ.get("SMC_TG_TOKEN", "").strip()


def _call(method, params, timeout=12):
    tok = token()
    if not tok:
        raise TgError("не задано SMC_TG_TOKEN (рядок у ~/.zshrc)")
    url = f"{API}/bot{tok}/{method}"
    try:
        if requests is not None:
            r = requests.post(url, data=params, timeout=timeout)
            code, body = r.status_code, r.text
        else:  # pragma: no cover
            data = urllib.parse.urlencode(params).encode()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=timeout) as resp:
                    code, body = resp.status, resp.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                code, body = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        raise TgError(f"немає зв'язку з Telegram ({e.__class__.__name__})")
    try:
        d = json.loads(body)
    except ValueError:
        raise TgError(f"Telegram відповів не JSON (HTTP {code})")
    if not d.get("ok"):
        desc = d.get("description") or f"HTTP {code}"
        if code == 401:
            desc = "невірний токен бота (перевір SMC_TG_TOKEN)"
        elif code == 403:
            desc = "бот не може тобі писати: відкрий бота в Telegram і натисни Start"
        raise TgError(desc)
    return d.get("result")


def send(chat_id, text, html_mode=True, timeout=12):
    """Надіслати одразу (з помилкою, якщо не вдалося)."""
    if not chat_id:
        raise TgError("Telegram не підключено (немає chat id)")
    p = {"chat_id": str(chat_id), "text": text[:4000], "disable_web_page_preview": "true"}
    if html_mode:
        p["parse_mode"] = "HTML"
    return _call("sendMessage", p, timeout)


def send_bg(chat_id, text, html_mode=True, log=print):
    """Надіслати у фоні; помилки лише в лог (не частіше ніж раз на 10 хв однакова)."""
    if not chat_id or not token():
        return

    def run():
        try:
            send(chat_id, text, html_mode)
            _last_err["text"] = None
        except Exception as e:
            msg = str(e)
            if msg != _last_err["text"] or time.time() - _last_err["at"] > 600:
                log(f"  [Telegram] не вдалося надіслати: {msg}")
            _last_err.update(text=msg, at=time.time())

    threading.Thread(target=run, daemon=True).start()


def find_chat():
    """Останній особистий чат, який писав боту (після натискання Start). Повертає (chat_id, ім'я) або (None, None)."""
    upd = _call("getUpdates", {"limit": 50, "timeout": 0}) or []
    for u in reversed(upd):
        m = u.get("message") or u.get("edited_message") or u.get("my_chat_member") or {}
        chat = m.get("chat") or {}
        if chat.get("type") == "private" and chat.get("id"):
            name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username") or ""
            return chat["id"], name
    return None, None


def bot_name():
    me = _call("getMe", {})
    return (me or {}).get("username")


def tv_link(ticker):
    """'BINANCE:XXXUSDT.P' -> посилання на 4h-графік TradingView."""
    if not ticker:
        return ""
    return "https://www.tradingview.com/chart/?symbol=" + urllib.parse.quote(ticker, safe="") + "&interval=240"


def format_alert(title, text, kind="new", ticker=""):
    """Повідомлення скринера -> HTML для Telegram. text — рядок алерту скринера («НОВА ПАРА: …»)."""
    icon = "🔴" if kind == "zone" else "🆕"
    head, _, rest = text.partition(" | ")
    parts = [f"{icon} <b>{html.escape(head)}</b>"]
    for chunk in rest.split(" | "):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith("УВАГА:"):
            parts.append("⚠️ " + html.escape(chunk))
        else:
            parts.append(html.escape(chunk))
    link = tv_link(ticker)
    if link:
        parts.append(f'<a href="{link}">Графік 4h у TradingView</a>')
    return "\n".join(parts)
