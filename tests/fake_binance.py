# -*- coding: utf-8 -*-
"""Імітація Binance fapi для тестів бектесту (без мережі):
/fapi/v1/exchangeInfo, /fapi/v1/ticker/24hr, /fapi/v1/klines (startTime/endTime/limit до 1500, лише до «зараз»),
заголовок X-MBX-USED-WEIGHT-1M, режими: 429 на N-му запиті, монета з пізнім лістингом, дірка в історії."""
import json
import math
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TF = {"4h": 4 * 3600_000, "1h": 3600_000}
SYMBOLS = {  # символ -> (обсяг, лістинг ms або None)
    "BTCUSDT": (9e9, None), "ETHUSDT": (5e9, None), "SOLUSDT": (2e9, None), "DOGEUSDT": (1e9, None),
    "NEWUSDT": (8e8, "late"),       # з'явилась 60 днів тому
    "USDCUSDT": (7e9, None),        # стейбл — скринер відсіює
    "XAUUSDT": (6e9, None),         # метал — не COIN
    "TINYUSDT": (1e6, None),        # замалий обсяг
}


class State:
    def __init__(self):
        self.calls = []
        self.fail_429_at = set()
        self.gap = None  # (symbol, tf, from_ms, to_ms) — біржа не віддає свічки в цьому проміжку
        self.now = None
        self.delay = 0.0


S = State()


def listed_ms(sym):
    lst = SYMBOLS[sym][1]
    if lst == "late":
        return int(time.time() * 1000) - 60 * 86400_000
    return 0


def candle(sym, tf, t):
    seed = sum(map(ord, sym))
    x = t / 3600_000
    base = 100 + seed % 50
    c = base * (1 + 0.05 * math.sin(x / 37 + seed) + 0.02 * math.sin(x / 5))
    o = base * (1 + 0.05 * math.sin((x - TF[tf] / 3600_000) / 37 + seed) + 0.02 * math.sin((x - 1) / 5))
    return [t, f"{o:.4f}", f"{max(o, c) * 1.004:.4f}", f"{min(o, c) * 0.996:.4f}", f"{c:.4f}", "1000", t + TF[tf] - 1,
            "0", 10, "0", "0", "0"]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _out(self, code, obj, weight=10, extra=None):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-MBX-USED-WEIGHT-1M", str(weight))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        S.calls.append((u.path, q))
        if len(S.calls) in S.fail_429_at:
            return self._out(429, {"code": -1003, "msg": "Too many requests"}, extra={"Retry-After": "1"})
        if u.path.startswith("/api/v3/"):
            res = spot_get(u.path, q)
            return self._out(200 if res is not None else 404, res if res is not None else {"msg": "not found"})
        if u.path == "/fapi/v1/exchangeInfo":
            syms = []
            for s in SYMBOLS:
                syms.append({"symbol": s, "status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT",
                             "underlyingType": "COMMODITY" if s == "XAUUSDT" else "COIN", "underlyingSubType": []})
            return self._out(200, {"symbols": syms})
        if u.path == "/fapi/v1/ticker/24hr":
            return self._out(200, [{"symbol": s, "quoteVolume": str(v[0])} for s, v in SYMBOLS.items()])
        if u.path == "/fapi/v1/klines":
            time.sleep(S.delay)
            sym, tf = q["symbol"], q["interval"]
            step = TF[tf]
            start = int(q.get("startTime", 0))
            end = int(q.get("endTime", time.time() * 1000))
            limit = min(int(q.get("limit", 500)), 1500)
            now = S.now or int(time.time() * 1000)
            t = max(start, listed_ms(sym))
            t = t + (-t % step)  # вирівняти на початок свічки
            out = []
            while t <= end and t <= now and len(out) < limit:
                if not (S.gap and S.gap[0] == sym and S.gap[1] == tf and S.gap[2] <= t < S.gap[3]):
                    out.append(candle(sym, tf, t))
                t += step
            return self._out(200, out, weight=5 if limit <= 1000 else 10)
        return self._out(404, {"msg": "not found"})


def serve(port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------- спот (для вкладки «Спот»): /api/v3/*, денні свічки
DAYMS = 86400_000


def _spot_series(kind, n_days, now):
    """Денні свічки [t, o, h, l, c, v, close_t, qv] під потрібний сценарій."""
    out = []
    start = now - now % DAYMS - n_days * DAYMS
    for i in range(n_days):
        t = start + i * DAYMS
        if kind == "accum":        # 170 днів спаду, потім ціна стоїть, а обсяг останні 14 днів ×3
            c = 10 * (1 - 0.004 * min(i, 140)) if i < 160 else 4.5 + 0.05 * ((i % 3) - 1)
            qv = 2e6 if i < n_days - 14 else 6e6
        elif kind == "pump":       # стояла, а останні 5 днів +80% (вже вистрілила)
            c = 5.0 if i < n_days - 5 else 5.0 * (1 + 0.16 * (i - (n_days - 6)))
            qv = 2e6 if i < n_days - 14 else 6e6
        elif kind == "newl":       # лістинг 60 днів тому: пік на 5-й день, падіння −70% до 35-го дня, далі стоїть
            c = 2 + 8 * min(i, 5) / 5 if i <= 5 else (10 - 7 * min(i - 5, 30) / 30 if i <= 35 else 3.1 + 0.05 * (i % 2))
            qv = 3e6
        elif kind == "falling":    # новий лістинг, що досі робить нові мінімуми
            c = 10 * (0.97 ** i)
            qv = 3e6
        else:                      # звичайна: шум без сигналу
            c = 20 + 2 * math.sin(i / 9)
            qv = 4e6
        o = float(out[-1][4]) if out else c
        h, l = max(o, c) * 1.01, min(o, c) * 0.99
        out.append([t, f"{o:.6f}", f"{h:.6f}", f"{l:.6f}", f"{c:.6f}", f"{qv / c:.2f}", t + DAYMS - 1, f"{qv:.2f}", 100, "0", "0", "0"])
    return out


SPOT = {  # символ -> (обсяг 24h, сценарій, днів історії)
    "BTCUSDT": (9e9, "plain", 300), "ETHUSDT": (5e9, "plain", 300),            # топ — пропускаються
    "ACCUSDT": (5e6, "accum", 200), "PMPUSDT": (6e6, "pump", 200), "NEWUSDT": (8e6, "newl", 60),
    "FALUSDT": (4e6, "falling", 50), "PLAUSDT": (3e6, "plain", 200),
    "DEDUSDT": (2e5, "accum", 200),                                              # замалий обсяг
    "USDCUSDT": (7e9, "plain", 300), "BTCUPUSDT": (5e6, "accum", 200),           # стейбл і токен з плечем
    "NVDABUSDT": (5e6, "accum", 200), "牛来USDT": (4e6, "newl", 60),              # bStock (акція) і монета з ієрогліфами
}


def spot_get(path, q):
    now = S.now or int(time.time() * 1000)
    if path == "/api/v3/exchangeInfo":
        return {"symbols": [{"symbol": s, "status": "TRADING", "quoteAsset": "USDT", "baseAsset": s[:-4], "isSpotTradingAllowed": True}
                            for s in SPOT]}
    if path == "/api/v3/ticker/24hr":
        return [{"symbol": s, "quoteVolume": str(v[0])} for s, v in SPOT.items()]
    if path == "/api/v3/klines":
        vol, kind, n = SPOT[q["symbol"]]
        rows = _spot_series(kind, n, now)
        rows.append([now - now % DAYMS, rows[-1][4], rows[-1][4], rows[-1][4], rows[-1][4], "1", now - now % DAYMS + DAYMS - 1, "1", 1, "0", "0", "0"])  # незакрита
        return rows[-int(q.get("limit", 500)):]
    return None
