# -*- coding: utf-8 -*-
"""
Спот-сканер «шансових» монет (Binance Spot, USDT, денні свічки) — для невеликих довгих покупок на тижні–місяці.

Не скринер входів за SMC, а список монет, які варто роздивитися на графіку. Дві ситуації:
  1) «Накопичення»: ціна місяць стоїть у вузькому діапазоні, а обсяг торгів останні 2 тижні помітно більший,
     ніж звичайно (хтось купує, поки ціна не зрушила). Ще не «вистрілила».
  2) «Новий лістинг»: монета на Binance 2 тижні – 4 місяці, після перших піків відкотилась щонайменше вдвічі
     і перестала падати (останні дні тримається над мінімумом).
Відбір монет: спот-пари до USDT, що торгуються; без стейблів, металів, токенів з плечем; поза топ-N за обсягом;
обсяг за добу в межах [min; max] млн $ (щоб можна було купити й продати).

Це ГІПОТЕЗИ з порогами, а не перевірені правила і не фінансова порада. Такі монети можуть впасти до нуля.
"""
import datetime as dt
import json
import os
import threading
import time

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

DAY = 86400_000
DEFAULT_BASE = os.environ.get("SMC_SPOT_API") or "https://data-api.binance.vision"
STABLES = {"USDC", "FDUSD", "TUSD", "BUSD", "USDP", "DAI", "EUR", "AEUR", "USDE", "PYUSD", "XUSD", "USD1", "UST", "EURI", "GBP",
           "TRY", "BRL", "ARS", "ZAR", "UAH", "RUB", "IDRT", "BIDR", "BVND", "NGN", "PLN", "RON", "JPY", "MXN", "COP", "CZK"}
NON_CRYPTO = {"XAU", "XAG", "PAXG", "XAUT"}
LEVERAGED = ("UP", "DOWN", "BULL", "BEAR")
# bStocks — токенізовані акції США на споті Binance (тікер акції + «B»: NVDAB, GOOGLB…). Це не крипта — відсіюємо.
STOCKS = set("""AAPL MSFT NVDA GOOGL GOOG AMZN META TSLA AVGO BRK BRKB NFLX AMD INTC ORCL CRM ADBE CSCO QCOM TXN IBM MU ARM ASML TSM
SMCI PLTR SNOW SHOP UBER ABNB PYPL SQ XYZ COIN HOOD MSTR CRCL CBRS GME AMC RIVN LCID NIO BABA JD PDD BIDU DIS NKE SBUX MCD KO PEP
WMT COST TGT HD LOW JPM BAC WFC GS MS C V MA AXP BLK SCHW UNH JNJ PFE MRK LLY ABBV MRNA BMY CVS XOM CVX COP OXY BA LMT RTX GE CAT
DE F GM T VZ TMUS SPY QQQ IWM DIA VOO VTI GLD SLV TLT ARKK RBLX SPOT ZM DOCU ROKU SNAP PINS U NET CRWD PANW ZS DDOG MDB OKTA TEAM
NOW INTU WDAY ADSK ANET DELL HPQ HPE SONY TM HMC SAP ACN IONQ RGTI QBTS SOFI AFRM UPST DKNG CVNA CELH ENPH FSLR SEDG MARA RIOT
CLSK HUT BITF CIFR IREN WULF CORZ BTDR GLXY BMNR SBET OPEN APP RDDT ASTS OKLO SMR NBIS CRWV ALAB AVAV KTOS RKLB LUNR SPCX""".split())


def is_bstock(base):
    return len(base) > 2 and base.endswith("B") and (base[:-1] in STOCKS or base in STOCKS)

DEFAULTS = {
    "skip_top": 50,        # пропустити найпопулярніші (топ-N за обсягом)
    "exclude": "",         # монети, які не показувати (через кому)
    "vol_min": 1.0,        # мін. обсяг за добу, млн $
    "vol_max": 30.0,       # макс. обсяг за добу, млн $
    # накопичення
    "acc_days": 30,        # скільки днів дивимось на діапазон ціни
    "acc_range": 35.0,     # макс. діапазон ціни за ці дні, % (висота / середина)
    "acc_vol_ratio": 1.8,  # обсяг за 14 днів ÷ звичайний (60 днів до того) — не менше
    "acc_max_up": 25.0,    # ціна не вище за мінімум діапазону більш ніж на N% (ще не вистрілила)
    # новий лістинг
    "new_min_days": 14,
    "new_max_days": 120,
    "new_drawdown": 50.0,  # відкат від максимуму після лістингу — не менше, %
    "new_hold_days": 7,    # останні N днів без нового мінімуму
}


def clean(p):
    out = dict(DEFAULTS)
    for k, v in (p or {}).items():
        if k == "exclude":
            import re as _re
            out[k] = ", ".join(dict.fromkeys(x.upper().replace("USDT", "") for x in _re.split(r"[\s,;]+", str(v or "")) if x))
            continue
        if k in DEFAULTS:
            try:
                out[k] = type(DEFAULTS[k])(float(v))
            except (TypeError, ValueError):
                raise ValueError(f"«{k}» — число")
    out["skip_top"] = max(0, min(300, out["skip_top"]))
    if out["vol_min"] < 0 or out["vol_max"] <= out["vol_min"]:
        raise ValueError("обсяг: мін. має бути менший за макс.")
    if not (1 <= out["new_min_days"] < out["new_max_days"] <= 400):
        raise ValueError("вік лістингу: від 1 до 400 днів, «від» менше за «до»")
    return out


class SpotError(Exception):
    pass


def _get(base, path, params=None, retries=3, sleep=time.sleep):
    if requests is None:
        raise SpotError("не встановлено пакет requests")
    last = ""
    for i in range(retries):
        try:
            r = requests.get(base.rstrip("/") + path, params=params, timeout=20)
        except requests.RequestException as e:
            last = f"мережа: {e.__class__.__name__}"
            sleep(2 * (i + 1))
            continue
        if r.status_code in (429, 418):
            sleep(min(int(r.headers.get("Retry-After") or 30), 120))
            last = f"ліміт Binance (HTTP {r.status_code})"
            continue
        if r.status_code != 200:
            raise SpotError(f"HTTP {r.status_code}: {r.text[:120]}")
        return r.json()
    raise SpotError(last or "немає відповіді від Binance")


def universe(base, P):
    """Спот-пари USDT поза топ-N з обсягом у межах. [(symbol, quote_volume_usd)] за спаданням обсягу."""
    info = _get(base, "/api/v3/exchangeInfo")
    excl = {x.strip().upper() for x in (P.get("exclude") or "").split(",") if x.strip()}
    ok = set()
    for s in info.get("symbols", []):
        b = s.get("baseAsset", "")
        if (s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT" and s.get("isSpotTradingAllowed", True)
                and b not in STABLES and b not in NON_CRYPTO and not any(b.endswith(x) and len(b) > len(x) for x in LEVERAGED)
                and not is_bstock(b) and b.upper() not in excl):
            ok.add(s["symbol"])
    tick = _get(base, "/api/v3/ticker/24hr")
    vols = sorted(((t["symbol"], float(t.get("quoteVolume") or 0)) for t in tick if t.get("symbol") in ok), key=lambda x: -x[1])
    rest = vols[P["skip_top"]:]
    return [(s, v) for s, v in rest if P["vol_min"] * 1e6 <= v <= P["vol_max"] * 1e6]


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def analyze(symbol, kl, P, now_ms=None):
    """kl — денні свічки [(t,o,h,l,c,qv)] (лише закриті). Повертає список знахідок (0–2)."""
    out = []
    if len(kl) < 5:
        return out
    now_ms = now_ms or int(time.time() * 1000)
    closes = [x[4] for x in kl]
    px = closes[-1]
    age = (now_ms - kl[0][0]) / DAY
    hi_all = max(x[2] for x in kl)
    base_info = {"symbol": symbol, "price": px, "age_days": round(age), "from_high": round((1 - px / hi_all) * 100, 1) if hi_all else None,
                 "vol_24h": kl[-1][5], "chg_7d": round((px / closes[-8] - 1) * 100, 1) if len(closes) >= 8 else None}
    # ---- накопичення: потрібна історія ≥ 74 днів (14 + 60)
    n = P["acc_days"]
    if len(kl) >= 74 and len(kl) >= n:
        win = kl[-n:]
        lo, hi = min(x[3] for x in win), max(x[2] for x in win)
        rng = (hi - lo) / ((hi + lo) / 2) * 100 if hi + lo else 999
        v14 = _mean(x[5] for x in kl[-14:])
        v60 = _mean(x[5] for x in kl[-74:-14])
        ratio = v14 / v60 if v60 > 0 else 0
        up = (px / lo - 1) * 100 if lo else 999
        v7, v7p = _mean(x[5] for x in kl[-7:]), _mean(x[5] for x in kl[-14:-7])
        if rng <= P["acc_range"] and ratio >= P["acc_vol_ratio"] and up <= P["acc_max_up"]:
            score = round(min(ratio, 6) + (1 if v7 >= v7p else 0) + max(0, (P["acc_range"] - rng) / P["acc_range"]), 2)
            out.append(dict(base_info, kind="накопичення", score=score, range_pct=round(rng, 1), vol_ratio=round(ratio, 2),
                            vol_rising=v7 >= v7p, base_low=lo, base_high=hi, above_low=round(up, 1),
                            why=f"ціна {n} днів у діапазоні {rng:.0f}%, обсяг за 2 тижні ×{ratio:.1f} до звичайного".replace(".", ",")
                                + (", і далі росте" if v7 >= v7p else "")))
    # ---- новий лістинг
    if P["new_min_days"] <= age <= P["new_max_days"]:
        peak_i = max(range(len(kl)), key=lambda i: kl[i][2])
        peak = kl[peak_i][2]
        dd = (1 - px / peak) * 100 if peak else 0
        hold = P["new_hold_days"]
        after = kl[peak_i:]
        low_all = min(x[3] for x in after)
        low_i = max(i for i, x in enumerate(after) if x[3] == low_all) + peak_i
        days_since_low = len(kl) - 1 - low_i
        if dd >= P["new_drawdown"] and days_since_low >= hold and px > low_all:
            recent = kl[-hold:]
            rng = (max(x[2] for x in recent) - min(x[3] for x in recent)) / px * 100
            score = round(dd / 25 + min(days_since_low, 30) / 10 + (1 if _mean(x[5] for x in kl[-7:]) >= _mean(x[5] for x in kl[-14:-7]) else 0), 2)
            out.append(dict(base_info, kind="новий лістинг", score=score, drawdown=round(dd, 1), days_since_low=days_since_low,
                            base_low=low_all, base_high=max(x[2] for x in kl[low_i:]), range_pct=round(rng, 1),
                            why=f"на Binance {age:.0f} днів; від піку −{dd:.0f}%, нового мінімуму немає {days_since_low} днів"))
    return out


def klines_1d(base, symbol, limit=200):
    raw = _get(base, "/api/v3/klines", {"symbol": symbol, "interval": "1d", "limit": limit})
    now = int(time.time() * 1000)
    out = []
    for k in raw:
        t, close_t = int(k[0]), int(k[6])
        if close_t >= now:      # незакрита денна свічка
            continue
        qv = float(k[7]) if len(k) > 7 and float(k[7] or 0) > 0 else float(k[5]) * float(k[4])
        out.append((t, float(k[1]), float(k[2]), float(k[3]), float(k[4]), qv))
    return out


class SpotScanner:
    """Скан у фоновому потоці; результат — data/spot_latest.json, перші появи монет — data/spot_seen.json."""

    def __init__(self, data_dir, base=None):
        self.data_dir = data_dir
        self.base = base or DEFAULT_BASE
        self.path = os.path.join(data_dir, "spot_latest.json")
        self.seen_path = os.path.join(data_dir, "spot_seen.json")
        self.lock = threading.Lock()
        self.thread = None
        self.progress = {"status": "idle"}

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def latest(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def start(self, params=None):
        P = clean(params)
        with self.lock:
            if self.running():
                return False
            self.progress = {"status": "running", "done": 0, "total": 0, "phase": "список монет", "started": time.time()}
            self.thread = threading.Thread(target=self._run, args=(P,), daemon=True)
            self.thread.start()
            return True

    def _run(self, P):
        t0 = time.time()
        try:
            syms = universe(self.base, P)
            self.progress.update(total=len(syms), phase="денні свічки")
            found, errors = [], []
            for i, (s, v) in enumerate(syms):
                try:
                    kl = klines_1d(self.base, s)
                    for r in analyze(s, kl, P):
                        r["vol_24h"] = v
                        found.append(r)
                except Exception as e:
                    errors.append(f"{s}: {e}")
                self.progress.update(done=i + 1, symbol=s)
            seen = {}
            try:
                with open(self.seen_path, encoding="utf-8") as f:
                    seen = json.load(f)
            except Exception:
                pass
            today = dt.date.today().isoformat()
            for r in found:
                k = f"{r['symbol']}|{r['kind']}"
                r["first_seen"] = seen.setdefault(k, today)
                r["new_today"] = r["first_seen"] == today
            found.sort(key=lambda r: (-r["score"]))
            res = {"generated": dt.datetime.now().isoformat(timespec="seconds"), "params": P, "checked": len(syms),
                   "found": found, "errors": errors[:20], "elapsed_s": round(time.time() - t0, 1)}
            os.makedirs(self.data_dir, exist_ok=True)
            for path, obj in ((self.path, res), (self.seen_path, seen)):
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False, indent=1)
                os.replace(tmp, path)
            self.progress = {"status": "done", "done": len(syms), "total": len(syms), "found": len(found),
                             "errors": len(errors), "elapsed_s": res["elapsed_s"]}
        except Exception as e:
            self.progress = {"status": "error", "error": str(e)}
