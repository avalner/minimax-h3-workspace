/* Own workflows: the user's own ComfyUI workflow, its fields and its result window.
 *
 * A mode module. The section's static markup (head, the how-to note and #cwBody) stays in the
 * page; the form, the result window and the history are built into #cwBody on the first visit.
 * The state `CW` (the open workflow, what was filled in, the running job) lives with the module:
 * the file picker, the history list and the progress ticks all read it, and the browser keeps the
 * last opened workflow and its values in localStorage so a reload does not lose the form.
 *
 * Loaded before the page; nothing here runs until the section is opened.
 */

(function () {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

/* ---------- свои workflow ---------- */
const CW = { id: null, rep: null, vals: {}, files: {}, run: null, shown: null };
let body = null;        // #cwBody, where the form and the result window are built
let liveUrl = '';       // the object URL of the live preview frame of a running job
let draftHook = null;   // the DRAFT_HOOKS entry that keeps this section's history current
async function cwList(select) {
  let items = [];
  try { items = await apiGet('/api/wf/list'); } catch {}
  const sel = $('#cwSel');
  sel.innerHTML = items.length ? items.map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join('') : '<option value="">нет сохранённых</option>';
  let last = ''; try { last = localStorage.getItem('h3-cw-last') || ''; } catch {}
  if (!select && items.some(x => x.id === last)) select = last;
  if (select && items.some(x => x.id === select)) sel.value = select;
  $('#cwDel').hidden = !items.length;
  if (items.length) cwOpen(sel.value); else { CW.id = null; $('#cwBody').innerHTML = ''; $('#cwHowto').hidden = false; }
}
async function cwOpen(id) {
  CW.id = id; CW.shown = null; try { localStorage.setItem('h3-cw-last', id); } catch {} CW.vals = {}; CW.files = {}; $('#cwHowto').hidden = true;
  $('#cwBody').innerHTML = '<div class="hint">Разбираем workflow…</div>';
  try {
    CW.rep = await apiGet('/api/wf/' + id);
  } catch (e) { $('#cwBody').innerHTML = `<div class="note warn"><span>${esc(e.message || 'ComfyUI не отвечает. Запустите его и откройте раздел снова')}</span></div>`; return; }
  CW.rep.widgets.forEach(w => { CW.vals[w.key] = w.missing ? w.suggest : w.value; });
  try {  // what was set last time in this workflow
    const saved = JSON.parse(localStorage.getItem('h3-cw-' + id) || '{}');
    CW.rep.widgets.forEach(w => { if (w.key in saved && w.role !== 'media' && !(w.missing && !saved[w.key])) CW.vals[w.key] = saved[w.key]; });
  } catch {}
  cwRender();
}
const CW_MODEL = w => w.kind === 'combo' && w.role === 'other' && /(_name$|^ckpt|checkpoint|^model$|lora|^unet|^vae|^clip)/i.test(w.name);
const cwGroupBy = list => { const m = new Map(); list.forEach(w => { if (!m.has(w.node)) m.set(w.node, []); m.get(w.node).push(w); }); return [...m.values()]; };
function cwField(w) {
  const k = esc(w.key), v = CW.vals[w.key];
  if (w.kind === 'combo') return `<select data-k="${k}">${w.missing && !v ? '<option value="">— выберите файл —</option>' : ''}${(w.options || []).map(o => `<option${o === v ? ' selected' : ''}>${esc(o)}</option>`).join('')}</select>`;
  if (w.kind === 'bool') return `<label class="cw-chk"><input type="checkbox" data-k="${k}"${v ? ' checked' : ''}> включено</label>`;
  if (w.kind === 'int' || w.kind === 'float') return `<input type="number" data-k="${k}" value="${esc(String(v))}"${w.min != null ? ` min="${w.min}"` : ''}${w.max != null ? ` max="${w.max}"` : ''} step="${w.kind === 'int' ? 1 : (w.step || 'any')}">`;
  if (w.multiline || String(v).length > 60) return `<textarea data-k="${k}" rows="4">${esc(String(v))}</textarea>`;
  return `<input type="text" data-k="${k}" value="${esc(String(v))}">`;
}
// a node's fields as one small window: title on top, fields in a tidy grid
const cwBox = (title, ws) => `<div class="cw-box"><div class="cw-bt">${esc(title)}</div><div class="cw-bg">${ws.map(w =>
  `<label class="cw-f${w.kind === 'combo' || w.multiline ? ' wide' : ''}"><span>${esc(w.name)}</span>${cwField(w)}</label>`).join('')}</div></div>`;
function cwRender() {
  try { cwRenderInner(); }
  catch (e) { $('#cwBody').innerHTML = `<div class="note warn"><span>Не удалось показать форму этого workflow: ${esc(e.message || e)}. Пришлите «Отчёт для разработчика» (Настройки) вместе с файлом workflow.</span></div>`; }
}
function cwRenderInner() {
  const r = CW.rep, W = r.widgets;
  const missing = W.filter(w => w.missing), prompts = W.filter(w => w.role === 'prompt' || w.role === 'negative'), media = W.filter(w => w.role === 'media');
  const seeds = W.filter(w => w.role === 'seed'), sizes = W.filter(w => w.role === 'size');
  const aspects = W.filter(w => w.role === 'aspect'), toggles = W.filter(w => w.role === 'toggle'), params = W.filter(w => w.role === 'param');
  const tiles = W.filter(w => ['length', 'steps', 'cfg'].includes(w.role));
  const models = W.filter(w => CW_MODEL(w) && !w.missing);
  const other = W.filter(w => w.role === 'other' && !w.missing && !CW_MODEL(w));
  let status = '';
  if (r.missing_nodes.length) status += `<div class="note warn"><span><b>В вашем ComfyUI нет нод:</b> ${[...new Set(r.missing_nodes.map(m => esc(m.cls)))].join(', ')}. Установите их (например, через ComfyUI Manager), перезапустите ComfyUI и откройте раздел снова. Пока их нет, запустить нельзя.</span></div>`
    + `<div class="cw-bg3">${[...new Set(r.missing_nodes.map(m => m.cls))].map(c => `<div class="cw-f"><span>Нода <b style="color:var(--pink)">${esc(c)}</b></span><button class="btn mini" type="button" data-websearch="${esc('ComfyUI custom node ' + c + ' github')}">Найти в интернете</button></div>`).join('')}</div>`;
  if (missing.length) status += `<div class="note warn"><span><b>Не найдены файлы.</b> Выберите замену из того, что есть в вашем ComfyUI.</span></div><div class="cw-bg3">${missing.map(w =>
    `<label class="cw-f"><span>${esc(w.title)} · не найден: <b style="color:var(--pink)">${esc(w.value)}</b> <button class="btn mini" type="button" data-websearch="${esc('"' + String(w.value).split(/[\/]/).pop() + '" huggingface download')}">Найти в интернете</button></span>${cwField(w)}</label>`).join('')}</div>`;
  r.notes.forEach(n => status += `<div class="note"><span>${esc(n)}</span></div>`);
  let html = '';
  if (status) html += `<div class="card cw-status"><div class="card-h"><h3>Что нашлось в «${esc(r.name)}»</h3></div>${status}</div>`;
  else html += `<div class="cw-ok">✓ «${esc(r.name)}»: все ноды и файлы на месте</div>`;

  let main = '';
  if (prompts.length) main += `<div class="cw-prompts">${prompts.map(w => `<div class="cw-pp ${w.role === 'negative' ? 'neg' : 'pos'}"><div class="cw-pb">${w.role === 'negative' ? '✕ Негативный промпт' : '✓ Основной промпт'} <small>${esc(w.title)}</small></div>${cwField(w)}</div>`).join('')}</div>`;
  if (media.length) main += `<div class="cw-media">${media.map(w => `<label class="cw-file"><input type="file" data-f="${esc(w.key)}" accept="${w.mkind}/*" hidden>
      <div class="cw-fp"><i>${{ image: '🖼', audio: '🎵', video: '🎬' }[w.mkind]}</i></div>
      <b>${{ image: 'Изображение', audio: 'Аудио', video: 'Видео' }[w.mkind]}</b><small class="cw-fn">${esc(w.title)} · ${w.has_default ? 'можно не менять' : 'нужно выбрать'}</small>${w.mkind === 'audio' ? '' : `<button class="btn mini cw-lib" data-lib="${esc(w.key)}" type="button">Из Библиотеки</button>`}</label>`).join('')}</div>`;
  let tl = '';
  tl += aspects.map(w => `<div class="cw-t cw-asp"><span>Формат · ${esc(w.title)}</span>${cwField(w)}</div>`).join('');
  const bySizeNode = cwGroupBy(sizes);
  bySizeNode.forEach(ws => { tl += `<div class="cw-t"><span>Размер · ${esc(ws[0].title)}</span><div class="cw-pair">${ws.map(w => `<label>${esc(w.name)}${cwField(w)}</label>`).join('<b>×</b>')}</div></div>`; });
  tl += tiles.map(w => `<div class="cw-t"><span>${{ length: 'Длина', steps: 'Шаги', cfg: 'CFG' }[w.role]} · ${esc(w.title)}</span>${cwField(w)}</div>`).join('');
  tl += seeds.map(w => `<div class="cw-t"><span>Seed · ${esc(w.title)}</span>${cwField(w)}<label class="cw-chk"><input type="checkbox" data-rnd="${esc(w.key)}" checked> каждый раз новый</label></div>`).join('');
  tl += params.map(w => `<div class="cw-t"><span>${esc(w.title)}</span>${cwField(w)}</div>`).join('');
  if (tl) main += `<div class="cw-tiles">${tl}</div>`;
  if (toggles.length) main += `<div class="cw-toggles">${toggles.map(w => `<label class="cw-sw"><input type="checkbox" data-k="${esc(w.key)}"${CW.vals[w.key] ? ' checked' : ''}><b>${esc(w.title)}</b></label>`).join('')}</div>`;
  html += `<div class="card"><div class="card-h"><h3>Основное <small style="color:var(--muted);font-weight:500">· промпт, размер, шаги, seed</small></h3></div><div style="display:grid;gap:10px">${main || '<div class="hint">Основных полей не нашлось. Всё остальное внизу.</div>'}</div></div>`;

  const det = (title, n, body) => `<details class="card cw-det"><summary>${title} <small>${n}</small></summary><div class="cw-boxes">${body}</div></details>`;
  if (models.length) html += det('Модели и файлы', `${models.length} · обычно менять не нужно`, cwGroupBy(models).map(ws => cwBox(ws[0].title, ws)).join(''));
  if (other.length) html += det('Дополнительные настройки', `${other.length} · обычно менять не нужно`, cwGroupBy(other).map(ws => cwBox(ws[0].title, ws)).join(''));

  const result = `<div class="card cw-res"><div class="card-h"><h3>Результат</h3></div>
    <div class="cw-frame" id="cwFrame"><button class="cw-zb" id="cwZoomBtn" type="button" title="Увеличить" hidden><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4M11 8v6M8 11h6"/></svg></button><div class="cw-wait" id="cwWait">Здесь появится результат</div>
      <img id="cwLive" alt="" hidden><video id="cwVideo" controls loop playsinline hidden></video><img id="cwImage" alt="" hidden>
      <div class="cw-bar"><i id="cwBar"></i></div></div>
    <div class="cw-acts" id="cwActs" hidden><button class="btn pri" data-a="addlib" type="button" hidden>Добавить в библиотеку</button><button class="btn" data-a="save" type="button">Сохранить</button><button class="btn" data-a="lib" type="button">В Библиотеке</button><button class="btn" data-a="restore" type="button" title="Вернуть в форму настройки этой генерации">Вернуть настройки</button></div>
    <div class="cw-time" id="cwTime"></div><div class="status" id="cwStatus"></div>
    <button class="go" id="cwGo"${r.missing_nodes.length ? ' disabled' : ''}>Сгенерировать</button>
    <button class="stop" id="cwStop" hidden><i></i>Остановить</button>
    <div class="cw-hh" id="cwHH" hidden>История</div><div class="cw-hist" id="cwHist"></div></div>`;
  $('#cwBody').innerHTML = `<div class="cw-cols"><div class="cw-left">${html}</div><div class="cw-rs" id="cwRs" title="Потяните, чтобы изменить ширину окна. Двойной клик: вернуть"></div><div class="cw-right">${result}</div></div>`;
  cwTick();
  if (!CW.run) cwShowLast();
  cwHist();
}
/* The handlers of the section, wired once on the first visit: the form is rebuilt on every
   workflow and the result window is redrawn, so everything is delegated from #cwBody. */
function wireBody(scope) {
scope.listen(body, 'input', e => {
  const k = e.target.dataset?.k; if (!k) return;
  const w = CW.rep.widgets.find(x => x.key === k); if (!w) return;
  CW.vals[k] = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
  try { localStorage.setItem('h3-cw-' + CW.id, JSON.stringify(CW.vals)); } catch {}
});
// width / height are rounded to a multiple of 32 (what the video and picture models accept)
scope.listen(body, 'change', e => {
  const inp = e.target, k = inp.dataset?.k; if (!k || !inp.closest('.cw-pair')) return;
  const v = parseInt(inp.value, 10); if (!(v > 0)) return;
  const r = Math.max(64, Math.round(v / 32) * 32);
  if (r !== v) { inp.value = r; CW.vals[k] = String(r); try { localStorage.setItem('h3-cw-' + CW.id, JSON.stringify(CW.vals)); } catch {} toast(`Размер округлён: ${v} → ${r} (кратно 32)`); }
});
scope.listen(body, 'change', e => {
  const k = e.target.dataset?.f; if (!k) return;
  cwSetFile(k, e.target.files[0] || null);
});
}

/* ---------- own workflows: files, library picker, width of the result window ---------- */
function cwSetFile(key, f) {
  CW.files[key] = f;
  const inp = $(`[data-f="${key}"]`, $('#cwBody')), tile = inp && inp.closest('.cw-file'); if (!tile) return;
  $('.cw-fn', tile).textContent = f ? f.name : 'Выберите файл';
  $('.cw-fp', tile).innerHTML = f && f.type.startsWith('image/') ? `<img src="${URL.createObjectURL(f)}" alt="">` : '<i>✓</i>';
  tile.classList.toggle('on', !!f);
}
function cwPick(key) {
  const w = CW.rep.widgets.find(x => x.key === key); if (!w) return;
  const items = LIB.filter(it => !it.missing && it.url && (w.mkind === 'image' ? it.kind === 'image' : it.kind !== 'image')).sort((a, b) => b.date - a.date);
  let m = $('#cwPick');
  if (!m) { m = document.createElement('div'); m.className = 'modal'; m.id = 'cwPick'; document.body.append(m); }
  m.hidden = false;
  m.innerHTML = `<div class="box" style="padding:20px"><div class="card-h"><h3>Из Библиотеки · ${w.mkind === 'image' ? 'изображения' : 'видео'}</h3><button class="btn" data-x type="button">Закрыть</button></div>`
    + (items.length ? `<div class="cw-pickgrid">${items.map(it => `<button class="cw-th" data-id="${it.id}" type="button" title="${esc((it.label ? it.label + ' · ' : '') + (it.prompt || '').slice(0, 80))}">${w.mkind === 'image' ? `<img src="${it.url}" alt="" loading="lazy">` : `<video src="${it.url}#t=0.5" preload="metadata" muted></video>`}${it.label ? `<span class="cw-lb">${esc(it.label)}</span>` : ''}</button>`).join('')}</div>`
      : '<div class="hint" style="padding:20px 4px">В Библиотеке пока нет подходящих файлов.</div>') + '</div>';
  m.onclick = async e => {
    if (e.target === m || e.target.closest('[data-x]')) { m.hidden = true; return; }
    const th = e.target.closest('.cw-th'); if (!th) return;
    const it = LIB.find(x => x.id === th.dataset.id); if (!it) return;
    try {
      const blob = await apiGet(it.url, { raw: 'blob' });
      cwSetFile(key, new File([blob], `${it.id}.${it.ext || (w.mkind === 'image' ? 'png' : 'mp4')}`, { type: blob.type }));
      m.hidden = true; toast('Файл взят из Библиотеки');
    } catch { toast('Не удалось взять файл'); }
  };
}

/* the divider before the result window: drag to change its width, double-click to forget it */
function wireDivider(scope) {
scope.listen(document, 'pointerdown', e => {
  const h = e.target.closest('#cwRs'); if (!h) return;
  const cols = h.parentElement, box = body; h.classList.add('drag'); h.setPointerCapture(e.pointerId);
  const move = ev => { const r = cols.getBoundingClientRect(); const w = Math.round(Math.min(r.width * .7, Math.max(300, r.right - ev.clientX - 5))); box.style.setProperty('--cw-rw', w + 'px'); try { localStorage.setItem('h3-cw-rw', w); } catch {} };
  const up = () => { h.classList.remove('drag'); h.removeEventListener('pointermove', move); h.removeEventListener('pointerup', up); };
  h.addEventListener('pointermove', move); h.addEventListener('pointerup', up);
});
scope.listen(document, 'dblclick', e => { if (e.target.closest('#cwRs')) { body.style.removeProperty('--cw-rw'); try { localStorage.removeItem('h3-cw-rw'); } catch {} } });
}
const cwMine = () => [...DRAFTS, ...LIB].filter(x => x.mode === 'custom' && (x.wf ? x.wf === CW.id : x.sub === CW.rep.name))
  .filter(x => !window.h3Projects || window.h3Projects.ok(x))
  .filter((x, i, a) => a.findIndex(y => y.id === x.id) === i).sort((a, b) => b.date - a.date);
function cwFit(w, h) {  // the window takes the shape of the picture, never taller than 68% of the screen
  const f = $('#cwFrame'); if (!f || !w || !h) return;
  f.style.aspectRatio = `${w} / ${h}`; f.style.width = `min(100%, calc(var(--vh,1vh)*${(68 * w / h).toFixed(1)}))`; f.style.minHeight = '0'; f.style.margin = '0 auto';
}
function cwShow(it) {
  const f = $('#cwFrame'); if (!f || !it) return;
  CW.shown = it.id;
  hideSoundChip(f);
  const pic = it.kind === 'image', v = $('#cwVideo'), im = $('#cwImage');
  $('#cwWait').hidden = true; $('#cwLive').hidden = true; v.hidden = pic; im.hidden = !pic;
  if (pic) { v.removeAttribute('src'); im.onload = () => cwFit(im.naturalWidth, im.naturalHeight); im.src = it.url; }
  else { v.onloadedmetadata = () => cwFit(v.videoWidth, v.videoHeight); v.src = it.url; playWithSound(v); }
  const draft = !!it.preview && !byId(it.id);
  $('#cwActs').hidden = false; $('#cwActs [data-a="restore"]').hidden = !it.cw_values;
  $('#cwActs [data-a="addlib"]').hidden = !draft; $('#cwActs [data-a="lib"]').hidden = draft;
  if (!CW.run) { $('#cwBar').style.width = '0'; $('#cwTime').innerHTML = `<b>${pic ? 'Изображение' : 'Видео'}${it.label ? ' · ' + esc(it.label) : ''}</b> · ${fmtDate(it.date)}`; $('#cwStatus').textContent = [it.res, it.sub, draft ? 'черновик — в Библиотеку не добавлен' : ''].filter(Boolean).join(' · '); }
  $('#cwZoomBtn').hidden = !pic;
  $$('#cwHist .cw-th').forEach(t => t.classList.toggle('on', t.dataset.id === it.id));
}
function cwShowLast() { const mine = cwMine(); const it = mine.find(x => x.id === CW.shown) || mine[0]; if (it && !it.missing) cwShow(it); }
function cwHist() {
  const box = $('#cwHist'); if (!box) return;
  const mine = cwMine().filter((it, i) => it.preview || i < 16);
  $('#cwHH').hidden = !mine.length;
  box.innerHTML = mine.map(it => `<button draggable="true" class="cw-th${it.id === CW.shown ? ' on' : ''}${it.preview ? ' draft' : ''}" data-id="${it.id}" type="button" title="${esc((it.label ? it.label + ' · ' : '') + fmtDate(it.date))}">${it.missing ? '<i>—</i>'
    : it.kind === 'image' ? `<img src="${it.url}" alt="" loading="lazy">` : `<video src="${it.url}#t=0.5" preload="metadata" muted"></video>`}${it.label ? `<span class="cw-lb">${esc(it.label)}</span>` : ''}</button>`).join('');
}
/* a new result of this workflow appears in its history at once; the hook is the page's list of
   sections that show their own drafts, so it is taken back when the window closes */
function hookDraftHistory(scope) {
  if (draftHook) return;
  draftHook = () => { if (CW.rep) cwHist(); };
  DRAFT_HOOKS.push(draftHook);
  scope.add(() => {
    const i = DRAFT_HOOKS.indexOf(draftHook);
    if (i >= 0) DRAFT_HOOKS.splice(i, 1);
    draftHook = null;
  });
}
/* the running job's tick, its prompt subscription and the live frame: given back on close */
function stopRun() {
  const run = CW.run; if (!run) return;
  clearInterval(run.tick); offPrompt(run.id);
  CW.run = null;
  previewHandlers.delete('cw');
}
function cwTick() {
  const r = CW.run, bar = $('#cwBar'); if (!r || !bar) return;
  const el = (performance.now() - r.t0) / 1000, sampling = r.stage.startsWith('sample') && r.max;
  const base = { queued: .02, load: .05, encode: .12, sample: .15, upscale: .7, sample2: .75, decode: .92, save: .98 }[r.stage] ?? .1;
  let p = base, line = QSTAGE[r.stage] || 'идёт', eta = '';
  if (sampling) {
    const frac = (r.step || 0) / r.max, two = r.stage === 'sample2';
    p = (two ? .75 : .15) + (two ? .15 : .55) * frac; line += ` · шаг ${r.step || 0} из ${r.max}`;
    if (r.step) eta = ` · до конца этапа ~${fmtTime((r.max - r.step) * ((r.stepT - r.sampleT0) / r.step) / 1000)}`;
  }
  bar.style.width = (p * 100) + '%';
  $('#cwTime').innerHTML = r.stage === 'queued' ? `<b>В очереди</b> · ждём своей очереди · ${fmtTime(el)}` : `<b>Идёт</b> · прошло ${fmtTime(el)}${eta}`;
  $('#cwStatus').textContent = line;
}
function cwFinish(kind, ev) {
  const r = CW.run; if (!r) return;
  clearInterval(r.tick); offPrompt(r.id); previewHandlers.delete('cw'); CW.run = null;
  const took = fmtTime((performance.now() - r.t0) / 1000);
  if (!$('#cwLive')) return;
  $('#cwLive').hidden = true; $('#cwStop').hidden = true; $('#cwGo').disabled = !!CW.rep.missing_nodes.length;
  if (kind === 'done') {
    const it = byId(ev.item.id) || draftById(ev.item.id) || normItem(ev.item);
    cwShow(it); cwHist(); $('#cwBar').style.width = '100%';
    $('#cwTime').innerHTML = `<b style="color:var(--ok)">Готово</b> · за ${took}`;
    $('#cwStatus').textContent = it.preview ? 'Черновик — в Библиотеку не добавлен: нажмите «Добавить в библиотеку».'
      : `${it.kind === 'image' ? 'Изображение сохранена' : 'Видео сохранено'} в Библиотеку`;
  } else {
    $('#cwBar').style.width = '0'; $('#cwWait').hidden = false; $('#cwWait').textContent = 'Здесь появится результат';
    $('#cwTime').innerHTML = kind === 'error' ? '<b style="color:var(--pink)">Ошибка</b>' : '<b>Остановлено</b>';
    $('#cwStatus').textContent = kind === 'error' ? 'Ошибка ComfyUI: ' + ev.message : 'Генерация остановлена';
  }
}
/* what the buttons of the result window do: stop, generate, take a file from the Library, open a
   picture, "add to the Library" and "restore the settings" of an earlier generation */
function wireActions(scope) {
scope.listen(body, 'click', async e => {
  const lb = e.target.closest('[data-lib]'); if (lb) { e.preventDefault(); cwPick(lb.dataset.lib); return; }
  if (e.target.closest('#cwZoomBtn') || e.target.closest('#cwImage')) { zoomOpen($('#cwImage').src); return; }
  const th = e.target.closest('.cw-th'); if (th) { const it = byId(th.dataset.id) || draftById(th.dataset.id); if (it) cwShow(it); return; }
  const act = e.target.closest('#cwActs [data-a]');
  if (act) {
    const it = byId(CW.shown) || draftById(CW.shown); if (!it) return;
    if (act.dataset.a === 'addlib') {
      const kept = await keepDraftItem(it);
      if (kept) { CW.shown = kept.id; cwShow(kept); cwHist(); }
    }
    else if (act.dataset.a === 'save') saveItems([it]);
    else if (act.dataset.a === 'lib') goTo('library');
    else if (act.dataset.a === 'restore' && it.cw_values) {
      Object.entries(it.cw_values).forEach(([k, v]) => { if (k in CW.vals) CW.vals[k] = v; });
      try { localStorage.setItem('h3-cw-' + CW.id, JSON.stringify(CW.vals)); } catch {}
      cwRender(); toast('Настройки возвращены');
    }
    return;
  }
  if (e.target.closest('#cwStop')) {
    if (CW.run) { $('#cwStatus').textContent = 'Останавливаем…'; apiPost('/api/interrupt', { prompt_id: CW.run.id }).catch(() => {}); }
    return;
  }
  if (!e.target.closest('#cwGo')) return;
  const status = $('#cwStatus'), W = CW.rep.widgets;
  const bad = W.find(w => w.missing && !CW.vals[w.key]); if (bad) { status.textContent = `Выберите файл: ${bad.title}`; return; }
  const noFile = W.find(w => w.role === 'media' && !w.has_default && !CW.files[w.key]); if (noFile) { status.textContent = `Загрузите файл: ${noFile.title}`; return; }
  $$('[data-rnd]', $('#cwBody')).forEach(c => {
    if (!c.checked) return;
    const v = Math.floor(Math.random() * 2 ** 50); CW.vals[c.dataset.rnd] = v;
    const inp = $(`[data-k="${c.dataset.rnd}"]`, $('#cwBody')); if (inp) inp.value = v;
  });
  const fd = new FormData(); fd.append('wf', CW.id); fd.append('values', JSON.stringify(CW.vals)); fd.append('preview', '1');
  Object.entries(CW.files).forEach(([k, f]) => f && fd.append('f:' + k, f));
  $('#cwGo').disabled = true; status.textContent = 'Отправляем задание в ComfyUI…';
  try {
    const res = await apiSend('/api/generate/custom', { method: 'POST', body: fd, timeout: 180000 });
    const prompt = (W.find(w => w.role === 'prompt') || {}).key;
    qAdd(res.prompt_id, 'custom', prompt ? String(CW.vals[prompt]) : CW.rep.name);
    if (CW.run) { clearInterval(CW.run.tick); offPrompt(CW.run.id); }  // the older job goes on in the queue
    CW.run = { id: res.prompt_id, t0: performance.now(), stage: 'queued' };
    $('#cwVideo').hidden = true; $('#cwImage').hidden = true; $('#cwLive').hidden = true; $('#cwWait').hidden = false; $('#cwWait').textContent = 'Задание в очереди…';
    $('#cwStop').hidden = false; $('#cwGo').disabled = false;
    CW.run.tick = setInterval(cwTick, 1000); cwTick();
    previewHandlers.set('cw', blob => {
      const run = CW.run, im = $('#cwLive'); if (!run || run.stage === 'queued' || !im) return;
      URL.revokeObjectURL(liveUrl); liveUrl = im.dataset.url = im.src = URL.createObjectURL(blob); im.hidden = false; $('#cwWait').hidden = true;
    });
    onPrompt(res.prompt_id, ev => {
      const run = CW.run; if (!run || run.id !== ev.prompt_id) return;
      if (ev.type === 'stage') {
        const sampling = ev.stage.startsWith('sample');
        if (sampling && run.stage !== ev.stage) run.sampleT0 = performance.now();
        run.stage = ev.stage; run.step = ev.step; run.max = ev.max;
        if (sampling && ev.step) run.stepT = performance.now();
        if ($('#cwWait')) $('#cwWait').textContent = QSTAGE[ev.stage] || 'Идёт генерация…';
        cwTick();
      } else if (ev.type === 'done') cwFinish('done', ev);
      else if (ev.type === 'error') cwFinish('error', ev);
      else if (ev.type === 'interrupted') cwFinish('stopped', ev);
    });
  } catch (err) { status.textContent = 'Ошибка: ' + err.message; $('#cwGo').disabled = !!CW.rep.missing_nodes.length; }
});
}

  /* the page's «← Модели» button in this section's head */
  function wireBack() {
    const back = $('#cwBack');
    if (back) back.onclick = () => $('.nav[data-view="models"]').click();
  }

  window.h3Custom = {
    get state() { return CW; },
    list: cwList,
    open: cwOpen,
    render: cwRender,
  };

  window.h3Modes.register("custom", {
    mount(scope) {
      body = $('#cwBody');
      wireBack();
      wireBody(scope);
      wireDivider(scope);
      wireActions(scope);
      // the width of the result window is remembered: it is the same on every visit
      try { const rw = +localStorage.getItem('h3-cw-rw'); if (rw >= 300) body.style.setProperty('--cw-rw', rw + 'px'); } catch {}
      hookDraftHistory(scope);
      scope.listen($('#cwSel'), 'change', e => e.target.value && cwOpen(e.target.value));
      scope.listen($('#cwDel'), 'click', async () => {
        if (!CW.id || !confirm('Удалить этот workflow из списка? Файлы на диске не трогаем.')) return;
        await apiSend('/api/wf/' + CW.id, { method: 'DELETE' }).catch(() => {});
        cwList();
      });
      scope.listen($('#cwFile'), 'change', async e => {
        const f = e.target.files[0]; e.target.value = ''; if (!f) return;
        try {
          const graph = JSON.parse(await f.text());
          await apiPost('/api/wf/add', { name: f.name.replace(/\.json$/i, ''), graph });
        } catch (err) { toast(err instanceof SyntaxError ? 'Это не файл workflow (.json)' : err.message); }
      });
    },
    enter: () => cwList(CW.id),        // the saved workflows are read again on every visit
    leave: () => window.h3Lifecycle.pauseMedia($('#view-custom')),
    dispose() {
      stopRun();
      try { URL.revokeObjectURL(liveUrl); } catch {}
      liveUrl = '';
      const modal = $('#cwPick');
      if (modal) modal.remove();
      window.h3Lifecycle.pauseMedia($('#view-custom'));
    },
  });
})();
