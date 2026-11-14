/* Rulebound — вкладка «Підсумок» (етап 3). Використовує $, $$, esc, api, store (app.js) і ua2 (journal.js). */
"use strict";

const SS = { period: "all", data: null };
const sR = x => (x === null || x === undefined) ? "–" : (x > 0 ? "+" : "") + ua2(x) + "R";
const sCls = x => x > 0 ? "pos" : x < 0 ? "neg" : "";
const pct1 = x => (x === null || x === undefined) ? "–" : String(x).replace(".", ",") + "%";

async function loadSummary() {
  const id = +(store("smc_jlist") || 0);   // вибраний список — спільний із «Журналом»
  const d = await api("/api/stats?period=" + SS.period + (id ? "&list=" + id : ""));
  SS.data = d;
  renderListSel();
  renderSummary();
}
function renderListSel() {
  const d = SS.data, cur = d.list ? d.list.id : null;
  const by = {};
  (d.lists || []).forEach(l => { (by[l.section] = by[l.section] || []).push(l); });
  $("#sList").innerHTML = Object.keys(by).map(sec => `<optgroup label="${esc(sec)}">` + by[sec].slice().sort((a, b) => (a.status === "active" ? 0 : 1) - (b.status === "active" ? 0 : 1) || b.id - a.id)
    .map(l => `<option value="${l.id}"${l.id === cur ? " selected" : ""}>${esc(l.name)}${l.status !== "active" ? " (архів)" : ""} · угод ${l.count}</option>`).join("") + "</optgroup>").join("");
}
$("#sList").addEventListener("change", e => {
  store("smc_jlist", e.target.value);
  loadSummary();
});
window.loadSummary = loadSummary;

$("#sPeriod").addEventListener("click", e => {
  const b = e.target.closest(".chip");
  if (!b) return;
  SS.period = b.dataset.p;
  $$("#sPeriod .chip").forEach(x => x.classList.toggle("on", x === b));
  store("smc_period", SS.period);
  loadSummary();
});

function kpi(label, value, sub, cls) {
  return `<div class="stat"><span>${label}</span><b class="${cls || ""}">${value}</b>${sub ? `<i>${sub}</i>` : ""}</div>`;
}

function renderSummary() {
  const d = SS.data, g = d.general;
  $("#sNote").textContent = (d.list ? `${d.list.section} · «${d.list.name}»${d.list.status !== "active" ? " (архів)" : ""} · ` : "")
    + `${d.period_label}` + (g.open ? ` · відкритих угод ${g.open} (не враховано)` : "");
  if (!g.n) {
    $("#sKpi").innerHTML = "";
    $("#sEq").innerHTML = '<div class="empty">За цей період немає закритих угод.</div>';
    ["#sHyp", "#sGroups", "#sMfe", "#sSig"].forEach(s => { $(s).innerHTML = ""; });
    return;
  }
  $("#sKpi").innerHTML = [
    kpi("Угод", g.n, `${g.wins} + / ${g.losses} − / ${g.be} у 0`),
    kpi("Вінрейт", pct1(g.winrate), g.be_winrate !== null ? `беззбитковий ${pct1(g.be_winrate)} при R:R ${ua2(g.avg_rr)}` : ""),
    kpi("Сума", sR(g.sum), "", sCls(g.sum)),
    kpi("Середній R", sR(g.avg), "очікування на угоду", sCls(g.avg)),
    kpi("Profit factor", g.profit_factor === null ? "–" : ua2(g.profit_factor), "сума плюсів / сума мінусів"),
    kpi("Сер. виграш / збиток", `${sR(g.avg_win)} / ${sR(g.avg_loss)}`, ""),
    kpi("Макс. просадка", ua2(g.max_dd) + "R", `зараз ${ua2(g.current_dd)}R`, g.max_dd > 0 ? "neg" : ""),
    kpi("Найдовша серія збитків", g.longest_streak, `зараз ${g.current_streak}`),
  ].join("");
  renderEquity(d.equity);
  renderHyp(d.hypotheses);
  renderGroups(d.groups, d.small);
  renderMfe(d.mfe);
  renderSig(d.signal);
}

// ---------------------------------------------------------------- крива накопиченого R (SVG)
function renderEquity(eq) {
  const W = 1000, H = 260, L = 46, R = 12, T = 14, B = 30;
  const ys = eq.map(p => p.cum).concat([0]);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (hi - lo < 1) { hi += 0.5; lo -= 0.5; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const n = eq.length;
  const X = i => L + (n === 1 ? (W - L - R) / 2 : i * (W - L - R) / (n - 1));
  const Y = v => T + (hi - v) * (H - T - B) / (hi - lo);
  const step = (hi - lo) > 20 ? 5 : (hi - lo) > 8 ? 2 : 1;
  let grid = "";
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
    grid += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" class="${v === 0 ? "zero" : "gl"}"/>`
      + `<text x="${L - 6}" y="${Y(v) + 4}" class="yl">${v}R</text>`;
  }
  // просадка: між піком і поточним значенням
  let peak = -Infinity, dd = "";
  eq.forEach((p, i) => {
    peak = Math.max(peak, p.cum);
    if (p.dd > 0) dd += `<rect x="${X(i) - 4}" y="${Y(peak)}" width="8" height="${Math.max(1, Y(p.cum) - Y(peak))}" class="ddbar"/>`;
  });
  const line = eq.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p.cum).toFixed(1)}`).join(" ");
  const every = Math.max(1, Math.ceil(n / 20));
  const dots = eq.map((p, i) => `<circle cx="${X(i)}" cy="${Y(p.cum)}" r="4.5" class="${p.result > 0 ? "dw" : p.result < 0 ? "dl" : "d0"}">
      <title>№${p.n} ${esc(p.instrument)} · ${p.date ? p.date.slice(8, 10) + "." + p.date.slice(5, 7) : ""} · ${sR(p.result)} · накопичено ${ua2(p.cum)}R</title></circle>`
    + (i % every === 0 || i === n - 1 ? `<text x="${X(i)}" y="${H - 10}" class="xl">${p.n}</text>` : "")).join("");
  $("#sEq").innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Крива накопиченого R">
    ${grid}${dd}<path d="${line}" class="eqline"/>${dots}</svg>`;
}

// ---------------------------------------------------------------- гіпотези
function renderHyp(list) {
  $("#sHyp").innerHTML = list.map(h => `<div class="hyp h-${h.cls}">
      <div class="hyp-hd"><b>${esc(h.title)}</b><span class="verdict">${esc(h.verdict)}</span></div>
      <div class="hyp-tx">${esc(h.text)}</div></div>`).join("");
}

// ---------------------------------------------------------------- групи
const GROUPS = [
  ["rules", "За правилами"], ["choch", "Був CHoCH на 1h"], ["zone", "Тип зони"], ["support", "Підтримка 4h поряд"],
  ["rr", "R:R плану"], ["stop", "Ширина стопа (від входу)"], ["management", "Ведення угоди"], ["screener", "По скринеру"],
  ["dir", "Напрямок"], ["instrument", "Інструмент"],
];
function groupTable(rows, small) {
  const maxAbs = Math.max(0.01, ...rows.map(r => Math.abs(r.avg || 0)));
  return `<table class="gt"><thead><tr><th></th><th>Угод</th><th>Вінрейт</th><th>Сер. R</th><th>Сума</th></tr></thead><tbody>` +
    rows.map(r => {
      const w = r.avg === null ? 0 : Math.round(Math.abs(r.avg) / maxAbs * 100);
      const bar = r.n ? `<span class="bar ${r.avg >= 0 ? "bp" : "bn"}" style="width:${w}%"></span>` : "";
      return `<tr class="${r.n ? "" : "empty-row"}">
        <td>${esc(r.label)}${r.n && r.small ? ` <span class="tag" title="менше ${small} угод">мало</span>` : ""}</td>
        <td class="num">${r.n}</td><td class="num">${r.winrate === null ? "–" : r.winrate + "%"}</td>
        <td class="num avgc"><span class="barw">${bar}</span><b class="${sCls(r.avg)}">${r.avg === null ? "–" : sR(r.avg)}</b></td>
        <td class="num ${sCls(r.sum)}">${r.n ? sR(r.sum) : "–"}</td></tr>`;
    }).join("") + "</tbody></table>";
}
function renderGroups(g, small) {
  $("#sGroups").innerHTML = GROUPS.map(([k, title]) =>
    `<div class="card gcard"><div class="hd"><b>${title}</b></div>${groupTable(g[k], small)}</div>`).join("");
}

// ---------------------------------------------------------------- MFE
function renderMfe(m) {
  if (!m.n) {
    $("#sMfe").innerHTML = '<p class="muted">MFE ще не заповнено. Записуй у кожній закритій угоді «найкращу ціну після входу» — калькулятор у формі порахує MFE.</p>';
    return;
  }
  const best = Math.max(...m.scenarios.map(s => Math.abs(s.sum || 0)), 0.01);
  $("#sMfe").innerHTML = `
    <div class="mfe-kpi">
      <span>Угод з MFE: <b>${m.n}</b>${m.small ? ' <span class="tag">мало</span>' : ""}</span>
      <span>Середній MFE: <b>${ua2(m.avg)}R</b></span>
      <span>Дійшли до 3R: <b>${m.reached3}</b> (з них закрито в 0 або мінус: <b>${m.reached3_closed_le0}</b>)</span>
      <span>Дійшли до 4R: <b>${m.reached4}</b></span>
      <span>Ціль взята (MFE ≥ R:R плану): <b>${m.target_taken}</b></span>
    </div>
    <table class="gt scen"><thead><tr><th>Сценарій на цих ${m.n} угодах</th><th>Сума</th><th></th></tr></thead><tbody>
      ${m.scenarios.map(s => `<tr><td><b>${esc(s.label)}</b><div class="muted small">${esc(s.hint)}</div></td>
        <td class="num avgc"><span class="barw"><span class="bar ${s.sum >= 0 ? "bp" : "bn"}" style="width:${Math.round(Math.abs(s.sum) / best * 100)}%"></span></span>
          <b class="${sCls(s.sum)}">${sR(s.sum)}</b></td><td></td></tr>`).join("")}
    </tbody></table>
    <p class="muted small">Сценарії — наближення: якщо ціна не дійшла до X, ми не знаємо, куди б вона пішла далі, тому береться фактичний результат.</p>`;
}

// ---------------------------------------------------------------- скринер
function renderSig(s) {
  if (!s.n) {
    $("#sSig").innerHTML = '<p class="muted">Ще немає закритих угод із прив\'язаним сигналом скринера. Коли додаєш угоду «зі скринера» (або прив\'язуєш сигнал у ручній формі), тут з\'явиться порівняння твого стопа й входу з рівнями скринера.</p>';
    return;
  }
  $("#sSig").innerHTML = `<p class="muted small">Угод із прив'язаним сигналом: ${s.n}.</p>
    <div class="sgrid two"><div>${groupTable(s.stop, SS.data.small)}</div><div>${groupTable(s.entry, SS.data.small)}</div></div>`;
}

// запам'ятований період і вкладка, відкрита до завантаження файла
(function () {
  const p = store("smc_period");
  if (p && $(`#sPeriod [data-p="${p}"]`)) {
    SS.period = p;
    $$("#sPeriod .chip").forEach(x => x.classList.toggle("on", x.dataset.p === p));
  }
  if ($("#tab-summary").classList.contains("active")) loadSummary();
})();
