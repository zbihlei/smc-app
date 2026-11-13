#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Запускач скринера для ФОРЕКСУ (v1.6): та сама стратегія smc_screener.py, але свічки й ціни — з MT5 Michel
(робот SMC_Bridge, див. fx_source.py), а не з Binance.

Усе, що робить screener_runner.py (latest_app.json, Telegram, сон Mac, «чому вибула»), працює й тут.
Підміняються лише джерела даних і посилання:
  fetch_top_symbols -> пари зі списку (SMC_FX_PAIRS), які є в брокера;
  fetch_klines      -> свічки з файлів MT5 (час — UTC);
  fetch_prices      -> поточні ціни зі status.json;
  TradingView       -> FX:EURUSD.
Фільтр обсягу в $ (--min-volume) для форексу не застосовується (обсяг там тиковий).

Використання (це робить сам додаток):
  python3 -u fx_runner.py /шлях/до/smc_screener.py --watch 30 --approach 4 ... --out results/forex
Змінні середовища: SMC_FX_DIR (папка файлів робота; порожньо — шукати автоматично), SMC_FX_PAIRS.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fx_source  # noqa: E402
import screener_runner as SR  # noqa: E402


def install_fx(mod, src):
    mod.MKT = dict(mod.MKT, futures=False, forex=True, bases=[], ticker="", klines="", info="", price="")

    def fetch_top_symbols(n, min_volume=0.0):
        st = src.status(check_fresh=True)          # MT5 закритий -> скан не вдався -> повтор за 2 хв
        pairs = src.available(st)
        src.check_candles(st, pairs)                # свічки старі (MT5 щойно запущено) -> теж повтор за 2 хв
        miss = [p for p in src.pairs if p not in pairs]
        if miss:
            print(f"Немає в брокера / ще не завантажено: {', '.join(miss)}")
        if not st["market_open"]:
            print("Форекс-ринок закритий (вихідні) — свічки не оновлюються, показую останній стан.")
        return pairs[:n] if n else pairs

    def fetch_klines(symbol, interval, limit):
        return [{"t": k[0], "o": k[1], "h": k[2], "l": k[3], "c": k[4], "v": k[5], "closed": k[6]}
                for k in src.klines(symbol, interval, limit)]

    def fetch_prices():
        return src.prices(src.status(check_fresh=True))

    def tv_ticker(symbol):
        return "FX:" + symbol

    def tv_link(symbol):
        return f"https://www.tradingview.com/chart/?symbol=FX%3A{symbol}&interval=240"

    mod.fetch_top_symbols = fetch_top_symbols
    mod.fetch_klines = fetch_klines
    mod.fetch_prices = fetch_prices
    mod.tv_ticker = tv_ticker
    mod.tv_link = tv_link
    mod.binance_link = tv_link

    orig_notify = mod.notify  # уже з дублюванням у Telegram (screener_runner)

    def notify(title, text, kind="new", speak="", ticker=""):
        pair = ticker.split(":")[-1] if ticker else ""
        if speak and pair:  # скринер обрізає «USDT» з кінця назви — для форексу назва ціла
            speak = re.sub(r"^(New pair|Price in zone) \S+", lambda m: f"{m.group(1)} {pair}", speak)
        orig_notify("SMC-скринер ФОРЕКС" if title == "SMC-скринер" else title, text, kind, speak, ticker)

    mod.notify = notify


def main():
    if len(sys.argv) < 2:
        print("Використання: fx_runner.py /шлях/до/smc_screener.py [параметри скринера]")
        sys.exit(2)
    path = os.path.abspath(sys.argv[1])
    if not os.path.exists(path):
        print(f"Не знайдено скринер: {path}")
        sys.exit(2)
    mod = SR.load_screener(path)
    SR.install_hooks(mod)
    src = fx_source.Source(os.environ.get("SMC_FX_DIR", ""), os.environ.get("SMC_FX_PAIRS") or fx_source.DEFAULT_PAIRS)
    install_fx(mod, src)
    sys.argv = [path] + sys.argv[2:]
    print(f"[додаток] форекс-скринер: {path}; пар у списку: {len(src.pairs)}; дані — MT5 (SMC_Bridge)", flush=True)
    try:
        print(f"[додаток] файли MT5: {src.dir()}", flush=True)
    except fx_source.FxError as e:
        print(f"[додаток] {e}", flush=True)
    mod.main()


if __name__ == "__main__":
    main()
