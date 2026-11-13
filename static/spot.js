/* Rulebound — вкладка «Спот»: «шансові» монети на споті (денні свічки). Використовує $, $$, esc, api (app.js). */
"use strict";

const SPS = { data: null, timer: null, kind: "all", err: "" };

async function loadSpot() {
  clearTimeout(SPS.timer);
  try { SPS.data = await api("/api/spot"); } catch (e) { return; }
  renderSpot();
  if (SPS.data.running && $("#tab-spot").classList.contains("active")) SPS.timer = setTimeout(loadSpot, 2000);
}
window.loadSpot = loadSpot;

const spNum = (x, d = 2) => x == null ? "–" : (+x).toFixed(d).replace(".", ",");
const spPx = x => x == null ? "–" : (+x).toPrecision(5).replace(/\.?0+$/, "").replace(".", ",");
const spUsd = x => x == null ? "–" : (x >= 1e6 ? spNum(x / 1e6, 1) + " млн $" : spNum(x / 1e3, 0) + " тис. $");
const spPct = x => x == null ? "–" : `<span class="${x > 0 ? "rpos" : x < 0 ? "rneg" : ""}">${x > 0 ? "+" : ""}${spNum(x, 1)}%</span>`;
const spDate = iso => iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}` : "";

function spMsg(text, kind) {
  const m = $("#spMsg");
  if (!text) { m.classList.add("hidden"); return; }
  m.innerHTML = text;
  m.className = "msg" + (kind === "err" ? " err" : kind === "warn" ? " warn" : "");
}

function renderSpot() {
  const d = SPS.data, p = d.progress || {}, lat = d.latest;
  $("#spScan").disabled = d.running;
  const prog = $("#spProg");
  prog.classList.toggle("hidden", !d.running);
  if (d.running) {
    const pct = p.total ? Math.round(p.done / p.total * 100) : 3;
    $("#spBarI").style.width = pct + "%";
    $("#spBarI").className = "run";
    $("#spProgTxt").innerHTML = `<b>сканую</b>${p.total ? ` · монет ${p.done} з ${p.total}` : ` · ${esc(p.phase || "")}`}${p.symbol ? ` · ${esc(p.symbol)}` : ""}`;
  }
  $("#spSt").innerHTML = lat ? `останній скан ${esc(lat.generated.replace("T", " ").slice(0, 16))} · перевірено монет ${lat.checked} · знайдено ${lat.found.length}`
    + (d.auto ? " · автоскан раз на добу" : "") : "ще не сканували — натисни «Сканувати» (≈1–3 хв)";
  if (SPS.err) spMsg(SPS.err, "err");
  else if (p.status === "error") spMsg("Скан не вдався: " + esc(p.error || ""), "err");
  else if (lat && lat.errors && lat.errors.length) spMsg(`Частину монет не перевірено (${lat.errors.length}): ` + lat.errors.slice(0, 3).map(esc).join("; "), "warn");
  else spMsg("");
  fillSpForm((lat && lat.params) || d.defaults);
  const all = lat ? lat.found : [];
  $("#spNAll").textContent = all.length || "";
  $("#spNAcc").textContent = all.filter(r => r.kind === "накопичення").length || "";
  $("#spNNew").textContent = all.filter(r => r.kind === "новий лістинг").length || "";
  const only = $("#spOnlyNew").checked;
  const rows = all.filter(r => (SPS.kind === "all" || r.kind === SPS.kind) && (!only || r.new_today));
  $("#spBody").innerHTML = rows.map(r => {
    const base = r.symbol.replace(/USDT$/, "");
    return `<tr${r.new_today ? ' class="sp-new"' : ""}>
      <td class="sym"><a href="https://www.tradingview.com/chart/?symbol=${encodeURIComponent("BINANCE:" + r.symbol)}" target="_blank" rel="noopener">${esc(base)}</a>
        <a class="muted small" href="https://www.binance.com/en/trade/${encodeURIComponent(base)}_USDT?type=spot" target="_blank" rel="noopener">Binance</a>
        <button class="linkbtn small" type="button" data-excl="${esc(base)}" title="Більше не показувати цю монету">приховати</button></td>
      <td><span class="tag ${r.kind === "накопичення" ? "green" : "amber"}">${esc(r.kind)}</span></td>
      <td class="sp-why">${esc(r.why)}</td>
      <td class="num">${spPx(r.price)}</td><td class="num nowrap">${spUsd(r.vol_24h)}</td>
      <td class="num">${spPct(r.chg_7d)}</td><td class="num">${r.from_high == null ? "–" : "−" + spNum(r.from_high, 0) + "%"}</td>
      <td class="nowrap">${spPx(r.base_low)} – ${spPx(r.base_high)}</td>
      <td class="nowrap">${r.new_today ? "<b>нова</b>" : esc(spDate(r.first_seen))}</td></tr>`;
  }).join("");
  $("#spEmpty").textContent = lat && !rows.length ? "Нічого не знайдено за цими критеріями — це нормально: такі ситуації трапляються нечасто." : "";
}

function fillSpForm(p) {
  const f = $("#spForm");
  if (!p || f.contains(document.activeElement)) return;
  Object.entries(p).forEach(([k, v]) => { if (f.elements[k]) f.elements[k].value = v; });
}

async function spScan(params) {
  SPS.err = "";
  $("#spScan").disabled = true;
  let r;
  try { r = await api("/api/spot/scan", params ? { params } : {}); } catch (e) { r = { error: "немає зв'язку з додатком" }; }
  if (!r.ok) SPS.err = "Не запустилось: " + esc(r.error || r.message || "невідома помилка");
  setTimeout(loadSpot, 300);
}

$("#spScan").addEventListener("click", () => spScan(null));
$("#spForm").addEventListener("submit", ev => {
  ev.preventDefault();
  const params = {};
  Array.from(ev.target.elements).forEach(el => { if (el.name) params[el.name] = el.name === "exclude" ? el.value : +el.value; });
  spScan(params);
});
$("#spReset").addEventListener("click", () => { const d = SPS.data && SPS.data.defaults; if (d) Object.entries(d).forEach(([k, v]) => { const el = $("#spForm").elements[k]; if (el) el.value = v; }); });
$("#spFilt").addEventListener("click", ev => {
  const b = ev.target.closest("[data-k]");
  if (!b) return;
  SPS.kind = b.dataset.k;
  $$("#spFilt .chip").forEach(x => x.classList.toggle("on", x === b));
  if (SPS.data) renderSpot();
});
$("#spOnlyNew").addEventListener("change", () => SPS.data && renderSpot());
$("#spBody").addEventListener("click", ev => {
  const b = ev.target.closest("[data-excl]");
  if (!b) return;
  const f = $("#spForm"), cur = f.elements.exclude.value.trim();
  f.elements.exclude.value = cur ? cur + ", " + b.dataset.excl : b.dataset.excl;
  b.closest("tr").remove();
  $("#spCritBox").open = true;
  spMsg(`${esc(b.dataset.excl)} додано у «Виключити». Натисни «Сканувати з цими критеріями», щоб зберегти.`);
});
