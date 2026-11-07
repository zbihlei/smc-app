# -*- coding: utf-8 -*-
"""Спільне для тестів бектесту: синтетичний кеш свічок і аргументи живого скринера."""
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backtest_data as BD  # noqa: E402
import backtest_signals as BS  # noqa: E402

H1, H4 = BS.H1, BS.H4
SYMS = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
ARGS = ["--watch", "30", "--approach", "4", "--top", "100", "--price-check", "15", "--min-stop-atr", "0.6",
        "--trend", "strict", "--out", tempfile.mkdtemp(), "--popup", "--voice", "--tv-button", "--stop-wick"]


def make_cache(data_dir, symbols, months=2, seed=7, alter_after=None):
    """Випадкові блукання 1h і 4h, зібрані з тих самих годин (як на біржі). alter_after — змінити «майбутнє» після мітки."""
    cache = BD.Cache(data_dir)
    end = BD.now_ms() - BD.now_ms() % H4
    start = BD.period(months, end)[0]
    t0 = start - (BS.WINDOW + 10) * H4
    for si, sym in enumerate(symbols):
        rnd = random.Random(seed + si)
        alt = random.Random(999 + si)
        p = 50.0 + 30 * si
        h1 = []
        for t in range(t0, end, H1):
            r = alt if (alter_after and t >= alter_after) else rnd
            o = p
            p = p * (1 + r.gauss(0, 0.007))
            h = max(o, p) * (1 + abs(r.gauss(0, 0.002)))
            lo = min(o, p) * (1 - abs(r.gauss(0, 0.002)))
            h1.append((t, o, h, lo, p, r.uniform(1e5, 5e5)))
        h4 = []
        for i in range(0, len(h1), 4):
            g = h1[i:i + 4]
            if len(g) < 4:
                break
            h4.append((g[0][0], g[0][1], max(x[2] for x in g), min(x[3] for x in g), g[-1][4], sum(x[5] for x in g)))
        cache.insert(sym, "4h", h4)
        cache.insert(sym, "1h", [x for x in h1 if x[0] >= start - H1])
    cache.set_symbols([(s, 1e9 - i) for i, s in enumerate(symbols)])
    rid = cache.run_start(months, len(symbols), start, end)
    cache.run_end(rid, status="done", symbols=len(symbols), candles=0, requests=0, errors="")
    return start, end
