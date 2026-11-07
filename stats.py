# -*- coding: utf-8 -*-
"""
Підсумок журналу (етап 3): ті самі блоки, що й аркуш «Підсумок» у Google Таблиці, плюс перевірка гіпотез.

Рахуються лише закриті угоди (з результатом); відкриті показуються окремо.
Формули як у таблиці: середній R = середнє J; макс. просадка = макс. (пік накопиченого − накопичене);
беззбитковий вінрейт при R:R = 1 / (1 + середній R:R плану); «ціль взята» = MFE ≥ R:R плану.
"""
import datetime as dt
import json

SMALL = 5  # менше угод у групі = «мала вибірка»
ZONES = ("4h OB", "Локальний 1h OB", "Підтримка (тіні)")
MANAGEMENT = ("Без змін", "BE на 3R", "BE на 3R + частина на 4R", "Закрив самостійно достроково")


def _r(x, d=2):
    """Округлення «як у школі» і в Google Таблиці (0,235 -> 0,24), а не банківське, як round() у Python."""
    if x is None:
        return None
    from decimal import Decimal, ROUND_HALF_UP
    return float(Decimal(repr(round(x, 10))).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP))


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def group_row(label, ts):
    res = [t["result"] for t in ts]
    wins = [r for r in res if r > 0]
    return {"label": label, "n": len(ts), "wins": len(wins),
            "winrate": round(len(wins) / len(ts) * 100) if ts else None,
            "avg": _r(_avg(res)), "sum": _r(sum(res)) if res else 0.0, "small": len(ts) < SMALL}


def group_by(closed, key, order=None, other=None, blank="не вказано"):
    """Таблиця групи: [{label, n, wins, winrate, avg, sum, small}] у заданому порядку, порожні групи — теж (як у таблиці)."""
    buckets = {}
    for t in closed:
        k = key(t)
        k = blank if k in (None, "") else k
        if order and other and k not in order and k != blank:
            k = other
        buckets.setdefault(k, []).append(t)
    labels = list(order or [])
    if other and other not in labels:
        labels.append(other)
    labels += sorted(k for k in buckets if k not in labels and k != blank)
    if blank in buckets:
        labels.append(blank)
    return [group_row(k, buckets.get(k, [])) for k in labels]


def stop_pct(t):
    if not t.get("entry") or t.get("stop") is None:
        return None
    return abs(t["entry"] - t["stop"]) / t["entry"] * 100


def rr_bucket(t):
    rr = t.get("rr_plan")
    if rr is None:
        return None
    return "< 3" if rr < 3 else "3 – 4" if rr < 4 else "≥ 4"


def stop_bucket(t):
    p = stop_pct(t)
    if p is None:
        return None
    return "< 1%" if p < 1 else "1 – 1,5%" if p < 1.5 else "≥ 1,5%"


def instrument_key(t):
    return (t.get("instrument") or "").strip().upper() or None


def period_filter(trades, period, today=None):
    today = today or dt.date.today()
    if period in (None, "", "all"):
        return trades, "усі угоди"
    if period == "month":
        start = today.replace(day=1)
        label = f"цей місяць (з {start:%d.%m})"
    else:
        days = int(period)
        start = today - dt.timedelta(days=days)
        label = f"останні {days} днів (з {start:%d.%m})"
    return [t for t in trades if t.get("date") and dt.date.fromisoformat(t["date"]) >= start], label


def equity(closed):
    out, cum, peak, streak = [], 0.0, 0.0, 0
    max_dd = longest = 0
    for t in closed:
        cum += t["result"]
        peak = max(peak, cum)
        dd = peak - cum
        streak = streak + 1 if t["result"] < 0 else 0
        max_dd, longest = max(max_dd, dd), max(longest, streak)
        out.append({"n": t["n"], "date": t["date"], "instrument": t["instrument"], "result": t["result"],
                    "cum": round(cum, 2), "dd": round(dd, 2)})
    return out, round(max_dd, 2), longest, streak


def mfe_block(closed):
    m = [t for t in closed if t.get("mfe") is not None]
    reached = lambda x: [t for t in m if t["mfe"] >= x]
    taken = [t for t in m if t.get("rr_plan") is not None and t["mfe"] >= t["rr_plan"]]

    # Сценарії «що було б» (наближення): ціль X спрацювала б, якщо MFE ≥ X; інакше лишаємо фактичний результат
    # (ми не знаємо, куди пішла б ціна далі). БУ на 3R: якщо MFE ≥ 3, а угода закрилась у мінус -> 0.
    def scen(fn):
        return _r(sum(fn(t) for t in m)) if m else None

    risk = lambda t: t.get("risk") or 1.0
    scenarios = [
        {"label": "Фактично", "sum": scen(lambda t: t["result"]),
         "hint": "як закрились насправді"},
        {"label": "Фіксована ціль 3R", "sum": scen(lambda t: 3 * risk(t) if t["mfe"] >= 3 else t["result"]),
         "hint": "якщо MFE ≥ 3R — вихід на 3R, інакше фактичний результат"},
        {"label": "Фіксована ціль 4R", "sum": scen(lambda t: 4 * risk(t) if t["mfe"] >= 4 else t["result"]),
         "hint": "якщо MFE ≥ 4R — вихід на 4R, інакше фактичний результат"},
        {"label": "План + БУ на 3R", "sum": scen(lambda t: 0.0 if (t["mfe"] >= 3 and t["result"] < 0) else t["result"]),
         "hint": "як фактично, але мінус після 3R у плюс-зоні став би 0"},
    ]
    return {"n": len(m), "avg": _r(_avg(t["mfe"] for t in m)),
            "reached3": len(reached(3)), "reached3_closed_le0": len([t for t in reached(3) if t["result"] <= 0]),
            "reached4": len(reached(4)), "target_taken": len(taken), "scenarios": scenarios,
            "small": len(m) < SMALL}


def signal_block(closed):
    """Порівняння з рівнями скринера — лише для угод із прив'язаним сигналом."""
    rows = []
    for t in closed:
        sig = t.get("signal")
        if not isinstance(sig, dict) and t.get("signal_json"):
            try:
                sig = json.loads(t["signal_json"])
            except ValueError:
                sig = None
        if not sig or not sig.get("entry") or not sig.get("stop"):
            continue
        user_stop = stop_pct(t)
        scr_stop = abs(sig["entry"] - sig["stop"]) / sig["entry"] * 100
        zl, zh = sig.get("zone_low"), sig.get("zone_high")
        middle = None
        if zl and zh and zh > zl:
            middle = abs((t["entry"] - zl) / (zh - zl) - 0.5) <= 0.15
        rows.append({"t": t, "narrower": user_stop < 0.8 * scr_stop, "middle": middle})
    narrower = [r["t"] for r in rows if r["narrower"]]
    same = [r["t"] for r in rows if not r["narrower"]]
    mid = [r["t"] for r in rows if r["middle"] is True]
    notmid = [r["t"] for r in rows if r["middle"] is False]
    return {"n": len(rows),
            "stop": [group_row("стоп вужчий за скринерний", narrower), group_row("стоп як у скринера або ширший", same)],
            "entry": [group_row("вхід від середини зони скринера", mid), group_row("вхід не від середини", notmid)]}


TIE = 0.15  # різниця середнього R, меншу за яку вважаємо «різниці не видно»


def _cmp(a, b):
    """Порівняння двох груп за середнім R: 'більше' / 'менше' / 'рівно' / None, якщо мало даних (< 3 угод у групі)."""
    if a["n"] < 3 or b["n"] < 3 or a["avg"] is None or b["avg"] is None:
        return None
    if abs(a["avg"] - b["avg"]) < TIE:
        return "рівно"
    return "більше" if a["avg"] > b["avg"] else "менше"


def fmt_r(x):
    return "–" if x is None else f"{x:+.2f}".replace(".", ",") + "R"


def hypotheses(closed, g, mfe, sig):
    out = []

    def row(title, a, b, expect_more, extra=""):
        c = _cmp(a, b)
        if c is None:
            verdict, cls = "мало даних", "na"
        elif c == "рівно":
            verdict, cls = "різниці не видно", "tie"
        elif (c == "більше") == expect_more:
            verdict, cls = "поки на користь", "yes"
        else:
            verdict, cls = "поки проти", "no"
        text = (f"{a['label']}: {fmt_r(a['avg'])} у середньому (угод {a['n']}) проти "
                f"{b['label']}: {fmt_r(b['avg'])} (угод {b['n']}).{extra}")
        out.append({"title": title, "verdict": verdict, "cls": cls, "text": text})

    find = lambda rows, label: next((r for r in rows if r["label"] == label), group_row(label, []))
    row("1h CHoCH до входу покращує результат", find(g["choch"], "так"), find(g["choch"], "ні"), True)
    row("Угоди за правилами кращі за угоди поза правилами", find(g["rules"], "так"), find(g["rules"], "ні"), True)
    narrow = group_row("стоп < 1,5%", [t for t in closed if (stop_pct(t) or 99) < 1.5])
    wide = group_row("стоп ≥ 1,5%", [t for t in closed if (stop_pct(t) or 0) >= 1.5])
    row("Вузькі стопи (< 1,5%) гірші", narrow, wide, False,
        f" Збиткових серед вузьких: {narrow['n'] - narrow['wins']} з {narrow['n']}.")
    lo = group_row("R:R плану < 3", [t for t in closed if t.get("rr_plan") is not None and t["rr_plan"] < 3])
    hi = group_row("R:R плану ≥ 3", [t for t in closed if t.get("rr_plan") is not None and t["rr_plan"] >= 3])
    row("R:R плану ≥ 3 (урок №20) дає кращий результат", hi, lo, True)
    row("Вхід від локального 1h OB (уточнення) кращий за 4h OB", find(g["zone"], "Локальний 1h OB"), find(g["zone"], "4h OB"), True)
    scr = group_row("за скринером", [t for t in closed if t.get("by_screener") == "так"])
    noscr = group_row("ні / не вказано", [t for t in closed if t.get("by_screener") != "так"])
    row("Угоди за сигналом скринера кращі", scr, noscr, True)

    # Цілі > 4R
    if mfe["n"] < 3:
        out.append({"title": "Цілі далі за 4R виправдані", "verdict": "мало даних", "cls": "na",
                    "text": f"MFE заповнено лише в {mfe['n']} угодах — заповнюй MFE в кожній закритій угоді."})
    else:
        s = {x["label"]: x["sum"] for x in mfe["scenarios"]}
        best = max(mfe["scenarios"], key=lambda x: x["sum"] if x["sum"] is not None else -1e9)
        verdict, cls = ("поки проти", "no") if best["label"] in ("Фіксована ціль 3R", "Фіксована ціль 4R") else ("поки на користь", "yes")
        out.append({"title": "Цілі далі за 4R виправдані", "verdict": verdict, "cls": cls,
                    "text": f"З {mfe['n']} угод з MFE до 3R дійшли {mfe['reached3']}, до 4R — {mfe['reached4']}. "
                            f"Сума: фактично {fmt_r(s['Фактично'])}, з ціллю 3R {fmt_r(s['Фіксована ціль 3R'])}, "
                            f"з ціллю 4R {fmt_r(s['Фіксована ціль 4R'])} (наближення: для угод без досягнення X лишається фактичний результат)."})
    if sig["n"]:
        a, b = sig["stop"]
        row("Стоп вужчий за скринерний шкодить", a, b, False)
    return out


def build(trades, period="all", today=None):
    """trades — Journal.list() (усі угоди за номером). Повертає все для вкладки «Підсумок»."""
    sel, label = period_filter(trades, period, today)
    closed = [t for t in sel if t.get("result") is not None]
    res = [t["result"] for t in closed]
    wins, losses = [r for r in res if r > 0], [r for r in res if r < 0]
    eq, max_dd, longest, cur_streak = equity(closed)
    rr_plans = [t["rr_plan"] for t in sel if t.get("rr_plan") is not None]
    avg_rr = _avg(rr_plans)
    general = {
        "n": len(closed), "open": len(sel) - len(closed), "wins": len(wins), "losses": len(losses),
        "be": len([r for r in res if r == 0]),
        "winrate": _r(len(wins) / len(closed) * 100, 1) if closed else None,
        "sum": _r(sum(res)) if res else 0.0, "avg": _r(_avg(res)),
        "avg_win": _r(_avg(wins)), "avg_loss": _r(_avg(losses)),
        "profit_factor": _r(sum(wins) / abs(sum(losses))) if losses else None,
        "max_dd": max_dd, "longest_streak": longest, "current_streak": cur_streak,
        "current_dd": eq[-1]["dd"] if eq else 0.0,
        "avg_rr": _r(avg_rr), "be_winrate": _r(1 / (1 + avg_rr) * 100, 1) if avg_rr else None,
    }
    g = {
        "rules": group_by(closed, lambda t: t.get("rules"), ["так", "ні"]),
        "choch": group_by(closed, lambda t: t.get("choch"), ["так", "ні"]),
        "zone": group_by(closed, lambda t: t.get("zone_type"), list(ZONES), other="Інше"),
        "support": group_by(closed, lambda t: t.get("support4h"), ["так", "ні"]),
        "management": group_by(closed, lambda t: t.get("management"), list(MANAGEMENT)),
        "screener": group_by(closed, lambda t: t.get("by_screener"), ["так", "ні"]),
        "dir": group_by(closed, lambda t: t.get("dir"), ["Long", "Short"]),
        "rr": group_by(closed, rr_bucket, ["< 3", "3 – 4", "≥ 4"]),
        "stop": group_by(closed, stop_bucket, ["< 1%", "1 – 1,5%", "≥ 1,5%"]),
        "instrument": sorted(group_by(closed, instrument_key), key=lambda r: (-r["n"], -(r["sum"] or 0))),
    }
    mfe = mfe_block(closed)
    sig = signal_block(closed)
    return {"period": period, "period_label": label, "general": general, "groups": g, "mfe": mfe, "signal": sig,
            "equity": eq, "hypotheses": hypotheses(closed, g, mfe, sig), "small": SMALL}
