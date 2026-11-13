/* Rulebound — вкладка «Стратегія»: документ у Markdown, редагування, історія версій. Використовує $, $$, esc, api з app.js */
"use strict";

const ST = { data: null, editing: false, viewing: null };

// ---------------------------------------------------------------- маленький безпечний Markdown
function mdInline(s) {
  // спершу екрануємо HTML, потім дозволяємо лише жирний, курсив, код і посилання http(s)
  let t = esc(s);
  t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
  t = t.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  t = t.replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>");
  t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (m, txt, url) => `<a href="${url}" target="_blank" rel="noopener">${txt}</a>`);
  return t;
}
function slug(s, used) {
  let b = s.toLowerCase().replace(/<[^>]+>/g, "").replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "") || "r";
  let k = b, i = 2;
  while (used.has(k)) k = `${b}-${i++}`;
  used.add(k);
  return k;
}
function renderMd(src) {
  const lines = (src || "").replace(/\r\n/g, "\n").split("\n");
  const out = [], toc = [], used = new Set();
  let para = [], stack = [];  // stack: [{type: "ul"|"ol", indent}]
  const flushPara = () => { if (para.length) { out.push(`<p>${mdInline(para.join(" "))}</p>`); para = []; } };
  const closeLists = (toIndent) => {
    while (stack.length && stack[stack.length - 1].indent >= toIndent) out.push(`</li></${stack.pop().type}>`);
  };
  for (const raw of lines) {
    const line = raw.replace(/\t/g, "  ");
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    const li = line.match(/^(\s*)([-*]|\d+[.)])\s+(.*)$/);
    if (!line.trim()) { flushPara(); continue; }
    if (h) {
      flushPara(); closeLists(0);
      const lvl = h[1].length, text = h[2].trim(), id = slug(text, used);
      out.push(`<h${lvl} id="st-${id}">${mdInline(text)}</h${lvl}>`);
      if (lvl >= 2 && lvl <= 3) toc.push({ lvl, id, text });
      continue;
    }
    if (li) {
      flushPara();
      const indent = li[1].length, type = /\d/.test(li[2]) ? "ol" : "ul";
      const top = stack[stack.length - 1];
      if (!top || indent > top.indent) {
        out.push(`<${type}><li>`);
        stack.push({ type, indent });
      } else {
        closeLists(indent + 1);
        const cur = stack[stack.length - 1];
        if (cur && cur.indent === indent && cur.type !== type) { out.push(`</li></${stack.pop().type}><${type}><li>`); stack.push({ type, indent }); }
        else if (cur) out.push("</li><li>");
        else { out.push(`<${type}><li>`); stack.push({ type, indent }); }
      }
      out.push(mdInline(li[3]));
      continue;
    }
    if (stack.length && /^\s+/.test(line)) { out.push(" " + mdInline(line.trim())); continue; }  // продовження пункту
    closeLists(0);
    para.push(line.trim());
  }
  flushPara(); closeLists(0);
  return { html: out.join("\n"), toc };
}

// ---------------------------------------------------------------- завантаження й показ
async function loadStrategy() {
  if (ST.editing) return;
  ST.data = await api("/api/strategy");
  ST.viewing = null;
  renderStrategy(ST.data.text, false);
}
window.loadStrategy = loadStrategy;

function stMsg(text, kind) {
  const m = $("#stMsg");
  m.innerHTML = text;
  m.className = "msg" + (kind === "err" ? " err" : kind === "warn" ? " warn" : "");
  clearTimeout(stMsg.t);
  if (kind === "ok") stMsg.t = setTimeout(() => m.classList.add("hidden"), 6000);
}

function renderStrategy(text, isOld) {
  const d = ST.data;
  const r = renderMd(text);
  $("#stView").innerHTML = (isOld ? `<div class="note-box">Це стара версія <b>${esc(ST.viewing)}</b>.
      <button type="button" class="linkbtn" id="stBack">Повернутись до поточної</button> ·
      <button type="button" class="linkbtn" id="stRestore">Відновити цю версію</button></div>` : "")
    + (r.html || '<p class="muted">Документ порожній. Натисни «Редагувати», щоб описати стратегію.</p>');
  $("#stToc").innerHTML = r.toc.length ? "<b>Зміст</b>" + r.toc.map(t =>
    `<a href="#st-${t.id}" class="toc-${t.lvl}">${mdInline(t.text).replace(/<\/?[bi]>/g, "")}</a>`).join("") : "";
  $("#stTitle").textContent = d.title || "Стратегія";
  const upd = d.updated_at ? d.updated_at.slice(8, 10) + "." + d.updated_at.slice(5, 7) + "." + d.updated_at.slice(0, 4) + " " + d.updated_at.slice(11, 16) : "";
  $("#stMeta").textContent = (upd ? `оновлено ${upd}` : "") + (d.versions.length ? ` · версій в історії: ${d.versions.length}` : "");
}
$("#stToc").addEventListener("click", e => {
  const a = e.target.closest("a[href^='#st-']");
  if (!a) return;
  e.preventDefault();
  const el = document.getElementById(a.getAttribute("href").slice(1));
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
});

// ---------------------------------------------------------------- редагування
function setEditing(on) {
  ST.editing = on;
  $("#stEditor").classList.toggle("hidden", !on);
  $("#stView").classList.toggle("hidden", on);
  $("#stToc").classList.toggle("hidden", on);
  $("#stEdit").hidden = on; $("#stHistBtn").hidden = on;
  $("#stSave").hidden = !on; $("#stCancel").hidden = !on;
  $("#stHist").classList.add("hidden");
}
$("#stEdit").addEventListener("click", () => {
  $("#stText").value = ST.data.text;
  setEditing(true);
  $("#stText").focus();
});
$("#stCancel").addEventListener("click", () => {
  if ($("#stText").value !== ST.data.text && !confirm("Скасувати зміни в документі?")) return;
  setEditing(false);
  renderStrategy(ST.data.text, false);
});
async function saveStrategy(text) {
  const r = await api("/api/strategy", { text, base_mtime: ST.data.mtime });
  if (!r.ok) { stMsg(esc(r.error || r.message || "Не вдалося зберегти"), "err"); return false; }
  ST.data = r;
  stMsg(r.unchanged ? "Змін немає." : "Збережено ✓ Попередню версію додано в «Історію версій»."
    + (r.extra_error ? ` <b class="warnc">Друга папка: ${esc(r.extra_error)}</b>` : ""), r.extra_error ? "warn" : "ok");
  return true;
}
$("#stSave").addEventListener("click", async () => {
  if (await saveStrategy($("#stText").value)) { setEditing(false); ST.viewing = null; renderStrategy(ST.data.text, false); }
});
$("#stText").addEventListener("keydown", e => {
  if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); $("#stSave").click(); }
});

// ---------------------------------------------------------------- історія версій
$("#stHistBtn").addEventListener("click", () => {
  const box = $("#stHist");
  if (!box.classList.contains("hidden")) { box.classList.add("hidden"); return; }
  const v = ST.data.versions;
  box.innerHTML = v.length ? "<b>Попередні версії</b> <span class='muted small'>(кожне збереження зберігає те, що було до нього)</span><ul>" +
    v.map(x => `<li><button type="button" class="linkbtn" data-ver="${esc(x.name)}">${x.mtime.slice(8, 10)}.${x.mtime.slice(5, 7)}.${x.mtime.slice(0, 4)} ${x.mtime.slice(11, 16)}</button>
      <span class="muted">${esc(x.title)}</span></li>`).join("") + "</ul>"
    : "<span class='muted'>Історія порожня: попередні версії з'являться після першого збереження.</span>";
  box.classList.remove("hidden");
});
$("#stHist").addEventListener("click", async e => {
  const b = e.target.closest("[data-ver]");
  if (!b) return;
  const r = await api("/api/strategy/version?name=" + encodeURIComponent(b.dataset.ver));
  if (!r.text && r.text !== "") { stMsg("Версію не знайдено", "err"); return; }
  ST.viewing = r.name; ST.oldText = r.text;
  renderStrategy(r.text, true);
  $("#stHist").classList.add("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
});
$("#stView").addEventListener("click", async e => {
  if (e.target.id === "stBack") { ST.viewing = null; renderStrategy(ST.data.text, false); }
  if (e.target.id === "stRestore" && confirm(`Відновити версію ${ST.viewing}? Поточний текст піде в історію.`)) {
    if (await saveStrategy(ST.oldText)) { ST.viewing = null; renderStrategy(ST.data.text, false); }
  }
});

// не втратити незбережений текст
window.addEventListener("beforeunload", e => {
  if (ST.editing && ST.data && $("#stText").value !== ST.data.text) { e.preventDefault(); e.returnValue = ""; }
});
if ($("#tab-strategy").classList.contains("active")) loadStrategy();
