/* The widgets every mode shares: one preview panel, one parameters card, the media slots, the
 * size/seed controls and the LoRA switches.
 *
 * These used to be a set of functions at the top of the page with the whole page in scope. A copy
 * pasted into the next section looked the same and behaved differently a release later. Here the
 * widget owns its markup and its events, and the page lends it only what it cannot know itself:
 * toast/fmtDate, whether the engine is online, the Library and drafts, the job queue and the
 * prompt/document subscriptions. `configure()` is called once by the page before the first panel.
 *
 * A classic script, loaded before the page: `previewPanel` is called while the page is parsed.
 */

(function () {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const normalize = it => ({ ...it, date: it.date instanceof Date ? it.date : new Date(it.date) });

  /* Defaults make a forgotten dependency visible in the console instead of silent over a wrong
     Library list; the page replaces all of them in `configure` before the first widget exists. */
  let deps = {
    toast: msg => console.warn("components: no toast hooked up —", msg),
    fmtDate: d => d instanceof Date ? d.toLocaleString("ru") : String(d),
    engineOnline: () => true,
    online: () => true,
    byId: () => null,
    draftById: () => null,
    drafts: () => [],
    library: () => [],
    addDraftHook() {},
    removeDraftHook() {},
    keepDraft: async it => it,
    deleteClip: async () => false,
    projectOk: () => true,
    openUpscale() {},
    addJob() {},
    onPrompt() {},
    offPrompt() {},
    previewHandlers: new Map(),
    goTo() {},
    postJSON: async () => ({}),
  };
  function configure(next) { deps = { ...deps, ...next }; }

/* ---------- resolution / duration math (from the workflows) ---------- */
const ASPECTS = [
  { id: '16:9', w: 16, h: 9, name: 'Горизонт' },
  { id: '9:16', w: 9, h: 16, name: 'Вертикаль' },
  { id: '1:1', w: 1, h: 1, name: 'Квадрат' },
  { id: '4:3', w: 4, h: 3, name: '4:3' },
  { id: '3:4', w: 3, h: 4, name: '3:4' },
  { id: '21:9', w: 21, h: 9, name: 'Кино 21:9' },
];
// the sizes of the original workflow's table (16:9 base). The buttons show the eight most used ones as plain sizes;
// the rest stay known (saved settings and old library items use them) but are not offered
const QUALITY_ALL = [
  { id: '0.2', px: 608 * 352, res: '608×352' },
  { id: '0.3', px: 736 * 416, res: '736×416', sub: 'самый быстрый', show: 1 },
  { id: '480p', px: 864 * 480, res: '864×480', sub: 'быстро', show: 1 },
  { id: '0.5', px: 960 * 544, res: '960×544', sub: 'под ×2 = 1920', show: 1 },
  { id: '0.6', px: 1056 * 608, res: '1056×608', show: 1 },
  { id: '0.7', px: 1152 * 640, res: '1152×640' },
  { id: '0.8', px: 1216 * 672, res: '1216×672', show: 1 },
  { id: '720p', px: 1280 * 736, res: '1280×736', sub: 'баланс', show: 1 },
  { id: 'HD+', px: 1376 * 768, res: '1376×768', sub: 'чётче', show: 1 },
  { id: '1.2', px: 1504 * 832, res: '1504×832', show: 1 },
  { id: '1.5', px: 1664 * 928, res: '1664×928' },
  { id: '1.8', px: 1824 * 1024, res: '1824×1024' },
  { id: '1080p', px: 1920 * 1088, res: '1920×1088', sub: 'максимум, долго', show: 1 },
];
const QUALITY = QUALITY_ALL.filter(q => q.show);
const qLabel = q => q.res;
const r32 = v => Math.max(32, Math.round(v / 32) * 32);
function resFor(aspect, q) {
  const a = ASPECTS.find(x => x.id === aspect), p = QUALITY_ALL.find(x => x.id === q).px;
  return [r32(Math.sqrt(p * a.w / a.h)), r32(Math.sqrt(p * a.h / a.w))];
}
// frame count formula from the workflow: 24 fps, length = 17k + 5
function frames(sec) { let f = Math.max(5, Math.round(sec * 24)); return f + (5 - f % 17 + 17) % 17; }

/* ---------- shared params / preview builders ---------- */
function paramsCard(el, st, opts) {
  el.innerHTML = `
    <div class="card-h"><h3><span class="n">${opts.n}</span>Параметры</h3></div>
    <div class="row">
      <div class="row-l">Формат</div>
      <div class="chips">${ASPECTS.map(a => {
        const k = 18 / Math.max(a.w, a.h);
        return `<button class="chip" data-a="${a.id}"><i class="ar" style="width:${a.w * k}px;height:${a.h * k}px"></i>${a.name}${a.name !== a.id ? ` <small>${a.id}</small>` : ''}</button>`;
      }).join('')}</div>
    </div>
    <div class="row">
      <div class="row-l">Качество <span class="resv"></span></div>
      <div class="seg seg-q">${QUALITY.map(q => `<button data-q="${q.id}">${qLabel(q)}<small>${q.sub || '&nbsp;'}</small></button>`).join('')}</div>
      <div class="hint">Чем меньше размер, тем быстрее расчёт; чем больше, тем чётче изображение. Размеры в кнопках для формата 16:9, для других форматов рядом с «Качество» показан точный размер.</div>
    </div>
    <div class="row">
      <div class="row-l">Свой размер <small class="hint">(необязательно)</small></div>
      <div class="path on" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <input class="cw-w" inputmode="numeric" placeholder="ширина" style="width:90px"> <span>×</span>
        <input class="cw-h" inputmode="numeric" placeholder="высота" style="width:90px">
        <button class="btn cw-reset" type="button" hidden style="padding:6px 12px;font-size:12.5px">Сбросить</button>
      </div>
      <div class="hint cw-hint">Впишите нужные числа: программа сама округлит их до значений, которые понимает модель (кратно 32). Пусто — размер по формату и качеству выше.</div>
    </div>
    ${opts.extraRow || ''}
    ${opts.duration ? `
    <div class="row">
      <div class="row-l">Длительность <span class="durv"></span></div>
      <input type="range" min="3" max="25" step="1" class="dur">
      <div class="ticks"><span>3 с</span><span>8</span><span>14</span><span>19</span><span>25 с</span></div>
    </div>` : ''}
    <div class="note warn vwarn" hidden>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3 2 21h20L12 3Z"/><path d="M12 10v5M12 18h0"/></svg>
      <span>1080p на длинном ролике может не поместиться в видеопамять. Если генерация упадёт — выберите 720p или сократите длительность.</span>
    </div>
    <details style="margin-top:16px">
      <summary>Дополнительно</summary>
      <div class="row" style="margin-top:14px">
        <div class="row-l">Seed — «номер варианта»</div>
        <div class="seg seed-seg" style="grid-template-columns:1fr 1fr">
          <button data-sf="0">Случайный<small>каждый раз новое видео</small></button>
          <button data-sf="1">Фиксированный<small>тот же вариант снова</small></button>
        </div>
        <div class="path on seed-row" hidden>
          <input class="seed-in" inputmode="numeric" spellcheck="false" placeholder="число, например 123456789">
          <button class="btn seed-new" type="button">🎲 Новый</button>
          <button class="btn seed-copy" type="button">Копировать</button>
        </div>
        <div class="hint seed-hint"></div>
      </div>
    </details>`;
  const upd = () => {
    $$('.chip', el).forEach(c => c.classList.toggle('on', c.dataset.a === st.aspect));
    $$('.seg button[data-q]', el).forEach(c => c.classList.toggle('on', c.dataset.q === st.q));
    $$('.seg button[data-r]', el).forEach(c => c.classList.toggle('on', c.dataset.r === st.refSize));
    const [w, h] = st.cw && st.ch ? [st.cw, st.ch] : resFor(st.aspect, st.q);
    $('.resv', el).textContent = `${w}×${h}` + (st.cw && st.ch ? ' · свой' : '');
    $('.cw-reset', el).hidden = !(st.cw || st.ch);
    $$('.seg button[data-q]', el).forEach(c => c.style.opacity = st.cw && st.ch ? .45 : '');
    if (opts.duration) {
      const d = $('.dur', el); d.value = st.dur;
      d.style.setProperty('--p', ((st.dur - 3) / 22 * 100) + '%');
      $('.durv', el).textContent = `${st.dur} с · ${frames(st.dur)} кадров` + (st.dur > 15 ? ' · дольше 15 с модель держит качество хуже' : '');
    }
    $('.vwarn', el).hidden = !(st.q === '1080p' && st.len() > 8);
    $$('.seed-seg button', el).forEach(b => b.classList.toggle('on', (b.dataset.sf === '1') === !!st.seedFixed));
    $('.seed-row', el).hidden = !st.seedFixed;
    if ($('.seed-in', el) !== document.activeElement) $('.seed-in', el).value = st.seed || '';
    $('.seed-hint', el).textContent = st.seedFixed
      ? 'Одинаковый seed + одинаковые настройки и промпт = одинаковое видео. Удобно менять что-то одно и сравнивать. Понравившийся seed можно взять из библиотеки или сразу после генерации.'
      : 'Каждый раз получается новый вариант. Seed понравившегося клипа сохраняется в нём — потом его можно зафиксировать.';
    opts.onChange();
  };
  st._upd = upd;  // lets the library and the preview panel switch this mode to a given seed
  el.addEventListener('click', e => {
    if (((st.locked && st.locked() && e.target.closest('.chip[data-a]')) || ((st.lockedQ ? st.lockedQ() : st.locked && st.locked()) && e.target.closest('.seg button[data-q]')))) { deps.toast('Формат и качество нельзя менять, пока в проекте есть готовые сцены: все сцены должны быть одного размера. Чтобы сменить, нажмите «Начать заново».'); return; }
    const c = e.target.closest('.chip[data-a]'); if (c) { const ch = st.aspect !== c.dataset.a; st.aspect = c.dataset.a; st.userAspect = true; upd(); if (ch) st.onSizeChange?.('aspect'); }
    const q = e.target.closest('.seg button[data-q]'); if (q) { const ch = st.q !== q.dataset.q; st.q = q.dataset.q; upd(); if (ch) st.onSizeChange?.('q'); }
    const r = e.target.closest('.seg button[data-r]'); if (r) { st.refSize = r.dataset.r; upd(); }
    const sf = e.target.closest('.seed-seg button'); if (sf) { st.seedFixed = sf.dataset.sf === '1'; if (st.seedFixed && !st.seed) st.seed = newSeed(); upd(); }
    if (e.target.closest('.seed-new')) { st.seed = newSeed(); upd(); }
    if (e.target.closest('.seed-copy')) { navigator.clipboard?.writeText(String(st.seed || '')); deps.toast('Seed скопирован: ' + st.seed); }
  });
  const cwApply = () => {
    const iw = $('.cw-w', el), ih = $('.cw-h', el), hint = $('.cw-hint', el);
    if (st.lockedQ ? st.lockedQ() : st.locked && st.locked()) { iw.value = st.cw || ''; ih.value = st.ch || ''; deps.toast('Размер нельзя менять, пока в проекте есть готовые сцены. Нажмите «Начать заново».'); return; }
    const a = parseInt(iw.value, 10), b = parseInt(ih.value, 10);
    if (a >= 64 && b >= 64) {
      st.cw = Math.min(2048, Math.max(64, Math.round(a / 32) * 32)); st.ch = Math.min(2048, Math.max(64, Math.round(b / 32) * 32));
      iw.value = st.cw; ih.value = st.ch;
      hint.textContent = `Размер будет ${st.cw}×${st.ch}` + (st.cw !== a || st.ch !== b ? ` (округлено с ${a}×${b})` : '') + (st.cw * st.ch > 1920 * 1088 ? '. Это больше 1080p: может не хватить видеопамяти.' : '');
    } else { st.cw = st.ch = 0; hint.textContent = 'Впишите оба числа (от 64 и больше): программа округлит их до кратных 32. Пусто — размер по формату и качеству.'; }
    upd(); st.onSizeChange?.('q');
  };
  $('.cw-w', el).onchange = $('.cw-h', el).onchange = cwApply;
  $('.cw-reset', el).onclick = () => { $('.cw-w', el).value = $('.cw-h', el).value = ''; st.cw = st.ch = 0; cwApply(); };
  $('.seed-in', el).oninput = e => { st.seed = e.target.value.replace(/\D/g, '').slice(0, 18); e.target.value = st.seed; };
  if (opts.duration) $('.dur', el).oninput = e => { st.dur = +e.target.value; upd(); };
  return upd;
}
const SZ_ROW = `<div class="row sz-row"><div class="row-l">Свой размер <span class="hint">(необязательно)</span></div>
  <div class="path on" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><button class="btn sz-p" data-p="0" type="button" title="Обычный размер (около 1 МП): быстро, как эскиз" style="padding:6px 12px;font-size:12.5px">Эскиз</button><button class="btn sz-p" data-p="2k" type="button" title="Сразу 2К: по длинной стороне 2048" style="padding:6px 12px;font-size:12.5px">2К</button><input class="sz-w" inputmode="numeric" placeholder="ширина" style="width:90px"> <span>×</span> <input class="sz-h" inputmode="numeric" placeholder="высота" style="width:90px"> <button class="btn sz-reset" type="button" hidden style="padding:6px 12px;font-size:12.5px">Сбросить</button></div>
  <div class="hint sz-hint">Размер подстраивается под шаг модели, кратно 32: например, 1920×1080 станет 1920×1088. Пусто — размер по формату выше.</div></div>`;
function szWire(el, st, onChange, getBase) {
  const iw = $('.sz-w', el), ih = $('.sz-h', el), hint = $('.sz-hint', el), rs = $('.sz-reset', el);
  const def = hint.textContent;
  const apply = () => {
    const a = parseInt(iw.value, 10), b = parseInt(ih.value, 10);
    if (a >= 64 && b >= 64) {
      let k = a * b > 2200000 ? Math.sqrt(2200000 / (a * b)) : 1;
      const r = v => Math.min(2048, Math.max(256, Math.round(v * k / 32) * 32));
      st.cw = r(a); st.ch = r(b); iw.value = st.cw; ih.value = st.ch;
      hint.textContent = `Будет ${st.cw}×${st.ch}` + (st.cw !== a || st.ch !== b ? ` (округлено с ${a}×${b}, шаг 32)` : '') + (st.cw * st.ch > 1500000 ? '. Это больше 1,5 МП: дольше и больше видеопамяти.' : '');
    } else { st.cw = st.ch = 0; hint.textContent = def; }
    rs.hidden = !(st.cw || st.ch);
    onChange();
  };
  iw.onchange = ih.onchange = apply;
  $$('.sz-p', el).forEach(b => b.onclick = () => {
    if (b.dataset.p === '0') { iw.value = ih.value = ''; apply(); return; }
    const [bw, bh] = (getBase && getBase()) || [1376, 768], k = 2048 / Math.max(bw, bh);
    iw.value = Math.round(bw * k / 32) * 32; ih.value = Math.round(bh * k / 32) * 32; apply();
    if (st.engine === 'krea') deps.toast('Krea 2 лучше всего работает около 1 МП: для неё надёжнее эскиз и потом «Увеличить до 2К» в Библиотеке');
  });
  rs.onclick = () => { iw.value = ih.value = ''; apply(); };
}
function sizeForm(st, fd) { if (st.cw && st.ch) { fd.append('width', st.cw); fd.append('height', st.ch); } }
const newSeed = () => String(Math.floor(Math.random() * 2 ** 50));
// the seed goes to the server only when the user fixed it; otherwise the server draws a new one
function seedForm(st, fd) { if (st.seedFixed && st.seed) fd.append('seed', st.seed); }
// make a mode use this exact seed (from the library or from a finished video)
function useSeed(st, seed, go) {
  st.seedFixed = true; st.seed = String(seed); st._upd?.();
  if (go) deps.goTo(go);
  deps.toast(`Seed ${seed} зафиксирован в этом режиме`);
}

/* ---------- live preview rendering (demo: imitates denoising toward a target) ---------- */
const offCv = document.createElement('canvas'), noiseCv = document.createElement('canvas');
noiseCv.width = 160; noiseCv.height = 100;
function drawCover(c, src, w, h, z = 1, sx = 0) {
  const iw = src.naturalWidth || src.width, ih = src.naturalHeight || src.height;
  const s = Math.max(w / iw, h / ih) * z, dw = iw * s, dh = ih * s;
  c.drawImage(src, (w - dw) / 2 + sx * w, (h - dh) / 2, dw, dh);
}
function makeScene(seed = Math.random()) {
  const c = document.createElement('canvas'); c.width = 320; c.height = 200;
  const x = c.getContext('2d'); let r = seed * 9301;
  const rnd = () => (r = (r * 9301 + 49297) % 233280) / 233280;
  x.fillStyle = `hsl(${340 + rnd() * 30},40%,8%)`; x.fillRect(0, 0, 320, 200);
  for (let i = 0; i < 6; i++) {
    const g = x.createRadialGradient(rnd() * 320, rnd() * 200, 0, rnd() * 320, rnd() * 200, 60 + rnd() * 140);
    const hue = [350, 10, 25, 220, 280][Math.floor(rnd() * 5)];
    g.addColorStop(0, `hsla(${hue},${60 + rnd() * 30}%,${40 + rnd() * 25}%,.9)`); g.addColorStop(1, 'transparent');
    x.fillStyle = g; x.fillRect(0, 0, 320, 200);
  }
  return c;
}
function renderLive(ctx, W, H, src, p, t) {
  const sw = Math.max(4, Math.round(4 + p * p * 110));
  offCv.width = sw; offCv.height = Math.max(3, Math.round(sw * H / W));
  const o = offCv.getContext('2d');
  drawCover(o, src, offCv.width, offCv.height, 1.06 + Math.sin(t / 1300) * 0.02, Math.sin(t / 900) * 0.02);
  ctx.imageSmoothingEnabled = true; ctx.drawImage(offCv, 0, 0, W, H);
  const a = (1 - p) * 0.85;
  if (a > 0.02) {
    const n = noiseCv.getContext('2d'), d = n.createImageData(160, 100);
    for (let i = 0; i < d.data.length; i += 4) { const v = Math.random() * 255; d.data[i] = v; d.data[i + 1] = v * .7; d.data[i + 2] = v * .8; d.data[i + 3] = 255; }
    n.putImageData(d, 0, 0);
    ctx.globalAlpha = a; ctx.imageSmoothingEnabled = false; ctx.drawImage(noiseCv, 0, 0, W, H); ctx.globalAlpha = 1;
  }
}
const fmtTime = s => { s = Math.round(s); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };

/* A finished clip plays with sound. Browsers (and WebView2) sometimes refuse an unmuted autoplay:
   then the clip starts muted and a «Включить звук» chip appears over it — one click puts the sound back. */
function playWithSound(v) {
  v.muted = false;
  try { v.volume = 1; } catch (_) {}
  const p = v.play();
  if (p && p.catch) p.catch(() => {
    v.muted = true;
    const q = v.play(); if (q && q.catch) q.catch(() => {});
    soundChip(v);
  });
}
function soundChip(v) {
  const host = v.closest('.frame, .cw-frame') || v.parentElement;
  if (!host) return;
  let b = host.querySelector('.snd');
  if (!b) {
    b = document.createElement('button');
    b.type = 'button'; b.className = 'snd'; b.textContent = '🔊 Включить звук';
    host.append(b);
  }
  b.hidden = false;
  b.onclick = e => {
    e.stopPropagation(); e.preventDefault();
    b.hidden = true; v.muted = false;
    try { v.volume = 1; } catch (_) {}
    const p = v.play(); if (p && p.catch) p.catch(() => {});
  };
  if (!v.dataset.sndHook) {   // the sound turned on by hand in the player: the chip is no longer needed
    v.dataset.sndHook = '1';
    v.addEventListener('volumechange', () => { if (!v.muted) hideSoundChip(host); });
  }
}
function hideSoundChip(host) { host?.querySelector?.('.snd')?.remove(); }

/* A piece of decoded audio as 16-bit PCM WAV: used by the lipsync editor and by the agent when it
   sends a fragment of a clip to the chat. Pure — no DOM, no state. */
function wavSlice(buf, t0, t1) {
  const sr = buf.sampleRate, ch = Math.min(2, buf.numberOfChannels);
  const a = Math.floor(t0 * sr), n = Math.max(0, Math.floor(t1 * sr) - a);
  const out = new DataView(new ArrayBuffer(44 + n * ch * 2));
  const str = (o, s) => [...s].forEach((c, i) => out.setUint8(o + i, c.charCodeAt(0)));
  str(0, 'RIFF'); out.setUint32(4, 36 + n * ch * 2, true); str(8, 'WAVEfmt ');
  out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, ch, true);
  out.setUint32(24, sr, true); out.setUint32(28, sr * ch * 2, true); out.setUint16(32, ch * 2, true);
  out.setUint16(34, 16, true); str(36, 'data'); out.setUint32(40, n * ch * 2, true);
  const data = Array.from({ length: ch }, (_, c) => buf.getChannelData(c));
  let o = 44;
  for (let i = 0; i < n; i++) for (let c = 0; c < ch; c++) {
    const s = Math.max(-1, Math.min(1, data[c][a + i])); out.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true); o += 2;
  }
  return new Blob([out.buffer], { type: 'audio/wav' });
}

function previewPanel(el, st, opts) {
  el.innerHTML = `
    <div class="screen"><div class="frame">
      <div class="res"></div>
      <canvas class="live" hidden></canvas>
      <img class="live-img" alt="" hidden>
      <video class="live-vid" autoplay loop muted playsinline hidden style="width:100%;height:100%;object-fit:contain"></video>
      <video class="result" controls loop playsinline hidden></video>
      <div class="wait" hidden></div>
      <div class="live-hud" hidden><span class="live-dot"></span><span class="hud-t">Предпросмотр</span><span class="hud-s"></span></div>
      <div class="bar"></div>
    </div></div>
    <div class="summary">
      <div class="sum"><small>Разрешение</small><b class="s-res"></b></div>
      <div class="sum"><small>Длина</small><b class="s-len"></b></div>
      <div class="sum"><small>Кадров</small><b class="s-fr"></b></div>
      <div class="sum"><small>${opts.extraLabel}</small><b class="s-ex"></b></div>
    </div>
    <div class="pbox" hidden>
      <div class="pbox-top"><span class="pbox-l"></span><b class="pbox-p">0%</b></div>
      <div class="pbox-bar"><i></i></div>
      <div class="pbox-badges"></div>
      <div class="pbox-chip ok"><span class="pbox-dot"></span><span class="pbox-t"></span></div>
    </div>
    <button class="go"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4z"/></svg>Сгенерировать</button>
    <button class="stop" hidden><i></i>Остановить генерацию</button>
    <div class="cloud-line" hidden>☁ Генерация пойдёт в облако <b>RunningHub</b> (переключить — в разделе «Модели»)</div>
    <div class="status"></div>
    <div class="cw-hh vh-hh" hidden>Прошлые клипы — нажмите, чтобы открыть</div>
    <div class="cw-hist vh-hist"></div>`;
  const frame = $('.frame', el), go = $('.go', el), stopB = $('.stop', el), status = $('.status', el);
  const live = $('.live', el), hud = $('.live-hud', el), bar = $('.bar', el);
  const liveImg = $('.live-img', el), result = $('.result', el), wait = $('.wait', el), liveVid = $('.live-vid', el);
  const vhHh = $('.vh-hh', el), vhHist = $('.vh-hist', el);
  const STEP_MS = 420;
  let run = null, shown = null;   // shown: the clip in the player — a draft of this section or a Library one
  const pbox = $('.pbox', el), pboxBar = $('.pbox-bar i', el), pboxChip = $('.pbox-chip', el);

  /* the big bar + a colour chip: green = alive, yellow = quiet for a while, red = probably stuck / connection lost */
  function health(p, stepLine, badges) {
    pbox.hidden = false;
    $('.pbox-l', el).textContent = stepLine;
    $('.pbox-badges', el).innerHTML = (badges || []).map(b => `<span${b.eta ? ' class="eta"' : ''}>${esc(b.t)}</span>`).join('');
    $('.pbox-p', el).textContent = Math.round(p * 100) + '%';
    pboxBar.style.width = (p * 100) + '%';
    const now = performance.now(), idle = (now - (run.lastEv || run.t0)) / 1000;
    const sampling = run.stage && run.stage.startsWith('sample');
    const per = run.step >= 2 && run.step1T ? (run.stepT - run.step1T) / (run.step - 1) / 1000 : 0;
    const warnAt = sampling ? Math.max(90, per * 3) : run.stage === 'queued' ? 1e9 : 300;
    const badAt = sampling ? Math.max(240, per * 6) : run.stage === 'queued' ? 1e9 : 600;
    let lvl = 'ok', txt = 'Всё идёт, программа получает данные от ComfyUI';
    if (run.stopping) {
      const s = Math.round((now - run.stopping) / 1000);
      lvl = s > 15 ? 'warn' : 'ok'; txt = `Останавливаем… ${s} с` + (s > 15 ? ' · ComfyUI не отвечает на отмену, программа перезапустит движок' : '');
    } else if (!deps.engineOnline()) { lvl = 'bad'; txt = 'Обрыв связи: ComfyUI не отвечает или закрылся. Дождитесь возврата или перезапустите движок'; }
    else if (run.stage === 'queued') txt = 'В очереди: ждёт, пока освободится ComfyUI';
    else if (idle > badAt) { lvl = 'bad'; txt = `Нет ответа уже ${fmtTime(idle)}. Похоже, зависло. Нажмите «Остановить генерацию» и запустите снова (можно с меньшим размером)`; }
    else if (idle > warnAt) { lvl = 'warn'; txt = `Тишина ${fmtTime(idle)}. ${sampling ? 'Шаг идёт долго, на слабой карте это бывает' : 'Возможно, грузится большая модель'}. Если станет красным, то зависло`; }
    else if (sampling && run.step) txt = `Всё идёт · последний шаг был ${fmtTime(idle)} назад`;
    pboxChip.className = 'pbox-chip ' + lvl; pbox.classList.toggle('warn', lvl === 'warn'); pbox.classList.toggle('bad', lvl === 'bad');
    $('.pbox-t', el).textContent = txt;
  }

  function resetFrame() {
    pbox.hidden = true;
    [live, hud, liveImg, liveVid, result, wait].forEach(x => x.hidden = true);
    result.pause(); result.removeAttribute('src');
    URL.revokeObjectURL(liveImg.dataset.url || '');
    delete liveImg.dataset.url;
    hideSoundChip(frame);
    $('.res', frame).hidden = false; frame.classList.remove('done');
  }

  /* ----- a finished clip: a draft («Добавить в библиотеку») or a Library video with its own buttons ----- */
  // the buttons fit the clip: a draft waits for the Library button, its result can still be saved, enlarged or redone
  function resultActions(it, took, fresh) {
    const draft = !!it.preview && !deps.byId(it.id);
    const head = draft
      ? (took ? `Готово за ${took} — черновик, в Библиотеку не добавлен.` : 'Черновик, в Библиотеку не добавлен.')
      : (took ? `Готово за ${took} — видео в Библиотеке.` : 'Видео в Библиотеке.');
    // the text and the buttons are one wrapping row with even gaps: no stray spaces, nothing crowds
    status.innerHTML = `<div class="v-status"><span class="v-head">${head} Seed: `
      + `<b style="font-family:var(--mono)">${esc(String(it.seed ?? '—'))}</b></span>`
      + '<span class="v-actions" id="vActions"></span></div>';
    const acts = $('#vActions', el);
    const add = (id, label, { pri = false, title = '' } = {}) => acts.insertAdjacentHTML(
      'beforeend', `<button class="btn${pri ? ' pri' : ''} v-act" id="${id}" type="button"`
      + `${title ? ` title="${title}"` : ''}>${label}</button>`);
    if (draft) add('vKeepBtn', 'Добавить в библиотеку', { pri: true });
    // any clip can be deleted from here, a Library one included: the file goes to the Recycle Bin
    add('vDelBtn', 'Удалить', { title: draft
      ? 'Удалить черновик: файл уйдёт в корзину, Библиотека не изменится'
      : 'Удалить клип из Библиотеки: файл уйдёт в корзину' });
    if (it.seed != null) add('keepSeed', 'Зафиксировать');
    add('upBtn', 'Увеличить видео', { pri: true, title: 'Увеличить разрешение и добавить чёткости (SeedVR2)' });
    $('#upBtn', el).onclick = () => deps.openUpscale(deps.byId(it.id) || normalize(it));
    $('#keepSeed', el)?.addEventListener('click', () => useSeed(st, it.seed));
    $('#vDelBtn', el)?.addEventListener('click', ev => deleteClipFlow(it, ev));
    $('#vKeepBtn', el)?.addEventListener('click', async () => {
      const b = $('#vKeepBtn', el); b.disabled = true;
      const kept = await deps.keepDraft(it);
      if (!kept) { b.disabled = false; return; }
      shown = kept; result.dataset.dragid = kept.id; resultActions(kept, took, fresh); refreshHist();
      opts.onShow?.(kept);
    });
    // beginner flow: try another variant, or redo this very one (same seed) in a better size
    if (!fresh) return;
    add('otherBtn', 'Другой вариант', { title: 'Новый вариант с другим seed, настройки те же' });
    $('#otherBtn', el).addEventListener('click', () => { st.seedFixed = false; st._upd?.(); go.onclick(); });
    const curQ = QUALITY_ALL.find(x => x.id === st.q), goodQ = QUALITY_ALL.find(x => x.id === '720p');
    if (it.seed != null && curQ && !(st.cw && st.ch) && curQ.px < goodQ.px) {
      add('goodBtn', 'В хорошем качестве', { title: 'Тот же вариант (seed сохранится), размер 1280×736. '
        + 'Результат будет похож, но не пиксель в пиксель' });
      $('#goodBtn', el).addEventListener('click', () => { st.seedFixed = true; st.seed = String(it.seed); st.q = '720p'; st._upd?.(); go.onclick(); });
    }
  }
  // the clips of this section that are not in the Library yet: they would otherwise be lost when the next one is made
  function historyItems() {
    const mode = opts.meta && opts.meta().mode;
    return [...deps.drafts().filter(x => x.kind !== 'image' && x.mode === mode && !x.missing && x.url),
            ...deps.library().filter(x => x.kind !== 'image' && x.mode === mode && !x.missing && x.url)]
      .filter((x, i, a) => a.findIndex(y => y.id === x.id) === i)
      .filter(x => deps.projectOk(x))      // one work at a time: the project switch shows the rest
      .sort((a, b) => b.date - a.date);
  }
  function refreshHist() {
    if (opts.history === false) { vhHh.hidden = true; vhHist.innerHTML = ''; return; }
    // a clip deleted in another window must not stay in the player
    if (shown && !deps.byId(shown.id) && !deps.draftById(shown.id)) {
      shown = null;
      resetFrame();
      status.textContent = '';
    }
    const mine = historyItems().filter((it, i) => it.preview || i < 12);
    if (!mine.length) {
      // say why the list is empty instead of looking broken: there may be clips in other projects
      const mode = opts.meta && opts.meta().mode;
      const elsewhere = [...deps.drafts(), ...deps.library()].some(x => x.kind !== 'image' && x.mode === mode
        && !x.missing && x.url && !deps.projectOk(x));
      vhHh.hidden = !elsewhere;
      vhHh.textContent = elsewhere ? 'В этом проекте клипов нет. Все проекты — кнопкой «Этот проект» в шапке' : '';
      vhHist.innerHTML = '';
      return;
    }
    vhHh.hidden = false;
    vhHh.textContent = 'Прошлые клипы — нажмите, чтобы открыть';
    vhHist.innerHTML = mine.map(x => `<button draggable="true" class="cw-th${x.preview ? ' draft' : ''}${shown && shown.id === x.id ? ' on' : ''}" data-id="${x.id}" type="button" title="${esc((x.preview ? 'Черновик, не в Библиотеке · ' : '') + (x.prompt || x.sub || 'клип') + ' · ' + deps.fmtDate(x.date))}"><i class="cw-x" data-del="${x.id}" title="${x.preview ? 'Удалить черновик' : 'Удалить клип из Библиотеки'}" role="button">✕</i><video src="${x.url}#t=0.5" preload="metadata" muted playsinline></video></button>`).join('');
  }
  function showItem(it) {
    if (!it || it.missing) return false;
    shown = it;
    [liveImg, liveVid, wait].forEach(x => x.hidden = true);
    $('.res', frame).hidden = true;
    result.src = it.url; result.dataset.dragid = it.id; result.draggable = true; result.hidden = false;
    playWithSound(result);
    frame.classList.add('done'); $('.hud-t', el).textContent = 'Готово'; $('.hud-s', el).textContent = '';
    resultActions(it, '', false); refreshHist();
    opts.onShow?.(it);
    return true;
  }
  async function deleteClipFlow(it, ev) {
    const wasShown = !!(shown && shown.id === it.id);
    const at = ev && ev.clientX != null ? { x: ev.clientX, y: ev.clientY } : null;
    if (!(await deps.deleteClip(it, at))) return;
    if (wasShown) {
      shown = null;
      const next = historyItems()[0];
      if (next) showItem(next); else { resetFrame(); status.textContent = 'Клип удалён.'; }
    }
    refreshHist();
  }
  deps.addDraftHook(refreshHist);
  vhHist.onclick = e => {
    const del = e.target.closest('[data-del]');
    if (del) {
      const it = deps.byId(del.dataset.del) || deps.draftById(del.dataset.del);
      if (it) deleteClipFlow(it, e);
      return;
    }
    const b = e.target.closest('.cw-th'); const it = b && (deps.byId(b.dataset.id) || deps.draftById(b.dataset.id));
    if (!it || run) return;
    showItem(it);
  };

  /* ----- real generation through the local server ----- */
  const STAGE = {
    queued: ['В очереди ComfyUI…', 0.02, 'в очереди'],
    load: ['Загружаем модели в видеопамять… (первый запуск дольше)', 0.05, 'загрузка моделей'],
    encode: ['Кодируем промпт и кадры…', 0.12, 'кодирование'],
    sample: ['Генерация кадров', 0.15, ''],
    upscale: ['Увеличиваем разрешение для второго прохода…', 0.67, 'апскейл'],
    cloud_queued: ['В очереди на RunningHub…', 0.05, 'облако: очередь'],
    cloud_run: ['Генерация в облаке RunningHub… (превью в облаке не передаётся)', 0.4, 'облако'],
    sample2: ['Проход 2 · детализация', 0.70, ''],
    decode: ['Собираем видео и звук…', 0.92, 'сборка видео'],
    save: ['Сохраняем файл…', 0.98, 'сохранение'],
  };
  const goText = () => opts.goLabel ? opts.goLabel() : 'Сгенерировать';
  const setGo = busy => { go.lastChild.textContent = busy ? 'Поставить в очередь ещё одно' : goText(); };
  async function realStart() {
    const meta = opts.meta();
    go.disabled = true; if (!run) status.textContent = 'Отправляем задание в ComfyUI…';
    let res;
    try {
      const fd = opts.real.form();
      if (!fd.has('preview')) fd.append('preview', '1');   // the Library waits for «Добавить в библиотеку»
      res = await apiSend(opts.real.url, { method: 'POST', body: fd, timeout: 180000 });
    } catch (e) { go.disabled = false; if (run) deps.toast('Ошибка: ' + e.message); else status.textContent = 'Ошибка: ' + e.message; return; }
    go.disabled = false;
    if (run) {  // the previous job keeps going in the queue; this panel now follows the new one
      clearInterval(run.tick); deps.offPrompt(run.id);
      if (window.h3State.has(run.id)) window.h3State.update(run.id, { detached: true });
      run = null;
    }
    deps.addJob(res.prompt_id, meta.mode, meta.prompt);
    resetFrame();
    run = { real: true, id: res.prompt_id, t0: performance.now(), lastEv: performance.now(), stage: 'queued' };
    const tgt = opts.target();
    if (tgt) { liveImg.src = tgt.src; liveImg.classList.add('dim'); liveImg.hidden = false; }
    go.hidden = false; setGo(true); stopB.hidden = false; hud.hidden = false; wait.hidden = false; $('.res', frame).hidden = true;
    $('.hud-t', el).textContent = 'Предпросмотр';
    showStage();
    run.tick = setInterval(showStage, 1000);
    deps.previewHandlers.set(el, blob => {
      if (run) run.lastEv = performance.now();
      if (!run || run.stage === 'queued' || run.anim) return;   // the animated preview (KJNodes) wins over the single frame
      URL.revokeObjectURL(liveImg.dataset.url || '');
      liveImg.dataset.url = liveImg.src = URL.createObjectURL(blob);
      liveImg.classList.remove('dim'); liveImg.hidden = false; wait.hidden = true;
    });
    deps.onPrompt(run.id, ev => {
      if (!run || run.id !== ev.prompt_id) return;
      run.lastEv = performance.now();
      if (ev.type === 'preview_anim') {
        run.anim = true; wait.hidden = true; liveImg.classList.remove('dim');
        if (ev.mime.startsWith('video/')) { liveVid.src = `data:${ev.mime};base64,${ev.image}`; liveVid.hidden = false; liveImg.hidden = true; liveVid.play().catch(() => {}); }
        else { liveImg.src = `data:${ev.mime};base64,${ev.image}`; liveImg.hidden = false; liveVid.hidden = true; }
        return;
      }
      if (ev.type === 'stage') {
        const sampling = ev.stage.startsWith('sample');
        if (sampling && run.stage !== ev.stage) { run.sampleT0 = performance.now(); run.step1T = null; }
        run.stage = ev.stage; run.step = ev.step; run.max = ev.max;
        if (ev.scene) { if (run.scene !== ev.scene) { run.sampleT0 = performance.now(); run.step1T = null; } run.scene = ev.scene; run.nscenes = ev.scenes; }
        if (sampling && ev.step) run.stepT = performance.now();
        if (sampling && ev.step === 1) run.step1T = run.stepT;   // the first step includes loading, it is not typical
        showStage();
      } else if (ev.type === 'done') realFinish('done', ev.item);
      else if (ev.type === 'error') realFinish('error', ev.message);
      else if (ev.type === 'interrupted') realFinish('stopped');
    });
  }
  function showStage() {
    if (!run) return;
    const [txt, base, short] = STAGE[run.stage];
    const two = opts.twoPass, sampling = run.stage.startsWith('sample') && run.max;
    let p = base, line = txt, hudTxt = short;
    if (sampling) {
      const frac = (run.step || 0) / run.max;
      p = run.stage === 'sample2' ? 0.70 + 0.20 * frac : 0.15 + (two ? 0.50 : 0.75) * frac;
      const pass = two ? `проход ${run.stage === 'sample2' ? 2 : 1}/2 · ` : '';
      line = `${two && run.stage === 'sample' ? 'Проход 1 · генерация кадров' : txt} · шаг ${run.step || 0} из ${run.max}`;
      hudTxt = `${pass}шаг ${run.step || 0}/${run.max}`;
      if (run.scene) { line = `Сцена ${run.scene} из ${run.nscenes} · ` + line; hudTxt = `сцена ${run.scene}/${run.nscenes} · ${hudTxt}`;
        p = 0.15 + 0.75 * ((run.scene - 1 + frac) / run.nscenes); }
      if (run.step >= 2 && run.step1T) {
        const per = (run.stepT - run.step1T) / (run.step - 1);
        const left = (run.max - run.step) + (run.scene ? (run.nscenes - run.scene) * run.max : 0), extra = run.scene ? 10 * (run.nscenes - run.scene + 1) : 10;
        if (run.scene) {
          line += ` · эта сцена ещё ~${fmtTime((run.max - run.step) * per / 1000 + 10)}`;
          if (run.nscenes > run.scene) line += ` · весь ролик ~${fmtTime(left * per / 1000 + extra)}`;
        } else line += ` · ${two ? 'до конца прохода' : 'осталось'} ~${fmtTime(left * per / 1000 + extra)}`;
      } else if (run.step >= 1) line += ' · время считаем, нужен ещё шаг';
    }
    bar.style.width = (p * 100) + '%';
    const badges = [], single = opts.singleScene && opts.singleScene();   // one scene only: do not count the whole video
    if (run.scene) badges.push({ t: single ? `Сцена ${run.scene}` : `Сцена ${run.scene} из ${run.nscenes}` });
    if (sampling) {
      badges.push({ t: `Шаг ${run.step || 0} из ${run.max}` });
      if (run.step >= 2 && run.step1T) {
        const per = (run.stepT - run.step1T) / (run.step - 1) / 1000, thisLeft = (run.max - run.step) * per + 10;
        badges.push({ t: `Осталось ~${fmtTime(thisLeft)}`, eta: true });
        if (!single && run.scene && run.nscenes > run.scene) badges.push({ t: `Весь ролик ~${fmtTime(thisLeft + (run.nscenes - run.scene) * (run.max * per + 10))}`, eta: true });
      } else badges.push({ t: 'Считаем время…', eta: true });
    }
    badges.push({ t: `Прошло ${fmtTime((performance.now() - run.t0) / 1000)}` });
    health(p, sampling ? (two && run.stage === 'sample' ? 'Проход 1 · генерация кадров' : txt) : txt.replace(/…$/, ''), badges);
    $('.hud-s', el).textContent = hudTxt;
    wait.textContent = liveImg.hidden || liveImg.classList.contains('dim') ? txt : '';
    status.textContent = run.stopping ? 'Останавливаем…' : '';   // the big box above shows the same, no second small line
  }
  function realFinish(kind, data) {
    clearInterval(run.tick); deps.offPrompt(run.id); deps.previewHandlers.delete(el);
    go.hidden = false; setGo(false); stopB.hidden = true; bar.style.width = 0; pbox.hidden = true;
    const took = fmtTime((performance.now() - run.t0) / 1000);
    run = null;
    if (kind === 'done') {
      if (!data) { resetFrame(); status.textContent = 'Готово, но программа не получила файл: проверьте «Очередь»'; return; }
      shown = normalize(data);
      [liveImg, liveVid, wait].forEach(x => x.hidden = true);
      $('.res', frame).hidden = true;
      result.src = shown.url; result.dataset.dragid = shown.id; result.draggable = true; result.hidden = false; playWithSound(result);
      frame.classList.add('done'); $('.hud-t', el).textContent = 'Готово'; $('.hud-s', el).textContent = took;
      resultActions(shown, took, true);
      refreshHist();
      opts.onShow?.(shown);
    } else {
      resetFrame();
      status.textContent = kind === 'error' ? 'Ошибка ComfyUI: ' + data : 'Генерация остановлена. Поправьте промпт или настройки и запустите снова';
    }
  }

  go.onclick = () => {
    const err = opts.validate(); if (err) { status.textContent = err; return; }
    if (deps.online()) {
      if (!opts.real) { status.textContent = 'Этот режим ещё не подключён к ComfyUI — пока работает «Image-to-Video»'; return; }
      return realStart();
    }
    const [w, h] = resFor(st.aspect, st.q);
    live.width = 480; live.height = Math.round(480 * h / w);
    const passes = opts.passes();
    run = { passes, total: passes.reduce((a, b) => a + b, 0), step: 0, last: performance.now(), t0: performance.now(), src: opts.target() || makeScene() };
    go.hidden = true; stopB.hidden = false; live.hidden = false; hud.hidden = false; $('.res', frame).hidden = true;
    frame.classList.remove('done'); $('.hud-t', el).textContent = 'Предпросмотр';
    run.timer = setInterval(() => { run.step++; run.last = performance.now(); if (run.step >= run.total) finish(true); }, STEP_MS);
    const draw = now => {
      if (!run) return;
      const p = Math.min(1, (run.step + Math.min(1, (now - run.last) / STEP_MS)) / run.total);
      renderLive(live.getContext('2d'), live.width, live.height, run.src, p, now);
      bar.style.width = (p * 100) + '%';
      let s = run.step, pass = 0; while (pass < run.passes.length - 1 && s >= run.passes[pass]) s -= run.passes[pass++];
      const passTxt = run.passes.length > 1 ? `проход ${pass + 1}/${run.passes.length} · ` : '';
      $('.hud-s', el).textContent = `${passTxt}шаг ${Math.min(s + 1, run.passes[pass])}/${run.passes[pass]}`;
      const el2 = (now - run.t0) / 1000;
      status.textContent = `Генерация · ${Math.round(p * 100)}%` + (p > 0.05 ? ` · осталось ~${fmtTime(el2 / p * (1 - p))}` : '');
      run.raf = requestAnimationFrame(draw);
    };
    run.raf = requestAnimationFrame(draw);
  };
  stopB.onclick = () => {
    if (!run?.real) return finish(false);
    status.textContent = 'Останавливаем…'; run.stopping = performance.now(); showStage();
    deps.postJSON('/api/interrupt', { prompt_id: run.id }).catch(() => {});
  };

  function finish(ok) {
    clearInterval(run.timer); cancelAnimationFrame(run.raf);
    go.hidden = false; stopB.hidden = true; bar.style.width = 0;
    if (ok) {
      renderLive(live.getContext('2d'), live.width, live.height, run.src, 1, performance.now());
      frame.classList.add('done'); $('.hud-t', el).textContent = 'Готово'; $('.hud-s', el).textContent = '';
      const [w, h] = resFor(st.aspect, st.q);
      opts.onDone({ ...opts.meta(), res: `${w}×${h}`, aspect: st.aspect, q: st.q, len: st.len(), thumb: live.toDataURL('image/jpeg', .85) });
      status.textContent = 'Готово — видео сохранено в библиотеку';
    } else {
      live.hidden = true; hud.hidden = true; $('.res', frame).hidden = false;
      status.textContent = 'Генерация остановлена. Поправьте промпт или настройки и запустите снова';
    }
    run = null;
  }

  queueMicrotask(refreshHist);   // LIB is declared later in the script: the history is filled once everything is ready
  const update = () => {
    if (!run && $('.res', frame).hidden) resetFrame();
    const effQ = st.effQ ? st.effQ() : st.q, [w, h] = resFor(st.aspect, effQ);
    if (!run) go.lastChild.textContent = goText();
    frame.style.aspectRatio = `${w}/${h}`;
    frame.style.width = w >= h ? '100%' : `${Math.round(100 * w / h * 0.95)}%`;
    $('.res', frame).innerHTML = `<b>${w} × ${h}</b>${st.aspect} · ${effQ !== st.q ? 'черновик' : st.q}`;
    $('.s-res', el).textContent = `${w}×${h}`;
    const L = st.len();
    $('.s-len', el).textContent = L ? `${L.toFixed(1).replace('.0', '')} с` : '—';
    $('.s-fr', el).textContent = L ? frames(L) : '—';
    $('.s-ex', el).textContent = opts.extra();
  };
  /* the user opened another section: stop what makes noise; the job itself keeps running in the
     queue and the panel follows it when the section is opened again */
  update.pause = () => {
    if (!run) { try { result.pause(); } catch (e) { /* already gone */ } }
    try { liveVid.pause(); } catch (e) { /* not playing */ }
  };
  /* pagehide: give back the timers, the prompt subscription and the preview's object URL */
  update.dispose = () => {
    if (run) {
      clearInterval(run.tick); clearInterval(run.timer); cancelAnimationFrame(run.raf);
      deps.offPrompt(run.id); deps.previewHandlers.delete(el); run = null;
    }
    try { result.pause(); } catch (e) { /* already gone */ }
    try { liveVid.pause(); } catch (e) { /* already gone */ }
    result.removeAttribute("src");
    URL.revokeObjectURL(liveImg.dataset.url || "");
    delete liveImg.dataset.url;
    deps.removeDraftHook(refreshHist);
  };
  /* show a clip by its id: a stage remembers the clip it was saved with and brings it back.
     If that clip is gone, the newest clip of the project is shown instead — an empty player with no
     explanation looks like a broken stage. */
  update.show = id => {
    const it = id && (deps.byId(id) || deps.draftById(id));
    if (it && !it.missing && showItem(it)) return true;
    const first = historyItems()[0];
    return first ? showItem(first) : false;
  };
  update.clear = () => {
    shown = null; resetFrame(); status.textContent = ''; refreshHist();
  };
  return update;
}

/* ---------- dropzones ---------- */
const REF_HINT = {
  max: 'Модель внимательно разглядывает ваши изображения в полном размере, поэтому лицо и одежда похожи сильнее всего. Но работает в 2–3 раза дольше. Берите для готового видео.',
  match: 'Модель смотрит на изображения в уменьшенном виде: быстрее, но сходство с лицом чуть хуже. Берите для проб, пока подбираете промпт.',
};
/* optional LoRAs: the same on/off switch and strength slider in every mode */
const LORA_DEFS = {
  realism: { name: 'Реализм людей', hint: 'Для живых людей: естественная кожа и лицо. Если лицо выглядит «пластиково» — включите.', min: 0, max: 1, step: .05, v: .5, on: false },
  motion: { name: 'Исправление движения', hint: 'Если движение рвётся, дёргается или «плывёт» — включите.', min: 0, max: 1, step: .05, v: .5, on: false },
  dmad: { name: 'DMAD', hint: 'Усиливает динамику и чёткость движения. Как в исходном экшн-workflow: 0.8.', min: 0, max: 1.2, step: .05, v: .8, on: true },
  speed: { name: 'Speed Slider', hint: 'Скорость движения: 3–4 — быстро и резко; ближе к 0 — спокойнее; со знаком минус — замедление.', min: -8, max: 8, step: .5, v: 4, on: true },
};
function mountLoras(box, st, keys) {
  st.loras = st.loras || {};
  keys.forEach(k => { st.loras[k] = st.loras[k] || { on: LORA_DEFS[k].on, v: LORA_DEFS[k].v }; });
  box.innerHTML = keys.length ? `<div class="row"><div class="row-l">Дополнительные LoRA <span class="hint" style="font-family:inherit">включите нужные и задайте силу влияния</span></div>`
    + keys.map(k => { const d = LORA_DEFS[k]; return `<div class="lora" data-k="${k}"><div class="lora-h"><button class="chip lora-t" type="button">${d.name}</button><span class="lora-v"></span></div>`
      + `<input type="range" class="lora-r" min="${d.min}" max="${d.max}" step="${d.step}"><div class="hint">${d.hint}</div></div>`; }).join('') + '</div>' : '';
  const paint = () => $$('.lora', box).forEach(el => {
    const s = st.loras[el.dataset.k], r = $('.lora-r', el);
    $('.lora-t', el).classList.toggle('on', s.on);
    r.value = s.v; r.disabled = !s.on; r.style.opacity = s.on ? 1 : .35;
    r.style.setProperty('--p', ((s.v - r.min) / (r.max - r.min) * 100) + '%');
    $('.lora-v', el).textContent = s.on ? `сила ${(+s.v).toFixed(2).replace(/\.?0+$/, '')}` : 'выключена';
  });
  box.onclick = e => { const el = e.target.closest('.lora'); if (!el || !e.target.closest('.lora-t')) return; const s = st.loras[el.dataset.k]; s.on = !s.on; paint(); };
  box.oninput = e => { const el = e.target.closest('.lora'); if (!el) return; st.loras[el.dataset.k].v = +e.target.value; paint(); };
  st._paintLoras = paint;
  paint();
}
function loraForm(st, fd) { Object.entries(st.loras || {}).forEach(([k, s]) => fd.append('lora_' + k, s.on ? s.v : 'off')); }

// reference slots take the shape of the picture: 16:9 wide, 9:16 tall, square square
function fitSlot(r, img) {
  const go = () => {
    const ratio = Math.min(16 / 9, Math.max(9 / 16, img.naturalWidth / img.naturalHeight));
    r.style.aspectRatio = ratio.toFixed(4); r.classList.toggle('wide', ratio > 1.2);
  };
  img.complete && img.naturalWidth ? go() : img.addEventListener('load', go, { once: true });
}
function resetSlot(r) { r.style.aspectRatio = ''; r.classList.remove('wide'); }

// pick the format closest to a picture's proportions once it has loaded
function fitAspect(st, img, upd, force) {
  if ((st.userAspect && !force) || (st.locked && st.locked())) return;
  const apply = () => {
    const r = img.naturalWidth / img.naturalHeight; if (!r) return;
    const best = ASPECTS.reduce((a, b) => Math.abs(Math.log(b.w / b.h / r)) < Math.abs(Math.log(a.w / a.h / r)) ? b : a);
    if (best.id === st.aspect) return;
    st.aspect = best.id; upd();
    deps.toast(`Формат подстроен под изображение: ${best.name}${best.name !== best.id ? ' ' + best.id : ''}`);
  };
  img.complete && img.naturalWidth ? apply() : img.addEventListener('load', apply, { once: true });
}

function wireDrop(zone, onFile) {
  const input = $('input', zone);
  const set = file => {
    if (!file || !file.type.startsWith('image/')) return;
    zone._file = file;
    let img = $('img', zone); if (!img) { img = document.createElement('img'); zone.prepend(img); }
    URL.revokeObjectURL(zone._url || '');            // a replaced picture is not a live URL any more
    zone._url = URL.createObjectURL(file);
    img.src = zone._url;
    zone.classList.add('filled'); $('.ph', zone).hidden = true;
    const tag = $('.tag', zone); if (tag) tag.hidden = false;
    onFile(true, img);
  };
  input.onchange = () => set(input.files[0]);
  // a file restored from the last session is already in the input before this mode is built
  if (input.files && input.files[0]) set(input.files[0]);
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('over'));
  zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('over'); set(e.dataTransfer.files[0]); });
  const x = $('.x', zone);
  if (x) x.onclick = e => { e.preventDefault(); e.stopPropagation(); clear(); };
  function clear() {
    zone._file = null;
    URL.revokeObjectURL(zone._url || '');
    zone._url = null;
    $('img', zone)?.remove(); zone.classList.remove('filled'); $('.ph', zone).hidden = false;
    const tag = $('.tag', zone); if (tag) tag.hidden = true; input.value = ''; onFile(false);
  }
  return { clear };
}

/* picture slots: drag one picture onto another slot to swap them; clearAll clicks every slot's ✕ */
function slotSet(z, f, src) {
  z._file = f || null;
  let im = $('img', z);
  if (src) { if (!im) { im = document.createElement('img'); z.prepend(im); } im.src = src; }
  else if (im) im.remove();
  z.classList.toggle('filled', !!f);
  const ph = $('.plus, .ph', z); if (ph) ph.hidden = !!f;
  const tg = $('.tag', z); if (tg) tg.hidden = !f;
}
function wireSlotSwap(zones, onSwap) {
  let from = null;
  zones.forEach(z => {
    z.addEventListener('dragstart', e => {
      if (!z._file) { e.preventDefault(); return; }
      from = z;
      try { e.dataTransfer.setData('text/plain', 'slot'); e.dataTransfer.effectAllowed = 'move'; } catch {}
    });
    z.addEventListener('dragend', () => { from = null; });
    z.addEventListener('dragover', e => { if (from && from !== z) { e.preventDefault(); try { e.dataTransfer.dropEffect = 'move'; } catch {} } });
    z.addEventListener('drop', e => {
      e.preventDefault(); e.stopPropagation();
      if (!from || from === z) return;
      const a = from, b = z;
      const fa = a._file, sa = $('img', a)?.src, fb = b._file, sb = $('img', b)?.src;
      slotSet(a, fb, sb); slotSet(b, fa, sa);
      from = null;
      if (onSwap) onSwap();
    });
  });
}
const clearSlots = zones => zones.forEach(z => { const x = $('.x', z); if (x) x.click(); });


  window.h3Components = {
    configure,
    ASPECTS, QUALITY_ALL, QUALITY, qLabel, r32, resFor, frames,
    paramsCard, SZ_ROW, szWire, sizeForm, newSeed, seedForm, useSeed,
    drawCover, makeScene, renderLive, fmtTime, wavSlice, playWithSound, soundChip, hideSoundChip, previewPanel,
    REF_HINT, LORA_DEFS, mountLoras, loraForm,
    fitSlot, resetSlot, fitAspect, wireDrop, slotSet, wireSlotSwap, clearSlots,
  };
})();
