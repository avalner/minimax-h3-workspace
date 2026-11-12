/* Lipsync: a clip driven by an uploaded audio fragment and up to nine reference pictures.
 *
 * A mode module. The state `L` and the audio editor exist from load: the page restores the last
 * audio and the reference pictures while it is parsed, before anyone opens the section, and the
 * details window/«Повторить»/the agent read `L`. The preview panel and the parameters card are
 * built on the first visit; before that every update is a no-op.
 *
 * Loaded before the page: `h3Lipsync.state` is read during parse. The WAV encoder is shared with
 * the agent and lives in components.js.
 */

(function () {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  /* components.js is loaded before the modes: the reference slots and the audio editor are built
     while the page is still parsed, so their widgets have to be here from the start */
  const { previewPanel, paramsCard, mountLoras, useSeed, fitSlot, fitAspect, wireSlotSwap,
          clearSlots, REF_HINT, QUALITY_ALL, ASPECTS, resFor, r32, sizeForm, seedForm,
          loraForm, wavSlice } = window.h3Components;

  let ui = null;   // { update, refresh } — built with the panel on the first visit
  const update = () => { if (ui) ui.update(); };
  const refresh = () => { if (ui) ui.refresh(); };

const L = { aspect: '9:16', q: '1080p', refSize: 'max', start: 0, end: 0, dur0: 0, refs: 0, len: () => Math.max(0, L.end - L.start) };
const MAX_LEN = 15, REC_LEN = 12;


// refs
const MAX_REFS = 9;
const refsEl = $('#refs');
for (let i = 1; i <= MAX_REFS; i++) {
  const r = document.createElement('label');
  r.className = 'ref';
  r.innerHTML = `<input type="file" accept="image/*"><div class="plus"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>${i === 1 ? 'Персонаж' : 'Референс'}</div>
    <span class="tag" hidden>Picture ${i}</span><button class="x" type="button">✕</button>`;
  refsEl.append(r);
  const input = $('input', r);
  const set = f => {
    if (!f || !f.type.startsWith('image/')) return;
    $('img', r)?.remove(); $('select', r)?.remove();
    r._file = f;
    const img = document.createElement('img'); img.src = URL.createObjectURL(f); r.prepend(img);
    fitSlot(r, img);
    if (!$$('.ref.filled', refsEl).length) fitAspect(L, img, update);
    const sel = document.createElement('select');
    sel.innerHTML = ['Персонаж', 'Локация', 'Одежда', 'Предмет', 'Стиль'].map(o => `<option>${o}</option>`).join('');
    if (i > 1) sel.selectedIndex = 1;
    sel.onclick = e => e.preventDefault();
    sel.onmousedown = e => e.stopPropagation();
    r.append(sel);
    r.classList.add('filled'); $('.plus', r).hidden = true; $('.tag', r).hidden = false;
    refsChanged();
  };
  input.onchange = () => set(input.files[0]);
  r.addEventListener('dragover', e => e.preventDefault());
  r.addEventListener('drop', e => { e.preventDefault(); set(e.dataTransfer.files[0]); });
  $('.x', r).onclick = e => {
    e.preventDefault(); e.stopPropagation();
    $('img', r)?.remove(); $('select', r)?.remove(); input.value = ''; r._file = null; resetSlot(r);
    r.classList.remove('filled'); $('.plus', r).hidden = false; $('.tag', r).hidden = true; refsChanged();
  };
}
wireSlotSwap($$('.ref', refsEl), refsChanged);
$('#rClrAll').onclick = () => clearSlots($$('.ref', refsEl));
function refsChanged() {
  // H3 numbers references without gaps, so tags follow the order of filled slots
  const slots = $$('.ref.filled', refsEl);
  slots.forEach((r, i) => $('.tag', r).textContent = `Picture ${i + 1}`);
  const filled = slots.map((_, i) => i + 1);
  L.refs = filled.length;
  $('#rCount').textContent = `${L.refs} / ${MAX_REFS}`;
  $('#tokens').innerHTML = filled.map(n => `<button class="token" type="button">&lt;Picture ${n}&gt;</button>`).join('') || '<span class="hint">добавьте референсы — появятся метки</span>';
}
$('#tokens').onclick = e => {
  const t = e.target.closest('.token'); if (!t) return;
  const ta = $('#lPrompt'), s = ta.selectionStart, txt = t.textContent + ' ';
  ta.value = ta.value.slice(0, s) + txt + ta.value.slice(ta.selectionEnd);
  ta.focus(); ta.selectionStart = ta.selectionEnd = s + txt.length;
};
refsChanged();

// audio editor
let actx, abuf, peaks = [], src, raf, playStart = 0;
const wave = $('#wave'), cv = $('#waveCv'), selEl = $('#sel'), ph = $('#ph');
const aFile = $('#aFile');
$('#aChange').onclick = () => aFile.click();
$('#aRemove').onclick = () => {
  stop();
  abuf = null; peaks = []; undoStack = []; cut = null; audioName = '';
  L.dur0 = 0; L.start = 0; L.end = 0; MEM.audio = null; aFile.value = '';
  $('#aEd').classList.remove('on'); aDrop.style.display = '';
  syncCut(); update(); memSave();
  toast('Аудио удалено');
};
aFile.onchange = () => loadAudio(aFile.files[0]);
const aDrop = $('#aDrop');
aDrop.addEventListener('dragover', e => { e.preventDefault(); aDrop.classList.add('over'); });
aDrop.addEventListener('dragleave', () => aDrop.classList.remove('over'));
aDrop.addEventListener('drop', e => { e.preventDefault(); aDrop.classList.remove('over'); loadAudio(e.dataTransfer.files[0]); });

async function loadAudio(f) {
  if (!f) return;
  MEM.audio = f;
  stop();
  actx = actx || new AudioContext();
  try { abuf = await actx.decodeAudioData(await f.arrayBuffer()); }
  catch { alert('Не удалось прочитать этот аудиофайл'); return; }
  L.dur0 = abuf.duration; L.start = 0; L.end = Math.min(abuf.duration, REC_LEN);
  const fmt = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  audioName = f.name; undoStack = []; cut = null; setEditMode('clip'); $('#aPausesBox').hidden = true;
  aDrop.style.display = 'none'; $('#aEd').classList.add('on');
  refreshAudioInfo(); computePeaks();
  drawWave(); syncSel(); syncCut(); checkVocals();
}
function computePeaks() {
  const ch = abuf.getChannelData(0), N = 600, step = Math.max(1, Math.floor(ch.length / N));
  peaks = Array.from({ length: N }, (_, i) => { let m = 0; for (let j = i * step; j < (i + 1) * step; j += 16) m = Math.max(m, Math.abs(ch[j] || 0)); return m; });
  const mx = Math.max(...peaks) || 1; peaks = peaks.map(p => p / mx);
}
function refreshAudioInfo() {
  const fmt = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  $('#aName').innerHTML = `${esc(audioName)}<small>${fmt(abuf.duration)} · ${abuf.sampleRate / 1000} кГц${undoStack.length ? ' · изменено' : ''}</small>`;
}

/* editing: cut a piece out, remove pauses, keep only the voice. Every edit makes a new buffer; "Отменить" goes back. */
let audioName = '', editMode = 'clip', cut = null, undoStack = [];
function setEditMode(m) {
  editMode = m; wave.classList.toggle('cutmode', m === 'cut');
  $$('.a-mode button').forEach(b => b.classList.toggle('on', b.dataset.m === m));
  $('#aToolHint').textContent = m === 'cut' ? 'Проведите мышью по волне, чтобы выделить кусок, который нужно убрать (края можно двигать), и нажмите «Удалить вырезанное».' : '';
  if (m === 'clip') { cut = null; syncCut(); }
}
function syncCut() {
  const el = $('#cutSel'), btn = $('#aCutDel');
  if (!cut || !L.dur0) { el.hidden = true; btn.disabled = true; btn.textContent = '✂ Удалить вырезанное'; return; }
  const w = wave.clientWidth; el.hidden = false;
  el.style.left = (cut.a / L.dur0 * w) + 'px'; el.style.width = ((cut.b - cut.a) / L.dur0 * w) + 'px';
  btn.disabled = cut.b - cut.a < 0.05; btn.textContent = `✂ Удалить вырезанное (${(cut.b - cut.a).toFixed(1)} с)`;
}
// the pieces [a,b] (seconds) of buf that stay, joined with tiny fades so the joins do not click
function keepRanges(buf, ranges) {
  const sr = buf.sampleRate, chs = buf.numberOfChannels, fade = Math.round(0.004 * sr);
  const lens = ranges.map(([a, b]) => Math.max(0, Math.round((b - a) * sr)));
  const out = actx.createBuffer(chs, Math.max(1, lens.reduce((x, y) => x + y, 0)), sr);
  for (let c = 0; c < chs; c++) {
    const srcd = buf.getChannelData(c), dst = out.getChannelData(c); let o = 0;
    ranges.forEach(([a], i) => {
      const s = Math.round(a * sr), n = lens[i];
      dst.set(srcd.subarray(s, s + n), o);
      for (let k = 0; k < Math.min(fade, n); k++) { if (i > 0) dst[o + k] *= k / fade; if (i < ranges.length - 1) dst[o + n - 1 - k] *= k / fade; }
      o += n;
    });
  }
  return out;
}
// where a moment of the old audio ends up after the removed pieces [a,b] are taken out
const shiftAfter = removed => t => t - removed.reduce((s, [a, b]) => s + (t >= b ? b - a : t > a ? t - a : 0), 0);
function commitEdit(newBuf, mapT) {
  stop();
  undoStack.push({ buf: abuf, start: L.start, end: L.end }); if (undoStack.length > 15) undoStack.shift();
  abuf = newBuf; L.dur0 = abuf.duration;
  L.start = Math.min(mapT(L.start), Math.max(0, L.dur0 - 1)); L.end = Math.min(Math.max(mapT(L.end), L.start + 1), L.dur0);
  cut = null; computePeaks(); refreshAudioInfo(); drawWave(); syncSel(); syncCut(); $('#aUndo').disabled = false;
}
$('.a-mode').onclick = e => { const b = e.target.closest('button[data-m]'); if (b && L.dur0) setEditMode(b.dataset.m); };
$('#aCutDel').onclick = () => {
  if (!cut || cut.b - cut.a < 0.05) return;
  const { a, b } = cut, keep = [[0, a], [b, abuf.duration]].filter(([x, y]) => y - x > 0.001);
  if (!keep.length) { toast('Нельзя вырезать всё аудио'); return; }
  commitEdit(keepRanges(abuf, keep), shiftAfter([[a, b]]));
  toast(`Вырезано ${(b - a).toFixed(1)} с`);
};
$('#aUndo').onclick = () => {
  const s = undoStack.pop(); if (!s) return; stop();
  abuf = s.buf; L.dur0 = abuf.duration; L.start = s.start; L.end = s.end; cut = null;
  computePeaks(); refreshAudioInfo(); drawWave(); syncSel(); syncCut(); $('#aUndo').disabled = !undoStack.length;
};
// pauses: stretches quieter than the speech, longer than "min", shortened to "keep"
function findPauses(buf, minLen, keep) {
  const sr = buf.sampleRate, win = Math.round(sr * 0.01), n = Math.floor(buf.length / win);
  const a = buf.getChannelData(0), b = buf.numberOfChannels > 1 ? buf.getChannelData(1) : a, rms = new Float32Array(n);
  for (let i = 0; i < n; i++) { let s = 0, c = 0; for (let j = i * win; j < (i + 1) * win; j += 2) { s += (a[j] * a[j] + b[j] * b[j]) * 0.5; c++; } rms[i] = Math.sqrt(s / c); }
  const sorted = Float32Array.from(rms).sort(), floor = sorted[Math.floor(n * 0.1)] || 0, loud = sorted[Math.floor(n * 0.9)] || 0;
  const thr = Math.min(Math.max(floor * 3, loud * 0.05, 0.002), loud * 0.2), found = []; let run = -1;
  for (let i = 0; i <= n; i++) {
    const quiet = i < n && rms[i] < thr;
    if (quiet && run < 0) run = i;
    if (!quiet && run >= 0) { const s = run * 0.01, e = i * 0.01; if (e - s >= minLen && e - s > keep + 0.02) found.push([s + keep / 2, e - keep / 2]); run = -1; }
  }
  return found;
}
function pausesPreview() {
  if (!L.dur0) return [];
  const f = findPauses(abuf, +$('#apMin').value || 0.6, +$('#apKeep').value || 0.25), saved = f.reduce((s, [x, y]) => s + y - x, 0);
  $('#apInfo').textContent = f.length ? `Найдено пауз: ${f.length}, станет короче на ${saved.toFixed(1)} с` : 'Длинных пауз не найдено';
  $('#apApply').disabled = !f.length; return f;
}
$('#aPauses').onclick = () => { const box = $('#aPausesBox'); box.hidden = !box.hidden; if (!box.hidden) pausesPreview(); };
$('#apMin').oninput = $('#apKeep').oninput = pausesPreview;
$('#apApply').onclick = () => {
  const f = pausesPreview(); if (!f.length) return;
  const keep = []; let pos = 0; f.forEach(([s, e]) => { keep.push([pos, s]); pos = e; }); keep.push([pos, abuf.duration]);
  commitEdit(keepRanges(abuf, keep.filter(([x, y]) => y - x > 0.001)), shiftAfter(f));
  $('#aPausesBox').hidden = true; toast(`Убрано пауз: ${f.length}`);
};
// voice only (needs the MelBand RoFormer node and model in ComfyUI)
let vocalsOk = null;
async function checkVocals() {
  if (!API) return;
  try { vocalsOk = await apiGet('/api/audio/info'); } catch { vocalsOk = { available: false, reason: 'ComfyUI не запущен.' }; }
  const b = $('#aVocals'); b.disabled = !vocalsOk.available;
  b.title = vocalsOk.available ? 'Оставить только голос, без музыки' : 'Недоступно: ' + vocalsOk.reason;
}
$('#aVocals').onclick = async () => {
  if (!L.dur0) return;
  if (abuf.duration > 360) { toast('Для отделения голоса аудио должно быть не длиннее 6 минут: сначала вырежьте нужное'); return; }
  const b = $('#aVocals'), old = b.textContent; b.disabled = true; b.textContent = 'Отделяем голос…'; stop();
  try {
    const fd = new FormData(); fd.append('file', wavSlice(abuf, 0, abuf.duration), 'a.wav');
    const nb = await actx.decodeAudioData(await apiSend('/api/audio/separate', { method: 'POST', body: fd, raw: 'arrayBuffer' }));
    commitEdit(nb, t => Math.min(t, nb.duration));
    toast('Голос отделён, музыки больше нет. Прослушайте (▶). Не понравилось — «Отменить».');
  } catch (e) { toast(e.message); }
  b.textContent = old; b.disabled = !(vocalsOk?.available);
};
function drawWave() {
  const r = wave.getBoundingClientRect(), dpr = devicePixelRatio || 1;
  cv.width = r.width * dpr; cv.height = r.height * dpr;
  const c = cv.getContext('2d'); c.scale(dpr, dpr); c.clearRect(0, 0, r.width, r.height);
  const cs = getComputedStyle(document.documentElement);
  const wc = { accent: cs.getPropertyValue('--accent').trim(), accent2: cs.getPropertyValue('--accent-2').trim(), idle: cs.getPropertyValue('--line-2').trim() };
  const bars = Math.floor(r.width / 3), mid = r.height / 2;
  const a = L.start / L.dur0, b = L.end / L.dur0;
  for (let i = 0; i < bars; i++) {
    const p = peaks[Math.floor(i / bars * peaks.length)] || 0, hgt = Math.max(2, p * (r.height - 16));
    const t = i / bars;
    c.fillStyle = t >= a && t <= b ? (t < (a + b) / 2 ? wc.accent : wc.accent2) : wc.idle;
    c.fillRect(i * 3, mid - hgt / 2, 2, hgt);
  }
}
function syncSel() {
  const w = wave.clientWidth;
  selEl.style.left = (L.start / L.dur0 * w) + 'px';
  selEl.style.width = ((L.end - L.start) / L.dur0 * w) + 'px';
  $('#aStart').value = L.start.toFixed(1); $('#aEnd').value = L.end.toFixed(1);
  const len = L.len();
  $('#aLen').textContent = `${len.toFixed(1)} с`;
  const n = $('#aNote');
  if (len > MAX_LEN) { n.className = 'note warn'; n.querySelector('span').textContent = `Максимум ${MAX_LEN} секунд — укоротите фрагмент.`; }
  else if (len > REC_LEN) { n.className = 'note warn'; n.querySelector('span').textContent = `Лучше держать фрагмент до ${REC_LEN} секунд — так синхронизация губ точнее. Длинный трек можно разбить на несколько клипов.`; }
  else { n.className = 'note'; n.querySelector('span').textContent = 'Оставьте небольшую паузу до и после слов — так губы начинают и заканчивают движение естественно.'; }
  drawWave(); refresh();
}
let drag = null;
wave.addEventListener('pointerdown', e => {
  if (!L.dur0) return;
  const w = wave.clientWidth, x = e.clientX - wave.getBoundingClientRect().left, t = x / w * L.dur0;
  const h = e.target.dataset.h;
  if (editMode === 'cut' && !(h === 'cl' || h === 'cr')) { drag = { mode: 'cnew', anchor: t }; cut = { a: t, b: t }; }
  else if (h === 'cl' || h === 'cr') drag = { mode: h };
  else drag = h ? { mode: h } : e.target === selEl ? { mode: 'move', off: t - L.start, len: L.len() } : { mode: 'new', anchor: t };
  wave.setPointerCapture(e.pointerId);
});
wave.addEventListener('pointermove', e => {
  if (!drag) return;
  const w = wave.clientWidth;
  const t = Math.min(L.dur0, Math.max(0, (e.clientX - wave.getBoundingClientRect().left) / w * L.dur0));
  const MIN = 1;
  if (drag.mode === 'cnew') { cut = { a: Math.min(drag.anchor, t), b: Math.max(drag.anchor, t) }; syncCut(); return; }
  if (drag.mode === 'cl') { cut.a = Math.min(t, cut.b - 0.05); syncCut(); return; }
  if (drag.mode === 'cr') { cut.b = Math.max(t, cut.a + 0.05); syncCut(); return; }
  if (drag.mode === 'l') L.start = Math.min(t, L.end - MIN);
  else if (drag.mode === 'r') L.end = Math.max(t, L.start + MIN);
  else if (drag.mode === 'move') { L.start = Math.min(Math.max(0, t - drag.off), L.dur0 - drag.len); L.end = L.start + drag.len; }
  else { L.start = Math.min(drag.anchor, t); L.end = Math.max(drag.anchor, t, L.start + MIN); }
  syncSel();
});
wave.addEventListener('pointerup', () => drag = null);
$('#aStart').onchange = e => { L.start = Math.min(Math.max(0, +e.target.value), L.end - 1); syncSel(); };
$('#aEnd').onchange = e => { L.end = Math.max(Math.min(L.dur0, +e.target.value), L.start + 1); syncSel(); };


$('#aPlay').onclick = () => src ? stop() : play();
function play() {
  src = actx.createBufferSource(); src.buffer = abuf; src.connect(actx.destination);
  src.start(0, L.start, L.len()); playStart = actx.currentTime;
  src.onended = stop;
  $('#aPlayIc').innerHTML = '<path d="M6 4h4v16H6zM14 4h4v16h-4z"/>';
  ph.style.display = 'block';
  const tick = () => {
    const t = L.start + (actx.currentTime - playStart);
    ph.style.left = (t / L.dur0 * wave.clientWidth) + 'px';
    raf = requestAnimationFrame(tick);
  };
  tick();
}
function stop() {
  if (src) { src.onended = null; try { src.stop(); } catch {} src = null; }
  cancelAnimationFrame(raf); ph.style.display = 'none';
  $('#aPlayIc').innerHTML = '<path d="M7 4v16l13-8z"/>';
}
  function mount(scope) {
  const lPrev = previewPanel($('#lPreview'), L, {
    extraLabel: 'Проходы',
    extra: () => {
      const [w, h] = resFor(L.aspect, L.q), s = QUALITY_ALL.find(x => x.id === L.q).px >= 1824 * 1024 ? 2 : 1.5;
      return `${r32(w / s)}×${r32(h / s)} → ×${s}`;
    },
    passes: () => [8, 4],
    twoPass: true,
    real: {
      url: '/api/generate/lipsync',
      form: () => {
        const fd = new FormData();
        fd.append('prompt', $('#lPrompt').value.trim());
        sizeForm(L, fd); fd.append('aspect', L.aspect); fd.append('quality', L.q); fd.append('ref_size', L.refSize);
        loraForm(L, fd); seedForm(L, fd);
        fd.append('audio', new File([wavSlice(abuf, L.start, L.end)], 'clip.wav', { type: 'audio/wav' }));
        fd.append('audio_len', L.len().toFixed(3));
        $$('.ref', refsEl).filter(r => r._file).forEach((r, i) => fd.append('ref' + i, r._file));
        return fd;
      },
    },
    target: () => $('#refs .ref img'),
    meta: () => ({ mode: 'lipsync', prompt: $('#lPrompt').value.trim(), sub: `${L.refs} реф. · аудио ${L.len().toFixed(1)} с` }),
    onDone: item => addToLib(item),
    validate: () => !L.dur0 ? 'Загрузите аудио' : !L.refs ? 'Добавьте хотя бы один референс — лицо персонажа' : L.len() > MAX_LEN ? `Фрагмент длиннее ${MAX_LEN} с — укоротите` : !$('#lPrompt').value.trim() ? 'Напишите промпт' : '',
  });
  const lUpd = paramsCard($('#lParams'), L, {
    n: 4, duration: false, onChange: () => {
      lPrev();
      $('#refHint').textContent = REF_HINT[L.refSize];
    },
    extraRow: `
      <div class="row">
        <div class="row-l">Точность переноса с изображений</div>
        <div class="seg" style="grid-template-columns:1fr 1fr">
          <button data-r="match">Быстро<small>изображения уменьшаются</small></button>
          <button data-r="max">Максимум<small>лучшее сходство</small></button>
        </div>
        <div class="hint" id="refHint"></div>
      </div>
      <div id="lLoras"></div>`,
  });
  lUpd();
  mountLoras($('#lLoras'), L, ['realism']);
    ui = { update: lUpd, refresh: lPrev };
    lUpd();
    scope.listen(window, 'resize', () => { if (L.dur0) (syncSel(), syncCut()); });
    if (L.dur0) { refreshAudioInfo(); computePeaks(); drawWave(); syncSel(); syncCut(); }
    checkVocals();   // after the page's modules are up: before that the check simply says "unavailable"
  }


  /* the details window fills this section with the job's settings; the page opens it first */
  async function edit(meta, files) {
    $('#lPrompt').value = String(meta.prompt || '');
    $$('#refs .ref.filled .x').forEach(x => x.click());
    const slots = $$('#refs .ref input');
    for (let i = 0; i < Math.min(slots.length, files.image.length); i++) await jobPut(slots[i], files.image[i].file);
    if (ASPECTS.some(a => a.id === meta.aspect)) L.aspect = meta.aspect;
    if (QUALITY_ALL.some(q => q.id === meta.q)) L.q = meta.q;
    if (meta.ref_size) L.refSize = meta.ref_size;
    if (meta.seed != null && meta.seed !== '') useSeed(L, meta.seed);
    if (files.audio[0]) {   // голос задания: тот же фрагмент встаёт в аудиоредактор
      if (L.dur0) $('#aRemove').click();
      await setDropFile($('#aFile'), files.audio[0].file);
      const t0 = performance.now();
      while (!L.dur0 && performance.now() - t0 < 6000) await new Promise(r => setTimeout(r, 80));
      if (L.dur0) select(0, meta.len > 0 ? Math.min(L.dur0, +meta.len) : Math.min(L.dur0, REC_LEN));
    }
    update();
  }

  /* «Повторить» in the viewer: same prompt, format and reference-size choice, then open the section */
  function repeat(it) {
    Object.assign(L, { aspect: it.aspect, q: it.q, refSize: it.ref_size || L.refSize });
    $('#lPrompt').value = it.prompt;
    goTo('lipsync');
    update();
  }

  /* sets the fragment without going through the two number fields */
  function select(start, end) {
    if (!L.dur0) return;
    L.start = Math.max(0, Math.min(start, Math.max(0, L.dur0 - 1)));
    L.end = Math.min(L.dur0, Math.max(end, L.start + 1));
    syncSel();
    update();
  }

  /* the fragment as a WAV File for the chat: null when there is no audio */
  function audioClip() {
    if (!abuf || !L.dur0) return null;
    return new File([wavSlice(abuf, L.start, L.end)], 'lipsync.wav', { type: 'audio/wav' });
  }

  function disposeLipsync() {
    stop();
    try { ui?.refresh.dispose(); } catch (e) { /* already gone */ }
    const view = $('#view-lipsync');
    if (!view) return;
    window.h3Lifecycle.pauseMedia(view);
    view.querySelectorAll('img[src^="blob:"]').forEach(img => {
      try { URL.revokeObjectURL(img.src); } catch (e) { /* already gone */ }
    });
  }

  window.h3Lipsync = {
    get state() { return L; },
    update, refresh, edit, repeat, select, audioClip, loadAudio,
  };

  window.h3Modes.register("lipsync", {
    mount,
    leave: () => { stop(); ui?.refresh.pause(); window.h3Lifecycle.pauseMedia($('#view-lipsync')); },
    dispose: disposeLipsync,
  });
})();
