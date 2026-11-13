#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.0 Rulebound: профіль бота (назва, описи, фото) — кожен крок окремо; лого в шапці. Запуск: python3 tests/test_brand.py"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
import tg  # noqa: E402

ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


calls, ups = [], []
tg._call = lambda m, p, timeout=12: calls.append((m, p)) or True
tg._upload = lambda m, p, files, timeout=30: ups.append((m, p, list(files))) or True
jpg = os.path.join(APP, "static", "brand", "bot.jpg")
r = tg.set_brand(jpg)
check("назва «Rulebound», короткий і повний опис, фото — усі 4 кроки", r == {"done": ["назва", "короткий опис", "опис", "фото"], "errors": {}}, r)
check("setMyName з назвою Rulebound", ("setMyName", {"name": "Rulebound"}) in calls)
check("фото: setMyProfilePhoto, static, attach://pic", ups and ups[0][0] == "setMyProfilePhoto" and '"attach://pic"' in ups[0][1]["photo"] and ups[0][2] == ["pic"], ups)
check("файл лого для бота є (JPEG 640×640)", os.path.getsize(jpg) > 5000 and open(jpg, "rb").read(2) == b"\xff\xd8")


def boom(m, p, files, timeout=30):
    raise tg.TgError("Not Found: method not found")


tg._upload = boom
r = tg.set_brand(jpg)
check("фото не вдалось (старий Bot API) → назва й описи все одно оновлені, помилка пояснена",
      r["done"] == ["назва", "короткий опис", "опис"] and "method not found" in r["errors"].get("фото", ""), r)
check("лого додатка й favicon — квадратні PNG", all(open(os.path.join(APP, "static", n), "rb").read(8) == b"\x89PNG\r\n\x1a\n" for n in ("app_icon.png", "favicon.png")))
print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"))
sys.exit(0 if ok else 1)
