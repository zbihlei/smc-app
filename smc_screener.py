#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SMC-скринер v1: шукає на Binance (USDT-пари, 4h) кандидатів за правилами стратегії:
  1) тренд за останнім BOS (закриття свічки за свінгом);
  2) ордерблок на початку імпульсу, що дав BOS, з імбалансом (FVG) в імпульсі;
  3) ціна наближається до зони або в ній (перший дотик);
  4) вхід від середини зони, стоп за межею зони, ціль на найближчому свінгу;
  5) R:R не менше заданого (за замовчуванням 3);
  7) v2.7: допуск входу --entry-tol N (% висоти зони): ціна не завжди доходить рівно до середини блока;
  8) v2.8: ордерблок — ТІЛО свічки (--ob-zone body, за замовчуванням); стоп — за гнотом свічки ОБ; уся свічка: --ob-zone candle.
  6) v2.6: брейкер-блоки — пробитий ордерблок протилежного напрямку, якщо пробій дав BOS (вимкнути: --no-breakers).

Це інструмент для економії часу: він показує КАНДИДАТІВ для ручної перевірки, а не сигнали.
Не є фінансовою порадою.
"""
import argparse
import csv
import datetime as dt
import html
import json
import os
import subprocess
import sys
import time

try:
    import requests
except ImportError:  # requests потрібен лише для реальних запитів
    requests = None

SPOT = {"bases": ["https://api.binance.com", "https://data-api.binance.vision"], "ticker": "/api/v3/ticker/24hr",
        "klines": "/api/v3/klines", "info": "/api/v3/exchangeInfo", "price": "/api/v3/ticker/price", "futures": False}
FUT = {"bases": ["https://fapi.binance.com"], "ticker": "/fapi/v1/ticker/24hr",
       "klines": "/fapi/v1/klines", "info": "/fapi/v1/exchangeInfo", "price": "/fapi/v1/ticker/price", "futures": True}
MKT = dict(FUT)  # за замовчуванням ф'ючерси USDT-M; --spot перемикає на спот
# Не-крипто та «токенізовані» активи (золото, метали, нафта, акції/ETF), які не скануємо
NON_CRYPTO = {"XAU", "XAG", "XPT", "XPD", "PAXG", "XAUT", "CL", "BZ", "NATGAS", "COPPER", "MSTRB", "QQQB", "SPY", "QQQ"}
NON_CRYPTO_WORDS = ("tradfi", "stock", "equity", "commodity", "commodities", "index", "metal", "forex", "etf")
STABLES = {"USDC", "FDUSD", "TUSD", "BUSD", "USDP", "DAI", "EUR", "AEUR", "USDE", "PYUSD", "XUSD", "USD1", "UST", "EURI", "GBP"}


# ----------------------------------------------------------------------------
# Мережа
# ----------------------------------------------------------------------------
def http_get(path, params=None, retries=3):
    if requests is None:
        raise RuntimeError("Не встановлено пакет requests. Виконай: pip install requests")
    last_err = "невідома помилка"
    for base in MKT["bases"]:
        for attempt in range(retries):
            try:
                r = requests.get(base + path, params=params, timeout=15)
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (418, 429):
                    time.sleep(2 * (attempt + 1))
                    continue
                last_err = f"{base}: {r.status_code} {r.text[:120]}"
                break
            except requests.RequestException as e:
                last_err = f"{base}: {e}"
                time.sleep(1)
    raise RuntimeError(last_err)


def fetch_tradable():
    """Пари, якими зараз можна торгувати (статус TRADING). Для ф'ючерсів: лише безстрокові USDT-M на криптоактивах.
    Повертає (множина, скільки відсіяно як не-крипто). Якщо не вдалося: (None, 0)."""
    try:
        if MKT["futures"]:
            info = http_get(MKT["info"])
            ok, skipped = set(), 0
            for x in info["symbols"]:
                if x.get("status") != "TRADING" or x.get("contractType") != "PERPETUAL" or x.get("quoteAsset") != "USDT":
                    continue
                kind = str(x.get("underlyingType", "COIN")).lower() + " " + " ".join(map(str, x.get("underlyingSubType") or [])).lower()
                if x.get("underlyingType", "COIN") != "COIN" or any(w in kind for w in NON_CRYPTO_WORDS):
                    skipped += 1
                    continue
                ok.add(x["symbol"])
            return ok, skipped
        info = http_get(MKT["info"], {"permissions": "SPOT"})
        return {x["symbol"] for x in info["symbols"] if x.get("status") == "TRADING" and x.get("isSpotTradingAllowed", True)}, 0
    except Exception:
        return None, 0


def fetch_top_symbols(n, min_volume=0.0):
    tradable, skipped = fetch_tradable()
    if skipped:
        print(f"Відсіяно як не-крипто (метали, акції, індекси): {skipped}")
    if tradable is None:
        print("Увага: не вдалося отримати список торгових пар (exchangeInfo). Фільтр статусу/типу контракту пропущено.")
    data = http_get(MKT["ticker"])
    rows = []
    for t in data:
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        if tradable is not None and s not in tradable:
            continue
        base = s[:-4]
        if base in STABLES or base in NON_CRYPTO or any(x in base for x in ("DOWN", "BULL", "BEAR")):
            continue
        try:
            vol = float(t["quoteVolume"])
        except (KeyError, ValueError):
            continue
        if vol < min_volume:
            continue
        rows.append((s, vol))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in rows[:n]]


def fetch_klines(symbol, interval, limit):
    raw = http_get(MKT["klines"], {"symbol": symbol, "interval": interval, "limit": limit})
    now_ms = int(time.time() * 1000)
    candles = []
    for k in raw:
        candles.append({
            "t": int(k[0]), "o": float(k[1]), "h": float(k[2]), "l": float(k[3]),
            "c": float(k[4]), "v": float(k[5]), "closed": int(k[6]) < now_ms,
        })
    return candles


# ----------------------------------------------------------------------------
# Логіка: свінги, BOS, ордерблок, імбаланс
# ----------------------------------------------------------------------------
def find_swings(c, n):
    """Свінг-максимум: high строго вищий за n свічок зліва й не нижчий за n справа. Дзеркально для мінімуму."""
    highs, lows = [], []
    for i in range(n, len(c) - n):
        h, l = c[i]["h"], c[i]["l"]
        if all(h > c[j]["h"] for j in range(i - n, i)) and all(h >= c[j]["h"] for j in range(i + 1, i + n + 1)):
            highs.append((i, h))
        if all(l < c[j]["l"] for j in range(i - n, i)) and all(l <= c[j]["l"] for j in range(i + 1, i + n + 1)):
            lows.append((i, l))
    return highs, lows


def structure_events(c, n):
    """BOS = закриття свічки за останнім підтвердженим свінгом. dir: +1 вгору, -1 вниз."""
    highs, lows = find_swings(c, n)
    hi_by, lo_by = {}, {}
    for i, p in highs:
        hi_by.setdefault(i + n, []).append((i, p))
    for i, p in lows:
        lo_by.setdefault(i + n, []).append((i, p))
    last_high = last_low = None
    events = []
    for j in range(len(c)):
        for i, p in hi_by.get(j, []):
            last_high = (i, p)
        for i, p in lo_by.get(j, []):
            last_low = (i, p)
        close = c[j]["c"]
        if last_high is not None and close > last_high[1]:
            events.append({"idx": j, "dir": 1, "level_idx": last_high[0], "level": last_high[1]})
            last_high = None
        elif last_low is not None and close < last_low[1]:
            events.append({"idx": j, "dir": -1, "level_idx": last_low[0], "level": last_low[1]})
            last_low = None
    return events, highs, lows


def ob_bounds(cd, mode):
    """Межі ордерблока зі свічки cd. mode="body" (v2.8, за замовчуванням у командному рядку): зона = тіло свічки
    (від open до close), бо гноти часто «плавають»; якщо тіло менше 10% діапазону (доджі) — уся свічка.
    mode="candle": уся свічка від low до high (як до v2.8). Повертає (zl, zh, xl, xh): зона і крайні точки свічки."""
    xl, xh = cd["l"], cd["h"]
    if mode == "body":
        bl, bh = min(cd["o"], cd["c"]), max(cd["o"], cd["c"])
        if xh > xl and (bh - bl) >= 0.1 * (xh - xl):
            return bl, bh, xl, xh
    return xl, xh, xl, xh


def build_zone(c, ev, cfg=None):
    """Ордерблок: остання протилежна свічка перед імпульсом, що зламав структуру. Імбаланс (FVG) в імпульсі.
    Зона — тіло свічки або вся свічка (cfg["ob_zone"]); xl/xh — крайні точки свічки (для стопа й «зону зламано»)."""
    b, d, h = ev["idx"], ev["dir"], ev["level_idx"]
    if d == 1:
        o = min(range(h, b + 1), key=lambda k: c[k]["l"])
        ob = next((k for k in range(o, max(-1, o - 7), -1) if c[k]["c"] < c[k]["o"]), o)
    else:
        o = max(range(h, b + 1), key=lambda k: c[k]["h"])
        ob = next((k for k in range(o, max(-1, o - 7), -1) if c[k]["c"] > c[k]["o"]), o)
    zl, zh, xl, xh = ob_bounds(c[ob], (cfg or {}).get("ob_zone", "candle"))
    fvgs = []
    for k in range(ob + 1, b):
        if d == 1 and c[k + 1]["l"] > c[k - 1]["h"]:
            fvgs.append((c[k - 1]["h"], c[k + 1]["l"], k))
        if d == -1 and c[k + 1]["h"] < c[k - 1]["l"]:
            fvgs.append((c[k + 1]["h"], c[k - 1]["l"], k))
    return {"ob": ob, "origin": o, "zl": zl, "zh": zh, "xl": xl, "xh": xh, "fvgs": fvgs}


def find_breakers(c, events, d, cfg):
    """Брейкер-блоки для напрямку d (v2.6). Брейкер = ордерблок протилежного напрямку (з попередньої серії BOS),
    який ціна пробила закриттям свічки, і цей пробій дав BOS у напрямку d (на свічці пробою або за кілька свічок).
    Далі зона працює як звичайна зона входу в напрямку d: перший ретест, свіжість від пробою, ті самі стоп/ціль/R:R.
    Повертає [(ev, z)] у форматі, сумісному з build_zone/count_visits: ev["idx"] = свічка пробою."""
    i = len(events) - 1
    while i >= 0 and events[i]["dir"] == d:
        i -= 1
    old = []
    while i >= 0 and events[i]["dir"] == -d and len(old) < cfg["events"]:
        old.append(events[i])
        i -= 1
    d_events = [e for e in events if e["dir"] == d]
    look = cfg.get("breaker_bos_bars", 10)
    out = []
    for oe in old:
        zo = build_zone(c, oe, cfg)
        zl, zh, xl, xh = zo["zl"], zo["zh"], zo["xl"], zo["xh"]
        # пробій — закриття за крайньою точкою свічки ордерблока (а не лише за тілом)
        k_br = next((k for k in range(oe["idx"] + 1, len(c)) if (c[k]["c"] < xl if d == -1 else c[k]["c"] > xh)), None)
        if k_br is None:
            continue
        bos = next((e for e in d_events if k_br <= e["idx"] <= k_br + look), None)
        if bos is None:
            continue
        rng = range(zo["ob"], k_br + 1)
        o = max(rng, key=lambda k: c[k]["h"]) if d == -1 else min(rng, key=lambda k: c[k]["l"])
        fvgs = []
        for k in range(o + 1, k_br):
            if d == 1 and c[k + 1]["l"] > c[k - 1]["h"]:
                fvgs.append((c[k - 1]["h"], c[k + 1]["l"], k))
            if d == -1 and c[k + 1]["h"] < c[k - 1]["l"]:
                fvgs.append((c[k + 1]["h"], c[k - 1]["l"], k))
        ev = {"idx": k_br, "dir": d, "level_idx": o, "level": c[o]["h"] if d == -1 else c[o]["l"],
              "breaker": True, "old_idx": oe["idx"], "bos_idx": bos["idx"]}
        out.append((ev, {"ob": zo["ob"], "origin": o, "zl": zl, "zh": zh, "xl": xl, "xh": xh, "fvgs": fvgs, "breaker": True}))
    return out


def count_visits(c, ev, z):
    """Скільки разів ціна заходила в зону після BOS (по закритих свічках) і чи зону не закрито за межею."""
    b, d = ev["idx"], ev["dir"]
    zl, zh = z["zl"], z["zh"]
    xl, xh = z.get("xl", zl), z.get("xh", zh)   # зону зламано — закриття за крайньою точкою свічки ордерблока
    visits, in_visit, broken = 0, False, False
    for k in range(b + 1, len(c)):
        if d == 1:
            touched = c[k]["l"] <= zh
            if c[k]["c"] < xl:
                broken = True
        else:
            touched = c[k]["h"] >= zl
            if c[k]["c"] > xh:
                broken = True
        if touched and not in_visit:
            visits += 1
        in_visit = touched
    return visits, in_visit, broken


def find_target(c, highs, lows, entry, d):
    """Найближча «ліквідність» за входом: неторкнутий свінг-максимум (лонг) чи мінімум (шорт)."""
    if d == 1:
        cands = []
        for i, p in highs:
            if p > entry and max((c[k]["h"] for k in range(i + 1, len(c))), default=p) <= p:
                cands.append(p)
        return (min(cands), "свінг-максимум") if cands else (None, None)
    cands = []
    for i, p in lows:
        if p < entry and min((c[k]["l"] for k in range(i + 1, len(c))), default=p) >= p:
            cands.append(p)
    return (max(cands), "свінг-мінімум") if cands else (None, None)


def fib_retracement(c, ev, z, entry):
    """На якому відкаті від імпульсу лежить вхід (0 = екстремум імпульсу, 1 = його початок)."""
    d, b, o = ev["dir"], ev["idx"], z["origin"]
    if d == 1:
        start = c[o]["l"]
        ext = max(c[k]["h"] for k in range(b, len(c)))
        rng = ext - start
        return (ext - entry) / rng if rng > 0 else None
    start = c[o]["h"]
    ext = min(c[k]["l"] for k in range(b, len(c)))
    rng = start - ext
    return (entry - ext) / rng if rng > 0 else None


def detect_sweep(c, ev, z, highs, lows, lookback=40, tol=0.003):
    """Свіп перед BOS: свічка-початок імпульсу проколола попередній свінг (мінімум для лонга, максимум для шорта)
    і протягом 3 свічок закрилась назад за ним. Якщо проколото 2+ майже рівних рівнів, це «рівні мінімуми/максимуми»."""
    d, o = ev["dir"], z["origin"]
    lb = max(0, o - lookback)
    end = min(o + 3, len(c))
    if d == 1:
        swept = [p for i, p in lows if lb <= i < o and p > c[o]["l"]]
        back = [p for p in swept if max(c[k]["c"] for k in range(o, end)) > p]
        if not back:
            return None
        ref = max(back)
        equal = len([p for p in back if abs(p - ref) / ref <= tol]) >= 2
        return "свіп рівних мінімумів" if equal else "свіп мінімуму"
    swept = [p for i, p in highs if lb <= i < o and p < c[o]["h"]]
    back = [p for p in swept if min(c[k]["c"] for k in range(o, end)) < p]
    if not back:
        return None
    ref = min(back)
    equal = len([p for p in back if abs(p - ref) / ref <= tol]) >= 2
    return "свіп рівних максимумів" if equal else "свіп максимуму"


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def volume_analysis(c, ev, z, sweep, cfg):
    """Обсяг як ПІДТВЕРДЖЕННЯ (не пошук входу). Усе порівнюється з власною базою: середній обсяг 20 свічок перед імпульсом.
    1) імпульс: середній обсяг свічок від початку імпульсу до BOS відносно бази (>= vol_imp = підтверджує);
    2) відкат: середній обсяг свічок відкату відносно імпульсу (<= vol_pb = слабкий відкат = підтверджує; >= 1.2 = попередження);
    3) «обсяг спадає»: останні 3 свічки відкату проти попередніх (ціна йде до зони, а обсяг згасає);
    4) поглинання в зоні: у зоні велика свічка за обсягом (>= 1.8 бази), але малий результат (мале тіло або гніт у бік зони);
    5) свіп на великому обсязі (>= 2 бази).
    Це гіпотези з порогами для тесту, а не перевірені правила."""
    if not c or "v" not in c[0] or c[0].get("v") is None:
        return None
    d, b, o = ev["dir"], ev["idx"], z["origin"]
    last = len(c) - 1
    base = _mean(c[k]["v"] for k in range(max(0, o - 20), o))
    imp_avg = _mean(c[k]["v"] for k in range(o, b + 1))
    if o < 6 or base <= 0 or imp_avg <= 0:
        return None
    res = {"imp": imp_avg / base, "pb": None, "fade": False, "absorb": False, "sweepvol": False, "warn": []}
    ext = max(range(b, last + 1), key=lambda k: c[k]["h"]) if d == 1 else min(range(b, last + 1), key=lambda k: c[k]["l"])
    pb = list(range(ext + 1, last + 1))
    if len(pb) >= 2:
        res["pb"] = _mean(c[k]["v"] for k in pb) / imp_avg
        if len(pb) >= 4:
            early = _mean(c[k]["v"] for k in pb[:-3])
            res["fade"] = early > 0 and _mean(c[k]["v"] for k in pb[-3:]) / early < 0.8
    zl, zh = z["zl"], z["zh"]
    for k in range(max(0, last - 2), last + 1):
        cd = c[k]
        rng = cd["h"] - cd["l"]
        if rng <= 0 or not (cd["l"] <= zh and cd["h"] >= zl) or cd["v"] < 1.8 * base:
            continue
        body = abs(cd["c"] - cd["o"]) / rng
        wick = (min(cd["o"], cd["c"]) - cd["l"]) / rng if d == 1 else (cd["h"] - max(cd["o"], cd["c"])) / rng
        if body <= 0.35 or wick >= 0.5:
            res["absorb"] = True
    if sweep:
        res["sweepvol"] = c[o]["v"] >= 2.0 * base
    conf = 0
    parts = [f"імпульс ×{res['imp']:.1f}" + (" ✓" if res["imp"] >= cfg.get("vol_imp", 1.3) else "")]
    conf += res["imp"] >= cfg.get("vol_imp", 1.3)
    if res["pb"] is None:
        parts.append("відкату ще немає")
    else:
        t = f"відкат ×{res['pb']:.1f}"
        if res["pb"] <= cfg.get("vol_pb", 0.7):
            t += " ✓"
            conf += 1
        elif res["pb"] >= 1.2:
            t += " ✗"
            res["warn"].append("відкат на сильному обсязі: зона може не втримати")
        if res["fade"]:
            t += " спадає"
        parts.append(t)
    if res["absorb"]:
        parts.append("поглинання в зоні ✓")
        conf += 1
    if res["sweepvol"]:
        parts.append("свіп на обсязі ✓")
        conf += 1
    res["conf"] = int(conf)
    res["text"] = " | ".join(parts)
    return res


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return 0.0
    m = n // 2
    return xs[m] if n % 2 else (xs[m - 1] + xs[m]) / 2


def volatility_info(c, cfg):
    """Волатильність і «ножі». Звичайний розмір свічки = медіана діапазону (h-l) за останні ~50 свічок (стійка до викидів).
    «Ніж» = у останніх knife_look свічках є така, чий діапазон >= knife_mult × звичайного. atr_pct = середній діапазон у % ціни."""
    n = len(c)
    w = min(50, n)
    rngs = [c[k]["h"] - c[k]["l"] for k in range(n - w, n)]
    med = _median(rngs)
    px = c[-1]["c"]
    atr_pct = (_mean(rngs) / px * 100) if px else 0.0
    look = min(cfg.get("knife_look", 30), n)
    recent = [c[k]["h"] - c[k]["l"] for k in range(n - look, n)]
    maxr = max(recent) if recent else 0.0
    ratio = (maxr / med) if med > 0 else 0.0
    knife = ratio >= cfg.get("knife_mult", 3.0)
    return {"atr_pct": atr_pct, "knife": knife, "knife_ratio": ratio}


def analyze_symbol(symbol, candles, px, cfg):
    """Повертає список кандидатів (найкращий першим) для одного символа. c = лише закриті свічки."""
    c = candles
    st = cfg.get("stats")

    def mark(k):
        if st is not None:
            st[k] = st.get(k, 0) + 1

    if len(c) < cfg.get("min_bars", 60):
        return []
    mark("0 пар з даними")
    events, highs, lows = structure_events(c, cfg["swing"])
    if not events:
        return []
    mark("1 є BOS")
    volat = volatility_info(c, cfg) if cfg.get("knife", True) else {"atr_pct": 0.0, "knife": False, "knife_ratio": 0.0}
    if cfg.get("max_atr") is not None and volat["atr_pct"] > cfg["max_atr"]:
        return []  # пара надто волатильна — відсіяно повністю
    last_dir = events[-1]["dir"]
    # Структура тренду: два останні свінг-максимуми й мінімуми. Вгору = HH+HL, вниз = LH+LL, інакше 0 (змішана).
    tmode = cfg.get("trend", "bos")
    against = False
    th, tl = find_swings(c, cfg.get("trend_swing", 5))
    struct = 0
    if len(th) >= 2 and len(tl) >= 2:
        if th[-1][1] > th[-2][1] and tl[-1][1] > tl[-2][1]:
            struct = 1
        elif th[-1][1] < th[-2][1] and tl[-1][1] < tl[-2][1]:
            struct = -1
    if tmode != "bos":
        if struct != last_dir:
            against = True  # у режимі strict такі кандидати відсіюються нижче, але потрапляють у лог
        else:
            mark("1b структура 4h збігається з напрямком")
    run = []
    for ev in reversed(events):
        if ev["dir"] != last_dir:
            break
        run.append(ev)
    run = run[:cfg["events"]]
    last_i = len(c) - 1
    cands = [(ev, None) for ev in run]
    if cfg.get("breakers", True):
        cands += find_breakers(c, events, last_dir, cfg)
    out = []
    for ev, zb in cands:
        d = ev["dir"]
        if last_i - ev["idx"] > cfg["max_age"] or ev["idx"] - ev["level_idx"] > cfg["max_leg"]:
            continue
        mark("2 BOS свіжий, імпульс не задовгий" if zb is None else "2b брейкер: пробій свіжий")
        z = zb or build_zone(c, ev, cfg)
        if cfg.get("need_fvg", False) and not z["fvgs"]:
            continue
        mark("3 імбаланс (вимагається лише з --need-fvg)")
        zl, zh = z["zl"], z["zh"]
        xl, xh = z.get("xl", zl), z.get("xh", zh)
        mid = (zl + zh) / 2
        # v2.7: допуск входу — лімітка зсунута від середини до краю, з якого приходить ціна, на N% висоти зони
        tol = max(0.0, min(50.0, float(cfg.get("entry_tol", 0) or 0)))
        entry = mid + d * tol / 100 * (zh - zl)
        if (zh - zl) / mid * 100 > cfg["max_zone"]:
            continue
        mark("4 зона не надто висока")
        visits, in_visit_last, broken = count_visits(c, ev, z)
        if broken:
            continue
        mark("5 зона не закрита за межею")
        # поточна ціна відносно зони
        if d == 1:
            if px < xl:
                continue
            live_touch = px <= zh
            dist = 0.0 if live_touch else (px - zh) / px * 100
        else:
            if px > xh:
                continue
            live_touch = px >= zl
            dist = 0.0 if live_touch else (zl - px) / px * 100
        mark("6 ціна по правильний бік зони")
        total = visits + (1 if (live_touch and not in_visit_last) else 0)
        if total == 0:
            if dist > cfg["approach"]:
                continue
            status, rank = "підхід до зони", 1
        elif total == 1 and live_touch:
            status, rank = "у зоні (1-й дотик)", 0
        elif total >= 2 and live_touch and cfg["allow_retests"]:
            status, rank = "у зоні (повторний дотик)", 2
        else:
            continue
        mark("7 ціна біля зони / у зоні, дотик перший")
        stop = xl * (1 - cfg["buffer"] / 100) if d == 1 else xh * (1 + cfg["buffer"] / 100)   # за гнотом свічки ОБ
        wick_used = False
        if cfg.get("wick"):
            lb = max(0, ev["level_idx"] - 20)
            wp = cfg.get("wick_pct", 3.0) / 100
            if d == 1:
                cs = [p for i, p in lows if i >= lb and p < xl and p >= xl * (1 - wp)]
                w = min(cs) * (1 - cfg["buffer"] / 100) if cs else None
                if w is not None and w < stop:
                    stop, wick_used = w, True
            else:
                cs = [p for i, p in highs if i >= lb and p > xh and p <= xh * (1 + wp)]
                w = max(cs) * (1 + cfg["buffer"] / 100) if cs else None
                if w is not None and w > stop:
                    stop, wick_used = w, True
        target, ttype = find_target(c, highs, lows, entry, d)
        if target is None:
            continue
        mark("8 є ціль")
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        rr = abs(target - entry) / risk
        # R:R для відбору сигналу рахується від середини зони (сигнал валідний, як і без допуску);
        # rr — від фактичної лімітки з допуском (показується в таблиці й алертах). --rr-at-limit — суворо від лімітки.
        rr_mid = abs(target - mid) / abs(mid - stop) if abs(mid - stop) > 0 else 0.0
        if (rr if cfg.get("rr_at_limit") else rr_mid) < cfg["min_rr"]:
            continue
        mark("9 R:R достатній")
        fib = fib_retracement(c, ev, z, entry)
        retr_now = fib_retracement(c, ev, z, px)
        sweep = detect_sweep(c, ev, z, highs, lows)
        if cfg.get("only_sweep") and not sweep:
            continue
        if sweep:
            mark("10 є свіп перед BOS")
        vol = volume_analysis(c, ev, z, sweep, cfg) if cfg.get("volume", True) else None
        flags = []
        mid_passed = live_touch and ((px > entry) if d == -1 else (px < entry))
        if zb is not None:
            flags.append(f"брейкер: колишній {'бичачий' if d == -1 else 'ведмежий'} ордерблок, пробитий "
                         f"{dt.datetime.fromtimestamp(c[ev['idx']]['t'] / 1000).strftime('%d.%m %H:%M')}")
        if against:
            flags.append("ПРОТИ структури 4h (немає HH+HL / LH+LL у напрямку)")
        if mid_passed:
            flags.append("середина зони вже пройдена, шукай 1h-уточнення" if not tol else
                         f"рівень лімітки (середина + допуск {tol:g}%) вже пройдено, шукай 1h-уточнення")
        if retr_now is not None and retr_now > cfg.get("deep", 0.85):
            flags.append(f"глибокий відкат {retr_now * 100:.0f}% імпульсу")
        if wick_used:
            flags.append("стоп за гнотом")
        if vol:
            flags += vol["warn"]
        if volat["knife"]:
            flags.append(f"ніж: різка свічка ×{volat['knife_ratio']:.1f} до звичайної (стоп можуть зняти гнотом)")
        msa = cfg.get("min_stop_atr", 0)
        if msa and volat["atr_pct"] > 0:
            stop_pct = risk / entry * 100
            if stop_pct < msa * volat["atr_pct"]:
                need = entry * (1 - msa * volat["atr_pct"] / 100) if d == 1 else entry * (1 + msa * volat["atr_pct"] / 100)
                flags.append(f"тісний стоп: {stop_pct:.1f}% = {stop_pct / volat['atr_pct']:.2f}× середньої свічки ({volat['atr_pct']:.1f}%), "
                             f"шум може його вибити; для {msa:g}× потрібен стоп ≈{fmt(need)}")
        fib_ok = fib is not None and cfg["fib_lo"] <= fib <= cfg["fib_hi"]
        # Бали = сума вже показаних підтверджень (для сортування, НЕ ймовірність і не сигнал):
        #   + підтвердження обсягу (0..4), + свіп, + вхід у фібо 0.5-0.79;
        #   - проти структури, - глибокий відкат, - відкат на сильному обсязі.
        neg = (1 if against else 0) \
            + (1 if (retr_now is not None and retr_now > cfg.get("deep", 0.85)) else 0) \
            + (1 if (vol and vol.get("warn")) else 0) \
            + (1 if volat["knife"] else 0)
        score = (vol["conf"] if vol else 0) + (1 if sweep else 0) + (1 if fib_ok else 0) - neg
        if cfg.get("min_score") is not None and score < cfg["min_score"]:
            continue
        item = ({"volume": vol["text"] if vol else "-", "vol_conf": vol["conf"] if vol else 0, "score": score,
                    "chart": [[x["t"], x["o"], x["h"], x["l"], x["c"]] + ([x["v"]] if x.get("v") is not None else []) for x in c[-cfg.get("chart_bars", 150):]],
                    "zone_t": c[z["ob"]]["t"], "tf_ms": (c[-1]["t"] - c[-2]["t"]),
                    "sweep": sweep, "flags": flags, "retr_now": retr_now, "mid_passed": mid_passed, "wick_used": wick_used,
            "symbol": symbol, "dir": "Long" if d == 1 else "Short", "status": status, "rank": rank,
            "price": px, "zone_low": zl, "zone_high": zh, "entry": entry, "entry_mid": mid, "entry_tol": tol, "rr_mid": rr_mid, "stop": stop, "target": target,
            "target_type": ttype, "rr": rr, "dist": dist, "fvg": len(z["fvgs"]),
            "fib": fib, "fib_ok": fib_ok,
            "atr_pct": round(volat["atr_pct"], 2), "knife": volat["knife"],
            "bos_bars_ago": last_i - ev["idx"],
            "bos_time": dt.datetime.fromtimestamp(c[ev["idx"]]["t"] / 1000).strftime("%d.%m %H:%M"),
            "struct": struct, "against": against, "zone_type": "брейкер" if zb is not None else "OB",
        })
        if against and tmode == "strict":
            cfg.setdefault("rejects", []).append(item)  # лише для логу: у список не потрапляє
            continue
        out.append(item)
    out.sort(key=lambda r: (-r["score"], r["rank"], r["dist"], -r["rr"]))
    return out[:1]


# ----------------------------------------------------------------------------
# Вивід
# ----------------------------------------------------------------------------
def fmt(x):
    return f"{x:.6g}"


def tv_link(symbol):
    suffix = ".P" if MKT["futures"] else ""
    return f"https://www.tradingview.com/chart/?symbol=BINANCE%3A{symbol}{suffix}&interval=240"


def binance_link(symbol):
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    if MKT["futures"]:
        return f"https://www.binance.com/en/futures/{symbol}"
    return f"https://www.binance.com/en/trade/{base}_USDT?type=spot"


ALERT = {"new": "Hero", "zone": "Sosumi", "voice": False, "popup": False, "tv_button": False}


def tv_ticker(symbol):
    return "BINANCE:" + symbol + (".P" if MKT["futures"] else "")


def save_watchlist(rows, path):
    """Файл для імпорту в TradingView: Watchlist -> ... -> Import list. Перезаписується кожного скану."""
    with open(path, "w", encoding="utf-8") as f:
        f.write("###SMC-скринер,\n" + ",".join(tv_ticker(r["symbol"]) for r in rows) + "\n")


def play_sound(name):
    """Системний звук macOS за назвою (Hero, Sosumi, Glass, Ping, Funk, Submarine, Tink, Pop, Purr, Basso, Blow, Bottle, Frog, Morse) або шлях до свого файлу."""
    if not name:
        return
    path = name if os.path.exists(name) else f"/System/Library/Sounds/{name}.aiff"
    try:
        subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        print("\a", end="")


def _as(x):
    return str(x).replace("\\", "\\\\").replace('"', '\\"')


def tv_switch_script(ticker):
    """AppleScript: вивести десктопний TradingView вперед і ввести тикер (відкриває пошук символу). Потрібен дозвіл Універсального доступу."""
    return ('tell application "TradingView" to activate\ndelay 0.8\n'
            'tell application "System Events"\n'
            f'keystroke "{_as(ticker)}"\ndelay 0.7\nkey code 36\nend tell')


def open_tv(ticker):
    subprocess.Popen(["osascript", "-e", tv_switch_script(ticker)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def notify(title, text, kind="new", speak="", ticker=""):
    """Банер у Центрі сповіщень macOS + свій звук (+ голос і спливаюче вікно за бажанням)."""
    play_sound(ALERT["zone"] if kind == "zone" else ALERT["new"])
    t = text.replace('"', "'")
    try:
        subprocess.run(["osascript", "-e", f'display notification "{_as(t)}" with title "{_as(title)}"'],
                       check=False, timeout=5, capture_output=True)
        if ALERT["popup"]:
            if ALERT["tv_button"] and ticker:
                script = (f'set r to display alert "{_as(title)}" message "{_as(t)}" buttons {{"Закрити", "Відкрити в TradingView"}} '
                          'default button "Закрити" giving up after 60\n'
                          'if button returned of r is "Відкрити в TradingView" then\n' + tv_switch_script(ticker) + '\nend if')
            else:
                script = f'display alert "{_as(title)}" message "{_as(t)}" giving up after 30'
            subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if ALERT["voice"] and speak:
            subprocess.Popen(["say", speak], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def alert_new(rows, seen_path):
    """Повідомляє лише про НОВІ пари (або коли ціна зайшла в зону). Пам'ять про побачене тримає у файлі."""
    try:
        with open(seen_path, encoding="utf-8") as f:
            seen = json.load(f)
    except Exception:
        seen = {}
    now_keys = {}
    new = []
    for r in rows:
        key = f"{r['symbol']}|{r['dir']}|{r['zone_low']:.8g}"
        now_keys[key] = r["status"]
        if key not in seen:
            new.append((r, "НОВА ПАРА"))
        elif seen[key] != r["status"] and r["rank"] == 0:
            new.append((r, "ЦІНА В ЗОНІ"))
    os.makedirs(os.path.dirname(seen_path) or ".", exist_ok=True)
    with open(seen_path, "w", encoding="utf-8") as f:
        json.dump(now_keys, f)
    for r, kind in new:
        line = (f"{kind}: {r['symbol']} {r['dir']}{' БРЕЙКЕР' if r.get('zone_type') == 'брейкер' else ''} | бали {r['score']} | {r['status']} | вхід {fmt(r['entry'])} стоп {fmt(r['stop'])} "
                f"ціль {fmt(r['target'])} R:R {r['rr']:.1f}" + (f" | {r['sweep'].upper()}" if r["sweep"] else "") + (f" | ОБСЯГ: {r['volume']}" if r["volume"] != "-" else "") + (" | УВАГА: " + "; ".join(r["flags"]) if r["flags"] else ""))
        print("  >>> " + line)
        notify("SMC-скринер", line, "zone" if kind == "ЦІНА В ЗОНІ" else "new",
               f"{'Price in zone' if kind == 'ЦІНА В ЗОНІ' else 'New pair'} {r['symbol'][:-4]} {'long' if r['dir'] == 'Long' else 'short'}",
               tv_ticker(r["symbol"]))
    return len(new)


def print_table(rows):
    head = ["№", "Символ", "Напрям", "Статус", "Бали", "Ціна", "Зона", "Вхід", "Стоп", "Ціль", "R:R", "Відст.%", "Fib", "FVG", "Свіп", "Обсяг", "BOS", "Увага"]
    lines = [head]
    for i, r in enumerate(rows, 1):
        lines.append([
            str(i), r["symbol"], r["dir"], r["status"], str(r["score"]), fmt(r["price"]),
            f"{fmt(r['zone_low'])}-{fmt(r['zone_high'])}", fmt(r["entry"]), fmt(r["stop"]), fmt(r["target"]),
            f"{r['rr']:.1f}", f"{r['dist']:.2f}",
            ("так " if r["fib_ok"] else "ні  ") + (f"({r['fib']:.2f})" if r["fib"] is not None else ""),
            str(r["fvg"]), r["sweep"] or "-", r["volume"], r["bos_time"], "; ".join(r["flags"]),
        ])
    widths = [max(len(l[k]) for l in lines) for k in range(len(head))]
    tty = sys.stdout.isatty()
    for n, l in enumerate(lines):
        out = "  ".join(v.ljust(widths[k]) for k, v in enumerate(l))
        if n > 0 and rows[n - 1]["rank"] == 0:
            out = ("\033[1;31m" + out + "  <<< ЦІНА В ЗОНІ\033[0m") if tty else (out + "  <<< ЦІНА В ЗОНІ")
        print(out)
        if n == 0:
            print("  ".join("-" * w for w in widths))


def save_csv(rows, path):
    keys = ["symbol", "dir", "status", "score", "price", "zone_low", "zone_high", "entry", "stop", "target", "target_type", "rr", "dist", "fvg", "fib", "fib_ok", "bos_time", "struct"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(keys + ["sweep", "volume", "warnings", "tradingview", "binance"])
        for r in rows:
            w.writerow([r[k] for k in keys] + [r["sweep"] or "", r["volume"], "; ".join(r["flags"]), tv_link(r["symbol"]), binance_link(r["symbol"])])


def save_json(rows, path, stamp):
    """Компактний JSON усіх кандидатів (без важких даних графіка) для журналу чи подальшої автоматизації."""
    keep = ["symbol", "dir", "status", "score", "price", "zone_low", "zone_high", "entry", "stop", "target",
            "target_type", "rr", "dist", "fib", "fib_ok", "fvg", "sweep", "volume", "vol_conf", "atr_pct", "knife", "flags", "bos_time", "struct", "against", "zone_type", "entry_mid", "entry_tol", "rr_mid"]
    data = {"generated": stamp, "market": "futures" if MKT["futures"] else "spot",
            "count": len(rows), "candidates": [{k: r.get(k) for k in keep} for r in rows]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


STRUCT_TXT = {1: "вгору (HH+HL)", -1: "вниз (LH+LL)", 0: "змішана"}
LOG_KEYS = ["time", "kind", "symbol", "dir", "status", "score", "price", "zone_low", "zone_high", "entry", "stop",
            "target", "rr", "dist", "struct4h", "against", "bos_time", "knife", "warnings"]


def append_log(out_dir, stamp, rows, rejects, live=None):
    """Дописує кожен скан у signals_log.csv: і показані кандидати, і відсіяні за структурою 4h.
    Це історія за день/тиждень, бо latest.* перезаписуються щоразу."""
    path = os.path.join(out_dir, "signals_log.csv")
    new = not os.path.exists(path)
    os.makedirs(out_dir, exist_ok=True)
    day = dt.datetime.now().strftime("%Y-%m-%d")
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(LOG_KEYS)
        for kind, lst in (("у списку", rows), ("відсіяно: проти структури 4h", rejects),
                          ("наживо: ціна в зоні", [dict(r, price=px) for r, px in (live or [])])):
            for r in lst:
                w.writerow([f"{day} {stamp[-5:]}", kind, r["symbol"], r["dir"], r["status"], r["score"], r["price"],
                            r["zone_low"], r["zone_high"], round(r["entry"], 8), round(r["stop"], 8), r["target"],
                            round(r["rr"], 2), round(r["dist"], 2), STRUCT_TXT.get(r.get("struct", 0), ""),
                            "так" if r.get("against") else "ні", r["bos_time"], "так" if r.get("knife") else "ні",
                            "; ".join(r["flags"])])


def day_report(out_dir, day=None):
    """Підсумок за день із signals_log.csv: унікальні пари, скільки з них проти структури 4h."""
    path = os.path.join(out_dir, "signals_log.csv")
    if not os.path.exists(path):
        print(f"Немає {path}: лог з'явиться після першого скану з цією версією.")
        return
    day = day or dt.datetime.now().strftime("%Y-%m-%d")
    seen = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if not r["time"].startswith(day):
                continue
            key = (r["symbol"], r["dir"], r["zone_low"])
            seen.setdefault(key, r)  # перше входження пари за день
    if not seen:
        print(f"За {day} у логу порожньо.")
        return
    shown = [r for r in seen.values() if not r["kind"].startswith("відсіяно")]
    rej = [r for r in seen.values() if r["kind"].startswith("відсіяно")]
    print(f"Звіт за {day}: унікальних пар {len(seen)} | показано {len(shown)} | відсіяно проти структури 4h {len(rej)}")
    for title, lst in (("Показано:", shown), ("Відсіяно (проти структури 4h):", rej)):
        if lst:
            print(title)
            for r in lst:
                print(f"  {r['time'][11:]} {r['symbol']:<12} {r['dir']:<5} бали {r['score']:>3}  R:R {r['rr']:>5}  структура: {r['struct4h']}")
    n_long = sum(1 for r in seen.values() if r["dir"] == "Long")
    print(f"Long: {n_long}, Short: {len(seen) - n_long}")


LWC_URL = "https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"

CHART_JS = r"""
const LWC = LightweightCharts, FUT = 24, H = 430;
function build(i, d) {
  const el = document.getElementById('ch' + i);
  const chart = LWC.createChart(el, {
    width: el.clientWidth, height: H,
    layout: {background: {color: '#ffffff'}, textColor: '#131722', fontFamily: 'Arial'},
    grid: {vertLines: {color: '#f0f3fa'}, horzLines: {color: '#f0f3fa'}},
    rightPriceScale: {borderColor: '#d1d4dc', scaleMargins: {top: 0.08, bottom: 0.22}},
    timeScale: {borderColor: '#d1d4dc', timeVisible: true, secondsVisible: false},
    crosshair: {mode: 0}
  });
  const lo = Math.min(d.stop, d.target, d.zl, d.entry), hi = Math.max(d.stop, d.target, d.zh, d.entry);
  const series = chart.addCandlestickSeries({
    upColor: '#ffffff', downColor: '#000000', borderUpColor: '#000000', borderDownColor: '#000000',
    wickUpColor: '#000000', wickDownColor: '#000000', priceLineVisible: false, lastValueVisible: false,
    autoscaleInfoProvider: (orig) => {
      const r = orig();
      if (!r) return {priceRange: {minValue: lo, maxValue: hi}};
      return {priceRange: {minValue: Math.min(r.priceRange.minValue, lo), maxValue: Math.max(r.priceRange.maxValue, hi)}, margins: r.margins};
    }
  });
  const data = d.c.map(x => ({time: x[0], open: x[1], high: x[2], low: x[3], close: x[4]}));
  const step = d.tf, last = data[data.length - 1].time;
  for (let k = 1; k <= FUT; k++) data.push({time: last + k * step});
  series.setData(data);
  if (d.c[0].length > 5) {
    const vol = chart.addHistogramSeries({priceFormat: {type: 'volume'}, priceScaleId: '', lastValueVisible: false, priceLineVisible: false});
    vol.priceScale().applyOptions({scaleMargins: {top: 0.82, bottom: 0}});
    vol.setData(d.c.map(x => ({time: x[0], value: x[5], color: x[4] >= x[1] ? 'rgba(38,166,154,0.55)' : 'rgba(239,83,80,0.55)'})));
  }
  const pl = (price, color, title, style) => series.createPriceLine({price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title});
  pl(d.px, '#2962ff', 'Ціна', 0);
  pl(d.entry, '#434651', 'Вхід', 2);
  pl(d.stop, '#f23645', 'Стоп', 2);
  pl(d.target, '#089981', 'Ціль', 2);
  // сценарій-ілюстрація (не прогноз): шлях від поточної ціни до входу та до цілі
  const path = chart.addLineSeries({color: '#787b86', lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false});
  const pts = d.inzone ? [{time: last, value: d.px}, {time: last + 12 * step, value: d.target}]
                       : [{time: last, value: d.px}, {time: last + 4 * step, value: d.entry}, {time: last + 16 * step, value: d.target}];
  path.setData(pts);
  const mk = (css) => { const e = document.createElement('div'); e.style.cssText = 'position:absolute;pointer-events:none;z-index:3;' + css; el.appendChild(e); return e; };
  const zoneD = mk('background:rgba(150,150,150,0.35);');
  const midD = mk('border-top:1px dashed #9c27b0;height:0;');
  const riskD = mk('background:rgba(120,123,134,0.45);');
  const rewD = mk('background:rgba(77,182,205,0.45);color:#0b3c49;font:12px Arial;padding:2px 6px;');
  rewD.textContent = 'R:R ' + d.rr.toFixed(1);
  function redraw() {
    const ts = chart.timeScale();
    const X = (t, f) => { const v = ts.timeToCoordinate(t); return v === null ? f : v; };
    const x0 = X(last, 0), x1 = X(last + FUT * step, el.clientWidth), xz = Math.max(0, X(d.zt, 0));
    const Y = (p) => series.priceToCoordinate(p);
    const yzh = Y(d.zh), yzl = Y(d.zl), ye = Y(d.entry), ys = Y(d.stop), yt = Y(d.target), ym = Y((d.zh + d.zl) / 2);
    if ([yzh, yzl, ye, ys, yt, ym].some(v => v === null)) return;
    const box = (e, x, w, ya, yb) => { e.style.left = x + 'px'; e.style.width = Math.max(0, w) + 'px'; e.style.top = Math.min(ya, yb) + 'px'; e.style.height = Math.abs(ya - yb) + 'px'; };
    box(zoneD, xz, x1 - xz, yzh, yzl);
    midD.style.left = xz + 'px'; midD.style.width = Math.max(0, x1 - xz) + 'px'; midD.style.top = ym + 'px';
    box(riskD, x0, x1 - x0, ye, ys);
    box(rewD, x0, x1 - x0, ye, yt);
  }
  chart.timeScale().subscribeVisibleLogicalRangeChange(redraw);
  chart.subscribeCrosshairMove(redraw);
  new ResizeObserver(() => { chart.applyOptions({width: el.clientWidth}); redraw(); }).observe(el);
  chart.timeScale().setVisibleLogicalRange({from: data.length - FUT - 70, to: data.length - 1});
  setTimeout(redraw, 50); setTimeout(redraw, 300);
}
DATA.forEach((d, i) => build(i, d));
"""


HTML_REFRESH_S = 30  # у режимі --watch сторінка latest.html сама оновлюється кожні 30 с


def action_text(r):
    """Що робити з рядком: червоним у результатах виділяємо пари, де ціна вже в зоні й час вирішувати щодо лімітки."""
    if r["rank"] == 0:
        return "ЦІНА В ЗОНІ: вирішуй, чи ставити лімітку (перевір 1h)"
    if r["rank"] == 1:
        return f"чекаю підходу до зони ({r['dist']:.1f}%)"
    return "повторний дотик зони"


def save_html(rows, path, stamp, cfg, max_charts=None, refresh_s=0):
    """Таблиця + інтерактивні графіки (TradingView Lightweight Charts) з позицією, зоною та сценарієм."""
    max_charts = max_charts or cfg.get("charts", 12)
    tz = time.localtime().tm_gmtoff
    in_zone = [r for r in rows if r["rank"] == 0]
    parts = ["<html><head><meta charset='utf-8'>" + (f"<meta http-equiv='refresh' content='{int(refresh_s)}'>" if refresh_s else "")
             + "<title>" + ("\U0001F534 В ЗОНІ: " + ", ".join(r["symbol"][:-4] for r in in_zone) + " | " if in_zone else "") + "SMC-скринер</title>",
             "<style>body{font-family:Arial;margin:24px;background:#f5f6f8}table{border-collapse:collapse;background:#fff}td,th{border:1px solid #ccc;padding:6px 10px;font-size:14px}"
             "th{background:#dce6f1}.s0{background:#ffd9d9}.s1{background:#fff8e1}.s2{background:#ececec}"
             ".act0{color:#b00020;font-weight:bold}.s0 td{border-color:#d32f2f}"
             ".alert{background:#d32f2f;color:#fff;padding:10px 14px;border-radius:6px;margin:10px 0;font-size:15px;font-weight:bold}"
             ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(620px,1fr));gap:18px;margin-top:22px}"
             ".card{background:#fff;border:1px solid #d1d4dc;border-radius:6px;padding:10px}.hd{font-size:14px;margin-bottom:6px}"
             ".chart{position:relative;width:100%}.note{color:#555;font-size:12px;margin-top:14px}</style></head><body>",
             f"<h2>SMC-скринер, {html.escape(stamp)}</h2>",
             "<p>Кандидати для ручної перевірки, а не сигнали. Не є фінансовою порадою.</p>"]
    if in_zone:
        parts.append("<div class='alert'>\U0001F534 Ціна вже в зоні: " + "; ".join(
            f"{html.escape(r['symbol'])} {r['dir']} (вхід {fmt(r['entry'])}, стоп {fmt(r['stop'])}, R:R {r['rr']:.1f})" for r in in_zone)
            + ". Час вирішувати, чи ставити лімітку: перевір 1h.</div>")
    parts += ["<table><tr><th>№</th><th>Символ</th><th>Напрям</th><th>Статус</th><th>Дія</th><th>Бали</th><th>Ціна</th><th>Зона</th><th>Вхід</th><th>Стоп</th><th>Ціль</th><th>R:R</th><th>Відст.%</th><th>Fib</th><th>FVG</th><th>Свіп</th><th>Обсяг</th><th>BOS</th><th>Увага</th><th>Binance</th></tr>"]
    for i, r in enumerate(rows, 1):
        fib = ("так " if r["fib_ok"] else "ні ") + (f"({r['fib']:.2f})" if r["fib"] is not None else "")
        num = f"<a href='#c{i - 1}'>{i}</a>" if i <= max_charts else str(i)
        parts.append(
            f"<tr class='s{r['rank']}'><td>{num}</td><td><a href='{tv_link(r['symbol'])}' target='_blank'>{r['symbol']}</a></td><td>{r['dir']}</td><td>{r['status']}</td><td class='act{r['rank']}'>{html.escape(action_text(r))}</td><td><b>{r['score']}</b></td>"
            f"<td>{fmt(r['price'])}</td><td>{fmt(r['zone_low'])}-{fmt(r['zone_high'])}</td><td>{fmt(r['entry'])}</td><td>{fmt(r['stop'])}</td><td>{fmt(r['target'])}</td>"
            f"<td>{r['rr']:.1f}</td><td>{r['dist']:.2f}</td><td>{fib}</td><td>{r['fvg']}</td><td>{html.escape(r['sweep'] or '-')}</td><td>{html.escape(r['volume'])}</td><td>{r['bos_time']}</td>"
            f"<td>{html.escape('; '.join(r['flags']))}</td><td><a href='{binance_link(r['symbol'])}' target='_blank'>графік</a></td></tr>")
    parts.append("</table>")
    shown = [r for r in rows if r.get("chart")][:max_charts]
    data = []
    if shown:
        parts.append(f"<h3>Графіки (перші {len(shown)})</h3><div class='note'>Сіра зона з фіолетовою пунктирною серединою = зона входу; "
                     "сірий блок = ризик до стопа, блакитний = шлях до цілі. Пунктирна лінія від ціни це сценарій-ілюстрація, а не прогноз. "
                     "Колесо миші: масштаб; перетягування: рух графіка.</div><div class='grid'>")
        for i, r in enumerate(shown):
            tf = max(1, int(r["tf_ms"] // 1000))
            data.append({"c": [[int(x[0] / 1000) + tz] + x[1:] for x in r["chart"]], "tf": tf, "zt": int(r["zone_t"] / 1000) + tz,
                         "zl": r["zone_low"], "zh": r["zone_high"], "entry": r["entry"], "stop": r["stop"], "target": r["target"],
                         "px": r["price"], "rr": r["rr"], "inzone": r["rank"] != 1})
            extra = ((" | " + html.escape(r["sweep"])) if r["sweep"] else "") + ((" | обсяг: " + html.escape(r["volume"])) if r["volume"] != "-" else "")
            warn = (" | <b>" + html.escape("; ".join(r["flags"])) + "</b>") if r["flags"] else ""
            parts.append(f"<div class='card' id='c{i}'><div class='hd'><b>{r['symbol']}</b> {r['dir']} | {html.escape(r['status'])} | R:R {r['rr']:.1f}{extra}{warn} "
                         f"| <a href='{tv_link(r['symbol'])}' target='_blank'>TradingView</a></div><div class='chart' id='ch{i}'></div></div>")
        parts.append("</div>")
        payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
        parts.append(f"<script src='{LWC_URL}'></script><script>const DATA = {payload};{CHART_JS}</script>")
        parts.append("<div class='note'>Графіки: <a href='https://www.tradingview.com/' target='_blank'>TradingView</a> Lightweight Charts™. "
                     "Для них потрібен інтернет.</div>")
    parts.append("</body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


# ----------------------------------------------------------------------------
# Самоперевірка на синтетичних даних (без мережі)
# ----------------------------------------------------------------------------
def _mk(closes, wick=0.3, first_open=None):
    c, prev = [], first_open if first_open is not None else closes[0]
    for i, cl in enumerate(closes):
        o = prev
        c.append({"t": 1_700_000_000_000 + i * 14_400_000, "o": o, "h": max(o, cl) + wick, "l": min(o, cl) - wick, "c": cl, "closed": True})
        prev = cl
    return c


def _demo_bullish():
    closes = [100, 101, 102, 100, 98, 99.5, 101, 102.5, 104, 103, 102.5, 101.5, 100.5, 99.8, 99.0,
              101.5, 103.8, 105.5, 106.5, 107.0, 106.0, 105.0, 104.0, 103.0, 102.0, 101.0]
    return _mk(closes)


def selftest():
    cfg = {"swing": 3, "events": 3, "max_age": 120, "max_leg": 80, "max_zone": 6.0, "approach": 2.0, "buffer": 0.15,
           "min_rr": 3.0, "allow_retests": False, "fib_lo": 0.5, "fib_hi": 0.79, "min_bars": 20}
    ok = True

    def check(name, cond, info=""):
        nonlocal ok
        print(("OK   " if cond else "FAIL ") + name + (f"  {info}" if info else ""))
        ok = ok and cond

    c = _demo_bullish()
    events, highs, lows = structure_events(c, 3)
    check("BOS вгору знайдено на свічці 17", any(e["dir"] == 1 and e["idx"] == 17 for e in events), str([(e["idx"], e["dir"]) for e in events]))
    z = build_zone(c, [e for e in events if e["dir"] == 1 and e["idx"] == 17][0])
    check("ордерблок = свічка 14 (остання ведмежа перед імпульсом)", z["ob"] == 14, str(z["ob"]))
    check("межі зони 98.7-100.1", abs(z["zl"] - 98.7) < 1e-9 and abs(z["zh"] - 100.1) < 1e-9, f"{z['zl']}-{z['zh']}")
    zb_ = build_zone(c, [e for e in events if e["dir"] == 1 and e["idx"] == 17][0], {"ob_zone": "body"})
    check("v2.8: ордерблок по тілу — зона 99.0-99.8 (open-close), гноти 98.7/100.1 окремо",
          (zb_["zl"], zb_["zh"], zb_["xl"], zb_["xh"]) == (99.0, 99.8, 98.7, 100.1), str(zb_))
    check("v2.8: доджі (тіло < 10% свічки) — зона на всю свічку",
          ob_bounds({"o": 100.0, "c": 100.01, "l": 99.0, "h": 101.0}, "body") == (99.0, 101.0, 99.0, 101.0))
    _rb = analyze_symbol("DEMOUSDT", c, 100.4, dict(cfg, ob_zone="body", min_stop_atr=0, min_rr=1.0))
    check("v2.8: вхід від середини тіла (99.4), стоп за гнотом свічки ОБ (нижче 98.7)",
          len(_rb) == 1 and abs(_rb[0]["entry"] - 99.4) < 1e-9 and _rb[0]["stop"] < 98.7 and _rb[0]["zone_low"] == 99.0,
          str({k: _rb[0][k] for k in ("entry", "stop", "zone_low", "zone_high")}) if _rb else "нема")
    check("імбаланс (FVG) в імпульсі є", len(z["fvgs"]) >= 1)

    _rt = analyze_symbol("DEMOUSDT", c, 100.4, dict(cfg, min_stop_atr=100))
    _rn = analyze_symbol("DEMOUSDT", c, 100.4, dict(cfg, min_stop_atr=0))
    check("тісний стоп: попередження з'являється, коли стоп менший за N× середню свічку",
          len(_rt) == 1 and any("тісний стоп" in f for f in _rt[0]["flags"]), str(_rt[0]["flags"]) if _rt else "нема")
    check("тісний стоп: з --min-stop-atr 0 попередження немає", len(_rn) == 1 and not any("тісний стоп" in f for f in _rn[0]["flags"]))
    res = analyze_symbol("DEMOUSDT", c, 100.4, cfg)
    check("лонг-кандидат: підхід до зони", len(res) == 1 and res[0]["status"] == "підхід до зони" and res[0]["dir"] == "Long", str(res[0]["status"]) if res else "нема")
    if res:
        r = res[0]
        check("вхід = середина зони 99.4", abs(r["entry"] - 99.4) < 1e-6, fmt(r["entry"]))
        check("стоп нижче зони", r["stop"] < 98.7, fmt(r["stop"]))
        check("ціль = найближчий неторкнутий максимум (≈107.3)", abs(r["target"] - 107.3) < 1e-6, fmt(r["target"]))
        check("R:R ≥ 3", r["rr"] >= 3, f"{r['rr']:.2f}")
        rt = analyze_symbol("DEMOUSDT", c, 100.4, dict(cfg, entry_tol=25))
        check("допуск входу 25%: лімітка 99.75 (середина 99.4 + 0.25×1.4), R:R менший, стоп і ціль ті самі",
              len(rt) == 1 and abs(rt[0]["entry"] - 99.75) < 1e-6 and rt[0]["rr"] < r["rr"] and abs(rt[0]["stop"] - r["stop"]) < 1e-9
              and abs(rt[0]["entry_mid"] - 99.4) < 1e-6, fmt(rt[0]["entry"]) if rt else "нема")
        rlim = analyze_symbol("DEMOUSDT", c, 100.4, dict(cfg, entry_tol=50, min_rr=r["rr"] - 0.01))
        rlim2 = analyze_symbol("DEMOUSDT", c, 100.4, dict(cfg, entry_tol=50, min_rr=r["rr"] - 0.01, rr_at_limit=True))
        check("R:R для відбору — від середини (сигнал лишається), з --rr-at-limit — від лімітки (відсіяно)", len(rlim) == 1 and rlim2 == [])

    res2 = analyze_symbol("DEMOUSDT", c, 99.9, cfg)
    check("ціна в зоні, 1-й дотик", len(res2) == 1 and res2[0]["status"] == "у зоні (1-й дотик)", str(res2[0]["status"]) if res2 else "нема")

    res3 = analyze_symbol("DEMOUSDT", c, 90.0, cfg)
    check("ціна нижче зони: кандидата немає (зона зламана)", res3 == [])

    res4 = analyze_symbol("DEMOUSDT", c, 110.0, cfg)
    check("ціна далеко від зони: кандидата немає", res4 == [])

    # повторний дотик: ціна один раз зайшла в зону й вийшла, потім знову підходить
    closes = [x["c"] for x in c] + [100.0, 101.5, 102.5, 101.8]
    c2 = _mk(closes)
    res5 = analyze_symbol("DEMOUSDT", c2, 100.0, cfg)
    check("повторний дотик не показується за замовчуванням", res5 == [], str(res5[0]["status"]) if res5 else "")
    cfg2 = dict(cfg, allow_retests=True)
    res6 = analyze_symbol("DEMOUSDT", c2, 100.0, cfg2)
    check("повторний дотик з прапорцем --allow-retests", len(res6) == 1 and "повторний" in res6[0]["status"], str(res6[0]["status"]) if res6 else "нема")

    # шорт: дзеркальні дані
    mir = [{"t": x["t"], "o": 200 - x["o"], "h": 200 - x["l"], "l": 200 - x["h"], "c": 200 - x["c"], "closed": True} for x in c]
    res7 = analyze_symbol("DEMOUSDT", mir, 200 - 100.4, cfg)
    check("шорт-кандидат (дзеркало)", len(res7) == 1 and res7[0]["dir"] == "Short" and res7[0]["status"] == "підхід до зони", str(res7[0]["status"]) if res7 else "нема")
    if res7:
        check("шорт: стоп вище зони", res7[0]["stop"] > res7[0]["zone_high"])
        check("шорт: ціль нижче входу", res7[0]["target"] < res7[0]["entry"])
        r7t = analyze_symbol("DEMOUSDT", mir, 200 - 100.4, dict(cfg, entry_tol=25))
        check("шорт: допуск зсуває лімітку вниз (ціна приходить знизу)", len(r7t) == 1 and r7t[0]["entry"] < r7t[0]["entry_mid"])

    # фільтр тренду: низхідна структура (LH+LL) з відскоком, що зламав останній LH (як ETH). Лонг не має проходити.
    def zig(pivots, seg=8):
        out = [pivots[0]]
        for x, y in zip(pivots, pivots[1:]):
            out += [x + (y - x) * k / seg for k in range(1, seg + 1)]
        return out

    ce = _mk(zig([130, 120, 125, 110, 116, 100, 106, 92, 98, 86, 100, 112, 94]))
    pe = ce[-1]["c"]
    wide = dict(cfg, max_zone=20.0, approach=10.0, min_rr=1.0)
    ra = analyze_symbol("ETH", ce, pe, dict(wide, trend="bos"))
    check("режим bos: лонг проти структури проходить (старий режим)", len(ra) == 1 and ra[0]["dir"] == "Long")
    rb = analyze_symbol("ETH", ce, pe, dict(wide, trend="strict"))
    check("режим strict: лонг проти структури 4h відсіяно", rb == [])
    # у strict відсіяний кандидат потрапляє в cfg["rejects"] (для логу) з позначкою against
    cfg_rj = dict(wide, trend="strict")
    analyze_symbol("ETH", ce, pe, cfg_rj)
    rj = cfg_rj.get("rejects", [])
    check("strict: відсіяний кандидат записано в rejects (структура не збігається з напрямком)", len(rj) == 1 and rj[0]["struct"] != 1 and rj[0]["against"])
    # живі алерти: ціна торкається зони -> один раз, не раніше
    base = dict(symbol="AAAUSDT", dir="Long", zone_low=100.0, zone_high=102.0)
    base_s = dict(symbol="BBBUSDT", dir="Short", zone_low=200.0, zone_high=204.0)
    fired_t = set()
    check("live: ціна ще вище зони лонга -> без алерту", live_touches([base], {"AAAUSDT": 103.0}, fired_t) == [])
    hit = live_touches([base], {"AAAUSDT": 101.9}, fired_t)
    check("live: ціна зайшла в зону лонга -> алерт", len(hit) == 1 and hit[0][1] == 101.9)
    check("live: повторно по тій самій парі алерту немає", live_touches([base], {"AAAUSDT": 101.0}, fired_t) == [])
    check("live: шорт, ціна нижче зони -> без алерту", live_touches([base_s], {"BBBUSDT": 198.0}, set()) == [])
    check("live: шорт, ціна в зоні -> алерт", len(live_touches([base_s], {"BBBUSDT": 200.5}, set())) == 1)
    check("live: ціна пробила зону лонга вниз -> без алерту", live_touches([base], {"AAAUSDT": 99.0}, set()) == [])
    check("live: немає ціни пари -> без помилок", live_touches([base], {}, set()) == [])
    import tempfile as _tf2_mod
    # результати: пара в зоні виділяється червоним і банером
    _row = dict(symbol="ZZZUSDT", dir="Long", status="у зоні (1-й дотик)", rank=0, score=1, price=100.0, zone_low=99.0, zone_high=101.0,
                entry=100.0, stop=98.0, target=110.0, rr=5.0, dist=0.0, fib=0.6, fib_ok=True, fvg=1, sweep=None, volume="-",
                bos_time="01.01 00:00", flags=[], chart=[], zone_t=0, tf_ms=14400000, retr_now=None, struct=1, against=False, knife=False)
    _row2 = dict(_row, symbol="WWWUSDT", status="підхід до зони", rank=1, dist=1.2)
    _dh = _tf2_mod.mkdtemp()
    save_html([_row, _row2], os.path.join(_dh, "t.html"), "12:00", dict(charts=0), refresh_s=30)
    _h = open(os.path.join(_dh, "t.html"), encoding="utf-8").read()
    check("html: банер і червоний рядок для пари в зоні", "class='alert'" in _h and "class='s0'" in _h and "ЦІНА В ЗОНІ: вирішуй" in _h)
    check("html: пара «підхід» без червоного, з текстом очікування", "class='s1'" in _h and "чекаю підходу до зони" in _h)
    check("html: автооновлення сторінки вмикається в режимі стеження", "http-equiv='refresh' content='30'" in _h)
    _sr = sheet_rows([_row, _row2])
    check("таблиця: 18 колонок, посилання й дія на місці", len(_sr) == 2 and all(len(x) == 18 for x in _sr) and _sr[0][3].startswith("у зоні") and _sr[0][1].startswith("=HYPERLINK") and "ЦІНА В ЗОНІ" in _sr[0][4])
    import tempfile as _tf2
    _d = _tf2.mkdtemp()
    append_log(_d, "12:00", rb, rj)
    append_log(_d, "12:30", rb, rj)  # той самий кандидат вдруге: у звіті має бути один раз
    with open(os.path.join(_d, "signals_log.csv"), encoding="utf-8-sig", newline="") as _f:
        _rows = list(csv.DictReader(_f))
    check("signals_log.csv: 2 рядки (по одному на скан), позначено відсіяні", len(_rows) == 2 and _rows[0]["kind"].startswith("відсіяно") and _rows[0]["against"] == "так")
    import io as _io, contextlib as _cl
    _buf = _io.StringIO()
    with _cl.redirect_stdout(_buf):
        day_report(_d)
    check("--report: унікальні пари рахуються один раз", "унікальних пар 1" in _buf.getvalue() and "відсіяно проти структури 4h 1" in _buf.getvalue())
    rc = analyze_symbol("ETH", ce, pe, dict(wide, trend="flag"))
    check("режим flag: показано з позначкою «ПРОТИ структури»", len(rc) == 1 and any("ПРОТИ" in f for f in rc[0]["flags"]))
    mir2 = [{"t": x["t"], "o": 300 - x["o"], "h": 300 - x["l"], "l": 300 - x["h"], "c": 300 - x["c"], "closed": True} for x in ce]
    rd = analyze_symbol("ETH", mir2, 300 - pe, dict(wide, trend="strict"))
    check("дзеркало: шорт проти висхідної структури відсіяно", rd == [])
    # брейкер (v2.6): бичачий OB пробито вниз із BOS, ціна повертається знизу до тієї самої зони -> шорт від брейкера
    cbk = _mk(zig([100, 110, 104, 120, 113, 128, 100, 108, 92, 104]))
    wide_bk = dict(wide, approach=10.0)
    rbk = analyze_symbol("BRK", cbk, cbk[-1]["c"], dict(wide_bk, trend="strict"))
    check("брейкер: шорт від пробитого бичачого ордерблока, з позначкою",
          len(rbk) == 1 and rbk[0]["zone_type"] == "брейкер" and rbk[0]["dir"] == "Short"
          and any(f.startswith("брейкер") for f in rbk[0]["flags"]), str([(x["zone_type"], x["dir"]) for x in rbk]))
    if rbk:
        check("брейкер: стоп над зоною, ціль під входом, R:R пораховано", rbk[0]["stop"] > rbk[0]["zone_high"]
              and rbk[0]["target"] < rbk[0]["entry"] and rbk[0]["rr"] >= 1)
    rnb = analyze_symbol("BRK", cbk, cbk[-1]["c"], dict(wide_bk, trend="strict", breakers=False))
    check("--no-breakers: брейкера немає", all(x["zone_type"] != "брейкер" for x in rnb))
    cbk2 = _mk(zig([100, 110, 104, 120, 113, 128, 100, 108, 92, 104, 118]))
    rbk2 = analyze_symbol("BRK", cbk2, cbk2[-1]["c"], dict(wide_bk, trend="bos"))
    check("брейкер: ціна закрилась над зоною — шорт-брейкер зламано, не показується",
          not any(x["zone_type"] == "брейкер" and x["dir"] == "Short" for x in rbk2 + cfg.get("rejects", [])),
          str([(x["zone_type"], x["dir"]) for x in rbk2]))

    # нормальний висхідний тренд має проходити у strict
    cu = _mk(zig([80, 90, 85, 100, 94, 110, 104, 120, 114, 130, 120]))
    ru = analyze_symbol("UP", cu, cu[-1]["c"], dict(wide, trend="strict"))
    check("висхідна структура (HH+HL): лонг у strict проходить", len(ru) == 1 and ru[0]["dir"] == "Long")

    # свіп: рівні мінімуми, прокол однією свічкою, повернення, BOS вгору
    def zig2(pivots, seg=8):
        out = [pivots[0]]
        for x, y in zip(pivots, pivots[1:]):
            out += [x + (y - x) * k / seg for k in range(1, seg + 1)]
        return out

    cs = _mk(zig2([120, 100, 108, 100.2, 108, 100.1, 112, 104]))
    k_sw = [k for k in range(len(cs)) if abs(cs[k]["c"] - 100.1) < 1e-6 or abs(cs[k]["c"] - 100.0) < 1e-6][-1]
    cs[k_sw]["l"] = 98.0
    cs[k_sw]["c"] = max(cs[k_sw]["c"], 101.0)
    ev_s, hs, ls = structure_events(cs, 3)
    evs = [e for e in ev_s if e["dir"] == 1]
    zs = build_zone(cs, evs[-1]) if evs else None
    sw = detect_sweep(cs, evs[-1], zs, hs, ls) if evs else None
    check("свіп рівних мінімумів перед BOS знайдено", sw == "свіп рівних мінімумів", str(sw))
    check("у звичайному демо (без проколу) свіпу немає", detect_sweep(_demo_bullish(), [e for e in structure_events(_demo_bullish(), 3)[0] if e["dir"] == 1][0],
          build_zone(_demo_bullish(), [e for e in structure_events(_demo_bullish(), 3)[0] if e["dir"] == 1][0]), *structure_events(_demo_bullish(), 3)[1:]) is None)

    # обсяг: сильний імпульс і слабкий відкат підтверджують; сильний відкат дає попередження; поглинання в зоні
    def with_vol(candles, imp_v, pb_v):
        cc = [dict(x, v=100.0) for x in candles]
        evx = [e for e in structure_events(cc, 3)[0] if e["dir"] == 1][-1]
        zx = build_zone(cc, evx)
        for k in range(zx["origin"], evx["idx"] + 1):
            cc[k]["v"] = imp_v
        ext = max(range(evx["idx"], len(cc)), key=lambda k: cc[k]["h"])
        for k in range(ext + 1, len(cc)):
            cc[k]["v"] = pb_v
        return cc

    cv_base = _mk(zig([130, 120, 125, 110, 116, 100, 106, 92, 98, 86, 100, 112, 94]), wick=0.5)
    vcfg = dict(cfg, max_zone=20.0, approach=10.0, min_rr=1.0, trend="bos")
    r1 = analyze_symbol("V", with_vol(cv_base, 220.0, 50.0), 94.0, vcfg)
    check("обсяг: сильний імпульс і слабкий відкат дають ✓✓", len(r1) == 1 and r1[0]["vol_conf"] >= 2 and r1[0]["volume"].count("✓") >= 2, r1[0]["volume"] if r1 else "нема")
    r2 = analyze_symbol("V", with_vol(cv_base, 220.0, 300.0), 94.0, vcfg)
    check("обсяг: відкат на сильному обсязі дає попередження", len(r2) == 1 and any("сильному обсязі" in f for f in r2[0]["flags"]), r2[0]["volume"] if r2 else "нема")
    r3 = analyze_symbol("V", cv_base, 94.0, vcfg)
    check("без даних про обсяг колонка '-' і нічого не ламається", len(r3) == 1 and r3[0]["volume"] == "-")
    cab = with_vol(cv_base, 220.0, 60.0)
    cab[-1].update(o=87.6, c=87.5, h=88.0, l=86.4, v=400.0)
    rr_ = analyze_symbol("V", cab, 87.5, dict(vcfg, approach=15.0))
    check("обсяг: поглинання в зоні (великий обсяг, довгий нижній гніт) знайдено", len(rr_) == 1 and "поглинання" in rr_[0]["volume"], (rr_[0]["volume"] if rr_ else "нема кандидата"))

    # бали та --min-score
    rsc = analyze_symbol("V", with_vol(cv_base, 220.0, 50.0), 94.0, vcfg)
    check("бали — ціле число", len(rsc) == 1 and isinstance(rsc[0]["score"], int), str(rsc[0]["score"]) if rsc else "нема")
    check("--min-score з високим порогом відсіює все", analyze_symbol("V", with_vol(cv_base, 220.0, 50.0), 94.0, dict(vcfg, min_score=99)) == [])
    # фіктивний JSON не падає
    import tempfile as _tf
    save_json(rsc, _tf.mktemp(suffix=".json"), "t")
    check("save_json працює", True)

    # ножі: спокійний графік -> немає ножа; інжектуємо одну величезну свічку -> є ніж
    calm = _mk(zig([100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 102, 103, 104, 105, 106, 105, 104, 103]), wick=0.3)
    vi_calm = volatility_info(calm, {"knife_mult": 3.0, "knife_look": 30})
    check("спокійний графік: ножа немає", not vi_calm["knife"], f"ratio={vi_calm['knife_ratio']:.1f}")
    knife = [dict(x) for x in calm]
    knife[-5].update(h=knife[-5]["h"] + 20, l=knife[-5]["l"] - 20)  # величезна свічка
    vi_knife = volatility_info(knife, {"knife_mult": 3.0, "knife_look": 30})
    check("різка свічка розпізнається як ніж", vi_knife["knife"], f"ratio={vi_knife['knife_ratio']:.1f}")
    # ніж дає попередження й мінус у балах: інжектуємо різку свічку в ранню частину (не чіпає зону),
    # дивимось широким вікном knife_look, щоб її помітити
    ck = [dict(x) for x in cv_base]
    ck[15].update(h=ck[15]["h"] + 12, l=ck[15]["l"] - 12)
    base_cfg = dict(vcfg, approach=15.0, knife_mult=3.0, knife_look=200)
    r_noknife = analyze_symbol("K", cv_base, cv_base[-1]["c"], dict(base_cfg, knife=False))
    rk = analyze_symbol("KNIFE", ck, ck[-1]["c"], base_cfg)
    check("ніж позначається в «Увага» і знижує бали", len(rk) == 1 and any("ніж" in f for f in rk[0]["flags"])
          and (not r_noknife or rk[0]["score"] < r_noknife[0]["score"]), (rk[0]["flags"] if rk else "нема"))
    # --max-atr відсіює надто волатильну пару (будь-який реальний графік має atr_pct > 0.01%)
    rmax = analyze_symbol("V", with_vol(cv_base, 220.0, 50.0), 94.0, dict(vcfg, max_atr=0.01))
    check("--max-atr відсіює надто волатильну пару", rmax == [])

    print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "ЄСТЬ ПОМИЛКИ"))
    return 0 if ok else 1


# ----------------------------------------------------------------------------
def scan(a, cfg, verbose=True):
    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()] or fetch_top_symbols(a.top, a.min_volume * 1e6)
    excl = {x.strip().upper() for x in a.exclude.split(",") if x.strip()}
    symbols = [s for s in symbols if s[:-4] not in excl and s not in excl]
    if verbose:
        print(f"Перевіряю {len(symbols)} пар на {a.interval}...")
    rows, errors = [], []
    cfg["rejects"] = []
    for i, s in enumerate(symbols, 1):
        try:
            k = fetch_klines(s, a.interval, a.limit)
            closed = [x for x in k if x["closed"]]
            px = k[-1]["c"]
            rows += analyze_symbol(s, closed, px, cfg)
        except Exception as e:  # одна проблемна пара не має зупиняти все
            errors.append(f"{s}: {e}")
        if verbose and i % 10 == 0:
            print(f"  {i}/{len(symbols)}")
        time.sleep(0.08)
    rows.sort(key=lambda r: (-r["score"], r["rank"], r["dist"], -r["rr"]))
    return rows, errors


STRUCT_SHORT = {1: "вгору", -1: "вниз", 0: "змішана"}
_sheet_err = {"last": None}


def sheet_rows(rows):
    """Рядки для вкладки «Скринер» у Google Таблиці (18 колонок, як у шапці).
    Формули подаються як при ручному введенні в україномовній таблиці: роздільник аргументів «;»."""
    out = []
    for i, r in enumerate(rows, 1):
        out.append([
            i, f'=HYPERLINK("{tv_link(r["symbol"])}";"{r["symbol"]}")', r["dir"], r["status"], action_text(r), r["score"],
            r["price"], r["zone_low"], r["zone_high"], r["entry"], r["stop"], r["target"], round(r["rr"], 2), round(r["dist"], 2),
            STRUCT_SHORT.get(r.get("struct", 0), ""), r["volume"], "; ".join(r["flags"]),
            f'=HYPERLINK("{binance_link(r["symbol"])}";"графік")',
        ])
    return out


def push_sheet(a, rows, stamp):
    """Надсилає поточну таблицю в Google Таблицю (через Apps Script). Помилки не зупиняють скринер."""
    url = getattr(a, "sheet_url", "") or ""
    if not url:
        return
    try:
        if requests is None:
            raise RuntimeError("немає пакета requests")
        resp = requests.post(url, json={"token": getattr(a, "sheet_token", ""), "stamp": stamp, "rows": sheet_rows(rows)}, timeout=30)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"таблиця відповіла: {data.get('error')}")
        _sheet_err["last"] = None
    except Exception as e:
        msg = str(e)[:160]
        if _sheet_err["last"] != msg:  # не спамимо однаковою помилкою
            print(f"  [Google Таблиця] не вдалося оновити: {msg}")
        _sheet_err["last"] = msg


def fetch_prices():
    """Остання ціна всіх пар одним легким запитом (для живих алертів між повними сканами)."""
    data = http_get(MKT["price"])
    return {x["symbol"]: float(x["price"]) for x in data}


def live_key(r):
    return f"{r['symbol']}|{r['dir']}|{r['zone_low']:.8g}"


def live_touches(live_rows, prices, fired):
    """Які з пар «підхід до зони» вже торкнулись зони за свіжими цінами. Кожна пара спрацьовує один раз (fired)."""
    out = []
    for r in live_rows:
        key = live_key(r)
        if key in fired:
            continue
        px = prices.get(r["symbol"])
        if px is None:
            continue
        if r["zone_low"] <= px <= r["zone_high"]:
            fired.add(key)
            out.append((r, px))
    return out


def fire_live(a, r, px, seen_path, stamp):
    """Живий алерт: ціна щойно зайшла в зону. Оновлює seen.json, щоб наступний повний скан не продублював сповіщення."""
    line = (f"ЦІНА В ЗОНІ (наживо): {r['symbol']} {r['dir']}{' БРЕЙКЕР' if r.get('zone_type') == 'брейкер' else ''} | ціна {fmt(px)} | зона {fmt(r['zone_low'])}-{fmt(r['zone_high'])} | "
            f"вхід {fmt(r['entry'])} стоп {fmt(r['stop'])} ціль {fmt(r['target'])} R:R {r['rr']:.1f} | бали {r['score']}"
            + (" | УВАГА: " + "; ".join(r["flags"]) if r["flags"] else "") + " | перевір 1h")
    print("  >>> " + line)
    notify("SMC-скринер", line, "zone", f"Price in zone {r['symbol'][:-4]} {'long' if r['dir'] == 'Long' else 'short'}", tv_ticker(r["symbol"]))
    try:
        try:
            with open(seen_path, encoding="utf-8") as f:
                seen = json.load(f)
        except Exception:
            seen = {}
        seen[live_key(r)] = "у зоні (1-й дотик)"
        with open(seen_path, "w", encoding="utf-8") as f:
            json.dump(seen, f)
        append_log(a.out, stamp, [], [], live=[(r, px)])
    except Exception:
        pass


def live_wait(a, live_rows, seen_path, refresh=None):
    """Замість глухого сну між скінами: кожні --price-check секунд дивимось ціни й одразу сповіщаємо про вхід у зону."""
    total = a.watch * 60
    step = getattr(a, "price_check", 0)
    if step <= 0 or not live_rows:
        time.sleep(total)
        return
    end = time.time() + total
    fired = set()
    while True:
        left = end - time.time()
        if left <= 0:
            return
        time.sleep(min(step, left))
        try:
            prices = fetch_prices()
        except Exception:
            continue  # тимчасова помилка мережі: спробуємо на наступному кроці
        touched = live_touches(live_rows, prices, fired)
        for r, px in touched:
            fire_live(a, r, px, seen_path, dt.datetime.now().strftime("%H:%M"))
            r.update(status="у зоні (1-й дотик)", rank=0, price=px, dist=0.0)  # у результатах рядок стає червоним
        if touched and refresh:
            try:
                refresh()
            except Exception:
                pass


def watch(a, cfg):
    seen_path = os.path.join(a.out, "seen.json")
    pc = getattr(a, "price_check", 0)
    if getattr(a, "sheet_url", ""):
        print("Google Таблиця: вкладка «Скринер» оновлюється після кожного скану й живого алерту.")
    print(f"Режим стеження: скан кожні {a.watch} хв. Зупинити: Ctrl+C. Сповіщення про нові пари"
          + (f"; ціна перевіряється кожні {pc:g} с і про вхід у зону сповіщаю одразу." if pc > 0 else "."))
    try:
        while True:
            stamp = dt.datetime.now().strftime("%H:%M")
            live_rows = []
            try:
                rows, errors = scan(a, cfg, verbose=False)
                live_rows = [r for r in rows if r["rank"] == 1]
                n_new = alert_new(rows, seen_path)
                append_log(a.out, stamp, rows, cfg.get("rejects", []))
                tail = f", помилок: {len(errors)}" if errors else ""
                print(f"[{stamp}] у списку: {len(rows)}, нових: {n_new}{tail}")
                push_sheet(a, rows, stamp)
                if rows:
                    os.makedirs(a.out, exist_ok=True)
                    save_html(rows, os.path.join(a.out, "latest.html"), stamp, cfg, refresh_s=HTML_REFRESH_S)
                    save_csv(rows, os.path.join(a.out, "latest.csv"))
                    save_json(rows, os.path.join(a.out, "latest.json"), stamp)
                    save_watchlist(rows, os.path.join(a.out, "tv_watchlist.txt"))
                    if n_new:
                        print(f"      повний список: {os.path.join(a.out, 'latest.html')}")
            except Exception as e:
                print(f"[{stamp}] помилка скану: {e}")
            def refresh(rows=rows if live_rows else [], stamp=stamp):
                # після живого алерту одразу оновлюємо результати: рядок із зоною стає червоним
                save_html(rows, os.path.join(a.out, "latest.html"), stamp, cfg, refresh_s=HTML_REFRESH_S)
                save_csv(rows, os.path.join(a.out, "latest.csv"))
                save_json(rows, os.path.join(a.out, "latest.json"), stamp)
                push_sheet(a, rows, stamp)
            live_wait(a, live_rows, seen_path, refresh)
    except KeyboardInterrupt:
        print("\nЗупинено.")
        return


def main():
    p = argparse.ArgumentParser(description="SMC-скринер v1 для Binance (кандидати для ручної перевірки, не сигнали)")
    p.add_argument("--top", type=int, default=60, help="скільки USDT-пар із найбільшим обсягом перевіряти (60)")
    p.add_argument("--symbols", type=str, default="", help="свій список через кому, напр. SOLUSDT,ADAUSDT (тоді --top ігнорується)")
    p.add_argument("--interval", default="4h", help="таймфрейм (4h)")
    p.add_argument("--limit", type=int, default=500, help="скільки свічок завантажувати (500)")
    p.add_argument("--swing", type=int, default=3, help="скільки свічок з кожного боку для свінга (3)")
    p.add_argument("--events", type=int, default=3, help="скільки останніх BOS у напрямку тренду розглядати (3)")
    p.add_argument("--max-age", type=int, default=120, help="макс. вік BOS у свічках (120)")
    p.add_argument("--max-leg", type=int, default=80, help="макс. довжина імпульсу в свічках (80)")
    p.add_argument("--max-zone", type=float, default=6.0, help="макс. висота зони у %% від ціни (6)")
    p.add_argument("--approach", type=float, default=2.0, help="макс. відстань ціни до зони у %%, коли ще показуємо «підхід» (2)")
    p.add_argument("--buffer", type=float, default=0.15, help="запас стопа за межею зони у %% (0.15)")
    p.add_argument("--min-rr", type=float, default=3.0, help="мінімальний R:R (3)")
    p.add_argument("--fib-lo", type=float, default=0.5, help="нижня межа відкату Фібоначі (0.5)")
    p.add_argument("--fib-hi", type=float, default=0.79, help="верхня межа відкату Фібоначі (0.79)")
    p.add_argument("--allow-retests", action="store_true", help="показувати й повторні дотики зони")
    p.add_argument("--out", default="results", help="папка для результатів (results)")
    p.add_argument("--watch", type=float, default=0, help="сканувати в фоні кожні N хвилин і сповіщати про нові пари (напр. 30)")
    p.add_argument("--exclude", default="", help="монети, які не показувати, через кому (напр. PAXG,XAUT,MSTRB,QQQB)")
    p.add_argument("--spot", action="store_true", help="сканувати спот замість ф'ючерсів (за замовчуванням ф'ючерси USDT-M)")
    p.add_argument("--min-volume", type=float, default=30.0, help="мін. обсяг за 24 год у млн USDT, щоб відсіяти дрібні монети (30)")
    p.add_argument("--need-fvg", action="store_true", help="вимагати імбаланс (FVG) в імпульсі; за замовчуванням вимкнено")
    p.add_argument("--sound-new", default="Hero", help="звук для нової пари: назва системного звуку macOS або шлях до файлу (Hero)")
    p.add_argument("--sound-zone", default="Sosumi", help="звук, коли ціна зайшла в зону (Sosumi)")
    p.add_argument("--voice", action="store_true", help="голосове повідомлення англійською, напр. 'New pair AAVE short'")
    p.add_argument("--popup", action="store_true", help="додатково спливаюче вікно, що висить до 30 с (потрібно дати дозвіл)")
    p.add_argument("--test-alert", action="store_true", help="лише перевірити звук і банер, нічого не сканувати")
    p.add_argument("--min-stop-atr", type=float, default=0.6, metavar="N",
                   help="попереджати «тісний стоп», якщо відстань до стопа менша за N × середній діапазон свічки пари (0.6; 0 = вимкнути)")
    p.add_argument("--stop-wick", action="store_true", help="стоп за найближчим гнотом/свінгом поруч із зоною (замість краю зони)")
    p.add_argument("--wick-pct", type=float, default=3.0, help="наскільки далеко від зони шукати гніт для стопа, %% (3)")
    p.add_argument("--deep", type=float, default=0.85, help="попереджати, якщо ціна відкотилась більше цієї частки імпульсу (0.85)")
    p.add_argument("--tv-button", action="store_true", help="у попапі (--popup) кнопка «Відкрити в TradingView»: перемикає десктопний графік на пару")
    p.add_argument("--test-open", default="", help="перевірити перемикання десктопного TradingView, напр. --test-open AAVEUSDT")
    p.add_argument("--trend", choices=["strict", "bos", "flag"], default="strict",
                   help="як визначати тренд: strict = структура 4h (HH+HL / LH+LL) має збігатись з напрямком (за замовч.); "
                        "bos = лише останній BOS (старий режим); flag = показувати, але з позначкою «проти структури»")
    p.add_argument("--trend-swing", type=int, default=5, help="розмір свінга для структури тренду, свічок з кожного боку (5)")
    p.add_argument("--entry-tol", type=float, default=0.0, metavar="N",
                   help="допуск входу: лімітка на N%% висоти зони ближче до краю, з якого приходить ціна (0 = рівно середина; 50 = край)")
    p.add_argument("--rr-at-limit", action="store_true", help="мін. R:R перевіряти від лімітки з допуском (за замовч. — від середини зони)")
    p.add_argument("--ob-zone", choices=["body", "candle"], default="body",
                   help="межі ордерблока: body — тіло свічки (за замовч., v2.8), candle — уся свічка з гнотами")
    p.add_argument("--no-breakers", action="store_true", help="не шукати брейкер-блоки (пробиті ордерблоки, що стали зоною в інший бік)")
    p.add_argument("--only-sweep", action="store_true", help="показувати лише сетапи, де перед BOS був свіп ліквідності")
    p.add_argument("--min-score", type=float, default=None, help="показувати лише кандидатів із балами >= N (за замовч. без фільтра)")
    p.add_argument("--no-knife", action="store_true", help="вимкнути детектор «ножів» (різких свічок)")
    p.add_argument("--knife-mult", type=float, default=3.0, help="«ніж» = свічка, чий діапазон >= N × звичайного розміру свічки пари (3)")
    p.add_argument("--knife-look", type=int, default=30, help="скільки останніх свічок перевіряти на «ножі» (30)")
    p.add_argument("--max-atr", type=float, default=None, help="відсіювати пари, де середній діапазон свічки більший за N%% ціни (за замовч. без фільтра)")
    p.add_argument("--charts", type=int, default=12, help="скільки графіків малювати в HTML (12)")
    p.add_argument("--no-volume", action="store_true", help="вимкнути аналіз обсягу")
    p.add_argument("--vol-imp", type=float, default=1.3, help="імпульс підтверджено, якщо його середній обсяг >= N × бази (1.3)")
    p.add_argument("--vol-pb", type=float, default=0.7, help="відкат слабкий (підтверджує), якщо його обсяг <= N × імпульсу (0.7)")
    p.add_argument("--debug", action="store_true", help="показати, на якому фільтрі відсіюються пари")
    p.add_argument("--price-check", type=float, default=15, metavar="СЕК",
                   help="у режимі --watch перевіряти ціну пар «підхід до зони» кожні N секунд і сповіщати одразу, як ціна зайшла в зону (15; 0 = вимкнути)")
    p.add_argument("--sheet-url", default=os.environ.get("SMC_SHEET_URL", ""), metavar="URL",
                   help="адреса веб-застосунку Apps Script: скринер оновлює вкладку «Скринер» у Google Таблиці наживо (або змінна SMC_SHEET_URL)")
    p.add_argument("--sheet-token", default=os.environ.get("SMC_SHEET_TOKEN", ""), metavar="СЕКРЕТ",
                   help="секрет із Code.gs (або змінна SMC_SHEET_TOKEN)")
    p.add_argument("--report", nargs="?", const="today", default=None, metavar="РРРР-ММ-ДД",
                   help="показати підсумок за день із signals_log.csv (без аргументу — сьогодні) і вийти")
    p.add_argument("--selftest", action="store_true", help="перевірка логіки на синтетичних даних без мережі")
    a = p.parse_args()

    if a.selftest:
        sys.exit(selftest())
    if a.report is not None:
        day_report(a.out, None if a.report == "today" else a.report)
        return

    ALERT.update({"new": a.sound_new, "zone": a.sound_zone, "voice": a.voice, "popup": a.popup, "tv_button": a.tv_button})
    if a.test_open:
        open_tv(tv_ticker(a.test_open.upper()))
        print("Надіслано команду в TradingView. Якщо нічого не сталось: Системні налаштування -> Конфіденційність і безпека -> Універсальний доступ -> дозволь Термінал.")
        time.sleep(3)
        return
    if a.test_alert:
        notify("SMC-скринер (тест)", "НОВА ПАРА: TESTUSDT Short | тестове сповіщення", "new", "New pair test short")
        time.sleep(2)
        notify("SMC-скринер (тест)", "ЦІНА В ЗОНІ: TESTUSDT Short | тестове сповіщення", "zone", "Price in zone test short")
        time.sleep(2)
        return
    if a.spot:
        MKT.clear(); MKT.update(SPOT)
    cfg = {"knife": not a.no_knife, "knife_mult": a.knife_mult, "knife_look": a.knife_look, "max_atr": a.max_atr, "min_score": a.min_score, "volume": not a.no_volume, "vol_imp": a.vol_imp, "vol_pb": a.vol_pb, "charts": a.charts, "only_sweep": a.only_sweep, "trend": a.trend, "trend_swing": a.trend_swing, "wick": a.stop_wick, "wick_pct": a.wick_pct, "deep": a.deep, "min_stop_atr": a.min_stop_atr, "need_fvg": a.need_fvg, "swing": a.swing, "events": a.events, "max_age": a.max_age, "max_leg": a.max_leg, "max_zone": a.max_zone,
           "approach": a.approach, "buffer": a.buffer, "min_rr": a.min_rr, "allow_retests": a.allow_retests,
           "fib_lo": a.fib_lo, "fib_hi": a.fib_hi, "breakers": not a.no_breakers, "ob_zone": a.ob_zone,
           "entry_tol": max(0.0, min(50.0, a.entry_tol)), "rr_at_limit": a.rr_at_limit}
    if a.debug:
        cfg["stats"] = {}

    if a.watch > 0:
        watch(a, cfg)
        return

    rows, errors = scan(a, cfg, verbose=True)

    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\nГотово, {stamp}. Знайдено кандидатів: {len(rows)}\n")
    append_log(a.out, stamp, rows, cfg.get("rejects", []))
    if rows:
        print_table(rows)
        os.makedirs(a.out, exist_ok=True)
        base = os.path.join(a.out, "latest")  # завжди ті самі файли, кожен запуск їх перезаписує
        save_csv(rows, base + ".csv")
        save_html(rows, base + ".html", stamp, cfg)
        save_json(rows, base + ".json", stamp)
        save_watchlist(rows, os.path.join(a.out, "tv_watchlist.txt"))
        print(f"\nЗбережено: {base}.csv, {base}.html та {base}.json (у HTML є графіки TradingView)")
        print(f"Файл для імпорту у список TradingView: {os.path.join(a.out, 'tv_watchlist.txt')}")
    else:
        print("Кандидатів за поточних параметрів немає. Спробуй збільшити --approach або зменшити --min-rr.")
    if a.debug:
        print("\nДіагностика (скільки пар пройшло кожен крок; пара/імпульс рахується окремо):")
        for k in sorted(cfg["stats"]):
            print(f"  {k}: {cfg['stats'][k]}")
    if errors:
        print(f"\nПропущено пар через помилки: {len(errors)} (перші: {errors[:3]})")
    print("\nЦе кандидати для ручної перевірки, а не торгові сигнали.")


if __name__ == "__main__":
    main()
