/* Rulebound — вкладка «Журнал» (етап 2). Використовує $, $$, esc, api, store з app.js */
"use strict";

const JS = {
  data: null, sheet: null, emptySec: null, listForm: null,
  mode: "signal", editN: null, signal: null, signals: [],
  rulesTouched: false, scrTouched: false, dirty: false, checkTimer: null, lastCheck: null, saving: false,
};

// ---------------------------------------------------------------- формат
const ua = x => (x === null || x === undefined || x === "") ? "" : String(parseFloat(Number(x).toPrecision(6))).replace(".", ",");
const ua2 = x => (x === null || x === undefined || x === "") ? "" : Number(x).toFixed(2).replace(".", ",");
const dmy = iso => iso ? iso.slice(8, 10) + "." + iso.slice(5, 7) + "." + iso.slice(0, 4) : "";
const todayIso = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 10); };
const numIn = s => { const v = String(s || "").trim().replace(/\s/g, "").replace(",", "."); return v === "" ? null : Number(v); };

function jMsg(text, kind) {
  const m = $("#jMsg");
  m.innerHTML = text;
  m.className = "msg" + (kind === "err" ? " err" : kind === "warn" ? " warn" : "");
  clearTimeout(jMsg.t);
  if (kind === "ok") jMsg.t = setTimeout(() => m.classList.add("hidden"), 9000);
}

// ---------------------------------------------------------------- список угод
// вибраний список спільний із «Підсумком» (smc_jlist)
const curListId = () => +(store("smc_jlist") || 0) || null;
async function loadJournal() {
  const id = curListId();
  const d = await api("/api/journal" + (id ? "?list=" + id : ""));
  JS.data = d;
  if (d.list) store("smc_jlist", String(d.list.id));
  renderJournal();
  if (d.sheet_sync) loadSheetStatus(false);
  if ($("#bkBox").open) loadBackups(); else loadBackupSummary();
  loadLinkSuggestions(!$("#lnkBox").classList.contains("hidden"));
}
window.loadJournal = loadJournal;

function renderJournal() {
  const d = JS.data;
  if (!d) return;
  renderLists();
  const ro = jReadonly(), empty = !!JS.emptySec;
  ["#jAddSignal", "#jAddManual"].forEach(x => { $(x).disabled = ro || empty; });
  const sheetList = !!(d.list && d.list.sheet);
  $("#jImport").classList.toggle("hidden", !sheetList || empty);
  $("#jImport2").classList.toggle("hidden", !sheetList);
  $("#jEmpty p").textContent = d.list && d.list.status === "active" ? "У цьому списку ще немає угод." : "У списку немає угод.";
  $("#jCsv").href = "/api/journal/export.csv" + (d.list ? "?list=" + d.list.id : "");
  if (empty) {
    $("#jStats").innerHTML = "";
    $("#jTbl").classList.add("hidden");
    $("#jEmpty").classList.add("hidden");
    return;
  }
  const s = d.summary;
  const rcls = x => x > 0 ? "pos" : x < 0 ? "neg" : "";
  $("#jStats").innerHTML = `
    <div class="stat"><span>Угод</span><b>${s.count}</b>${s.open ? `<i>відкритих ${s.open}</i>` : ""}</div>
    <div class="stat"><span>Накопичено</span><b class="${rcls(s.cum)}">${ua2(s.cum)}R</b></div>
    <div class="stat"><span>Просадка</span><b class="${s.dd > 0 ? "neg" : ""}">${ua2(s.dd)}R</b></div>
    <div class="stat"><span>Серія збитків</span><b>${s.streak}</b></div>
    <div class="stat"><span>Вінрейт</span><b>${s.winrate === null ? "–" : s.winrate + "%"}</b></div>`;
  const rows = d.trades.slice().reverse();
  $("#jBody").innerHTML = rows.map(t => {
    const res = t.result === null ? '<span class="muted">відкрита</span>' : `<b class="${rcls(t.result)}">${t.result > 0 ? "+" : ""}${ua2(t.result)}</b>`;
    const risk = t.risk && t.risk !== 1 ? ` <span class="tag" title="ризик угоди">${ua(t.risk)}R</span>` : "";
    const sync = t.sheet_status === "ok" ? '<span class="ok" title="Записано в Google Таблицю">✓</span>'
      : `<span class="warnc" title="${esc(t.sheet_error || "ще не записано в таблицю")}">!</span>`;
    const note = t.note || "";
    return `<tr data-n="${t.n}" class="${t.result === null ? "open" : ""}">
      <td class="num">${t.ln}</td><td>${dmy(t.date)}</td><td class="sym">${esc(t.instrument)}</td>
      <td class="dir-${esc(t.dir)}">${esc(t.dir)}</td>
      <td class="num">${ua(t.entry)}</td><td class="num">${ua(t.stop)}</td><td class="num">${ua(t.target)}</td>
      <td class="num ${t.rr_plan !== null && t.rr_plan < 3 ? "neg" : ""}">${ua2(t.rr_plan)}</td>
      <td>${esc(t.rules)}</td><td class="num">${res}${risk}</td>
      <td class="num">${ua2(t.cum)}</td><td class="num">${ua2(t.dd)}</td><td class="num">${t.streak === null ? "" : t.streak}</td>
      <td>${esc(t.zone_type)}</td><td>${esc(t.choch)}</td><td class="num">${ua2(t.mfe)}</td><td>${esc(t.by_screener)}</td>
      <td class="c">${sync}</td><td class="note" title="${esc(note)}">${esc(note.length > 90 ? note.slice(0, 90) + "…" : note)}</td>
    </tr>`;
  }).join("");
  $("#jTbl").classList.toggle("nosync", !d.sheet_sync);
  $("#jEmpty").classList.toggle("hidden", rows.length > 0);
  $("#jTbl").classList.toggle("hidden", rows.length === 0);
  $("#jFoot").innerHTML = `База: <code>${esc(d.db)}</code> · резервні копії (CSV і база щодня): <code>${esc(d.backup_dir)}</code>`;
  renderSheetLine();
}

// ---------------------------------------------------------------- списки журналу
const secKey = x => x.market + "|" + x.mode;
const jReadonly = () => !JS.emptySec && !!(JS.data && JS.data.list && JS.data.list.status !== "active");
const signR = x => (x > 0 ? "+" : "") + ua2(x) + "R";
const jlPeriod = l => l.first_date ? (l.first_date === l.last_date ? dmy(l.first_date) : `${dmy(l.first_date)}–${dmy(l.last_date)}`) : "";
function curSec() {
  const d = JS.data;
  return JS.emptySec || (d.list ? secKey(d.list) : null);
}
function renderLists() {
  const d = JS.data, sec = curSec();
  const lists = d.lists || [];
  $("#jSec").innerHTML = (d.sections || []).map(x => {
    const k = secKey(x), act = lists.find(l => secKey(l) === k && l.status === "active");
    return `<button type="button" class="chip${k === sec ? " on" : ""}" data-sec="${k}" role="tab" aria-selected="${k === sec}">${esc(x.label)}${act ? `<span>${act.count}</span>` : ""}</button>`;
  }).join("");
  const inSec = lists.filter(l => secKey(l) === sec);
  const active = inSec.find(l => l.status === "active");
  const arch = inSec.filter(l => l.status !== "active").reverse();
  const label = (d.sections || []).find(x => secKey(x) === sec);
  const secName = label ? label.label : "";
  if (JS.emptySec || !inSec.length) {
    $("#jListRow").classList.add("hidden");
    openListForm("first", secName);
    return;
  }
  $("#jListRow").classList.remove("hidden");
  const opt = l => `<option value="${l.id}"${d.list && l.id === d.list.id ? " selected" : ""}>${esc(l.name)} · угод ${l.count}${l.count ? " · " + signR(l.cum) : ""}${l.status !== "active" && jlPeriod(l) ? " · " + jlPeriod(l) : ""}</option>`;
  $("#jListSel").innerHTML = (active ? `<optgroup label="Поточний">${opt(active)}</optgroup>` : "")
    + (arch.length ? `<optgroup label="Архів (${arch.length})">${arch.map(opt).join("")}</optgroup>` : "");
  const ro = jReadonly();
  $("#jListTag").innerHTML = ro ? `<span class="tag arch" title="Завершено ${esc(dmy((d.list.archived_at || "").slice(0, 10)))}">архів · лише перегляд</span>`
    : '<span class="tag green">поточний</span>';
  $("#jRename").classList.toggle("hidden", false);
  $("#jFinish").classList.toggle("hidden", ro);
  $("#jUnarchive").classList.toggle("hidden", !ro);
  $("#jNewList").textContent = active ? "Завершити й почати новий" : "Почати новий список";
  $("#jNewList").classList.toggle("hidden", ro && !!active);
  if (JS.listForm === "first") closeListForm();
}
function openListForm(mode, secName) {
  const d = JS.data, l = d.list;
  JS.listForm = mode;
  $("#jListForm").classList.remove("hidden");
  $("#jListCancel").classList.toggle("hidden", mode === "first");
  const inp = $("#jListName");
  if (mode === "rename") {
    $("#jFormLbl").textContent = "Нова назва списку:";
    $("#jListOk").textContent = "Зберегти";
    inp.value = l ? l.name : "";
  } else {
    $("#jFormLbl").innerHTML = mode === "first" ? `У розділі <b>${esc(secName)}</b> ще немає списку. Назва першого:` : "Назва нового списку:";
    $("#jListOk").textContent = "Почати список";
    if (mode !== "first" || !inp.value) inp.value = "";
  }
  if (mode !== "first") setTimeout(() => inp.focus(), 30);
}
function closeListForm() {
  JS.listForm = null;
  $("#jListForm").classList.add("hidden");
}
async function listAction(body, okText) {
  const r = await api("/api/journal/lists", body);
  if (!r.ok) { jMsg("Не вдалося: " + esc(r.error || r.message), "err"); return null; }
  let t = okText;
  const ret = r.retired;
  if (ret) t += ret.how === "deleted" ? ` Порожній список «${esc(ret.name)}» видалено.` : ` Список «${esc(ret.name)}» перенесено в архів.`;
  if (r.backup_error) t += ` <b class="warnc">Резервна копія: ${esc(r.backup_error)}</b>`;
  jMsg(t, r.backup_error ? "warn" : "ok");
  return r;
}
$("#jSec").addEventListener("click", e => {
  const b = e.target.closest("[data-sec]");
  if (!b || b.dataset.sec === curSec()) return;
  const k = b.dataset.sec, lists = (JS.data.lists || []).filter(l => secKey(l) === k);
  const pick = lists.find(l => l.status === "active") || lists[lists.length - 1];
  closeListForm();
  if (pick) { JS.emptySec = null; store("smc_jlist", String(pick.id)); loadJournal(); }
  else { JS.emptySec = k; $("#jListName").value = ""; renderJournal(); }
});
$("#jListSel").addEventListener("change", e => {
  JS.emptySec = null; closeListForm();
  store("smc_jlist", e.target.value);
  loadJournal();
});
$("#jRename").addEventListener("click", () => openListForm("rename"));
$("#jNewList").addEventListener("click", () => openListForm("new"));
$("#jListCancel").addEventListener("click", closeListForm);
$("#jFinish").addEventListener("click", async () => {
  const l = JS.data.list;
  if (!l) return;
  const q = l.count ? `Завершити список «${l.name}» (угод: ${l.count})?\n\nВін піде в архів: угоди лишаться для перегляду в журналі й «Підсумку», змінювати їх не можна. Повернути список можна будь-коли.`
    : `Список «${l.name}» порожній — його буде видалено. Продовжити?`;
  if (!confirm(q)) return;
  const r = await listAction({ action: "archive", id: l.id }, "");
  if (!r) return;
  jMsg(r.how === "deleted" ? `Порожній список «${esc(l.name)}» видалено.` : `Список «${esc(l.name)}» завершено й перенесено в архів.`, "ok");
  if (r.how === "deleted") { JS.emptySec = secKey(l); store("smc_jlist", ""); }
  loadJournal();
});
$("#jUnarchive").addEventListener("click", async () => {
  const l = JS.data.list;
  const act = (JS.data.lists || []).find(x => secKey(x) === secKey(l) && x.status === "active");
  const q = `Повернути список «${l.name}» з архіву? Він знову стане поточним у розділі ${l.section}.`
    + (act ? `\n\nПоточний список «${act.name}» ${act.count ? "піде в архів" : "порожній — його буде видалено"}.` : "");
  if (!confirm(q)) return;
  if (await listAction({ action: "unarchive", id: l.id }, `Список «${esc(l.name)}» знову поточний.`)) loadJournal();
});
$("#jListForm").addEventListener("submit", async e => {
  e.preventDefault();
  const name = $("#jListName").value.trim(), mode = JS.listForm;
  if (!name) { $("#jListName").focus(); return; }
  const d = JS.data;
  if (mode === "rename") {
    if (await listAction({ action: "rename", id: d.list.id, name }, `Список перейменовано: «${esc(name)}».`)) { closeListForm(); loadJournal(); }
    return;
  }
  const [market, md] = curSec().split("|");
  const act = (d.lists || []).find(x => x.market === market && x.mode === md && x.status === "active");
  if (act && !confirm(`Почати новий список «${name}»?\n\n` + (act.count
      ? `Поточний «${act.name}» (угод: ${act.count}) буде завершено й перенесено в архів — угоди лишаться для перегляду, список можна повернути.`
      : `Поточний «${act.name}» порожній — його буде видалено.`))) return;
  const r = await listAction({ action: "create", name, market, mode: md }, `Почато новий список «${esc(name)}».`);
  if (!r) return;
  JS.emptySec = null; closeListForm(); $("#jListName").value = "";
  store("smc_jlist", String(r.id));
  loadJournal();
});

$("#jBody").addEventListener("click", e => {
  const tr = e.target.closest("tr[data-n]");
  if (tr) openDrawer("edit", +tr.dataset.n);
});

// ---------------------------------------------------------------- Google Таблиця
async function loadSheetStatus(force) {
  if (!JS.sheet || force) $("#jSheet").textContent = "Перевіряю зв'язок з Google Таблицею…";
  JS.sheet = await api("/api/journal/sheet" + (force ? "?force=1" : ""));
  renderSheetLine();
}
function renderSheetLine() {
  const st = JS.sheet, el = $("#jSheet");
  if (JS.data && JS.data.sheet_sync && JS.data.list && !JS.data.list.sheet) {
    el.innerHTML = '<span class="ok">✓</span> Список зберігається в додатку, з резервними копіями. У Google Таблицю пишеться лише список, '
      + 'що був до появи списків.';
    el.className = "sheetline";
    return;
  }
  if (JS.data && !JS.data.sheet_sync) {
    el.innerHTML = '<span class="ok">✓</span> Журнал зберігається в додатку, з резервними копіями (див. унизу). '
      + 'Запис у Google Таблицю вимкнено в «Налаштуваннях».';
    el.className = "sheetline";
    return;
  }
  if (!st || !JS.data) return;
  const pending = JS.data.summary.pending_sync;
  let html;
  if (!st.configured) html = `<span class="warnc">!</span> Запис у Google Таблицю вимкнено: ${esc(st.error)}. Угоди зберігаються лише в додатку.`;
  else if (!st.ok) html = `<span class="neg">✗</span> Google Таблиця: ${esc(st.error)} <button class="linkbtn" id="jRecheck" type="button">перевірити ще раз</button>`;
  else html = `<span class="ok">✓</span> Google Таблиця підключена (Code.gs v${st.version}): нові угоди й виправлення записуються в аркуш «Журнал».`
    + (st.latest && st.version < st.latest ? ` <b class="warnc">Онови Code.gs до v${st.latest}: у v${st.version} дата в таблиці може зсуватись на день назад (README → «Оновлення Code.gs»).</b>` : "");
  if (pending && st.configured) html += ` <b class="warnc">Не записано в таблицю: ${pending}.</b> <button class="btn small" id="jSync" type="button">Дописати</button>`;
  el.innerHTML = html;
  el.className = "sheetline";
}
$("#jSheet").addEventListener("click", async e => {
  if (e.target.id === "jRecheck") return loadSheetStatus(true);
  if (e.target.id === "jSync") {
    e.target.disabled = true; e.target.textContent = "Записую…";
    const r = await api("/api/journal/sync", {});
    jMsg(r.ok ? `Дописано в таблицю: ${r.synced.length ? "№" + r.synced.join(", №") : "нічого"} ✓` : `Не вдалося: ${esc(r.error)}`, r.ok ? "ok" : "err");
    loadJournal();
  }
});

async function doImport(btn) {
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = "Імпортую…";
  const r = await api("/api/journal/import", {});
  btn.disabled = false; btn.textContent = label;
  if (!r.ok) { jMsg("Імпорт не вдався: " + esc(r.error || r.message), "err"); return; }
  let t = `Імпорт з таблиці: нових ${r.added}, оновлено ${r.updated}.`;
  if (r.sheet_cum !== null && r.sheet_cum !== undefined)
    t += r.cum_match ? ` Накопичено ${ua2(r.app_cum)}R — збігається з таблицею ✓` : ` <b>Увага: накопичено в додатку ${ua2(r.app_cum)}R, у таблиці ${ua2(r.sheet_cum)}R.</b>`;
  if (r.local_only && r.local_only.length) t += `<br>Є лише в додатку (немає в таблиці): №${r.local_only.join(", №")} — натисни «Дописати».`;
  if (r.problems && r.problems.length) t += "<br>" + r.problems.map(esc).join("<br>");
  jMsg(t, (r.problems && r.problems.length) || !r.cum_match ? "warn" : "ok");
  loadJournal();
}
$("#jImport").addEventListener("click", e => doImport(e.currentTarget));
$("#jImport2").addEventListener("click", e => doImport(e.currentTarget));

// ---------------------------------------------------------------- форма
$("#jAddSignal").addEventListener("click", () => openDrawer("signal"));
$("#jAddManual").addEventListener("click", () => openDrawer("manual"));

const F = () => $("#tForm");
function setField(name, v) {
  const f = F();
  const els = f.querySelectorAll(`[name="${name}"]`);
  if (!els.length) return;
  if (els[0].type === "radio") els.forEach(r => { r.checked = String(r.value) === String(v); });
  else els[0].value = v === null || v === undefined ? "" : v;
}
function formTrade() {
  const f = F(), t = {};
  ["date", "instrument", "entry", "stop", "target", "result", "management", "mfe", "rules", "zone_type", "choch", "support4h", "by_screener", "note"]
    .forEach(k => { t[k] = f.elements[k].value; });
  const dir = f.querySelector('[name="dir"]:checked');
  const risk = f.querySelector('[name="risk"]:checked');
  t.dir = dir ? dir.value : "";
  t.risk = risk ? risk.value : "1";
  t.signal_id = JS.signal ? JS.signal.id : "";
  return t;
}

function openDrawer(mode, n) {
  JS.mode = mode; JS.editN = null; JS.signal = null;
  JS.rulesTouched = false; JS.scrTouched = false; JS.dirty = false; JS.lastCheck = null;
  F().reset();
  $("#exitPx").value = ""; $("#mfePx").value = "";
  $("#dErr").classList.add("hidden");
  $("#checks").innerHTML = "";
  $("#matchBanner").classList.add("hidden");
  $("#rrOut").textContent = "–";
  $("#dDelete").hidden = true;
  $$(".scr").forEach(x => { x.textContent = ""; });
  if (mode === "edit") {
    const t = JS.data.trades.find(x => x.n === n);
    if (!t) return;
    JS.editN = n;
    JS.rulesTouched = true; JS.scrTouched = true;  // у збереженій угоді нічого не підміняємо автоматично
    $("#dTitle").textContent = `Угода №${t.ln} · ${t.instrument} ${t.dir}` + (jReadonly() ? " · архів, лише перегляд" : "");
    ["date", "instrument", "dir", "management", "rules", "zone_type", "choch", "support4h", "by_screener", "note"].forEach(k => setField(k, t[k]));
    ["entry", "stop", "target", "result", "mfe"].forEach(k => setField(k, t[k] === null ? "" : ua(t[k])));
    setField("risk", t.risk === 0.5 ? "0.5" : "1");
    JS.signal = t.signal || null;
    const last = JS.data.trades[JS.data.trades.length - 1];
    $("#dDelete").hidden = jReadonly() || !last || n !== last.n;
    $("#sigBox").classList.toggle("hidden", !JS.signal);
    $("#sigList").classList.add("hidden");
    $(".sig-tools").classList.add("hidden");
    renderChosen();
    $("#dSave").textContent = "Зберегти зміни";
    $("#dSave").hidden = jReadonly();
  } else {
    $("#dSave").hidden = false;
    const ln = `«${JS.data.list.name}» (${JS.data.list.section})`;
    $("#dTitle").textContent = mode === "signal" ? `Нова угода №${JS.data.next_n} зі скринера · ${ln}` : `Нова угода №${JS.data.next_n} вручну · ${ln}`;
    setField("date", todayIso());
    setField("risk", "1");
    $("#sigBox").classList.toggle("hidden", mode !== "signal");
    $("#sigList").classList.remove("hidden");
    $(".sig-tools").classList.remove("hidden");
    renderChosen();
    $("#dSave").textContent = "Зберегти угоду";
    if (mode === "signal") loadSignals();
  }
  $("#drawer").classList.remove("hidden");
  document.body.classList.add("noscroll");
  scheduleCheck(0);
  setTimeout(() => {  // курсор у перше поле, лише якщо користувач ще нічого не почав вводити
    if (!$("#drawer").contains(document.activeElement)) (mode === "signal" ? $("#sigSearch") : F().elements.instrument).focus();
  }, 50);
}

function closeDrawer(force) {
  if (!force && JS.dirty && !confirm("Закрити без збереження?")) return;
  $("#drawer").classList.add("hidden");
  document.body.classList.remove("noscroll");
}
$("#drawer").addEventListener("click", e => { if (e.target.closest("[data-close]")) closeDrawer(false); });
document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#drawer").classList.contains("hidden")) closeDrawer(false); });

// ---------------------------------------------------------------- сигнали
async function loadSignals() {
  const days = $("#sigDays").value, rej = $("#sigRejected").checked ? 1 : 0;
  $("#sigList").innerHTML = '<div class="muted pad">Завантажую сигнали з логу…</div>';
  const lid = JS.data && JS.data.list ? JS.data.list.id : "";
  const r = await api(`/api/journal/signals?days=${days}&rejected=${rej}&list=${lid}`);
  JS.signals = r.signals || [];
  JS.signalsPath = r.path;
  JS.signalsExists = r.exists;
  renderSignals();
}
function sigWhen(s) {
  const f = s.first.slice(8, 10) + "." + s.first.slice(5, 7) + " " + s.first.slice(11, 16);
  const l = s.last.slice(8, 10) + "." + s.last.slice(5, 7) + " " + s.last.slice(11, 16);
  return f === l ? f : `${f} → ${l}`;
}
function renderSignals() {
  const q = $("#sigSearch").value.trim().toUpperCase();
  const list = JS.signals.filter(s => !q || s.symbol.includes(q));
  if (!JS.signalsExists) {
    $("#sigList").innerHTML = `<div class="muted pad">Немає логу сигналів <code>${esc(JS.signalsPath)}</code>. Перевір папку результатів у «Налаштуваннях».</div>`;
    return;
  }
  if (!list.length) {
    $("#sigList").innerHTML = '<div class="muted pad">Сигналів за цей період немає. Збільш період або додай угоду вручну.</div>';
    return;
  }
  $("#sigList").innerHTML = list.map(s => {
    const tags = [];
    if (s.touched_at) tags.push(`<span class="tag red">в зоні ${s.touched_at.slice(11, 16)}</span>`);
    if (s.rejected) tags.push('<span class="tag grey">відсіяно: проти 4h</span>');
    if (s.knife === "так") tags.push('<span class="tag amber">ніж</span>');
    const on = JS.signal && JS.signal.id === s.id ? " on" : "";
    return `<button type="button" class="sig${on}" data-id="${esc(s.id)}">
      <span class="s1"><b>${esc(s.base)}</b> <span class="dir-${esc(s.dir)}">${esc(s.dir)}</span> ${tags.join(" ")}</span>
      <span class="s2">зона ${ua(s.zone_low)}–${ua(s.zone_high)} · вхід ${ua(s.entry)} · стоп ${ua(s.stop)} · ціль ${ua(s.target)} · <b>R:R ${ua2(s.rr)}</b></span>
      <span class="s3">${sigWhen(s)} · сканів у списку: ${s.scans} · бали ${esc(s.score)}</span>
    </button>`;
  }).join("");
}
$("#sigSearch").addEventListener("input", renderSignals);
$("#sigDays").addEventListener("change", loadSignals);
$("#sigRejected").addEventListener("change", loadSignals);
$("#sigList").addEventListener("click", e => {
  const b = e.target.closest(".sig");
  if (!b) return;
  const s = JS.signals.find(x => x.id === b.dataset.id);
  if (s) pickSignal(s, true);
});

function pickSignal(s, fill) {
  JS.signal = s;
  $("#matchBanner").classList.add("hidden");
  $("#sigBox").classList.remove("hidden");
  if (JS.mode !== "signal") { $("#sigList").classList.add("hidden"); $(".sig-tools").classList.add("hidden"); }
  if (fill) {
    setField("instrument", s.base);
    setField("dir", s.dir);
    setField("entry", ua(s.entry));
    setField("stop", ua(s.stop));
    setField("target", ua(s.target));
    if (!F().elements.zone_type.value) setField("zone_type", "4h OB");
  }
  JS.dirty = true;
  renderChosen();
  renderSignals();
  scheduleCheck(0);
}
function renderChosen() {
  const s = JS.signal, box = $("#sigChosen");
  $$(".scr").forEach(x => { x.textContent = s && s[x.dataset.for] != null ? `скринер: ${ua(s[x.dataset.for])}` : ""; });
  if (!s) { box.classList.add("hidden"); box.innerHTML = ""; autoScreener(); return; }
  box.classList.remove("hidden");
  const w = s.warnings ? `<div class="s3 warnc">${esc(s.warnings)}</div>` : "";
  box.innerHTML = `<div><b>Прив'язано:</b> ${esc(s.symbol)} <span class="dir-${esc(s.dir)}">${esc(s.dir)}</span>
      · зона ${ua(s.zone_low)}–${ua(s.zone_high)} (середина ${ua((s.zone_low + s.zone_high) / 2)})
      · рівні скринера: вхід ${ua(s.entry)} / стоп ${ua(s.stop)} / ціль ${ua(s.target)} · R:R ${ua2(s.rr)}
      <button type="button" class="linkbtn" id="sigUnlink">відв'язати</button></div>
    <div class="s3">${esc(s.status || "")} · структура 4h: ${esc(s.struct4h || "–")} · BOS ${esc(s.bos_time || "–")} · у логу ${sigWhen(s)}</div>${w}`;
  autoScreener();
}
$("#sigChosen").addEventListener("click", e => {
  if (e.target.id === "sigUnlink") {
    JS.signal = null; JS.dirty = true;
    if (JS.mode !== "signal") $("#sigBox").classList.add("hidden");
    renderChosen(); renderSignals(); scheduleCheck(0);
  }
});

// «По скринеру» і «За правилами» підставляються самі, доки ти їх не змінив руками
function autoScreener() {
  if (!JS.scrTouched) setField("by_screener", JS.signal ? "так" : (JS.editN ? F().elements.by_screener.value : "ні"));
  $("#scrAuto").textContent = JS.scrTouched ? "" : "(авто)";
}
F().addEventListener("input", e => {
  JS.dirty = true;
  if (e.target.name === "rules") JS.rulesTouched = true;
  if (e.target.name === "by_screener") JS.scrTouched = true;
  scheduleCheck(250);
});
F().addEventListener("change", e => {
  if (e.target.name === "rules") JS.rulesTouched = true;
  if (e.target.name === "by_screener") JS.scrTouched = true;
  scheduleCheck(0);
});

// ---------------------------------------------------------------- перевірка
function scheduleCheck(ms) {
  clearTimeout(JS.checkTimer);
  JS.checkTimer = setTimeout(runCheck, ms);
}
async function runCheck() {
  const t = formTrade();
  const e = numIn(t.entry), s = numIn(t.stop), g = numIn(t.target);
  $("#rrOut").textContent = (e && s && g && e !== s) ? ua2(Math.abs(g - e) / Math.abs(e - s)) : "–";
  const seq = (JS.checkSeq = (JS.checkSeq || 0) + 1);
  const r = await api("/api/journal/check", { trade: t, list_id: JS.data && JS.data.list ? JS.data.list.id : null });
  if (seq !== JS.checkSeq) return;  // поки чекали, поля вже змінились — ця відповідь застаріла
  JS.lastCheck = r;
  const icon = { error: "✗", warn: "!", info: "i", ok: "✓" };
  let html = (r.items || []).map(i => `<li class="ck-${i.level}"><span>${icon[i.level]}</span>${esc(i.text)}</li>`).join("");
  $("#checks").innerHTML = html;
  const mb = $("#matchBanner");
  if (!JS.signal && r.matches && r.matches.length) {
    mb.innerHTML = `<div class="mb-hd">Схоже на сигнал скринера — прив'язати?</div>` + r.matches.map((m, k) => `<div class="mb-row">
        <div>${sigLine(m)}</div>
        <button type="button" class="btn small primary" data-match="${k}">Прив'язати</button></div>`).join("")
      + `<div class="muted small">Прив'язка зберігає рівні скринера для порівняння в «Підсумку». «По скринеру?» постав сам, як вважаєш.</div>`;
    mb.classList.remove("hidden");
  } else mb.classList.add("hidden");
  const sug = r.rules_suggest;
  if (!JS.rulesTouched && sug) setField("rules", sug);
  $("#rulesAuto").textContent = sug ? (JS.rulesTouched ? `перевірка радить: ${sug}` : `(авто: ${sug})`) : "";
  $("#rulesAuto").title = (r.rules_reasons || []).join("; ");
}
$("#matchBanner").addEventListener("click", e => {
  const b = e.target.closest("[data-match]");
  if (b && JS.lastCheck) pickSignal(JS.lastCheck.matches[+b.dataset.match], false);
});

// ---------------------------------------------------------------- збереження / видалення
$("#dSave").addEventListener("click", async () => {
  if (JS.saving) return;
  const t = formTrade();
  const err = $("#dErr");
  err.classList.add("hidden");
  if (JS.mode === "signal" && !JS.signal && !confirm("Сигнал скринера не вибрано. Зберегти як ручну угоду?")) return;
  JS.saving = true;
  const btn = $("#dSave"), label = btn.textContent;
  btn.disabled = true; btn.textContent = "Зберігаю…";
  const body = { trade: t, list_id: JS.data.list ? JS.data.list.id : null };
  if (JS.editN) body.n = JS.editN;
  const r = await api("/api/journal/save", body);
  JS.saving = false; btn.disabled = false; btn.textContent = label;
  if (!r.ok) {
    err.innerHTML = (r.errors || [r.message]).map(esc).join("<br>");
    err.classList.remove("hidden");
    return;
  }
  closeDrawer(true);
  const what = JS.editN ? `Угоду №${r.ln} оновлено` : `Угоду №${r.ln} збережено`;
  if (r.sync.disabled) jMsg(`${what} ✓` + (r.backup_error ? ` <b class="warnc">Резервна копія: ${esc(r.backup_error)}</b>` : ""), r.backup_error ? "warn" : "ok");
  else if (r.sync.ok) jMsg(`${what} і записано в Google Таблицю ✓`, "ok");
  else if (!r.sync.configured) jMsg(`${what} у додатку. ${esc(r.sync.error)}.`, "warn");
  else jMsg(`${what} у додатку, але в таблицю <b>не записано</b>: ${esc(r.sync.error)}. Кнопка «Дописати» повторить спробу.`, "err");
  loadJournal();
});

$("#dDelete").addEventListener("click", async () => {
  const t = JS.data.trades.find(x => x.n === JS.editN);
  if (!t) return;
  const sheetNote = JS.data.sheet_sync ? "\nРядок у Google Таблиці теж буде очищено (формули лишаться)." : "\nПеред видаленням стан збережено в резервній копії.";
  if (!confirm(`Видалити угоду №${t.ln} (${t.instrument} ${t.dir})?${sheetNote}`)) return;
  let r = await api("/api/journal/delete", { n: t.n });
  if (!r.ok && r.can_local && confirm(`${r.error}\n\nВидалити лише в додатку?`)) r = await api("/api/journal/delete", { n: t.n, local_only: true });
  if (!r.ok) { $("#dErr").textContent = r.error || "Не вдалося видалити"; $("#dErr").classList.remove("hidden"); return; }
  closeDrawer(true);
  jMsg(`Угоду №${t.ln} видалено.`, "ok");
  loadJournal();
});

// вкладка могла бути відкрита ще до завантаження цього файла (запам'ятована з минулого разу)
if ($("#tab-journal").classList.contains("active")) loadJournal();

// ---------------------------------------------------------------- калькулятор результату і MFE
// Результат у R = рух ціни / відстань до стопа × ризик (0,5R -> стоп = −0,5R).
// MFE у R = найкращий рух ціни після входу / відстань до стопа (без множення на ризик, як у журналі: №17 TRUMP MFE ≈2R).
function levels() {
  const t = formTrade();
  const e = numIn(t.entry), s = numIn(t.stop), g = numIn(t.target), risk = numIn(t.risk) || 1;
  if (!e || !s || e === s || (t.dir !== "Long" && t.dir !== "Short")) return null;
  return { e, s, g, risk, sign: t.dir === "Long" ? 1 : -1, dist: Math.abs(e - s) };
}
function setNum(name, v) {
  setField(name, ua2(Math.round(v * 100) / 100));
  JS.dirty = true;
  scheduleCheck(0);
}
function needLevels() {
  $("#dErr").textContent = "Спершу заповни напрямок, вхід і стоп.";
  $("#dErr").classList.remove("hidden");
}
$("#tForm").addEventListener("click", e => {
  const b = e.target.closest("[data-res]");
  if (!b) return;
  const L = levels();
  if (!L) return needLevels();
  $("#dErr").classList.add("hidden");
  if (b.dataset.res === "stop") setNum("result", -L.risk);
  else if (b.dataset.res === "be") setNum("result", 0);
  else if (L.g) setNum("result", Math.abs(L.g - L.e) / L.dist * L.risk);
  $("#exitPx").value = "";
});
$("#exitPx").addEventListener("input", e => {
  const px = numIn(e.target.value), L = levels();
  if (!px) return;
  if (!L) return needLevels();
  setNum("result", L.sign * (px - L.e) / L.dist * L.risk);
});
$("#mfePx").addEventListener("input", e => {
  const px = numIn(e.target.value), L = levels();
  if (!px) return;
  if (!L) return needLevels();
  setNum("mfe", Math.max(0, L.sign * (px - L.e) / L.dist));
});

// ---------------------------------------------------------------- резервні копії
const KIND = { latest: "актуальна", daily: "щоденна (початок дня)", manual: "ручна", before_restore: "перед відновленням" };
const kb = n => n < 1024 ? n + " Б" : (n / 1024).toFixed(0) + " КБ";
const when = iso => iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)} ${iso.slice(11, 16)}` : "";
async function loadBackupSummary() {
  const r = await api("/api/journal/backups");
  JS.bk = r;
  const db = (r.backups || []).filter(b => b.type === "db" && b.kind !== "latest");
  $("#bkSum").textContent = `· копій: ${db.length}` + (r.extra_dir ? " · + друга папка" : "");
}
async function loadBackups() {
  await loadBackupSummary();
  const r = JS.bk;
  $("#bkWhere").innerHTML = `Папка: <code>${esc(r.dir)}</code>` + (r.extra_dir ? ` і <code>${esc(r.extra_dir)}</code>` : " · другу папку (напр. iCloud Drive) можна задати в «Налаштуваннях»");
  $("#bkBody").innerHTML = (r.backups || []).map(b => `<tr>
      <td><b>${esc(KIND[b.kind] || b.kind)}</b> <span class="muted small">${esc(b.name)}</span></td>
      <td>${when(b.mtime)}</td>
      <td class="num">${b.trades == null ? "" : b.trades}</td>
      <td class="num">${b.cum == null ? "" : ua2(b.cum) + "R"}</td>
      <td class="num">${kb(b.size)}</td>
      <td class="acts"><a href="/api/journal/backup_file?name=${encodeURIComponent(b.name)}">завантажити</a>
        ${b.type === "db" && b.kind !== "latest" ? `<button type="button" class="linkbtn" data-restore="${esc(b.name)}">відновити</button>` : ""}</td>
    </tr>`).join("") || '<tr><td colspan="6" class="muted">Копій ще немає: вони з\'являться після першої зміни в журналі.</td></tr>';
}
$("#bkBox").addEventListener("toggle", () => { if ($("#bkBox").open) loadBackups(); });
$("#bkNow").addEventListener("click", async e => {
  e.target.disabled = true;
  const r = await api("/api/journal/backup", {});
  e.target.disabled = false;
  jMsg(r.ok ? `Копію збережено: ${esc(r.name)}` + (r.extra_error ? ` <b class="warnc">${esc(r.extra_error)}</b>` : "") : "Не вдалося: " + esc(r.error), r.ok && !r.extra_error ? "ok" : "warn");
  loadBackups();
});
$("#bkBody").addEventListener("click", async e => {
  const b = e.target.closest("[data-restore]");
  if (!b) return;
  const item = (JS.bk.backups || []).find(x => x.name === b.dataset.restore);
  const now = JS.data.summary;
  if (!confirm(`Відновити журнал з копії ${item.name}?\n\nУ копії: угод ${item.trades}, накопичено ${ua2(item.cum)}R.\nЗараз: угод ${now.count}, накопичено ${ua2(now.cum)}R.\n\nПоточний стан спершу буде збережено (копія «перед відновленням»).`)) return;
  const r = await api("/api/journal/restore", { name: item.name });
  jMsg(r.ok ? `Журнал відновлено з ${esc(r.restored)}: угод ${r.trades}. Попередній стан збережено як ${esc(r.safety)}.` : "Не вдалося: " + esc(r.error), r.ok ? "ok" : "err");
  loadJournal();
});

// ---------------------------------------------------------------- сигнали для угод без прив'язки
function sigLine(m) {
  const tags = [];
  if (m.rejected) tags.push('<span class="tag grey">скринер відсіяв: проти структури 4h</span>');
  if (m.touched_at) tags.push(`<span class="tag red">ціна заходила в зону ${m.touched_at.slice(8, 10)}.${m.touched_at.slice(5, 7)} ${m.touched_at.slice(11, 16)}</span>`);
  return `<b>${esc(m.symbol)}</b> <span class="dir-${esc(m.dir)}">${esc(m.dir)}</span> · зона ${ua(m.zone_low)}–${ua(m.zone_high)}
    · скринер: вхід ${ua(m.entry)} / стоп ${ua(m.stop)} / ціль ${ua(m.target)} · R:R ${ua2(m.rr)}
    <span class="muted">· у логу ${sigWhen(m)}</span> ${tags.join(" ")}`;
}
async function loadLinkSuggestions(render) {
  const r = await api("/api/journal/link_suggestions");
  JS.links = r.suggestions || [];
  const c = $("#jLinkCount");
  c.textContent = JS.links.length;
  c.classList.toggle("hidden", !JS.links.length);
  if (render) renderLinks();
}
function renderLinks() {
  const box = $("#lnkBox");
  if (!JS.links.length) {
    box.innerHTML = '<div class="lnk-hd"><b>Сигнали для угод</b><button type="button" class="x" data-lclose>×</button></div>'
      + '<p class="muted">Немає угод без прив\'язки, для яких у логу скринера є схожий сигнал.</p>';
    return;
  }
  box.innerHTML = `<div class="lnk-hd"><b>Сигнали для угод</b> <span class="muted small">— угоди без прив'язки, для яких у логу є сигнал
      з тією ж монетою й напрямом, вхід біля зони, дати поруч. Перевір і підтверди кожну.</span>
      <button type="button" class="btn small" data-lclear>Очистити список</button>
      <button type="button" class="x" data-lclose>×</button></div>`
    + JS.links.map(t => `<div class="lnk-item">
      <div class="lnk-tr"><span class="muted">${esc(t.section)} · «${esc(t.list_name)}»</span> <b>№${t.ln}</b> ${dmy(t.date)} · <b>${esc(t.instrument)}</b> <span class="dir-${esc(t.dir)}">${esc(t.dir)}</span>
        · вхід ${ua(t.entry)} / стоп ${ua(t.stop)} / ціль ${ua(t.target)}
        · ${t.result === null ? "відкрита" : (t.result > 0 ? "+" : "") + ua2(t.result) + "R"}
        · «По скринеру»: ${esc(t.by_screener || "–")}</div>
      ${t.matches.map(m => `<div class="lnk-m"><div>↳ ${sigLine(m)}</div><div class="lnk-act">
          <button type="button" class="btn small primary" data-link="${t.n}" data-sid="${esc(m.id)}" data-scr="так">Прив'язати + «По скринеру» = так</button>
          <button type="button" class="btn small" data-link="${t.n}" data-sid="${esc(m.id)}">Лише прив'язати</button>
          <button type="button" class="btn small" data-dismiss="${t.n}" data-sid="${esc(m.id)}">Не те</button></div></div>`).join("")}
    </div>`).join("");
}
$("#jLinkBtn").addEventListener("click", async () => {
  const box = $("#lnkBox");
  if (!box.classList.contains("hidden")) { box.classList.add("hidden"); return; }
  box.innerHTML = '<p class="muted">Шукаю в логу скринера…</p>';
  box.classList.remove("hidden");
  await loadLinkSuggestions(true);
});
$("#lnkBox").addEventListener("click", async e => {
  if (e.target.closest("[data-lclose]")) { $("#lnkBox").classList.add("hidden"); return; }
  if (e.target.closest("[data-lclear]")) {
    if (!confirm(`Прибрати всі ${JS.links.length} пропозиції зі списку?\n\nУгоди й поле «По скринеру» не змінюються — просто ці сигнали більше не пропонуватимуться. Для нових угод пошук працюватиме як раніше.`)) return;
    const r = await api("/api/journal/link_clear", {});
    jMsg(r.ok ? `Список очищено (${r.cleared}). Нові угоди перевірятимуться як раніше.` : "Не вдалося: " + esc(r.error || r.message), r.ok ? "ok" : "err");
    await loadJournal();
    return;
  }
  const b = e.target.closest("[data-link],[data-dismiss]");
  if (!b) return;
  b.disabled = true;
  const n = +(b.dataset.link || b.dataset.dismiss);
  const body = { n, signal_id: b.dataset.sid };
  if (b.dataset.dismiss) body.dismiss = true;
  if (b.dataset.scr) body.by_screener = b.dataset.scr;
  const r = await api("/api/journal/link", body);
  if (!r.ok) { jMsg("Не вдалося: " + esc(r.error || r.message), "err"); b.disabled = false; return; }
  const lt = (JS.links || []).find(x => x.n === n), nm = lt ? `«${esc(lt.list_name)}» №${lt.ln}` : "Угода";
  jMsg(body.dismiss ? `${nm}: позначено «не той сигнал», більше не пропоную.` :
    `${nm}: сигнал ${esc(r.signal.symbol)} прив'язано` + (body.by_screener ? ", «По скринеру» = так" : "") + " ✓", "ok");
  await loadJournal();
});
