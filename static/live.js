/* SMC App — лайв-графіки 4h + 1h + 15m у вкладці «Скринер» і уточнення входу на 15m (v1.5).
   Історія свічок — через додаток (/api/klines), оновлення наживо — WebSocket Binance Futures;
   якщо WebSocket недоступний — додаток опитує свічки кожні 15 с. Використовує $, esc, api, fmt, fix (app.js). */
"use strict";

const LV = { mounts: new Map(), ws: null, wsKey: "", wsOk: false, poll: null, ltf: {}, ltfTimer: null, tz: -new Date().getTimezoneOffset() * 60 };
const LV_TF = { "4h": 14400, "1h": 3600, "15m": 900 };
const LV_LIMIT = { "4h": 200, "1h": 240, "15m": 300 };

function lvKey(r) { return `${r.symbol}|${r.dir}|${Number(r.zone_low).toPrecision(8).replace(/\.?0+$/, "")}`; }
function lvBar(b) { return { time: Math.round(b[0] / 1000) + LV.tz, open: b[1], high: b[2], low: b[3], close: b[4] }; }

function lvChart(el, r, tf, bars) {
  const LWC = LightweightCharts;
  const chart = LWC.createChart(el, {
    width: el.clientWidth, height: el.clientHeight || 360,
    layout: { background: { color: "#ffffff" }, textColor: "#131722", fontFamily: "-apple-system, Arial", fontSize: 11 },
    grid: { vertLines: { color: "#f0f3fa" }, horzLines: { color: "#f0f3fa" } },
    rightPriceScale: { borderColor: "#d1d4dc", scaleMargins: { top: 0.08, bottom: 0.08 } },
    timeScale: { borderColor: "#d1d4dc", timeVisible: true, secondsVisible: false, rightOffset: 14 },
    crosshair: { mode: 0 },
  });
  const lvl = [r.stop, r.target, r.zone_low, r.zone_high, r.entry];
  const series = chart.addCandlestickSeries({
    upColor: "#ffffff", downColor: "#000000", borderUpColor: "#000000", borderDownColor: "#000000",
    wickUpColor: "#000000", wickDownColor: "#000000", priceLineVisible: true, lastValueVisible: true,
    autoscaleInfoProvider: orig => {
      const o = orig(), extra = [...lvl, ...(tf === "15m" && st.ltf ? [st.ltf.stop, st.ltf.entry, st.ltf.trigger].filter(x => x != null) : [])];
      const lo = Math.min(...extra), hi = Math.max(...extra);
      if (!o) return { priceRange: { minValue: lo, maxValue: hi } };
      return { priceRange: { minValue: Math.min(o.priceRange.minValue, lo), maxValue: Math.max(o.priceRange.maxValue, hi) }, margins: o.margins };
    },
  });
  const st = { chart, series, tf, el, lines: [], ltf: null, last: 0 };
  series.setData(bars.map(lvBar));
  st.last = bars.length ? lvBar(bars[bars.length - 1]).time : 0;
  const pl = (price, color, title, style, width) => series.createPriceLine({ price, color, lineWidth: width || 1, lineStyle: style, axisLabelVisible: true, title });
  pl(r.entry, "#434651", tf === "4h" ? "Вхід" : "Вхід 4h", 2);
  pl(r.stop, "#f23645", tf === "4h" ? "Стоп" : "Стоп 4h", 2);
  pl(r.target, "#089981", "Ціль", 2);
  // зона 4h — напівпрозорий прямокутник від свічки зони (або на всю ширину на молодших ТФ)
  const mk = css => { const e = document.createElement("div"); e.style.cssText = "position:absolute;pointer-events:none;z-index:3;" + css; el.appendChild(e); return e; };
  st.zoneD = mk("background:rgba(150,150,150,0.30);");
  st.midD = mk("border-top:1px dashed #9c27b0;height:0;");
  st.riskD = mk("background:rgba(120,123,134,0.40);");
  st.rewD = mk("background:rgba(77,182,205,0.40);color:#0b3c49;font:11px -apple-system,Arial;padding:1px 4px;overflow:hidden;white-space:nowrap;");
  st.obD = mk("background:rgba(41,98,255,0.18);border:1px solid rgba(41,98,255,0.5);display:none;");
  st.zt = Math.round((r.zone_t || 0) / 1000) + LV.tz;
  st.redraw = () => {
    if (st.dead || !el.isConnected) return;  // графік уже знищено (картку перемалювали)
    const ts = chart.timeScale(), Y = p => series.priceToCoordinate(p);
    const right = Math.max(0, el.clientWidth - (chart.priceScale("right").width() || 60));
    const box = (e, x, w, ya, yb) => Object.assign(e.style, { left: x + "px", width: Math.max(0, w) + "px", top: Math.min(ya, yb) + "px", height: Math.max(1, Math.abs(ya - yb)) + "px" });
    const xz = Math.max(0, ts.timeToCoordinate(st.zt) ?? 0);
    const ya = Y(r.zone_high), yb = Y(r.zone_low), ym = Y((r.zone_high + r.zone_low) / 2);
    if (ya == null || yb == null || ym == null) return;
    box(st.zoneD, xz, right - xz, ya, yb);
    Object.assign(st.midD.style, { left: xz + "px", width: Math.max(0, right - xz) + "px", top: ym + "px" });
    // ризик і шлях до цілі — праворуч від останньої свічки; на 15m після зламу — від лімітки 15m
    const L = st.ltf, own = tf === "15m" && L && L.entry != null;
    const en = own ? L.entry : r.entry, sp = own ? L.stop : r.stop, rr = own ? L.rr : r.rr;
    const x0 = Math.min(right, Math.max(0, ts.timeToCoordinate(st.last) ?? right - 40));
    const ye = Y(en), ys = Y(sp), yt = Y(r.target);
    if (ye != null && ys != null && yt != null) {
      box(st.riskD, x0, right - x0, ye, ys);
      box(st.rewD, x0, right - x0, ye, yt);
      st.rewD.textContent = "R:R " + Number(rr || 0).toFixed(1);
    }
    if (tf === "15m" && L && L.ob_low != null) {
      const x1 = Math.max(0, ts.timeToCoordinate(Math.round(L.ob_t / 1000) + LV.tz) ?? 0), y1 = Y(L.ob_high), y2 = Y(L.ob_low);
      if (y1 != null && y2 != null) { box(st.obD, x1, right - x1, y1, y2); st.obD.style.display = "block"; }
    } else st.obD.style.display = "none";
  };
  chart.timeScale().subscribeVisibleLogicalRangeChange(st.redraw);
  chart.subscribeCrosshairMove(st.redraw);
  const ro = new ResizeObserver(() => { if (el.clientWidth) { chart.applyOptions({ width: el.clientWidth }); st.redraw(); } });
  ro.observe(el);
  const n = bars.length;
  chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - (tf === "4h" ? 90 : 110)), to: n + 14 });
  setTimeout(st.redraw, 60); setTimeout(st.redraw, 300);
  st.update = b => { if (st.dead) return; const x = lvBar(b); if (x.time >= st.last) { const nw = x.time > st.last; series.update(x); st.last = x.time; if (nw) st.redraw(); } };
  st.setLtf = L => {
    if (st.dead) return;
    st.ltf = L;
    st.lines.forEach(x => series.removePriceLine(x)); st.lines = [];
    series.setMarkers([]);
    if (!L) { st.redraw(); return; }
    if (L.trigger != null && L.status === "wait_choch") st.lines.push(pl(L.trigger, "#ff9800", "злам 15m", 0, 1));
    if (L.entry != null) { st.lines.push(pl(L.entry, "#2962ff", "Вхід 15m", 0, 2)); st.lines.push(pl(L.stop, "#f23645", "Стоп 15m", 0, 2)); }
    const m = [];
    if (L.ext_t) m.push({ time: Math.round(L.ext_t / 1000) + LV.tz, position: r.dir === "Long" ? "belowBar" : "aboveBar", color: "#f23645", shape: "circle", text: "екстремум" });
    if (L.choch_t) m.push({ time: Math.round(L.choch_t / 1000) + LV.tz, position: r.dir === "Long" ? "aboveBar" : "belowBar", color: "#2962ff", shape: r.dir === "Long" ? "arrowUp" : "arrowDown", text: "злам" });
    if (L.fill_t) m.push({ time: Math.round(L.fill_t / 1000) + LV.tz, position: r.dir === "Long" ? "belowBar" : "aboveBar", color: "#089981", shape: "square", text: "вхід" });
    m.sort((a, b) => a.time - b.time);
    try { series.setMarkers(m); } catch (e) { /* маркер поза даними */ }
    st.redraw();
  };
  st.destroy = () => { st.dead = true; ro.disconnect(); chart.remove(); };
  return st;
}

async function liveMount(i, r) {
  const key = lvKey(r), m = { key, symbol: r.symbol, r, charts: {} };
  LV.mounts.set(i, m);
  const ids = { "4h": "ch" + i, "1h": "ch1h" + i, "15m": "ch15" + i };
  await Promise.all(Object.entries(ids).map(async ([tf, id]) => {
    const el = document.getElementById(id);
    if (!el) return;
    let bars = null;
    try {
      const res = await api(`/api/klines?symbol=${encodeURIComponent(r.symbol)}&tf=${tf}&limit=${LV_LIMIT[tf]}` + mkq("&"));
      if (res.ok) bars = res.bars;
    } catch (e) { /* нижче — запасний варіант */ }
    if (!bars && tf === "4h" && r.chart && r.chart.length > 2) bars = r.chart.map(x => [x[0], x[1], x[2], x[3], x[4], x[5] || 0, true]);
    if (!LV.mounts.has(i) || LV.mounts.get(i) !== m) return;   // картку вже перемалювали
    if (!bars || !bars.length) { el.innerHTML = '<div class="nochart">Немає свічок (Binance недоступний?)</div>'; return; }
    m.charts[tf] = lvChart(el, r, tf, bars);
    if (tf === "15m") m.charts[tf].setLtf(LV.ltf[key] || null);
  }));
  lvRenderLtf(i);
  lvConnect();
  return { destroy: () => { Object.values(m.charts).forEach(c => { try { c.destroy(); } catch (e) { /* */ } }); if (LV.mounts.get(i) === m) LV.mounts.delete(i); lvConnect(); } };
}
window.liveMount = liveMount;

// ---------------------------------------------------------------- наживо: WebSocket Binance, або опитування
function lvConnect() {
  clearTimeout(LV.reconnect);
  LV.reconnect = setTimeout(() => {
    const syms = [...new Set([...LV.mounts.values()].map(m => m.symbol.toLowerCase()))].sort();
    const key = (S.mk || "c") + ":" + syms.join(",");
    if (key === LV.wsKey && (LV.ws || LV.poll)) return;
    LV.wsKey = key;
    if (LV.ws) { LV.ws.onclose = null; try { LV.ws.close(); } catch (e) { /* */ } LV.ws = null; }
    clearInterval(LV.poll); LV.poll = null;
    if (!syms.length) return;
    if (window.SMC_NO_WS || S.mk) { lvStartPoll(); return; }   // форекс: свічки з MT5 через додаток
    const streams = syms.flatMap(s => ["4h", "1h", "15m"].map(tf => `${s}@kline_${tf}`)).join("/");
    let opened = false;
    try { LV.ws = new WebSocket(`wss://fstream.binance.com/stream?streams=${streams}`); } catch (e) { lvStartPoll(); return; }
    LV.ws.onopen = () => { opened = true; LV.wsOk = true; lvBadge(); };
    LV.ws.onmessage = ev => {
      let d; try { d = JSON.parse(ev.data).data; } catch (e) { return; }
      if (!d || !d.k) return;
      const k = d.k, sym = d.s;
      LV.mounts.forEach(m => { if (m.symbol === sym && m.charts[k.i]) m.charts[k.i].update([k.t, +k.o, +k.h, +k.l, +k.c]); });
    };
    LV.ws.onclose = () => { LV.ws = null; LV.wsOk = false; lvBadge(); if (!opened) lvStartPoll(); else lvConnect(); };
    LV.ws.onerror = () => { /* onclose вирішить */ };
  }, 300);
}

function lvStartPoll() {
  clearInterval(LV.poll);
  LV.poll = setInterval(async () => {
    if (!$("#tab-screener").classList.contains("active")) return;
    for (const m of LV.mounts.values()) {
      for (const [tf, c] of Object.entries(m.charts)) {
        try {
          const res = await api(`/api/klines?symbol=${encodeURIComponent(m.symbol)}&tf=${tf}&limit=10` + mkq("&"));
          if (res.ok) res.bars.slice(-3).forEach(b => c.update(b));
        } catch (e) { /* наступного разу */ }
      }
    }
  }, 15000);
  lvBadge();
}

function lvBadge() {
  const b = $("#liveBadge");
  if (!b) return;
  b.textContent = LV.wsOk ? "● наживо" : LV.poll ? "● оновлення кожні 15 с" : "";
  b.className = "muted small " + (LV.wsOk ? "live-on" : "");
}

// ---------------------------------------------------------------- уточнення 15m (стан рахує додаток раз на хвилину)
const LV_CTX = { 1: "вгору", "-1": "вниз", 0: "немає" };
function lvRenderLtf(i) {
  const m = LV.mounts.get(i), el = document.getElementById("lt" + i);
  if (!m || !el) return;
  const L = LV.ltf[m.key];
  const lb = document.getElementById("lb1h" + i);
  if (lb) lb.textContent = "1h" + (L && L.ctx_1h != null ? ` · структура ${LV_CTX[L.ctx_1h]}` : "");
  if (!L) {
    el.innerHTML = `<span class="muted">${LV.ltfLoaded ? (LV.ltfEnabled === false ? "15m: уточнення вимкнено в «Налаштуваннях»"
      : "15m: уточнення ще не пораховано (рахується раз на хвилину для свіжого скану з додатка)") : "15m: рахую уточнення…"}</span>`;
    return;
  }
  const cls = { choch: "ltf-go", filled: "ltf-go", broken: "ltf-bad", missed: "ltf-bad", error: "ltf-bad", rr_low: "ltf-bad" }[L.status] || "";
  el.innerHTML = `<b class="${cls}">15m: ${esc(L.status_txt || L.status)}</b>` + (L.why ? ` — ${esc(L.why)}` : "")
    + (L.entry != null && L.status !== "rr_low" ? ` · <b>лімітка ${fmt(L.entry)} · стоп ${fmt(L.stop)} · R:R ${fix(L.rr, 1)}</b>` : "");
  if (m.charts["15m"]) m.charts["15m"].setLtf(L);
}

async function lvLoadLtf() {
  clearTimeout(LV.ltfTimer);
  if (LV.mounts.size && $("#tab-screener").classList.contains("active")) {
    try {
      const r = await api("/api/ltf" + mkq("?"));
      LV.ltf = r.state || {}; LV.ltfLoaded = true; LV.ltfEnabled = r.enabled;
      LV.mounts.forEach((m, i) => lvRenderLtf(i));
    } catch (e) { /* наступного разу */ }
  }
  LV.ltfTimer = setTimeout(lvLoadLtf, 30000);
}
setTimeout(lvLoadLtf, 1500);
