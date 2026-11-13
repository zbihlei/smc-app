# -*- coding: utf-8 -*-
"""
Форекс-дані для Rulebound з MetaTrader 5 (v1.6).

Робот SMC_Bridge.mq5 (папка mt5/) працює в терміналі MT5 Michel (RoboForex) і пише у СПІЛЬНУ папку файлів MT5
(Common/Files/SMC):
  status.json            — раз на кілька секунд: час GMT і сервера, сервер брокера, по кожній парі фактична назва
                            символу (з суфіксом, якщо є), bid/ask, час останнього тіку;
  EURUSD_H4.csv / _H1 / _M15 — раз на хвилину: останні N свічок «час_сервера,open,high,low,close,тиковий_обсяг».
Цей модуль їх читає й віддає в тому ж вигляді, що й свічки Binance у скринері (час — UTC, мс).

Робот НІЧОГО не торгує: лише читає історію й ціни. Права на угоди немає.
"""
import glob
import json
import os
import time

DEFAULT_PAIRS = ("EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,USDCAD,NZDUSD,"
                 "EURGBP,EURJPY,EURCHF,EURAUD,EURCAD,EURNZD,GBPJPY,GBPCHF,GBPAUD,GBPCAD,GBPNZD,"
                 "AUDJPY,AUDCHF,AUDCAD,AUDNZD,NZDJPY,NZDCHF,NZDCAD,CADJPY,CADCHF,CHFJPY")
CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "TRY",
              "ZAR", "MXN", "SGD", "HKD", "CNH", "ILS", "RUB"}
TF_FILE = {"4h": "H4", "1h": "H1", "15m": "M15"}
TF_S = {"4h": 14400, "1h": 3600, "15m": 900}
CANDLES_STALE_S = 300  # файли свічок старші (при відкритому ринку) — MT5 щойно запущено, робот ще не переписав свічки
STALE_S = 180          # status.json не оновлювався довше — термінал закритий або робот знято з графіка

# де шукати папку SMC (MT5 для macOS — це Wine-«пляшка» в ~/Library/Application Support)
SEARCH = [
    "~/Library/Application Support/*/drive_c/users/*/AppData/Roaming/MetaQuotes/Terminal/Common/Files/SMC",
    "~/Library/Application Support/*/drive_c/users/*/Application Data/MetaQuotes/Terminal/Common/Files/SMC",
    "~/Library/Application Support/CrossOver/Bottles/*/drive_c/users/*/AppData/Roaming/MetaQuotes/Terminal/Common/Files/SMC",
    "~/.wine/drive_c/users/*/AppData/Roaming/MetaQuotes/Terminal/Common/Files/SMC",
    "~/Library/Application Support/*/drive_c/Program Files/*/MQL5/Files/SMC",
]


class FxError(RuntimeError):
    pass


def parse_pairs(text):
    """«eurusd, GBPUSD» → ['EURUSD', 'GBPUSD']; лише валютні пари з 6 літер (без золота, індексів тощо)."""
    out, bad = [], []
    for x in (text or "").replace(";", ",").replace(" ", ",").split(","):
        x = x.strip().upper()
        if not x:
            continue
        if len(x) == 6 and x[:3] in CURRENCIES and x[3:] in CURRENCIES and x[:3] != x[3:]:
            if x not in out:
                out.append(x)
        else:
            bad.append(x)
    return out, bad


def find_dir(override=""):
    """Папка з файлами робота: вказана вручну або знайдена автоматично (з найсвіжішим status.json)."""
    if override:
        d = os.path.abspath(os.path.expanduser(override))
        return d if os.path.isdir(d) else None
    best, best_m = None, -1.0
    for pat in SEARCH:
        for d in glob.glob(os.path.expanduser(pat)):
            try:
                m = os.path.getmtime(os.path.join(d, "status.json"))
            except OSError:
                m = 0.0
            if m > best_m:
                best, best_m = d, m
    return best


def market_open(now=None):
    """Форекс закритий приблизно з п'ятниці 21:00 до неділі 21:00 UTC (точний час залежить від брокера й переходу на літній час)."""
    t = time.gmtime(now if now is not None else time.time())
    wd, h = t.tm_wday, t.tm_hour  # пн=0 … нд=6
    if wd == 5:
        return False
    if wd == 4 and h >= 21:
        return False
    if wd == 6 and h < 21:
        return False
    return True


class Source:
    def __init__(self, dir_override="", pairs=DEFAULT_PAIRS):
        self.dir_override = dir_override
        self.pairs, _ = parse_pairs(pairs)
        self._cache = {}

    def dir(self):
        d = find_dir(self.dir_override)
        if not d:
            raise FxError("не знайдено файлів робота SMC_Bridge: відкрий MT5 і постав робота на будь-який графік"
                          + (f" (папка «{self.dir_override}» не існує)" if self.dir_override else ""))
        return d

    def status(self, check_fresh=False):
        d = self.dir()
        p = os.path.join(d, "status.json")
        try:
            with open(p, encoding="utf-8-sig") as f:
                st = json.load(f)
        except FileNotFoundError:
            raise FxError(f"у {d} ще немає status.json — робот SMC_Bridge не запущений")
        except ValueError:
            time.sleep(0.2)
            with open(p, encoding="utf-8-sig") as f:
                st = json.load(f)
        age = max(0.0, time.time() - os.path.getmtime(p))
        off = int(st.get("server_time", 0)) - int(st.get("gmt", 0))
        st["offset_s"] = int(round(off / 900.0)) * 900          # зсув часу сервера від UTC (напр. +2 год)
        st["age_s"] = age
        st["dir"] = d
        st["fresh"] = age <= STALE_S
        st["market_open"] = market_open()
        if check_fresh and not st["fresh"]:
            raise FxError(f"MT5 не оновлює дані {int(age // 60)} хв — термінал закритий або робот SMC_Bridge знято з графіка")
        return st

    def klines(self, pair, tf, limit=500, st=None):
        """[[t_utc_ms, o, h, l, c, тиковий_обсяг, закрита?]] — як ltf.fetch_klines для Binance."""
        if tf not in TF_FILE:
            raise ValueError("таймфрейм: 4h, 1h або 15m")
        st = st or self.status()
        p = os.path.join(st["dir"], f"{pair}_{TF_FILE[tf]}.csv")
        try:
            m = os.path.getmtime(p)
        except OSError:
            raise FxError(f"{pair}: немає свічок {tf} від MT5 (пари немає у брокера або історія ще вантажиться)")
        key = (p, st["offset_s"])
        hit = self._cache.get(key)
        if hit and hit[0] == m:
            rows = hit[1]
        else:
            rows = []
            with open(p, encoding="utf-8-sig") as f:
                for line in f:
                    x = line.strip().split(",")
                    if len(x) < 6:
                        continue
                    try:
                        rows.append([(int(x[0]) - st["offset_s"]) * 1000, float(x[1]), float(x[2]), float(x[3]),
                                     float(x[4]), float(x[5])])
                    except ValueError:
                        continue
            self._cache[key] = (m, rows)
        now_ms = time.time() * 1000
        step = TF_S[tf] * 1000
        out = [r + [r[0] + step <= now_ms] for r in rows[-max(1, int(limit)):]]
        if not out:
            raise FxError(f"{pair}: файл свічок {tf} порожній")
        return out

    def check_candles(self, st, pairs, tf="4h"):
        """Свічки мають бути свіжими, а не лише status.json: після запуску MT5 робот оновлює стан одразу,
        а файли свічок — до хвилини пізніше; скан по старих файлах дав би сигнали кількаденної давнини."""
        if not st.get("market_open", True):
            return                                  # вихідні: свічки й не мають оновлюватись
        old = []
        for p in pairs:
            try:
                age = time.time() - os.path.getmtime(os.path.join(st["dir"], f"{p}_{TF_FILE[tf]}.csv"))
            except OSError:
                continue
            if age > CANDLES_STALE_S:
                old.append((age, p))
        if old:
            age, p = max(old)
            ago = f"{int(age // 3600)} год" if age >= 7200 else f"{int(age // 60)} хв"
            raise FxError(f"MT5 ще не оновив свічки ({p}: файл {ago} тому; застарілих пар: {len(old)}) — "
                          "зачекай хвилину після запуску MT5, скан повториться сам")

    def prices(self, st=None):
        """Поточні ціни (bid) усіх пар зі status.json."""
        st = st or self.status()
        return {x["pair"]: float(x["bid"]) for x in st.get("pairs", []) if x.get("ok") and x.get("bid")}

    def available(self, st=None):
        """Пари зі списку, які робот знайшов у брокера."""
        st = st or self.status()
        have = {x["pair"] for x in st.get("pairs", []) if x.get("ok")}
        return [p for p in self.pairs if p in have]


def describe(dir_override="", pairs=DEFAULT_PAIRS):
    """Стан для «Налаштувань» / вкладки: знайдено чи ні, свіжість, сервер, які пари є."""
    src = Source(dir_override, pairs)
    try:
        st = src.status()
    except FxError as e:
        return {"ok": False, "error": str(e), "pairs": src.pairs}
    found = {x["pair"]: x for x in st.get("pairs", [])}
    missing = [p for p in src.pairs if not found.get(p, {}).get("ok")]
    return {"ok": st["fresh"], "error": "" if st["fresh"] else f"MT5 не оновлює дані {int(st['age_s'] // 60)} хв — термінал закритий?",
            "dir": st["dir"], "server": st.get("server", ""), "age_s": round(st["age_s"]), "offset_h": st["offset_s"] / 3600,
            "market_open": st["market_open"], "pairs": src.pairs, "missing": missing,
            "names": {p: found[p].get("name") for p in src.pairs if p in found}, "ea_version": st.get("ea_version", "")}
