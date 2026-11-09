/* SMC App — інтерфейс (етап 1: Скринер + Налаштування) */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const POLL_MS = 5000;

const S = {
  running: false, status: null, settings: null,
  res: null, mtime: null, rows: [], market: "futures",
  filter: "all", dir: "all", q: "",
  charts: new Map(), observer: null, busy: false,
  mk: "",   // яку частину скринера показано: "" — крипто (Binance), "fx" — форекс (MT5)
};
try { S.mk = localStorage.getItem("smc_mk") === "fx" ? "fx" : ""; } catch (e) { /* */ }
function mkq(sep) { return S.mk ? `${sep}market=fx` : ""; }

// ---------------------------------------------------------------- утиліти
function esc(x) {
  return String(x == null ? "" : x).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmt(x) {
  if (x === null || x === undefined || x === "") return "-";
  const n = Number(x);
  if (!isFinite(n)) return String(x);
  return String(parseFloat(n.toPrecision(6)));
}
function fix(x, d) { return (x === null || x === undefined || !isFinite(Number(x))) ? "-" : Number(x).toFixed(d); }
function hhmm(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleTimeString("uk-UA", { hour: "2-digit", minute: "2-digit" });
}
function ago(sec) {
  if (sec == null) return "";
  const m = Math.round(sec / 60);
  if (m < 1) return "щойно";
  if (m < 60) return `${m} хв тому`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} год ${m % 60} хв тому`;
  return `${Math.floor(h / 24)} дн тому`;
}
async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-SMC-App": "1" }, body: JSON.stringify(body),
  };
  const r = await fetch(path, opts);
  let data = {};
  try { data = await r.json(); } catch (e) { /* порожня відповідь */ }
  if (!r.ok && !data.message && !data.errors) data.message = `Помилка ${r.status}`;
  data._ok = r.ok;
  return data;
}
function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }

function showMsg(text, err) {
  const m = $("#msg");
  m.textContent = text;
  m.className = "msg" + (err ? " err" : "");
  clearTimeout(showMsg.t);
  if (!err) showMsg.t = setTimeout(() => m.classList.add("hidden"), 6000);
}

const tvTicker = s => S.market === "forex" ? "FX%3A" + s : "BINANCE%3A" + s + (S.market === "spot" ? "" : ".P");
const tvLink = s => `https://www.tradingview.com/chart/?symbol=${tvTicker(s)}&interval=240`;
const bnLink = s => S.market === "forex" ? `https://www.tradingview.com/chart/?symbol=FX%3A${s}&interval=60`
  : S.market === "spot"
  ? `https://www.binance.com/en/trade/${s.replace(/USDT$/, "")}_USDT?type=spot`
  : `https://www.binance.com/en/futures/${s}`;

function rankOf(r) {
  if (r.rank !== undefined && r.rank !== null) return r.rank;
  const st = r.status || "";
  if (st.startsWith("у зоні (1")) return 0;
  if (st.startsWith("підхід")) return 1;
  return 2;
}
function actionText(r) {
  if (r.rank === 0) return "ЦІНА В ЗОНІ: вирішуй, чи ставити лімітку (перевір 1h)";
  if (r.rank === 1) return `чекаю підходу до зони (${fix(r.dist, 1)}%)`;
  return "повторний дотик зони";
}

// ---------------------------------------------------------------- вкладки
function openTab(name) {
  $$(".tab").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  $$(".panel").forEach(p => p.classList.toggle("active", p.id === "tab-" + name));
  store("smc_tab", name);
  if (name === "settings") loadSettings();
  if (name === "journal" && window.loadJournal) window.loadJournal();
  if (name === "summary" && window.loadSummary) window.loadSummary();
  if (name === "strategy" && window.loadStrategy) window.loadStrategy();
  if (name === "history" && window.loadHistory) window.loadHistory();
  if (name === "backtest" && window.loadBacktest) window.loadBacktest();
  if (name === "spot" && window.loadSpot) window.loadSpot();
  if (name === "screener") S.charts.forEach(c => c.redraw && c.redraw());
}
$$(".tab").forEach(b => b.addEventListener("click", () => openTab(b.dataset.tab)));

// ---------------------------------------------------------------- старт / стоп
$("#btnRun").addEventListener("click", async () => {
  if (S.busy) return;
  if (S.running && !confirm(S.mk ? "Зупинити форекс-скринер? Алерти по валютних парах перестануть приходити." : "Зупинити скринер? Алерти перестануть приходити.")) return;
  S.busy = true;
  const btn = $("#btnRun");
  btn.disabled = true;
  btn.textContent = S.running ? "Зупиняю…" : "Запускаю…";
  $("#pill").classList.add("busy");
  const r = await api((S.running ? "/api/stop" : "/api/start") + mkq("?"), {});
  showMsg(r.message || (r._ok ? "Готово" : "Помилка"), !r._ok);
  S.busy = false;
  $("#pill").classList.remove("busy");
  await pollStatus();
  pollResults(true);
});

function renderRun() {
  const btn = $("#btnRun"), pill = $("#pill");
  if (S.busy) return;
  btn.disabled = false;
  btn.textContent = S.running ? "Стоп" : "Старт";
  btn.className = "btn " + (S.running ? "danger" : "primary");
  pill.classList.toggle("on", S.running);
  pill.classList.toggle("off", !S.running);
  $("#pillText").textContent = (S.mk ? "Форекс: " : "") + (S.running ? (S.mk ? "працює" : "Працює") : (S.mk ? "зупинено" : "Зупинено"));
}

function renderStatusLine() {
  const st = S.status;
  if (!st) return;
  const watch = S.settings ? Number(S.settings.watch) : null;
  const parts = [];
  if (S.mk && st.fx) {
    const fx = st.fx;
    if (!fx.ok) parts.push(`<span style="color:var(--red)">MT5: ${esc(fx.error)}</span>`);
    else parts.push(`MT5 ${esc(fx.server || "")}: дані свіжі (${fx.age_s} с)` + (fx.missing && fx.missing.length ? ` · немає в брокера: ${esc(fx.missing.join(", "))}` : ""));
    if (fx.ok && fx.market_open === false) parts.push("форекс-ринок закритий (вихідні) — нових свічок немає");
  }
  if (st.running) {
    parts.push(`<b>${S.mk ? "Форекс-скринер" : "Скринер"} працює</b> з ${esc(hhmm(st.started_at))}`);
    if (st.last_scan) {
      const ls = st.last_scan;
      let next = "";
      if (watch) {
        const [h, m] = ls.time.split(":").map(Number);
        const d = new Date(); d.setHours(h, m + Math.round(watch), 0, 0);
        next = ` · наступний ≈ ${d.toLocaleTimeString("uk-UA", { hour: "2-digit", minute: "2-digit" })}`;
      }
      parts.push(`останній скан <b>${esc(ls.time)}</b>: у списку ${ls.rows}, нових ${ls.new}` +
        (ls.errors ? `, помилок ${ls.errors}` : "") + next);
    } else {
      parts.push(S.mk ? "перший скан триває (≈ 1 хв)" : "перший скан триває (100 пар ≈ 1–2 хв)");
    }
    if (st.last_error) parts.push(`<span style="color:var(--red)">помилка скану о ${esc(st.last_error.time)}: ${esc(st.last_error.text)}</span>`);
    if (st.stale) {
      const mins = st.last_scan_age_s != null ? Math.round(st.last_scan_age_s / 60) : null;
      parts.push(`<span class="stale">⚠ ${mins != null ? `останній скан ${mins} хв тому` : "з моменту старту ще не було скану"} — `
        + `скани пропущено (немає мережі?). Після пробудження Mac скан іде сам; якщо попередження не зникає — «Стоп» → «Старт».</span>`);
    }
  } else {
    parts.push(S.mk ? "<b>Форекс-скринер зупинено.</b> Натисни «Старт», щоб сканувати валютні пари (MT5 має бути відкритий з роботом SMC_Bridge)."
      : "<b>Скринер зупинено.</b> Натисни «Старт», щоб сканувати й отримувати алерти.");
    if (st.env && !st.env.screener_found) parts.push(`<span style="color:var(--red)">не знайдено ${esc(st.env.screener_path)} — вкажи шлях у «Налаштуваннях»</span>`);
  }
  $("#statusLine").innerHTML = parts.join(" · ");
}

async function pollStatus() {
  try {
    const st = await api("/api/status" + mkq("?"));
    const was = S.running;
    S.status = st;
    S.running = !!st.running;
    if (st.last_exit && !st.running && !pollStatus.exitShown) {
      pollStatus.exitShown = true;
      showMsg(`Скринер завершився сам о ${st.last_exit.at} (код ${st.last_exit.code}). Причина — у лозі внизу.`, true);
      $("#logBox").open = true;
      pollLog();
    }
    if (st.running) pollStatus.exitShown = false;
    renderMarkets(st);
    renderRun();
    renderStatusLine();
    if (was !== S.running) renderStale();
  } catch (e) {
    $("#statusLine").innerHTML = '<span style="color:var(--red)">Немає зв\'язку з додатком. Він запущений? (python3 app.py)</span>';
    $("#btnRun").disabled = true;
  }
}

// ---------------------------------------------------------------- крипто / форекс
function renderMarkets(st) {
  const box = $("#mkSwitch");
  const on = !!st.fx_enabled;
  box.hidden = !on && !S.mk;
  if (!on && S.mk) { setMarket(""); return; }   // форекс вимкнули в «Налаштуваннях»
  $$("#mkSwitch .mk").forEach(b => b.classList.toggle("on", b.dataset.mk === S.mk));
  const m = st.markets || {};
  $("#mkDotC").className = "mk-dot" + (m.crypto ? " run" : "");
  $("#mkDotF").className = "mk-dot" + (m.fx ? " run" : "");
}
function setMarket(mk) {
  if (mk === S.mk) return;
  S.mk = mk;
  try { localStorage.setItem("smc_mk", mk); } catch (e) { /* */ }
  S.mtime = null; S.rows = []; S.res = null; S.status = null;
  destroyCharts();
  $("#tbody").innerHTML = ""; $("#charts").innerHTML = "";
  $("#statusLine").textContent = "Завантаження…";
  pollStatus(); pollResults(true); pollLog();
}
$$("#mkSwitch .mk").forEach(b => b.addEventListener("click", () => setMarket(b.dataset.mk)));

// ---------------------------------------------------------------- результати
async function pollResults(force) {
  try {
    const q = (!force && S.mtime) ? `?since=${S.mtime}` : "";
    const mk = S.mk;
    const r = await api("/api/results" + q + mkq(q ? "&" : "?"));
    if (mk !== S.mk) return;  // поки чекали відповідь, перемкнули крипто/форекс
    if (r.changed === false) { renderStale(); return; }
    S.res = r;
    S.mtime = r.mtime;
    const data = r.data || {};
    S.market = data.market || "futures";
    S.rows = (data.candidates || []).map((x, i) => Object.assign({}, x, { idx: i, rank: rankOf(x) }));
    renderAll();
  } catch (e) { /* статус покаже проблему зі зв'язком */ }
}

function visibleRows() {
  const q = S.q.trim().toUpperCase();
  return S.rows.filter(r =>
    (S.filter === "all" || (S.filter === "zone" ? r.rank === 0 : r.rank === 1)) &&
    (S.dir === "all" || r.dir === S.dir) &&
    (!q || r.symbol.includes(q)));
}

function renderAll() {
  const rows = S.rows;
  $("#cAll").textContent = rows.length;
  $("#cZone").textContent = rows.filter(r => r.rank === 0).length;
  $("#cApproach").textContent = rows.filter(r => r.rank === 1).length;
  const inZone = rows.filter(r => r.rank === 0);
  const za = $("#zoneAlert");
  if (inZone.length) {
    za.innerHTML = "🟢 Ціна вже в зоні: " + inZone.map(r =>
      `<a href="#c${r.idx}" data-jump="${r.idx}">${esc(r.symbol)} ${esc(r.dir)}</a> (вхід ${fmt(r.entry)}, стоп ${fmt(r.stop)}, R:R ${fix(r.rr, 1)})`).join("; ") +
      ". Час вирішувати, чи ставити лімітку: перевір 1h.";
    za.classList.remove("hidden");
  } else za.classList.add("hidden");
  document.title = (inZone.length ? "🟢 В ЗОНІ: " + inZone.map(r => r.symbol.replace(/USDT$/, "")).join(", ") + " | " : "") + "SMC App";
  renderStale();
  renderTable();
  renderCharts();
}

function renderStale() {
  const r = S.res, box = $("#staleNote"), stamp = $("#dataStamp");
  if (!r) return;
  if (r.source === "none") {
    stamp.textContent = "";
    box.innerHTML = `Ще немає результатів у <code>${esc(r.dir)}</code>. Вони з'являться після першого скану.`;
    box.classList.remove("hidden");
    return;
  }
  if (r.source === "error") {
    box.textContent = r.error;
    box.classList.remove("hidden");
    return;
  }
  const gen = (r.data && r.data.generated) || "";
  stamp.textContent = `дані: ${gen}${r.mtime ? " · " + ago(Date.now() / 1000 - r.mtime) : ""}`;
  const notes = [];
  if (r.source === "legacy") notes.push("Показано <code>latest.json</code> зі скану без додатка: графіків у ньому немає. Вони з'являться після першого скану, запущеного кнопкою «Старт».");
  if (!S.running && S.rows.length) notes.push("Скринер зупинено — це останні збережені результати, ціни в них не оновлюються.");
  box.innerHTML = notes.join("<br>");
  box.classList.toggle("hidden", !notes.length);
}

function renderTable() {
  const rows = visibleRows();
  const tb = $("#tbody");
  tb.innerHTML = rows.map(r => {
    const fib = (r.fib_ok ? "так " : "ні ") + (r.fib != null ? `(${fix(r.fib, 2)})` : "");
    const flags = (r.flags || []).join("; ");
    return `<tr class="s${r.rank}" data-i="${r.idx}">
      <td class="num">${r.idx + 1}</td>
      <td><a class="sym" href="${tvLink(r.symbol)}" target="_blank" rel="noopener" title="Відкрити в TradingView">${esc(r.symbol)}</a></td>
      <td class="dir-${esc(r.dir)}">${esc(r.dir)}${r.zone_type === "брейкер" ? ' <span class="tag amber" title="брейкер-блок: пробитий ордерблок протилежного напрямку">брейкер</span>' : ""}</td>
      <td>${esc(r.status)}</td>
      <td class="act${r.rank}">${esc(actionText(r))}</td>
      <td class="num"><b>${esc(r.score)}</b></td>
      <td class="num">${fmt(r.price)}</td>
      <td class="num">${fmt(r.zone_low)}–${fmt(r.zone_high)}</td>
      <td class="num">${fmt(r.entry)}</td>
      <td class="num">${fmt(r.stop)}</td>
      <td class="num">${fmt(r.target)}</td>
      <td class="num"><b>${fix(r.rr, 1)}</b></td>
      <td class="num">${fix(r.dist, 2)}</td>
      <td>${esc(fib)}</td>
      <td>${esc(r.sweep || "-")}</td>
      <td>${esc(r.volume || "-")}</td>
      <td>${esc(r.bos_time || "")}</td>
      <td class="wrap warn">${esc(flags)}</td>
      <td><a href="${bnLink(r.symbol)}" target="_blank" rel="noopener">${S.market === "forex" ? "1h" : "Binance"}</a></td>
    </tr>`;
  }).join("");
  const empty = $("#empty");
  if (!rows.length) {
    empty.textContent = S.rows.length ? "Під фільтр нічого не підходить." :
      (S.res && S.res.source !== "none" ? "Кандидатів за поточних параметрів немає." : "Немає даних.");
    empty.classList.remove("hidden");
  } else empty.classList.add("hidden");
}

$("#tbody").addEventListener("click", e => {
  if (e.target.closest("a")) return;
  const tr = e.target.closest("tr[data-i]");
  if (tr) jumpTo(+tr.dataset.i);
});
$("#zoneAlert").addEventListener("click", e => {
  const a = e.target.closest("[data-jump]");
  if (a) { e.preventDefault(); jumpTo(+a.dataset.jump); }
});
function jumpTo(i) {
  const card = document.getElementById("c" + i);
  if (!card) return;
  card.scrollIntoView({ behavior: "smooth", block: "start" });
  card.classList.remove("flash"); void card.offsetWidth; card.classList.add("flash");
}

// фільтри
$("#filterChips").addEventListener("click", e => {
  const b = e.target.closest(".chip"); if (!b) return;
  S.filter = b.dataset.f;
  $$("#filterChips .chip").forEach(x => x.classList.toggle("on", x === b));
  renderTable(); renderCharts();
});
$("#dirChips").addEventListener("click", e => {
  const b = e.target.closest(".chip"); if (!b) return;
  S.dir = b.dataset.d;
  $$("#dirChips .chip").forEach(x => x.classList.toggle("on", x === b));
  renderTable(); renderCharts();
});
$("#search").addEventListener("input", e => { S.q = e.target.value; renderTable(); renderCharts(); });

// ---------------------------------------------------------------- графіки
function destroyCharts() {
  if (S.observer) S.observer.disconnect();
  S.charts.forEach(c => { try { c.destroy(); } catch (e) { /* вже знищено */ } });
  S.charts.clear();
}

// лайв-графіки 4h+1h+15m (v1.5): вимикаються в «Налаштуваннях» — тоді один графік 4h, як раніше
function liveOn() { return !(S.settings && S.settings.ltf_enabled === false); }

// згорнуті картки графіків (пам'ятаємо в браузері: монета|напрям|зона)
const FOLD = new Set((() => { try { return JSON.parse(localStorage.getItem("smc_fold") || "[]"); } catch (e) { return []; } })());
function foldKey(r) { return `${r.symbol}|${r.dir}|${r.zone_low}`; }
function foldSave() { try { localStorage.setItem("smc_fold", JSON.stringify([...FOLD].slice(-300))); } catch (e) { /* */ } }
function setFold(card, on) {
  card.classList.toggle("folded", on);
  const b = card.querySelector(".cfold");
  if (b) b.textContent = on ? "▸" : "▾";
  const k = card.dataset.fk;
  if (on) FOLD.add(k); else FOLD.delete(k);
}
document.addEventListener("click", e => {
  const all = e.target.closest("[data-foldall]");
  if (all) { const on = all.dataset.foldall === "1"; $$("#charts .card").forEach(c => setFold(c, on)); foldSave(); return; }
  const b = e.target.closest(".card .cfold");
  if (b) { const c = b.closest(".card"); setFold(c, !c.classList.contains("folded")); foldSave(); }
});

function renderCharts() {
  destroyCharts();
  const rows = visibleRows();
  const box = $("#charts");
  box.classList.toggle("live", liveOn());
  $("#chartsCount").textContent = rows.length ? `(${rows.length})` : "";
  box.innerHTML = rows.map(r => {
    const extra = (r.sweep ? " | " + esc(r.sweep) : "") + (r.volume && r.volume !== "-" ? " | обсяг: " + esc(r.volume) : "");
    const warn = (r.flags || []).length ? ` | <b class="warnings">${esc(r.flags.join("; "))}</b>` : "";
    const fk = foldKey(r);
    return `<div class="card s${r.rank}${FOLD.has(fk) ? " folded" : ""}" id="c${r.idx}" data-fk="${esc(fk)}">
      <div class="hd"><button type="button" class="cfold" title="Згорнути / розгорнути графіки">${FOLD.has(fk) ? "▸" : "▾"}</button><b>${r.idx + 1}. ${esc(r.symbol)}</b> <span class="dir-${esc(r.dir)}">${esc(r.dir)}</span>${r.zone_type === "брейкер" ? ' <span class="tag amber">брейкер</span>' : ""}
        | ${esc(r.status)} | вхід ${fmt(r.entry)} · стоп ${fmt(r.stop)} · ціль ${fmt(r.target)} | <b>R:R ${fix(r.rr, 1)}</b>${extra}${warn}
        | <a href="${tvLink(r.symbol)}" target="_blank" rel="noopener">TradingView</a></div>
      ${liveOn() ? `<div class="tf3">
        <div class="tfbox"><div class="tflb">4h · сигнал</div><div class="chart ch3" id="ch${r.idx}"></div></div>
        <div class="tfbox"><div class="tflb" id="lb1h${r.idx}">1h</div><div class="chart ch3" id="ch1h${r.idx}"></div></div>
        <div class="tfbox"><div class="tflb">15m · уточнення входу</div><div class="chart ch3" id="ch15${r.idx}"></div></div>
      </div>
      <div class="ltfline" id="lt${r.idx}"><span class="muted">15m: рахую уточнення…</span></div>`
      : `<div class="chart" id="ch${r.idx}">${r.chart && r.chart.length > 2 ? "" : '<div class="nochart">Графіка немає: дані свічок з\'являться після скану, запущеного з додатка.</div>'}</div>`}
    </div>`;
  }).join("");
  if (typeof LightweightCharts === "undefined") {
    showMsg("Не завантажилась бібліотека графіків (static/vendor).", true);
    return;
  }
  // графіки будуються лише коли картка близько до екрана — сторінка не гальмує навіть зі 100 кандидатами
  S.observer = new IntersectionObserver(entries => {
    entries.forEach(en => {
      if (!en.isIntersecting) return;
      const i = +en.target.id.slice(1);
      S.observer.unobserve(en.target);
      const r = S.rows[i];
      if (!r || S.charts.has(i)) return;
      if (liveOn() && typeof liveMount === "function") {
        // лайв: 4h + 1h + 15m (свічки з Binance через додаток, далі — наживо)
        const h = { destroy: () => {} };
        S.charts.set(i, h);
        liveMount(i, r).then(x => { if (S.charts.get(i) === h) S.charts.set(i, x); else x.destroy(); }).catch(e => console.error(e));
      } else if (r.chart && r.chart.length > 2) {
        try { S.charts.set(i, buildChart(document.getElementById("ch" + i), r)); }
        catch (e) { console.error(e); }
      }
    });
  }, { rootMargin: "600px 0px" });
  $$(".card", box).forEach(c => S.observer.observe(c));
}

const FUT = 24;
function buildChart(el, r) {
  const LWC = LightweightCharts;
  const tz = -new Date().getTimezoneOffset() * 60;  // показуємо час у часовому поясі цього комп'ютера
  const H = el.clientHeight || 430;
  const chart = LWC.createChart(el, {
    width: el.clientWidth, height: H,
    layout: { background: { color: "#ffffff" }, textColor: "#131722", fontFamily: "-apple-system, Arial" },
    grid: { vertLines: { color: "#f0f3fa" }, horzLines: { color: "#f0f3fa" } },
    rightPriceScale: { borderColor: "#d1d4dc", scaleMargins: { top: 0.08, bottom: 0.22 } },
    timeScale: { borderColor: "#d1d4dc", timeVisible: true, secondsVisible: false },
    crosshair: { mode: 0 },
  });
  const d = {
    zl: r.zone_low, zh: r.zone_high, entry: r.entry, stop: r.stop, target: r.target, px: r.price, rr: r.rr,
    tf: Math.max(1, Math.round((r.tf_ms || 14400000) / 1000)), zt: Math.round((r.zone_t || 0) / 1000) + tz,
    inzone: r.rank !== 1,
  };
  const lo = Math.min(d.stop, d.target, d.zl, d.entry), hi = Math.max(d.stop, d.target, d.zh, d.entry);
  const series = chart.addCandlestickSeries({
    upColor: "#ffffff", downColor: "#000000", borderUpColor: "#000000", borderDownColor: "#000000",
    wickUpColor: "#000000", wickDownColor: "#000000", priceLineVisible: false, lastValueVisible: false,
    autoscaleInfoProvider: orig => {
      const o = orig();
      if (!o) return { priceRange: { minValue: lo, maxValue: hi } };
      return { priceRange: { minValue: Math.min(o.priceRange.minValue, lo), maxValue: Math.max(o.priceRange.maxValue, hi) }, margins: o.margins };
    },
  });
  const bars = r.chart.map(x => ({ time: Math.round(x[0] / 1000) + tz, open: x[1], high: x[2], low: x[3], close: x[4] }));
  const last = bars[bars.length - 1].time;
  const data = bars.slice();
  for (let k = 1; k <= FUT; k++) data.push({ time: last + k * d.tf });
  series.setData(data);
  if (r.chart[0].length > 5) {
    const vol = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "", lastValueVisible: false, priceLineVisible: false });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(r.chart.map(x => ({ time: Math.round(x[0] / 1000) + tz, value: x[5], color: x[4] >= x[1] ? "rgba(38,166,154,0.55)" : "rgba(239,83,80,0.55)" })));
  }
  const pl = (price, color, title, style) => series.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title });
  pl(d.px, "#2962ff", "Ціна", 0);
  pl(d.entry, "#434651", "Вхід", 2);
  pl(d.stop, "#f23645", "Стоп", 2);
  pl(d.target, "#089981", "Ціль", 2);
  // сценарій-ілюстрація (не прогноз): від поточної ціни до входу і до цілі
  const path = chart.addLineSeries({ color: "#787b86", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
  path.setData(d.inzone ? [{ time: last, value: d.px }, { time: last + 12 * d.tf, value: d.target }]
    : [{ time: last, value: d.px }, { time: last + 4 * d.tf, value: d.entry }, { time: last + 16 * d.tf, value: d.target }]);

  const mk = css => { const e = document.createElement("div"); e.style.cssText = "position:absolute;pointer-events:none;z-index:3;" + css; el.appendChild(e); return e; };
  const zoneD = mk("background:rgba(150,150,150,0.35);");
  const midD = mk("border-top:1px dashed #9c27b0;height:0;");
  const riskD = mk("background:rgba(120,123,134,0.45);");
  const rewD = mk("background:rgba(77,182,205,0.45);color:#0b3c49;font:12px -apple-system,Arial;padding:2px 6px;");
  rewD.textContent = "R:R " + Number(d.rr).toFixed(1);
  function redraw() {
    const ts = chart.timeScale();
    const X = (t, f) => { const v = ts.timeToCoordinate(t); return v === null ? f : v; };
    const x0 = X(last, 0), x1 = X(last + FUT * d.tf, el.clientWidth), xz = Math.max(0, X(d.zt, 0));
    const Y = p => series.priceToCoordinate(p);
    const yzh = Y(d.zh), yzl = Y(d.zl), ye = Y(d.entry), ys = Y(d.stop), yt = Y(d.target), ym = Y((d.zh + d.zl) / 2);
    if ([yzh, yzl, ye, ys, yt, ym].some(v => v === null)) return;
    const box = (e, x, w, ya, yb) => { e.style.left = x + "px"; e.style.width = Math.max(0, w) + "px"; e.style.top = Math.min(ya, yb) + "px"; e.style.height = Math.abs(ya - yb) + "px"; };
    box(zoneD, xz, x1 - xz, yzh, yzl);
    midD.style.left = xz + "px"; midD.style.width = Math.max(0, x1 - xz) + "px"; midD.style.top = ym + "px";
    box(riskD, x0, x1 - x0, ye, ys);
    box(rewD, x0, x1 - x0, ye, yt);
  }
  chart.timeScale().subscribeVisibleLogicalRangeChange(redraw);
  chart.subscribeCrosshairMove(redraw);
  const ro = new ResizeObserver(() => { if (el.clientWidth) { chart.applyOptions({ width: el.clientWidth }); redraw(); } });
  ro.observe(el);
  chart.timeScale().setVisibleLogicalRange({ from: data.length - FUT - 70, to: data.length - 1 });
  setTimeout(redraw, 50); setTimeout(redraw, 300);
  return { redraw, destroy: () => { ro.disconnect(); chart.remove(); } };
}

// ---------------------------------------------------------------- лог
async function pollLog() {
  if (!$("#logBox").open) return;
  const r = await api("/api/log?n=400" + mkq("&"));
  const pre = $("#log");
  const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
  pre.textContent = (r.lines || []).join("\n") || "Лог порожній: скринер ще не запускався з додатка.";
  $("#logPath").textContent = r.path ? "· " + r.path : "";
  if (atBottom) pre.scrollTop = pre.scrollHeight;
}
$("#logBox").addEventListener("toggle", () => { if ($("#logBox").open) { pollLog().then(() => { const p = $("#log"); p.scrollTop = p.scrollHeight; }); } });

// ---------------------------------------------------------------- налаштування
async function loadSettings() {
  const r = await api("/api/settings");
  S.settings = r.settings;
  fillForm(r.settings);
  $("#cmdPreview").textContent = r.command || "";
  renderEnv(r.env);
  loadGit();
  loadFx();
}
async function loadFx() {
  const box = $("#fxState");
  if (!box) return;
  try {
    const r = await api("/api/fx");
    if (r.ok) {
      box.innerHTML = `<span class="ok">✓</span> MT5 підключено: <b>${esc(r.server || "сервер")}</b>, дані ${r.age_s} с тому, час сервера UTC${r.offset_h >= 0 ? "+" : ""}${r.offset_h}`
        + (r.missing && r.missing.length ? ` · <span class="no">немає в брокера: ${esc(r.missing.join(", "))}</span>` : ` · усі ${r.pairs.length} пар є`)
        + (r.market_open === false ? " · ринок зараз закритий (вихідні)" : "")
        + `<br><span class="muted small">${esc(r.dir)}</span>`;
    } else {
      box.innerHTML = `<span class="no">✗</span> ${esc(r.error || "MT5 не знайдено")}`
        + (r.enabled ? "" : ' <span class="muted">— інструкція нижче</span>');
    }
  } catch (e) { box.textContent = ""; }
}
function fillForm(s) {
  const f = $("#setForm");
  Object.entries(s).forEach(([k, v]) => {
    const el = f.elements[k];
    if (!el) return;
    if (el.tagName === "SELECT" && v !== "" && v != null && ![...el.options].some(o => Number(o.value) === Number(v) || o.value === String(v)))
      el.add(new Option(String(v), String(v)));  // значення не зі списку (напр. задане вручну) — показати як є
    if (el.type === "checkbox") el.checked = !!v;
    else if (el.tagName === "SELECT") el.value = ([...el.options].find(o => Number(o.value) === Number(v)) || {}).value ?? String(v);
    else el.value = v;
  });
}
function formValues() {
  const f = $("#setForm"), out = {};
  Array.from(f.elements).forEach(el => {
    if (!el.name) return;
    out[el.name] = el.type === "checkbox" ? el.checked : el.value;
  });
  return out;
}
$("#setForm").addEventListener("submit", async e => {
  e.preventDefault();
  const m = $("#setMsg"), btn = $("#btnSaveSet");
  btn.disabled = true; m.style.color = ""; m.textContent = "Зберігаю…";
  let r;
  try { r = await api("/api/settings", { settings: formValues() }); }
  finally { btn.disabled = false; }
  if (r.ok) {
    const wasLive = liveOn();
    S.settings = r.settings;
    if (wasLive !== liveOn()) renderCharts();
    m.textContent = r.message; m.style.color = "var(--green)";
    $("#cmdPreview").textContent = r.command || "";
    loadSettings();  // оновити й блоки стану (Telegram, середовище)
    pollResults(true); pollStatus();
  } else {
    m.textContent = (r.errors || [r.message]).join("; "); m.style.color = "var(--red)";
  }
});
$("#btnReset").addEventListener("click", async () => {
  if (!confirm("Повернути стандартні налаштування?")) return;
  await api("/api/settings/reset", {});
  loadSettings();
  $("#setMsg").textContent = "Стандартні налаштування відновлено.";
});
async function setupIcloud() {
  const r = await api("/api/journal/backups");
  const b = $("#btnIcloud");
  b.hidden = !r.icloud;
  b.onclick = () => { $("#setForm").elements.backup_extra_dir.value = r.icloud_suggest; };
}
function renderEnv(env) {
  if (!env) return;
  setupIcloud();
  renderTg(env);
  const li = (ok, text, hint) => `<li><span class="${ok ? "ok" : "no"}">${ok ? "✓" : "✗"}</span> ${text}${!ok && hint ? ` <span class="muted">— ${hint}</span>` : ""}</li>`;
  $("#envList").innerHTML = [
    li(env.screener_found, `скринер: <code>${esc(env.screener_path)}</code>`, "файл не знайдено"),
    li(env.requests, "пакет requests", "виконай: pip3 install requests"),
    li(env.caffeinate, "caffeinate (Mac не засинає)", "є лише на macOS"),
    env.sheet_sync
      ? li(env.sheet_url_set && env.sheet_token_set, "Google Таблиця: SMC_SHEET_URL і SMC_SHEET_TOKEN задані",
          "вкладка «Скринер» у таблиці не оновлюватиметься (див. README)")
      : `<li class="muted">Google Таблиця: вимкнено — журнал і результати лише в додатку</li>`,
    `<li class="muted">Python ${esc(env.python)} · ${esc(env.platform)} · додаток ${esc(env.app_version)} · результати: <code>${esc(env.results_dir)}</code></li>`,
  ].join("");
}
$("#btnSelftest").addEventListener("click", async () => {
  const b = $("#btnSelftest"), out = $("#selftestOut");
  b.disabled = true; b.textContent = "Перевіряю…";
  const r = await api("/api/selftest", {});
  b.disabled = false; b.textContent = "Перевірити скринер (selftest)";
  out.classList.remove("hidden");
  out.textContent = (r.ok ? `✓ Усі перевірки пройдено (${r.passed} OK)\n\n` : `✗ Є помилки: OK ${r.passed || 0}, FAIL ${r.failed || 0}\n\n`) + (r.output || "");
});

// ---------------------------------------------------------------- старт сторінки
(async function init() {
  const t = store("smc_tab");
  if (t && document.getElementById("tab-" + t)) openTab(t);
  try { S.settings = (await api("/api/settings")).settings; } catch (e) { /* покаже статус */ }
  await pollStatus();
  await pollResults(true);
  setInterval(pollStatus, POLL_MS);
  setInterval(() => pollResults(false), POLL_MS);
  setInterval(pollLog, POLL_MS);
  setInterval(renderStale, 60000);
})();

// ---------------------------------------------------------------- закрити додаток (сервер)
$("#btnShutdown").addEventListener("click", async () => {
  if (!confirm("Закрити додаток? Скринер, якщо працює, продовжить працювати.")) return;
  const r = await api("/api/shutdown", {});
  document.body.innerHTML = `<div class="closed"><h2>SMC App закрито</h2>
    <p>${r.screener_running ? "Скринер працює далі у фоні: алерти приходитимуть. " : ""}Щоб відкрити знову — «SMC App» у Dock / Launchpad
    (або <code>python3 app.py</code> у папці додатка). Це вікно можна закрити.</p></div>`;
});

// ---------------------------------------------------------------- GitHub
const gitTime = iso => iso ? iso.replace("T", " ").slice(0, 16) : "";
async function loadGit() {
  let g;
  try { g = await api("/api/git/status"); } catch (e) { return; }
  const el = $("#gitState");
  let h;
  if (!g.repo) h = `<span class="no">✗</span> Папку ще не підключено до git. У Терміналі: <code>cd ~/smc_app && git init -b main</code>, далі — як у першому пуші.`;
  else if (!g.remote) h = `<span class="no">✗</span> Немає адреси репозиторію. У Терміналі: <code>git remote add origin https://github.com/ЛОГІН/smc-app.git</code>`;
  else {
    const link = g.web ? `<a href="${esc(g.web)}" target="_blank" rel="noopener">${esc(g.web.replace("https://github.com/", ""))}</a>` : esc(g.remote);
    const bad = g.ok === false && g.error;
    h = `<span class="${bad ? "no" : "ok"}">${bad ? "✗" : "✓"}</span> ${link} · гілка ${esc(g.branch || "?")}`
      + (g.last_push ? ` · останній пуш ${esc(gitTime(g.last_push))}` : "")
      + (g.last_commit ? ` · коміт <code>${esc(g.last_commit.hash)}</code> ${esc(gitTime(g.last_commit.at))}` : "")
      + (g.ahead ? ` · <b>не запушено комітів: ${g.ahead}</b>` : "")
      + (g.dirty && g.dirty.length ? ` · змінених файлів: ${g.dirty.length}${g.enabled ? " (збережу за хвилину-дві)" : ""}` : "")
      + (bad ? `<br><span class="warnc">${esc(g.error)}</span>` : "")
      + (g.enabled ? "" : " — <b>автозбереження вимкнено</b>");
  }
  el.innerHTML = h;
  $("#gitPush").disabled = !(g.repo && g.remote);
}
$("#gitPush").addEventListener("click", async () => {
  const m = $("#gitMsg");
  m.textContent = "Пушу…"; m.style.color = "";
  const g = await api("/api/git/push", {});
  m.textContent = g.ok ? "Збережено в GitHub ✓" : "Не вдалося — див. вище";
  m.style.color = g.ok ? "var(--green)" : "var(--red)";
  loadGit();
});

// ---------------------------------------------------------------- Telegram
function renderTg(env) {
  const el = $("#tgState");
  let h;
  if (!env.tg_token_set) h = `<span class="no">✗</span> Немає токена бота. Створи бота в @BotFather і додай у Терміналі:
      <code>echo 'export SMC_TG_TOKEN="ТОКЕН"' &gt;&gt; ~/.zshrc</code>, потім «Закрити додаток» і знову SMC App.`;
  else if (!env.tg_chat) h = `<span class="warnc">!</span> Токен є. Відкрий свого бота в Telegram, натисни <b>Start</b>, потім — «Підключити Telegram».`;
  else h = `<span class="ok">✓</span> Підключено${env.tg_name ? ": " + esc(env.tg_name) : ""} (chat ${esc(env.tg_chat)})`
    + (env.tg_enabled ? "" : " — <b>надсилання вимкнено</b>");
  el.innerHTML = h;
  $("#tgConnect").disabled = !env.tg_token_set;
  $("#tgConnect").textContent = env.tg_chat ? "Підключити заново" : "Підключити Telegram";
  $("#tgTest").disabled = !(env.tg_token_set && env.tg_chat && env.tg_enabled);
  $("#tgOff").hidden = !env.tg_chat;
}
async function tgAction(path, okText) {
  const m = $("#tgMsg");
  m.textContent = "Зачекай…"; m.style.color = "";
  const r = await api(path, {});
  m.textContent = r.ok ? okText(r) : (r.error || r.message || "Помилка");
  m.style.color = r.ok ? "var(--green)" : "var(--red)";
  loadSettings();
}
$("#tgConnect").addEventListener("click", () => tgAction("/api/telegram/connect",
  r => `Підключено ✓ Перевір Telegram — там повідомлення від @${r.bot}.` + (r.screener_running ? " Скринер уже перезапущено — алерти йдуть у Telegram." : "")));
$("#tgTest").addEventListener("click", () => tgAction("/api/telegram/test", () => "Тест надіслано ✓ Перевір Telegram."));
$("#tgOff").addEventListener("click", () => { if (confirm("Відключити Telegram?")) tgAction("/api/telegram/disconnect", () => "Відключено."); });
