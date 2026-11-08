# -*- coding: utf-8 -*-
"""
Уточнення входу на молодших таймфреймах (v1.5) + свічки для лайв-графіків вкладки «Скринер».

Правило (рішення Michel, 2026-11-08): ціна зайшла в зону 4h → на 15m злам структури (CHoCH) у бік угоди →
вхід від 15m-ордерблока (середина + той самий допуск, що в «Налаштуваннях»), стоп за 15m-свінгом (екстремум,
з якого пішов злам), ціль — та сама, що в сигналі 4h. 1h — лише контекст (останній BOS на 1h).

Статуси (для лонгу; шорт дзеркально):
  wait_zone  — ціна ще не заходила в зону 4h (за останні ~2 доби 15m);
  wait_choch — у зоні, чекаю закриття 15m вище останнього 15m-свінгу (рівень «злам»);
  choch      — злам є: лімітка 15m, стоп 15m, R:R від неї до цілі 4h  ← на цей перехід іде алерт;
  filled     — після зламу ціна дійшла до лімітки 15m;
  missed     — після зламу ціна дійшла до цілі без входу;
  broken     — ціна пробила стоп 4h (або стоп 15m після входу) — сценарій зламано.
Лише закриті свічки; поточна (незакрита) — тільки для малювання.
"""
import json
import os
import threading
import time

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

TF_MS = {"4h": 4 * 3600_000, "1h": 3600_000, "15m": 15 * 60_000}
DEFAULT_BASE = os.environ.get("SMC_BT_FAPI") or "https://fapi.binance.com"
_CACHE = {}
_LOCK = threading.Lock()


def fetch_klines(symbol, tf, limit=200, base=None, ttl=20):
    """Свічки ф'ючерсів Binance: [[t, o, h, l, c, v, closed]] (остання може бути незакритою). Кеш ttl секунд."""
    if tf not in TF_MS:
        raise ValueError("таймфрейм: 4h, 1h або 15m")
    limit = max(10, min(1000, int(limit)))
    key = (symbol, tf, limit)
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    if requests is None:
        raise RuntimeError("не встановлено requests")
    r = requests.get((base or DEFAULT_BASE).rstrip("/") + "/fapi/v1/klines",
                     params={"symbol": symbol, "interval": tf, "limit": limit}, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Binance HTTP {r.status_code}")
    now_ms = int(time.time() * 1000)
    out = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), int(k[6]) < now_ms] for k in r.json()]
    with _LOCK:
        _CACHE[key] = (now, out)
        if len(_CACHE) > 300:
            for k in sorted(_CACHE, key=lambda x: _CACHE[x][0])[:100]:
                _CACHE.pop(k, None)
    return out


def swings(c, n=2):
    """Пари (індекс, ціна) свінгових максимумів і мінімумів: екстремум серед n свічок з кожного боку."""
    hs, ls = [], []
    for i in range(n, len(c) - n):
        h, l = c[i][2], c[i][3]
        if all(h >= c[j][2] for j in range(i - n, i + n + 1)) and any(h > c[j][2] for j in range(i - n, i + n + 1) if j != i):
            hs.append((i, h))
        if all(l <= c[j][3] for j in range(i - n, i + n + 1)) and any(l < c[j][3] for j in range(i - n, i + n + 1) if j != i):
            ls.append((i, l))
    return hs, ls


def structure(c, n=3):
    """Останній BOS на свічках c: 1 — вгору, -1 — вниз, 0 — немає. Для контексту 1h."""
    hs, ls = swings(c, n)
    last_h = last_l = None
    hi_by, lo_by = {}, {}
    for i, p in hs:
        hi_by.setdefault(i + n, []).append(p)
    for i, p in ls:
        lo_by.setdefault(i + n, []).append(p)
    d = 0
    for j in range(len(c)):
        for p in hi_by.get(j, []):
            last_h = p
        for p in lo_by.get(j, []):
            last_l = p
        if last_h is not None and c[j][4] > last_h:
            d, last_h = 1, None
        elif last_l is not None and c[j][4] < last_l:
            d, last_l = -1, None
    return d


def refine(c15, sig, tol_pct=0.0, buffer_pct=0.05, n=2):
    """c15 — ЗАКРИТІ свічки 15m [[t,o,h,l,c,...]], sig — сигнал скринера (dir, zone_low, zone_high, stop, target).
    Повертає dict зі статусом і рівнями (див. опис модуля)."""
    d = 1 if sig["dir"] == "Long" else -1
    zl, zh, S4, TG = sig["zone_low"], sig["zone_high"], sig["stop"], sig["target"]
    out = {"status": "wait_zone", "dir": sig["dir"]}
    if len(c15) < 2 * n + 5:
        return out
    touch = (lambda k: c15[k][3] <= zh) if d == 1 else (lambda k: c15[k][2] >= zl)
    i0 = next((k for k in range(len(c15)) if touch(k)), None)
    if i0 is None:
        return out
    out["touch_t"] = c15[i0][0]
    # ідемо свічка за свічкою: оновлюємо екстремум у зоні і шукаємо закриття за останнім свінгом перед ним
    # (послідовно — щоб злам, що вже стався, не «переписувався» пізнішими свічками)
    L, sp, si, k, piv_for = i0, None, None, None, None
    for j in range(i0, len(c15)):
        if (c15[j][3] < c15[L][3]) if d == 1 else (c15[j][2] > c15[L][2]):
            L = j
        ext = c15[L][3] if d == 1 else c15[L][2]
        if (d == 1 and ext <= S4) or (d == -1 and ext >= S4):
            out.update(status="broken", why="ціна пробила стоп 4h", ext=ext, ext_t=c15[L][0])
            return out
        if piv_for != L:
            hs, ls = swings(c15[:L + 1], n)
            piv = [p for p in (hs if d == 1 else ls) if p[0] < L]
            si, sp = piv[-1] if piv else (None, None)
            piv_for = L
        if sp is not None and j > L and (c15[j][4] > sp if d == 1 else c15[j][4] < sp):
            k = j
            break
    ext = c15[L][3] if d == 1 else c15[L][2]
    out.update(ext=ext, ext_t=c15[L][0])
    if sp is None:
        out.update(status="wait_choch", why="у зоні — чекаю перший свінг 15m")
        return out
    out.update(trigger=sp, trigger_t=c15[si][0])
    if k is None:
        out.update(status="wait_choch", why=f"у зоні — чекаю закриття 15m {'вище' if d == 1 else 'нижче'} {sp:g}")
        return out
    # ордерблок 15m: остання протилежна свічка в районі екстремуму, з якого пішов злам
    ob = next((j for j in range(L, max(-1, L - 7), -1) if (c15[j][4] < c15[j][1] if d == 1 else c15[j][4] > c15[j][1])), L)
    l15, h15 = c15[ob][3], c15[ob][2]
    mid = (l15 + h15) / 2
    entry = mid + d * max(0.0, min(50.0, tol_pct)) / 100 * (h15 - l15)
    stop = ext * (1 - d * buffer_pct / 100)
    risk = abs(entry - stop)
    rr = abs(TG - entry) / risk if risk > 0 else 0.0
    out.update(status="choch", choch_t=c15[k][0], ob_low=l15, ob_high=h15, ob_t=c15[ob][0], entry=entry, stop=stop, target=TG,
               rr=round(rr, 2), why=f"злам 15m: лімітка {entry:g}, стоп {stop:g}, R:R {rr:.1f}")
    for j in range(k + 1, len(c15)):
        hit_entry = c15[j][3] <= entry if d == 1 else c15[j][2] >= entry
        hit_target = c15[j][2] >= TG if d == 1 else c15[j][3] <= TG
        if hit_entry:
            out.update(status="filled", fill_t=c15[j][0], why=f"вхід 15m: ціна дійшла до лімітки {entry:g}")
            for q in range(j, len(c15)):
                if (c15[q][3] <= stop) if d == 1 else (c15[q][2] >= stop):
                    out.update(status="broken", why="після входу 15m ціна пробила стоп 15m")
                    break
            break
        if hit_target:
            out.update(status="missed", why="ціна дійшла до цілі без входу від 15m")
            break
    return out


STATUS_TXT = {"wait_zone": "ціна ще не в зоні 4h", "wait_choch": "у зоні — чекаю злам 15m", "choch": "злам 15m — лімітка",
              "filled": "вхід 15m відбувся", "missed": "ціль без входу 15m", "broken": "сценарій зламано"}


def sig_key(r):
    return f"{r['symbol']}|{r['dir']}|{r['zone_low']:.8g}"


class LtfWatcher:
    """Раз на хвилину: для кожного сигналу «у списку» — уточнення 15m і контекст 1h; алерт на перехід у «choch»."""

    def __init__(self, data_dir, get_rows, notify, get_tol=lambda: 0.0, base=None, interval=60):
        self.data_dir, self.get_rows, self.notify, self.get_tol = data_dir, get_rows, notify, get_tol
        self.base, self.interval = base, interval
        self.state = {}
        self.seen_path = os.path.join(data_dir, "ltf_seen.json")
        try:
            with open(self.seen_path, encoding="utf-8") as f:
                self.seen = json.load(f)
        except Exception:
            self.seen = {}
        self.last_run = None
        self.error = ""

    def run_once(self):
        rows = self.get_rows() or []
        new_state = {}
        for r in rows:
            try:
                key = sig_key(r)
                c15 = [x for x in fetch_klines(r["symbol"], "15m", 200, self.base) if x[6]]
                c1h = [x for x in fetch_klines(r["symbol"], "1h", 120, self.base) if x[6]]
                res = refine(c15, r, self.get_tol())
                res["ctx_1h"] = structure(c1h, 3)
                res["status_txt"] = STATUS_TXT.get(res["status"], res["status"])
                new_state[key] = res
                if res["status"] == "choch" and self.seen.get(key) != res.get("choch_t"):
                    self.seen[key] = res.get("choch_t")
                    self._save_seen()
                    ctx = {1: "вгору", -1: "вниз"}.get(res["ctx_1h"], "немає")
                    self.notify("УТОЧНЕННЯ 15m", f"{r['symbol']} {r['dir']} — лімітка {res['entry']:g}, стоп {res['stop']:g}, "
                                                  f"ціль {res['target']:g}, R:R {res['rr']:.1f} (структура 1h: {ctx})")
            except Exception as e:  # одна монета не має зупиняти решту
                new_state[sig_key(r)] = {"status": "error", "status_txt": "немає даних", "why": str(e)[:200]}
        self.state = new_state
        self.last_run = time.time()
        return new_state

    def _save_seen(self):
        try:
            tmp = self.seen_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.seen, f, ensure_ascii=False)
            os.replace(tmp, self.seen_path)
        except OSError:
            pass

    def loop(self):
        while True:
            try:
                self.run_once()
                self.error = ""
            except Exception as e:
                self.error = str(e)
            time.sleep(self.interval)

    def start(self):
        threading.Thread(target=self.loop, daemon=True).start()
