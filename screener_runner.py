#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Запускач скринера для додатка SMC App.

Завантажує твій smc_screener.py (без змін у ньому) і перехоплює дві його функції:
  - save_json  (викликається після скану з кандидатами і після живого алерту «ціна в зоні»);
  - append_log (викликається після КОЖНОГО скану, навіть коли кандидатів 0).
Після них додатково пише results/latest_app.json: усі кандидати разом із даними графіка
(свічки, час зони, таймфрейм), щоб додаток міг намалювати інтерактивні графіки.

Також підміняє очікування між сканами (live_wait):
  - після сну Mac (закрита кришка) скан запускається одразу після пробудження, а не через залишок 30 хв;
  - після збою мережі повторний скан — через RETRY_MIN хвилин, а не через повні 30.

І дублює кожен алерт скринера (НОВА ПАРА, ЦІНА В ЗОНІ) у Telegram, якщо задано SMC_TG_TOKEN і SMC_TG_CHAT.

Після кожного скану пояснює, чому пари зі списку минулого скану зникли («<<< ВИБУЛА: ... — причина»),
у лозі скринера і в results/dropped_log.csv.

Використання (це робить сам додаток):
  python3 -u screener_runner.py /шлях/до/smc_screener.py --watch 30 --approach 4 ...
"""
import datetime as dt
import importlib.util
import json
import os
import sys
import time

APP_FILE = "latest_app.json"
# поля, які потрібні додатку (таблиця + графік)
KEEP = ["symbol", "dir", "status", "rank", "score", "price", "zone_low", "zone_high", "entry", "stop", "target",
        "target_type", "rr", "dist", "fib", "fib_ok", "fvg", "sweep", "volume", "vol_conf", "atr_pct", "knife",
        "flags", "bos_time", "bos_bars_ago", "struct", "against", "chart", "zone_t", "tf_ms", "zone_type", "entry_mid", "entry_tol", "rr_mid"]


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # tg.py поруч


def load_screener(path):
    spec = importlib.util.spec_from_file_location("smc_screener", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["smc_screener"] = mod
    spec.loader.exec_module(mod)
    return mod


def write_app_json(out_dir, rows, stamp, market):
    """Атомарний запис: спершу тимчасовий файл, потім заміна (додаток ніколи не прочитає половину файла)."""
    os.makedirs(out_dir, exist_ok=True)
    data = {
        "generated": stamp,
        "written_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "market": market,
        "count": len(rows),
        "candidates": [{k: r.get(k) for k in KEEP} for r in rows],
    }
    path = os.path.join(out_dir, APP_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


RETRY_MIN = 2          # повтор скану після збою мережі, хв
WAKE_GAP_S = 90        # «годинник стрибнув» більше ніж на стільки за один крок = Mac спав
WAKE_DELAY_S = 20      # після пробудження чекаємо мережу перед сканом
IDLE_STEP_S = 15       # крок очікування, коли немає пар для живої перевірки ціни
STATE = {"scan_failed": False}


def log(msg):
    print(f"[{dt.datetime.now():%H:%M}] [додаток] {msg}", flush=True)


def make_live_wait(mod):
    """Як live_wait скринера, але очікує короткими кроками й дивиться на реальний годинник:
    сон Mac -> скан одразу після пробудження; збій скану -> повтор через RETRY_MIN хв."""
    def live_wait(a, live_rows, seen_path, refresh=None):
        total = a.watch * 60
        if STATE["scan_failed"]:
            total = min(total, RETRY_MIN * 60)
            log(f"скан не вдався (мережа?) — повтор через {RETRY_MIN} хв")
        pc = getattr(a, "price_check", 0)
        live = pc > 0 and bool(live_rows)
        step = pc if live else IDLE_STEP_S
        end = time.time() + total
        fired = set()
        while True:
            left = end - time.time()
            if left <= 0:
                return
            want = min(step, left)
            t0 = time.time()
            time.sleep(want)
            gap = time.time() - t0 - want
            if gap > WAKE_GAP_S:
                log(f"Mac прокинувся після сну (≈{gap / 60:.0f} хв) — сканую одразу")
                time.sleep(WAKE_DELAY_S)
                return
            if not live:
                continue
            try:
                prices = mod.fetch_prices()
            except Exception:
                continue  # тимчасова помилка мережі: спробуємо на наступному кроці
            touched = mod.live_touches(live_rows, prices, fired)
            for r, px in touched:
                mod.fire_live(a, r, px, seen_path, dt.datetime.now().strftime("%H:%M"))
                r.update(status="у зоні (1-й дотик)", rank=0, price=px, dist=0.0)
            if touched and refresh:
                try:
                    refresh()
                except Exception:
                    pass
    return live_wait


# ---------------------------------------------------------------- чому пара вибула зі списку
DROP_FILE = "dropped_log.csv"
DROP = {"prev": None, "touched": {}}


def _key(r):
    return f"{r['symbol']}|{r['dir']}|{float(r['zone_low']):.8g}"


def _arg(name, default):
    """Значення параметра скринера з командного рядка (--approach 4 або --approach=4)."""
    argv = sys.argv
    for i, x in enumerate(argv):
        if x == name and i + 1 < len(argv):
            try:
                return float(argv[i + 1])
            except ValueError:
                return default
        if x.startswith(name + "="):
            try:
                return float(x.split("=", 1)[1])
            except ValueError:
                return default
    return default


def drop_reason(p, rows, rejects, px, approach, touched_at=None):
    """Пояснення простими словами, чому пара p (зі списку минулого скану) зникла в цьому скані."""
    sym, d = p["symbol"], p["dir"]
    sgn = 1 if d == "Long" else -1
    zl, zh, stop, tgt = float(p["zone_low"]), float(p["zone_high"]), float(p["stop"]), float(p["target"])
    fm = lambda x: f"{x:g}"
    if any(r["symbol"] == sym and r["dir"] == d for r in rejects):
        return "структура 4h змінилась — тепер сигнал проти структури (відсіяно)"
    other = [r for r in rows if r["symbol"] == sym and r["dir"] == d]
    new_zone = f"; нова зона {fm(other[0]['zone_low'])}–{fm(other[0]['zone_high'])}" if other else ""
    if px is None:
        return "нова структура 4h" + new_zone if other else "немає ціни (монету могли прибрати з торгів)"
    if (px - stop) * sgn <= 0:
        return f"ціна {fm(px)} пробила стоп {fm(stop)} — сценарій зламано" + new_zone
    if (px - tgt) * sgn >= 0:
        return f"ціна {fm(px)} уже дійшла до цілі {fm(tgt)} без входу" + new_zone
    if touched_at or str(p.get("status", "")).startswith("у зоні") or zl <= px <= zh:
        when = f" о {touched_at}" if touched_at else ""
        return f"зону відпрацьовано: ціна вже заходила в зону{when} (беру лише перший дотик)" + new_zone
    dist = ((px - zh) / px * 100) if d == "Long" else ((zl - px) / px * 100)
    if dist > approach:
        return f"ціна {fm(px)} відійшла на {dist:.1f}% від зони (показую до {approach:g}%)" + new_zone
    if other:
        return "нова структура 4h" + new_zone
    return ("зона перестала проходити умови скринера (нова свічка 4h змінила структуру або імпульс, "
            "R:R нижче мінімуму чи зона застаріла)")


def report_dropped(mod, out_dir, stamp, rows, rejects):
    """Порівняти зі списком минулого скану: для кожної пари, що зникла, — рядок у лозі й у dropped_log.csv."""
    prev, DROP["prev"] = DROP["prev"], [dict(r) for r in rows]
    if not prev:
        return []
    cur = {_key(r) for r in rows}
    gone = [p for p in prev if _key(p) not in cur]
    if not gone:
        return []
    try:
        prices = mod.fetch_prices()
    except Exception:
        prices = {}
    approach = _arg("--approach", 2.0)
    out = []
    for p in gone:
        why = drop_reason(p, rows, rejects, prices.get(p["symbol"]), approach, DROP["touched"].pop(_key(p), None))
        print(f"  <<< ВИБУЛА: {p['symbol']} {p['dir']} (зона {p['zone_low']:g}–{p['zone_high']:g}) — {why}", flush=True)
        out.append((p, why))
    try:
        import csv
        path = os.path.join(out_dir, DROP_FILE)
        new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "symbol", "dir", "zone_low", "zone_high", "entry", "stop", "target", "price", "reason"])
            day = dt.datetime.now().strftime("%Y-%m-%d")
            for p, why in out:
                w.writerow([f"{day} {stamp[-5:]}", p["symbol"], p["dir"], p["zone_low"], p["zone_high"], p["entry"], p["stop"],
                            p["target"], prices.get(p["symbol"], ""), why])
    except Exception as e:
        print(f"  [додаток] не вдалося записати {DROP_FILE}: {e}")
    return out


def install_hooks(mod):
    orig_save_json = mod.save_json
    orig_append_log = mod.append_log

    def market():
        if mod.MKT.get("forex"):
            return "forex"
        return "futures" if mod.MKT.get("futures") else "spot"

    def save_json(rows, path, stamp):
        orig_save_json(rows, path, stamp)
        try:
            write_app_json(os.path.dirname(path) or ".", rows, stamp, market())
        except Exception as e:  # додаток не має ламати скринер
            print(f"  [додаток] не вдалося записати {APP_FILE}: {e}")

    def append_log(out_dir, stamp, rows, rejects, live=None):
        orig_append_log(out_dir, stamp, rows, rejects, live)
        if live:  # живий алерт: файл оновить save_json у refresh()
            for r, _px in live:
                DROP["touched"][_key(r)] = dt.datetime.now().strftime("%H:%M")
            return
        try:
            report_dropped(mod, out_dir, stamp, rows, rejects)
        except Exception as e:  # пояснення — не критичне, скринер працює далі
            print(f"  [додаток] не вдалося пояснити, чому пари вибули: {e}")
        try:
            write_app_json(out_dir, rows, stamp, market())
        except Exception as e:
            print(f"  [додаток] не вдалося записати {APP_FILE}: {e}")

    orig_scan = getattr(mod, "scan", None)

    def scan(*args, **kw):
        try:
            res = orig_scan(*args, **kw)
        except Exception:
            STATE["scan_failed"] = True
            raise
        STATE["scan_failed"] = False
        return res

    mod.save_json = save_json
    mod.append_log = append_log
    if orig_scan is not None:
        mod.scan = scan

    orig_notify = getattr(mod, "notify", None)
    if orig_notify is not None:
        import tg
        chat = os.environ.get("SMC_TG_CHAT", "").strip()

        def notify(title, text, kind="new", speak="", ticker=""):
            orig_notify(title, text, kind, speak, ticker)
            if chat:
                tg.send_bg(chat, tg.format_alert(title, text, kind, ticker))

        mod.notify = notify
    if hasattr(mod, "live_wait"):
        mod.live_wait = make_live_wait(mod)


def main():
    if len(sys.argv) < 2:
        print("Використання: screener_runner.py /шлях/до/smc_screener.py [параметри скринера]")
        sys.exit(2)
    path = os.path.abspath(sys.argv[1])
    if not os.path.exists(path):
        print(f"Не знайдено скринер: {path}")
        sys.exit(2)
    mod = load_screener(path)
    install_hooks(mod)
    sys.argv = [path] + sys.argv[2:]
    print(f"[додаток] скринер: {path}", flush=True)
    mod.main()


if __name__ == "__main__":
    main()
