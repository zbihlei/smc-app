/* SMC App — вкладка «Бектест»: етап 1 (дані) і етап 2 (сигнали). Використовує $, esc, api (app.js). */
"use strict";

const BTS = { dataErr: "", data: null, timer: null, sigRun: null, sigRes: null, sigErr: "", tr: null, trLoaded: false };
const RUNNING = s => s === "running";
const STATUS_TXT = { idle: "ще не запускалось", running: "йде завантаження", done: "готово", done_errors: "готово, з помилками",
  stopped: "зупинено (кеш збережено — наступний запуск докачає)", error: "помилка" };
const nf = n => (n || 0).toLocaleString("uk-UA");
const mins = s => s == null ? "" : s < 90 ? `${Math.round(s)} с` : `${Math.round(s / 60)} хв`;

async function loadBacktest() {
  BTS.data = await api("/api/backtest/status");
  renderBacktest();
  clearTimeout(BTS.timer);
  const sg = BTS.data.signals;
  if (!BTS.trLoaded) { BTS.trLoaded = true; loadTrades(); }
  if (!sg) return;   // старий сервер (додаток не перезапущено) — підказка вже показана в renderBtSignals
  if (sg.last_done && sg.last_done !== BTS.sigRun && !sg.running) {
    BTS.sigRun = sg.last_done;
    const r = await api("/api/backtest/signals?run=" + sg.last_done);
    BTS.sigRes = r.ok ? r : null;
    renderSigResult();
  }
  if ((RUNNING(BTS.data.progress.status) || sg.running) && $("#tab-backtest").classList.contains("active")) BTS.timer = setTimeout(loadBacktest, 2000);
}
window.loadBacktest = loadBacktest;

function btMsg(text, kind) {
  const m = $("#btMsg");
  m.innerHTML = text;
  m.className = "msg" + (kind === "err" ? " err" : kind === "warn" ? " warn" : "");
}

function renderBacktest() {
  const d = BTS.data, p = d.progress, c = d.cache;
  $("#btPeriod").textContent = `період: ${d.period.start_txt.slice(0, 10)} – ${d.period.end_txt.slice(0, 10)} (${d.period.months} міс.)`;
  $("#btPath").textContent = c.path;
  const run = RUNNING(p.status);
  $("#btStart").disabled = run || !d.screener_found;
  $("#btStop").hidden = !run;
  $("#btMonths").disabled = run; $("#btTop").disabled = run;
  const prog = $("#btProg");
  if (p.status && p.status !== "idle") {
    prog.classList.remove("hidden");
    const pct = p.total ? Math.round(p.done / p.total * 100) : 0;
    $("#btBar").style.width = (run && !p.total ? 3 : pct) + "%";
    $("#btBar").className = run ? "run" : p.status === "done" ? "ok" : "warn";
    $("#btProgTxt").innerHTML = `<b>${esc(STATUS_TXT[p.status] || p.status)}</b>`
      + (p.total ? ` · монет ${p.done} з ${p.total}` : p.phase ? ` · ${esc(p.phase)}` : "")
      + (p.symbol ? ` · зараз <b>${esc(p.symbol)}</b>` : "")
      + ` · нових свічок ${nf(p.candles)} · запитів ${nf(p.requests)}`
      + (run && p.eta_s ? ` · лишилось ≈ ${mins(p.eta_s)}` : "")
      + (p.weight ? ` · вага ${p.weight}/2400` : "");
  } else prog.classList.add("hidden");
  if (p.status === "done") prog.classList.add("hidden");   // готово — видно в статусі кроку, смужка не потрібна
  if (BTS.dataErr) btMsg(BTS.dataErr, "err");
  else if (p.errors && p.errors.length) btMsg(`Помилки (${p.errors.length}):<br>` + p.errors.map(esc).join("<br>"), p.status === "error" ? "err" : "warn");
  else if (!d.screener_found) btMsg("Не знайдено smc_screener.py — список монет береться його ж функціями. Вкажи шлях у «Налаштуваннях».", "err");
  else $("#btMsg").classList.add("hidden");

  const top = c.symbols.filter(x => x.in_list);
  const late = top.filter(x => x.late).length;
  const n4 = top.reduce((a, x) => a + (x["4h"] ? x["4h"].n : 0), 0), n1 = top.reduce((a, x) => a + (x["1h"] ? x["1h"].n : 0), 0);
  $("#btSum").innerHTML = top.length ? `
    <div class="stat"><span>Монет у кеші</span><b>${d.ready}</b><i>з ${top.length} у списку</i></div>
    <div class="stat"><span>Свічок 4h</span><b>${nf(n4)}</b></div>
    <div class="stat"><span>Свічок 1h</span><b>${nf(n1)}</b></div>
    <div class="stat"><span>Пізній лістинг</span><b>${late}</b><i>історія коротша за період</i></div>
    <div class="stat"><span>Розмір кешу</span><b>${String(c.size_mb).replace(".", ",")} МБ</b></div>`
    : `<p class="muted">Кеш порожній. Натисни «Завантажити / докачати».</p>`;
  $("#btTblCount").textContent = top.length ? `(${top.length})` : "";
  $("#btBody").innerHTML = top.map(x => `<tr>
      <td class="num">${x.rank || ""}</td><td class="sym">${esc(x.symbol)}</td>
      <td>${x["4h"] ? `${x["4h"].from_txt.slice(0, 10)} – ${x["4h"].to_txt.slice(0, 10)} <span class="muted">(${nf(x["4h"].n)})</span>` : "–"}</td>
      <td>${x["1h"] ? `${x["1h"].from_txt.slice(0, 10)} – ${x["1h"].to_txt.slice(0, 10)} <span class="muted">(${nf(x["1h"].n)})</span>` : "–"}</td>
      <td>${x.late ? '<span class="tag amber">пізній лістинг</span> ' : ""}${x.extra ? `<span class="tag">додано ${esc(x.source || "")}</span>
        <button class="linkbtn" type="button" data-rmsym="${esc(x.symbol)}">прибрати</button>` : ""}</td></tr>`).join("");
  renderLogMissing();
  renderSteps();
  $("#btRuns").innerHTML = d.runs.length ? d.runs.map(r => `<div class="bt-run"><b>${esc((r.started_at || "").replace("T", " ").slice(0, 16))}</b>
      · ${r.months} міс., ${r.top < 0 ? `додано монет: ${-r.top}` : `топ-${r.top}`} · монет ${r.symbols ?? "–"} · свічок ${nf(r.candles)} · запитів ${nf(r.requests)}
      · <span class="${r.status === "done" ? "ok" : "warnc"}">${esc(STATUS_TXT[r.status] || r.status)}</span></div>`).join("")
    : '<p class="muted">Запусків ще не було.</p>';
  renderBtSignals();
}

// ---------------------------------------------------------------- додати монети в кеш
function renderLogMissing() {
  const d = BTS.data, miss = d.log_missing || [], run = RUNNING(d.progress.status);
  $("#btAddBtn").disabled = run || !d.screener_found;
  $("#btLogMiss").innerHTML = miss.length ? `<span class="muted small">Є в живому лозі сигналів, але немає в кеші (${miss.length}):</span>
      ${miss.map(x => `<button type="button" class="chip" data-addsym="${esc(x.symbol)}" title="рядків у лозі: ${x.rows}; останній ${esc(x.last)}">${esc(x.symbol.replace(/USDT$/, ""))}</button>`).join("")}
      <button class="btn small" type="button" id="btAddLog"${run ? " disabled" : ""}>Додати всі (${miss.length})</button>` : "";
}
async function addSymbols(symbols, source) {
  $("#btAddBtn").disabled = true;
  const r = await api("/api/backtest/data/add", { symbols, source });
  BTS.dataErr = r.ok ? "" : "Не додано: " + esc(r.error || r.message || "невідома помилка");
  if (r.ok) $("#btAddSyms").value = "";
  loadBacktest();
}
$("#btAddForm").addEventListener("submit", ev => { ev.preventDefault(); addSymbols($("#btAddSyms").value, "manual"); });
$("#btLogMiss").addEventListener("click", ev => {
  const c = ev.target.closest("[data-addsym]");
  if (c) {
    const cur = $("#btAddSyms").value.trim(), s = c.dataset.addsym.replace(/USDT$/, "");
    if (!cur.toUpperCase().split(/[\s,]+/).includes(s)) $("#btAddSyms").value = cur ? cur + ", " + s : s;
    return;
  }
  if (ev.target.id === "btAddLog") addSymbols((BTS.data.log_missing || []).map(x => x.symbol).join(","), "log");
});
$("#btBody").addEventListener("click", async ev => {
  const b = ev.target.closest("[data-rmsym]");
  if (!b) return;
  if (!confirm(`Прибрати ${b.dataset.rmsym} з бектесту? Свічки лишаться в кеші, але в пошуку сигналів монети не буде.`)) return;
  await api("/api/backtest/data/remove", { symbol: b.dataset.rmsym });
  loadBacktest();
});

// ---------------------------------------------------------------- статуси кроків (верхня картка)
const isoTxt = iso => iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)} ${iso.slice(11, 16)}` : "";
const doneRun = runs => (runs || []).find(r => r.status === "done" || r.status === "done_errors");
function renderSteps() {
  const d = BTS.data;
  if (!d) return;
  const dl = doneRun(d.runs), sg = d.signals || {}, sr = doneRun(sg.runs);
  $("#btSt1").innerHTML = d.ready ? `${d.ready} монет · ${d.period.months} міс.` + (dl ? ` · оновлено ${esc(isoTxt(dl.finished_at || dl.started_at))}` : "")
    : "ще немає — натисни «Оновити дані»";
  let t2 = sr ? `${nf(BTS.sigRes && BTS.sigRes.run.id === sr.id ? BTS.sigRes.summary.list : sr.events)} сигналів · ${esc(isoTxt(sr.finished_at || sr.started_at))}` : "ще не шукали";
  if (sr && dl && (dl.finished_at || "") > (sr.started_at || "")) t2 += ' · <span class="stale">дані новіші — знайди знову</span>';
  $("#btSt2").innerHTML = t2;
  const r = BTS.tr;
  let t3 = r ? `прогін №${r.id} · ${esc(isoTxt(r.created_at))}` : "ще не проганяли";
  if (r && sg.last_done && r.sig_run_id !== sg.last_done) t3 += ' · <span class="stale">сигнали оновлено — прожени знову</span>';
  $("#btSt3").innerHTML = t3;
}

// ============================================================ етап 2: сигнали
const SIG_TXT = { idle: "ще не запускалось", running: "йде прохід", done: "готово", done_errors: "готово, з помилками",
  stopped: "зупинено", error: "помилка" };
const fx = x => x == null ? "–" : (+x).toPrecision(5).replace(/\.?0+$/, "").replace(/(\.\d*?)0+e/, "$1e");
const dirTag = d => `<span class="tag ${d === "Long" ? "green" : "red"}">${d === "Long" ? "Long" : "Short"}</span>`;

function sigMsg(text, kind) {
  const m = $("#btSigMsg");
  if (!text) { m.classList.add("hidden"); return; }
  m.innerHTML = text;
  m.className = "msg" + (kind === "err" ? " err" : kind === "warn" ? " warn" : "");
}

function renderBtSignals() {
  const d = BTS.data, sg = d.signals;
  if (!sg) {
    $("#btSigStart").disabled = true;
    sigMsg("Сервер додатка ще старої версії. Натисни «Закрити додаток» і знову відкрий SMC App.", "err");
    return;
  }
  const p = sg.progress || {}, run = sg.running;
  const dlRun = RUNNING(d.progress.status);
  $("#btSigStart").disabled = run || dlRun || !d.ready || !d.screener_found;
  $("#btSigStop").hidden = !run;
  $("#btTrRun").disabled = run;
  $("#btSigHint").textContent = !d.ready ? "спершу завантаж дані (крок 1)" : dlRun ? "дочекайся завершення завантаження"
    : run ? "" : `монет у кеші: ${d.ready}; налаштування — з «Налаштувань»`;
  const prog = $("#btSigProg");
  if (p.status && p.status !== "idle") {
    prog.classList.remove("hidden");
    const pct = p.total ? Math.round(p.done / p.total * 100) : 0;
    $("#btSigBar").style.width = (run && !p.total ? 3 : pct) + "%";
    $("#btSigBar").className = run ? "run" : p.status === "done" ? "ok" : "warn";
    $("#btSigProgTxt").innerHTML = `<b>${esc(SIG_TXT[p.status] || p.status)}</b>`
      + (p.total ? ` · монет ${p.done} з ${p.total}` : p.phase ? ` · ${esc(p.phase)}` : "")
      + (run && p.symbol ? ` · остання <b>${esc(p.symbol)}</b>` : "")
      + (p.events ? ` · подій ${nf(p.events)}` : "") + (p.steps ? ` · кроків ${nf(p.steps)}` : "")
      + (run && p.eta_s ? ` · лишилось ≈ ${mins(p.eta_s)}` : "")
      + (!run && p.elapsed_s ? ` · за ${mins(p.elapsed_s)}` : "");
  } else prog.classList.add("hidden");
  if (p.status === "done") prog.classList.add("hidden");   // готово — видно в статусі кроку, смужка не потрібна
  if (BTS.sigErr) sigMsg(BTS.sigErr, "err");
  else if (p.errors && p.errors.length) sigMsg(`Помилки (${p.errors.length}):<br>` + p.errors.map(esc).join("<br>"), p.status === "error" ? "err" : "warn");
  else sigMsg("");
  $("#btSigRuns").innerHTML = sg.runs.length ? sg.runs.map(r => `<div class="bt-run"><b>${esc((r.started_at || "").replace("T", " ").slice(0, 16))}</b>
      · №${r.id} · монет ${r.symbols ?? "–"} · подій ${nf(r.events)} · кроків ${nf(r.steps)}${r.elapsed_s ? ` · ${mins(r.elapsed_s)}` : ""}
      · <span class="${r.status === "done" ? "ok" : "warnc"}">${esc(SIG_TXT[r.status] || r.status)}</span>
      ${r.status === "done" || r.status === "done_errors" ? `<button class="linkbtn" type="button" data-sigrun="${r.id}">показати</button>` : ""}</div>`).join("")
    : '<p class="muted">Запусків ще не було.</p>';
  renderSteps();
}

function renderSigResult() {
  const r = BTS.sigRes;
  $("#btSigRes").classList.toggle("hidden", !r);
  if (!r) return;
  const s = r.summary;
  $("#btSigPeriod").textContent = `прохід №${r.run.id}: ${r.period_txt}`;
  $("#btSigSum").innerHTML = `
    <div class="stat"><span>Сигналів «у списку»</span><b>${nf(s.list)}</b><i>≈ ${String(s.per_week).replace(".", ",")} на тиждень</i></div>
    <div class="stat"><span>Торкнулись зони</span><b>${nf(s.zone)}</b><i>≈ ${String(s.zone_per_week).replace(".", ",")} на тиждень</i></div>
    <div class="stat"><span>Long / Short</span><b>${nf(s.long)} / ${nf(s.short)}</b></div>
    <div class="stat"><span>Монет із сигналами</span><b>${s.symbols}</b></div>
    <div class="stat"><span>Відсіяно (проти 4h)</span><b>${nf(s.reject)}</b><i>лише для аналізу</i></div>`;
  $("#btSigMonths").innerHTML = s.months.map(m => `<tr><td>${esc(m.month)}</td><td class="num">${m.list}</td><td class="num">${m.zone}</td>
      <td class="num">${m.long}</td><td class="num">${m.short}</td></tr>`).join("");
  $("#btSigEvCount").textContent = `(${r.events.length < s.list ? `останні ${r.events.length} з ${nf(s.list)}` : r.events.length})`;
  $("#btSigEv").innerHTML = r.events.map(e => `<tr>
      <td class="nowrap">${esc(e.first_txt.slice(0, 16))}</td><td class="sym">${esc(e.symbol)}</td><td>${dirTag(e.dir)}</td>
      <td>${esc(e.first_status)}${e.appearances > 1 ? ` <span class="muted">×${e.appearances}</span>` : ""}</td>
      <td class="nowrap">${fx(e.zone_low)} – ${fx(e.zone_high)}</td><td class="nowrap">${fx(e.entry)} · ${fx(e.stop)} · ${fx(e.target)}</td>
      <td class="num">${(+e.rr).toFixed(1)}</td><td class="num">${e.score}</td>
      <td class="nowrap">${e.zone_txt ? esc(e.zone_txt.slice(0, 16)) : '<span class="muted">ні</span>'}</td><td class="num">${e.hours}</td></tr>`).join("");
  const par = r.params || {};
  const show = ["approach", "trend", "min_stop_atr", "wick", "min_rr", "max_zone", "buffer", "max_age", "max_leg", "events", "swing", "allow_retests", "need_fvg", "knife"];
  $("#btSigPar").innerHTML = `<div>${show.filter(k => k in par).map(k => `<code>${esc(k)}=${esc(String(par[k]))}</code>`).join(" ")}</div>
    <div class="muted">Команда: <code>${esc(r.run.command || "")}</code> · версія скринера (md5) <code>${esc((r.run.screener_md5 || "").slice(0, 10))}</code></div>`;
  renderCompare(r.compare);
  renderSteps();
}

function cmpRows(list, cols) {
  return list.map(x => `<tr>${cols.map(c => `<td${c.cls ? ` class="${c.cls}"` : ""}>${c.f(x)}</td>`).join("")}</tr>`).join("");
}

function renderCompare(c) {
  const box = $("#btCmp");
  if (!c || !c.ok) {
    box.innerHTML = `<h4>Звірка з живим логом</h4><p class="muted small">${esc(c ? c.error : "")}</p>`;
    return;
  }
  const L = c.list, R = c.reject;
  const pct = (a, b) => b ? Math.round(a / b * 100) + "%" : "–";
  const zone = x => `${fx(x.zone_low)} – ${fx(x.zone_high)}`;
  const colsLog = [{ f: x => esc(x.first_txt.slice(0, 16)), cls: "nowrap" }, { f: x => esc(x.symbol), cls: "sym" }, { f: x => dirTag(x.dir) },
    { f: zone, cls: "nowrap" }, { f: x => esc(x.reason) }];
  const colsRep = [{ f: x => esc(x.first_txt.slice(0, 16)), cls: "nowrap" }, { f: x => esc(x.symbol), cls: "sym" }, { f: x => dirTag(x.dir) },
    { f: zone, cls: "nowrap" }, { f: x => x.hours, cls: "num" }, { f: x => esc(x.reason) }];
  const colsM = [{ f: x => esc(x.first_txt.slice(0, 16)), cls: "nowrap" }, { f: x => esc(x.replay_first_txt.slice(0, 16)), cls: "nowrap" },
    { f: x => esc(x.symbol), cls: "sym" }, { f: x => dirTag(x.dir) }, { f: zone, cls: "nowrap" },
    { f: x => x.lag_h == null ? '<span class="muted">жила до початку логу</span>'
      : (x.lag_h > 0 ? "+" : "") + String(x.lag_h).replace(".", ",") + (x.slept ? ' <span class="muted">(Mac спав)</span>' : ""), cls: "num" },
    { f: x => x.same_kind ? "так" : '<span class="warnc">інше</span>' }];
  const unexplained = L.only_log.filter(x => x.reason !== "монети немає в кеші бектесту").length;
  box.innerHTML = `<h4>Звірка з живим логом <span class="muted small">${esc(c.from_txt)} – ${esc(c.to_txt)} · сканів ${nf(c.scans)} · <code>${esc(c.path)}</code></span></h4>
    <div class="bt-sum">
      <div class="stat"><span>«У списку»: збіглось</span><b>${L.matched} з ${L.log_cached}</b><i>${pct(L.matched, L.log_cached)} подій по монетах із кешу (усього в лозі ${L.log})</i></div>
      <div class="stat"><span>Лише в живому лозі</span><b>${L.only_log.length}</b><i>${unexplained ? `${unexplained} без пояснення` : "усі пояснено"}</i></div>
      <div class="stat"><span>Лише в replay</span><b>${L.only_replay.length}</b><i>${L.explained_only_replay} — Mac не сканував</i></div>
      <div class="stat"><span>Момент появи ±1 год</span><b>${L.lag_n ? `${L.lag_within_1h} з ${L.lag_n}` : "–"}</b><i>медіана ${L.lag_median_h == null ? "–" : String(L.lag_median_h).replace(".", ",") + " год"}; replay — щогодини, живий — що 30 хв</i></div>
      <div class="stat"><span>«Відсіяно»: збіглось</span><b>${R.matched} з ${R.log_cached}</b><i>${pct(R.matched, R.log_cached)} по монетах із кешу</i></div>
    </div>
    <details class="logbox"><summary>Збіглись «у списку» (${L.matched})</summary><div class="table-wrap bt-tbl"><table><thead><tr><th>Живий</th><th>Replay</th><th>Монета</th><th></th><th>Зона</th><th>Replay − живий, год</th><th>Той самий тип</th></tr></thead>
      <tbody>${cmpRows(L.matched_rows, colsM)}</tbody></table></div></details>
    <details class="logbox"${L.only_log.length ? " open" : ""}><summary>Лише в живому лозі (${L.only_log.length})</summary><div class="table-wrap bt-tbl"><table><thead><tr><th>Перша поява</th><th>Монета</th><th></th><th>Зона</th><th>Причина</th></tr></thead>
      <tbody>${cmpRows(L.only_log, colsLog) || '<tr><td colspan="5" class="muted">немає</td></tr>'}</tbody></table></div></details>
    <details class="logbox"><summary>Лише в replay (${L.only_replay.length})</summary><div class="table-wrap bt-tbl"><table><thead><tr><th>Поява</th><th>Монета</th><th></th><th>Зона</th><th>Год.</th><th>Причина</th></tr></thead>
      <tbody>${cmpRows(L.only_replay, colsRep) || '<tr><td colspan="6" class="muted">немає</td></tr>'}</tbody></table></div></details>`;
}

$("#btSigStart").addEventListener("click", async () => {
  $("#btSigStart").disabled = true;
  BTS.sigErr = "";
  sigMsg("Запускаю…");
  let r;
  try { r = await api("/api/backtest/signals/start", {}); } catch (e) { r = { error: "немає зв'язку з додатком" }; }
  if (!r.ok) BTS.sigErr = "Не запустилось: " + esc(r.error || r.message || "невідома помилка");
  setTimeout(loadBacktest, 300);
});
$("#btSigStop").addEventListener("click", async () => {
  await api("/api/backtest/signals/stop", {});
  setTimeout(loadBacktest, 800);
});
$("#btSigRuns").addEventListener("click", async ev => {
  const b = ev.target.closest("[data-sigrun]");
  if (!b) return;
  const r = await api("/api/backtest/signals?run=" + b.dataset.sigrun);
  BTS.sigRes = r.ok ? r : null;
  BTS.sigRun = +b.dataset.sigrun;
  renderSigResult();
  $("#btSigCard").scrollIntoView({ block: "start" });
});

// ============================================================ етап 3: угоди
const R = x => x == null ? "–" : `<span class="${x > 0.05 ? "rpos" : x < -0.05 ? "rneg" : ""}">${x > 0 ? "+" : ""}${(+x).toFixed(2).replace(".", ",")}</span>`;
const num = (x, n = 2) => x == null ? "–" : (+x).toFixed(n).replace(".", ",");
const dts = ms => { if (!ms) return "–"; const d = new Date(ms); const p = n => String(n).padStart(2, "0");
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${String(d.getFullYear()).slice(2)} ${p(d.getHours())}:${p(d.getMinutes())}`; };

function trMsg(text, kind) {
  const m = $("#btTrMsg");
  if (!text) { m.classList.add("hidden"); return; }
  m.innerHTML = text;
  m.className = "msg" + (kind === "err" ? " err" : kind === "warn" ? " warn" : "");
}

async function loadTrades(id) {
  const r = await api("/api/backtest/trades" + (id ? "?run=" + id : ""));
  if (r.ok) { BTS.tr = r; fillTrForm(r.params); }
  renderTrades();
  if (!id) loadOos();
}

function renderBtSummary(sel, sm, title) {
  const box = $(sel);
  if (!sm) { box.innerHTML = ""; return; }
  box.className = "bt-summary tone-" + sm.tone;
  box.innerHTML = `<h4>${esc(title)}</h4><p class="verdict">${esc(sm.verdict)}</p><ul>${sm.points.map(x => `<li>${esc(x)}</li>`).join("")}</ul>`;
}

// ============================================================ етап 4: розрізи
function renderSlices(r) {
  $("#btSlRun").textContent = r ? `прогін №${r.id} · ${r.filters_text} · закритих угод ${r.metrics.a.n || 0}` : "";
  if (!r || !r.slices) return;
  renderBtSummary("#btSlSummary", r.slices_summary, "Що показують розрізи");
  const miss = r.slices.some(s => s.missing);
  $("#btSlBox").innerHTML = `<div class="table-wrap bt-tbl"><table><thead><tr><th>Група</th><th>Угод</th><th>Виграшних</th>
      <th>Сер. R: до цілі</th><th>Сума R</th><th>PF</th><th>Сер. R: за стратегією</th></tr></thead><tbody>`
    + r.slices.filter(s => s.rows.length).map(s => `<tr class="sl-hd"><td colspan="7">${esc(s.name)}</td></tr>` + s.rows.map(x =>
      `<tr${x.small ? ' class="small" title="менше 30 угод — мало даних"' : ""}><td>${esc(x.group)}</td><td class="num">${x.n}</td>
        <td class="num">${num(x.winrate, 1)}%</td><td class="num">${R(x.avg_a)}</td><td class="num">${R(x.sum_a)}</td>
        <td class="num">${x.pf == null ? "–" : num(x.pf)}</td><td class="num">${R(x.avg_b)}</td></tr>`).join("")).join("")
    + `</tbody></table></div>` + (miss ? '<p class="muted small">Частина ознак відсутня в цьому прогоні — натисни «Прогнати угоди» ще раз.</p>' : "");
}

// ============================================================ етап 5: поза вибіркою
function oosMsg(text, kind) {
  const m = $("#btOosMsg");
  if (!text) { m.classList.add("hidden"); return; }
  m.innerHTML = text;
  m.className = "msg" + (kind === "err" ? " err" : kind === "warn" ? " warn" : "");
}
function renderOosRules() {
  const r = BTS.tr;
  $("#btOosOpen").disabled = !r;
  $("#btOosRules").innerHTML = r ? `Правила прогону №${r.id}: <b>${esc(r.filters_text)}</b>; лімітка від середини,
      ${r.params.expiry_h ? `термін ${r.params.expiry_h} год` : "без терміну"}${r.params.cancel_on_target ? ", знімати за ціллю" : ""}${r.params.one_per_symbol ? ", одна угода на монету" : ""};
      ведення — обидва варіанти. На розробці: середній R ${R(r.metrics.a.avg)} / ${R(r.metrics.b.avg)}, угод ${r.metrics.a.n || 0}.`
    : "Спершу «Прогнати угоди» (крок 3).";
}
async function loadOos(id) {
  const r = await api("/api/backtest/oos" + (id ? "?run=" + id : ""));
  BTS.oos = r;
  renderOos();
}
function renderOos() {
  const r = BTS.oos || {}, log = r.log || [];
  $("#btOosCount").textContent = `(${log.length})`;
  $("#btOosLog").innerHTML = log.length ? log.slice().reverse().map(x => `<div class="bt-run"><b>${esc((x.created_at || "").replace("T", " ").slice(0, 16))}</b>
      · правила прогону №${x.base_id}: ${esc(x.rules)} <button class="linkbtn" type="button" data-oos="${x.oos_id}">показати</button></div>`).join("")
    : '<p class="muted">Ще не відкривали.</p>';
  $("#btOosRes").classList.toggle("hidden", !r.ok);
  if (!r.ok) return;
  const d = r.base ? r.base.metrics : null, o = r.metrics;
  $("#btOosPeriod").textContent = `відкриття для прогону №${r.base_id}: ${o.dev_from.slice(0, 10)} – ${o.dev_to.slice(0, 10)} · ${r.filters_text}`;
  const cell = (m, f) => m && m.a.n ? `${f(m.a)} · ${f(m.b)}` : "–";
  const row = (name, f) => `<tr><td>${name}</td><td class="num">${cell(d, f)}</td><td class="num">${cell(o, f)}</td></tr>`;
  $("#btOosTbl").innerHTML = [
    row("Закритих угод", x => nf(x.n)),
    row("Частка виграшних", x => num(x.winrate, 1) + "%"),
    row("<b>Середній R на угоду</b>", x => R(x.avg)),
    row("Сума R", x => R(x.sum)),
    row("Profit factor", x => x.pf == null ? "–" : num(x.pf)),
    row("Макс. просадка, R", x => num(x.max_dd, 1)),
    row("Найдовша серія збитків", x => x.max_streak),
  ].join("");
  renderBtSummary("#btOosSummary", r.summary, "Висновок перевірки поза вибіркою");
  $("#btOosNote").innerHTML = log.length > 1 ? `<b>Увага:</b> поза вибіркою відкривали ${log.length} рази — кожне нове відкриття знецінює перевірку.` : "";
}
$("#btOosOpen").addEventListener("click", async () => {
  const r = BTS.tr; if (!r) return;
  const n = ((BTS.oos || {}).log || []).length;
  const warn = n ? `\n\nУВАГА: поза вибіркою вже відкривали ${n} раз(и). Нове відкриття для інших правил — це вже підбір, а не перевірка.` : "";
  if (!confirm(`Відкрити останні 4 місяці для правил прогону №${r.id} (${r.filters_text})?\n\nПісля цього не підкручуй фільтри під результат.${warn}`)) return;
  $("#btOosOpen").disabled = true;
  oosMsg("Рахую…");
  let x;
  try { x = await api("/api/backtest/oos/open", { base_id: r.id }); } catch (e) { x = { error: "немає зв'язку з додатком" }; }
  $("#btOosOpen").disabled = false;
  if (!x.ok) { oosMsg("Не вдалося: " + esc(x.error || x.message || "невідома помилка"), "err"); return; }
  oosMsg("");
  BTS.oos = x;
  renderOos();
});
$("#btOosLog").addEventListener("click", ev => {
  const b = ev.target.closest("[data-oos]");
  if (b) loadOos(+b.dataset.oos);
});

function fillTrForm(p) {
  const f = $("#btTrForm");
  if (!p) return;
  f.expiry_h.value = p.expiry_h; f.maker.value = p.maker; f.taker.value = p.taker;
  f.cancel_on_target.checked = !!p.cancel_on_target; f.one_per_symbol.checked = !!p.one_per_symbol;
  f.min_score.value = p.min_score == null ? "" : String(p.min_score);
  f.max_rr.value = p.max_rr == null ? "" : p.max_rr;
  f.dirs.value = p.dirs || "both";
  f.zones.value = p.zones || "all";
  f.entry_tol.value = p.entry_tol == null ? "" : p.entry_tol;
  f.rr_at_limit.checked = !!p.rr_at_limit;
  f.no_knife.checked = !!p.no_knife; f.no_tight.checked = !!p.no_tight;
}

function renderTrades() {
  const r = BTS.tr;
  $("#btTrRes").classList.toggle("hidden", !r);
  if (!r) { $("#btTrRuns").innerHTML = '<p class="muted">Прогонів ще не було.</p>'; renderSteps(); return; }
  const m = r.metrics;
  $("#btTrPeriod").textContent = `прогін №${r.id}: сигнали ${m.dev_from.slice(0, 10)} – ${m.dev_to.slice(0, 10)} (розробка) · ${r.filters_text}`;
  const cancels = m.cancels.map(c => `${esc(c.text)} — <b>${c.n}</b>`).join(" · ");
  $("#btTrFunnel").innerHTML = (m.filtered ? `<span>Відфільтровано: <b>${nf(m.filtered)}</b> (${esc(r.filters_text)})</span>` : "")
    + `<span>Сигналів: <b>${nf(m.signals)}</b></span><span>Входів: <b>${nf(m.trades)}</b>${m.open ? ` (ще відкриті: ${m.open})` : ""}</span>
    <span>Медіана до входу: <b>${m.fill_h_median ?? "–"} год</b> (макс. ${m.fill_h_max ?? "–"})</span><span>Медіана R:R: <b>${num(m.rr_median, 1)}</b></span>
    <span class="muted">Не ввійшли: ${cancels || "–"}</span>`;
  const A = m.a, B = m.b;
  const row = (name, f) => `<tr><td>${name}</td><td class="num">${f(A)}</td><td class="num">${f(B)}</td></tr>`;
  $("#btTrVar").innerHTML = A.n ? [
    row("Закритих угод", x => nf(x.n)),
    row("Виграшних / збиткових / в нуль", x => x.n ? `${x.wins} / ${x.losses} / ${x.be}` : "–"),
    row("Частка виграшних", x => x.n ? num(x.winrate, 1) + "%" : "–"),
    row("<b>Середній R на угоду</b> (з комісіями)", x => x.n ? `<b>${R(x.avg)}</b>` : "–"),
    row("Сума R", x => x.n ? R(x.sum) : "–"),
    row("Profit factor", x => x.pf == null ? "–" : num(x.pf)),
    row("Макс. просадка, R", x => x.n ? num(x.max_dd, 1) : "–"),
    row("Найдовша серія збитків", x => x.n ? x.max_streak : "–"),
  ].join("") : '<tr><td colspan="3" class="muted">Закритих угод немає.</td></tr>';
  $("#btTrMfe").innerHTML = m.mfe.map(x => `<tr><td>${x.k}R</td><td class="num">${num(x.share, 1)}%</td><td class="num">${x.losers}</td></tr>`).join("");
  $("#btTrSide").innerHTML = ["Long", "Short"].map(k => { const x = m.side[k] || {}; return `<tr><td>${dirTag(k)}</td><td class="num">${x.n || 0}</td>
      <td class="num">${x.n ? num(x.winrate, 1) + "%" : "–"}</td><td class="num">${x.n ? R(x.avg) : "–"}</td><td class="num">${x.n ? R(x.sum) : "–"}</td></tr>`; }).join("");
  $("#btTrMonths").innerHTML = m.months.map(x => `<tr><td>${esc(x.month)}</td><td class="num">${x.n}</td><td class="num">${x.wins}</td>
      <td class="num">${R(x.a)}</td><td class="num">${R(x.b)}</td></tr>`).join("");
  renderBtSummary("#btTrSummary", r.summary, "Висновок простими словами");
  renderTrList();
  renderSlices(r);
  renderOosRules();
  renderKey(r);
  renderSteps();
  $("#btTrRuns").innerHTML = (r.runs || []).map(x => `<div class="bt-run"><b>${esc((x.created_at || "").replace("T", " ").slice(0, 16))}</b>
      · №${x.id} (сигнали №${x.sig_run_id}) · <b>${esc(x.filters_text)}</b> · угод ${x.trades} · сер. R ${R(x.avg_a)} / ${R(x.avg_b)} · сума ${R(x.sum_a)} / ${R(x.sum_b)}
      · термін ${x.params.expiry_h ? x.params.expiry_h + " год" : "без терміну"}${x.params.cancel_on_target ? "" : " · без зняття за ціллю"}${x.params.one_per_symbol ? "" : " · кілька угод на монету"}
      <button class="linkbtn" type="button" data-trrun="${x.id}">показати</button></div>`).join("") || '<p class="muted">Прогонів ще не було.</p>';
}

function renderKey(r) {
  const m = r.metrics, A = m.a || {}, B = m.b || {};
  $("#btFiltTxt").textContent = r.filters_text || "без фільтрів";
  $("#btKey").innerHTML = A.n ? `
    <div class="k"><span>Угод</span><b>${nf(A.n)}</b><i>${m.period_days ? `≈ ${num(A.n / m.period_days * 7, 1)} на тиждень` : ""}</i></div>
    <div class="k"><span>Виграшних</span><b>${num(A.winrate, 0)}%</b><i>за стратегією ${B.n ? num(B.winrate, 0) + "%" : "–"}</i></div>
    <div class="k"><span>Середній R на угоду</span><b>${R(A.avg)}</b><i>за стратегією ${B.n ? R(B.avg) : "–"}</i></div>
    <div class="k"><span>Макс. просадка</span><b>${num(A.max_dd, 0)}R</b><i>серія збитків ${A.max_streak}</i></div>`
    : '<p class="muted">Закритих угод немає — послаб фільтри.</p>';
}

function renderTrList() {
  const r = BTS.tr; if (!r) return;
  const q = $("#btTrSearch").value.trim().toUpperCase(), f = $("#btTrFilter").value;
  const list = r.trades.filter(t => (!q || t.symbol.includes(q)) && (!f || (f === "open" ? t.state === "open"
    : t.state === "closed" && (f === "win" ? t.r_a > 0 : t.r_a < 0)))).sort((a, b) => b.fill_ms - a.fill_ms);
  $("#btTrCount").textContent = `(${list.length})`;
  $("#btTrBody").innerHTML = list.slice(0, 500).map(t => `<tr>
      <td class="nowrap">${dts(t.signal_ms)}</td><td class="nowrap">${dts(t.fill_ms)}</td><td class="sym">${esc(t.symbol)}</td><td>${dirTag(t.dir)}</td>
      <td class="num">${num(t.rr, 1)}</td><td class="num">${num(t.risk_pct, 2)}</td>
      <td class="num">${t.state === "open" ? '<span class="muted">відкрита</span>' : R(t.r_a)}</td><td class="num">${R(t.r_b)}</td>
      <td class="num">${num(t.mfe, 1)}</td><td class="num">${num(t.mae, 2)}</td><td class="num">${t.hours ?? "–"}</td></tr>`).join("");
}

$("#btTrForm").addEventListener("submit", async ev => {
  ev.preventDefault();
  const f = ev.target, btn = $("#btTrRun");
  btn.disabled = true;
  trMsg("Рахую угоди…");
  const params = { expiry_h: +f.expiry_h.value || 0, maker: +f.maker.value, taker: +f.taker.value,
    cancel_on_target: f.cancel_on_target.checked, one_per_symbol: f.one_per_symbol.checked,
    min_score: f.min_score.value === "" ? null : +f.min_score.value, max_rr: f.max_rr.value === "" ? null : +f.max_rr.value,
    dirs: f.dirs.value, zones: f.zones.value, entry_tol: f.entry_tol.value === "" ? null : +f.entry_tol.value, rr_at_limit: f.rr_at_limit.checked, no_knife: f.no_knife.checked, no_tight: f.no_tight.checked };
  let r;
  try { r = await api("/api/backtest/trades/run", { params }); } catch (e) { r = { error: "немає зв'язку з додатком" }; }
  btn.disabled = false;
  if (!r.ok) { trMsg("Не вдалося: " + esc(r.error || r.message || "невідома помилка"), "err"); return; }
  trMsg(`Готово за ${String(r.elapsed_s).replace(".", ",")} с.`);
  BTS.tr = r;
  renderTrades();
  if (BTS.oos) renderOos();
});
$("#btTrSearch").addEventListener("input", renderTrList);
$("#btTrFilter").addEventListener("change", renderTrList);
$("#btTrRuns").addEventListener("click", ev => {
  const b = ev.target.closest("[data-trrun]");
  if (b) { trMsg(""); loadTrades(+b.dataset.trrun); }
});

$("#btStart").addEventListener("click", async () => {
  $("#btStart").disabled = true;
  const r = await api("/api/backtest/data/start", { months: +$("#btMonths").value, top: +$("#btTop").value });
  BTS.dataErr = r.ok ? "" : "Не запустилось: " + esc(r.error || r.message || "невідома помилка");
  loadBacktest();
});
$("#btStop").addEventListener("click", async () => {
  await api("/api/backtest/data/stop", {});
  setTimeout(loadBacktest, 500);
});

if ($("#tab-backtest").classList.contains("active")) loadBacktest();
