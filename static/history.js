/* Rulebound — вкладка «Історія»: хронологія змін (що, чому, з якої угоди, результат).
   Використовує $, $$, esc, api, store (app.js), mdInline (strategy.js), openTab (app.js), loadJournal/openDrawer (journal.js). */
"use strict";

const HS = { data: null, comp: "all", q: "", editId: null };
const MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"];
const COMP_CLASS = { "Скринер": "c-scr", "Стратегія": "c-str", "Журнал": "c-jrn", "Додаток": "c-app", "Інструменти": "c-tool" };

function hDate(iso) {
  if (!iso) return "Дата невідома";
  const d = new Date(iso + "T12:00:00");
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

async function loadHistory() {
  HS.data = await api("/api/changelog");
  renderHistory();
}
window.loadHistory = loadHistory;

function hMsg(text, kind) {
  const m = $("#hMsg");
  m.innerHTML = text;
  m.className = "msg" + (kind === "err" ? " err" : kind === "warn" ? " warn" : "");
  clearTimeout(hMsg.t);
  if (kind === "ok") hMsg.t = setTimeout(() => m.classList.add("hidden"), 6000);
}

function matches(e) {
  if (HS.comp !== "all" && e.component !== HS.comp) return false;
  const q = HS.q.trim().toLowerCase().replace(/^№\s*/, "");
  if (!q) return true;
  if (/^\d+$/.test(q)) return e.trades.includes(+q);  // число = номер угоди
  return [e.title, e.what, e.why, e.result, e.version, e.component].join(" ").toLowerCase().includes(q);
}

function renderHistory() {
  const d = HS.data, all = d.entries || [];
  const counts = {};
  all.forEach(e => { counts[e.component] = (counts[e.component] || 0) + 1; });
  $("#hComp").innerHTML = `<button class="chip ${HS.comp === "all" ? "on" : ""}" data-c="all">Усе <span>${all.length}</span></button>`
    + d.components.map(c => `<button class="chip ${HS.comp === c ? "on" : ""}" data-c="${esc(c)}">${esc(c)} <span>${counts[c] || 0}</span></button>`).join("");
  const unknown = all.filter(e => !e.date).length;
  $("#hMeta").textContent = `записів: ${all.length}` + (unknown ? ` · без дати: ${unknown}` : "");

  // новіші вгорі; записи без дати — внизу; в межах дня порядок як у файлі
  const list = all.map((e, i) => ({ e, i })).filter(x => matches(x.e))
    .sort((a, b) => (b.e.date || "0000").localeCompare(a.e.date || "0000") || a.i - b.i).map(x => x.e);
  if (!list.length) {
    $("#hList").innerHTML = `<div class="empty">${all.length ? "Під фільтр нічого не підходить." : "Історія порожня. Натисни «+ Запис»."}</div>`;
    return;
  }
  let html = "", lastDay = null;
  for (const e of list) {
    const day = e.date || "";
    if (day !== lastDay) {
      html += `<div class="t-day">${e.approx && day ? "≈ " : ""}${hDate(day)}</div>`;
      lastDay = day;
    }
    const trades = (e.trades || []).map(n => `<button type="button" class="tlink" data-trade="${n}">№${n}</button>`).join(" ");
    html += `<article class="t-item ${COMP_CLASS[e.component] || ""}" data-id="${esc(e.id)}">
      <div class="t-dot"></div>
      <div class="t-body">
        <div class="t-hd">
          <span class="ctag">${esc(e.component)}</span>${e.version ? `<span class="ver">${esc(e.version)}</span>` : ""}
          <b class="t-title">${mdInline(e.title)}</b>
          <button type="button" class="linkbtn t-edit" data-edit="${esc(e.id)}">змінити</button>
        </div>
        ${e.what ? `<p>${mdInline(e.what)}</p>` : ""}
        ${e.why ? `<p class="why"><span>Чому:</span> ${mdInline(e.why)}</p>` : ""}
        ${e.result ? `<p class="res"><span>Результат:</span> ${mdInline(e.result)}</p>` : ""}
        ${trades ? `<div class="t-trades">Угоди: ${trades}</div>` : ""}
      </div>
    </article>`;
  }
  $("#hList").innerHTML = html;
}

$("#hComp").addEventListener("click", e => {
  const b = e.target.closest(".chip");
  if (!b) return;
  HS.comp = b.dataset.c;
  renderHistory();
});
$("#hSearch").addEventListener("input", e => { HS.q = e.target.value; renderHistory(); });

// ---------------------------------------------------------------- перехід до угоди
$("#hList").addEventListener("click", async e => {
  const t = e.target.closest("[data-trade]");
  if (t) {
    const n = +t.dataset.trade;
    openTab("journal");
    await loadJournal();
    if (JS.data && JS.data.trades.some(x => x.n === n)) openDrawer("edit", n);
    else jMsg(`Угоди №${n} у журналі немає.`, "warn");
    return;
  }
  const ed = e.target.closest("[data-edit]");
  if (ed) openForm(ed.dataset.edit);
});

// ---------------------------------------------------------------- форма
const HF = () => $("#hForm");
function openForm(id) {
  const f = HF(), e = id ? HS.data.entries.find(x => x.id === id) : null;
  HS.editId = e ? e.id : null;
  f.elements.component.innerHTML = HS.data.components.map(c => `<option>${esc(c)}</option>`).join("");
  const v = e || { date: new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10),
    approx: false, component: "Додаток", version: "", title: "", what: "", why: "", result: "", trades: [] };
  ["date", "component", "version", "title", "what", "why", "result"].forEach(k => { f.elements[k].value = v[k] || ""; });
  f.elements.approx.checked = !!v.approx;
  f.elements.trades.value = (v.trades || []).join(", ");
  $("#hFormTitle").textContent = e ? "Змінити запис" : "Новий запис";
  $("#hDelete").hidden = !e;
  $("#hErr").classList.add("hidden");
  f.classList.remove("hidden");
  f.scrollIntoView({ behavior: "smooth", block: "start" });
  f.elements.title.focus();
}
function closeForm() { HF().classList.add("hidden"); HS.editId = null; }
$("#hAdd").addEventListener("click", () => openForm(null));
HF().addEventListener("click", e => { if (e.target.closest("[data-hclose]")) closeForm(); });

async function saveEntries(entries, okText) {
  const r = await api("/api/changelog", { entries, base_mtime: HS.data.mtime });
  if (!r.ok) {
    $("#hErr").innerHTML = (r.errors || [r.message]).map(esc).join("<br>");
    $("#hErr").classList.remove("hidden");
    return false;
  }
  HS.data = r;
  closeForm();
  renderHistory();
  hMsg(okText + (r.extra_error ? ` <b class="warnc">Друга папка: ${esc(r.extra_error)}</b>` : ""), r.extra_error ? "warn" : "ok");
  return true;
}
HF().addEventListener("submit", async ev => {
  ev.preventDefault();
  const f = HF();
  const rec = {
    id: HS.editId || "", date: f.elements.date.value, approx: f.elements.approx.checked, component: f.elements.component.value,
    version: f.elements.version.value, title: f.elements.title.value, what: f.elements.what.value, why: f.elements.why.value,
    result: f.elements.result.value, trades: f.elements.trades.value,
  };
  const list = HS.data.entries.slice();
  if (HS.editId) list[list.findIndex(x => x.id === HS.editId)] = rec;
  else list.push(rec);
  await saveEntries(list, HS.editId ? "Запис оновлено ✓" : "Запис додано ✓");
});
$("#hDelete").addEventListener("click", async () => {
  const e = HS.data.entries.find(x => x.id === HS.editId);
  if (!e || !confirm(`Видалити запис «${e.title}»?\nПопередня версія історії збережеться в data/changelog_history.`)) return;
  await saveEntries(HS.data.entries.filter(x => x.id !== HS.editId), "Запис видалено.");
});

if ($("#tab-history").classList.contains("active")) loadHistory();
