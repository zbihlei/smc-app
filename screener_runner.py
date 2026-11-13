#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Запускач скринера для додатка Rulebound.

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
        "flags", "bos_time", "bos_bars_ago", "struct", "against", "chart", "zone_t", "tf_ms", "zone_type", "entry_mid", "entry_tol", "rr_mid", "struct_range", "eq_low", "eq_high", "stop_sweep"]


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


# ---------------------------------------------------------------- «ціна в зоні»: один алерт на зону
FIRED_FILE = "live_fired.json"
FIRED_DAYS = 7
FIRED = {"path": None, "data": {}}


def fired_load(out_dir):
    """Зони, по яких уже був живий алерт «ЦІНА В ЗОНІ» (ключ: монета|напрям|низ зони -> час). Між сканами
    й перезапусками — щоб ціна, що «тремтить» біля краю зони, не давала той самий алерт знову й знову."""
    path = os.path.join(out_dir, FIRED_FILE)
    if FIRED["path"] != path:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        cut = time.time() - FIRED_DAYS * 86400
        FIRED.update(path=path, data={k: v for k, v in data.items() if isinstance(v, (int, float)) and v >= cut})
    return FIRED["data"]


def fired_add(out_dir, keys):
    data = fired_load(out_dir)
    for k in keys:
        data[k] = time.time()
    try:
        tmp = FIRED["path"] + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, FIRED["path"])
    except OSError:
        pass


SCAN_NOW_FILE = "scan_now.flag"   # додаток створює цей файл — скринер сканує одразу
FLAG_POLL_S = 2.0


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
        out_dir = getattr(a, "out", None) or os.path.dirname(seen_path) or "."
        fired = set(fired_load(out_dir))   # зони, по яких алерт уже був (і в минулих сканах)
        flag = os.path.join(out_dir, SCAN_NOW_FILE)
        next_px = time.time() + step
        while True:
            left = end - time.time()
            if left <= 0:
                return
            want = min(FLAG_POLL_S, left, max(0.05, next_px - time.time())) if live else min(FLAG_POLL_S, left)
            t0 = time.time()
            time.sleep(want)
            gap = time.time() - t0 - want
            if gap > WAKE_GAP_S:
                log(f"Mac прокинувся після сну (≈{gap / 60:.0f} хв) — сканую одразу")
                time.sleep(WAKE_DELAY_S)
                return
            if os.path.exists(flag):          # кнопка «Сканувати зараз» у додатку
                try:
                    os.remove(flag)
                except OSError:
                    pass
                log("скан за запитом з додатка")
                return
            if not live or time.time() < next_px:
                continue
            next_px = time.time() + step
            try:
                prices = mod.fetch_prices()
            except Exception:
                continue  # тимчасова помилка мережі: спробуємо на наступному кроці
            touched = mod.live_touches(live_rows, prices, fired)
            if touched:
                fired_add(out_dir, [mod.live_key(r) for r, _ in touched])
            if touched and hasattr(mod, "apply_btc"):
                try:   # свіже попередження про різкий рух BTC саме на момент входу в зону
                    mod.apply_btc([r for r, _ in touched])
                except Exception:
                    pass
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
    n_atr, atr = _arg("--approach-atr", 0.0), float(p.get("atr_pct") or 0)
    if n_atr and atr > 0 and n_atr * atr < approach and dist > n_atr * atr:
        return (f"ціна {fm(px)} відійшла на {dist:.1f}% від зони — це {dist / atr:.1f} середньої свічки "
                f"(показую до {n_atr:g} свічок)") + new_zone
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


# ---------------------------------------------------------------- стоп уже пробивало / зняття ліквідності (v1.9)
SWEEP_FILE = "sweeps_log.csv"
SWEEP_SEEN = "sweeps_seen.json"
SWEEP_TF, SWEEP_BARS = "15m", 96          # дивимось останні 24 год по 15 хв
SWEEP_VOL_BASE = 20                       # обсяг свічки пробою — відносно середнього за стільки свічок перед нею
SWEEP_VOL_X, SWEEP_WICK = 2.0, 0.33       # «підвищений обсяг» від ×2; «довгий гніт» від третини свічки (початкові пороги)
SWEEP_MAX_ROWS = 40
SWEEP_PREFIX = ("стоп уже пробивало", "схоже на зняття ліквідності", "після пробою стопа")
SWEEP_COLS = ["time", "symbol", "dir", "zone_low", "zone_high", "entry", "stop", "target", "ext_time", "ext_price",
              "depth_pct", "vol_ratio", "wick_pct", "pattern", "reclaim", "price", "score"]


def stop_sweep(c, r, now_ms=None):
    """Чи пробивала ціна стоп сигналу r за свічками c (15m, найновіша остання) і як це виглядало.
    Сигнал ще в списку, отже 4h за зоною не закрилась: пробій стопа «всередині свічки» — це або зняття
    ліквідності, або початок зламу. Повертає None (стоп не чіпали) або словник з описом. Лише інформація."""
    sgn = 1 if r["dir"] == "Long" else -1
    stop, zl, zh = float(r["stop"]), float(r["zone_low"]), float(r["zone_high"])
    ext = lambda x: x["l"] if sgn == 1 else x["h"]
    hit = [i for i, x in enumerate(c) if (ext(x) - stop) * sgn < 0]
    if not hit or stop <= 0:
        return None
    i = min(range(hit[0], len(c)), key=lambda k: ext(c[k]) * sgn)      # свічка з крайньою точкою після пробою
    x = c[i]
    base = [y["v"] for y in c[max(0, i - SWEEP_VOL_BASE):i] if y.get("v")]
    vol = (x["v"] / (sum(base) / len(base))) if base and x.get("v") and sum(base) > 0 else None
    rng = x["h"] - x["l"]
    wick = ((min(x["o"], x["c"]) - x["l"]) if sgn == 1 else (x["h"] - max(x["o"], x["c"]))) / rng if rng > 0 else 0.0
    back = (x["c"] - x["o"]) * sgn > 0                                   # свічка закрилась у бік угоди
    prev = c[i - 1] if i else None
    engulf = bool(prev and back and (prev["c"] - prev["o"]) * sgn < 0 and (x["c"] - prev["o"]) * sgn > 0)
    pat = []
    if wick >= 0.5:
        pat.append(f"пін-бар (гніт {wick * 100:.0f}% свічки)")
    elif wick >= SWEEP_WICK:
        pat.append(f"довгий гніт ({wick * 100:.0f}% свічки)")
    if engulf:
        pat.append("поглинання попередньої свічки")
    closed = [y for y in c[i:] if y.get("closed", True)]
    last = closed[-1]["c"] if closed else x["c"]
    edge_in, edge_out = (zl, zh) if sgn == 1 else (zh, zl)
    if (last - edge_out) * sgn > 0:
        reclaim = "над зоною" if sgn == 1 else "під зоною"
    elif (last - edge_in) * sgn >= 0:
        reclaim = "у зоні"
    elif (last - stop) * sgn > 0:
        reclaim = "за стопом, але ще поза зоною"
    else:
        reclaim = ""
    now_ms = now_ms or int(dt.datetime.now().timestamp() * 1000)
    return {"t": x["t"], "price": ext(x), "depth": abs(ext(x) - stop) / stop * 100, "vol": vol, "wick": wick,
            "pattern": pat, "reclaim": reclaim, "last": last, "mins": max(0, int((now_ms - x["t"]) / 60000)),
            "sweep_like": bool(reclaim and (pat or (vol or 0) >= SWEEP_VOL_X))}


def sweep_flags(r, s):
    side, far = ("мін.", "нижче") if r["dir"] == "Long" else ("макс.", "вище")
    when = dt.datetime.fromtimestamp(s["t"] / 1000).strftime("%H:%M")
    ago = f"{s['mins']} хв тому" if s["mins"] < 120 else f"{s['mins'] / 60:.0f} год тому"
    out = [f"стоп уже пробивало: {side} {s['price']:g} ({s['depth']:.1f}% {far} стопа) о {when}, {ago} — "
           f"лімітка за цим сигналом уже закрилась би по стопу"]
    info = []
    if s["vol"] is not None:
        info.append(f"обсяг на пробої ×{s['vol']:.1f} до середнього" + (" (підвищений)" if s["vol"] >= SWEEP_VOL_X else " (звичайний)"))
    info.append("свічка: " + (", ".join(s["pattern"]) if s["pattern"] else "без розворотного патерну"))
    info.append(("15m закрилась " + s["reclaim"]) if s["reclaim"] else "ціна ще за стопом — повернення немає")
    head = "схоже на зняття ліквідності" if s["sweep_like"] else "після пробою стопа"
    out.append(f"{head}: " + "; ".join(info))
    return out


def annotate_sweeps(mod, out_dir, rows):
    """Після скану: для кожної пари зі списку — чи пробивало стоп за останню добу. Попередження у flags (у алерті,
    на плитці, в логу), поле stop_sweep для додатка і рядок у sweeps_log.csv (раз на кожен новий екстремум)."""
    if not rows or not hasattr(mod, "fetch_klines"):
        return 0
    seen_path = os.path.join(out_dir, SWEEP_SEEN)
    try:
        with open(seen_path, encoding="utf-8") as f:
            seen = json.load(f)
    except Exception:
        seen = {}
    new = []
    for r in rows[:SWEEP_MAX_ROWS]:
        r["flags"] = [f for f in r.get("flags", []) if not f.startswith(SWEEP_PREFIX)]
        r.pop("stop_sweep", None)
        try:
            s = stop_sweep(mod.fetch_klines(r["symbol"], SWEEP_TF, SWEEP_BARS), r)
        except Exception:
            continue                                                    # немає 15m — просто без цього попередження
        if not s:
            continue
        r["flags"] = sweep_flags(r, s) + r["flags"]
        r["stop_sweep"] = {k: s[k] for k in ("t", "price", "depth", "vol", "wick", "pattern", "reclaim", "sweep_like")}
        if seen.get(_key(r)) != s["t"]:
            seen[_key(r)] = s["t"]
            new.append((r, s))
    if new:
        try:
            import csv
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, SWEEP_FILE)
            fresh = not os.path.exists(path)
            with open(path, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                if fresh:
                    w.writerow(SWEEP_COLS)
                now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
                for r, s in new:
                    w.writerow([now, r["symbol"], r["dir"], r["zone_low"], r["zone_high"], r["entry"], r["stop"], r["target"],
                                dt.datetime.fromtimestamp(s["t"] / 1000).strftime("%Y-%m-%d %H:%M"), s["price"],
                                round(s["depth"], 2), "" if s["vol"] is None else round(s["vol"], 2), round(s["wick"] * 100),
                                ", ".join(s["pattern"]), s["reclaim"], r.get("price", ""), r.get("score", "")])
            keep = {_key(r) for r in rows}
            with open(seen_path, "w", encoding="utf-8") as f:
                json.dump({k: v for k, v in seen.items() if k in keep}, f)
        except Exception as e:
            print(f"  [додаток] не вдалося записати {SWEEP_FILE}: {e}")
    return len(new)


# ---------------------------------------------------------------- сповіщення Mac: по черзі, без «хору»
GROUP_FROM = 3     # стільки й більше алертів за один скан — одне зведене сповіщення на Mac
GAP_S = 1.5        # пауза між сповіщеннями Mac


class MacQueue:
    """Банери, звуки, спливаючі вікна й голос macOS — по одному, а не всі в ту саму секунду.
    Алерти одного скану (alert_new) збираються в пачку: 1–2 — по черзі, від GROUP_FROM — одне зведене
    «Нових сигналів: N …» з одним голосовим повідомленням. Telegram від цього не залежить (там кожен окремо)."""

    def __init__(self, mod, mac_notify, say=None, gap=GAP_S):
        import queue
        import threading
        self.mod, self.mac_notify, self.gap = mod, mac_notify, gap
        self.say = say or self._say
        self.q = queue.Queue()
        self.batch = None
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    @staticmethod
    def _say(text):
        import subprocess
        try:
            subprocess.run(["say", text], timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def begin(self):
        with self.lock:
            self.batch = []

    def put(self, title, text, kind="new", speak="", ticker=""):
        item = (title, text, kind, speak, ticker)
        with self.lock:
            if self.batch is not None:
                self.batch.append(item)
                return
        self.q.put(item)

    def flush(self):
        with self.lock:
            items, self.batch = self.batch or [], None
        if len(items) < GROUP_FROM:
            for it in items:
                self.q.put(it)
            return
        zone = [it for it in items if it[2] == "zone"]
        names = [it[1].split(" | ")[0].split(": ", 1)[-1] for it in items]   # «EURUSD Long» з «НОВА ПАРА: EURUSD Long | …»
        shown = ", ".join(names[:8]) + (f" і ще {len(names) - 8}" if len(names) > 8 else "")
        head = f"Нових сигналів: {len(items)}" + (f" (у зоні: {len(zone)})" if zone else "")
        speak = f"{len(items)} new signals" + (f", {len(zone)} in zone" if zone else "")
        self.q.put((items[0][0], f"{head}: {shown}. Деталі — у додатку й Telegram.", "zone" if zone else "new", speak, ""))

    def wait_idle(self, timeout=10):
        """Для тестів: дочекатися, поки черга спорожніє."""
        import time
        end = time.time() + timeout
        while time.time() < end:
            if self.q.unfinished_tasks == 0:
                return True
            time.sleep(0.02)
        return False

    def _run(self):
        import time
        while True:
            title, text, kind, speak, ticker = self.q.get()
            lk = self._lock_other_screener()
            try:
                self.mac_notify(title, text, kind, "", ticker)       # банер + звук (+ вікно), без голосу
                if speak and getattr(self.mod, "ALERT", {}).get("voice"):
                    self.say(speak)                                   # голос — до кінця, потім наступне
                time.sleep(self.gap)
            except Exception:
                pass
            finally:
                if lk is not None:
                    try:
                        lk.close()                                    # знімає блокування
                    except Exception:
                        pass
                self.q.task_done()

    @staticmethod
    def _lock_other_screener():
        """Крипто- і форекс-скринер — окремі процеси: спільний файл-замок, щоб і вони не говорили одночасно."""
        try:
            import fcntl
            import tempfile
            f = open(os.path.join(tempfile.gettempdir(), "smc_app_mac_alerts.lock"), "w")
            fcntl.flock(f, fcntl.LOCK_EX)
            return f
        except Exception:
            return None


def suppress_repeat_zone(mod, rows, seen_path):
    """Повний скан після живого алерту: якщо по зоні вже був «ЦІНА В ЗОНІ», не повторювати його (позначаємо в seen.json,
    що скринер уже бачив цей статус). Нові пари й нові зони сповіщаються як раніше."""
    if not hasattr(mod, "live_key"):
        return
    fired = fired_load(os.path.dirname(seen_path) or ".")
    hit = [r for r in rows if isinstance(r, dict) and r.get("rank") == 0 and mod.live_key(r) in fired]
    if not hit:
        return
    try:
        with open(seen_path, encoding="utf-8") as f:
            seen = json.load(f)
    except Exception:
        seen = {}
    for r in hit:
        seen[mod.live_key(r)] = r["status"]
    with open(seen_path, "w", encoding="utf-8") as f:
        json.dump(seen, f)


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
        try:
            rows = res[0] if isinstance(res, tuple) else res
            out_dir = getattr(args[0], "out", None) if args else None
            if out_dir and isinstance(rows, list):
                annotate_sweeps(mod, out_dir, rows)
        except Exception as e:  # попередження — не критичне, скринер працює далі
            print(f"  [додаток] не вдалося перевірити пробої стопа: {e}")
        return res

    mod.save_json = save_json
    mod.append_log = append_log
    if orig_scan is not None:
        mod.scan = scan

    orig_notify = getattr(mod, "notify", None)
    if orig_notify is not None:
        import tg
        chat = os.environ.get("SMC_TG_CHAT", "").strip()
        macq = MacQueue(mod, orig_notify)
        mod._mac_queue = macq

        def notify(title, text, kind="new", speak="", ticker=""):
            macq.put(title, text, kind, speak, ticker)      # Mac: по черзі, пачка — одним сповіщенням
            if chat:
                tg.send_bg(chat, tg.format_alert(title, text, kind, ticker))   # Telegram: кожен алерт окремо

        mod.notify = notify
        orig_alert_new = getattr(mod, "alert_new", None)
        if orig_alert_new is not None:
            def alert_new(rows, seen_path):
                macq.begin()
                try:
                    suppress_repeat_zone(mod, rows, seen_path)
                    n = orig_alert_new(rows, seen_path)
                    zone = [mod.live_key(r) for r in rows if isinstance(r, dict) and r.get("rank") == 0] if hasattr(mod, "live_key") else []
                    if zone:  # «у зоні» за повним сканом — далі живий алерт по цій зоні вже не потрібен
                        fired_add(os.path.dirname(seen_path) or ".", zone)
                    return n
                finally:
                    macq.flush()
            mod.alert_new = alert_new
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
