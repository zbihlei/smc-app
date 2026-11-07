// Перевірка apps_script/Code.gs у node з імітацією SpreadsheetApp (без Google).
// Запуск: node tests/test_code_gs.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function colNum(letters) { return letters.split('').reduce((a, ch) => a * 26 + ch.charCodeAt(0) - 64, 0); }

class Sheet {
  constructor(name) { this.name = name; this.cells = {}; }
  key(r, c) { return r + ':' + c; }
  get(r, c) { return this.cells[this.key(r, c)] || { v: '', f: '' }; }
  put(r, c, cell) { this.cells[this.key(r, c)] = cell; }
  getName() { return this.name; }
  getLastRow() {
    let m = 0;
    for (const k of Object.keys(this.cells)) { const c = this.cells[k]; if (c.v !== '' || c.f) m = Math.max(m, +k.split(':')[0]); }
    return m;
  }
  getRange(r, c, nr, nc) {
    if (typeof r === 'string') { const m = r.match(/^([A-Z]+)(\d+)$/); return new Range(this, +m[2], colNum(m[1]), 1, 1); }
    return new Range(this, r, c, nr || 1, nc || 1);
  }
}
class Range {
  constructor(sh, r, c, nr, nc) { Object.assign(this, { sh, r, c, nr, nc }); }
  getValues() { const out = []; for (let i = 0; i < this.nr; i++) { const row = []; for (let j = 0; j < this.nc; j++) row.push(this.sh.get(this.r + i, this.c + j).v); out.push(row); } return out; }
  getValue() { return this.sh.get(this.r, this.c).v; }
  setValues(vals) {
    if (vals.length !== this.nr || vals[0].length !== this.nc) throw new Error('setValues: розмір не збігається');
    vals.forEach((row, i) => row.forEach((v, j) => this.sh.put(this.r + i, this.c + j, { v, f: '' })));
    return this;
  }
  setValue(v) { this.sh.put(this.r, this.c, { v, f: '' }); return this; }
  getFormula() { return this.sh.get(this.r, this.c).f; }
  setNumberFormat() { return this; }
  clearContent() { for (let i = 0; i < this.nr; i++) for (let j = 0; j < this.nc; j++) this.sh.put(this.r + i, this.c + j, { v: '', f: '' }); return this; }
  copyTo(dst) {
    const d = dst.r - this.r;
    const f = this.getFormula().replace(/(\$?)([A-Z]+)(\$?)(\d+)/g, (m, a, col, b, row) => a + col + b + (b ? row : +row + d));
    dst.sh.put(dst.r, dst.c, { v: '(формула)', f });
  }
}

function makeEnv() {
  const journal = new Sheet('Журнал');
  const screener = new Sheet('Скринер');
  // угоди №1..3 у рядках 3..5; №4..6 — порожні рядки з номером і формулами (як у справжній таблиці до рядка 28)
  const data = [
    [1, new Date(2026, 9, 17), 'Zcash', 'Short', 1.231, 1.259, 1.128, 'так', 3.73],
    [2, new Date(2026, 9, 18), 'Cardano', 'Long', 0.223, 0.2175, 0.2361, 'ні', -2.2],
    [3, new Date(2026, 9, 18), 'Solana', 'Long', 102, 101, 106, 'ні', -1.1],
  ];
  data.forEach((t, i) => {
    const r = 3 + i;
    [t[0], t[1], t[2], t[3], t[4], t[5], t[6]].forEach((v, j) => journal.put(r, j + 1, { v, f: '' }));
    journal.put(r, 9, { v: t[7], f: '' });
    journal.put(r, 10, { v: t[8], f: '' });
  });
  for (let r = 4; r <= 8; r++) {
    if (r > 5) journal.put(r, 1, { v: r - 2, f: '' });
    journal.put(r, 8, { v: '', f: `=IF(OR(E${r}="";F${r}="";G${r}="");"";ROUND(ABS(G${r}-E${r})/ABS(E${r}-F${r});2))` });
    journal.put(r, 14, { v: '', f: `=IF(J${r}="";"";SUM(J$3:J${r}))` });
  }
  const sheets = { 'Журнал': journal, 'Скринер': screener };
  const ss = { getSheetByName: n => sheets[n] || null, getSpreadsheetTimeZone: () => 'Europe/Kiev' };
  const ctx = {
    SpreadsheetApp: { openById: () => ss },
    LockService: { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) },
    ContentService: { createTextOutput: s => ({ s, setMimeType() { return JSON.parse(this.s); } }), MimeType: { JSON: 'json' } },
    Utilities: { formatDate: (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` },
    JSON, String, Number, Math, Date, Error,
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'apps_script', 'Code.gs'), 'utf8'), ctx);
  const post = body => ctx.doPost({ postData: { contents: JSON.stringify(Object.assign({ token: '5abc21c30dc2c4b39032ddbf' }, body)) } });
  return { ctx, post, journal, screener };
}

let ok = true;
const check = (name, cond, info) => { console.log((cond ? 'OK   ' : 'FAIL ') + name + (cond || info === undefined ? '' : '  ' + JSON.stringify(info))); ok = ok && !!cond; };

const { ctx, post, journal, screener } = makeEnv();
check('GET повертає версію 3', ctx.doGet().version === 3);
check('чужий токен відхиляється', post({ token: 'x' }).error === 'bad token');

let r = post({ stamp: '12:00', rows: [[1, 'X'], [2, 'Y']] });
check('скринер без action: як раніше пише в «Скринер»', r.ok && r.n === 2 && screener.get(4, 2).v === 'X' && screener.get(1, 4).v === 'Оновлено: 12:00', r);
check('скринер не чіпає «Журнал»', journal.get(3, 3).v === 'Zcash');

r = post({ action: 'listTrades' });
check('listTrades: 3 угоди, дати у форматі yyyy-MM-dd', r.ok && r.trades.length === 3 && r.trades[0].values[1] === '2026-10-17' && r.trades[0].row === 3, r);

const t4 = { n: 4, date: '2026-11-04', instrument: 'DOGE', dir: 'Short', entry: 0.07533, stop: 0.07577, target: 0.07399, rules: 'так',
  result: null, management: 'Без змін', choch: 'ні', note: 'тест', zone_type: '4h OB', support4h: '', mfe: null, by_screener: 'так' };
r = post({ action: 'saveTrade', trade: t4 });
check('saveTrade №4 у рядок 6', r.ok && r.row === 6 && journal.get(6, 3).v === 'DOGE' && journal.get(6, 2).v instanceof Date, r);
check('порожній результат записано як порожню клітинку', journal.get(6, 10).v === '');
check('дата угоди на полудень (без зсуву на день через часові пояси)', journal.get(6, 2).v.getHours() === 12 && journal.get(6, 2).v.getDate() === 4);
check('формули H і N у рядку 6 не зачеплено', journal.get(6, 8).f.includes('G6') && journal.get(6, 14).f.includes('J6'));
check('номер у колонці A лишився', journal.get(6, 1).v === 4);
r = post({ action: 'saveTrade', trade: t4 });
check('повторна спроба тієї самої угоди (той самий інструмент) проходить', r.ok && r.row === 6, r);
r = post({ action: 'saveTrade', trade: Object.assign({}, t4, { instrument: 'SUI' }) });
check('інша угода в зайнятий рядок без overwrite -> «зайнято»', !r.ok && r.code === 'occupied', r);
r = post({ action: 'saveTrade', trade: Object.assign({}, t4, { result: 3.05, overwrite: true }) });
check('виправлення своєї угоди (overwrite) працює', r.ok && journal.get(6, 10).v === 3.05, r);
r = post({ action: 'saveTrade', trade: Object.assign({}, t4, { n: 6, instrument: 'SUI' }) });
check('пропуск номера (№5 порожня) -> помилка gap', !r.ok && r.code === 'gap', r);

r = post({ action: 'saveTrade', trade: Object.assign({}, t4, { n: 5, instrument: 'SUI' }) });
check('№5 у рядок 7', r.ok && r.row === 7);
r = post({ action: 'saveTrade', trade: Object.assign({}, t4, { n: 6, instrument: 'PENG' }) });
check('№6 у рядок 8', r.ok && r.row === 8);
r = post({ action: 'saveTrade', trade: Object.assign({}, t4, { n: 7, instrument: 'ONDO' }) });
check('№7: рядок 9 без формул -> формули скопійовано з рядка 8 зі зсувом', r.ok && journal.get(9, 8).f.includes('G9') && journal.get(9, 14).f === '=IF(J9="";"";SUM(J$3:J9))', journal.get(9, 14));
check('№7: номер A9 = 7 (його не було)', journal.get(9, 1).v === 7);

r = post({ action: 'clearTrade', n: 6, instrument: 'PENG' });
check('видалити не останню угоду не можна', !r.ok, r);
r = post({ action: 'clearTrade', n: 7, instrument: 'SUI' });
check('видалення з чужим інструментом відхиляється', !r.ok, r);
r = post({ action: 'clearTrade', n: 7, instrument: 'ONDO' });
check('видалення останньої угоди: значення очищено, формули й номер лишились',
  r.ok && journal.get(9, 3).v === '' && journal.get(9, 2).v === '' && journal.get(9, 1).v === 7 && journal.get(9, 14).f !== '', r);
r = post({ action: 'listTrades' });
check('listTrades після змін: 6 угод', r.trades.length === 6, r.trades.length);
check('невідома дія -> помилка', post({ action: 'zzz' }).ok === false);

console.log('\nПІДСУМОК: ' + (ok ? 'усі перевірки пройдено' : 'Є ПОМИЛКИ'));
process.exit(ok ? 0 : 1);
