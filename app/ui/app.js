import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import { HELP, SYNONYMS, SMALLTALK, STOP } from './help.js';

// ------------------------------------------------------------------ helpers
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids) if (k != null) e.append(k);
  return e;
};
const clone = (o) => JSON.parse(JSON.stringify(o));
// what the page remembers (last presets, filament slots, open tabs, cameras…). Kept by the app in
// ui_state.json: the window's own storage starts empty every time (new local address on each start).
const UI_STATE = await fetch('/api/ui-state').then((r) => r.json()).catch(() => ({}));
let uiTimer = 0;
const store = {
  get(k, d) {
    if (k in UI_STATE) return clone(UI_STATE[k]);
    try { const v = localStorage.getItem('np.' + k); return v == null ? d : JSON.parse(v); } catch { return d; }
  },
  set(k, v) {
    UI_STATE[k] = v === undefined ? null : clone(v);
    clearTimeout(uiTimer);
    uiTimer = setTimeout(() => fetch('/api/ui-state', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(UI_STATE) }).catch(() => { }), 400);
  },
  flush() {                                  // the window is closing: send what is still waiting
    if (!uiTimer) return;
    clearTimeout(uiTimer); uiTimer = 0;
    try { navigator.sendBeacon('/api/ui-state', new Blob([JSON.stringify(UI_STATE)], { type: 'application/json' })); } catch { }
  },
};
window.addEventListener('pagehide', () => store.flush());
async function api(path, body) {
  const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch('/api/' + path, opt);
  const j = await r.json();
  if (!r.ok || j.error) throw new Error(j.error || r.statusText);
  return j;
}
async function upload(path, file, headers = {}) {
  const r = await fetch('/api/' + path, { method: 'POST', headers: { 'X-Filename': encodeURIComponent(file.name), ...headers }, body: file });
  const j = await r.json();
  if (!r.ok || j.error) throw new Error(j.error || r.statusText);
  return j;
}
let toastTimer;
function toast(msg, err = false) {
  const t = $('#toast');
  t.textContent = msg; t.className = 'show' + (err ? ' err' : '');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.className = ''), err ? Math.max(6000, msg.length * 70) : 3000);
}
const b64 = (s, T) => { const b = Uint8Array.from(atob(s), (c) => c.charCodeAt(0)); return new T(b.buffer); };
const LIVE = { on: false, layer: 0, layerAt: 0, raf: 0, saved: null, name: '', layers: 0 };   // live view (see liveStart)
const fmtTime = (s) => { s = Math.round(s); const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60); return h ? `${h}h ${m}m` : `${m}m`; };

// ------------------------------------------------------------------ state
const S = {
  schema: null, percent: new Set(), presets: null,
  sel: { printer: null, filament: null, process: null },
  work: { printer: {}, filament: {}, process: {} },
  model: null, placement: { x: 128, y: 128, scale: 100, rot: 0, orient: [1, 0, 0, 0, 1, 0, 0, 0, 1] }, paint: null,
  processTab: store.get('processTab', 'Quality'),
  advanced: store.get('advanced', false),
  preview: null, sliced: false, view: 'prepare',
  // AMS: filament slots [{preset, color}]; S.work.filament is the working copy of the ACTIVE slot's preset
  slots: [], active: 0,
  parts: [], partTools: [], selPart: -1,   // model parts and the filament slot (0-based) of each
  toolChanges: [],                          // [{layer, tool}]: from that layer up, print with that slot
  colorMode: store.get('colorMode', 'kind'),
  // project file: where Save writes (only a 3MF this app saved or opened as a project), unsaved changes
  projectPath: null, dirty: false, hasWindow: false,
  calib: null,                              // calibration print on the plate: {test, name, steps, result, ...}
};
const LABELS = { printer: 'Printer', filament: 'Filament', process: 'Process' };
const MAX_SLOTS = 4;   // AMS lite

// ------------------------------------------------------------------ colour palette
const PALETTE = [
  ['White', '#ffffff'], ['Ivory', '#f4efe1'], ['Beige', '#e5d3b3'], ['Light grey', '#c8c8c8'], ['Silver', '#a6a9aa'],
  ['Grey', '#8e9089'], ['Dark grey', '#545454'], ['Black', '#161616'],
  ['Red', '#c12e1f'], ['Crimson', '#8e1b26'], ['Terracotta', '#c4623f'], ['Orange', '#f57a1f'], ['Peach', '#ffb38a'],
  ['Gold', '#c9a64b'], ['Yellow', '#f4d53f'], ['Olive', '#7a7a35'],
  ['Lime', '#a3d134'], ['Green', '#2e9e4f'], ['Dark green', '#1f5133'], ['Mint', '#8fd9b6'], ['Teal', '#00897b'],
  ['Cyan', '#20b2c8'], ['Sky', '#a7d8f0'], ['Light blue', '#7fb6e8'],
  ['Blue', '#1f57c3'], ['Navy', '#1c2a55'], ['Lavender', '#b39ddb'], ['Purple', '#6b3fa0'], ['Magenta', '#d3359b'],
  ['Pink', '#f59ac1'], ['Brown', '#6e4b33'], ['Copper', '#b87333'],
];
const normHex = (c) => { const m = /^#?([0-9a-f]{6})$/i.exec(String(c || '').trim()); return m ? '#' + m[1].toLowerCase() : null; };
function contrast(hex) {
  const c = normHex(hex) || '#cccccc';
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.55 ? '#111' : '#fff';
}
let paletteAnchor = null;
function closePalette() { $('#palette').classList.add('hidden'); paletteAnchor = null; }
function openPalette(anchor, current, onPick) {
  const box = $('#palette');
  paletteAnchor = anchor;
  box.innerHTML = '';
  const cur = normHex(current);
  const name = el('div', { class: 'name' }, PALETTE.find((p) => p[1] === cur)?.[0] || cur || '');
  const pick = (c, close = true) => {
    c = normHex(c); if (!c) return;
    onPick(c);
    if (close) {
      store.set('recentColors', [c, ...store.get('recentColors', []).filter((x) => x !== c)].slice(0, 8));
      closePalette();
    }
  };
  const swatches = (list) => {
    const g = el('div', { class: 'grid' });
    for (const [n, c] of list) g.append(el('button', { class: c === cur ? 'on' : '', style: `background:${c}`, title: n,
      onmouseenter: () => (name.textContent = n), onclick: () => pick(c) }));
    return g;
  };
  box.append(el('h5', {}, 'Colours'), swatches(PALETTE));
  const recent = store.get('recentColors', []);
  if (recent.length) box.append(el('h5', {}, 'Recent'), swatches(recent.map((c) => [c, c])));
  const hex = el('input', { type: 'text', value: cur || '', maxlength: 7, placeholder: '#rrggbb',
    onkeydown: (e) => { if (e.key === 'Enter') pick(e.target.value); } });
  const native = el('input', { type: 'color', value: cur || '#cccccc', title: 'Custom colour',
    oninput: (e) => { hex.value = e.target.value; pick(e.target.value, false); },
    onchange: (e) => pick(e.target.value) });
  box.append(el('div', { class: 'row' }, hex, el('button', { onclick: () => pick(hex.value) }, 'OK'), native), name);
  box.classList.remove('hidden');
  const r = anchor.getBoundingClientRect();
  const w = box.offsetWidth, h = box.offsetHeight;
  box.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left)) + 'px';
  box.style.top = (r.bottom + h + 8 > innerHeight ? Math.max(8, r.top - h - 6) : r.bottom + 6) + 'px';
}
document.addEventListener('mousedown', (e) => {
  if (!$('#palette').classList.contains('hidden') && !e.target.closest('#palette') && e.target !== paletteAnchor) closePalette();
  if (!$('#menu').classList.contains('hidden') && !e.target.closest('#menu')) $('#menu').classList.add('hidden');
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closePalette(); $('#menu').classList.add('hidden'); $('#calib-modal').classList.add('hidden'); } });
function openMenu(anchor, items, opts = {}) {
  const m = $('#menu');
  m.innerHTML = '';
  for (const it of items) {
    if (it.sep) { m.append(el('div', { class: 'sep' })); continue; }
    if (it.heading) { m.append(el('h6', {}, it.heading)); continue; }
    m.append(el('button', { class: it.cls || '', title: it.title, disabled: !!it.disabled,
      onclick: () => { m.classList.add('hidden'); it.onclick(); } },
      ...it.content, it.key ? el('span', { class: 'k' }, it.key) : null));
  }
  m.classList.remove('hidden');
  const r = anchor.getBoundingClientRect();
  const x = opts.left ? r.left : r.right - m.offsetWidth;
  m.style.left = Math.max(8, Math.min(innerWidth - m.offsetWidth - 8, x)) + 'px';
  m.style.top = Math.min(innerHeight - m.offsetHeight - 8, r.bottom + 4) + 'px';
}
const swatchEl = (i, cls = 'swatch sm') => el('span', { class: cls, style: `background:${slotColor(i)};color:${contrast(slotColor(i))}` }, String(i + 1));

// ------------------------------------------------------------------ filament slots (AMS)
function slotColor(i) { return (S.slots[i] && S.slots[i].color) || '#cccccc'; }
// printer / filament lists: your own presets first, then Bambu Lab, Generic, the other brands A-Z
function groupedOptions(sel, cat, selected, label) {
  const P = S.presets[cat];
  const own = cat === 'printer' ? 'My printers' : 'My filaments';
  const rank = (n) => { const v = P[n].vendor || ''; return !P[n]._builtin ? '0' : v === 'Bambu Lab' ? '1' : v === 'Generic' ? '2' : v === 'Custom' ? '4' : '3' + v.toLowerCase(); };
  const groups = {};
  const vis = S.visible && S.visible[cat];
  const shows = (n) => !vis || !P[n]._builtin || n === selected || vis.includes(n);
  for (const n of Object.keys(P).sort((a, b) => rank(a).localeCompare(rank(b)))) {
    if (!shows(n)) continue;
    const k = P[n]._builtin ? (P[n].vendor || 'Other') : own;
    (groups[k] ||= el('optgroup', { label: k })).append(el('option', { value: n, selected: n === selected }, label(n)));
  }
  for (const g of Object.values(groups)) sel.append(g);
}
// filaments hidden in Printers & filaments (☰) are replaced in the slots by the first filament you kept
function useVisibleFilaments() {
  const vis = S.visible && S.visible.filament, P = S.presets.filament;
  if (!vis || !vis.length) return;
  const keep = vis.filter((n) => P[n]);
  if (!keep.length) return;
  let changed = false;
  for (const sl of S.slots) {
    if (P[sl.preset] && P[sl.preset]._builtin && !vis.includes(sl.preset)) {
      sl.preset = keep[0]; sl.color = P[keep[0]].color || sl.color; changed = true;
    }
  }
  if (changed) {
    S.active = Math.min(S.active, S.slots.length - 1);
    S.sel.filament = S.slots[S.active].preset;
    S.work.filament = clone(P[S.sel.filament]);
  }
}
function renderSlots() {
  const box = $('#filament-slots');
  box.innerHTML = '';
  S.slots.forEach((sl, i) => {
    const sel = el('select', { title: 'Filament preset', onchange: (e) => changeSlotPreset(i, e.target.value), onclick: (e) => e.stopPropagation() });
    groupedOptions(sel, 'filament', sl.preset, (name) => name + (i === S.active && name === sl.preset && isDirty('filament') ? ' (modified)' : ''));
    const sw = el('button', { class: 'swatch', style: `background:${sl.color};color:${contrast(sl.color)}`, title: 'Filament colour',
      onclick: (e) => { e.stopPropagation(); openPalette(e.currentTarget, sl.color, (c) => setSlotColor(i, c)); } }, String(i + 1));
    const rm = S.slots.length > 1 ? el('button', { class: 'icon', title: 'Remove this filament',
      onclick: (e) => { e.stopPropagation(); removeSlot(i); } }, '✕') : null;
    box.append(el('div', { class: 'filament-row' + (i === S.active && S.slots.length > 1 ? ' active' : ''),
      title: S.slots.length > 1 ? 'Click to select; ⚙ edits the selected filament' : '', onclick: () => activateSlot(i) }, sw, sel, rm));
  });
  $('#filament-add').disabled = S.slots.length >= MAX_SLOTS;
  store.set('slots', S.slots);
}
function activateSlot(i) {
  if (i === S.active) return;
  if (isDirty('filament') && !confirm(`Discard unsaved changes to “${S.sel.filament}”?`)) return;
  S.active = i;
  S.sel.filament = S.slots[i].preset;
  S.work.filament = clone(S.presets.filament[S.sel.filament]);
  store.set('sel', S.sel);
  renderSlots();
  if (!$('#modal').classList.contains('hidden') && modal.cat === 'filament') renderModal();
}
function changeSlotPreset(i, name) {
  if (i === S.active && isDirty('filament') && !confirm(`Discard unsaved changes to “${S.sel.filament}”?`)) return renderSlots();
  S.slots[i].preset = name;
  S.slots[i].color = S.presets.filament[name].color || S.slots[i].color;
  if (i === S.active) { S.sel.filament = name; S.work.filament = clone(S.presets.filament[name]); store.set('sel', S.sel); }
  renderSlots(); updateModelColors(); renderObjects(); markStale();
}
function setSlotColor(i, c) {
  S.slots[i].color = c;
  renderSlots(); updateModelColors(); renderObjects(); markStale();
}
function addSlot() {
  if (S.slots.length >= MAX_SLOTS) return toast(`The AMS lite has ${MAX_SLOTS} slots`, true);
  const used = new Set(S.slots.map((s) => s.color));
  const color = ['#c12e1f', '#1f57c3', '#f4d53f', '#2e9e4f', '#161616', '#ffffff'].find((c) => !used.has(c)) || '#8e9089';
  S.slots.push({ preset: S.slots[S.slots.length - 1].preset, color });
  renderSlots(); renderObjects();
  toast(`Added filament ${S.slots.length}. Pick its preset and colour, then assign it to a part or layer.`);
}
function removeSlot(i) {
  const fix = (t) => (t === i ? 0 : t > i ? t - 1 : t);
  S.partTools = S.partTools.map(fix);
  S.toolChanges = S.toolChanges.map((c) => ({ ...c, tool: fix(c.tool) }));
  S.slots.splice(i, 1);
  if (S.active >= S.slots.length || S.active === i) {
    S.active = Math.min(S.active, S.slots.length - 1);
    S.sel.filament = S.slots[S.active].preset;
    S.work.filament = clone(S.presets.filament[S.sel.filament]);
  } else if (S.active > i) S.active -= 1;
  renderSlots(); updateModelColors(); renderObjects(); markStale();
}
function matchPreset(tray) {
  const fil = S.presets.filament, names = Object.keys(fil);
  const type = (tray.type || '').toUpperCase();
  return names.find((n) => tray.name && n.toLowerCase() === ('bambu ' + tray.name).toLowerCase())
    || names.find((n) => (fil[n].type || '').toUpperCase() === type && /bambu/i.test(n))
    || names.find((n) => (fil[n].type || '').toUpperCase() === type)
    || S.slots[0]?.preset || names[0];
}
function syncFromAms(trays) {
  const t = (trays || []).filter((x) => x.unit === 0).sort((a, b) => a.tray - b.tray).slice(0, MAX_SLOTS);
  const last = t.reduce((m, x, k) => (x.type ? k : m), -1);
  if (last < 0) return toast('The printer reports no filament in the AMS lite', true);
  if (isDirty('filament') && !confirm(`Discard unsaved changes to “${S.sel.filament}”?`)) return;
  S.slots = t.slice(0, last + 1).map((x) => ({ preset: matchPreset(x), color: normHex(x.color) || '#cccccc' }));
  const n = S.slots.length;
  S.partTools = S.partTools.map((v) => Math.min(v, n - 1));
  S.toolChanges = S.toolChanges.filter((c) => c.tool < n);
  S.active = 0; S.sel.filament = S.slots[0].preset; S.work.filament = clone(S.presets.filament[S.sel.filament]);
  store.set('sel', S.sel);
  renderSlots(); updateModelColors(); renderObjects(); markStale();
  const empty = t.slice(0, last + 1).filter((x) => !x.type).length;
  toast(`Loaded ${n} filament${n > 1 ? 's' : ''} from the AMS lite` + (empty ? ` (${empty} empty slot${empty > 1 ? 's' : ''})` : ''));
}

// ------------------------------------------------------------------ presets
function selectPreset(cat, name) {
  S.sel[cat] = name;
  S.work[cat] = clone(S.presets[cat][name]);
  store.set('sel', S.sel);
  refreshSelects();
  if (cat === 'process') renderProcess();
  if (cat === 'printer') { buildBed(); updateDeviceHeader(); ensureCompatibleProcess(); }
  if (cat === 'filament') {
    const sl = S.slots[S.active];
    if (sl.preset !== name) sl.color = S.presets.filament[name].color || sl.color;
    sl.preset = name;
    renderSlots(); updateModelColors(); renderObjects();
  }
  updateModel();
  markStale();
}
function isDirty(cat) {
  const base = S.presets[cat][S.sel[cat]] || {};
  return Object.keys(S.work[cat]).some((k) => !k.startsWith('_') && JSON.stringify(S.work[cat][k]) !== JSON.stringify(base[k]));
}
const nozzle = () => +S.work.printer.nozzle_diameter || 0.4;
const fitsNozzle = (name) => Math.abs((+(S.presets.process[name] || {}).compatible_nozzle || 0.4) - nozzle()) < 1e-6;
// 0.7.3: process presets lost "@A1" ("0.20mm Standard @A1" -> "0.20mm Standard", "... @A1 0.6 nozzle" -> "... @0.6 nozzle")
const newProcessName = (n) => String(n).replace(/ @A1(?: (\d\.\d+) nozzle)?$/, (m, nz) => (nz ? ` @${+nz} nozzle` : ''));
function refreshSelects() {
  if (S.slots.length) renderSlots();
  for (const cat of ['printer', 'process']) {
    const sel = $(`#${cat}-select`);
    sel.innerHTML = '';
    if (cat === 'printer') {
      groupedOptions(sel, 'printer', S.sel.printer, (name) => name + (name === S.sel.printer && isDirty('printer') ? ' (modified)' : ''));
      $('#btn-print').textContent = isBambu() ? 'Print plate' : 'Save G-code';
      $('#btn-print').title = isBambu() ? 'Send to the printer (Device tab)' : 'Save the G-code, then print it from USB / SD or your printer’s web page';
      continue;
    }
    for (const name of Object.keys(S.presets[cat])) {
      // Orca shows only the process presets made for the printer's nozzle size
      const fits = fitsNozzle(name);
      if (!fits && name !== S.sel[cat]) continue;
      const dirty = name === S.sel[cat] && isDirty(cat);
      sel.append(el('option', { value: name, selected: name === S.sel[cat] }, name + (dirty ? ' (modified)' : '') +
        (fits ? '' : ` ⚠ for ${S.presets.process[name].compatible_nozzle} mm nozzle`)));
    }
  }
}
// after the nozzle size changes: switch to the matching process preset (Standard quality if there is one)
function ensureCompatibleProcess() {
  if (fitsNozzle(S.sel.process)) return;
  if (isDirty('process')) return refreshSelects();      // keep the user's unsaved edits; the ⚠ shows the mismatch
  const names = Object.keys(S.presets.process).filter(fitsNozzle);
  if (!names.length) return refreshSelects();
  const lh = +S.work.process.layer_height;
  const pick = names.find((n) => /Standard/.test(n) && S.presets.process[n]._builtin) || names[0];
  selectPreset('process', pick);
  toast(`Process switched to “${pick}” for the ${nozzle()} mm nozzle` + (lh ? ` (was ${lh} mm layers)` : ''));
}
async function reloadPresets() { S.presets = await api('profiles'); }
async function savePreset(cat, asNew = false) {
  let name = S.sel[cat];
  if (asNew) {
    name = prompt(`Name for the new ${LABELS[cat].toLowerCase()} preset:`, name + ' copy');
    if (!name) return false;
  }
  const r = await api('profiles/save', { category: cat, name, data: S.work[cat] });
  await reloadPresets();
  S.sel[cat] = r.name;
  S.work[cat] = clone(S.presets[cat][r.name]);
  if (cat === 'filament') S.slots[S.active].preset = r.name;
  store.set('sel', S.sel);
  refreshSelects();
  if (cat === 'process') renderProcess();
  toast(`Saved ${LABELS[cat].toLowerCase()} preset “${r.name}”`);
  return true;
}
// Reset: undo unsaved changes (the ↺ arrows, the Reset buttons) or restore a built-in preset's factory values
function afterWorkChange(cat) {
  refreshSelects();
  if (cat === 'process') renderProcess();
  if (cat === 'printer') { buildBed(); updateDeviceHeader(); }
  if (!$('#modal').classList.contains('hidden') && modal.cat === cat) renderModal();
  updateModel();
  markStale();
}
function resetWork(cat) {
  if (!isDirty(cat)) return toast('No unsaved changes');
  S.work[cat] = clone(S.presets[cat][S.sel[cat]]);
  afterWorkChange(cat);
  toast(`Unsaved changes to “${S.sel[cat]}” were reset`);
}
async function restoreDefaults(cat) {
  const name = S.sel[cat];
  if (!S.presets[cat][name]._builtin) return toast('Only built-in presets have factory defaults. Your own presets can be reset to their last save.', true);
  if (!confirm(`Restore “${name}” to its factory settings?\n\nAll saved changes to this preset are replaced.` +
    (cat === 'printer' ? ' Your connection details and Bambu template are kept.' : ''))) return;
  await api('profiles/reset', { category: cat, name });
  await reloadPresets();
  S.work[cat] = clone(S.presets[cat][name]);
  afterWorkChange(cat);
  toast(`“${name}” is back to its factory settings`);
}
function resetField(cat, f, rerender) {
  const base = S.presets[cat][S.sel[cat]] || {};
  S.work[cat][f.key] = base[f.key] === undefined ? undefined : clone(base[f.key]);
  if (cat === 'filament' && f.key === 'color' && base.color) setSlotColor(S.active, base.color);
  refreshSelects();
  markStale();
  if (cat === 'printer') buildBed();
  if (rerender) rerender();
  updateModel();
}
async function deletePreset(cat) {
  const name = S.sel[cat];
  if (S.presets[cat][name]._builtin) return toast('Built-in presets can’t be deleted (you can edit and save them)', true);
  if (!confirm(`Delete the ${LABELS[cat].toLowerCase()} preset “${name}”? This can’t be undone.`)) return;
  await api('profiles/delete', { category: cat, name });
  await reloadPresets();
  selectPreset(cat, Object.keys(S.presets[cat])[0]);
  toast(`Deleted “${name}”`);
}

// ------------------------------------------------------------------ forms
function fieldValue(cat, f) {
  const v = S.work[cat][f.key];
  return S.percent.has(f.key) ? Math.round((v ?? 0) * 1000) / 10 : v;
}
function setField(cat, f, raw, rerender) {
  let v = raw;
  if (f.type === 'number' || f.type === 'percent') {
    v = parseFloat(raw);
    if (Number.isNaN(v)) return;
    if (S.percent.has(f.key)) v = v / 100;
  } else if (f.type === 'select' && f.options && typeof optValue(f.options[0]) === 'number') v = parseFloat(raw);
  S.work[cat][f.key] = v;
  refreshSelects();
  markStale();
  if (cat === 'printer') buildBed();
  if (cat === 'printer' && f.key === 'nozzle_diameter') ensureCompatibleProcess();
  if (cat === 'filament' && f.key === 'color') setSlotColor(S.active, v);
  if (rerender) rerender();
  updateModel();
}
const optValue = (o) => (Array.isArray(o) ? o[0] : o);
const optLabel = (o) => String(Array.isArray(o) ? o[1] : o);
// settings that only matter when another one is switched on (greyed out otherwise)
function isDisabled(key, w, tabName) {
  if (key === 'cone_cx' || key === 'cone_cy') return !!w.auto_center;
  if (key === 'ramp_value') return !!w.auto_ramp;
  if (tabName === 'Support' && key.startsWith('support_')) return !w.enable_support;
  if (key.startsWith('tree_support_')) return !w.enable_support || w.support_type !== 'tree';
  if (key === 'support_expansion') return w.support_type === 'tree';
  if (key === 'brim_ears_max_angle') return w.brim_type !== 'brim_ears';
  if (key.startsWith('fuzzy_skin_')) return w.fuzzy_skin === 'none';
  if (key === 'lightning_angle') return w.infill_pattern !== 'lightning';
  if (key.startsWith('ironing_') && key !== 'ironing_type') return w.ironing_type === 'no ironing';
  if (key === 'brim_width' || key === 'brim_object_gap') return w.brim_type === 'no_brim';
  if (/^overhang_\d_4_speed$/.test(key)) return !w.enable_overhang_speed;
  if (key === 'pressure_advance') return !w.enable_pressure_advance;
  if (key === 'slow_down_min_speed') return !w.slow_down_for_layer_cooling;
  if (key === 'small_perimeter_speed') return !(w.small_perimeter_threshold > 0);
  if (key === 'skirt_height' || key === 'skirt_distance') return !(w.skirt_loops > 0);
  return false;
}
function renderFields(container, cat, tab, rerender) {
  container.innerHTML = '';
  const base = S.presets[cat][S.sel[cat]] || {};
  const w = S.work[cat];
  for (const g of tab.groups) {
    const fields = g.fields.filter((f) => S.advanced || !f.adv);
    if (!fields.length) continue;
    const group = el('div', { class: 'group' }, el('h4', {}, g.name));
    for (const f of fields) {
      const id = `f-${cat}-${f.key}`;
      const modified = JSON.stringify(w[f.key]) !== JSON.stringify(base[f.key]);
      const label = el('label', { for: id, class: modified ? 'modified' : '', title: f.tip || '' }, f.label,
        el('button', { class: 'reset', title: 'Reset to the preset value', tabindex: -1,
          onclick: (e) => { e.preventDefault(); e.stopPropagation(); resetField(cat, f, rerender); } }, '↺'));
      let ctl;
      const disabled = isDisabled(f.key, w, tab.tab);
      const val = fieldValue(cat, f);
      // controls that other settings depend on re-render the form so greyed-out rows update
      const drives = ['checkbox', 'file', 'select'].includes(f.type) || ['skirt_loops', 'small_perimeter_threshold'].includes(f.key);
      const onchange = (e) => setField(cat, f, f.type === 'checkbox' ? e.target.checked : e.target.value,
        drives ? rerender : () => label.classList.toggle('modified',
          JSON.stringify(S.work[cat][f.key]) !== JSON.stringify((S.presets[cat][S.sel[cat]] || {})[f.key])));
      if (f.type === 'select') {
        ctl = el('select', { id, onchange, disabled });
        for (const o of f.options) ctl.append(el('option', { value: optValue(o), selected: String(optValue(o)) === String(val) }, optLabel(o)));
      } else if (f.type === 'checkbox') {
        ctl = el('input', { id, type: 'checkbox', onchange, disabled });
        ctl.checked = !!val;
      } else if (f.type === 'code') {
        ctl = el('textarea', { id, spellcheck: 'false', onchange, disabled });
        ctl.value = val ?? '';
      } else if (f.type === 'color') {
        const c = val || '#cccccc';
        ctl = el('button', { id, class: 'swatch', style: `background:${c}`, title: c,
          onclick: (e) => openPalette(e.currentTarget, c, (v) => setField(cat, f, v, rerender)) });
      } else if (f.type === 'file') {
        const name = val ? val.split(/[\\/]/).pop() : 'Not set';
        ctl = el('div', { class: 'file-ctl' },
          el('span', { class: 'fname', title: val || '' }, name),
          el('button', { onclick: () => pickTemplate(cat, f, rerender) }, 'Choose…'),
          val ? el('button', { onclick: () => setField(cat, f, '', rerender) }, 'Clear') : null);
      } else {
        const type = f.type === 'percent' ? 'number' : f.type;
        ctl = el('input', { id, type, value: val ?? '', min: f.min, max: f.max, step: f.step ?? 'any', onchange, disabled,
          autocomplete: f.type === 'password' ? 'off' : undefined, list: f.suggest ? id + '-list' : undefined });
        if (f.suggest) {
          const nz = nozzle();     // common values for this nozzle first
          const vals = [...f.suggest].sort((a, b) => (a > 0.75 * nz) - (b > 0.75 * nz) || a - b);
          ctl = el('span', { class: 'with-list' }, ctl, el('datalist', { id: id + '-list' },
            ...vals.map((v) => el('option', { value: v }, v > 0.75 * nz ? 'too thick for this nozzle' : ''))));
        }
      }
      const wide = f.type === 'code' || f.type === 'file';
      const row = el('div', { class: 'field' + (wide ? (f.type === 'file' ? ' has-file' : ' wide') : '') + (disabled ? ' disabled' : '') },
        label, el('div', { class: 'ctl' }, ctl, f.unit && !wide ? el('span', { class: 'unit' }, f.unit) : null));
      if (f.tip && (wide || f.key === 'enable_support' || cat !== 'process')) row.append(el('div', { class: 'tip' }, f.tip));
      group.append(row);
    }
    container.append(group);
  }
}
function renderProcess() {
  const tabs = S.schema.process;
  if (!tabs.find((t) => t.tab === S.processTab)) S.processTab = tabs[0].tab;
  const bar = $('#process-tabs');
  bar.innerHTML = '';
  for (const t of tabs) bar.append(el('button', { class: t.tab === S.processTab ? 'active' : '',
    onclick: () => { S.processTab = t.tab; store.set('processTab', t.tab); renderProcess(); } }, t.tab));
  renderFields($('#process-form'), 'process', tabs.find((t) => t.tab === S.processTab), renderProcess);
}
function pickTemplate(cat, f, rerender) {
  const input = $('#file-template');
  input.value = '';
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    try {
      const r = await upload('template', file);
      setField(cat, f, r.path, rerender);
      toast('Template loaded. Save the printer preset to keep it.');
    } catch (e) { toast(e.message, true); }
  };
  input.click();
}

// ------------------------------------------------------------------ preset editor (modal)
let modal = { cat: null, tab: null };
function openEditor(cat) {
  modal = { cat, tab: S.schema[cat][0].tab };
  $('#modal').classList.remove('hidden');
  renderModal();
}
function renderModal() {
  const { cat } = modal;
  $('#modal-title').textContent = `${LABELS[cat]} settings`;
  const ps = $('#modal-preset');
  ps.innerHTML = '';
  const lab = (n) => n + (n === S.sel[cat] && isDirty(cat) ? ' (modified)' : '');
  if (cat === 'process') for (const n of Object.keys(S.presets[cat])) ps.append(el('option', { value: n, selected: n === S.sel[cat] }, lab(n)));
  else groupedOptions(ps, cat, S.sel[cat], lab);
  const tabs = $('#modal-tabs');
  tabs.innerHTML = '';
  for (const t of S.schema[cat]) tabs.append(el('button', { class: t.tab === modal.tab ? 'active' : '', onclick: () => { modal.tab = t.tab; renderModal(); } }, t.tab));
  renderFields($('#modal-form'), cat, S.schema[cat].find((t) => t.tab === modal.tab), renderModal);
  $('#modal-delete').disabled = !!S.presets[cat][S.sel[cat]]._builtin;
  $('#modal-reset').disabled = !isDirty(cat);
  $('#modal-restore').disabled = !S.presets[cat][S.sel[cat]]._builtin;
  $('#modal-title').textContent = `${LABELS[cat]} settings` + (cat === 'filament' && S.slots.length > 1 ? ` (filament ${S.active + 1})` : '');
}

// ------------------------------------------------------------------ 3D scene
const wrap = $('#canvas-wrap');
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
wrap.append(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1d1f24);
const camera = new THREE.PerspectiveCamera(40, 1, 0.5, 5000);
camera.up.set(0, 0, 1);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
scene.add(new THREE.HemisphereLight(0xffffff, 0x404050, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.6);
sun.position.set(200, -150, 300);
scene.add(sun);
const bedGroup = new THREE.Group(); scene.add(bedGroup);
const modelGroup = new THREE.Group(); scene.add(modelGroup);
const previewGroup = new THREE.Group(); scene.add(previewGroup);
let modelMesh = null, axisLine = null;
let framed = false;

function resize() {
  const w = wrap.clientWidth, h = wrap.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(wrap);

// The 3D view can lose its graphics context (screen off, PC locked, driver reset, hours on another tab):
// the canvas then stays blank. three.js rebuilds everything once the context comes back; ask for it back
// if the browser doesn't do it alone.
const GL = { lost: false, timer: 0, ext: renderer.getContext().getExtension('WEBGL_lose_context') };  // can't be fetched once lost
function glRecover() {
  if (!GL.lost || !GL.ext) return;
  try { GL.ext.restoreContext(); } catch (e) { /* the browser brings it back by itself */ }
}
renderer.domElement.addEventListener('webglcontextlost', (e) => {
  e.preventDefault();
  GL.lost = true;
  clearInterval(GL.timer);
  GL.timer = setInterval(() => { if (S.view !== 'device') glRecover(); }, 3000);
});
renderer.domElement.addEventListener('webglcontextrestored', () => {
  GL.lost = false;
  clearInterval(GL.timer);
  buildBed();
  resize();
});

// the build plate: a drawn texture (plate surface, grid, logo) with mm rulers along the front and right edges
const PLATE_LOOK = {
  'Textured PEI Plate': { base: '#2f2b24', dots: true, tint: '#c9a85a' },
  'Cool Plate': { base: '#2b2e35', dots: false, tint: '#8fa3b8' },
  'Engineering Plate': { base: '#2c3036', dots: false, tint: '#9aa0a8' },
  'High Temp Plate': { base: '#33302b', dots: false, tint: '#c7b38a' },
  'Cool Plate (SuperTack)': { base: '#26282c', dots: false, tint: '#7fb0d0' },
};
function plateTexture(bx, by, m, look) {
  const W = bx + 2 * m, H = by + 2 * m;
  const k = Math.min(8, 4096 / Math.max(W, H));             // pixels per mm
  const c = document.createElement('canvas');
  c.width = Math.round(W * k); c.height = Math.round(H * k);
  const g = c.getContext('2d');
  const X = (x) => (x + m) * k, Y = (y) => (H - (y + m)) * k;   // plate mm -> canvas px (y up)
  // plate surface with rounded corners and a soft sheen
  const r = 6 * k;
  g.beginPath(); g.roundRect(X(0), Y(by), bx * k, by * k, r); g.closePath();
  const grad = g.createLinearGradient(X(0), Y(by), X(bx), Y(0));
  grad.addColorStop(0, look.base); grad.addColorStop(0.55, shade(look.base, 10)); grad.addColorStop(1, look.base);
  g.fillStyle = grad; g.fill();
  g.save(); g.clip();
  if (look.dots) {                                         // textured PEI: fine speckle
    g.fillStyle = 'rgba(255,240,200,0.05)';
    for (let i = 0; i < bx * by * 0.35; i++) g.fillRect(X(Math.random() * bx), Y(Math.random() * by), k * 0.5, k * 0.5);
  }
  for (let x = 0; x <= bx + 1e-6; x += 10) {
    g.strokeStyle = x % 50 === 0 ? 'rgba(255,255,255,0.13)' : 'rgba(255,255,255,0.05)'; g.lineWidth = x % 50 === 0 ? 1.4 : 1;
    g.beginPath(); g.moveTo(X(x), Y(0)); g.lineTo(X(x), Y(by)); g.stroke();
  }
  for (let y = 0; y <= by + 1e-6; y += 10) {
    g.strokeStyle = y % 50 === 0 ? 'rgba(255,255,255,0.13)' : 'rgba(255,255,255,0.05)'; g.lineWidth = y % 50 === 0 ? 1.4 : 1;
    g.beginPath(); g.moveTo(X(0), Y(y)); g.lineTo(X(bx), Y(y)); g.stroke();
  }
  // logo and name, bottom middle, like the print on a real plate
  const fs = Math.max(5, Math.min(11, bx / 22)) * k;
  g.globalAlpha = 0.34; g.fillStyle = look.tint; g.textAlign = 'center'; g.textBaseline = 'middle';
  const cy = Y(Math.max(8, by * 0.055));
  g.font = `700 ${fs}px "Segoe UI", system-ui, sans-serif`;
  const label = 'ArcLayer', tw = g.measureText(label).width;
  g.lineWidth = fs * 0.16; g.strokeStyle = look.tint; g.lineCap = 'round';
  const lx = X(bx / 2) - tw / 2 - fs * 0.85;
  g.beginPath(); g.arc(lx, cy + fs * 0.28, fs * 0.5, Math.PI * 1.05, Math.PI * 1.95); g.stroke();   // ◠ mark
  g.fillText(label, X(bx / 2) + fs * 0.3, cy);
  g.font = `600 ${fs * 0.42}px "Segoe UI", system-ui, sans-serif`;
  g.fillText((S.plate || 'Textured PEI Plate').toUpperCase() + `  ·  ${bx} × ${by} mm`, X(bx / 2), cy + fs * 0.95);
  g.globalAlpha = 1;
  g.restore();
  g.beginPath(); g.roundRect(X(0), Y(by), bx * k, by * k, r);
  g.strokeStyle = '#00a98f'; g.lineWidth = 2.2; g.stroke();
  // rulers: X along the front edge, Y along the right edge
  g.fillStyle = '#8b909a'; g.strokeStyle = '#5b606a'; g.lineWidth = 1.2;
  g.font = `600 ${4.6 * k}px "Segoe UI", system-ui, sans-serif`;
  g.textAlign = 'center'; g.textBaseline = 'top';
  for (let x = 0; x <= bx + 1e-6; x += 10) {
    const big = x % 50 === 0;
    g.beginPath(); g.moveTo(X(x), Y(0) + 1.2 * k); g.lineTo(X(x), Y(0) + (big ? 3.4 : 2.2) * k); g.stroke();
    if (big) g.fillText(String(x), X(x), Y(0) + 4.2 * k);
  }
  g.textAlign = 'left'; g.textBaseline = 'middle';
  for (let y = 0; y <= by + 1e-6; y += 10) {
    const big = y % 50 === 0;
    g.beginPath(); g.moveTo(X(bx) + 1.2 * k, Y(y)); g.lineTo(X(bx) + (big ? 3.4 : 2.2) * k, Y(y)); g.stroke();
    if (big) g.fillText(String(y), X(bx) + 4.4 * k, Y(y));
  }
  g.font = `700 ${4.2 * k}px "Segoe UI", system-ui, sans-serif`; g.fillStyle = "#ff6b6b";
  g.textAlign = 'center'; g.textBaseline = 'top'; g.fillText('X mm', X(bx / 2), Y(0) + 10.5 * k);
  g.fillStyle = '#6bd16b'; g.save(); g.translate(X(bx) + 13 * k, Y(by / 2)); g.rotate(-Math.PI / 2);
  g.textBaseline = 'middle'; g.fillText('Y mm', 0, 0); g.restore();
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = renderer.capabilities.getMaxAnisotropy();
  return t;
}
function shade(hex, d) {
  const n = parseInt(hex.slice(1), 16), f = (v) => Math.max(0, Math.min(255, v + d));
  return '#' + [f(n >> 16), f((n >> 8) & 255), f(n & 255)].map((v) => v.toString(16).padStart(2, '0')).join('');
}
function buildBed() {
  const p = S.work.printer;
  const bx = +p.bed_x || 256, by = +p.bed_y || 256, m = 18;
  bedGroup.clear();
  const look = PLATE_LOOK[S.plate] || PLATE_LOOK['Textured PEI Plate'];
  const plate = new THREE.Mesh(new THREE.PlaneGeometry(bx + 2 * m, by + 2 * m),
    new THREE.MeshBasicMaterial({ map: plateTexture(bx, by, m, look), transparent: true }));
  plate.position.set(bx / 2, by / 2, -0.05);
  bedGroup.add(plate);
  if (!framed) frameBed();
}
function frameBed() {
  const p = S.work.printer;
  const bx = +p.bed_x || 256, by = +p.bed_y || 256;
  controls.target.set(bx / 2, by / 2, 10);
  camera.position.set(bx / 2, -by * 0.95, Math.max(bx, by) * 0.95);
  controls.update();
  framed = true;
}

function setModel(info) {
  modelGroup.clear();
  modelMesh = null;
  S.model = info;
  S.parts = info ? info.parts.map((p) => ({ name: p.name, faces: p.faces })) : [];
  S.partTools = S.parts.map(() => Math.min(S.active, S.slots.length - 1));
  S.toolChanges = [];
  S.selPart = -1;
  if (info) {
    modelMesh = new THREE.Group();     // one mesh per part, so each can have its own filament colour
    info.parts.forEach((p, k) => {
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(b64(p.tris, Float32Array), 3));
      g.computeVertexNormals();
      const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: '#cccccc', roughness: 0.55, metalness: 0.05 }));
      m.userData.part = k;
      modelMesh.add(m);
    });
    modelGroup.add(modelMesh);
    axisLine = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineDashedMaterial({ color: 0x00c2a4, dashSize: 2, gapSize: 1.5 }));
    modelGroup.add(axisLine);
  }
  $('#empty-hint').classList.toggle('hidden', !!info);
  modelChanged();
  renderObjects();
  updateModel();
}
let modelOut = false;
function updateModelColors() {
  if (!modelMesh) return;
  for (const m of modelMesh.children) {
    const k = m.userData.part;
    m.material.color.set(slotColor(S.partTools[k] ?? 0));
    m.material.emissive.set(modelOut ? 0x661111 : (k === S.selPart ? 0x1f4d47 : 0x000000));
  }
}
function setPartTool(k, tool) {
  if (tool >= S.slots.length) return toast(`There is no filament ${tool + 1}: add it with ＋ in the Filament card`, true);
  const ks = k < 0 ? S.parts.map((_, i) => i) : [k];
  for (const i of ks) S.partTools[i] = tool;
  updateModelColors(); renderObjects(); markStale();
}
function selectPart(k) { S.selPart = k; updateModelColors(); renderObjects(); }
function renderObjects() {
  const box = $('#objects');
  box.classList.toggle('hidden', !S.model || !!S.calib);   // a calibration print is one object to you
  if (!S.model || S.calib) return;
  box.innerHTML = '';
  box.append(el('h4', {}, S.parts.length > 1 ? `Objects · ${S.parts.length} parts` : 'Object'));
  S.parts.forEach((p, k) => {
    const sel = el('select', { title: 'Filament for this part', onclick: (e) => e.stopPropagation(),
      onchange: (e) => setPartTool(k, +e.target.value) });
    S.slots.forEach((sl, i) => sel.append(el('option', { value: i, selected: i === S.partTools[k] }, `${i + 1} · ${sl.preset}`)));
    box.append(el('div', { class: 'part' + (k === S.selPart ? ' sel' : ''), onclick: () => selectPart(k === S.selPart ? -1 : k) },
      swatchEl(S.partTools[k] ?? 0), el('span', { class: 'n', title: `${p.faces.toLocaleString()} triangles` }, p.name), sel));
  });
  if (S.slots.length > 1) box.append(el('div', { class: 'hint2' }, S.parts.length > 1
    ? 'Click a part (here or on the plate), then press 1–4 to set its filament.'
    : 'One part: colour it by height with ＋ colour on the Preview layer slider.'));
  if (S.toolChanges.length) {
    box.append(el('h4', { style: 'margin-top:8px' }, 'Filament changes by layer'));
    for (const c of S.toolChanges) box.append(el('div', { class: 'chg' }, swatchEl(c.tool), el('span', { class: 'n' }, `From layer ${c.layer}`),
      el('button', { title: 'Remove', onclick: () => { S.toolChanges = S.toolChanges.filter((x) => x !== c); renderObjects(); markStale(); } }, '✕')));
  }
}
function updateModel() {
  const info = $('#object-info');
  if (!modelMesh) { info.textContent = ''; return; }
  const pl = S.placement;
  const sc = (+pl.scale || 100) / 100;
  modelMesh.scale.setScalar(sc);
  modelMesh.quaternion.setFromRotationMatrix(rotMat(pl));
  modelMesh.position.set(0, 0, 0);
  modelMesh.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(modelMesh);
  const c = box.getCenter(new THREE.Vector3());
  modelMesh.position.set(+pl.x - c.x, +pl.y - c.y, -box.min.z);
  modelMesh.updateMatrixWorld(true);
  box.setFromObject(modelMesh);
  const size = box.getSize(new THREE.Vector3());
  const p = S.work.printer;
  const out = box.min.x < 0 || box.min.y < 0 || box.max.x > +p.bed_x || box.max.y > +p.bed_y || size.z > +p.max_z;
  modelOut = out;
  updateModelColors();
  // cone axis
  const w = S.work.process;
  const ax = w.auto_center ? (box.min.x + box.max.x) / 2 : +w.cone_cx;
  const ay = w.auto_center ? (box.min.y + box.max.y) / 2 : +w.cone_cy;
  axisLine.geometry.setFromPoints([new THREE.Vector3(ax, ay, 0), new THREE.Vector3(ax, ay, size.z + 12)]);
  axisLine.computeLineDistances();
  info.className = 'overlay info view-prepare' + (out ? ' bad' : '');
  info.textContent = `${S.model.name}   ${size.x.toFixed(1)} × ${size.y.toFixed(1)} × ${size.z.toFixed(1)} mm   ${S.model.faces.toLocaleString()} triangles` +
    (S.parts.length > 1 ? `   ${S.parts.length} parts` : '') +
    (S.calib ? '\nCalibration print: flat layers' : `\n${w.mode} layers, ${(+w.cone_angle).toFixed(1)}° around (${ax.toFixed(1)}, ${ay.toFixed(1)})`) +
    (out ? '\nThe model is outside the printable area' : '');
}
function syncPlacementInputs() {
  $('#obj-x').value = S.placement.x; $('#obj-y').value = S.placement.y;
  $('#obj-scale').value = S.placement.scale; $('#obj-rot').value = S.placement.rot;
}
function placeOnBed() {
  const p = S.work.printer;
  S.placement = { x: (+p.bed_x || 256) / 2, y: (+p.bed_y || 256) / 2, scale: 100, rot: 0, orient: ID3.slice() };
}
// a model or project the server loaded: {name, parts, ..., project?, path?}
function showLoaded(info, geometryOnly = false) {
  S.calib = (!geometryOnly && info.calibration) || null;
  if (info.project && !geometryOnly) {
    applyProject(info);
    toast(`Opened project ${info.file || info.name}`);
  } else {
    placeOnBed();
    syncPlacementInputs();
    setModel(info);
    S.projectPath = null;
    S.dirty = true;                    // a new, unsaved project
    toast(`Imported ${info.name}`);
  }
  markStale();
  if (info.project && !geometryOnly) S.dirty = false;
  setView('prepare');
  renderCalibPanel();
  reportState();
}
async function importFile(file, geometryOnly = false, asked = false) {
  if (!file) return;
  if (!asked && !(await guardUnsaved('opening another model'))) return;
  toast(`Loading ${file.name}…`);
  try {
    showLoaded(await upload('model', file, geometryOnly ? { 'X-Geometry-Only': '1' } : {}), geometryOnly);
  } catch (e) { toast(e.message, true); }
}
async function openPath(path, geometryOnly = false, asked = false) {
  if (!asked && !(await guardUnsaved('opening another file'))) return;
  toast(`Loading ${path.split(/[\\/]/).pop()}…`);
  try { showLoaded(await api('open-path', { path, geometry_only: geometryOnly }), geometryOnly); } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------ project files (File menu)
const PRIVATE_KEYS = ['ip', 'serial', 'access_code', 'template_3mf'];   // stay on this computer, never in a project
const strip = (d) => Object.fromEntries(Object.entries(d || {}).filter(([k]) => !k.startsWith('_') && !PRIVATE_KEYS.includes(k)));
function projectData() {
  return {
    printer: { name: S.sel.printer, data: strip(S.work.printer) },
    process: { name: S.sel.process, data: strip(S.work.process) },
    filaments: S.slots.map((sl, i) => ({ preset: sl.preset, color: sl.color, type: (S.presets.filament[sl.preset] || {}).type,
      data: strip(i === S.active ? S.work.filament : (sl.overrides || S.presets.filament[sl.preset])) })),
    active: S.active,
    part_tools: S.partTools, tool_changes: S.toolChanges,
    paint: paintReq(),
  };
}
function applyProject(info) {
  const pj = info.project, P = S.presets, missing = [];
  for (const cat of ['printer', 'process']) {
    const want = pj[cat] || {};
    let name = want.name;
    if (cat === 'process' && name && !P.process[name] && P.process[newProcessName(name)]) name = newProcessName(name);
    if (!P[cat][name]) {
      if (name) missing.push(name);
      name = cat === 'process'
        ? Object.keys(P.process).find((n) => Math.abs(+P.process[n].compatible_nozzle - +(pj.printer?.data?.nozzle_diameter || 0.4)) < 1e-6) || S.sel.process
        : S.sel.printer;
    }
    S.sel[cat] = name;
    S.work[cat] = { ...clone(P[cat][name]), ...(want.data || {}) };
  }
  const fil = P.filament;
  const slots = (pj.filaments || []).slice(0, MAX_SLOTS).map((f) => {
    const have = !!fil[f.preset];
    if (!have && f.preset) missing.push(f.preset);
    const preset = have ? f.preset : matchPreset({ type: f.type || (f.data || {}).type });
    return { preset, color: normHex(f.color) || '#cccccc', overrides: f.data ? { ...clone(fil[preset]), ...f.data } : null };
  });
  if (slots.length) S.slots = slots;
  S.active = Math.max(0, Math.min(pj.active || 0, S.slots.length - 1));
  S.sel.filament = S.slots[S.active].preset;
  S.work.filament = S.slots[S.active].overrides || clone(fil[S.sel.filament]);
  for (const sl of S.slots) if (sl.overrides && JSON.stringify(strip(sl.overrides)) === JSON.stringify(strip(fil[sl.preset]))) sl.overrides = null;
  store.set('sel', S.sel);
  const pl = pj.placement || {};
  placeOnBed();
  S.placement = { x: pl.x ?? S.placement.x, y: pl.y ?? S.placement.y, scale: pl.scale ?? 100, rot: pl.rot_z ?? 0,
    orient: Array.isArray(pl.orient) && pl.orient.length === 9 ? pl.orient.slice() : ID3.slice() };
  syncPlacementInputs();
  setModel(info);
  const n = S.slots.length;
  S.partTools = S.parts.map((_, k) => Math.min(Math.max(0, +(pj.part_tools || [])[k] || 0), n - 1));
  S.toolChanges = (pj.tool_changes || []).filter((c) => c.tool < n).map((c) => ({ layer: +c.layer, tool: +c.tool }));
  loadPaint(pj.paint);
  S.projectPath = info.path || null;
  refreshSelects(); renderProcess(); buildBed(); updateDeviceHeader();
  updateModelColors(); renderObjects(); updateModel();
  if (missing.length) setTimeout(() => toast(`Not on this computer: ${missing.join(', ')}. The project's own settings are used instead.`, true), 400);
}
async function saveProject(saveAs = false) {
  if (!S.model) { toast('Nothing to save: import a model first', true); return false; }
  try {
    const r = await api('project/save', { path: S.projectPath, save_as: saveAs, project: projectData(),
      placement: placementReq() });
    if (r.cancelled) return false;
    S.projectPath = r.path; S.dirty = false;
    reportState();
    toast(`Saved ${r.name}` + (S.hasWindow ? '' : ` in ${r.path}`));
    return true;
  } catch (e) { toast(e.message, true); return false; }
}
// "Save changes?" box: resolves to one of the button keys ('cancel' on Esc)
function ask(title, text, buttons) {
  return new Promise((resolve) => {
    const box = $('#ask'), btns = $('#ask-btns');
    $('#ask-title').textContent = title; $('#ask-text').textContent = text;
    btns.innerHTML = '';
    const done = (k) => { box.classList.add('hidden'); document.removeEventListener('keydown', onKey, true); resolve(k); };
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); done('cancel'); } };
    for (const [k, label, cls] of buttons) btns.append(el('button', { class: cls || '', onclick: () => done(k) }, label));
    document.addEventListener('keydown', onKey, true);
    box.classList.remove('hidden');
    btns.querySelector('.primary')?.focus();
  });
}
const projectTitle = () => (S.projectPath ? S.projectPath.split(/[\\/]/).pop() : (S.model ? S.model.name : 'Untitled'));
async function guardUnsaved(verb) {           // true when it's fine to go on
  if (!(S.dirty && S.model)) return true;
  const c = await ask('Save changes?', `Save the changes to “${projectTitle()}” before ${verb}?`,
    [['save', 'Save', 'primary'], ['discard', 'Don’t save'], ['cancel', 'Cancel']]);
  if (c === 'cancel') return false;
  return c === 'save' ? saveProject(false) : true;
}
async function exitApp() {
  if (S.dirty && S.model) {
    const c = await ask('Save changes before closing?', `“${projectTitle()}” has changes that aren’t saved.`,
      [['save', 'Save and exit', 'primary'], ['discard', 'Exit without saving'], ['cancel', 'Cancel']]);
    if (c === 'cancel') return;
    if (c === 'save' && !(await saveProject(false))) return;
  }
  S.dirty = false;
  await api('state', { dirty: false }).catch(() => { });
  api('quit', {}).catch(() => { });
}
async function newProject() {
  if (!(await guardUnsaved('starting a new project'))) return;
  setModel(null); markStale();
  S.projectPath = null; S.dirty = false; S.toolChanges = []; S.calib = null; renderCalibPanel();
  placeOnBed(); syncPlacementInputs(); renderObjects(); reportState();
  setView('prepare');
}
async function openDialog(kind) {        // kind: 'project' (with settings) or 'model' (geometry only)
  if (!(await guardUnsaved('opening another file'))) return;
  try {
    const r = await api('open', { kind });
    if (r.cancelled) return;
    if (r.nodialog) {                    // web browser: use the page's file picker
      const input = $('#file-project');
      input.onchange = (e) => { const f = e.target.files[0]; e.target.value = ''; if (f) importFile(f, kind === 'model', true); };
      return input.click();
    }
    showLoaded(r, kind === 'model');
  } catch (e) { toast(e.message, true); }
}
async function exportKind(kind) {
  if (!S.sliced) return toast('Slice the plate first', true);
  try {
    const r = await api('export', { kind, ask: true });
    if (r.cancelled) return;
    toast(`Saved ${r.path.split(/[\\/]/).pop()}` + (kind === 'gcode' ? ' (plain G-code)' : ''));
    if (!S.hasWindow) api('open-folder', { path: r.path }).catch(() => { });
  } catch (e) { toast(e.message, true); }
}
async function fileMenu(anchor) {
  let recent = [];
  try { recent = (await api('recent')).items; } catch { }
  const tpl = plateKind() === '3mf';
  const items = [
    { content: [el('span', { class: 'ic' }, '🗋'), 'New project'], key: 'Ctrl+N', onclick: newProject },
    { content: [el('span', { class: 'ic' }, '📂'), 'Open project…'], key: 'Ctrl+O', onclick: () => openDialog('project') },
  ];
  if (recent.length) {
    items.push({ heading: 'Recent' });
    for (const r of recent) items.push({ cls: 'recent', title: r, content: [el('span', { class: 'ic' }, ''), el('span', { class: 'n' }, r.split(/[\\/]/).pop())],
      onclick: () => openPath(r) });
  }
  items.push({ sep: true },
    { content: [el('span', { class: 'ic' }, '💾'), 'Save project'], key: 'Ctrl+S', disabled: !S.model, onclick: () => saveProject(false) },
    { content: [el('span', { class: 'ic' }, ''), 'Save project as…'], key: 'Ctrl+Shift+S', disabled: !S.model, onclick: () => saveProject(true) },
    { sep: true },
    { content: [el('span', { class: 'ic' }, '⤓'), 'Import model…'], key: 'Ctrl+I', title: 'STL, OBJ, 3MF, PLY (geometry only)', onclick: () => openDialog('model') },
    { sep: true },
    { content: [el('span', { class: 'ic' }, '☰'), 'Printers & filaments…'], title: 'Choose which printers and filaments the lists show', onclick: () => openChooser(false) },
    { content: [el('span', { class: 'ic' }, '✦'), 'Show the intro'], onclick: () => showIntro(null) },
    { content: [el('span', { class: 'ic' }, '💬'), 'Ask the helper'], onclick: () => toggleHelp(true) },
    { content: [el('span', { class: 'ic' }, '🐞'), 'Report a problem…'], onclick: () => openReport() },
    { content: [el('span', { class: 'ic' }, '⟳'), 'Check for updates'], onclick: () => checkUpdate(true) },
    { content: [el('span', { class: 'ic' }, '⇅'), 'Sync with my other PCs…'], title: 'Keep presets and settings the same on your PCs through OneDrive / Google Drive / Dropbox', onclick: openSync },
    { sep: true },
    { content: [el('span', { class: 'ic' }, '⤒'), 'Export plate sliced file…'], key: 'Ctrl+G', disabled: !S.sliced || !tpl,
      title: tpl ? 'Printable .gcode.3mf' : 'Needs a Bambu template (Printer settings → Machine G-code)', onclick: () => exportKind('3mf') },
    { content: [el('span', { class: 'ic' }, ''), 'Export G-code…'], disabled: !S.sliced, onclick: () => exportKind('gcode') },
    { content: [el('span', { class: 'ic' }, ''), 'Open export folder'], onclick: () => api('open-folder', {}).catch((e) => toast(e.message, true)) });
  if (S.hasWindow) items.push({ sep: true }, { content: [el('span', { class: 'ic' }, ''), 'Exit'], key: 'Alt+F4', onclick: exitApp });
  openMenu(anchor, items, { left: true });
}
// tell the server about unsaved changes (it asks before the window closes) and set the window title
let stateTimer = null;
function reportState() {
  const name = S.model ? projectTitle() : '';
  const dirty = S.dirty && !!S.model;
  $('#project-name').textContent = name ? name + (dirty ? ' •' : '') : '';
  $('#project-name').title = (S.projectPath || (S.model ? 'Not saved yet' : '')) + (dirty ? ' (unsaved changes)' : '');
  clearTimeout(stateTimer);
  stateTimer = setTimeout(() => api('state', { dirty, title: (name ? `${name}${dirty ? ' *' : ''} – ` : '') + 'ArcLayer' }).catch(() => { }), 150);
}
function touch() { if (S.model && !S.dirty) { S.dirty = true; reportState(); } }
// files opened from Explorer while the app runs, and the window's close button
let pendingBusy = false;
async function pollPending() {
  if (pendingBusy) return;
  pendingBusy = true;
  try {
    const r = await api('pending');
    S.hasWindow = r.window;
    for (const path of r.open) await openPath(path);
    if (r.close) await exitApp();
  } catch { } finally { pendingBusy = false; }
}

// ------------------------------------------------------------------ calibration (Orca's calibration prints)
let CAL_TESTS = [];
async function loadCalibTests() {
  const type = S.work.filament.type || 'PLA';
  CAL_TESTS = (await api(`calibration/tests?type=${encodeURIComponent(type)}&nozzle=${nozzle()}`)).tests;
  return CAL_TESTS;
}
async function calibMenu(anchor) {
  try { await loadCalibTests(); } catch (e) { return toast(e.message, true); }
  const items = [];
  let group = null;
  for (const t of CAL_TESTS) {
    if (t.group !== group) { if (group) items.push({ sep: true }); items.push({ heading: t.group }); group = t.group; }
    const na = t.available === false;
    items.push({ cls: na ? 'na' : '', title: t.about, key: na ? 'not on Bambu' : '',
      content: [el('span', { class: 'ic' }, ''), t.name + (na ? '' : '…')], onclick: () => openCalib(t) });
  }
  openMenu(anchor, items, { left: true });
}
const calCount = (p) => (p.step > 0 ? Math.floor(Math.abs(p.end - p.start) / p.step + 1e-6) + 1 : 0);
function openCalib(t) {
  const box = $('#calib-modal'), form = $('#calib-form'), btns = $('#calib-btns');
  $('#calib-title').textContent = t.name;
  $('#calib-about').textContent = t.about;
  form.innerHTML = ''; btns.innerHTML = '';
  const close = () => box.classList.add('hidden');
  const p = { ...(t.defaults || {}) };
  const inputs = {};
  const info = el('div', { class: 'info' });
  const isRange = (t.params || []).some((f) => f.key === 'step');   // start → end in steps (not the non-planar test)
  const update = () => {
    const sl = S.slots[S.active];
    const lines = [`Filament <b>${sl.preset}</b> (slot ${S.active + 1}, ${S.work.filament.type || 'PLA'}) · printer <b>${S.sel.printer}</b> · ${nozzle()} mm nozzle`];
    if (isRange) {
      const n = calCount(p);
      lines.push(n > 0 ? `${n} steps: ${p.start} → ${p.end}` : '<span class="bad">The step must be more than 0</span>');
      if (n > 200) lines.push('<span class="bad">Too many steps (200 at most)</span>');
    }
    if (isBambu() && !S.work.printer.template_3mf && !builtinBambu()) lines.push('<span class="bad">No Bambu template set: you can preview and export G-code, but set a template (Printer settings → Machine G-code) to print it on your Bambu printer.</span>');
    if (S.slots.length > 1) lines.push('Prints in one filament: the selected slot.');
    info.innerHTML = lines.join('<br>');
  };
  if (t.available === false) {
    form.append(el('div', { class: 'info' }, 'This calibration isn’t available for Bambu printers.'));
    btns.append(el('button', { class: 'primary', onclick: close }, 'OK'));
  } else {
    if (t.presets) form.append(el('div', { class: 'presets' }, ...t.presets.map((pr) => el('button', { onclick: () => {
      Object.assign(p, pr.values); for (const k in inputs) inputs[k].value = p[k]; update(); } }, pr.label))));
    if (t.params.length) {
      const row = el('div', { class: 'row' });
      for (const f of t.params) {
        const inp = el('input', { type: 'number', step: f.step, value: p[f.key], oninput: (e) => { p[f.key] = parseFloat(e.target.value); update(); } });
        inputs[f.key] = inp;
        row.append(el('label', {}, f.label + (f.unit ? ` (${f.unit})` : ''), inp));
      }
      form.append(row);
    }
    form.append(info);
    update();
    btns.append(el('button', { onclick: close }, 'Cancel'),
      el('button', { class: 'primary', onclick: async () => {
        if (isRange && !(calCount(p) > 0 && calCount(p) <= 200)) return toast('Check the range and step', true);
        close(); createCalib(t, p);
      } }, 'Create and slice'));
  }
  box.classList.remove('hidden');
  btns.querySelector('.primary')?.focus();
}
async function createCalib(t, params) {
  if (!(await guardUnsaved('creating a calibration print'))) return;
  toast(`Creating ${t.name}…`);
  try {
    const info = await api('calibration/create', { test: t.id, params, nozzle: nozzle(), layer_height: +S.work.process.layer_height || 0.2 });
    placeOnBed(); syncPlacementInputs();
    setModel(info);
    S.calib = info.calibration;
    S.projectPath = null;
    renderObjects(); updateModel();
    markStale();
    S.dirty = false;                       // nothing of yours to lose: closing won't ask to save it
    renderCalibPanel(); reportState(); setView('prepare');
    slice();
  } catch (e) { toast(e.message, true); }
}
function calibResult(c, step) {
  const r = c.result;
  if (!r) return null;
  const cur = +(S.work[r.cat][r.key] ?? 0);
  let v = step.value;
  if (r.mode === 'flow') v = Math.round(cur * (1 + step.value / 100) * 10000) / 10000;
  if (r.mode === 'mvs') v = Math.round(step.value * 0.85 * 10) / 10;
  if (r.mode === 'pct') v = Math.round(step.value * 100) / 10000;
  return { ...r, value: v, current: cur };
}
function renderCalibPanel() {
  const box = $('#calib-panel');
  const c = S.calib;
  box.classList.toggle('hidden', !c || !S.model || S.view === 'device' || LIVE.on);
  if (!c) return;
  box.innerHTML = '';
  box.append(el('div', { class: 'head' }, `🎯 ${c.name}`, el('button', { title: 'Hide (the print stays on the plate)', onclick: () => box.classList.add('hidden') }, '✕')));
  if (!S.sliced) box.append(el('div', { class: 'read' }, 'Slice the plate, print it, then come back here with the result.'));
  box.append(el('div', { class: 'read' }, el('b', {}, 'Reading the print: '), c.read));
  if (c.notes?.length) box.append(el('div', {}, el('div', { class: 'read' }, 'This print uses:'), el('ul', {}, ...c.notes.map((n) => el('li', {}, n)))));
  if (c.result && c.steps.length) {
    const sel = el('select', {});
    c.steps.forEach((st, i) => sel.append(el('option', { value: i }, st.label)));
    sel.value = String(c.pick ?? Math.floor(c.steps.length / 2));
    const out = el('div', { class: 'newval' });
    const apply = el('button', { class: 'primary' }, 'Apply');
    const show = () => {
      c.pick = +sel.value;
      const r = calibResult(c, c.steps[c.pick]);
      const preset = r.cat === 'filament' ? S.slots[S.active].preset : S.sel[r.cat];
      out.textContent = `${r.label}: ${r.current} → ${r.value}  (${LABELS[r.cat].toLowerCase()} “${preset}”)`;
    };
    sel.onchange = show;
    apply.onclick = () => {
      const r = calibResult(c, c.steps[+sel.value]);
      S.work[r.cat][r.key] = r.value;
      if (r.mode === 'pa') S.work[r.cat].enable_pressure_advance = true;
      if (r.key === 'ironing_flow' && (S.work.process.ironing_type || 'no ironing') === 'no ironing') S.work.process.ironing_type = 'top';
      afterWorkChange(r.cat);
      if (r.cat === 'filament') renderSlots();
      toast(`${r.label} set to ${r.value}. Save the ${LABELS[r.cat].toLowerCase()} preset to keep it.`);
      renderCalibPanel();
    };
    show();
    box.append(el('div', { class: 'read' }, el('b', {}, 'Best result:')), sel, out,
      el('div', { class: 'apply' }, apply,
        el('button', { title: 'Save the preset with the new value', onclick: () => savePreset(c.result.cat).catch((e) => toast(e.message, true)) }, '💾 Save preset')));
  } else if (c.steps.length) {
    box.append(el('div', { class: 'read' }, 'Clearances: ' + c.steps.map((st) => st.label.replace(' clearance', '')).join(', ')));
  }
  if (S.sliced) box.append(el('div', { class: 'apply' }, el('button', { onclick: () => setView('device') }, '▶ Print it')));
}

// ------------------------------------------------------------------ preview
const KIND_STYLE = {   // Orca's line-type colours
  outer_wall: ['Outer wall', '#ff7d38'], inner_wall: ['Inner wall', '#ffe64d'],
  overhang_wall: ['Overhang wall', '#3b5bff'], top_surface: ['Top surface', '#f04040'],
  bottom_surface: ['Bottom surface', '#66a3e6'], solid: ['Internal solid infill', '#9654cc'],
  bridge: ['Bridge', '#4d80ba'], infill: ['Sparse infill', '#b03029'], ironing: ['Ironing', '#ff8c69'],
  skirt: ['Skirt', '#00876e'], brim: ['Brim', '#00c2a4'],
  support: ['Support', '#2ecc71'], support_interface: ['Support interface', '#1f8f4a'],
  prime_tower: ['Prime tower', '#9aa3ad'],
};
// colour modes with a value per point (like Orca's "Speed", "Flow", "Line width"…): [label, unit, decimals]
const GRAD_MODES = {
  speed: ['Speed', 'mm/s', 0], flow: ['Volumetric flow', 'mm³/s', 1], width: ['Line width', 'mm', 2],
  height: ['Layer height', 'mm', 2], layer_time: ['Layer time', 's', 1], fan: ['Fan speed', '%', 0],
  temp: ['Temperature', '°C', 0], accel: ['Acceleration', 'mm/s²', 0], pa: ['Pressure advance', '', 3],
};
const RAMP = ['#0b2c7a', '#135985', '#1c8891', '#04d60f', '#aaf200', '#fcf903', '#f5ce0a', '#d16830', '#c2523c', '#942616']
  .map((h) => new THREE.Color(h));
function rampColor(u, out) {                       // u in [0, 1] -> blue … red
  const x = Math.max(0, Math.min(1, u)) * (RAMP.length - 1), i = Math.min(RAMP.length - 2, Math.floor(x));
  return out.copy(RAMP[i]).lerp(RAMP[i + 1], x - i);
}
function modeValues(mode) {                        // Float32Array: the value at every preview point, or null
  if (!P) return null;
  if (P.vals[mode]) return P.vals[mode];
  const d = P.data, n = P.posArr.length / 3;
  let v = null;
  if (mode === 'speed' && P.speed) v = Float32Array.from(P.speed);
  else if (mode === 'height' && d.height) { const h = b64(d.height, Uint16Array); v = new Float32Array(n); for (let k = 0; k < n; k++) v[k] = h[k] / 1000; }
  else if (mode === 'layer_time' && d.layer_time) {
    v = new Float32Array(n);
    let j = 0;
    d.layers.forEach((end, L) => { for (; j < end; j++) v[P.idx[j]] = d.layer_time[L]; });
  } else if (d.attrs && d.attrs[mode]) {
    const a = b64(d.attrs[mode], Uint16Array), sc = d.attr_scale[mode];
    v = new Float32Array(n); for (let k = 0; k < n; k++) v[k] = a[k] / sc;
  }
  return (P.vals[mode] = v);
}
function modeRange(mode) {
  const v = modeValues(mode);
  if (!v) return null;
  let lo = Infinity, hi = -Infinity;
  for (const o of P.objs) {                       // only what is shown (hidden line types don't stretch the scale)
    if (hiddenKinds.has(o.name)) continue;
    const ix = o.line.geometry.index.array;
    for (let k = 0; k < o.count; k++) { const x = v[ix[k]]; if (x < lo) lo = x; if (x > hi) hi = x; }
  }
  return lo <= hi ? [lo, hi] : [0, 0];
}
const hiddenKinds = new Set(store.get('hiddenKinds', []));
const hiddenTools = new Set();
let P = null; // preview buffers

function buildPreview(data, stats, coll) {
  previewGroup.clear();
  const pos = new THREE.BufferAttribute(b64(data.pos, Float32Array), 3);
  const kind = b64(data.kind, Uint8Array);
  const idx = b64(data.idx, Uint32Array);
  const tool = data.tool ? b64(data.tool, Uint8Array) : new Uint8Array(kind.length);
  const nk = data.kinds.length;
  let nt = 1;
  for (let v = 0; v < tool.length; v++) if (tool[v] + 1 > nt) nt = tool[v] + 1;
  // split segments per (feature type, filament), tracking where each layer ends in every list
  const lists = Array.from({ length: nk * nt }, () => []);
  const ends = Array.from({ length: nk * nt }, () => []);
  const gsegs = Array.from({ length: nk * nt }, () => []);
  let j = 0;
  for (const end of data.layers) {
    for (; j < end; j += 2) { const v = idx[j], c = kind[v] * nt + tool[v]; lists[c].push(v, idx[j + 1]); gsegs[c].push(j >> 1); }
    for (let k = 0; k < nk * nt; k++) ends[k].push(lists[k].length);
  }
  const objs = [];
  const colorAttr = new THREE.BufferAttribute(new Float32Array(pos.array.length), 3);
  for (let c = 0; c < nk * nt; c++) {
    if (!lists[c].length) continue;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', pos);
    g.setAttribute('color', colorAttr);
    g.setIndex(new THREE.BufferAttribute(new Uint32Array(lists[c]), 1));
    const line = new THREE.LineSegments(g, new THREE.LineBasicMaterial());
    line.frustumCulled = false;
    previewGroup.add(line);
    objs.push({ name: data.kinds[Math.floor(c / nt)], t: c % nt, line, ends: ends[c], count: lists[c].length,
      gseg: Uint32Array.from(gsegs[c]) });
  }
  P = { data, objs, stats, coll, posArr: pos.array, kind, idx, tool, speed: data.speed ? b64(data.speed, Uint16Array) : null,
    colorAttr, vals: {} };
  if (GRAD_MODES[S.colorMode] && !modeValues(S.colorMode)) S.colorMode = 'kind';
  P.cum = simTimes();
  buildCollisionMarks(coll);
  buildToolhead();
  colorPreview();
  const r = $('#layer-range');
  r.max = data.layers.length; r.value = data.layers.length;
  renderLegend();
  updateLayers(true);
}

// ------------------------------------------------------------------ toolhead + in-layer playback
let head = null, marks = null;
function buildToolhead() {
  // nozzle cone and heater block / sock, from Printer settings > Toolhead clearance
  const p = S.work.printer;
  const free = +p.nozzle_free_height || 3, R = +p.head_radius || 12, slope = +p.nozzle_cone_slope || 1.2;
  head = new THREE.Group();
  const coneR = Math.min(free / slope, R);
  const cone = new THREE.Mesh(new THREE.ConeGeometry(coneR, free, 32, 1, true),
    new THREE.MeshStandardMaterial({ color: 0xc9a227, metalness: 0.6, roughness: 0.35, side: THREE.DoubleSide }));
  cone.geometry.rotateX(-Math.PI / 2);           // apex down
  cone.geometry.translate(0, 0, free / 2);       // apex at the nozzle tip (z = 0)
  const blockMat = new THREE.MeshStandardMaterial({ color: 0x9aa3b5, transparent: true, opacity: 0.28, depthWrite: false });
  const block = new THREE.Mesh(new THREE.CylinderGeometry(R, R, 8, 48), blockMat);
  block.geometry.rotateX(Math.PI / 2);
  block.geometry.translate(0, 0, free + 4);
  const rim = new THREE.LineSegments(new THREE.EdgesGeometry(block.geometry, 30), new THREE.LineBasicMaterial({ color: 0xd0d6e2 }));
  head.add(cone, block, rim);
  head.userData = { blockMat, rim };
  head.renderOrder = 10;
  previewGroup.add(head);
}
function buildCollisionMarks(coll) {
  const pts = (coll && coll.points) || [];
  marks = null;
  if (!pts.length) return;
  pts.sort((a, b) => a[3] - b[3]);
  const arr = new Float32Array(pts.length * 3);
  pts.forEach((q, k) => { arr[3 * k] = q[0]; arr[3 * k + 1] = q[1]; arr[3 * k + 2] = q[2]; });
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(arr, 3));
  const m = new THREE.Points(g, new THREE.PointsMaterial({ color: 0xff3030, size: 7, sizeAttenuation: false, depthTest: false }));
  m.renderOrder = 11;
  m.frustumCulled = false;
  previewGroup.add(m);
  // per preview layer: how many marks are on that layer or below
  const nl = P.data.layers.length, upto = new Uint32Array(nl);
  let k = 0;
  for (let L = 0; L < nl; L++) { while (k < pts.length && pts[k][3] <= L) k++; upto[L] = k; }
  marks = { obj: m, pts, upto };
}
const lowerBound = (a, x) => { let lo = 0, hi = a.length; while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] < x) lo = m + 1; else hi = m; } return lo; };
function layerSegs(i) {             // [first, end) global segment numbers of preview layer i (1-based)
  return [i > 1 ? P.data.layers[i - 2] >> 1 : 0, P.data.layers[i - 1] >> 1];
}
function showWorstCollision() {
  if (!P || !marks) return;
  const worst = marks.pts.reduce((a, b) => (b[4] > a[4] ? b : a));
  const r = $('#layer-range');
  r.value = worst[3] + 1;
  updateLayers(true);
  // move the toolhead to that point: the first segment of the layer ending there
  const [s0, s1] = layerSegs(worst[3] + 1);
  for (let g = s0; g < s1; g++) {
    const v = P.idx[2 * g + 1];
    if (Math.abs(P.posArr[3 * v] - worst[0]) < 0.6 && Math.abs(P.posArr[3 * v + 1] - worst[1]) < 0.6 && Math.abs(P.posArr[3 * v + 2] - worst[2]) < 0.6) {
      $('#move-range').value = g - s0 + 1; break;
    }
  }
  updateLayers();
  controls.target.set(worst[0], worst[1], worst[2]);
  controls.update();
}
let playing = null;
function stopPlay() { if (playing) cancelAnimationFrame(playing); playing = null; $('#move-play').textContent = '▶'; }
function togglePlay() {
  if (!P) return;
  if (playing) return stopPlay();
  const r = $('#move-range');
  if (+r.value >= +r.max) r.value = 0;
  $('#move-play').textContent = '❚❚';
  const n = +r.max;
  const rate = Math.max(20, n / 8);                      // moves per second: about 8 s per layer
  let pos = +r.value, last = performance.now();
  const tick = (now) => {
    pos = Math.min(n, pos + rate * Math.max(0, Math.min(0.25, (now - last) / 1000)));   // by elapsed time, not frames
    last = now;
    r.value = Math.floor(pos);
    updateLayers();
    if (pos >= n) return stopPlay();
    playing = requestAnimationFrame(tick);
  };
  playing = requestAnimationFrame(tick);
}
// ------------------------------------------------------------------ simulation of the whole print
// cumulative time at the end of every segment: length / print speed, plus travel between paths
function simTimes() {
  const idx = P.idx, pa = P.posArr, sp = P.speed, n = idx.length >> 1;
  const travel = Math.max(1, +P.data.travel_speed || 200);
  const cum = new Float64Array(n + 1);
  let t = 0;
  for (let g = 0; g < n; g++) {
    const a = idx[2 * g], b = idx[2 * g + 1];
    if (g > 0 && a !== idx[2 * g - 1]) {               // a new path: travel from the end of the last one
      const c = idx[2 * g - 1];
      t += Math.hypot(pa[3 * a] - pa[3 * c], pa[3 * a + 1] - pa[3 * c + 1], pa[3 * a + 2] - pa[3 * c + 2]) / travel;
    }
    const len = Math.hypot(pa[3 * b] - pa[3 * a], pa[3 * b + 1] - pa[3 * a + 1], pa[3 * b + 2] - pa[3 * a + 2]);
    t += len / Math.max(1, sp ? sp[b] : 50);
    cum[g + 1] = t;
  }
  // scale to the slicer's estimate (which adds acceleration and the start sequence)
  const est = +(P.stats && P.stats.time_s) || t;
  P.simScale = t > 0 ? est / t : 1;
  return cum;
}
const SIM = { on: false, raf: 0, t: 0, playing: false, last: 0, lastLayer: -1 };
const fmtClock = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m ${String(x).padStart(2, '0')}s`; };
function simShow() {                     // put the preview at simulated time SIM.t (seconds of real print time)
  const cum = P.cum, n = cum.length - 1, total = cum[n] * P.simScale;
  const tt = Math.min(SIM.t, total) / P.simScale;
  const g = Math.min(n - 1, Math.max(0, lowerBound(cum, tt) - 1));    // segment being printed
  const L = lowerBound(P.data.layers, 2 * (g + 1)) + 1;                // its layer (1-based)
  const r = $('#layer-range');
  if (+r.value !== L || SIM.lastLayer !== L) { r.value = L; updateLayers(true); SIM.lastLayer = L; }
  const [s0] = layerSegs(L);
  $('#move-range').value = g - s0 + 1;
  updateLayers();
  if ($('#sim-follow').checked && head) { controls.target.lerp(head.position, 0.15); controls.update(); }
  $('#sim-range').value = Math.round(1000 * Math.min(1, SIM.t / Math.max(total, 1e-6)));
  $('#sim-time').textContent = `${fmtClock(SIM.t)} / ${fmtClock(total)}`;
  const v = P.idx[2 * g + 1], kname = P.data.kinds[P.kind[v]];
  $('#sim-where').textContent = `Layer ${L} / ${P.data.layers.length}  ·  ${(KIND_STYLE[kname] || [kname])[0]}  ·  ${P.speed ? P.speed[v] : '?'} mm/s`;
  return SIM.t >= total;
}
function simLoop(now) {
  if (!SIM.on) return;
  if (SIM.playing) {
    const dt = Math.min(0.25, Math.max(0, (now - SIM.last) / 1000));
    SIM.t += dt * +$('#sim-speed').value;
    if (simShow()) { SIM.playing = false; $('#sim-play').textContent = '▶'; }
  }
  SIM.last = now;
  SIM.raf = requestAnimationFrame(simLoop);
}
function simStart() {
  if (!P || !P.cum) return;
  stopPlay();
  SIM.on = true; SIM.playing = true; SIM.t = 0; SIM.lastLayer = -1; SIM.last = performance.now();
  $('#single-layer').checked = false;
  $('#show-head').checked = true;
  $('#sim-btn').classList.add('hidden'); $('#move-slider').classList.add('hidden');
  $('#sim-bar').classList.remove('hidden');
  $('#sim-play').textContent = '❚❚';
  simShow();
  SIM.raf = requestAnimationFrame(simLoop);
}
function simStop() {
  if (!SIM.on) return;
  SIM.on = false; SIM.playing = false;
  cancelAnimationFrame(SIM.raf);
  $('#sim-bar').classList.add('hidden');
  if (P) { $('#sim-btn').classList.remove('hidden'); $('#move-slider').classList.remove('hidden');
    const r = $('#layer-range'); r.value = r.max; updateLayers(true); }
}
function simToggle() {
  if (!SIM.on) return simStart();
  const total = P.cum[P.cum.length - 1] * P.simScale;
  if (!SIM.playing && SIM.t >= total) SIM.t = 0;          // finished: play again from the start
  SIM.playing = !SIM.playing;
  SIM.last = performance.now();
  $('#sim-play').textContent = SIM.playing ? '❚❚' : '▶';
}
$('#sim-btn').onclick = simStart;
$('#sim-play').onclick = (e) => { e.currentTarget.blur(); simToggle(); };
$('#sim-close').onclick = simStop;
$('#sim-range').oninput = (e) => {
  SIM.t = (+e.target.value / 1000) * P.cum[P.cum.length - 1] * P.simScale;
  simShow();
};
function updateLayers(layerChanged = false) {
  if (!P) return;
  const i = +$('#layer-range').value;
  const single = $('#single-layer').checked;
  const [s0, s1] = layerSegs(i);
  const mr = $('#move-range');
  if (layerChanged || P.moveLayer !== i) {           // new layer: the move slider starts full, like Orca
    stopPlay();
    mr.max = s1 - s0; mr.value = s1 - s0;
    P.moveLayer = i;
  }
  const m = Math.min(+mr.value, s1 - s0);
  const cut = s0 + m;                                // segments [s0, cut) of this layer are drawn
  for (const o of P.objs) {
    const start = single && i > 1 ? o.ends[i - 2] : 0;
    const end = m >= s1 - s0 ? o.ends[i - 1] : 2 * lowerBound(o.gseg, cut);
    o.line.geometry.setDrawRange(start, Math.max(0, end - start));
    o.line.visible = !hiddenKinds.has(o.name) && !(S.colorMode === 'tool' && hiddenTools.has(o.t));
  }
  $('#layer-top-label').textContent = `${i}\n${P.data.layer_z[i - 1].toFixed(2)} mm`;
  // toolhead at the end of the last drawn segment
  const seg = Math.max(s0, cut - 1);
  const v = m > 0 ? P.idx[2 * seg + 1] : P.idx[2 * s0];
  const x = P.posArr[3 * v], y = P.posArr[3 * v + 1], z = P.posArr[3 * v + 2];
  let near = null;
  if (marks) {
    const lo = i > 1 ? marks.upto[i - 2] : 0, hi = marks.upto[i - 1];
    marks.obj.geometry.setDrawRange(0, single ? 0 : hi);
    if (single) marks.obj.geometry.setDrawRange(lo, hi - lo);
    for (let k = lo; k < hi; k++) {
      const q = marks.pts[k];
      if (Math.abs(q[0] - x) < 0.6 && Math.abs(q[1] - y) < 0.6 && Math.abs(q[2] - z) < 0.6) { near = q; break; }
    }
  }
  if (head) {
    head.position.set(x, y, z);
    head.visible = $('#show-head').checked && s1 > s0;
    head.userData.blockMat.color.set(near ? 0xff4040 : 0x9aa3b5);
    head.userData.blockMat.opacity = near ? 0.45 : 0.28;
  }
  const k = P.kind[v], kname = P.data.kinds[k];
  const label = (KIND_STYLE[kname] || [kname])[0];
  const fil = (P.stats.filaments || []).length > 1 ? ` · filament ${P.tool[v] + 1}` : '';
  $('#move-label').textContent = `${m} / ${s1 - s0}  ·  ${label}${fil}  ·  X ${x.toFixed(2)}  Y ${y.toFixed(2)}  Z ${z.toFixed(2)}` +
    (near ? `  ·  ⚠ ${near[4].toFixed(2)} mm too close` : '');
  $('#move-label').classList.toggle('bad', !!near);
}
function renderLegend() {
  const L = $('#legend');
  L.innerHTML = '';
  if (!P) return;
  const st = P.stats;
  const pick = el('select', { class: 'mode-pick', title: 'Colour the toolpaths by', onchange: (e) => {
    S.colorMode = e.target.value; store.set('colorMode', S.colorMode); colorPreview(); renderLegend(); updateLayers(); } },
  el('option', { value: 'kind' }, 'Line type'), el('option', { value: 'tool' }, 'Filament'),
  ...Object.entries(GRAD_MODES).filter(([m]) => modeValues(m)).map(([m, [label]]) => el('option', { value: m }, label)));
  pick.value = S.colorMode;
  L.append(el('div', { class: 'modes' }, el('span', { class: 'muted' }, 'Colour by'), pick));
  if (GRAD_MODES[S.colorMode]) {
    const [label, unit, dec] = GRAD_MODES[S.colorMode];
    const [lo, hi] = modeRange(S.colorMode);
    const steps = hi - lo < 1e-9 ? 1 : 10, c = new THREE.Color();
    for (let k = steps - 1; k >= 0; k--) {
      const val = steps === 1 ? lo : lo + (hi - lo) * k / (steps - 1);
      L.append(el('div', { class: 'item grad' }, el('span', { class: 'sw', style: `background:#${rampColor(steps === 1 ? 0.5 : k / (steps - 1), c).getHexString()}` }),
        `${val.toFixed(dec)} ${unit}`));
    }
    if (S.colorMode === 'pa' && hi === 0) L.append(el('div', { class: 'note' }, 'Pressure advance is off: the printer uses its own value.'));
    if (S.colorMode === 'accel' && hi === 0) L.append(el('div', { class: 'note' }, 'No acceleration commands: the printer uses its own.'));
  } else if (S.colorMode === 'tool') {
    for (const f of st.filaments || []) {
      L.append(el('div', { class: 'item' + (hiddenTools.has(f.slot) ? ' off' : ''), title: f.preset, onclick: () => {
        hiddenTools.has(f.slot) ? hiddenTools.delete(f.slot) : hiddenTools.add(f.slot); renderLegend(); updateLayers();
      } }, swatchEl(f.slot), el('span', { class: 'grow' }, f.preset), el('span', { class: 'muted' }, `${f.used_m.toFixed(2)} m · ${f.used_g.toFixed(1)} g`)));
    }
  } else {
    const seen = new Set();
    for (const o of P.objs) {
      if (seen.has(o.name)) continue;
      seen.add(o.name);
      const [label, color] = KIND_STYLE[o.name] || [o.name, '#fff'];
      L.append(el('div', { class: 'item' + (hiddenKinds.has(o.name) ? ' off' : ''), onclick: () => {
        hiddenKinds.has(o.name) ? hiddenKinds.delete(o.name) : hiddenKinds.add(o.name);
        store.set('hiddenKinds', [...hiddenKinds]); colorPreview(); renderLegend(); updateLayers();
      } }, el('span', { class: 'sw', style: `background:${color}` }), label));
    }
  }
  L.append(el('h4', { style: 'margin-top:10px' }, 'Summary'));
  const row = (a, b) => L.append(el('div', { class: 'stat' }, el('span', { class: 'muted' }, a), el('b', {}, b)));
  row('Estimated time', fmtTime(st.time_s));
  row('Filament', `${st.filament_m.toFixed(2)} m · ${st.filament_g.toFixed(1)} g`);
  if (st.cost > 0) row('Cost', st.cost.toFixed(2));
  row('Layers', String(st.layers));
  if (st.time_by_kind) {                         // speed check: where the time goes
    const total = Object.values(st.time_by_kind).reduce((a, b) => a + b, 0) || 1;
    const d = el('details', { class: 'time-split' }, el('summary', {}, 'Time by line type'));
    for (const [k, v] of Object.entries(st.time_by_kind)) {
      if (v < 1) continue;
      const [label, color] = k === 'travel' ? ['Travel', '#8a909c'] : (KIND_STYLE[k] || [k, '#fff']);
      d.append(el('div', { class: 'stat' }, el('span', { class: 'muted' }, el('span', { class: 'sw', style: `background:${color}` }), ' ', label),
        el('b', {}, `${fmtTime(v)} · ${Math.round(100 * v / total)}%`)));
    }
    L.append(d);
    const slow = [];
    if (st.time_cool_extra > 60) slow.push(`Slowing down for layer cooling adds ${fmtTime(st.time_cool_extra)} (${st.slowed_layers} layers). Filament → Cooling → "layers shorter than".`);
    if (st.time_mvs_extra > 60) slow.push(`The filament's max volumetric speed adds ${fmtTime(st.time_mvs_extra)}. Filament → Max volumetric speed.`);
    for (const t of slow) L.append(el('div', { class: 'note' }, '⏱ ' + t));
  }
  if (st.tool_changes) row('Filament changes', `${st.tool_changes} · flush ≈ ${st.flush_g.toFixed(1)} g`);
  row('Cone / ramp', `${st.angle.toFixed(1)}° / ${st.ramp.toFixed(1)} mm`);
  row('Layer thickness', `${st.thick_min.toFixed(2)}–${st.thick_max.toFixed(2)} mm`);
  const req = P.coll.angle_requested, used = P.coll.angle_used;
  const lowered = req != null && used != null && Math.abs(req - used) > 0.01;
  if (P.coll.hits) L.append(el('div', { class: 'warn' }, `⚠ Possible toolhead collision at ${P.coll.hits} points (worst ${P.coll.worst_mm.toFixed(2)} mm, layer ${P.coll.worst_layer}). ` +
    (lowered ? `Already lowered the cone angle to ${used.toFixed(1)}°. ` : '') +
    'Lower the cone angle, or measure your hotend and set Printer settings → Toolhead clearance. ',
    el('button', { class: 'tiny-btn', onclick: showWorstCollision }, 'Show')));
  else L.append(el('div', { class: 'ok' }, '✓ No toolhead collisions found'));
  if (P.coll.order && P.coll.order.length) row('Print order (by object)', P.coll.order.join(' → '));
  for (const wv of P.coll.sequence || []) L.append(el('div', { class: 'warn' }, '⚠ ' + wv.text));
  if ((P.coll.sequence || []).some((wv) => wv.kind === 'too_close'))
    L.append(el('div', { class: 'gbtns' }, el('button', { class: 'sm', onclick: () => { setView('prepare'); arrangeModel(); } }, '⊞ Arrange with the toolhead clearance')));
  if (lowered) L.append(el('div', { class: 'note' }, `Cone angle lowered from ${req.toFixed(1)}° to ${used.toFixed(1)}° so the toolhead clears the print. ` +
    'Steep overhangs may now need support. Turn this off in Non-planar → "If the toolhead could hit the print".'));
}
function colorPreview() {
  if (!P) return;
  const grad = GRAD_MODES[S.colorMode] && modeValues(S.colorMode);
  if (grad) {
    const [lo, hi] = modeRange(S.colorMode), span = hi - lo, arr = P.colorAttr.array, c = new THREE.Color();
    for (let k = 0; k < grad.length; k++) {
      rampColor(span > 1e-9 ? (grad[k] - lo) / span : 0.5, c);
      arr[3 * k] = c.r; arr[3 * k + 1] = c.g; arr[3 * k + 2] = c.b;
    }
    P.colorAttr.needsUpdate = true;
  }
  for (const o of P.objs) {
    const m = o.line.material;
    if (m.vertexColors !== !!grad) { m.vertexColors = !!grad; m.needsUpdate = true; }
    m.color.set(grad ? '#ffffff' : S.colorMode === 'tool' ? slotColor(o.t) : (KIND_STYLE[o.name] || [o.name, '#ffffff'])[1]);
  }
}
function addToolChange(anchor) {
  if (!P) return;
  if (S.slots.length < 2) return toast('Add a second filament first (＋ in the Filament card)', true);
  const layer = +$('#layer-range').value;
  if (layer <= 1) return toast('Pick a layer above the first one with the slider', true);
  openMenu(anchor, S.slots.map((sl, i) => ({ content: [swatchEl(i), `Filament ${i + 1} · ${sl.preset}`], onclick: () => {
    S.toolChanges = [...S.toolChanges.filter((c) => c.layer !== layer), { layer, tool: i }].sort((a, b) => a.layer - b.layer);
    renderObjects();
    toast(`From layer ${layer}: filament ${i + 1}. Re-slicing…`);
    S.sliced = true; markStale(); slice();
  } })));
}
function markStale() {
  touch();
  if (!S.sliced) return;
  S.sliced = false;
  $('#btn-print').disabled = true; $('#btn-export').disabled = true;
  previewGroup.clear(); P = null; renderLegend();
  $('#preview-empty').classList.remove('hidden');
  $('#layer-slider').classList.add('hidden'); $('#move-slider').classList.add('hidden'); stopPlay(); simStop(); $('#sim-btn').classList.add('hidden');
}

// ------------------------------------------------------------------ slicing
let polling = null;
async function slice() {
  if (!S.model) return toast('Import a model first', true);
  if (LIVE.on) liveStop(false);
  $('#btn-slice').disabled = true;
  $('#slice-status').classList.remove('hidden');
  $('#log').textContent = '';
  try {
    await api('slice', {
      printer: S.sel.printer, filament: S.sel.filament, process: S.sel.process,
      printer_overrides: S.work.printer, process_overrides: S.work.process,
      filaments: S.slots.map((sl, i) => ({ preset: sl.preset, color: sl.color, overrides: i === S.active ? S.work.filament : (sl.overrides || null) })),
      part_tools: S.partTools, tool_changes: S.toolChanges,
      placement: placementReq(), paint: paintReq(), plate: S.plate,
    });
  } catch (e) { $('#btn-slice').disabled = false; return toast(e.message, true); }
  clearInterval(polling);
  polling = setInterval(async () => {
    let j;
    try { j = await api('job'); } catch { return; }
    $('#progress-bar').style.width = (j.progress * 100).toFixed(1) + '%';
    $('#progress-text').textContent = j.text;
    $('#log').textContent = j.log.join('\n');
    if (j.state === 'done' || j.state === 'error') {
      clearInterval(polling);
      $('#btn-slice').disabled = false;
      if (j.state === 'error') return toast(j.error, true);
      const data = await api('preview');
      buildPreview(data, j.stats, j.collision);
      S.sliced = true;
      $('#btn-print').disabled = false; $('#btn-export').disabled = false;
      S.fileFilaments = j.stats.filaments || [];
      renderAmsMap();
      $('#preview-empty').classList.add('hidden');
      $('#layer-slider').classList.remove('hidden'); $('#move-slider').classList.remove('hidden'); $('#sim-btn').classList.remove('hidden');
      setView('preview');
      renderCalibPanel();
      const c = j.collision || {};
      const low = c.angle_requested != null && Math.abs(c.angle_requested - c.angle_used) > 0.01;
      toast(`Sliced: ${fmtTime(j.stats.time_s)}, ${j.stats.filament_g.toFixed(1)} g` + (low ? ` · cone lowered to ${c.angle_used.toFixed(1)}° to avoid a collision` : ''));
    }
  }, 350);
}
// Bambu printers print over the network (Device tab); others get a G-code file for USB / SD / web upload
const isBambu = () => (S.presets.printer[S.sel.printer]?.vendor || 'Bambu Lab') === 'Bambu Lab';
// Bambu printers get a printable .gcode.3mf without a template (their own start G-code is built in)
const builtinBambu = () => isBambu();   // every Bambu printer has its own start G-code built in
const plateKind = () => (S.work.printer.template_3mf || builtinBambu() ? '3mf' : 'gcode');
async function exportPlate() {
  const kind = plateKind();
  if (kind === 'gcode' && isBambu()) toast('No Bambu template set: exporting plain G-code (set one to get a printable .gcode.3mf)');
  return exportKind(kind);
}

// ------------------------------------------------------------------ device
let devTimer = null;
const sendOptions = () => Object.fromEntries($$('[data-opt]').map((x) => [x.dataset.opt, x.checked]));
function updateDeviceHeader() { $('#dev-printer-name').textContent = S.devActive || S.sel.printer; }
// every printer with connection details: click one to show it (and connect to it)
function renderDevPrinters(list) {
  const box = $('#dev-printers');
  const sig = JSON.stringify(list.map((p) => [p.name, p.connected, p.active, p.state, p.percent]));
  if (sig === box.dataset.sig) return;
  box.dataset.sig = sig;
  box.innerHTML = '';
  if (list.length < 2 && !(list[0] && !list[0].active)) return;
  for (const p of list) {
    const st = !p.connected ? 'Not connected' : p.state === 'RUNNING' ? `Printing ${p.percent ?? 0}%` : p.state === 'PAUSE' ? 'Paused'
      : p.state === 'FINISH' ? 'Finished' : p.state === 'FAILED' ? 'Stopped' : p.state === 'PREPARE' ? 'Preparing' : 'Idle';
    box.append(el('button', { class: 'dev-chip' + (p.active ? ' on' : '') + (p.connected ? ' live' : ''), title: `${p.model} · serial …${p.tail}`,
      onclick: async () => {
        if (p.active) return;
        try { await api('device/use', { printer: p.name }); box.dataset.sig = ''; devPoll(); } catch (e) { toast(e.message, true); }
      } }, el('span', { class: 'dot' }), el('span', { class: 'n' }, p.name.replace(/ 0\.\d nozzle$/, '')), el('span', { class: 'muted small' }, st)));
  }
}
async function devPoll() {
  let s;
  try { s = await api('device/status'); } catch { return; }
  S.devStatus = s;
  document.body.classList.toggle('dev-not-bambu', !!s.connected && (s.kind || 'bambu') !== 'bambu');
  if (s.active !== S.devActive) { S.devActive = s.active; updateDeviceHeader(); }
  renderDevPrinters(s.printers || []);
  if (s.runout_cfg && !S.runoutShown) { S.runoutShown = true; showRunout(s.runout_cfg); }
  const ev = $('#runout-events');
  ev.innerHTML = '';
  for (const e of (s.runout_events || []).slice(-3).reverse())
    ev.append(el('div', {}, `${new Date(e.time * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} · ${e.text}`));
  if (s.runout_events?.length && s.runout_events.at(-1).time !== S.lastRunout) {
    if (S.lastRunout !== undefined) toast(s.runout_events.at(-1).text);
    S.lastRunout = s.runout_events.at(-1).time;
  }
  $('#dev-live').disabled = !s.connected;
  const pill = $('#dev-conn');
  pill.textContent = s.connected ? `Connected · ${s.ip}` : 'Disconnected';
  pill.className = 'pill' + (s.connected ? ' on' : '');
  const t = (a, b) => (a == null ? '–' : `${Math.round(a)}°C` + (b ? ` / ${Math.round(b)}°C` : ''));
  $('#dev-nozzle').textContent = t(s.nozzle_temper, s.nozzle_target_temper);
  $('#dev-bed').textContent = t(s.bed_temper, s.bed_target_temper);
  const STATES = { RUNNING: 'Running', PAUSE: 'Paused', FINISH: 'Finished', IDLE: 'Idle', FAILED: 'Failed', PREPARE: 'Preparing', SLICING: 'Preparing' };
  const st = s.gcode_state ? (STATES[String(s.gcode_state).toUpperCase()] || String(s.gcode_state).charAt(0).toUpperCase() + String(s.gcode_state).slice(1).toLowerCase()) : '';
  $('#dev-job').textContent = st || (s.connected ? 'Waiting…' : '–');
  $('#dev-job').className = 'job-state' + (st ? ' st-' + st.toLowerCase() : '');
  $('#dev-job-name').textContent = s.subtask_name || '';
  $('#dev-progress').style.width = (s.mc_percent || 0) + '%';
  $('#dev-layer').textContent = s.total_layer_num ? `Layer ${s.layer_num} / ${s.total_layer_num}` : 'Layer –';
  $('#dev-remaining').textContent = s.mc_remaining_time != null ? `${fmtTime(s.mc_remaining_time * 60)} left · ${s.mc_percent}%` : '–';
  S.ams = s.ams || [];
  if (JSON.stringify(S.ams.map((t) => t.type + t.color)) !== S.runoutSig) { S.runoutSig = JSON.stringify(S.ams.map((t) => t.type + t.color)); renderRunoutSlots(); }
  const box = $('#dev-ams');
  box.innerHTML = '';
  if (!s.connected) box.append(el('span', { class: 'muted small' }, 'Connect to see the loaded filaments'));
  else if (!S.ams.length) box.append(el('span', { class: 'muted small' }, 'No AMS lite reported (yet)'));
  for (const tr of S.ams) {
    const c = normHex(tr.color) || '#2e3138';
    box.append(el('div', { class: 'ams-tray' + (tr.active ? ' active' : ''), title: tr.name || tr.type || 'Empty' },
      el('span', { class: 'swatch', style: `background:${c};color:${contrast(c)}` }, String(tr.index + 1)),
      el('span', {}, tr.type || 'Empty'),
      el('span', { class: 'muted small' }, tr.remain != null && tr.remain >= 0 ? `${tr.remain}%` : '')));
  }
  $('#dev-ams-sync').disabled = !S.ams.some((x) => x.type);
  $$('#dev-speed button').forEach((b) => b.classList.toggle('on', s.connected && +b.dataset.lvl === +s.spd_lvl));
  $$('#dev-speed button').forEach((b) => (b.disabled = !s.connected));
  $$('.temp-set button, .temp-set input').forEach((x) => (x.disabled = !s.connected));
  for (const [w, cur] of [['nozzle', s.nozzle_target_temper], ['bed', s.bed_target_temper]]) {
    const inp = $(`.temp-set[data-what="${w}"] input`);
    if (document.activeElement !== inp && !inp.dataset.edited) inp.value = cur != null ? Math.round(cur) : '';
  }
  for (const [n, f] of Object.entries(s.cameras || {})) {
    const box = $('#cam-' + n);
    if (box) box.querySelector('.cam-msg').textContent = f.error && !box.classList.contains('hidden') ? f.error : '';
  }
  if (S.devConnected !== s.connected) { S.devConnected = s.connected; renderAmsMap(); }
  else if (JSON.stringify(S.ams.map((t) => t.type + t.color)) !== S.amsSig) renderAmsMap();
}
// file filament -> AMS slot (or the external spool), chosen before printing
function renderAmsMap() {
  const box = $('#dev-map');
  if (!box) return;
  S.amsSig = JSON.stringify((S.ams || []).map((t) => t.type + t.color));
  box.innerHTML = '';
  const fil = S.fileFilaments || [];
  if (!fil.length) { box.append(el('p', { class: 'muted small' }, 'Slice the plate to choose which AMS slot prints each filament.')); return; }
  box.append(el('div', { class: 'muted small' }, 'Filament → AMS slot'));
  S.amsMap = S.amsMap || {};
  const trays = (S.ams || []).filter((t) => t.type);
  for (const f of fil) {
    const sel = el('select', {});
    for (const t of trays) sel.append(el('option', { value: t.index }, `AMS ${t.index + 1} · ${t.type}${t.name ? ' ' + t.name : ''}`));
    if (fil.length === 1) sel.append(el('option', { value: -1 }, 'External spool'));
    let v = S.amsMap[f.slot];
    if (v == null || (v >= 0 && !trays.some((t) => t.index === v))) {
      const same = trays.find((t) => t.index === f.slot && t.type === f.type) || trays.find((t) => t.type === f.type) || trays.find((t) => t.index === f.slot) || trays[0];
      v = same ? same.index : -1;
    }
    sel.value = String(v); S.amsMap[f.slot] = +sel.value;
    sel.onchange = () => { S.amsMap[f.slot] = +sel.value; checkMap(); };
    const c = normHex(f.color) || '#808080';
    box.append(el('div', { class: 'map-row' },
      el('span', { class: 'swatch', style: `background:${c};color:${contrast(c)}` }, String(f.slot + 1)),
      el('span', { class: 'map-type' }, f.type), el('span', { class: 'muted' }, '→'), sel));
  }
  box.append(el('div', { class: 'map-warn small', id: 'map-warn' }));
  checkMap();
}
function checkMap() {
  const w = $('#map-warn'); if (!w) return;
  const msgs = [];
  for (const f of S.fileFilaments || []) {
    const t = (S.ams || []).find((x) => x.index === S.amsMap[f.slot]);
    if (t && t.type && t.type !== f.type) msgs.push(`Filament ${f.slot + 1} is ${f.type} but AMS ${t.index + 1} holds ${t.type}`);
  }
  w.textContent = msgs.join(' · ');
}
function startDevPoll() { clearInterval(devTimer); devPoll(); devTimer = setInterval(devPoll, 2000); }

// ------------------------------------------------------------------ views
// ------------------------------------------------------------------ live view of the print on the printer
// The preview of the print last sent from this app, following the printer: layers done, the layer being printed
// drawn up to where the nozzle should be (estimated from the time since the printer started that layer).
const SPEED_FACTOR = { 1: 0.5, 2: 1, 3: 1.24, 4: 1.66 };      // Silent / Standard / Sport / Ludicrous
async function liveStart() {
  let d;
  try { d = await api('live-print'); } catch (e) { return toast(e.message, true); }
  if (!d || !d.preview) return toast('Send a print from this app first: the live view shows the print you sent.', true);
  simStop(); stopPlay();
  if (!LIVE.on) LIVE.saved = P && S.sliced ? [P.data, P.stats, P.coll] : null;
  buildPreview(d.preview, d.stats || {}, d.collision || {});
  LIVE.on = true; LIVE.layer = 0; LIVE.name = d.name || ''; LIVE.layers = d.preview.layers.length;
  $('#preview-empty').classList.add('hidden');
  $('#layer-slider').classList.remove('hidden');
  $('#sim-btn').classList.add('hidden'); $('#move-slider').classList.add('hidden');
  $('#live-bar').classList.remove('hidden');
  $('#show-head').checked = true;
  setView('preview');
  $('#calib-panel').classList.add('hidden');
  liveCam(store.get('live.cam', '') || '');
  cancelAnimationFrame(LIVE.raf);
  LIVE.raf = requestAnimationFrame(liveLoop);
}
function liveStop(restore = true) {
  if (!LIVE.on) return;
  LIVE.on = false;
  cancelAnimationFrame(LIVE.raf);
  $('#live-bar').classList.add('hidden');
  $('#live-cam-img').removeAttribute('src');
  if (S.calib) renderCalibPanel();
  if (!restore) return;
  if (LIVE.saved) {
    buildPreview(...LIVE.saved);
    $('#sim-btn').classList.remove('hidden'); $('#move-slider').classList.remove('hidden');
  } else {
    previewGroup.clear(); P = null;
    $('#legend').innerHTML = '';
    $('#preview-empty').classList.remove('hidden');
    $('#layer-slider').classList.add('hidden'); $('#move-slider').classList.add('hidden'); $('#sim-btn').classList.add('hidden');
  }
  LIVE.saved = null;
}
function liveLoop(now) {
  if (!LIVE.on || !P) return;
  LIVE.raf = requestAnimationFrame(liveLoop);
  const s = S.devStatus || {};
  const n = P.data.layers.length;
  const state = s.gcode_state || '';
  const match = !s.total_layer_num || +s.total_layer_num === n;
  let L = Math.max(1, Math.min(n, +s.layer_num || 1));
  if (state === 'FINISH') L = n;
  if (L !== LIVE.layer) { LIVE.layer = L; LIVE.layerAt = now; $('#layer-range').value = L; updateLayers(true); }
  const [s0, s1] = layerSegs(L);
  let m = s1 - s0;
  const running = state === 'RUNNING' && +s.layer_num > 0;
  if (running) {                                   // where the nozzle should be in this layer
    const f = SPEED_FACTOR[s.spd_lvl] || 1;
    const t = P.cum[s0] + ((now - LIVE.layerAt) / 1000) * f / (P.simScale || 1);
    const g = Math.min(s1, Math.max(s0, lowerBound(P.cum, t) - 1));
    m = Math.max(1, g - s0);
  } else if (!+s.layer_num && state !== 'FINISH') m = 0;
  const mr = $('#move-range');
  if (+mr.value !== m) { mr.value = m; updateLayers(); }
  if ($('#live-follow').checked && head) { controls.target.lerp(head.position, 0.08); controls.update(); }
  const pct = s.mc_percent != null ? `${s.mc_percent}%` : '';
  const left = s.mc_remaining_time != null ? `${fmtTime(s.mc_remaining_time * 60)} left` : '';
  const what = !s.connected ? 'Printer not connected: connect in the Device tab'
    : !match ? `⚠ The printer is printing something else (${s.total_layer_num} layers; the print sent from here has ${n})`
    : state === 'FINISH' ? 'Finished' : state === 'PAUSE' ? 'Paused' : state === 'PREPARE' || !+s.layer_num ? 'Preparing (heating, levelling…)'
    : state === 'FAILED' ? 'Stopped' : state === 'RUNNING' ? 'Printing' : state || 'Waiting for the printer…';
  $('#live-text').textContent = `${LIVE.name || 'Print'} · Layer ${+s.layer_num || 0} / ${n} · ${[pct, left].filter(Boolean).join(' · ')}`;
  $('#live-progress').style.width = (s.mc_percent || 0) + '%';
  const v = P.idx[2 * Math.max(s0, s0 + m - 1) + 1], kname = P.data.kinds[P.kind[v]];
  $('#live-where').textContent = running ? `${what} · now: ${(KIND_STYLE[kname] || [kname])[0]} (estimated position)` : what;
}
$('#dev-live').onclick = liveStart;
$('#live-close').onclick = () => liveStop(true);
// camera next to the live view: the printer's or your own (Tapo…); the choice is remembered
function liveCam(n) {
  const img = $('#live-cam-img'), pick = $('#live-cam');
  if (n === 'extra' && !S.appInfo?.camera_url) { toast('Set up your camera first: Device → Cameras → ⚙ Set up', true); n = ''; }
  pick.value = n;
  store.set('live.cam', n);
  img.classList.toggle('hidden', !n);
  if (n && LIVE.on) img.src = `/camera/${n}.mjpg?t=${Date.now()}`; else img.removeAttribute('src');
}
$('#live-cam').onchange = (e) => liveCam(e.target.value);

// ------------------------------------------------------------------ when a spool runs out
const RUNOUT_NOTES = {
  pause: 'The printer pauses and waits for you to load filament (Bambu\'s default).',
  printer: 'The printer switches by itself to another slot with the same filament (same type and colour). It picks the slot; this works even with the app closed.',
  order: 'The app switches to the next slot in your order and resumes, whatever filament is in it (keep the app open, it can be minimised). Slots after the last one in your order are not used. Use the same type of filament: the temperature stays the same.',
};
// the order: tap slots one after another (3, then 4, then 2 -> 3 > 4 > 2)
let runoutPick = [];
function renderRunoutSlots() {
  const box = $('#runout-slots');
  box.innerHTML = '';
  const trays = S.ams && S.ams.length ? S.ams : [0, 1, 2, 3].map((i) => ({ index: i }));
  for (const tr of trays) {
    const n = tr.index + 1, pos = runoutPick.indexOf(n);
    const c = normHex(tr.color) || '#2e3138';
    box.append(el('button', { class: 'runout-slot' + (pos >= 0 ? ' on' : ''), title: tr.type ? `Slot ${n}: ${tr.type}` : `Slot ${n}`,
      onclick: () => { pos >= 0 ? runoutPick.splice(pos, 1) : runoutPick.push(n); syncRunoutPick(); } },
      el('span', { class: 'swatch', style: `background:${c};color:${contrast(c)}` }, String(n)),
      el('span', { class: 'small' }, tr.type || ''),
      pos >= 0 ? el('b', { class: 'runout-badge' }, String(pos + 1)) : ''));
  }
}
function syncRunoutPick() {
  $('#runout-order').value = runoutPick.join('>');
  const seq = $('#runout-seq');
  seq.innerHTML = '';
  if (!runoutPick.length) seq.append(el('span', { class: 'muted' }, 'No order yet'));
  runoutPick.forEach((n, i) => { if (i) seq.append(el('span', { class: 'muted' }, ' › ')); seq.append(el('b', { class: 'runout-chip' }, String(n))); });
  renderRunoutSlots();
}
function showRunout(cfg) {
  $('#runout-mode').value = cfg.mode || 'pause';
  runoutPick = [...(cfg.order || [1, 2, 3, 4])];
  syncRunoutPick();
  $('#runout-order-row').classList.toggle('hidden', $('#runout-mode').value !== 'order');
  $('#runout-note').textContent = RUNOUT_NOTES[$('#runout-mode').value];
}
async function saveRunout() {
  const mode = $('#runout-mode').value;
  try {
    const r = await api('runout', { mode, order: $('#runout-order').value });
    showRunout({ mode, order: r.order });
    toast(mode === 'order' ? `Runout order saved: ${r.order.join(' > ')}` : mode === 'printer' ? 'Printer auto refill on' : 'The printer will pause on a runout');
    if (!r.printer_told) toast('Connect to the printer so it gets the setting too', true);
  } catch (e) { toast(e.message, true); }
}
$('#runout-mode').onchange = () => {
  showRunout({ mode: $('#runout-mode').value, order: runoutPick.length ? runoutPick : [1, 2, 3, 4] });
  if ($('#runout-mode').value !== 'order') saveRunout();
};
$('#runout-save').onclick = saveRunout;
$('#runout-clear').onclick = () => { runoutPick = []; syncRunoutPick(); };

// ------------------------------------------------------------------ sync with other PCs
function syncInfo(st) {
  const l = st.last || {};
  const when = l.time ? new Date(l.time * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
  $('#sync-info').textContent = !st.dir ? 'Sync is off.'
    : l.error ? `Last sync failed: ${l.error}`
    : when ? `On · last sync ${when}: ${l.pulled} from other PCs, ${l.pushed} sent, ${l.deleted} deleted.` : 'On · syncing…';
}
async function openSync() {
  let st;
  try { st = await api('sync/status', {}); } catch (e) { return toast(e.message, true); }
  $('#sync-dir').value = st.dir;
  const sg = $('#sync-sugg');
  sg.innerHTML = '';
  for (const s of st.suggestions || []) sg.append(el('button', { class: 'sm', title: s.path, onclick: () => { $('#sync-dir').value = s.path; } }, `☁ ${s.label}`));
  syncInfo(st);
  $('#sync-modal').classList.remove('hidden');
}
async function applySync(dir) {
  try {
    const st = await api('sync/config', { dir });
    syncInfo(st);
    S.syncChanges = st.changes;
    await reloadPresets(); useVisibleFilaments(); refreshSelects(); renderSlots();
    toast(dir ? 'Synced. Pick the same folder on your other PC.' : 'Sync turned off');
  } catch (e) { toast(e.message, true); }
}
$('#sync-save').onclick = () => applySync($('#sync-dir').value.trim());
$('#sync-off').onclick = () => { $('#sync-dir').value = ''; applySync(''); };
$('#sync-close').onclick = () => $('#sync-modal').classList.add('hidden');
$('#sync-browse').onclick = async () => {
  try { const r = await api('sync/choose', { start: $('#sync-dir').value }); if (r.path) $('#sync-dir').value = r.path; }
  catch (e) { toast(e.message, true); }
};
// presets changed on another PC: reload the lists (your unsaved edits stay)
setInterval(async () => {
  let st;
  try { st = await api('sync/status', {}); } catch { return; }
  if (!st.dir) return;
  if (S.syncChanges === undefined) { S.syncChanges = st.changes; return; }
  if (st.changes !== S.syncChanges) {
    S.syncChanges = st.changes;
    await reloadPresets(); refreshSelects(); renderSlots();
    toast('Presets updated from your other PC');
  }
}, 20000);

function setView(v) {
  S.view = v;
  document.body.classList.remove('view-prepare', 'view-preview', 'view-device');
  document.body.classList.add('view-' + v);
  $$('#tabs button').forEach((b) => b.classList.toggle('active', b.dataset.view === v));
  modelGroup.visible = v === 'prepare';
  if (v !== 'prepare') setTool(null);
  previewGroup.visible = v === 'preview';
  if (v === 'device') { updateDeviceHeader(); startDevPoll(); }
  if (S.calib) renderCalibPanel();
  resize();
  if (v !== 'device') glRecover();
}

// ------------------------------------------------------------------ object tools: move, rotate, scale, lay on face, paint support
const ID3 = [1, 0, 0, 0, 1, 0, 0, 0, 1];
const deg = THREE.MathUtils.degToRad;
function orientMat(pl) {
  const o = pl.orient || ID3, m = new THREE.Matrix4();
  m.set(o[0], o[1], o[2], 0, o[3], o[4], o[5], 0, o[6], o[7], o[8], 0, 0, 0, 0, 1);
  return m;
}
function rotMat(pl) { return new THREE.Matrix4().makeRotationZ(deg(+pl.rot || 0)).multiply(orientMat(pl)); }
function setOrient(m) {
  const q = new THREE.Quaternion().setFromRotationMatrix(m).normalize();
  const e = new THREE.Matrix4().makeRotationFromQuaternion(q).elements;      // column-major
  S.placement.orient = [e[0], e[4], e[8], e[1], e[5], e[9], e[2], e[6], e[10]].map((v) => +v.toFixed(9));
}
// turn the model by W (a rotation in plate coordinates); the Z angle field keeps its value
function rotateWorld(W) {
  const Rz = new THREE.Matrix4().makeRotationZ(deg(+S.placement.rot || 0));
  setOrient(Rz.clone().invert().multiply(W).multiply(Rz).multiply(orientMat(S.placement)));
}
function placementReq() {
  const p = S.placement;
  const r = { x: +p.x, y: +p.y, scale: +p.scale, rot_z: +p.rot };
  if (p.orient && p.orient.some((v, i) => Math.abs(v - ID3[i]) > 1e-9)) r.orient = p.orient;
  return r;
}

const T = { tool: null, mode: 'enforce', brush: 4, faces: null, painting: false, hover: null };
const pivot = new THREE.Object3D();
scene.add(pivot);
const tc = new TransformControls(camera, renderer.domElement);
tc.setSize(0.85);
tc.setRotationSnap(deg(5));
tc.space = 'world';
scene.add(tc);
let drag0 = null;
tc.addEventListener('dragging-changed', (e) => {
  controls.enabled = !e.value;
  if (e.value) drag0 = { rot: +S.placement.rot || 0, orient: (S.placement.orient || ID3).slice() };
  else { drag0 = null; pivot.quaternion.identity(); placePivot(); markStale(); renderToolPanel(); }
});
tc.addEventListener('objectChange', () => {
  if (!drag0) return;
  if (T.tool === 'move') {
    S.placement.x = +pivot.position.x.toFixed(2); S.placement.y = +pivot.position.y.toFixed(2);
  } else if (T.tool === 'rotate') {
    S.placement.rot = drag0.rot; S.placement.orient = drag0.orient.slice();
    const q = pivot.quaternion;
    if (tc.axis === 'Z') S.placement.rot = +((drag0.rot + THREE.MathUtils.radToDeg(2 * Math.atan2(q.z, q.w)) + 540) % 360 - 180).toFixed(2);
    else rotateWorld(new THREE.Matrix4().makeRotationFromQuaternion(q));
  }
  syncPlacementInputs(); updateModel(); updatePanelValues();
});
function modelBox() { return modelMesh ? new THREE.Box3().setFromObject(modelMesh) : null; }
function placePivot() {
  const b = modelBox();
  if (!b) return;
  const c = b.getCenter(new THREE.Vector3());
  pivot.position.set(+S.placement.x, +S.placement.y, T.tool === 'rotate' ? c.z : 0);
  pivot.updateMatrixWorld(true);
}

// per-model face data for painting and lay-on-face (local coordinates, all parts in one list)
function modelChanged() {
  setTool(null);
  S.paint = null;
  buildFaces();
}
async function arrangeModel() {
  if (!S.model) return toast('Import a model first', true);
  if (S.calib) return toast('A calibration print is one object: nothing to arrange', true);
  if (S.parts.length < 2) return toast('The model is one part: there is nothing to arrange. Several objects come in as separate parts of one file (3MF, or an STL/OBJ with separate bodies)', true);
  const model = S.model;
  let r;
  try {
    r = await api('model/arrange', { printer: S.sel.printer, filament: S.sel.filament, process: S.sel.process,
      printer_overrides: S.work.printer, process_overrides: S.work.process, placement: placementReq() });
  } catch (e) { return toast(e.message, true); }
  if (S.model !== model) return;
  for (const m of modelMesh.children) {
    const p = r.parts[m.userData.part];
    if (!p) continue;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(b64(p.tris, Float32Array), 3));
    g.computeVertexNormals();
    m.geometry.dispose(); m.geometry = g;
  }
  model.faces = r.faces; model.size = r.size;
  S.placement.x = (+S.work.printer.bed_x) / 2; S.placement.y = (+S.work.printer.bed_y) / 2;
  if (T.faces) { buildFaces(); drawPaint(); }
  S.dirty = true;
  syncPlacementInputs(); updateModel(); placePivot(); markStale(); updatePanelValues();
  toast(r.text);
}
function buildFaces() {
  T.faces = null;
  if (!modelMesh) return;
  const parts = modelMesh.children.filter((m) => m.userData.part !== undefined);
  let total = 0;
  const per = parts.map((m) => { const pos = m.geometry.attributes.position.array; const n = pos.length / 9; const r = { mesh: m, pos, n, off: total }; total += n; return r; });
  const cen = new Float32Array(total * 3), nor = new Float32Array(total * 3), dd = new Float32Array(total);
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  for (const p of per) {
    for (let f = 0; f < p.n; f++) {
      const i = f * 9, g = p.off + f;
      a.fromArray(p.pos, i); b.fromArray(p.pos, i + 3); c.fromArray(p.pos, i + 6);
      cen[g * 3] = (a.x + b.x + c.x) / 3; cen[g * 3 + 1] = (a.y + b.y + c.y) / 3; cen[g * 3 + 2] = (a.z + b.z + c.z) / 3;
      const nn = b.clone().sub(a).cross(c.clone().sub(a)).normalize();
      nor[g * 3] = nn.x; nor[g * 3 + 1] = nn.y; nor[g * 3 + 2] = nn.z;
      dd[g] = nn.dot(a);
    }
  }
  T.faces = { per, total, cen, nor, dd };
}
function partFace(hit) {
  const p = T.faces?.per.find((q) => q.mesh === hit.object);
  return p ? p.off + hit.faceIndex : -1;
}
function hitModel(e) {
  const r = renderer.domElement.getBoundingClientRect();
  const ray = new THREE.Raycaster();
  ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  return ray.intersectObjects(modelMesh ? modelMesh.children.filter((m) => m.userData.part !== undefined) : [], false)[0] || null;
}

// overlay meshes (painted faces, lay-on-face highlight) live inside each part mesh, so they follow it
function overlay(name, build) {
  if (!T.faces) return;
  for (const p of T.faces.per) {
    const old = p.mesh.getObjectByName(name);
    if (old) { p.mesh.remove(old); old.geometry.dispose(); }
    const out = build(p);
    if (!out) continue;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(out.pos, 3));
    if (out.col) g.setAttribute('color', new THREE.Float32BufferAttribute(out.col, 3));
    const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: out.col ? 0xffffff : out.color, vertexColors: !!out.col,
      transparent: true, opacity: out.opacity ?? 0.8, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2, depthWrite: false }));
    m.name = name; m.renderOrder = 2;
    p.mesh.add(m);
  }
}
const PAINT_COL = { 1: [0.18, 0.8, 0.44], 2: [1, 0.35, 0.35] };
function drawPaint() {
  overlay('paint', (p) => {
    if (!S.paint) return null;
    const pos = [], col = [];
    for (let f = 0; f < p.n; f++) {
      const v = S.paint[p.off + f];
      if (!v) continue;
      for (let k = 0; k < 9; k++) pos.push(p.pos[f * 9 + k]);
      const c = PAINT_COL[v]; col.push(...c, ...c, ...c);
    }
    return pos.length ? { pos, col, opacity: 0.85 } : null;
  });
}
function drawFlatHighlight(faces) {
  overlay('flat', (p) => {
    if (!faces) return null;
    const pos = [];
    for (const g of faces) if (g >= p.off && g < p.off + p.n) for (let k = 0; k < 9; k++) pos.push(p.pos[(g - p.off) * 9 + k]);
    return pos.length ? { pos, color: 0x00d4b3, opacity: 0.6 } : null;
  });
}
function coplanar(g) {
  const F = T.faces, out = [];
  const nx = F.nor[g * 3], ny = F.nor[g * 3 + 1], nz = F.nor[g * 3 + 2], d = F.dd[g];
  for (let i = 0; i < F.total; i++) {
    if (F.nor[i * 3] * nx + F.nor[i * 3 + 1] * ny + F.nor[i * 3 + 2] * nz > 0.9995 && Math.abs(F.dd[i] - d) < 0.05) out.push(i);
  }
  return out;
}
function layOnFace(g) {
  const n = new THREE.Vector3(T.faces.nor[g * 3], T.faces.nor[g * 3 + 1], T.faces.nor[g * 3 + 2]).applyMatrix4(rotMat(S.placement)).normalize();
  rotateWorld(new THREE.Matrix4().makeRotationFromQuaternion(new THREE.Quaternion().setFromUnitVectors(n, new THREE.Vector3(0, 0, -1))));
  updateModel(); markStale(); placePivot();
  toast('Laid on that face');
}
function paintAt(hit) {
  const F = T.faces;
  if (!S.paint) S.paint = new Uint8Array(F.total);
  const lp = hit.object.worldToLocal(hit.point.clone());
  const r = T.brush / ((+S.placement.scale || 100) / 100), r2 = r * r;
  const v = T.mode === 'enforce' ? 1 : T.mode === 'block' ? 2 : 0;
  // only paint faces turned towards the camera side of the hit (not the far wall of a thin part)
  const hn = hit.face.normal;
  for (let i = 0; i < F.total; i++) {
    const dx = F.cen[i * 3] - lp.x, dy = F.cen[i * 3 + 1] - lp.y, dz = F.cen[i * 3 + 2] - lp.z;
    if (dx * dx + dy * dy + dz * dz > r2) continue;
    if (F.nor[i * 3] * hn.x + F.nor[i * 3 + 1] * hn.y + F.nor[i * 3 + 2] * hn.z < -0.2) continue;
    S.paint[i] = v;
  }
}
function paintCounts() {
  let e = 0, b = 0;
  if (S.paint) for (const v of S.paint) { if (v === 1) e++; else if (v === 2) b++; }
  return { e, b };
}
function paintReq() {
  if (!S.paint) return null;
  const enforce = [], block = [];
  S.paint.forEach((v, i) => { if (v === 1) enforce.push(i); else if (v === 2) block.push(i); });
  return enforce.length || block.length ? { enforce, block } : null;
}
function loadPaint(pp) {
  if (!pp || !T.faces) return;
  S.paint = new Uint8Array(T.faces.total);
  for (const i of pp.enforce || []) if (i < T.faces.total) S.paint[i] = 1;
  for (const i of pp.block || []) if (i < T.faces.total) S.paint[i] = 2;
  S.model.refined = true;                 // painted in this app: already split into small triangles
  drawPaint();
}

// brush ring under the mouse while painting
const brushRing = new THREE.Mesh(new THREE.RingGeometry(0.92, 1, 48),
  new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.85, depthTest: false, side: THREE.DoubleSide }));
brushRing.renderOrder = 5; brushRing.visible = false;
scene.add(brushRing);
function showBrush(hit) {
  if (!hit) { brushRing.visible = false; return; }
  const n = hit.face.normal.clone().transformDirection(hit.object.matrixWorld);
  brushRing.position.copy(hit.point).addScaledVector(n, 0.05);
  brushRing.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), n);
  brushRing.scale.setScalar(T.brush);
  brushRing.material.color.set(T.mode === 'enforce' ? 0x2ecc71 : T.mode === 'block' ? 0xff5a5a : 0xffffff);
  brushRing.visible = true;
}

function setTool(t) {
  if (t && (!S.model || S.view !== 'prepare')) return;
  if (t === T.tool) t = null;
  T.tool = t;
  $$('#gizmo-bar button').forEach((b) => b.classList.toggle('on', b.dataset.tool === t));
  tc.detach();
  if (t === 'move' || t === 'rotate') {
    placePivot();
    pivot.quaternion.identity();
    tc.setMode(t === 'move' ? 'translate' : 'rotate');
    tc.showX = tc.showY = true;
    tc.showZ = t === 'rotate';
    tc.attach(pivot);
  }
  brushRing.visible = false;
  drawFlatHighlight(null);
  if (t === 'paint' && !S.model.refined) refineForPaint();
  if (t === 'paint') drawPaint();
  else if (S.paint && !paintCounts().e && !paintCounts().b) S.paint = null;
  if (t !== 'paint') drawPaint();         // painted areas stay visible, dimmer tools aside
  renderToolPanel();
}
async function refineForPaint() {
  const model = S.model;
  model.refined = true;
  try {
    const r = await api('model/refine', {});
    if (S.model !== model || !r.changed) return;
    for (const m of modelMesh.children) {
      const p = r.parts[m.userData.part];
      if (!p) continue;
      for (const o of [...m.children]) { m.remove(o); o.geometry.dispose(); }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(b64(p.tris, Float32Array), 3));
      g.computeVertexNormals();
      m.geometry.dispose(); m.geometry = g;
      S.parts[m.userData.part].faces = p.faces;
    }
    model.faces = r.faces;
    S.paint = null;
    buildFaces(); updateModel(); drawPaint(); renderToolPanel();
  } catch (e) { model.refined = false; toast(e.message, true); }
}
const num = (v, d = 2) => +(+v).toFixed(d);
function sizeNow() { const b = modelBox(); return b ? b.getSize(new THREE.Vector3()) : new THREE.Vector3(); }
function updatePanelValues() {
  const P = $('#gizmo-panel');
  if (T.tool === 'move') { P.querySelector('[data-k=x]').value = num(S.placement.x); P.querySelector('[data-k=y]').value = num(S.placement.y); }
  if (T.tool === 'rotate') P.querySelector('[data-k=rot]').value = num(S.placement.rot, 1);
  if (T.tool === 'scale') {
    const s = sizeNow();
    P.querySelector('[data-k=scale]').value = num(S.placement.scale, 1);
    for (const k of ['x', 'y', 'z']) P.querySelector(`[data-k=s${k}]`).value = num(s[k], 2);
  }
}
function changed() { syncPlacementInputs(); updateModel(); placePivot(); markStale(); updatePanelValues(); }
function renderToolPanel() {
  const P = $('#gizmo-panel');
  P.classList.toggle('hidden', !T.tool);
  if (!T.tool) return;
  const keys = { move: 'M', rotate: 'R', scale: 'S', layflat: 'F', paint: 'L' };
  const titles = { move: 'Move', rotate: 'Rotate', scale: 'Scale', layflat: 'Lay on face', paint: 'Paint support' };
  P.innerHTML = '';
  P.append(el('h4', {}, titles[T.tool], el('kbd', {}, keys[T.tool])));
  const field = (k, v, step, onchange) => el('input', { type: 'number', step, value: v, 'data-k': k, onchange: (e) => onchange(+e.target.value) });
  const ax = (a) => el('span', { class: 'ax ' + a }, a.toUpperCase());
  if (T.tool === 'move') {
    for (const a of ['x', 'y']) P.append(el('div', { class: 'grow two' }, ax(a), field(a, num(S.placement[a]), 1, (v) => { S.placement[a] = v; changed(); })));
    P.append(el('div', { class: 'gbtns' }, el('button', { class: 'sm', onclick: () => $('#btn-center').click() }, '⌖ Center on plate')));
    P.append(el('div', { class: 'note' }, 'Drag the red / green arrows on the plate, or type a position (mm, centre of the model).'));
  }
  if (T.tool === 'rotate') {
    const turn = (axis, a) => { rotateWorld(new THREE.Matrix4().makeRotationAxis(axis, deg(a))); changed(); };
    const X = new THREE.Vector3(1, 0, 0), Y = new THREE.Vector3(0, 1, 0);
    P.append(el('div', { class: 'grow' }, ax('x'), el('span', {}, 'Turn'), el('button', { class: 'sm', onclick: () => turn(X, -90) }, '−90°'), el('button', { class: 'sm', onclick: () => turn(X, 90) }, '+90°')));
    P.append(el('div', { class: 'grow' }, ax('y'), el('span', {}, 'Turn'), el('button', { class: 'sm', onclick: () => turn(Y, -90) }, '−90°'), el('button', { class: 'sm', onclick: () => turn(Y, 90) }, '+90°')));
    P.append(el('div', { class: 'grow two' }, ax('z'), field('rot', num(S.placement.rot, 1), 5, (v) => { S.placement.rot = v; changed(); })));
    P.append(el('div', { class: 'gbtns' }, el('button', { class: 'sm', onclick: () => { S.placement.orient = ID3.slice(); S.placement.rot = 0; changed(); } }, '↺ Reset rotation')));
    P.append(el('div', { class: 'note' }, 'Drag a ring on the plate (5° steps). The model always sits on the plate.'));
  }
  if (T.tool === 'scale') {
    const s = sizeNow();
    P.append(el('div', { class: 'grow two' }, el('span', {}, '%'), field('scale', num(S.placement.scale, 1), 5, (v) => { if (v > 0) { S.placement.scale = v; changed(); } })));
    for (const a of ['x', 'y', 'z']) P.append(el('div', { class: 'grow two' }, ax(a), field('s' + a, num(s[a], 2), 1, (v) => {
      const now = sizeNow()[a];
      if (v > 0 && now > 0) { S.placement.scale = num(+S.placement.scale * v / now, 3); changed(); }
    })));
    P.append(el('div', { class: 'gbtns' }, el('button', { class: 'sm', onclick: () => { S.placement.scale = 100; changed(); } }, '↺ 100 %')));
    P.append(el('div', { class: 'note' }, 'Size in mm. Scaling keeps the proportions.'));
  }
  if (T.tool === 'layflat') {
    P.append(el('div', { class: 'note', style: 'margin-top:0' }, 'Move over the model: the flat face under the mouse lights up. Click it to put that face down on the plate.'));
    P.append(el('div', { class: 'gbtns' }, el('button', { class: 'sm', onclick: () => { S.placement.orient = ID3.slice(); changed(); } }, '↺ Original orientation')));
  }
  if (T.tool === 'paint') {
    const seg = el('div', { class: 'seg' });
    for (const [k, label] of [['enforce', 'Enforce'], ['block', 'Block'], ['erase', 'Erase']]) {
      seg.append(el('button', { class: k + (T.mode === k ? ' on' : ''), onclick: () => { T.mode = k; renderToolPanel(); } }, label));
    }
    P.append(seg);
    const lab = el('span', {}, `Brush ${T.brush} mm`);
    P.append(el('div', { class: 'grow two', style: 'grid-template-columns:1fr' }, lab));
    P.append(el('input', { type: 'range', min: 0.5, max: 25, step: 0.5, value: T.brush, oninput: (e) => { T.brush = +e.target.value; lab.textContent = `Brush ${T.brush} mm`; } }));
    const c = paintCounts();
    P.append(el('div', { class: 'counts' }, el('span', {}, el('b', { class: 'e' }, '■ '), `${c.e.toLocaleString()} enforced`),
      el('span', {}, el('b', { class: 'b' }, '■ '), `${c.b.toLocaleString()} blocked`)));
    P.append(el('div', { class: 'gbtns' }, el('button', { class: 'sm', onclick: () => { S.paint = null; drawPaint(); markStale(); renderToolPanel(); } }, 'Clear all')));
    P.append(el('div', { class: 'note' }, 'Left-drag on the model to paint, drag beside it to turn the view. Shift-drag erases. ' +
      'Green gets support even where the slope wouldn\'t need it, or with support switched off. Red never gets support.'));
  }
}

// mouse: painting and lay on face
renderer.domElement.addEventListener('pointerdown', (e) => {
  if (e.button !== 0 || S.view !== 'prepare' || T.tool !== 'paint' || !T.faces) return;
  const hit = hitModel(e);
  if (!hit) return;
  T.painting = true; T.paintMode0 = T.mode;
  if (e.shiftKey) T.mode = 'erase';
  controls.enabled = false;
  renderer.domElement.setPointerCapture(e.pointerId);
  paintAt(hit); drawPaint();
});
let paintFrame = 0;
renderer.domElement.addEventListener('pointermove', (e) => {
  if (S.view !== 'prepare' || !T.faces) return;
  if (T.tool === 'paint') {
    const hit = hitModel(e);
    showBrush(hit);
    if (T.painting && hit) {
      paintAt(hit);
      if (!paintFrame) paintFrame = requestAnimationFrame(() => { paintFrame = 0; drawPaint(); });
    }
  } else if (T.tool === 'layflat' && !(e.buttons & 1)) {
    const hit = hitModel(e);
    const g = hit ? partFace(hit) : -1;
    if (g !== T.hover) { T.hover = g; drawFlatHighlight(g >= 0 ? coplanar(g) : null); }
  }
});
renderer.domElement.addEventListener('pointerleave', () => { brushRing.visible = false; });
let flatDown = null;
renderer.domElement.addEventListener('pointerdown', (e) => { flatDown = [e.clientX, e.clientY]; }, true);
renderer.domElement.addEventListener('pointerup', (e) => {
  if (T.painting) {
    T.painting = false; T.mode = T.paintMode0 || T.mode;
    controls.enabled = true;
    drawPaint(); markStale(); renderToolPanel();
    return;
  }
  if (T.tool === 'layflat' && flatDown && Math.hypot(e.clientX - flatDown[0], e.clientY - flatDown[1]) < 4) {
    const hit = hitModel(e);
    if (hit) { layOnFace(partFace(hit)); T.hover = -1; drawFlatHighlight(null); }
  }
});
window.addEventListener('keydown', (e) => {
  if (S.view !== 'prepare' || e.ctrlKey || e.metaKey || e.altKey || e.target.closest('input,textarea,select')
      || !$('#modal').classList.contains('hidden') || !$('#ask').classList.contains('hidden')) return;
  const map = { m: 'move', r: 'rotate', s: 'scale', f: 'layflat', l: 'paint' };
  const k = e.key.toLowerCase();
  if (map[k]) { setTool(map[k]); e.preventDefault(); }
  if (k === 'a' && S.model) { arrangeModel(); e.preventDefault(); }
  if (e.key === 'Escape' && T.tool) setTool(null);
});
$$('#gizmo-bar button').forEach((b) => { b.onclick = () => setTool(b.dataset.tool); });

// ------------------------------------------------------------------ wiring
function wire() {
  $$('#tabs button').forEach((b) => (b.onclick = () => setView(b.dataset.view)));
  for (const cat of ['printer', 'process']) $(`#${cat}-select`).onchange = (e) => {
    if (isDirty(cat) && !confirm(`Discard unsaved changes to “${S.sel[cat]}”?`)) { refreshSelects(); return; }
    selectPreset(cat, e.target.value);
  };
  $('#printer-edit').onclick = () => openEditor('printer');
  $('#filament-edit').onclick = () => openEditor('filament');
  $('#filament-add').onclick = addSlot;
  $('#ams-sync').onclick = async () => {
    try {
      const s = await api('device/status');
      if (!s.connected) return toast('Connect to your printer in the Device tab first', true);
      syncFromAms(s.ams);
    } catch (e) { toast(e.message, true); }
  };
  $('#dev-ams-sync').onclick = () => syncFromAms(S.ams);
  $('#process-reset').onclick = (e) => openMenu(e.currentTarget, [
    { content: ['↺', 'Reset unsaved changes'], onclick: () => resetWork('process') },
    { content: ['⟲', 'Restore factory defaults…'], onclick: () => restoreDefaults('process').catch((er) => toast(er.message, true)) },
  ]);
  $('#modal-reset').onclick = () => resetWork(modal.cat);
  $('#modal-restore').onclick = () => restoreDefaults(modal.cat).catch((e) => toast(e.message, true));
  $('#add-change').onclick = (e) => addToolChange(e.currentTarget);
  // click a part on the plate to select it; 1-4 sets its filament (all parts if none is selected)
  let down = null;
  renderer.domElement.addEventListener('pointerdown', (e) => (down = [e.clientX, e.clientY]));
  renderer.domElement.addEventListener('pointerup', (e) => {
    if (!down || S.view !== 'prepare' || !modelMesh || T.tool === 'layflat' || T.tool === 'paint' || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;
    const r = renderer.domElement.getBoundingClientRect();
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
    const hit = ray.intersectObjects(modelMesh.children, false)[0];
    selectPart(hit ? hit.object.userData.part : -1);
  });
  $('#process-save').onclick = () => savePreset('process').catch((e) => toast(e.message, true));
  $('#adv-toggle').checked = S.advanced;
  $('#adv-toggle').onchange = (e) => {
    S.advanced = e.target.checked; store.set('advanced', S.advanced);
    renderProcess();
    if (!$('#modal').classList.contains('hidden')) renderModal();
  };
  $('#process-saveas').onclick = () => savePreset('process', true).catch((e) => toast(e.message, true));
  $('#process-delete').onclick = () => deletePreset('process').catch((e) => toast(e.message, true));

  $('#modal-close').onclick = () => { $('#modal').classList.add('hidden'); refreshSelects(); };
  $('#modal').onclick = (e) => { if (e.target.id === 'modal') $('#modal-close').onclick(); };
  $('#modal-save').onclick = () => savePreset(modal.cat).then(renderModal).catch((e) => toast(e.message, true));
  $('#modal-saveas').onclick = () => savePreset(modal.cat, true).then(renderModal).catch((e) => toast(e.message, true));
  $('#modal-delete').onclick = () => deletePreset(modal.cat).then(renderModal).catch((e) => toast(e.message, true));
  $('#modal-preset').onchange = (e) => {
    if (isDirty(modal.cat) && !confirm(`Discard unsaved changes to “${S.sel[modal.cat]}”?`)) return renderModal();
    selectPreset(modal.cat, e.target.value); renderModal();
  };

  $('#btn-import').onclick = () => (S.hasWindow ? openDialog('model') : $('#file-model').click());
  $('#file-model').onchange = (e) => { const f = e.target.files[0]; e.target.value = ''; importFile(f, true); };
  $('#btn-file').onclick = (e) => fileMenu(e.currentTarget);
  $('#btn-calib').onclick = (e) => calibMenu(e.currentTarget);
  $('#printer-choose').onclick = () => openChooser(false, 'printer');
  $('#filament-choose').onclick = () => openChooser(false, 'filament');
  {
    const i = S.appInfo || {};
    if (i.donate_url) {
      const b = $('#btn-donate');
      b.hidden = false;
      b.onclick = () => api('donate', {}).then(() => toast('Opened in your browser. Thank you! ☕'));
    }
    const nag = i.donate_url && !i.supporter;
    const next = () => {
      if (!S.visible) { S.nagPending = nag; setTimeout(() => openChooser(true), 300); }   // first start: pick printers first
      else if (nag) setTimeout(showNag, 1800);
    };
    if (!i.intro_seen) setTimeout(() => showIntro(next), 300);                         // first start: the intro
    else next();
  }
  $('#calib-modal').onclick = (e) => { if (e.target.id === 'calib-modal') $('#calib-modal').classList.add('hidden'); };
  window.addEventListener('keydown', (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey || !$('#ask').classList.contains('hidden')) return;
    const k = e.key.toLowerCase();
    const act = { n: newProject, o: () => openDialog('project'), s: () => saveProject(e.shiftKey), i: () => openDialog('model'),
      g: () => exportKind(plateKind()) }[k];
    if (act) { e.preventDefault(); act(); }
  });
  window.addEventListener('beforeunload', (e) => { if (!S.hasWindow && S.dirty && S.model) { e.preventDefault(); e.returnValue = ''; } });
  for (const k of ['x', 'y', 'scale', 'rot']) $('#obj-' + k).onchange = (e) => { S.placement[k] = +e.target.value; updateModel(); markStale(); };
  $('#btn-center').onclick = () => { S.placement.x = (+S.work.printer.bed_x) / 2; S.placement.y = (+S.work.printer.bed_y) / 2; syncPlacementInputs(); updateModel(); markStale(); };
  $('#btn-arrange').onclick = arrangeModel;
  $('#btn-remove').onclick = async () => { if (!(await guardUnsaved('removing the model'))) return; setModel(null); markStale(); S.projectPath = null; S.dirty = false; S.calib = null; renderCalibPanel(); reportState(); };

  const stage = $('#stage');
  stage.addEventListener('dragover', (e) => { e.preventDefault(); stage.classList.add('dragover'); });
  stage.addEventListener('dragleave', (e) => { if (e.target === stage || !stage.contains(e.relatedTarget)) stage.classList.remove('dragover'); });
  stage.addEventListener('drop', (e) => { e.preventDefault(); stage.classList.remove('dragover'); importFile(e.dataTransfer.files[0]); });

  wireHelp();
  wireReport();
  setTimeout(checkUpdate, 4000);
  $('#btn-slice').onclick = slice;
  $('#btn-export').onclick = exportPlate;
  $('#btn-print').onclick = () => (isBambu() ? setView('device') : exportKind('gcode'));
  $('#layer-range').oninput = () => updateLayers(true);
  $('#single-layer').onchange = () => updateLayers();
  $('#move-range').oninput = () => { stopPlay(); updateLayers(); };
  $('#move-play').onclick = (e) => { e.currentTarget.blur(); togglePlay(); };
  $('#show-head').checked = store.get('showHead', true);
  $('#show-head').onchange = (e) => { store.set('showHead', e.target.checked); updateLayers(); };
  window.addEventListener('keydown', (e) => {
    if (S.view === 'prepare' && S.model && /^[1-4]$/.test(e.key) && !e.target.closest('input,textarea,select')
        && $('#modal').classList.contains('hidden')) return setPartTool(S.selPart, +e.key - 1);
    if (S.view !== 'preview' || !P || e.target.closest('input,textarea,select')) return;
    if (SIM.on) {
      if (e.key === ' ') { simToggle(); e.preventDefault(); }
      if (e.key === 'Escape') simStop();
      return;
    }
    const r = $('#layer-range');
    if (e.key === 'ArrowUp') { r.value = Math.min(+r.max, +r.value + 1); updateLayers(true); }
    if (e.key === 'ArrowDown') { r.value = Math.max(1, +r.value - 1); updateLayers(true); }
    const mr = $('#move-range'), stepN = e.shiftKey ? 10 : 1;
    if (e.key === 'ArrowRight') { stopPlay(); mr.value = Math.min(+mr.max, +mr.value + stepN); updateLayers(); e.preventDefault(); }
    if (e.key === 'ArrowLeft') { stopPlay(); mr.value = Math.max(0, +mr.value - stepN); updateLayers(); e.preventDefault(); }
    if (e.key === ' ') { togglePlay(); e.preventDefault(); }
  });

  $('#dev-connect').onclick = async () => {
    $('#dev-connect').disabled = true; $('#dev-conn').textContent = 'Connecting…';
    try {
      if (isDirty('printer')) await savePreset('printer');
      await api('device/connect', { printer: S.sel.printer }); toast('Connected');
    } catch (e) { toast(e.message, true); }
    $('#dev-connect').disabled = false; devPoll();
  };
  $('#dev-disconnect').onclick = () => api('device/disconnect', {}).then(devPoll);
  // send options are remembered (in the app's settings, so they survive updates)
  const so = S.appInfo?.send_options || {};
  $$('[data-opt]').forEach((x) => {
    if (so[x.dataset.opt] != null) x.checked = !!so[x.dataset.opt];
    x.onchange = () => api('send-options', sendOptions()).catch(() => {});
  });
  // print speed
  $$('#dev-speed button').forEach((b) => (b.onclick = () => api('device/speed', { level: +b.dataset.lvl })
    .then(() => { toast('Speed: ' + b.textContent); $$('#dev-speed button').forEach((x) => x.classList.toggle('on', x === b)); })
    .catch((e) => toast(e.message, true))));
  // temperatures
  $$('.temp-set').forEach((row) => {
    const inp = row.querySelector('input');
    inp.oninput = () => (inp.dataset.edited = '1');
    const send = (v) => api('device/temp', { what: row.dataset.what, temp: v })
      .then(() => { toast(`${row.dataset.what === 'nozzle' ? 'Nozzle' : 'Bed'} → ${v} °C`); delete inp.dataset.edited; })
      .catch((e) => toast(e.message, true));
    const clamp = (v) => Math.min(+inp.max, Math.max(0, Math.round(v)));
    row.querySelectorAll('[data-d]').forEach((b) => (b.onclick = () => {
      const v = clamp((+inp.value || 0) + +b.dataset.d); inp.value = v; inp.dataset.edited = '1'; send(v);
    }));
    const typed = () => {
      if (inp.value === '' || isNaN(+inp.value)) return;
      const v = clamp(+inp.value); inp.value = v; send(v); inp.blur();
    };
    row.querySelector('[data-set]').onclick = typed;
    inp.onfocus = () => inp.select();
    inp.onkeydown = (e) => {
      if (e.key === 'Enter') typed();
      else if (e.key === 'Escape') { delete inp.dataset.edited; inp.blur(); }
    };
  });
  // cameras
  const camOn = (n, on) => {
    const box = $('#cam-' + n), img = box.querySelector('img');
    box.classList.toggle('hidden', !on);
    img.src = on ? `/camera/${n}.mjpg?t=${Date.now()}` : '';
    if (!on) img.removeAttribute('src');
    store.set('cam.' + n, on);
  };
  $$('.cam').forEach((box) => {
    const full = () => (document.fullscreenElement ? document.exitFullscreen() : box.requestFullscreen()).catch(() => {});
    box.querySelector('.cam-full').onclick = full;
    box.querySelector('img').ondblclick = full;
  });
  for (const n of ['printer', 'extra']) {
    const t = $(`#cam-${n}-on`);
    t.checked = store.get('cam.' + n, false);
    t.onchange = () => {
      if (n === 'extra' && t.checked && !S.appInfo?.camera_url) { t.checked = false; $('#cam-form').classList.remove('hidden'); return; }
      camOn(n, t.checked);
    };
    if (t.checked) camOn(n, true);
  }
  // own camera setup: Tapo (IP + camera account) or any address
  let camKind = 'tapo';
  const setKind = (k) => {
    camKind = k;
    $$('.cam-kind button').forEach((b) => b.classList.toggle('on', b.dataset.kind === k));
    $('#cam-tapo').classList.toggle('hidden', k !== 'tapo'); $('#cam-other').classList.toggle('hidden', k === 'tapo');
  };
  const camUrl = () => {
    if (camKind !== 'tapo') return $('#cam-url').value.trim();
    const ip = $('#cam-ip').value.trim(), u = $('#cam-user').value.trim(), pw = $('#cam-pass').value;
    if (!ip) throw new Error("Fill in the camera's IP address (or press Find)");
    if (!u || !pw) throw new Error('Fill in the Camera Account user name and password from the Tapo app');
    return `rtsp://${encodeURIComponent(u)}:${encodeURIComponent(pw)}@${ip}:554/${$('#cam-q').value}`;
  };
  $$('.cam-kind button').forEach((b) => (b.onclick = () => {
    if (b.dataset.kind === 'url' && camKind === 'tapo') { try { $('#cam-url').value = camUrl(); } catch { /* not filled in yet */ } }
    setKind(b.dataset.kind);
  }));
  const fillCam = (url) => {
    $('#cam-url').value = url;
    const m = /^rtsp:\/\/([^:@\/]*):([^@\/]*)@([^:\/]+)(?::554)?\/(stream[12])$/i.exec(url);
    if (m) {
      const dec = (x) => { try { return decodeURIComponent(x); } catch { return x; } };
      $('#cam-user').value = dec(m[1]); $('#cam-pass').value = dec(m[2]); $('#cam-ip').value = m[3]; $('#cam-q').value = m[4];
      setKind('tapo');
    } else setKind(url ? 'url' : 'tapo');
  };
  fillCam(S.appInfo?.camera_url || '');
  const camMsg = (t, bad) => { const m = $('#cam-test-msg'); m.textContent = t; m.classList.toggle('bad', !!bad); };
  $('#cam-find').onclick = async (e) => {
    const b = e.currentTarget, box = $('#cam-found');
    b.disabled = true; box.textContent = 'Searching the network…';
    try {
      const r = await api('camera/scan', {});
      box.innerHTML = '';
      if (!r.found.length) box.textContent = 'No camera found. Check that it is on the same Wi-Fi, or type its IP (Tapo app → camera → ⚙ → Device Info).';
      for (const c of r.found) box.append(el('button', { class: 'sm', onclick: () => { $('#cam-ip').value = c.ip; box.innerHTML = ''; } }, `${c.name} · ${c.ip}`));
      if (r.found.length === 1 && !$('#cam-ip').value.trim()) $('#cam-ip').value = r.found[0].ip;
    } catch (err) { box.textContent = err.message; }
    b.disabled = false;
  };
  $('#cam-test').onclick = async (e) => {
    const b = e.currentTarget;
    try {
      const url = camUrl();
      b.disabled = true; camMsg('Connecting to the camera…');
      const r = await api('camera/test', { url });
      camMsg(r.ok ? `✓ Works: picture ${r.message}. Press Save.` : '✗ ' + r.message, !r.ok);
    } catch (err) { camMsg(err.message, true); }
    b.disabled = false;
  };
  $('#cam-extra-setup').onclick = () => $('#cam-form').classList.toggle('hidden');
  $('#cam-cancel').onclick = () => $('#cam-form').classList.add('hidden');
  $('#cam-save').onclick = async () => {
    try {
      const url = camKind === 'url' && !$('#cam-url').value.trim() ? '' : camUrl();
      await api('camera/config', { url });
      S.appInfo.camera_url = url;
      $('#cam-form').classList.add('hidden');
      const t = $('#cam-extra-on'); t.checked = !!url; camOn('extra', t.checked);
      toast(url ? 'Camera saved' : 'Camera removed');
    } catch (e) { toast(e.message, true); }
  };
  // build plate
  const ps = $('#plate-select');
  for (const n of S.appInfo?.plates || ['Textured PEI Plate']) ps.append(el('option', { value: n }, n));
  S.plate = S.appInfo?.plate || 'Textured PEI Plate';
  ps.value = S.plate;
  if (S.work?.printer) buildBed();
  ps.onchange = () => { S.plate = ps.value; api('plate', { plate: S.plate }).catch(() => {}); buildBed(); markStale(); };
  renderAmsMap();
  $('#dev-settings').onclick = () => { openEditor('printer'); modal.tab = 'Connection'; renderModal(); };
  $$('[data-cmd]').forEach((b) => (b.onclick = () => {
    if (b.dataset.cmd === 'stop' && !confirm('Stop the current print? It can’t be resumed.')) return;
    api('device/command', { command: b.dataset.cmd }).then(() => toast('Sent: ' + b.textContent)).catch((e) => toast(e.message, true));
  }));
  $('#dev-send').onclick = async () => {
    if (!S.sliced) return toast('Slice the plate first', true);
    if (!confirm('Upload the sliced plate to the printer and start printing now?')) return;
    $('#dev-send').disabled = true;
    toast('Uploading, then waiting for the printer to start (up to 25 s)…');
    try {
      const r = await api('device/send', { ...sendOptions(), ams_map: Object.fromEntries((S.fileFilaments || []).map((f) => [f.slot, S.amsMap?.[f.slot] ?? f.slot])) });
      toast(`Uploaded ${r.remote}: the printer started the print`);
    } catch (e) { toast(e.message, true); }
    $('#dev-send').disabled = false;
  };
}

// ------------------------------------------------------------------ start
async function init() {
  const sc = await api('schema');
  S.schema = sc.schema; S.percent = new Set(sc.percent);
  await reloadPresets();
  const saved = store.get('sel', {});
  const RENAMED = { '0.16mm Fine @A1': '0.16mm Optimal @A1', '0.28mm Draft @A1': '0.28mm Extra Draft @A1' };   // 0.5
  if (RENAMED[saved.process] && !S.presets.process[saved.process]) saved.process = RENAMED[saved.process];
  if (saved.process && !S.presets.process[saved.process]) saved.process = newProcessName(saved.process);
  for (const cat of ['printer', 'filament', 'process']) {
    const names = Object.keys(S.presets[cat]);
    const preferred = names.find((n) => /0\.20mm Standard/.test(n)) || names.find((n) => /A1 0\.4/.test(n)) || names[0];
    S.sel[cat] = names.includes(saved[cat]) ? saved[cat] : (cat === 'filament' ? (names.includes('Bambu PLA Basic') ? 'Bambu PLA Basic' : names.find((n) => /Bambu PLA/.test(n))) || names[0] : preferred);
    S.work[cat] = clone(S.presets[cat][S.sel[cat]]);
  }
  // filament slots: restore the last ones if their presets still exist
  const fil = S.presets.filament;
  const savedSlots = (store.get('slots', []) || []).filter((sl) => sl && fil[sl.preset]).slice(0, MAX_SLOTS);
  S.slots = savedSlots.length ? savedSlots.map((sl) => ({ preset: sl.preset, color: normHex(sl.color) || fil[sl.preset].color || '#cccccc' }))
    : [{ preset: S.sel.filament, color: fil[S.sel.filament].color || '#cccccc' }];
  S.active = 0;
  S.sel.filament = S.slots[0].preset;
  S.work.filament = clone(fil[S.sel.filament]);
  if (!fitsNozzle(S.sel.process)) {
    const n = Object.keys(S.presets.process).filter(fitsNozzle);
    const pick = n.find((x) => /Standard/.test(x)) || n[0];
    if (pick) { S.sel.process = pick; S.work.process = clone(S.presets.process[pick]); }
  }
  S.appInfo = await api('app-info').catch(() => ({}));
  S.visible = S.appInfo.visible || null;
  if (S.visible) S.visible.printer = expandPrinters(S.visible.printer);
  useVisibleFilaments();
  refreshSelects();
  renderProcess();
  S.placement.x = (+S.work.printer.bed_x) / 2; S.placement.y = (+S.work.printer.bed_y) / 2;
  syncPlacementInputs();
  buildBed();
  wire();
  setView('prepare');
  $('#layer-slider').classList.add('hidden'); $('#move-slider').classList.add('hidden'); stopPlay(); simStop(); $('#sim-btn').classList.add('hidden');
  (function loop() {
    requestAnimationFrame(loop);
    if (S.view === 'device' || GL.lost) return;      // hidden: don't keep the graphics card busy for hours
    controls.update(); renderer.render(scene, camera);
  })();
  reportState();
  pollPending();
  setInterval(pollPending, 600);
}
init().catch((e) => toast('Failed to start: ' + e.message, true));

// ------------------------------------------------------------------ support reminder (until you say you're a supporter)
function showNag() {
  const n = $('#nag');
  // closes only with the ✕ (or after a valid code)
  const hide = () => { n.classList.remove('show'); setTimeout(() => n.classList.add('hidden'), 250); };
  $('#nag-x').onclick = hide;
  $('#nag-go').onclick = () => {
    api('donate', {}).catch(() => {});
    $('#nag-title').textContent = 'Thank you! 💚';
    $('#nag-main').innerHTML = '<p>Ko-fi opened in your browser. After you donate, Ko-fi shows you a <b>supporter code</b> ' +
      '(it\u2019s also in the email). Enter it below and this window won\u2019t show again.</p>';
    $('#nag-already').click();
  };
  $('#nag-already').onclick = (e) => {
    e.preventDefault();
    $('#nag-already').classList.add('hidden');
    $('#nag-code').classList.remove('hidden');
    $('#nag-code-in').focus();
  };
  $('#nag-code').onsubmit = async (e) => {
    e.preventDefault();
    $('#nag-err').textContent = '';
    try {
      await api('supporter', { code: $('#nag-code-in').value });
      hide(); toast('Thank you for supporting ArcLayer! 💚');
    } catch (er) { $('#nag-err').textContent = er.message; }
  };
  n.classList.remove('hidden');
  requestAnimationFrame(() => n.classList.add('show'));
}

// ------------------------------------------------------------------ printers & filaments chooser (which built-in presets the lists show)
const CH = { tab: 'printer', brand: '', q: '', pick: null, first: false };
function printerModels() {           // built-in printers, one entry per model, with a preset per nozzle size
  const P = S.presets.printer, out = {};
  for (const [n, p] of Object.entries(P)) {
    if (!p._builtin) continue;
    const m = p.model || n;
    (out[m] ||= { id: m, vendor: p.vendor || 'Custom', name: m.startsWith(p.vendor) ? m.slice(p.vendor.length).trim() || m : m,
      sub: `${p.bed_x} × ${p.bed_y} × ${p.max_z} mm`, form: p._form || 'bedslinger', nozzles: {} }).nozzles[(+p.nozzle_diameter).toFixed(1)] = n;
  }
  return Object.values(out);
}
// old saved choices (0.7.2) listed printer models: show what they showed then (A1 / A1 mini: all nozzles, others 0.4)
function expandPrinters(list) {
  const P = S.presets.printer, out = new Set();
  for (const x of list || []) {
    if (P[x]) { out.add(x); continue; }
    for (const [n, p] of Object.entries(P)) {
      if (p.model === x && (Math.abs(+p.nozzle_diameter - 0.4) < 1e-6 || /^Bambu Lab A1/.test(x))) out.add(n);
    }
  }
  return [...out];
}
// brand colours for the printer pictures (a colour only: no logos)
const BRAND_COL = { 'Bambu Lab': '#00ae42', Prusa: '#fa6831', Creality: '#2f80ed', Anycubic: '#3d7bff', Elegoo: '#00a6e0',
  Sovol: '#e8442e', Voron: '#d81f26', RatRig: '#7b2ff7', Qidi: '#2563eb', Artillery: '#16a34a', Flashforge: '#f59e0b',
  Anker: '#00a9e0', Snapmaker: '#6b7280', Tronxy: '#ef4444', 'Two Trees': '#22c55e', Kingroon: '#f97316', Mingda: '#06b6d4',
  Custom: '#9ca3af' };
const brandColor = (v) => BRAND_COL[v] || `hsl(${[...String(v)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7)} 60% 52%)`;
// a drawn picture of the printer's type (bed slinger, one-arm, open cube, enclosed)
function printerArt(form, color) {
  const F = '#3a3f48', L = '#565d69', B = '#2b2f36', G = '#9aa3b0';
  const head = (x, y) => `<rect x="${x - 7}" y="${y - 6}" width="14" height="11" rx="2" fill="${color}"/><path d="M${x - 2} ${y + 5}h4l-2 4z" fill="${G}"/>`;
  const bed = (x, y, w) => `<rect x="${x}" y="${y}" width="${w}" height="4" rx="1.5" fill="${color}" opacity=".85"/>`;
  const art = {
    bedslinger: `<rect x="14" y="70" width="92" height="8" rx="3" fill="${F}"/>${bed(30, 62, 60)}<rect x="22" y="14" width="6" height="56" rx="2" fill="${L}"/>
      <rect x="92" y="14" width="6" height="56" rx="2" fill="${L}"/><rect x="18" y="10" width="84" height="6" rx="2" fill="${L}"/>
      <rect x="22" y="32" width="76" height="5" rx="2" fill="${F}"/>${head(60, 40)}<circle cx="104" cy="22" r="9" fill="none" stroke="${G}" stroke-width="3"/>`,
    cantilever: `<rect x="18" y="70" width="80" height="8" rx="3" fill="${F}"/>${bed(40, 62, 50)}<rect x="22" y="12" width="9" height="58" rx="2" fill="${L}"/>
      <rect x="26" y="30" width="70" height="6" rx="2" fill="${L}"/>${head(64, 39)}<circle cx="100" cy="20" r="9" fill="none" stroke="${G}" stroke-width="3"/>`,
    cube: `<path d="M24 14h72v62H24z" fill="${B}" stroke="${L}" stroke-width="4" stroke-linejoin="round"/>
      <path d="M24 14l10-6h72l-10 6M96 14l10-6v62l-10 6" fill="none" stroke="${L}" stroke-width="3" stroke-linejoin="round"/>
      <rect x="28" y="20" width="64" height="4" rx="1.5" fill="${F}"/>${head(58, 28)}${bed(32, 56, 56)}
      <path d="M40 60v14M80 60v14" stroke="${F}" stroke-width="3"/>`,
    enclosed: `<rect x="20" y="8" width="80" height="70" rx="7" fill="${F}"/><rect x="27" y="15" width="66" height="50" rx="4" fill="#1d2127" stroke="${L}" stroke-width="1.5"/>
      <rect x="31" y="20" width="58" height="4" rx="1.5" fill="${L}"/>${head(54, 28)}${bed(35, 54, 50)}
      <path d="M33 18l20 44" stroke="#ffffff" stroke-opacity=".07" stroke-width="8"/>
      <rect x="27" y="68" width="16" height="6" rx="1.5" fill="${color}" opacity=".9"/><rect x="88" y="68" width="5" height="6" rx="1" fill="${L}"/>`,
  };
  return `<svg viewBox="0 0 120 86" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${art[form] || art.bedslinger}</svg>`;
}
function printerPicture(it) {
  const img = (S.appInfo?.printer_images || {})[it.id];
  const box = el('div', { class: 'ch-art' });
  if (img) box.append(el('img', { src: 'printer-images/' + encodeURIComponent(img), alt: '' }));
  else box.innerHTML = printerArt(it.form, brandColor(it.vendor));
  return box;
}
function filamentItems() {
  return Object.entries(S.presets.filament).filter(([, f]) => f._builtin).map(([n, f]) => ({
    id: n, vendor: f.vendor || 'Generic', name: n.startsWith(f.vendor === 'Bambu Lab' ? 'Bambu' : f.vendor) ? n.slice((f.vendor === 'Bambu Lab' ? 'Bambu' : f.vendor).length).trim() : n,
    sub: `${f.type} · ${f.nozzle_temp} °C / bed ${f.bed_temp} °C`, color: f.color }));
}
const chItems = () => (CH.tab === 'printer' ? printerModels() : filamentItems());
const brandRank = (v) => (v === 'Bambu Lab' ? '1' : v === 'Generic' ? '2' : v === 'Custom' ? '4' : '3' + v.toLowerCase());
function defaultVisible() {
  const pm = S.presets.printer[S.sel.printer];
  const fil = ['Bambu PLA Basic', 'Generic PLA', 'Generic PETG', 'Generic TPU', 'Generic ABS', 'Generic ASA', ...S.slots.map((s) => s.preset)];
  return { printer: [...new Set(['Bambu Lab A1 0.4 nozzle', pm && pm._builtin ? S.sel.printer : null].filter((n) => n && S.presets.printer[n]))],
    filament: [...new Set(fil.filter((n) => S.presets.filament[n]?._builtin))] };
}
function openChooser(first = false, tab = 'printer') {
  const v = S.visible || defaultVisible();
  CH.pick = { printer: new Set(expandPrinters(v.printer)), filament: new Set(v.filament) };
  CH.first = first; CH.tab = tab; CH.brand = ''; CH.q = '';
  $('#chooser-search').value = '';
  $('#chooser').classList.remove('hidden');
  renderChooser();
}
function renderChooser() {
  const pr = CH.tab === 'printer';
  $$('#chooser-steps span').forEach((b) => b.classList.toggle('on', b.dataset.step === CH.tab));
  $('#chooser-title').textContent = (CH.first ? 'Welcome! ' : '') + (pr ? 'Which printers do you use?' : 'Which filaments do you use?');
  $('#chooser-sub').textContent = pr
    ? 'Tick your printers and pick their nozzle sizes. Only these show in the Printer list. Next: filaments.'
    : 'Tick the filaments you print with. Only these show in the Filament lists. Your own saved presets always show.';
  $('#chooser-search').placeholder = pr ? 'Search printers…' : 'Search filaments…';
  $('#chooser-back').classList.toggle('hidden', pr);
  $('#chooser-next').classList.toggle('hidden', !pr);
  $('#chooser-done').classList.toggle('hidden', pr);
  $('#chooser-cancel').classList.toggle('hidden', CH.first && !pr);
  const items = chItems(), pick = CH.pick[CH.tab];
  const brands = [...new Set(items.map((i) => i.vendor))].sort((a, b) => brandRank(a).localeCompare(brandRank(b)));
  if (CH.brand && !brands.includes(CH.brand)) CH.brand = '';
  const side = $('#chooser-brands');
  side.innerHTML = '';
  const isOn = (i) => (i.nozzles ? Object.values(i.nozzles).some((n) => pick.has(n)) : pick.has(i.id));
  const brandRow = (label, b) => {
    const all = items.filter((i) => !b || i.vendor === b), on = all.filter(isOn).length;
    side.append(el('button', { class: 'brand' + (CH.brand === b ? ' on' : ''), onclick: () => { CH.brand = b; renderChooser(); } },
      el('span', {}, label), el('span', { class: 'cnt' + (on ? ' some' : '') }, on ? `${on}/${all.length}` : String(all.length))));
  };
  brandRow(CH.tab === 'printer' ? 'All printers' : 'All filaments', '');
  for (const b of brands) brandRow(b, b);
  const q = CH.q.toLowerCase();
  const shown = items.filter((i) => (!CH.brand || i.vendor === CH.brand) && (!q || (i.vendor + ' ' + i.name + ' ' + i.sub).toLowerCase().includes(q)))
    .sort((a, b) => brandRank(a.vendor).localeCompare(brandRank(b.vendor)) || a.name.localeCompare(b.name, undefined, { numeric: true }));
  const grid = $('#chooser-grid');
  grid.innerHTML = '';
  grid.classList.toggle('printers', CH.tab === 'printer');
  let lastBrand = null;
  for (const it of shown) {
    if (!CH.brand && it.vendor !== lastBrand) { grid.append(el('div', { class: 'ch-head' }, it.vendor)); lastBrand = it.vendor; }
    if (it.nozzles) {                   // printer card: tick = 0.4 mm nozzle, chips pick the nozzle sizes
      const on = isOn(it);
      const def = it.nozzles['0.4'] || Object.values(it.nozzles)[0];
      const card = el('div', { class: 'ch-card' + (on ? ' on' : ''), title: `${it.vendor} ${it.name}`,
        onclick: () => { if (on) Object.values(it.nozzles).forEach((n) => pick.delete(n)); else pick.add(def); renderChooser(); } },
        printerPicture(it),
        el('span', { class: 'ch-check' }, on ? '✓' : ''),
        el('b', {}, it.name), el('small', {}, it.sub));
      const chips = el('div', { class: 'ch-nozzles', title: 'Nozzle sizes you use' }, el('span', { class: 'lab' }, 'Nozzle'));
      for (const [d, n] of Object.entries(it.nozzles).sort()) {
        chips.append(el('button', { class: 'chip' + (pick.has(n) ? ' on' : ''), title: `${d} mm nozzle`,
          onclick: (e) => { e.stopPropagation(); pick.has(n) ? pick.delete(n) : pick.add(n); renderChooser(); } }, d));
      }
      card.append(chips);
      grid.append(card);
      continue;
    }
    const on = pick.has(it.id);
    grid.append(el('label', { class: 'ch-item' + (on ? ' on' : '') },
      el('input', { type: 'checkbox', checked: on, onchange: (e) => { e.target.checked ? pick.add(it.id) : pick.delete(it.id); renderChooser(); } }),
      it.color ? el('span', { class: 'ch-sw', style: `background:${it.color}` }) : el('span', { class: 'ch-ic' }, '🖨'),
      el('span', { class: 'ch-txt' }, el('b', {}, it.name), el('small', {}, it.sub))));
  }
  if (!shown.length) grid.append(el('div', { class: 'ch-empty' }, 'Nothing matches the search.'));
  const ids = (i) => (i.nozzles ? [i.nozzles['0.4'] || Object.values(i.nozzles)[0]] : [i.id]);
  const allIds = (i) => (i.nozzles ? Object.values(i.nozzles) : [i.id]);
  $('#chooser-all').onclick = () => { shown.forEach((i) => { if (!isOn(i)) ids(i).forEach((n) => pick.add(n)); }); renderChooser(); };
  $('#chooser-none').onclick = () => { shown.forEach((i) => allIds(i).forEach((n) => pick.delete(n))); renderChooser(); };
  const models = new Set([...CH.pick.printer].map((n) => S.presets.printer[n]?.model)).size;
  const np = CH.pick.printer.size, nf = CH.pick.filament.size;
  $('#chooser-count').textContent = `${models} printer${models === 1 ? '' : 's'} (${np} nozzle preset${np === 1 ? '' : 's'}) · ${nf} filament${nf === 1 ? '' : 's'} selected`;
  $('#chooser-next').disabled = !np;
  $('#chooser-next').title = np ? '' : 'Pick at least one printer';
  $('#chooser-done').disabled = !np || !nf;
  $('#chooser-done').title = !np || !nf ? 'Pick at least one printer and one filament' : '';
}
async function saveChooser() {
  const vis = { printer: [...CH.pick.printer], filament: [...CH.pick.filament] };
  try { await api('visible', vis); } catch (e) { return toast(e.message, true); }
  S.visible = vis;
  $('#chooser').classList.add('hidden');
  // the selected printer was hidden: switch to the first one that shows
  const cur = S.presets.printer[S.sel.printer];
  if (cur && cur._builtin && !vis.printer.includes(S.sel.printer)) {
    const next = vis.printer.find((n) => S.presets.printer[n]?.model === cur.model) || vis.printer.find((n) => S.presets.printer[n]);
    if (next) { const s = $('#printer-select'); refreshSelects(); s.value = next; s.dispatchEvent(new Event('change')); }
  }
  useVisibleFilaments();
  refreshSelects(); renderSlots();
  toast(`Lists updated: ${vis.printer.length} printer presets, ${vis.filament.length} filaments`);
  if (CH.first && S.nagPending) { S.nagPending = false; setTimeout(showNag, 600); }
}
function closeChooser() {
  if (CH.first) return saveChooser();     // first start: keep what's ticked
  $('#chooser').classList.add('hidden');
}
const chooserStep = (t) => { CH.tab = t; CH.brand = ''; CH.q = ''; $('#chooser-search').value = ''; renderChooser(); $('#chooser-grid').scrollTop = 0; };
$('#chooser-next').onclick = () => chooserStep('filament');
$('#chooser-back').onclick = () => chooserStep('printer');
$('#chooser-search').oninput = (e) => { CH.q = e.target.value; renderChooser(); };
$('#chooser-done').onclick = saveChooser;

$('#chooser-x').onclick = closeChooser;
$('#chooser-cancel').onclick = closeChooser;

// ------------------------------------------------------------------ first-start intro
const INTRO = [
  { art: 'nozzle', title: 'Welcome to <span class="brand-arc">Arc</span>Layer',
    text: 'A slicer that prints <b>curved layers</b>. The nozzle moves up and down inside each layer and follows the shape of your model.' },
  { art: 'compare', title: 'Smooth instead of stepped',
    text: 'Flat layers turn slopes and domes into stairs. Curved layers follow them, so tops come out smooth and stronger, and overhangs can print without support.' },
  { art: 'modes', title: 'Two ways to bend the layers',
    text: '<b>Conical</b>: every layer is a cone, for parts that lean out (overhangs without support). <b>Follow the top surface</b>: flat at the bottom, bending to wavy rims, domes and curved tops. Pick it in Process → Non-planar.' },
  { art: 'steps', title: 'Import, slice, check, print',
    text: 'Drop a model on the plate, pick your printer, filament and quality, press <b>Slice plate</b>. Check the <b>Preview</b> (▶ Simulate shows the nozzle moving), then <b>Print plate</b>.' },
  { art: 'device', title: 'Your printer, from here',
    text: 'Calibration prints tune each filament in a few minutes. The <b>Device</b> tab sends prints, shows the camera and a live 3D view, and handles the AMS: Bambu, Klipper (Creality…), OctoPrint and PrusaLink.' },
  { art: 'help', title: 'Stuck? Just ask',
    text: 'The <b>💬 Help</b> button (top right) answers questions about any setting, calibration or problem, and can take you straight to it. It works without internet.' },
];

function introArt(kind) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 360 170');
  svg.classList.add('intro-art');
  const path = (d, c, w = 7, extra = '') => `<path d="${d}" fill="none" stroke="${c}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" ${extra}/>`;
  const wave = (y, a, ph = 0) => {
    let d = '';
    for (let i = 0; i <= 40; i++) { const x = 30 + i * 7.5, yy = y - a * (0.5 + 0.5 * Math.sin(i / 40 * Math.PI * 2.5 + ph)); d += (i ? 'L' : 'M') + x.toFixed(1) + ' ' + yy.toFixed(1); }
    return d;
  };
  const nozzle = (x, y) => `<g class="noz" transform="translate(${x} ${y})"><circle r="9" cy="2" fill="#ffc98f" opacity=".35"/>
    <rect x="-5" y="-44" width="10" height="14" fill="#9aa3ad"/><rect x="-16" y="-30" width="32" height="18" rx="2" fill="#ff8a3d"/>
    <path d="M-7 -12 L7 -12 L2 0 L-2 0 Z" fill="#e8ecf0"/></g>`;
  let h = '';
  if (kind === 'nozzle') {
    const cols = ['#0b5248', '#0e6a5d', '#118573', '#14a28b', '#17bfa3'];
    cols.forEach((c, i) => { h += path(wave(150 - i * 14, 4 + i * 6, i * 0.25), c, 9); });
    h += path('M0 0', '#2ee6c6', 9, 'class="fresh"');
    h += nozzle(0, 0);
  } else if (kind === 'compare') {
    let st = '', y = 150;
    for (let i = 0; i < 8; i++) { const w = 120 - i * 13; st += `<rect x="${95 - w / 2}" y="${y - 12}" width="${w}" height="10" rx="3" fill="${i % 2 ? '#3d434d' : '#4a515d'}"/>`; y -= 12; }
    h += st + `<text x="95" y="20" class="cap">flat layers: stairs</text>`;
    for (let i = 0; i < 7; i++) {
      const r = 64 - i * 0;
      h += path(`M ${205} ${150 - i * 6} Q 265 ${150 - i * 6 - 2 * (40 + i * 6)} ${325} ${150 - i * 6}`, ['#0e6a5d', '#118573', '#14a28b', '#17bfa3', '#1fd3b5', '#2ee6c6', '#5ff2d8'][i], 7);
    }
    h += `<text x="265" y="20" class="cap">curved layers: smooth</text>`;
  } else if (kind === 'modes') {
    for (let i = 0; i < 6; i++) h += path(`M ${30 + i * 4} ${150 - i * 16} L 95 ${132 - i * 16} L ${160 - i * 4} ${150 - i * 16}`, ['#0e6a5d', '#118573', '#14a28b', '#17bfa3', '#1fd3b5', '#2ee6c6'][i], 7);
    h += `<text x="95" y="20" class="cap">conical</text>`;
    for (let i = 0; i < 6; i++) {
      let d = ''; const a = [0, 0, 3, 8, 14, 20][i];
      for (let k = 0; k <= 24; k++) { const x = 200 + k * 5.4, yy = 150 - i * 16 - a * (0.5 + 0.5 * Math.sin(k / 24 * Math.PI * 2 - 1)); d += (k ? 'L' : 'M') + x.toFixed(1) + ' ' + yy.toFixed(1); }
      h += path(d, ['#0e6a5d', '#118573', '#14a28b', '#17bfa3', '#1fd3b5', '#2ee6c6'][i], 7);
    }
    h += `<text x="265" y="20" class="cap">follow the top surface</text>`;
  } else if (kind === 'steps') {
    const st = [['⤓', 'Import'], ['⚙', 'Settings'], ['▦', 'Slice'], ['▶', 'Preview'], ['🖨', 'Print']];
    st.forEach(([ic, t], i) => {
      const x = 40 + i * 70;
      h += `<circle cx="${x}" cy="80" r="25" fill="#1f3d39" stroke="#17bfa3" stroke-width="2" class="pop" style="animation-delay:${i * 0.25}s"/>
        <text x="${x}" y="89" class="ic">${ic}</text><text x="${x}" y="130" class="cap">${t}</text>`;
      if (i < st.length - 1) h += path(`M ${x + 30} 80 L ${x + 40} 80`, '#3e5a56', 3);
    });
  } else if (kind === 'device') {
    h += `<rect x="40" y="20" width="130" height="130" rx="10" fill="#22262d" stroke="#30343c"/><rect x="52" y="34" width="106" height="64" rx="5" fill="#0f1a19"/>`;
    for (let i = 0; i < 4; i++) h += path(wave(88 - i * 7, 3 + i * 3, i * .3).replace(/(\d+\.?\d*) (\d+\.?\d*)/g, (m, x, y) => `${(52 + (x - 30) * 0.353).toFixed(1)} ${y}`), ['#118573', '#14a28b', '#1fd3b5', '#2ee6c6'][i], 3);
    h += `<circle cx="140" cy="44" r="4" fill="#ff5252" class="blink"/><rect x="52" y="108" width="106" height="8" rx="4" fill="#30343c"/><rect x="52" y="108" width="62" height="8" rx="4" fill="#17bfa3"/>
      <text x="105" y="138" class="cap">Running · 58%</text>`;
    ['#e8e8e8', '#2b7de9', '#f2c230', '#e24a4a'].forEach((c, i) => { h += `<rect x="${200 + i * 36}" y="60" width="28" height="44" rx="6" fill="${c}"/><text x="${214 + i * 36}" y="124" class="cap">${i + 1}</text>`; });
    h += `<text x="268" y="40" class="cap">AMS</text>`;
  } else if (kind === 'help') {
    h += `<rect x="70" y="26" width="180" height="40" rx="14" fill="#2a2f37"/><text x="160" y="51" class="cap">Why is my print stringing?</text>
      <rect x="110" y="80" width="200" height="56" rx="14" fill="#14463f"/><text x="210" y="103" class="cap">Print the temperature tower,</text>
      <text x="210" y="121" class="cap">then the retraction test…</text>`;
  }
  svg.innerHTML = h;
  if (kind === 'nozzle') {                     // the nozzle prints the top layer, riding the wave up and down
    const fresh = svg.querySelector('.fresh'), noz = svg.querySelector('.noz'), t0 = performance.now();
    const y = (i) => 80 - 34 * (0.5 + 0.5 * Math.sin(i / 40 * Math.PI * 2.5 + 1.25));
    const step = (now) => {
      if (!svg.isConnected && now - t0 > 500) return;
      const u = ((now - t0) / 3400) % 1, n = u * 40;
      let d = 'M30 ' + y(0).toFixed(1);
      for (let i = 1; i <= Math.floor(n); i++) d += ` L${30 + i * 7.5} ${y(i).toFixed(1)}`;
      const x = 30 + n * 7.5, yy = y(n);
      d += ` L${x.toFixed(1)} ${yy.toFixed(1)}`;
      fresh.setAttribute('d', d);
      noz.setAttribute('transform', `translate(${x.toFixed(1)} ${(yy - 5).toFixed(1)})`);
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  return svg;
}

function showIntro(after) {
  const box = $('#intro');
  let i = 0;
  const draw = () => {
    const s = INTRO[i];
    const art = $('#intro-art'); art.innerHTML = ''; art.append(introArt(s.art));
    $('#intro-title').innerHTML = s.title;
    $('#intro-text').innerHTML = s.text;
    $('#intro-dots').innerHTML = '';
    INTRO.forEach((_, k) => $('#intro-dots').append(el('button', { class: k === i ? 'on' : '', title: `${k + 1} / ${INTRO.length}`, onclick: () => { i = k; draw(); } })));
    $('#intro-back').classList.toggle('invisible', i === 0);
    $('#intro-next').textContent = i === INTRO.length - 1 ? (after ? 'Let’s start' : 'Done') : 'Next';
    box.querySelector('.intro-card').classList.remove('slide'); void box.offsetWidth; box.querySelector('.intro-card').classList.add('slide');
  };
  const close = () => {
    box.classList.add('hidden');
    document.removeEventListener('keydown', keys, true);
    api('intro-seen', {}).catch(() => { });
    if (after) after();
  };
  const keys = (e) => {
    if (box.classList.contains('hidden')) return;
    if (e.key === 'ArrowRight' || e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); $('#intro-next').click(); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); e.stopPropagation(); if (i > 0) { i--; draw(); } }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
  };
  $('#intro-next').onclick = () => { if (i < INTRO.length - 1) { i++; draw(); } else close(); };
  $('#intro-back').onclick = () => { if (i > 0) { i--; draw(); } };
  $('#intro-skip').onclick = close;
  document.addEventListener('keydown', keys, true);
  box.classList.remove('hidden');
  draw();
}

// ------------------------------------------------------------------ help: answers questions from what the app knows
const HELP_ACTIONS = {
  intro: () => showIntro(null),
  np: () => openSetting('process', 'mode'),
  speed: () => openSetting('process', 'outer_wall_speed'),
  support: () => openSetting('process', 'enable_support'),
  tower: () => openSetting('process', 'prime_tower'),
  sequence: () => openSetting('process', 'print_sequence'),
  arrange: () => { setView('prepare'); arrangeModel(); },
  report: () => openReport(),
  clearance: () => { openEditor('printer'); modal.tab = 'Toolhead clearance'; renderModal(); },
  connection: () => { openEditor('printer'); modal.tab = 'Connection'; renderModal(); },
  calib: () => calibMenu($('#btn-calib')),
  device: () => setView('device'),
  preview: () => setView('preview'),
  prepare: () => setView('prepare'),
  sync: () => openSync(),
  chooser: () => openChooser(false),
};

// open a setting where it lives (process: the panel on the left; printer / filament: the editor) and flash it
function openSetting(cat, key) {
  let tab = null, field = null;
  for (const t of S.schema[cat]) for (const g of t.groups) for (const f of g.fields) if (f.key === key) { tab = t.tab; field = f; }
  if (!tab) return;
  if (field.adv && !S.advanced) { S.advanced = true; store.set('advanced', true); $('#adv-toggle').checked = true; }
  if (S.view === 'device') setView('prepare');
  if (cat === 'process') { S.processTab = tab; renderProcess(); }
  else { openEditor(cat); modal.tab = tab; renderModal(); }
  setTimeout(() => {
    const ctl = document.getElementById(`f-${cat}-${key}`);
    const row = ctl && (ctl.closest('.field') || ctl);
    if (!row) return;
    row.scrollIntoView({ block: 'center', behavior: 'smooth' });
    row.classList.remove('flash'); void row.offsetWidth; row.classList.add('flash');
    ctl.focus({ preventScroll: true });
  }, 60);
}

const H = { docs: null, idf: null, open: false, greeted: false };
const stem = (w) => (w.length > 4 ? w.replace(/(ings|ing|ies|ied|ed|es|s)$/, '') : w);
function words(text) {
  const raw = String(text).toLowerCase().replace(/<[^>]+>/g, ' ').normalize('NFKD').replace(/[^a-z0-9Ͱ-Ͽ]+/g, ' ').split(' ');
  const out = [];
  for (let w of raw) {
    if (!w || STOP.has(w)) continue;
    const syn = SYNONYMS[w];
    out.push(stem(w));
    if (syn) for (const s of syn.split(' ')) out.push(stem(s));
  }
  return out;
}
function lev1(a, b) {                       // edit distance <= 1 (one typo)
  if (Math.abs(a.length - b.length) > 1) return false;
  let i = 0, j = 0, d = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { i++; j++; continue; }
    if (++d > 1) return false;
    if (a.length > b.length) i++; else if (b.length > a.length) j++; else { i++; j++; }
  }
  return d + (a.length - i) + (b.length - j) <= 1;
}
function helpIndex() {
  if (H.docs) return;
  const docs = [];
  for (const h of HELP) docs.push({ kind: 'topic', h, title: h.t, w: [...words(h.t), ...words(h.t), ...words(h.q), ...words(h.q)], body: words(h.a) });
  for (const cat of ['process', 'filament', 'printer']) for (const t of S.schema[cat]) for (const g of t.groups) for (const f of g.fields) {
    docs.push({ kind: 'setting', cat, tab: t.tab, g: g.name, f, title: f.label,
      w: [...words(f.label), ...words(f.label), ...words(g.name), ...words(g.name)], body: words(f.tip || '') });
  }
  for (const t of CAL_TESTS || []) docs.push({ kind: 'calib', t, title: t.name, w: [...words(t.name), ...words(t.name), 'calibration'], body: words(t.about || '') });
  const df = new Map();
  for (const d of docs) for (const w of new Set([...d.w, ...d.body])) df.set(w, (df.get(w) || 0) + 1);
  H.idf = (w) => Math.log(1 + docs.length / (1 + (df.get(w) || 0)));
  H.vocab = [...df.keys()];
  H.docs = docs;
}
function helpSearch(q) {
  helpIndex();
  let qw = [...new Set(words(q))];
  // a word we don't know: try the closest one (typos)
  qw = qw.flatMap((w) => (H.idf(w) < Math.log(1 + H.docs.length) - 0.01 || w.length < 5 ? [w] : [w, ...H.vocab.filter((v) => v.length >= 4 && lev1(w, v)).slice(0, 2)]));
  const scored = [];
  for (const d of H.docs) {
    let s = 0, hit = 0;
    for (const w of qw) {
      const a = d.w.filter((x) => x === w).length, b = d.body.includes(w) ? 1 : 0;
      if (a || b) { hit++; s += H.idf(w) * (Math.min(a, 3) * 1.0 + b * 0.6); }
    }
    if (!hit) continue;
    s *= hit / qw.length;                          // all the words matter
    if (d.kind !== 'topic') {                      // a name made of the asked words beats a long name that has one of them
      const tw = new Set(d.w);                    // its name and group
      const c = qw.filter((w) => tw.has(w)).length / qw.length;
      s *= (0.55 + 0.45 * c) * (c === 1 ? 1.3 : 1);
      const lw = words(d.title);
      if (lw.length && lw.every((w) => qw.includes(w))) s *= 1.25;     // the question names this setting
    }
    if (d.kind === 'topic') s *= 1.15;
    scored.push([s, d]);
  }
  scored.sort((a, b) => b[0] - a[0]);
  return { qw, results: scored };
}

function helpSay(who, content) {
  const log = $('#help-log');
  const m = el('div', { class: 'help-msg ' + who });
  if (typeof content === 'string') m.innerHTML = content; else m.append(content);
  log.append(m);
  log.scrollTop = log.scrollHeight;
  return m;
}
function helpButtons(list) {
  const row = el('div', { class: 'help-acts' });
  for (const [label, fn] of list) row.append(el('button', { class: 'sm', onclick: fn }, label));
  return row;
}
function helpAnswer(q) {
  const text = q.trim();
  if (!text) return;
  helpSay('me', el('span', {}, text));
  for (const [re, reply] of SMALLTALK) if (re.test(text)) return helpSay('bot', reply);
  const { results } = helpSearch(text);
  const best = results[0];
  if (!best || best[0] < 1.2) {
    const m = helpSay('bot', 'I\'m not sure about that one. Try other words, or one of these. If something is not working, report it and it gets fixed:');
    m.append(helpButtons([...HELP.slice(0, 5).map((h) => [h.t, () => helpAnswerTopic(h)]), ['🐞 Report a problem', () => openReport(text)]]));
    return;
  }
  const top = results.slice(0, 6).map((r) => r[1]);
  const topic = top.find((d) => d.kind === 'topic' && results.find((r) => r[1] === d)[0] >= best[0] * 0.55);
  const settings = top.filter((d) => d.kind === 'setting').slice(0, 3);
  const calib = top.find((d) => d.kind === 'calib');
  const box = el('div');
  if (topic && (topic === best[1] || !settings.length || best[1].kind !== 'setting')) {
    box.append(el('div', { class: 'help-t' }, topic.h.t));
    const a = el('div'); a.innerHTML = topic.h.a; box.append(a);
    if (topic.h.go) box.append(helpButtons(topic.h.go.map(([l, act]) => [l, () => HELP_ACTIONS[act]?.()])));
  } else if (best[1].kind === 'calib') {
    const t = best[1].t;
    box.append(el('div', { class: 'help-t' }, t.name), el('div', {}, t.about || ''));
    box.append(helpButtons([['Open this test', () => openCalib(t)]]));
  } else {
    const d = settings[0] || best[1];
    const opts = (d.f.options || []).map(optLabel);
    const about = d.f.tip || (opts.length ? `Choices: ${opts.join(', ')}.` : `${d.f.label}${d.f.unit ? ` (${d.f.unit})` : ''}.`);
    const now = fieldValue(d.cat, d.f);
    box.append(el('div', { class: 'help-t' }, d.f.label.length < 14 && !d.f.label.toLowerCase().includes(d.g.toLowerCase()) ? `${d.g}: ${d.f.label}` : d.f.label),
      el('div', { class: 'muted small' }, `${LABELS[d.cat]} settings → ${d.tab} · now: ${d.f.type === 'checkbox' ? (now ? 'on' : 'off') : (d.f.options ? optLabel((d.f.options.find((o) => String(optValue(o)) === String(now))) ?? now) : `${now ?? '–'} ${d.f.unit || ''}`)}`),
      el('div', {}, about));
    box.append(helpButtons([['Show me this setting', () => openSetting(d.cat, d.f.key)]]));
  }
  const more = settings.filter((d) => !box.textContent.includes(d.f.label)).slice(0, 2);
  if (more.length || (calib && !box.textContent.includes(calib.t.name))) {
    box.append(el('div', { class: 'muted small help-more' }, 'Related:'));
    const list = more.map((d) => [`⚙ ${d.f.label.length < 14 ? d.g + ': ' : ''}${d.f.label}`, () => openSetting(d.cat, d.f.key)]);
    if (calib && !box.textContent.includes(calib.t.name)) list.push([`🎯 ${calib.t.name}`, () => openCalib(calib.t)]);
    box.append(helpButtons(list));
  }
  helpSay('bot', box);
}
function helpAnswerTopic(h) { helpAnswer(h.t); }

function toggleHelp(open = !H.open) {
  H.open = open;
  $('#help-panel').classList.toggle('hidden', !open);
  $('#help-fab').classList.toggle('on', open);
  if (open) {
    if (!H.greeted) {
      H.greeted = true;
      const m = helpSay('bot', 'Hi! I\'m the ArcLayer helper. Ask me about any setting, calibration or problem. For example:');
      m.append(helpButtons(['What is non-planar printing?', 'Conical or "follow the top surface"?', 'Why is my print stringing?', 'Connect my Bambu printer']
        .map((t) => [t, () => helpAnswer(t)])));
    }
    setTimeout(() => $('#help-input').focus(), 50);
  }
}
async function wireHelp() {
  loadCalibTests().then(() => (H.docs = null)).catch(() => { });     // calibrations join the search
  $('#help-fab').onclick = () => toggleHelp();
  $('#help-close').onclick = () => toggleHelp(false);
  $('#help-form').onsubmit = (e) => { e.preventDefault(); const i = $('#help-input'); const v = i.value; i.value = ''; helpAnswer(v); };
  $('#help-input').onkeydown = (e) => { if (e.key === 'Escape') toggleHelp(false); e.stopPropagation(); };
}

// ------------------------------------------------------------------ report a problem
function plateImage() {
  try {
    renderer.render(scene, camera);                       // the drawing buffer is only readable right after a draw
    const src = renderer.domElement, w = Math.min(1280, src.width), h = Math.round(src.height * w / src.width);
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    c.getContext('2d').drawImage(src, 0, 0, w, h);
    return c.toDataURL('image/png');
  } catch (e) { return null; }
}
function openReport(prefill = '') {
  const box = $('#report');
  $('#report-what').value = prefill || $('#report-what').value;
  $('#report-done').classList.add('hidden');
  $('#report-go').disabled = false;
  $('#report-go').textContent = 'Make the report';
  box.classList.remove('hidden');
  setTimeout(() => $('#report-what').focus(), 50);
}
function wireReport() {
  const close = () => $('#report').classList.add('hidden');
  $('#report-close').onclick = close; $('#report-cancel').onclick = close;
  $('#report-go').onclick = async () => {
    const what = $('#report-what').value.trim();
    if (what.length < 5) { toast('Write a few words about what happened', true); return $('#report-what').focus(); }
    $('#report-go').disabled = true; $('#report-go').textContent = 'Making it…';
    const settings = $('#report-settings').checked ? {
      printer_name: S.sel.printer, filament_names: S.slots.map((s) => s.preset), process_name: S.sel.process,
      printer: S.work.printer, filament: S.work.filament, process: S.work.process, view: S.view,
      model: S.model ? { name: S.model.name, parts: (S.parts || []).length } : null } : { printer_name: S.sel.printer };
    try {
      const r = await api('report', { what, expected: $('#report-expected').value, include_log: $('#report-log').checked,
        include_settings: $('#report-settings').checked, settings, picture: $('#report-pic').checked && S.view !== 'device' ? plateImage() : null });
      const d = $('#report-done');
      d.innerHTML = '';
      d.append(el('b', {}, '✓ Report ready: ' + r.name),
        el('div', { class: 'muted small' }, 'Has: ' + r.included.join(', ') + '. Its folder is open.'));
      if (r.email) {
        d.append(el('div', {}, r.mailto_failed ? `No mail app opened. Send the file to ${r.email}` : `A new email to ${r.email} is open in your mail app: attach the file and press Send.`),
          el('div', { class: 'row gap' }, el('button', { class: 'sm', onclick: () => navigator.clipboard?.writeText(r.email).then(() => toast('Address copied')) }, 'Copy the address'),
            el('button', { class: 'sm', onclick: () => api('open-folder', { path: r.path }) }, 'Show the file')));
      } else {
        d.append(el('div', {}, 'Send this file to the developer (the address is not set up in this version yet).'),
          el('button', { class: 'sm', onclick: () => api('open-folder', { path: r.path }) }, 'Show the file'));
      }
      d.classList.remove('hidden');
      $('#report-go').textContent = 'Done';
      $('#report-what').value = ''; $('#report-expected').value = '';
    } catch (e) {
      toast(e.message, true);
      $('#report-go').disabled = false; $('#report-go').textContent = 'Make the report';
    }
  };
}

// ------------------------------------------------------------------ a newer version is out
async function checkUpdate(asked = false) {
  let u;
  try { u = await api(asked ? 'update-check' : 'update', asked ? {} : undefined); } catch (e) { if (asked) toast(e.message, true); return; }
  if (!u.newer) {
    if (asked) toast(u.latest ? `ArcLayer ${u.current} is the newest version` : `ArcLayer ${u.current}. Could not reach the download page right now`);
    return;
  }
  if (!asked && store.get('update.skip', '') === u.latest) return;
  $('#update-text').textContent = `ArcLayer ${u.latest} is out (you have ${u.current}). Your presets and settings are kept when you install it.`;
  $('#update-bar').classList.remove('hidden');
  $('#update-get').onclick = () => { api('open-release', {}); $('#update-bar').classList.add('hidden'); };
  $('#update-later').onclick = () => { store.set('update.skip', u.latest); $('#update-bar').classList.add('hidden'); };
}
