#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Перевірки логіки журналу без браузера і без Google. Запуск: python3 tests/test_journal.py"""
import datetime as dt
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import journal as J  # noqa: E402
from fixtures import write_signals_log  # noqa: E402

ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


res_dir = tempfile.mkdtemp()
write_signals_log(res_dir)
today = dt.date.today().isoformat()

# ---------------- сигнали
sigs, _ = J.recent_signals(res_dir, 7)
ids = {s["symbol"]: s for s in sigs}
check("сигнали: DOGE і SUI за 7 днів, відсіяний ETH прихований, старий BTC — ні", set(ids) == {"DOGEUSDT", "SUIUSDT"}, list(ids))
sigs_r, _ = J.recent_signals(res_dir, 7, include_rejected=True)
eth = [s for s in sigs_r if s["symbol"] == "ETHUSDT"]
check("із прапорцем видно відсіяний ETH (rejected)", len(eth) == 1 and eth[0]["rejected"])
doge = ids["DOGEUSDT"]
check("DOGE: 3 скани в списку, дотик зони наживо сьогодні", doge["scans"] == 3 and doge["touched_at"].startswith(today), doge)
check("DOGE: рівні скринера на місці", doge["entry"] == 0.07533 and doge["stop"] == 0.07577 and doge["zone_low"] == 0.0752)

# ---------------- перевірки
good = {"date": today, "instrument": "DOGE", "dir": "Short", "entry": 0.07533, "stop": 0.07577, "target": 0.07399, "risk": 1}
c = J.check_trade(good, doge)
lv = {i["level"] for i in c["items"]}
check("угода як №19 (середина, стоп за зоною, R:R 3,05): без попереджень, «за правилами» = так",
      "warn" not in lv and "error" not in lv and c["rules_suggest"] == "так", c["items"])
check("R:R рахується як у таблиці (3,05)", c["rr"] == 3.05, c["rr"])

bad = dict(good, entry=0.07521, stop=0.07545, target=0.07450)
c = J.check_trade(bad, doge)
texts = " | ".join(i["text"] for i in c["items"])
check("вхід біля краю -> «не від середини»", "не від середини" in texts, texts)
check("стоп усередині зони -> попередження", "усередині зони" in texts, texts)
check("стоп вужчий за скринерний -> попередження", "вужчий за скринерний" in texts, texts)
check("ціль ближче за скринерну -> попередження", "ближче за скринерну" in texts, texts)
check("R:R < 3 -> попередження (урок №20)", "< 3" in texts, texts)
check("«за правилами» = ні з причинами", c["rules_suggest"] == "ні" and len(c["rules_reasons"]) >= 3, c["rules_reasons"])
c = J.check_trade(dict(good, dir="Long"), doge)
check("Long зі стопом вище входу -> помилка напрямку", c["items"][0]["level"] == "error")
c = J.check_trade(dict(good, entry=0.07533, stop=0.07577, target=0.07399), dict(doge, dir="Long"))
check("напрямок не збігається із сигналом -> помилка", any(i["level"] == "error" for i in c["items"]))
c = J.check_trade(dict(good, risk=0.5, result=-1.0), doge)
check("ризик 0,5R і результат −1R -> «більший за ризик»", any("більший за ризик" in i["text"] for i in c["items"]))
c = J.check_trade(dict(good, result=3.05, mfe=2.0), doge)
check("MFE менший за результат -> попередження", any("MFE" in i["text"] for i in c["items"]))
sui = ids["SUIUSDT"]
c = J.check_trade({"date": today, "instrument": "SUI", "dir": "Short", "entry": 0.90325, "stop": 0.9104, "target": 0.8722}, sui)
check("попередження скринера («ніж») передається", any("ніж" in i["text"] for i in c["items"]))
c = J.check_trade(dict(good, date=(dt.date.today() - dt.timedelta(days=5)).isoformat()), doge)
check("дата раніше за появу сигналу -> попередження", any("раніше за першу появу" in i["text"] for i in c["items"]))

m = J.find_matches(res_dir, {"date": today, "instrument": "Dogecoin", "dir": "Short", "entry": 0.0753})
check("ручна угода «Dogecoin» знаходить сигнал DOGEUSDT", len(m) == 1 and m[0]["symbol"] == "DOGEUSDT", m)
check("ручна угода без збігу -> порожньо", J.find_matches(res_dir, {"date": today, "instrument": "DOGE", "dir": "Long", "entry": 0.0753}) == [])

# ---------------- нормалізація
t, err = J.normalize({"date": "04.11.2026", "instrument": " PENG ", "dir": "Short", "entry": "54,83", "stop": "55,74",
                      "target": "53,05", "result": "1,96", "risk": "1", "rules": "ні", "by_screener": "так"})
check("інструмент «WLDUSDT» записується як «WLD», «Zcash» без змін",
      J.normalize({"instrument": "WLDUSDT"})[0]["instrument"] == "WLD" and J.normalize({"instrument": "Zcash"})[0]["instrument"] == "Zcash")
check("форма: дата ДД.ММ.РРРР і коми в числах", not err and t["date"] == "2026-11-04" and t["entry"] == 54.83 and t["result"] == 1.96, err)
t, err = J.normalize({"date": "", "instrument": "", "dir": "Up", "entry": "abc", "stop": "1", "target": "2", "rules": "може"})
check("форма: зрозумілі помилки", len(err) >= 5, err)

# ---------------- база: імпорт і похідні колонки як у таблиці
db_dir = tempfile.mkdtemp()
jr = J.Journal(db_dir)
seed = [
    [1, "2026-10-17", "Zcash", "Short", 1.231, 1.259, 1.128, 3.73, "так", 3.73, "Без змін", "ні", "", 3.73],
    [2, "2026-10-18", "Cardano", "Long", 0.223, 0.2175, 0.2361, 2.38, "ні", -2.2, "Без змін", "ні", "", 1.53],
    [3, "18.10.2026", "Solana", "Long", "102", "101", "106", "", "ні", "-1,1", "Без змін", "ні", "", "0,43"],
    [4, "2026-10-20", "Solana", "Long", 104, 103, 107, 3, "так", 4.5, "Без змін", "так", "", 4.93],
    [5, "2026-11-03", "TRUMP", "Long", 1.568, 1.544, 1.671, 4.29, "ні", -0.5, "Без змін", "ні", "Ризик 0,5R, тому стоп", 4.43,
     0.5, 1, "4h OB", "ні", 2, "так"],
]
r = jr.import_sheet([{"row": v[0] + 2, "values": v} for v in seed])
check("імпорт: 5 нових, накопичене збігається з таблицею", r["added"] == 5 and r["cum_match"] and r["app_cum"] == 4.43, r)
L = jr.list()
check("накопичено / просадка / серія як формули таблиці",
      [x["cum"] for x in L] == [3.73, 1.53, 0.43, 4.93, 4.43] and [x["dd"] for x in L] == [0, 2.2, 3.3, 0, 0.5]
      and [x["streak"] for x in L] == [0, 1, 2, 0, 1], [(x["cum"], x["dd"], x["streak"]) for x in L])
check("R:R план рахується (№2 = 2,38)", L[1]["rr_plan"] == 2.38)
check("ризик 0,5R розпізнано з нотатки (№5)", L[4]["risk"] == 0.5 and L[0]["risk"] == 1)
r = jr.import_sheet([{"row": v[0] + 2, "values": v} for v in seed])
check("повторний імпорт нічого не дублює", r["added"] == 0 and r["updated"] == 5 and len(jr.rows()) == 5)
n = jr.insert(dict(J.normalize({"date": today, "instrument": "DOGE", "dir": "Short", "entry": "0.07533", "stop": "0.07577",
                                 "target": "0.07399", "rules": "так", "by_screener": "так"})[0]))
check("нова угода отримує №6, відкрита (без результату) не ламає підсумки",
      n == 6 and jr.summary()["cum"] == 4.43 and jr.summary()["open"] == 1)
jr.set_sync(6, False, "тест")
check("помилка синхронізації зберігається", jr.get(6)["sheet_status"] == "error")
check("резервна копія без помилок", jr.backup() is None and os.path.exists(os.path.join(db_dir, "backup", "journal_latest.csv")))
check("щоденна копія: робиться один раз за день (стан до першої зміни)", jr.daily_snapshot() is None
      and [b["trades"] for b in jr.list_backups() if b["kind"] == "daily"] == [6])
jr.delete(6)
jr.daily_snapshot()
check("щоденна копія не перезаписується наступними змінами", [b["trades"] for b in jr.list_backups() if b["kind"] == "daily"] == [6])
name, _ = jr.manual_backup()
jr.delete(5)
res = jr.restore(name)
check("відновлення з ручної копії: 5 угод назад, стан перед відновленням збережено",
      len(jr.rows()) == 5 and res["safety"].startswith("journal_before_restore_") and os.path.exists(os.path.join(db_dir, "backup", res["safety"])))
check("небезпечні імена копій відхиляються", jr.backup_path("../journal.db") is None and jr.backup_path("x.db") is None)

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"))
sys.exit(0 if ok else 1)
