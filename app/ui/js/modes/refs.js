/* References, actor, swap, action and the long-video studio: one factory, five sections.
 *
 * These five share `makeRefMode` (reference slots, prompt, parameters, preview, scene list). The
 * module runs after the page and builds the five sections at load: the remembered reference files
 * and the details window's forms need their slots and fields while the page is still being set up,
 * so a lazy build would lose them. The state `S` and the DOM are therefore as old as the page;
 * `mount` re-checks the model state on the first visit, `leave` pauses playback and saves the long
 * project, `dispose` gives back the preview subscriptions, blob URLs and a pending project save.
 *
 * Loaded after the page (it uses the page's Library, drafts, dialogs and memory); registers all
 * five modes itself. `window.RM` stays the object the page's job details, agent and bookmarks read.
 */

(function () {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const moduleScope = window.h3Lifecycle.scope('refs-family');

const ICON_IMG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>';
const ICON_VID = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="13" height="14" rx="2"/><path d="m16 10 5-3v10l-5-3"/></svg>';
const ICON_AUD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>';
let REF_FUSED_CHECK = null;   // the references section re-checks its optional model after a download

/* The DMAD LoRA of the action sections is a 3.9 GB file from Civitai, NOT the «DMAD 4 шагов» switch in «LoRA»:
   the switch turns on a file that is already in the loras folder (a different training for the studio draft), while
   the action scene needs its own file. The block below says that in plain words and gives the three steps. */
function dmadHelp(box, seen) {
  if (!box) return;
  const list = (seen || []).length
    ? `<br><span class="hint">ComfyUI видит файлы с «DMAD» в названии: ${esc(seen.slice(0, 5).join(', '))} — но это не тот файл.</span>`
    : '';
  box.innerHTML = `<div class="note warn" style="margin:8px 0"><span><b>Нужен отдельный файл DMAD — 3,9 ГБ.</b> Это не тот
    «DMAD 4 шагов», который включает переключатель в разделе «LoRA»: для экшна нужна своя LoRA, и без файла сцены не
    запустятся.${list}<br>
    <b>1.</b> Нажмите «Открыть страницу», войдите на Civitai (регистрация бесплатная) и скачайте файл
    <b>minimax_h3_dmad_full_dareties_v4_step600_comfy.safetensors</b> (3,9 ГБ; файлы на 500 МБ — другие LoRA).<br>
    <b>2.</b> Когда он скачается, нажмите «Я скачал — поставить файл»: программа найдёт его в «Загрузках» и положит куда надо.
    <span class="dmad-msg hint" style="display:block;margin-top:6px"></span>
    <span style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
      <button class="btn mini pri dm-open" type="button">1. Открыть страницу</button>
      <button class="btn mini dm-ok" type="button">2. Я скачал — поставить файл</button>
      <button class="btn mini dm-dir" type="button">Открыть папку loras</button></span></span></div>`;
  const msg = $('.dmad-msg', box);
  const call = url => apiPost(url, {});
  $('.dm-open', box).onclick = () => call('/api/dmad/open')
    .then(() => { msg.textContent = 'Страница открылась в браузере. Скачайте файл на 3,9 ГБ, потом нажмите «Я скачал».'; })
    .catch(e => { msg.textContent = e.message || String(e); });
  $('.dm-dir', box).onclick = () => call('/api/dmad/folder')
    .then(j => { msg.textContent = 'Папка открыта: ' + j.path; })
    .catch(e => { msg.textContent = e.message || String(e); });
  $('.dm-ok', box).onclick = async () => {
    msg.textContent = 'Ищу файл и переношу его (3,9 ГБ, это может занять минуту)…';
    try {
      const j = await call('/api/dmad/install');
      msg.textContent = 'Готово: файл на месте (' + j.name + ').';
      toast('DMAD установлен');
      box._after && box._after();
    } catch (e) { msg.textContent = e.message || String(e); }
  };
}

function makeRefMode(cfg) {
  const root = $('#view-' + cfg.mode);
  const ACT = !!cfg.action;   // «Экшн-видеостудия»: the same scenes as the studio, sampled by the action model
  const S = { aspect: '16:9', q: 'HD+', dur: cfg.dur, steps: 6, refSize: 'max', accel: 'old', unet: 'fast', len: () => S.dur };
  if (ACT) { S.actionModel = 'sing'; S.actionPika = true; }
  // this section writes its own markup, while the stage bar («＋ Сохранить этап» / «Этапы ▾») is the
  // page's and was put there before this module was loaded: keep it, or the section loses its stages
  const stBar = $('.st-bar', root);
  root.innerHTML = `
    <div class="head"><div><h1>${cfg.title}</h1><p>${cfg.sub}</p></div><div class="mode-pill"><i></i><span class="rm-pill">${cfg.pill}</span></div></div>
    <div class="grid"><div class="col">
      <div class="card">
        <div class="card-h"><h3><span class="n">1</span>Референсы</h3><span class="counter rm-count"></span> <button class="btn mini rm-clr" type="button" title="Убрать все изображения из ячеек">Очистить всё</button></span></div>
        <div class="refs rm-imgs"></div>
        <div class="rm-media">
          ${cfg.videos ? `<div><div class="sub-l"><span>Референс-видео</span></div><div class="slots rm-vids"></div><small class="media-hint">движение, камера, хореография · 2–15 с</small></div>` : ''}
          <div><div class="sub-l"><span>Референс-аудио</span></div><div class="slots rm-auds"></div><small class="media-hint">голос, ритм, атмосфера · до 15 с</small></div>
        </div>
        <div class="note" style="margin-top:12px"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 8h0M11 12h1v5h1"/></svg>
          <span>${cfg.refHint}</span></div>
      </div>
      <div class="card">
        <div class="card-h"><h3><span class="n">2</span>Промпт</h3><span class="hint">можно написать в разделе «Агент»</span></div>
        <textarea class="rm-prompt" placeholder="${cfg.placeholder}"></textarea>
        <div class="ta-foot"><div class="tokens rm-tokens"></div><span class="hint">нажмите, чтобы вставить</span></div>
      </div>
      <div class="card rm-params"></div>
    </div><div class="preview rm-preview"></div></div>`;
  if (stBar) root.prepend(stBar);   // the section's stages stay above its own markup
  const imgsEl = $('.rm-imgs', root), vidsEl = $('.rm-vids', root), audsEl = $('.rm-auds', root);

  // picture slots (same behaviour as Lipsync)
  for (let i = 0; i < cfg.maxImages; i++) {
    const r = document.createElement('label'); r.className = 'ref';
    r.innerHTML = `<input type="file" accept="image/*"><div class="plus">${ICON_IMG}${i === 0 ? 'Главный' : 'Референс'}</div><span class="tag" hidden></span><button class="x" type="button">✕</button>`;
    imgsEl.append(r);
    const input = $('input', r);
    const set = f => {
      if (!f || !f.type.startsWith('image/')) return;
      $('img', r)?.remove(); r._file = f;
      const img = document.createElement('img'); img.src = URL.createObjectURL(f); r.prepend(img);
      fitSlot(r, img);
      if (!$$('.ref.filled', imgsEl).length) fitAspect(S, img, () => upd());
      r.classList.add('filled'); $('.plus', r).hidden = true; $('.tag', r).hidden = false; changed();
    };
    input.onchange = () => set(input.files[0]);
    r.addEventListener('dragover', e => e.preventDefault());
    r.addEventListener('drop', e => { e.preventDefault(); set(e.dataTransfer.files[0]); });
    $('.x', r).onclick = e => { e.preventDefault(); e.stopPropagation(); $('img', r)?.remove(); input.value = ''; r._file = null; resetSlot(r); r.classList.remove('filled'); $('.plus', r).hidden = false; $('.tag', r).hidden = true; changed(); };
  }
  wireSlotSwap($$('.ref', imgsEl), changed);
  $('.rm-clr', root).onclick = () => clearSlots($$('.ref', imgsEl));
  // video / audio slots
  const mkSlots = (el, n, accept, icon, label) => {
    for (let i = 0; i < n; i++) {
      const s = document.createElement('label'); s.className = 'slot';
      s.innerHTML = `<input type="file" accept="${accept}" hidden>${icon}<span>${label} ${i + 1}</span><small></small><button class="x" type="button">✕</button>`;
      el.append(s);
      const input = $('input', s);
      input.onchange = () => {
        const f = input.files[0]; if (!f) return;
        s._file = f; s.classList.add('filled'); $('span', s).textContent = accept.startsWith('video') ? '✓ Видео загружено' : f.name;
        const media = document.createElement(accept.startsWith('video') ? 'video' : 'audio');
        media.preload = 'auto'; media.muted = true; media.src = URL.createObjectURL(f);
        if (accept.startsWith('video')) {   // a frame of the video in the slot, like a picture: it is clear at a glance that the video is there
          s.classList.add('vthumb'); s.style.backgroundImage = '';
          media.addEventListener('loadeddata', () => { try { media.currentTime = Math.min(0.3, (media.duration || 1) / 2); } catch {} });
          media.addEventListener('seeked', () => {
            const c = document.createElement('canvas'), w = 240; c.width = w; c.height = Math.round(w * (media.videoHeight || 3) / (media.videoWidth || 4));
            try { c.getContext('2d').drawImage(media, 0, 0, c.width, c.height); s.style.backgroundImage = `linear-gradient(rgba(0,0,0,.35),rgba(0,0,0,.6)),url(${c.toDataURL('image/jpeg', 0.8)})`; } catch {}
          }, { once: true });
        }
        const isVid = accept.startsWith('video'), mh = (el._hint || el.parentElement.querySelector('.media-hint')), mh0 = mh && (mh._t0 = mh._t0 || mh.textContent);
        const say = (txt, bad) => { if (!mh) return; mh.textContent = txt; mh.style.color = bad ? 'var(--pink)' : 'var(--green, #4ade80)'; };
        if (mh) say(`Загружается: ${f.name}…`, false);
        media.onloadedmetadata = () => {
          s._dur = media.duration; s.classList.remove('bad'); $('small', s).textContent = media.duration.toFixed(1) + ' с';
          if (isVid && media.videoWidth) {   // like a picture slot: a vertical video gets a vertical slot, a horizontal one a wide slot
            const ratio = Math.min(16 / 9, Math.max(9 / 16, media.videoWidth / media.videoHeight));
            s.style.aspectRatio = ratio.toFixed(4); s.classList.toggle('wide', ratio > 1.2);
          }
          say(`✓ Загружено: ${f.name} · ${media.duration.toFixed(1)} с${isVid && media.videoWidth ? ` · ${media.videoWidth}×${media.videoHeight}` : ''} · ${(f.size / 1048576).toFixed(1)} МБ`, false); changed();
        };
        media.onerror = () => {
          s._dur = 0; s.classList.add('bad'); $('small', s).textContent = 'не открылось'; if (isVid) $('span', s).textContent = '✗ Видео не открылось';
          say(`Не получилось открыть «${f.name}» в программе (возможно, необычный формат или кодек). Попробуйте сохранить файл как обычный MP4 (H.264) и загрузить снова.`, true);
          toast('Файл не открылся: ' + f.name);
        };
        changed();
      };
      $('.x', s).onclick = e => { e.preventDefault(); e.stopPropagation(); const mh = (el._hint || el.parentElement.querySelector('.media-hint')); if (mh && mh._t0) { mh.textContent = mh._t0; mh.style.color = ''; } s.classList.remove('bad', 'vthumb', 'wide'); s.style.backgroundImage = ''; s.style.aspectRatio = ''; s._file = null; s._dur = 0; input.value = ''; s.classList.remove('filled'); $('span', s).textContent = `${label} ${i + 1}`; $('small', s).textContent = ''; changed(); };
    }
  };
  if (vidsEl) mkSlots(vidsEl, cfg.swap ? 1 : 3, 'video/*', ICON_VID, 'Видео');
  if (cfg.swap) audsEl.parentElement.hidden = true;
  async function detectCuts(file) {   // looks at the video 10 times a second and finds hard cuts (sudden change of the picture)
    const v = document.createElement('video'); v.muted = true; v.preload = 'auto'; v.src = URL.createObjectURL(file);
    await new Promise(r => { v.onloadedmetadata = r; v.onerror = r; });
    const dur = v.duration || 0; if (!(dur > 2)) return [];
    const c = document.createElement('canvas'); c.width = 32; c.height = 24; const g = c.getContext('2d', { willReadFrequently: true });
    const diffs = []; let prev = null;
    for (let t = 0; t < Math.min(dur, 15); t += 0.1) {
      v.currentTime = t; await new Promise(r => { v.onseeked = r; setTimeout(r, 600); });
      g.drawImage(v, 0, 0, 32, 24); const d = g.getImageData(0, 0, 32, 24).data, cur = new Uint8Array(32 * 24);
      for (let i = 0; i < cur.length; i++) cur[i] = (d[i * 4] + d[i * 4 + 1] + d[i * 4 + 2]) / 3;
      if (prev) { let sum = 0; for (let i = 0; i < cur.length; i++) sum += Math.abs(cur[i] - prev[i]); diffs.push([t, sum / cur.length]); }
      prev = cur;
    }
    URL.revokeObjectURL(v.src);
    const med = diffs.map(x => x[1]).sort((a, b) => a - b)[Math.floor(diffs.length / 2)] || 0;
    const cuts = [];
    diffs.forEach(([t, d]) => { if (d > Math.max(28, med * 5) && (!cuts.length || t - cuts[cuts.length - 1] > 1.5)) cuts.push(+t.toFixed(2)); });
    return cuts;
  }
  mkSlots(audsEl, cfg.scenes ? 1 : 3, 'audio/*', ICON_AUD, cfg.scenes ? 'Музыка' : 'Аудио');
  if (cfg.swap) $('.rm-vids input', root).addEventListener('change', async e => {
    const f = e.target.files[0]; if (!f) return;
    const box = $('.sw-cuts', root), hint = $('.sw-cuts-hint', root); box.value = '';
    try { const c = await detectCuts(f); box.value = c.join(', '); } catch { /* one piece then */ }
  });
  let onScenes = () => {};
  const promptEl = () => $('.rm-prompt', root) || (cfg.scenes ? ($$('.sc textarea', root).find(t => t.value.trim()) || $('.sc textarea', root)) : (root._ta || $('.sc textarea', root)));   // video studio: the first scene that has a text (the focused field may be a stale one)
  if (cfg.swap) {   // beginner form: who to replace instead of a prompt; the program writes the prompt itself
    const card = $('.rm-prompt', root).closest('.card');
    card.innerHTML = `<div class="card-h"><h3><span class="n">2</span>Промпт</h3><span class="hint">можно написать в разделе «Агент»</span></div>
      <textarea class="rm-prompt" placeholder="Напишите обычным промптом, кого на кого заменить. Например: Replace the man in the white shirt in <Video 1> with the character from <Picture 1>. Keep the background, camera and movements of the video."></textarea>
      <div class="ta-foot"><div class="tokens rm-tokens"></div><button class="btn sw-example" type="button" style="padding:3px 10px;font-size:12px">Вставить пример</button></div>
      <div class="hint" style="margin-top:6px">Изображения называются <b>Picture 1</b>, <b>Picture 2</b> (по порядку слотов), видео — <b>Video 1</b>. Если оставить пустым, программа сама заменит главного человека в видео на героя с первого изображения.</div>
      <div hidden>
        <input class="sw-cuts"><input class="rm-who" data-i="0"><input class="rm-who" data-i="1">
      </div>`;
    $('.sw-example', root).onclick = () => { const t = $('.rm-prompt', root); if (!t.value.trim()) { t.value = 'Replace the person in <Video 1> with the character from <Picture 1>. Retain the identity, attire and style of the character from <Picture 1>. Preserve the camera angle, background, lighting and the movements of the original video. Do not display the reference images or their backgrounds.'; t.dispatchEvent(new Event('input', { bubbles: true })); } };
    $('.rm-media .sub-l span', root).textContent = 'Видео, в котором меняем';
    $('.rm-media .media-hint', root).textContent = 'до 15 секунд · лучше один непрерывный план';
    $$('.rm-imgs .plus', root).forEach((pl, i) => { pl.lastChild.textContent = i ? 'Персонаж 2' : 'Персонаж 1'; });
    // the video sits in the same row as the pictures (third slot); its status line goes under the row
    vidsEl._hint = $('.rm-media .media-hint', root); vidsEl.style.display = 'contents'; imgsEl.append(vidsEl);
    imgsEl.closest('.card').insertBefore(vidsEl._hint, $('.note', imgsEl.closest('.card'))); vidsEl._hint.style.margin = '8px 0 0';
    $('.rm-media', root).hidden = true;
    // «how it works» sits right after the references, before the prompt
    imgsEl.closest('.card').insertAdjacentHTML('beforeend', `<details class="agvar" style="margin:10px 0 0"><summary>Как работает и советы</summary>
      <div class="hint" style="font-size:12.5px;line-height:1.5">Берём ваше видео и заменяем в нём выбранного человека на героя с изображения: поза, движения, камера и фон остаются прежними. Лучше всего на коротком непрерывном плане; лицо и мелкие детали иногда «плывут», тогда нажмите «Другой вариант».</div>
      <div class="hint" style="font-size:12.5px;line-height:1.5;margin-top:6px"><b>Советы.</b> Изображение с одним героем и простым фоном. Качество задаёт размер результата, а не размер вашего видео. Для пробы выберите самый быстрый размер, понравилось — «В хорошем качестве» под результатом. Ориентир: около минуты расчёта на секунду видео на RTX 4060 Ti 16 ГБ при 864×480.</div>
      <div class="hint" style="font-size:11.5px;margin-top:6px">Основа: рабочий процесс Smart Bobo и LoRA akatz-ai.</div></details>`);
  }
  // which scenes start as a NEW SHOT: the ones marked by hand, and automatically every 4th continuation in a row
  // (a long chain of continuations wears the picture out; a new shot is built from the references again, so the quality stays the same)
  const MAX_CHAIN = 3;
  const freshAt = () => { let run = 0; return $$('.sc', root).map((c, i) => { if (!i) { run = 0; return false; } const hand = !!$('.sc-fresh', c)?.checked; if (hand || run >= MAX_CHAIN) { run = 0; return true; } run++; return false; }); };
  const sceneList = () => { const fr = freshAt(); return $$('.sc', root).map((c, i) => ({ prompt: $('textarea', c).value.trim(), sec: +$('input[type=range]', c).value, fresh: fr[i] })); };
  if (cfg.scenes) {
    const card = $('.rm-prompt', root).closest('.card');
    card.innerHTML = `<div class="card-h"><h3><span class="n">4</span>Сцены</h3><span class="counter lm-total"></span></div>
      <div class="tl-player"><video class="tl-vid" controls playsinline loop hidden></video><div class="hint tl-empty">Здесь покажется готовая сцена. Выберите фрагмент на ленте ниже и нажмите «Сделать эту сцену».</div></div>
      <div style="display:flex;gap:8px;margin-bottom:6px;flex-wrap:wrap"><button class="btn pri tl-all" type="button" title="Готовые сцены подряд, как один ролик: первая, вторая, третья...">▶ Смотреть готовые сцены подряд</button><button class="btn pri tl-join" type="button" title="Все готовые сцены по порядку отправляются в «Монтаж»: там остаётся нажать «Склеить», и весь ролик одним файлом появится справа — его можно сохранить или добавить в Библиотеку">⇩ Собрать весь ролик и сохранить</button><span class="hint tl-note" style="align-self:center"></span></div>
      <div class="tl" title="Фрагменты ролика по порядку. Нажмите на фрагмент, чтобы настроить его. Потяните его правый край, чтобы изменить длину"></div>
      <div class="hint" style="margin:-6px 0 10px;font-size:12px">Нажмите на фрагмент, чтобы открыть его. Потяните правый край фрагмента, чтобы сделать его короче или длиннее.</div>
      <div class="lm-scenes"></div>
      <div class="seg lm-q" style="grid-template-columns:1fr 1fr;margin:10px 0 6px"><button type="button" data-mq="draft">Черновик<small>4 шага, быстро: посмотреть, что получится</small></button><button type="button" data-mq="final">Хорошее качество<small>8 шагов: итоговые сцены</small></button></div>
      <div class="hint lm-q-hint" style="margin-bottom:10px"></div>
      <div class="seg lm-acc" style="grid-template-columns:1fr 1fr;margin:0 0 6px" title="Чем считаются сцены в хорошем качестве"><button type="button" data-x="pika" class="on">С ускорением Pika<small>8 шагов, быстро (как раньше)</small></button><button type="button" data-x="old">Без Pika<small>12 шагов, дольше, обычный путь модели</small></button></div>
      <div class="hint" style="margin-bottom:10px;font-size:12px">Если со сценами что-то не так (зависает, плохой звук или картинка), попробуйте «Без Pika». Выбор действует на «Хорошее качество»; в «Черновике» ускорение другое.</div>
      <div class="seg lm-mode" style="grid-template-columns:1fr 1fr;margin-bottom:6px"><button type="button" data-m="parts">По частям<small>сцена за сценой</small></button><button type="button" data-m="all">Весь ролик<small>одним файлом</small></button></div>
      <div class="hint lm-mode-hint" style="margin-bottom:10px"></div>
      <button class="btn pri lm-chain" type="button" style="margin-bottom:12px;width:100%;padding:12px" title="Сцены считаются одна за другой сами: следующая запускается, когда предыдущая готова. Можно оставить компьютер на ночь: каждая сцена готова отдельным клипом-черновиком.">▶ Сделать все сцены подряд (само, одну за другой)</button>
      <button class="btn pri lm-final" type="button" hidden style="margin-bottom:12px;width:100%;padding:12px" title="Все сцены считаются заново с теми же текстами и тем же seed, но на 8 шагах: детали станут лучше. Один в один они не будут.">✨ Всё готово? Сделать всё в хорошем качестве</button>
      <div class="note lm-src" hidden style="margin-bottom:10px"><span><b>Продолжаем готовое видео:</b> <span class="lm-srcname"></span>. Первая сцена продолжит его конец, как будто это была прошлая сцена.</span> <button class="btn lm-srcx" type="button" style="padding:2px 9px;margin-left:8px">Убрать</button></div>
      <button class="btn" type="button" id="lmAdd" style="margin-top:10px">+ Добавить сцену</button>
      <label class="btn" style="margin-top:10px;cursor:pointer" title="Видео, сделанное в другом месте: возьмём его последние кадры, и первая сцена продолжит их">Продолжить своё видео с компьютера…<input type="file" accept="video/*" class="lm-srcfile" hidden></label>
      <div class="ta-foot"><div class="tokens rm-tokens"></div><span class="hint">нажмите, чтобы вставить в сцену</span></div>
      <div class="note" style="margin-top:12px"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 8h0M11 12h1v5h1"/></svg><span>Изображения героя необязательны: без них ролик строится только по тексту (например, про Древний Рим). Сцены идут одна за другой, каждая продолжает прошлую: герой, место и звук переходят дальше. Но при каждом продолжении изображение немного портится, поэтому при смене плана или места ставьте у сцены галочку «Новый план»: она строится заново по вашим изображениям, и качество остаётся как в первой сцене. В каждой сцене опишите героя, что он делает и как движется камера. Длину сцены задаёт ползунок. Текст сцены можно составить в разделе «Агент».</span></div>`;
    const c1 = $('.rm-imgs', root).closest('.card');
    if (ACT) {   // the action studio: one quality, no song, the sound comes from the model
      const note = card.querySelector('.note:not(.lm-src) span');   // the long how-it-works note, not the «continuing a video» one
      if (note) note.innerHTML = 'Изображения героя необязательны: без них ролик строится только по тексту. Сцены идут одна за другой, '
        + 'каждая продолжает прошлую: герой, место и звук переходят дальше. При смене плана или места ставьте у сцены «Сменить план здесь»: '
        + 'она строится заново по вашим изображениям, и качество остаётся как в первой. Длину сцены задаёт ползунок: для экшна лучше 3–5 секунд — '
        + 'считается быстрее, движение чище. Своего аудио здесь нет: звук и музыку опишите в тексте сцены.';
    }
    $('h3', c1).innerHTML = '<span class="n">1</span>Референсы';
    $('.rm-count', root).hidden = true;
    $$('.rm-imgs .plus', root).forEach((pl, i) => { pl.lastChild.textContent = 'Референс'; });
    $('.rm-media .sub-l span', root).textContent = 'Песня или фрагмент';
    $('.rm-media .media-hint', root).textContent = 'любой длины: каждая сцена берёт свой кусок по порядку';
    // the pictures of this section go to the agent in one press (no screenshots), the agent writes the scenes for them
    if (!ACT) {   // the action studio has no agent target of its own yet: its scenes are written by hand
      const toAg = document.createElement('button'); toAg.type = 'button'; toAg.className = 'btn'; toAg.style.marginTop = '10px';
      toAg.textContent = 'Написать сцены в «Агенте» по этим изображениям'; toAg.title = 'Откроет «Агента» с этими референсами в чате: опишите историю, он разобьёт её на сцены, а кнопка вставит их сюда';
      c1.append(toAg);
      toAg.onclick = async () => {
        const files = $$('label.ref.filled', imgsEl).map(r => r._file).filter(Boolean);
        try {
          for (const f of files) {
            if (AG.att.some(a => a.file && a.file.name === f.name && a.file.size === f.size)) continue;
            await agAttachFile(f);
          }
          $('#agTarget [data-t="long"]').click(); goTo('agent');
          toast(files.length ? `В чат агента добавлено изображений: ${files.length}. Опишите историю по сценам.` : 'Откройте «Агента» и опишите историю по сценам.');
        } catch { toast('Не удалось передать изображения агенту'); }
      };
    }
    const box = $('.lm-scenes', root);
    box.addEventListener('click', async e => {   // «Добавить в библиотеку» in the scene line
      const b = e.target.closest('.sc-addlib'); if (!b) return;
      e.preventDefault(); e.stopPropagation();
      b.disabled = true;
      const kept = await keepDraftItem(draftById(b.dataset.id));
      if (kept) refreshStates(); else b.disabled = false;
    });
    const snap = v => { v = Math.max(5, Math.round(v * 24)); return v + (5 - v % 17 + 17) % 17; };
    const accs = () => {  // frames of the finished video after each scene (same arithmetic as the server)
      const out = []; let acc = 0;
      const fr = freshAt(); $$('.sc input[type=range]', root).forEach((r, k) => { const seg = snap(+r.value); if (!k) acc = seg; else if (fr[k]) acc += seg; else { const t = snap((seg + snap(1)) / 24), ov = snap((t - seg) / 24); acc += t - ov; } out.push(acc); });
      return out;
    };
    const total = () => { const a = accs(); return a.length ? a[a.length - 1] / 24 : 0; };
    const mmss = t => `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, '0')}`;
    const recount = () => {
      const all = $$('.sc', root), a = accs(), own = root._snd === 'own';
      const st = Math.max(0, parseFloat($('.lm-start', root)?.value) || 0);
      all.forEach((c, i) => {
        $('.sc-fl', c).hidden = i === 0; $('.sc-auto', c).hidden = !(i && !$('.sc-fresh', c).checked && freshAt()[i]); $('b', c).textContent = `Сцена ${i + 1}`; $('.x', c).hidden = all.length < 2; $('output', c).textContent = $('input[type=range]', c).value + ' с';
        const from = i ? a[i - 1] / 24 : 0, to = a[i] / 24;
        $('.sc-win', c).textContent = own ? `кусок аудио ${mmss(st + from)}–${mmss(st + to)}` : '';
      });
      const t = total(); $('.lm-total', root).textContent = `≈ ${Math.round(t)} с в итоге`; S.dur = Math.round(t);
      const dur = root._adur;
      const w = $('.lm-warn', root); if (w) { w.hidden = !(own && dur && st + t > dur + 0.5); w.textContent = `Аудио короче, чем нужно: ролик на ${Math.round(t)} с, а от выбранного места в треке осталось ${Math.max(0, Math.round(dur - st))} с. Сократите сцены или начните трек раньше.`; }
      root._renderTL?.(); changed(); onScenes(); refreshStates(); if (root._save && !root._restoring) root._save();
    };
    root._recount = recount;
    /* A film belongs to a project: the studio keeps the project the film was started in, so the work
       is never written into another project after the header was switched. A new film of a new
       project gets a fresh run id; the old one stays saved under its own project. */
    const newRun = () => { root._run = Math.random().toString(36).slice(2, 12); root._project = window.h3Projects?.active?.() || ''; return root._run; };
    root._newRun = newRun;
    root._mode = 'parts'; newRun();
    const sig = c => JSON.stringify([+$('input[type=range]', c).value]);
    const secOf = sg => { try { const a = JSON.parse(sg); return a.length === 1 ? a[0] : a[1]; } catch { return null; } };   // old saved projects had [text, seconds, file]
    const sigOk = c => !!c._done && String(secOf(c._done.sig)) === String(+$('input[type=range]', c).value);
    function readyUpTo() {   // how many scenes from the start are done and unchanged
      let n = 0; for (const c of $$('.sc', root)) { if (sigOk(c)) n++; else break; } return n;
    }
    function refreshStates() {
      const parts = root._mode === 'parts', ready = readyUpTo();
      // «Добавить в библиотеку» right in the scene line: a finished scene is a draft until this press
      // (the button is inside .sc-state, which is redrawn on every refresh)
      $$('.sc', root).forEach((c, i) => {
        $('.sc-act', c).hidden = !parts;
        const done = sigOk(c);
        const stale = c._done && !done;
        const inLib = !!(c._done && byId(c._done.id));   // the clip was added to the Library (a draft stays out of it)
        const note = c._done && c._done.text != null && c._done.text.trim() !== $('textarea', c).value.trim() ? ' · текст изменён после генерации, клип прежний' : '';
        $('.sc-state', c).innerHTML = done
          ? `✓ готова, ${inLib ? 'клип в Библиотеке' : 'черновик — в Библиотеку не добавлен'}${note}`
            + (inLib ? '' : ` <button class="btn sc-addlib" type="button" data-id="${esc(c._done.id)}" style="padding:2px 9px;font-size:11.5px">Добавить в библиотеку</button>`)
          : stale ? 'длина изменена: переделайте (следующие тоже)' : i > 0 && !root._canGo(i) ? `сцена ${i} ещё не готова: эта будет сделана как новый план` : '';
        $('.sc-state', c).style.color = done ? 'var(--ok, #7bd88f)' : stale ? 'var(--pink)' : '';
        const b = $('.sc-do', c); b.disabled = false; b.style.opacity = ''; $('.sc-undo', c).hidden = !(root._undo && root._undo.length); b.textContent = done ? 'Переделать' : 'Сделать эту сцену';
        const rg = $('.sc-redogo', c); if (rg) rg.hidden = !(done && parts && i < $$('.sc', root).length - 1);
      });
      const r = $('.lm-reset', root); if (r) r.hidden = !parts || !ready;
      const ch = $('.lm-chain', root); if (ch) { ch.hidden = !parts; ch.textContent = root._q === 'draft' ? '▶ Сделать все черновики подряд (само, одну за другой)' : '▶ Сделать все сцены подряд в хорошем качестве'; }
      const lf = $('.lm-final', root); if (lf) lf.hidden = !(parts && root._q === 'draft' && ready >= $$('.sc', root).length);
      root._renderTL?.();
      onScenes();
      if (root._save && !root._restoring) root._save();
    }
    root._ready = readyUpTo; root._sig = sig;
    DRAFT_HOOKS.push(() => { if ($$('.sc', root).length) refreshStates(); });   // a scene added to the Library from the result panel updates its line
    root._canGo = i => i === 0 || freshAt()[i] || sigOk($$('.sc', root)[i - 1]);   // a scene may start when it needs no previous one, or the previous one is ready
    S.onSizeChange = kind => {   // another size (or format): the ready scenes were made in the old one, they stay on the ribbon as an old version
      const modes = kind === 'aspect' ? ['draft', 'final'] : [root._q]; let any = false;
      $$('.sc', root).forEach(c => modes.forEach(m => { if (c._dn[m]) { c._old = { ...(c._old || {}), [m]: c._dn[m] }; c._dn[m] = null; any = true; } }));
      root._paintQ?.(); refreshStates(); root._renderTL?.();
      if (any) toast('Размер изменён. Готовые сцены остались на ленте как «старая версия»: сделайте их заново в новом размере.');
    };
    root._chainPart = null;   // scene number started by "make all scenes in a row": only its result starts the next one
    const chainNext = () => {
      const all = $$('.sc', root), k = readyUpTo();
      if (root._chainEnd != null && k > root._chainEnd) { root._chainPart = null; root._chainEnd = null; return; }
      if (k >= all.length) { root._chainPart = null; toast('Все сцены готовы: клипы — черновики. Их можно собрать в «Монтаже» или добавить в Библиотеку кнопкой под сценой.'); return; }
      if (!all[k].querySelector('textarea').value.trim()) { root._chainPart = null; toast(`Сцена ${k + 1} пустая: опишите её, и нажмите «Сделать все сцены подряд» снова.`); return; }
      root._chainPart = k; root._target = k; $('.go', $('.rm-preview', root)).click();
    };
    root._chainNext = chainNext;
    $('.lm-chain', root).onclick = () => {
      if (root._mode !== 'parts') return;
      root._chainEnd = null;
      if (readyUpTo() >= $$('.sc', root).length) { toast('Все сцены уже сделаны. Чтобы переделать одну, нажмите «Переделать» в её карточке.'); return; }
      chainNext();
    };
    root._next = () => { const n = $$('.sc', root).length; return Math.min(n - 1, root._target != null ? root._target : readyUpTo()); };
    S.len = () => { if (root._mode !== 'parts') return total(); const r = $$('.sc input[type=range]', root)[root._next()]; return r ? +r.value : total(); };
    root._src = null;
    const lastFrameFile = async file => {   // the last picture of a video file from the computer
      const v = document.createElement('video'); v.muted = true; v.preload = 'auto'; v.src = URL.createObjectURL(file);
      await new Promise((res, rej) => { v.onloadedmetadata = res; v.onerror = () => rej(new Error('Не удалось открыть видео')); });
      v.currentTime = Math.max(0, (v.duration || 1) - 0.12);
      await new Promise(res => { v.onseeked = res; setTimeout(res, 3000); });
      const c = document.createElement('canvas'); c.width = v.videoWidth; c.height = v.videoHeight; c.getContext('2d').drawImage(v, 0, 0);
      const blob = await new Promise(r => c.toBlob(r, 'image/png')); URL.revokeObjectURL(v.src);
      return new File([blob], 'last_frame.png', { type: 'image/png' });
    };
    root._setSrc = (src, fresh = true) => {   // src: { id } of a library clip or { file } from the computer; null removes it
      root._src = src || null;
      $('.lm-src', root).hidden = !src; $('.lm-srcname', root).textContent = src ? src.name : '';
      if (src && fresh) { newRun(); $$('.sc', root).forEach(c => { c._dn = { draft: null, final: null }; }); }
      if (src) setMode('parts');
      $('.lm-mode [data-m="all"]', root).disabled = !!src;
      refreshStates();
    };
    $('.lm-srcx', root).onclick = () => root._setSrc(null);
    $('.lm-srcfile', root).onchange = async e => {
      const f = e.target.files[0]; e.target.value = ''; if (!f) return;
      try {
        const frame = await lastFrameFile(f);
        root._setSrc({ file: f, name: f.name });
        $$('.ref:not(.filled) input', imgsEl)[0] && await putInto($$('.ref:not(.filled) input', imgsEl)[0], frame);
        toast('Видео загружено: последний кадр встал первым референсом, добавьте остальные изображения и опишите сцену.');
      } catch (err) { toast(err.message); }
    };
    root._setScenes = list => {   // from the agent: replace all scenes
      if (!list.length) return;
      $$('.sc', root).forEach(c => c.remove());
      list.slice(0, 12).forEach(x => addScene(x.prompt, Math.min(15, Math.max(3, Math.round(x.sec) || 10)), !!x.fresh));
      root._sel = 0;
      newRun(); recount(); refreshStates();
    };
    root._partDone = item => {
      const lg = item && item.long; if (!lg || lg.run !== root._run) return;
      if (root._doneIds && root._doneIds.has(item.id)) return;   // the same result can arrive twice (panel and queue)
      (root._doneIds = root._doneIds || new Set()).add(item.id);
      if (lg.all) {   // the whole video at once: every scene is done, the last one can be continued
        $$('.sc', root).forEach(c => { c._dn.final = { sig: root._sig(c), url: item.url, id: item.id, text: $('textarea', c).value, preview: !!item.preview }; });
        refreshStates(); toast('Ролик готов' + (item.preview ? ' — черновик, в Библиотеку не добавлен' : '') + '. Чтобы продлить его, добавьте сцену и нажмите «Сделать эту сцену» (режим «По частям»).'); return;
      }
      const c = $$('.sc', root)[lg.part]; if (!c) return;
      if (lg.test) { c._tests.push({ res: item.res, url: item.url, id: item.id }); c._paintTests(); toast(`Проба сцены ${lg.part + 1} в размере ${item.res} готова: кнопка под сценой`); return; }
      c._dn[lg.draft ? 'draft' : 'final'] = { sig: (root._sent && root._sent[lg.part]) || root._sig(c), url: item.url, id: item.id, text: root._sentText && root._sentText[lg.part] != null ? root._sentText[lg.part] : $('textarea', c).value, preview: !!item.preview };
      { const fr = freshAt(), all = $$('.sc', root), mk = lg.draft ? 'draft' : 'final'; for (let j = lg.part + 1; j < all.length && !fr[j]; j++) { const x = all[j]; if (x._dn[mk]) x._old = { ...(x._old || {}), [mk]: x._dn[mk] }; x._dn[mk] = null; } c._old = { ...(c._old || {}), [mk]: null }; }   // the scenes that continued the old one, up to the next new shot
      refreshStates();
      if (root._chainPart === lg.part) { toast(`Сцена ${lg.part + 1} готова. Запускаю следующую…`); setTimeout(root._chainNext, 800); return; }
      toast(`Сцена ${lg.part + 1} готова` + (lg.part + 1 < lg.n ? '. Можно делать следующую.' : '. Все сцены готовы: склейте клипы в «Монтаже».'));
    };
    const MODE_HINT = { parts: 'Каждая сцена считается отдельно: готовая сцена — черновик, в Библиотеку её добавляют кнопкой под сценой. Можно посмотреть первую, потом сделать следующую, а неудачную переделать, не трогая остальные. Клипы встают встык без повторов: склейте их в любом видеоредакторе.', all: 'Все сцены считаются подряд и склеиваются в один файл. Удобно, когда уверены в тексте, но если что-то не получится, ждать придётся заново целиком.' };
    const setMode = v => { root._mode = v; $$('.lm-mode button', root).forEach(b => b.classList.toggle('on', b.dataset.m === v)); $('.lm-mode-hint', root).textContent = MODE_HINT[v]; refreshStates(); };
    $('.lm-mode', root).onclick = e => { const b = e.target.closest('button'); if (b) setMode(b.dataset.m); };
    queueMicrotask(() => setMode('parts'));

    /* --- the project survives a restart: scenes, pictures of scenes, sound choice, which scenes are done --- */
    const PKEY = 'h3-long-projects', LASTKEY = ACT ? 'h3-long-last-actionsc' : 'h3-long-last', fsig = {};
    const loadAll = () => { try { return JSON.parse(localStorage.getItem(PKEY) || '{}'); } catch { return {}; } };
    const putFile = (key, f) => { const sg = f ? `${f.name}|${f.size}|${f.lastModified}` : ''; if (fsig[key] === sg) return; fsig[key] = sg; if (f) memFile('put', key, f); else memFile('delete', key); };
    let saveT = null;
    function saveNow() {
      try {
        const run = root._run, slotA = $('.rm-auds .slot', root);
        const proj = { run, project: root._project || window.h3Projects?.active?.() || '', t: Date.now(), mode: root._mode, qm: root._q, qBy: { ...(root._qBy || {}), [root._q]: S.q }, snd: root._snd, start: $('.lm-start', root).value, vocals: $('.lm-vocals', root).checked,
          studio: cfg.mode,   // the film knows its studio: a film of the action studio is never opened by the Видеостудия and back
          aspect: S.aspect, q: S.q, cw: S.cw || 0, ch: S.ch || 0, src: root._src ? { id: root._src.id || '', name: root._src.name } : null,
          scenes: $$('.sc', root).map(c => ({ prompt: $('textarea', c).value, sec: +$('input[type=range]', c).value, fresh: !!$('.sc-fresh', c)?.checked, nimg: c._files.length, var: c._var || 0,
            done: c._dn.final ? { sig: c._dn.final.sig, url: c._dn.final.url, id: c._dn.final.id, text: c._dn.final.text } : null,
            draftDone: c._dn.draft ? { sig: c._dn.draft.sig, url: c._dn.draft.url, id: c._dn.draft.id, text: c._dn.draft.text } : null })) };
        const all = loadAll();
        // An untouched studio is not a film: it starts with two empty scenes, and saving them would
        // create a record (and a folder) that is later offered as a work to keep. A film that was
        // saved once keeps being saved even if the scenes are cleared — the record is already there.
        const hasWork = proj.scenes.some(x => String(x.prompt || '').trim() || x.done || x.draftDone)
          || $$('.ref.filled', imgsEl).length > 0 || !!(slotA && slotA._file);
        if (!hasWork && !all[run]) return;
        all[run] = proj;
        Object.keys(all).sort((a, b) => all[b].t - all[a].t).slice(8).forEach(k => delete all[k]);   // the 8 latest projects
        localStorage.setItem(PKEY, JSON.stringify(all)); localStorage.setItem(LASTKEY, run);
        apiPost('/api/long/project', { run, project: proj }).catch(() => {});
        $$('.ref', imgsEl).forEach((r, i) => putFile(`long/${run}/img${i}`, r._file));
        putFile(`long/${run}/aud0`, slotA ? slotA._file : null);
        putFile(`long/${run}/src`, root._src && root._src.file ? root._src.file : null);
        $$('.sc', root).forEach((c, i) => { for (let j = 0; j < 5; j++) putFile(`long/${run}/simg${i}_${j}`, c._files[j] || null); });
      } catch {}
    }
    root._save = () => { clearTimeout(saveT); saveT = setTimeout(saveNow, 700); };
    root._flush = () => { clearTimeout(saveT); saveNow(); };
    root._cancelSave = () => clearTimeout(saveT);
    root._snap = () => { saveNow(); return JSON.parse(JSON.stringify(loadAll()[root._run] || null)); };   // a stage of the work: the whole project as it is now
    root._openStage = async (proj, sid) => {
      let r = {}; try { r = await apiPost('/api/long/stage', { run: proj.run, id: sid, action: 'restore' }); } catch {}
      const all = loadAll(); all[proj.run] = { ...proj, t: Date.now() }; localStorage.setItem(PKEY, JSON.stringify(all));
      root._diskRefs = !!r.refs; try { const ok = await root._openRun(proj.run); return { ok, files: !!r.ok }; } finally { root._diskRefs = false; }
    };
    const putInto = async (input, f) => { if (!input || !(f instanceof File)) return; const dt = new DataTransfer(); dt.items.add(f); input.files = dt.files; input.dispatchEvent(new Event('change')); };
    root._openRun = async run => {   // load a saved project (from the library: continue this video)
      let p = loadAll()[run], onDisk = null;
      try { onDisk = await apiGet('/api/long/project?run=' + run); } catch {}
      if (!p && onDisk && onDisk.project) p = onDisk.project;
      if (!p) return false;
      if (String(p.studio || 'long') !== cfg.mode) return false;   // a film of another studio (its scenes are counted another way)
      // the project the film was made in: an old record without one joins the project that is open
      root._project = String(p.project || (onDisk && onDisk.project && onDisk.project.project) || '')
                      || (window.h3Projects?.active?.() || '');
      const getRef = async key => {   // a picture of the project: the browser's memory, else the copy kept by the program (after a stage is opened: the copy first)
        if (!root._diskRefs) { const f = await memFile('get', `long/${run}/${key}`); if (f instanceof File) return f; }
        const nm = ((onDisk && onDisk.refs) || []).find(x => x.startsWith(key + '.')); if (!nm) return null;
        try { const r = await apiGet(`/api/long/ref/${run}/${nm}`, { raw: 'response' }); return new File([await r.blob()], nm, { type: r.headers.get('content-type') || 'image/png' }); } catch { return null; }
      };
      root._run = run; root._restoring = true;
      const okQ = v => QUALITY_ALL.some(x => x.id === v) ? v : '720p';   // a broken saved value (a mode name instead of a size) must not get through
      Object.assign(S, { aspect: ASPECTS.some(a => a.id === p.aspect) ? p.aspect : S.aspect, q: okQ(p.q || S.q), cw: p.cw || 0, ch: p.ch || 0 });
      $$('.ref .x', imgsEl).forEach(x => x.click());
      for (let i = 0; i < 5; i++) await putInto($$('.ref input', imgsEl)[i], await getRef('img' + i));
      const slotA = $('.rm-auds .slot', root); $('.x', slotA)?.click();
      const aud = await getRef('aud0'); if (aud instanceof File) await putInto($('input', slotA), aud);
      setSnd(p.snd || 'none'); $('.lm-start', root).value = p.start || '0'; $('.lm-vocals', root).checked = !!p.vocals;
      $$('.sc', root).forEach(c => c.remove());
      for (const x of p.scenes) addScene(x.prompt, x.sec, !!x.fresh);
      $$('.sc', root).forEach((c, i) => { c._var = (p.scenes[i] || {}).var || 0; });
      root._sel = 0;
      const cards = $$('.sc', root);
      for (const [i, x] of p.scenes.entries()) {
        const n = x.nimg || (x.img ? 1 : 0);   // an old project kept one picture per scene under another name
        for (let j = 0; j < n; j++) { const f = await getRef(x.nimg ? `simg${i}_${j}` : 'simg' + i); if (f instanceof File) cards[i]._files.push(f); }
        cards[i]._paint();
      }
      if (p.src) { const sf = p.src.id ? null : await memFile('get', `long/${run}/src`); root._setSrc(p.src.id ? { id: p.src.id, name: p.src.name } : sf instanceof File ? { file: sf, name: p.src.name } : null, false); }
      else root._setSrc(null, false);
      const okDone = d => d && byId(d.id) ? { sig: d.sig, url: d.url, id: d.id, text: d.text } : null;
      p.scenes.forEach((x, i) => { cards[i]._dn = { final: okDone(x.done), draft: okDone(x.draftDone) }; });
      // the continuation point of the last finished scene must still be on the disk (for the finished row and for the draft row)
      let st = {}; try { st = await apiGet('/api/long/state?run=' + run); } catch {}
      for (const mk of ['final', 'draft']) {
        root._q = mk; const ready = readyUpTo(), parts = (mk === 'draft' ? st.draft_parts : st.parts) || [];
        if (ready && !parts.includes(ready - 1)) { cards.forEach(c => { c._dn[mk] = null; }); toast('Точка продолжения не найдена на диске: сцены нужно сделать заново.'); }
      }
      { const tgt = p.qm === 'draft' && root._dmad !== false ? 'draft' : 'final'; root._qBy = { draft: S.q, final: S.q, ...(p.qBy || {}) }; root._qBy.draft = okQ(root._qBy.draft); root._qBy.final = okQ(root._qBy.final); S.q = root._qBy[tgt]; setQ(tgt, true); }
      setMode(p.mode || 'parts'); root._restoring = false; recount(); refreshStates(); RM.long?.upd?.();
      return true;
    };
    /* The film of the project that is open, else a new one. A film never follows the studio into
       another project: the header's switch brings this project's own film back (or starts one). */
    const projectFilm = async project => {
      if (!project) return '';
      try { const r = await apiGet('/api/long/project?studio=' + cfg.mode + '&project=' + encodeURIComponent(project)); return (r && r.run) || ''; } catch { return ''; }
    };
    /* the header switched the project: what is on the screen goes to the project it was made in, then
       the studio opens the film of the new project — or starts a new one with empty scenes */
    root._projectChanged = async () => {
      const project = window.h3Projects?.active?.() || '';
      if (project === root._project) return;
      // a film is saved into the project it was made in — but a project that has just been deleted
      // takes its film with it, and a save would only recreate the folder
      if (PROJECTS_VIEW.list.some(p => p.id === root._project)) root._flush();
      else { root._cancelSave?.(); root._project = ''; }
      const run = await projectFilm(project);
      if (run) { await root._openRun(run); return; }
      $$('.sc', root).forEach(c => c.remove());
      root._sel = 0; root._setSrc(null, false); newRun();
      addScene('', 10); addScene('', 10); recount(); refreshStates();
      const name = (PROJECTS_VIEW.list.find(p => p.id === project) || {}).name;
      toast(`Проект${name ? ': «' + name + '»' : ''}. Начинаем новый фильм`);
    };
    root._restoring = true;   // nothing is saved until the last project has been put back
    setTimeout(async () => {
      try {
        // the film of the project that is open first; «h3-long-last» and ?last=1 are the fallback for
        // the films saved before a film knew its project (a film of another project is never opened)
        let last = await projectFilm(window.h3Projects?.active?.() || '');
        if (!last) {
          last = localStorage.getItem(LASTKEY) || '';
          if (!last) { const r = await apiGet('/api/long/project?last=1&studio=' + cfg.mode); last = r.run || ''; }
          if (last) {
            const r = await apiGet('/api/long/project?run=' + encodeURIComponent(last)).catch(() => null);
            const own = (r && r.project && r.project.project) || '';
            if (own && own !== (window.h3Projects?.active?.() || '')) last = '';
          }
        }
        if (last) await root._openRun(last);
      } catch {}
      root._restoring = false; root._save();
    }, 2500);

    const rst = document.createElement('button'); rst.className = 'btn lm-reset'; rst.type = 'button'; rst.hidden = true; rst.textContent = 'Начать заново';
    rst.title = 'Забыть сделанные сцены. Нужно, если меняете изображения героя, размер или звук: следующие сцены не должны продолжать старые.';
    rst.onclick = () => { newRun(); $$('.sc', root).forEach(c => { c._dn = { draft: null, final: null }; }); refreshStates(); toast('Начинаем сначала'); };
    $('#lmAdd', root).after(rst);
    const addScene = (txt = '', sec = 10, fresh = false) => {
      const c = document.createElement('div'); c.className = 'sc'; c._dn = { draft: null, final: null };
      Object.defineProperty(c, '_done', { get: () => c._dn[root._q || 'final'], set: v => { c._dn[root._q || 'final'] = v; }, configurable: true });
      c.style.cssText = 'margin:0 0 10px;padding:12px;border:1px solid var(--line);border-radius:12px';
      c.innerHTML = `<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px"><b></b><input type="range" min="3" max="15" step="1" value="${sec}" style="display:none"><output class="hint" style="min-width:34px"></output><button class="btn sc-m" type="button" title="Короче на секунду" style="padding:2px 9px">−</button><button class="btn sc-p" type="button" title="Длиннее на секунду" style="padding:2px 9px">+</button><span class="hint sc-win" style="flex:1"></span><button class="x btn" type="button" title="Убрать сцену" style="padding:2px 9px">✕</button></div><textarea placeholder="Что происходит в этой сцене…" style="min-height:90px"></textarea>
<details class="sc-tdet" style="margin-top:8px"><summary class="hint" style="cursor:pointer;font-size:12px">Проба размера для этой сцены</summary><div class="sc-tst" style="margin-top:6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="hint" style="font-size:12px">Попробовать только эту сцену в другом размере (остальные не меняются):</span><select class="sc-tq">${QUALITY.map(q => `<option value="${q.id}">${q.res}</option>`).join('')}</select><button class="btn sc-tbtn" type="button" title="Сцена считается отдельно, в выбранном размере, на 8 шагов. Результат появится ниже (черновик), остальные сцены не затрагиваются.">Сделать пробу</button></div><div class="sc-tests" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px"></div></details>
        <div class="sc-refs" style="margin-top:8px"><div class="hint sc-rhint" style="font-size:12px"></div><div class="sc-thumbs" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:6px"><label class="btn sc-img" style="padding:4px 10px;font-size:12.5px;cursor:pointer" title="Необязательно. Изображения героя, места или реквизита только для этой сцены. Если загрузить, общие референсы сверху для неё не используются. В тексте сцены они называются &lt;Picture 1&gt;, &lt;Picture 2&gt; по порядку.">+ Свои референсы этой сцены<input type="file" accept="image/*" multiple hidden></label></div></div>`;
      const fin = $('.sc-img input', c);
      c._tests = [];
      c._paintTests = () => { $('.sc-tests', c).innerHTML = c._tests.map((t, j) => `<button class="btn sc-tt" type="button" data-j="${j}" style="padding:3px 9px;font-size:12px">▶ ${esc(t.res || '')}</button>`).join(''); };
      $('.sc-tq', c).value = S.q; $('.sc-tq', c).onchange = e => { e.target._touched = true; };
      $('.sc-tests', c).onclick = e => { const b = e.target.closest('.sc-tt'); const t = b && c._tests[+b.dataset.j]; if (!t) return; root._sel = $$('.sc', root).indexOf(c); const v = $('.tl-vid', root); v.src = t.url; v.hidden = false; $('.tl-empty', root).hidden = true; v.play().catch(() => {}); };
      $('.sc-tbtn', c).onclick = () => { root._test = { q: $('.sc-tq', c).value }; root._chainPart = null; root._target = $$('.sc', root).indexOf(c); $('.go', $('.rm-preview', root)).click(); };
      c._files = [];
      const paintRefs = c._paint = () => {
        const bx = $('.sc-thumbs', c); bx.querySelectorAll('.sc-th').forEach(x => x.remove());
        c._files.forEach((f, j) => {
          const sp = document.createElement('span'); sp.className = 'sc-th'; sp.style.cssText = 'position:relative;display:inline-block';
          const im = document.createElement('img'); im.src = URL.createObjectURL(f); im.title = `<Picture ${j + 1}>`; im.style.cssText = 'width:64px;height:64px;object-fit:cover;border-radius:8px;border:1px solid var(--line-2);display:block';
          const x = document.createElement('button'); x.type = 'button'; x.textContent = '✕'; x.className = 'btn'; x.style.cssText = 'position:absolute;top:-7px;right:-7px;padding:0 6px;font-size:11px;line-height:18px';
          x.onclick = () => { c._files.splice(j, 1); paintRefs(); refreshStates(); };
          sp.append(im, x); bx.insertBefore(sp, bx.querySelector('.sc-img'));
        });
        $('.sc-rhint', c).textContent = c._files.length ? `Своих референсов в этой сцене: ${c._files.length} (в тексте <Picture 1>…<Picture ${c._files.length}> по порядку). Общие референсы сверху здесь не используются.` : 'Своих референсов нет: берутся общие, которые загружены сверху.';
      };
      fin.onchange = () => { for (const f of fin.files) if (c._files.length < 5) c._files.push(f); fin.value = ''; paintRefs(); refreshStates(); };
      paintRefs();
      $('.sc-m', c).onclick = () => { const r = $('input[type=range]', c); r.value = Math.max(3, +r.value - 1); r.dispatchEvent(new Event('input')); };
      $('.sc-p', c).onclick = () => { const r = $('input[type=range]', c); r.value = Math.min(15, +r.value + 1); r.dispatchEvent(new Event('input')); };
      $('textarea', c).value = txt; box.append(c); attachRu($('textarea', c));
      c.insertAdjacentHTML('beforeend', `<label class="sc-fl" style="display:flex;gap:6px;align-items:center;margin-top:8px;font-size:12.5px" title="Как смена плана в кино: сцена не продолжает прошлую, а строится заново по вашим изображениям. Качество в ней как в первой сцене. Программа делает так сама после трёх продолжений подряд."><input type="checkbox" class="sc-fresh"> Сменить план здесь (кино-склейка)</label><div class="hint sc-auto" hidden style="font-size:12px;margin-top:6px">Программа сама сделает здесь смену плана: цепочка продолжений стала длинной, а так качество не падает.</div><div class="sc-act" style="display:flex;gap:10px;align-items:center;margin-top:8px"><button class="btn pri sc-do" type="button" style="padding:11px 20px;font-size:15px">Сделать эту сцену</button><button class="btn sc-clear" type="button" title="Стереть текст, изображения и результат только этой сцены">Очистить сцену</button><button class="btn sc-undo" type="button" hidden title="Вернуть то, что было стёрто или удалено (то же, что Ctrl+Z)">↶ Отменить действие</button><button class="btn pri sc-redogo" type="button" hidden title="Эта сцена считается заново, а потом сами пересчитаются все следующие, уже продолжая новую версию">Переделать и продолжить дальше</button><span class="hint sc-state"></span></div>`);
      $('.sc-fresh', c).checked = !!fresh;
      $('.sc-fresh', c).onchange = () => { c._done = null; recount(); };
      $('.sc-redogo', c).onclick = () => { const i = $$('.sc', root).indexOf(c); c._var = (c._var || 0) + 1; root._chainEnd = null; root._chainPart = i; root._target = i; $('.go', $('.rm-preview', root)).click(); };
      $('.sc-do', c).onclick = () => {
        const i = $$('.sc', root).indexOf(c), ready = readyUpTo();
        const active = window.h3State.active().some(j => j.mode === 'long');
        if (!root._canGo(i)) {   // the previous scene is not ready
          if (active && root._pending && i === root._pending.k + 1) { root._chainPart = root._pending.k; root._chainEnd = i; toast(`Сцена ${i + 1} запустится сама, когда будет готова сцена ${i}.`); return; }
          $('.sc-fresh', c).checked = true; recount();   // it does not wait: it is made as a new shot (not continuing the previous scene)
          toast(`Сцена ${i + 1} сделается как новый план: сцена ${i} ещё не готова, продолжать нечего.`);
        }
        if (sigOk(c)) c._var = (c._var || 0) + 1;   // «Переделать»: another variant of this scene, not the same clip again
        root._chainPart = null; root._target = i; $('.go', $('.rm-preview', root)).click(); };
      $('.sc-undo', c).onclick = () => root._doUndo?.();
      $('.sc-clear', c).onclick = () => {
        root._pushUndo?.();
        $('textarea', c).value = ''; c._files = []; c._paint(); c._dn = { draft: null, final: null }; c._old = null; c._tests = []; c._paintTests(); c._var = 0;
        const sf = $('.sc-fresh', c); if (sf) sf.checked = false;
        refreshStates(); recount(); toast('Сцена очищена');
      };
      $('textarea', c).addEventListener('input', () => refreshStates());
      $('textarea', c).addEventListener('focus', e => { root._ta = e.target; });
      $('input[type=range]', c).oninput = recount; $('.x', c).onclick = () => {
        root._pushUndo?.();
        // deleting the FIRST scene of a song-driven video: the next scene becomes the first, so its piece of the track must stay the
        // same, and the start of the track moves on by the length of the deleted scene (same frame arithmetic as the pieces themselves)
        const gone = root._snd === 'own' && $$('.sc', root)[0] === c ? (accs()[0] || 0) / 24 : 0;
        c.remove();
        if (gone) { const f = $('.lm-start', root); f.value = String(Math.round(((parseFloat(f.value) || 0) + gone) * 100) / 100); toast(`Начало трека сдвинуто на ${gone.toFixed(2)} с: следующие сцены берут тот же кусок аудио`); }
        recount();
      };
      fin.addEventListener('change', () => refreshStates());
      recount();
    };
    $('#lmAdd', root).onclick = () => { if ($$('.sc', root).length >= 12) { toast('Не больше 12 сцен'); return; } root._sel = $$('.sc', root).length; addScene('', 10); };
    queueMicrotask(() => { addScene('', 10); addScene('', 10); });  // after the rest of the set-up is done
    root._addScene = addScene;
    /* --- video studio: a ribbon of fragments, one fragment is open for settings, a player, draft / finished quality --- */
    root._sel = 0; root._q = 'final'; root._dmad = null;
    const tl = $('.tl', root), vid = $('.tl-vid', root), TL_PPS = 26;   // pixels per second on the ribbon: the length of a fragment is its width
    function renderTL() {
      const all = $$('.sc', root), fr = freshAt(), ready = readyUpTo();
      root._sel = Math.max(0, Math.min(all.length - 1, root._sel || 0));
      all.forEach((c, j) => c.classList.toggle('sel', j === root._sel));
      tl.innerHTML = all.map((c, i) => {
        const sec = +$('input[type=range]', c).value, done = sigOk(c), draft = root._q === 'draft', old = !done && !!(c._old || {})[root._q];
        return `<div class="tl-b${i === root._sel ? ' sel' : ''}${done ? (draft ? ' draft' : ' done') : old ? ' stale' : ''}${fr[i] ? ' cut' : ''}" data-i="${i}" style="flex:0 0 ${sec * TL_PPS}px" title="${fr[i] ? 'Новый план (смена кадра). ' : ''}Потяните правый край, чтобы изменить длину"><div><b>${i + 1}</b> · ${sec} с</div><div class="hint" style="font-size:11px;white-space:nowrap;overflow:hidden">${done ? (draft ? 'черновик ✓' : 'готово ✓') : old ? 'старая версия' : ''}${fr[i] ? ' ✂ новый план' : ''}</div><i class="tl-h"></i></div>`;
      }).join('') + '<div class="tl-b tl-add" data-add="1" style="flex:0 0 56px;align-items:center;justify-content:center" title="Добавить фрагмент">+</div>';
      const db = $('.lm-q button[data-mq="draft"]', root);
      if (db) {   // a dead switch must look dead and say why (it is dead when the 4-step draft LoRA is not in the loras folder)
        db.disabled = root._dmad === false;
        db.title = root._dmad === false ? 'Черновик недоступен: в папке loras нет файла minimax_h3_DMAD_4step_full_lora. Скачайте его в разделе «Модели» — кнопка включится сама.' : '4 шага вместо 8: быстро посмотреть, что получится';
      }
    }
    root._renderTL = renderTL;
    function showClip() {
      const c = $$('.sc', root)[root._sel], d = c && (c._dn[root._q] || (c._old || {})[root._q] || c._dn[root._q === 'draft' ? 'final' : 'draft']);
      if (d && d.url) { if (!vid.src.endsWith(d.url)) vid.src = d.url; vid.hidden = false; $('.tl-empty', root).hidden = true; }
      else { vid.pause(); vid.removeAttribute('src'); vid.hidden = true; $('.tl-empty', root).hidden = false; }
    }
    root._showClip = showClip;
    tl.addEventListener('pointerdown', e => {   // drag the right edge of a fragment: shorter / longer
      const h = e.target.closest('.tl-h'); if (!h) return; e.preventDefault();
      const i = +h.closest('.tl-b').dataset.i, sl = $('input[type=range]', $$('.sc', root)[i]);
      root._drag = { i, x0: e.clientX, sec0: +sl.value, sl }; tl.setPointerCapture(e.pointerId);
    });
    tl.addEventListener('pointermove', e => {
      const d = root._drag; if (!d) return;
      const sec = Math.max(3, Math.min(15, Math.round(d.sec0 + (e.clientX - d.x0) / TL_PPS)));
      if (+d.sl.value !== sec) { d.sl.value = sec; d.sl.dispatchEvent(new Event('input')); }
    });
    tl.addEventListener('pointerup', () => { root._drag = null; });
    tl.addEventListener('pointercancel', () => { root._drag = null; });
    tl.onclick = e => {
      if (e.target.closest('.tl-h')) return;
      const b = e.target.closest('.tl-b'); if (!b) return;
      if (b.dataset.add) { $('#lmAdd', root).click(); return; }
      root._sel = +b.dataset.i; renderTL(); showClip();
    };
    $('.tl-join', root).onclick = () => {   // the finished scenes, in order, go to the Montage page: one click there joins them into a single file
      const q = root._q, cards = $$('.sc', root);
      const ids = cards.map(c => (c._dn[q] || {}).id).filter(id => id && byId(id) && !byId(id).missing);
      if (ids.length < 2) { toast(ids.length ? 'Готова только одна сцена: сохранить её можно из Библиотеки. Для ролика нужно хотя бы две готовые сцены.' : 'Пока нет готовых сцен'); return; }
      if (ids.length < cards.length) toast(`Готово ${ids.length} из ${cards.length} сцен: в Монтаж отправлены только готовые`);
      goTo('montage');                       // the section is built on the first visit
      window.montageLoad(ids);               // then it takes the scenes
    };
    $('.tl-all', root).onclick = () => {   // the finished fragments one after another
      const urls = $$('.sc', root).map(c => (c._dn[root._q] || {}).url).filter(Boolean);
      if (!urls.length) { toast('Пока нет готовых фрагментов'); return; }
      let n = 0; vid.hidden = false; $('.tl-empty', root).hidden = true;
      const next = () => { if (n >= urls.length) { vid.onended = null; vid.loop = true; return; } vid.loop = false; vid.src = urls[n++]; vid.play().catch(() => {}); };
      vid.onended = next; next();
    };
    const sz = q => resFor(S.aspect, q).join('×');
    const szOf = v => sz(v === root._q ? S.q : (root._qBy || {})[v] || S.q);
    const Q_HINT = v => v === 'draft'
      ? `Черновик: ${szOf('draft')}, 4 шага, быстро. Понравился, его можно просто увеличить или пересчитать кнопкой «Сделать всё в хорошем качестве».`
      : `Хорошее качество: ${szOf('final')}, 8 шагов. Размер выбирается ниже, в «Формат» и «Качество».`;
    root._paintQ = () => {
      $('.lm-q button[data-mq="draft"] small', root).textContent = `${szOf('draft')} · 4 шага, быстро`;
      $('.lm-q button[data-mq="final"] small', root).textContent = `${szOf('final')} · 8 шагов, итог`;
      const qn = $('.lm-qnote', root); if (qn) qn.textContent = root._q === 'draft' ? 'Размер ниже выбирается для ЧЕРНОВИКА (режим переключается кнопками в блоке «Сцены»).' : 'Размер ниже выбирается для ХОРОШЕГО КАЧЕСТВА (режим переключается кнопками в блоке «Сцены»).';
      const qh = $('.lm-q-hint', root);
      qh.textContent = root._dmad === false ? `Черновик недоступен: ComfyUI не видит файл minimax_h3_DMAD_4step_full_lora в папке loras (в названии должно быть «DMAD_4step»).${(root._dmadFiles || []).length ? ' Похожие файлы, которые он видит: ' + root._dmadFiles.join(', ') + '.' : ''} Положили файл — откройте этот раздел заново. Сцены пока считаются в хорошем качестве.` : Q_HINT(root._q);
      qh.style.color = root._dmad === false ? 'var(--pink)' : '';
      $$('.sc-tq', root).forEach(sel => { if (!sel._touched) sel.value = S.q; });
    };
    root._qBy = { draft: S.q, final: S.q };
    function setQ(v, force) {
      if (ACT && v !== 'final') return;   // the action studio has one quality: picked by the model, not by a draft/final switch
      if (!force && v !== root._q) { root._qBy[root._q] = S.q; S.q = QUALITY_ALL.some(x => x.id === root._qBy[v]) ? root._qBy[v] : '720p'; S._upd?.(); }   // the size chosen in «Параметры» belongs to the row that is open
      root._q = v; $$('.lm-q button', root).forEach(b => b.classList.toggle('on', b.dataset.mq === v));
      root._paintQ();
      vid.onended = null; vid.loop = true; refreshStates(); renderTL(); showClip(); onScenes();
    }
    root._setQ = setQ;
    $('.lm-q', root).onclick = e => { const b = e.target.closest('button'); if (b && !b.disabled) setQ(b.dataset.mq); };
    $('.lm-acc', root).onclick = e => { const b = e.target.closest('button'); if (!b) return; $$('.lm-acc button', root).forEach(x => x.classList.toggle('on', x === b)); };
    $('.lm-final', root).onclick = () => { setQ('final'); $('.lm-chain', root).click(); };
    // the draft LoRA is looked for again every time the section is opened: a file put into the folder later is picked up without restarting
    // Only "ComfyUI answered and there is no such file" means no draft. An error while the page reacts to the answer must never turn the draft off
    // (before, any such error landed in the same catch and left «Черновик недоступен» for good, even with the file in place).
    const checkDmad = async first => {
      let r;
      try { r = await apiGet('/api/loras/custom'); } catch { r = null; }
      if (!r || !Array.isArray(r.files)) { if (root._dmad == null) { root._dmad = false; try { setQ('final'); } catch (e) { console.warn(e); } } return; }   // ComfyUI did not answer: try again when the section is opened
      const was = root._dmad;
      root._dmadFiles = r.files.filter(f => /dmad/i.test(f));
      root._dmad = r.files.some(f => /dmad[_\-\s]?4[_\-\s]?step/i.test(f));
      try {
        if (!root._dmad) { if (root._q === 'draft') setQ('final'); else root._paintQ?.(); }
        else if (first && !root._restoring && !readyUpTo()) setQ('draft');
        else if (was !== true) root._paintQ?.();
        renderTL();
      } catch (e) { console.warn('draft check:', e); }
    };
    checkDmad(true);
    root.closest('.view') && new MutationObserver(() => { if (root.closest('.view').classList.contains('active')) checkDmad(false); }).observe(root.closest('.view'), { attributes: true, attributeFilter: ['class'] });
    queueMicrotask(() => { setQ('final'); });
    queueMicrotask(() => queueMicrotask(() => {   /* the order of the page: main settings, pictures, scenes; the rare things are folded */
      const col = $('.col', root), top = $('.rm-params', root), refsCard = $('.rm-imgs', root).closest('.card'), musCard = $('.lm-snd', root).closest('.card'), scCard = $('.lm-scenes', root).parentElement;
      const hdr = $('.card-h', top), mk = (cls, html) => { const d = document.createElement('div'); d.className = cls; d.innerHTML = html; return d; };
      $('h3', hdr).innerHTML = '<span class="n">1</span>Главные настройки';
      $('.lm-qnote', root)?.remove();
      const how = $('details.agvar', top); if (how) how.open = false;
      // mode first, then format, quality, sound
      const q = $('.lm-q', root), qh = $('.lm-q-hint', root);
      qh.style.margin = '0 0 4px';
      const rows = $$(':scope > .row', top), fmt = rows[0], qual = rows[1], own = rows[2];
      const snd = $('.lm-snd', root), sh = $('.lm-snd-hint', root);
      // two compact columns: on the left the mode and the sound, on the right the format and the size
      const modeRow = mk('row', '<div class="row-l">Режим</div>'); modeRow.append(q);
      const sndRow = mk('row', '<div class="row-l">Звук</div>'); sndRow.append(snd, sh);
      const grid = mk('st-grid', ''), colL = mk('st-l', ''), colR = mk('st-r', '');
      const modeSeg = $('.lm-mode', root), modeHint = $('.lm-mode-hint', root);
      const how2 = mk('row', '<div class="row-l">Как считать</div>'); how2.append(modeSeg, modeHint);
      colL.append(modeRow, how2, sndRow); colR.append(fmt, qual); grid.append(colL, colR);
      (how || hdr).after(grid); grid.after(qh);
      if (ACT) {   // the action studio has one quality and no song: the model, its speed-up, and what the section needs
        modeRow.hidden = true; how2.hidden = true; sndRow.hidden = true;
        const mr = mk('row', '<div class="row-l">Модель <span class="hint">чем считать сцены</span></div>');
        mr.insertAdjacentHTML('beforeend', `<div class="seg rm-amodel" style="grid-template-columns:1fr 1fr">
          <button type="button" data-a="sing">Singularity<small>как Видеостудия · кинематографичнее</small></button>
          <button type="button" data-a="action">Hybrid<small>как Экшн-сцены · держит быстрое движение</small></button></div>
          <label class="cw-chk" style="display:flex;gap:8px;align-items:center;margin-top:8px"><input type="checkbox" class="rm-apika"> Ускорение Pika: 8 шагов вместо 12 (только Singularity)</label>
          <div class="hint rm-amodel-hint">Если файлов Singularity нет, программа сама возьмёт Hybrid. Сцены считаются по одной, звук придумывает модель — своего аудио здесь нет.</div>
          <div class="rm-admad" style="margin-top:6px"></div>`);
        grid.before(mr);
        // the action scene needs its DMAD LoRA: without the file the server refuses, so say it here, before a scene is sent
        root._check = async () => {
          const box = $('.rm-admad', root); if (!box) return;
          try {
            const a = await apiGet('/api/action/info');
            box.innerHTML = a.dmad ? '<span class="hint" style="color:var(--ok,#7bd88f)">✓ файл DMAD на месте</span>' : '';
            if (!a.dmad) { box._after = root._check; dmadHelp(box, a.dmad_files); }
          } catch { box.innerHTML = ''; }   // ComfyUI не отвечает: не пугаем ложным «файла нет»
        };
        const paintM = () => {
          $$('.rm-amodel button', root).forEach(b => b.classList.toggle('on', b.dataset.a === S.actionModel));
          $('.rm-apika', root).disabled = S.actionModel !== 'sing';
          if (S.actionModel !== 'sing') $('.rm-apika', root).checked = false;
          prev?.();
        };
        mr.onclick = e => { const b = e.target.closest('.rm-amodel button'); if (!b) return; S.actionModel = b.dataset.a; paintM(); };
        $('.rm-apika', root).onchange = e => { S.actionPika = e.target.checked; prev?.(); };
        paintM();
        // the draft/quality hint and the «how it works» of the Видеостудия do not apply here
        const qh2 = $('.lm-q-hint', root); if (qh2) qh2.hidden = true;
        const howA = $('details.agvar', top);
        if (howA) {
          howA.open = false;
          const txt = howA.querySelector('div.hint');
          if (txt) txt.innerHTML = '<b>1.</b> Загрузите изображения героя и места (необязательно). <b>2.</b> Выберите формат и размер. '
            + '<b>3.</b> Добавьте сцены и нажмите «Сделать эту сцену»: сцена за сценой, каждая продолжает прошлую. '
            + 'Для экшна лучше 3–5 секунд на сцену: считается быстрее, движение чище. <b>Модель</b>: Singularity — кинематографичнее, '
            + 'Hybrid — лучше держит быстрое движение. Своего аудио нет: звук придумывает модель, опишите его в тексте сцены.';
        }
      }
      // the rare things go into one fold
      const more = document.createElement('details'); more.className = 'st-more'; more.innerHTML = '<summary>Ещё: свой размер, seed</summary>';
      [own, $('.vwarn', top), ...$$(':scope > details:not(.agvar)', top)].filter(Boolean).forEach(n => more.append(n));
      top.append(more);
      // pictures right after the settings, then the scenes
      $('h3', refsCard).innerHTML = '<span class="n">2</span>Изображения героя и места <span class="hint" style="font-weight:400">(необязательно)</span>';
      top.after(refsCard);
      $('h3', musCard).textContent = 'Своё аудио';
      const syncMus = () => { musCard.hidden = ACT || $('.lm-own', root).hidden; };
      new MutationObserver(syncMus).observe($('.lm-own', root), { attributes: true, attributeFilter: ['hidden'] }); syncMus();
      $('h3', scCard).innerHTML = '<span class="n">3</span>Сцены';
      // under the open scene: the big buttons, the tags; the rest is folded
      const sceneBox = $('.lm-scenes', root), chain = $('.lm-chain', root), fin = $('.lm-final', root), src = $('.lm-src', root), foot = $('.ta-foot', scCard);
      // the prompt of the open scene comes first, then the ribbon of fragments, then the player
      const player = $('.tl-player', scCard), allRow = player.nextElementSibling, tlEl = $('.tl', scCard), tlHint = tlEl.nextElementSibling;
      $('.card-h', scCard).after(sceneBox, tlEl, tlHint, player, allRow);
      allRow.after(chain, fin, src, foot);
      /* undo: what was cleared or deleted can be brought back (button «Отменить действие» or Ctrl+Z) */
      root._undo = [];
      const refsFiles = () => $$('.ref', imgsEl).map(r => r._file || null);
      root._pushUndo = () => {
        root._undo.push({ run: root._run, project: root._project, sel: root._sel, refs: refsFiles(), scenes: $$('.sc', root).map(c => ({ prompt: $('textarea', c).value, sec: +$('input[type=range]', c).value, fresh: !!$('.sc-fresh', c)?.checked, files: [...c._files], dn: { ...c._dn }, old: c._old, tests: [...c._tests], var: c._var || 0 })) });
        if (root._undo.length > 10) root._undo.shift();
      };
      root._doUndo = async () => {
        const u = root._undo.pop(); if (!u) return;
        $$('.sc', root).forEach(c => c.remove()); root._run = u.run; root._project = u.project || root._project;
        u.scenes.forEach(x => { addScene(x.prompt, x.sec, x.fresh); const c = $$('.sc', root).pop(); c._files = x.files; c._paint(); c._dn = x.dn; c._old = x.old; c._tests = x.tests; c._paintTests(); c._var = x.var; });
        const names = a => JSON.stringify(a.map(f => f && f.name));
        if (names(refsFiles()) !== names(u.refs)) { $$('.ref .x', imgsEl).forEach(x => x.click()); for (let i = 0; i < u.refs.length; i++) if (u.refs[i]) await putInto($$('.ref input', imgsEl)[i], u.refs[i]); }
        root._sel = u.sel; recount(); refreshStates(); toast('Действие отменено');
      };
      moduleScope.listen(document, 'keydown', e => { if ((e.ctrlKey || e.metaKey) && !e.shiftKey && String(e.key).toLowerCase() === 'z' && $('#view-long.active') && !e.target.closest('textarea, input') && root._undo.length) { e.preventDefault(); root._doUndo(); } });
      const clr = document.createElement('button'); clr.type = 'button'; clr.className = 'btn lm-clearall'; clr.textContent = 'Очистить всё и начать заново'; clr.style.cssText = 'margin:0 0 12px;width:100%';
      clr.title = 'Стирает все сцены, изображения и готовые результаты проекта. Библиотека не затрагивается';
      clr.onclick = () => confirmBox('Очистить весь проект?', 'Все сцены, изображения героя и результаты этого проекта будут стёрты со страницы, останутся две пустые сцены. Готовые видео в Библиотеке не удаляются.', 'Очистить', () => {
        root._pushUndo();
        $$('.sc', root).forEach(c => c.remove()); newRun(); root._sel = 0;
        $$('.ref .x', imgsEl).forEach(x => x.click()); root._setSrc?.(null);
        addScene('', 10); addScene('', 10); recount(); refreshStates(); toast('Проект очищен');
      });
      fin.after(clr);
      const more2 = document.createElement('details'); more2.className = 'st-more'; more2.innerHTML = '<summary>Ещё: добавить сцену, продолжить своё видео, как это работает</summary>';
      [$('#lmAdd', root), $('.lm-reset', root), ...(ACT ? [] : [$('.lm-srcfile', root)?.closest('label')]),
       [...scCard.children].filter(c => c.classList.contains('note') && !c.classList.contains('lm-src')).pop()].filter(Boolean).forEach(n => more2.append(n));
      scCard.append(more2);
      if (ACT) { fin.hidden = true; src.hidden = true;   // the action studio always counts by scenes and starts from scene 1
        more2.querySelector('summary').textContent = 'Ещё: добавить сцену, как это работает'; }
    }));
    const mus = document.createElement('div'); mus.className = 'card';
    if (ACT) mus.hidden = true;   // the sound of an action scene is made by the model itself
    root._snd = 'none';
    mus.innerHTML = `<div class="card-h"><h3><span class="n">3</span>Звук</h3></div>
      <div class="seg lm-snd" style="grid-template-columns:1fr 1fr"><button type="button" data-s="none">Без аудио<small>звук придумает модель</small></button><button type="button" data-s="own">Своё аудио<small>песня или голос</small></button></div>
      <div class="hint lm-snd-hint" style="margin:8px 0"></div>
      <div class="lm-own" hidden>
        <div class="mus-slot"></div>
        <div class="note warn lm-warn" hidden style="margin:8px 0"><span></span></div>
        <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin-top:8px"><label>Начать с <input class="lm-start" inputmode="numeric" value="0" style="width:60px"> секунды</label>
          <button class="btn" type="button" id="lmVoice" title="Оставить в аудио только голос, без музыки (как в Lipsync)">Отделить голос от музыки</button><button class="btn" type="button" id="lmVoiceBack" hidden>Вернуть исходное аудио</button></div>
        <label style="display:flex;gap:6px;align-items:center;margin-top:10px" title="Видео подстраивается под голос, а в готовом ролике играет музыка целиком"><input type="checkbox" class="lm-vocals"> Музыку в ролике оставить, но модель слушает только голос (точнее губы)</label>
      </div>`;
    $('.mus-slot', mus).append($('.rm-media', root));
    c1.after(mus);
    queueMicrotask(() => { const pc = $('.rm-params', root), hw = $('details.agvar', pc); if (hw) $('.card-h', pc).after(hw); const nt = document.createElement('div'); nt.className = 'note lm-qnote'; nt.style.margin = '10px 0'; $('.card-h', pc).after(nt); root._paintQ?.(); });   // "how it works" goes to the top of the parameters
    c1.after($('.rm-params', root));   // size and quality first: they are chosen before the scenes are written
    const HINT = { none: 'Выберите это для обычного ролика. Что говорят герои и какая музыка играет за кадром, напишите в описании сцен.', own: 'Видео подстроится под аудио: каждая сцена получает свой кусок по порядку (он подписан в карточке сцены). Звук итогового ролика — ваше аудио.' };
    const setSnd = v => {
      root._snd = v; $$('.lm-snd button', root).forEach(b => b.classList.toggle('on', b.dataset.s === v));
      $('.lm-own', root).hidden = v !== 'own'; $('.lm-snd-hint', root).textContent = HINT[v]; recount();
    };
    $('.lm-snd', root).onclick = e => { const b = e.target.closest('button'); if (b) setSnd(b.dataset.s); };
    $('.lm-start', root).oninput = () => recount();
    const slot = $('.rm-auds .slot', root);
    new MutationObserver(() => {   // keep the real length of the chosen audio
      const f = slot._file; root._adur = f ? (slot._dur || 0) : 0; if (f && !slot._orig) { slot._orig = null; } recount();
    }).observe(slot, { attributes: true, subtree: true, childList: true, characterData: true });
    $('#lmVoice', root).onclick = async () => {
      if (!slot._file) { toast('Сначала загрузите аудио'); return; }
      const b = $('#lmVoice', root), old = b.textContent; b.disabled = true; b.textContent = 'Отделяем голос…';
      try {
        const info = await apiGet('/api/audio/info');
        if (!info.available) throw new Error('Недоступно: ' + info.reason);
        const fd = new FormData(); fd.append('file', slot._file, slot._file.name);
        const blob = await apiSend('/api/audio/separate', { method: 'POST', body: fd, raw: 'blob' });
        slot._orig = slot._orig || slot._file;
        const nf = new File([blob], slot._orig.name.replace(/\.[^.]+$/, '') + '_голос.flac', { type: blob.type || 'audio/flac' });
        slot._file = nf; $('span', slot).textContent = nf.name; $('#lmVoiceBack', root).hidden = false;
        $('.lm-vocals', root).checked = false;
        toast('Голос отделён: в ролике будет только он, без музыки. Не понравилось — «Вернуть исходное аудио».');
      } catch (err) { toast(err.message); }
      b.textContent = old; b.disabled = false;
    };
    $('#lmVoiceBack', root).onclick = () => {
      if (!slot._orig) return; slot._file = slot._orig; slot._orig = null; $('span', slot).textContent = slot._file.name; $('#lmVoiceBack', root).hidden = true; toast('Исходное аудио возвращено');
    };
    queueMicrotask(() => setSnd('none'));
  }
  const files = sel => $$(sel, root).filter(x => x._file);
  function changed() {
    const imgs = $$('.ref.filled', imgsEl);
    imgs.forEach((r, i) => $('.tag', r).textContent = `Picture ${i + 1}`);
    const nv = vidsEl ? files('.rm-vids .slot').length : 0, na = files('.rm-auds .slot').length;
    const total = imgs.length + nv + na;
    $('.rm-count', root).textContent = `${total} / 12`;
    if (cfg.scenes && root._save && !root._restoring) root._save();
    $('.rm-count', root).style.color = total > 12 ? 'var(--pink)' : '';
    $('.rm-tokens', root).innerHTML = imgs.map((_, i) => `<button class="token" type="button">&lt;Picture ${i + 1}&gt;</button>`).join('') || '<span class="hint">добавьте изображения — появятся метки</span>';
    if (cfg.swap) { const w2 = $('.rm-who[data-i="1"]', root); if (w2) w2.hidden = imgs.length < 2; }
    if (cfg.swap && S.autoLen !== false) { const v = files('.rm-vids .slot')[0]; if (v && v._dur) { S.dur = Math.min(15, Math.max(3, Math.round(v._dur))); root._upd?.(); } }
    const tooLong = !cfg.scenes && [...files('.rm-vids .slot'), ...files('.rm-auds .slot')].some(s => s._dur > 15);
    if (tooLong) toast('Референс-видео и аудио — до 15 секунд');
  }
  $('.rm-tokens', root).onclick = e => {
    const t = e.target.closest('.token'); if (!t) return;
    const ta = cfg.scenes && root._ta && root._ta.isConnected ? root._ta : promptEl(), p = ta.selectionStart, txt = t.textContent + ' ';
    ta.value = ta.value.slice(0, p) + txt + ta.value.slice(ta.selectionEnd); ta.focus(); ta.selectionStart = ta.selectionEnd = p + txt.length;
  };
  changed();

  const prev = previewPanel($('.rm-preview', root), S, {
    history: !cfg.scenes,   // Video Studio keeps its own clip cards for every scene; the other sections show «Прошлые клипы»
    singleScene: () => cfg.scenes && root._mode === 'parts' && root._chainPart == null,
    goLabel: () => !cfg.scenes ? 'Сгенерировать' : ACT
      ? `Сделать сцену ${root._next() + 1}`
      : root._mode === 'parts'
      ? `${root._q === 'draft' ? 'Черновик' : 'Хорошее качество'}: сделать сцену ${root._next() + 1}`
      : `${root._q === 'draft' ? 'Черновик' : 'Хорошее качество'}: весь ролик сразу`,
    extraLabel: cfg.mode === 'refs' || cfg.mode === 'actor' || cfg.swap || cfg.scenes ? 'Шаги' : 'Схема',
    extra: () => cfg.scenes ? (root._paintQ?.(), ACT
      ? (S.actionModel === 'sing' ? (S.actionPika ? '8 шагов · Pika' : '12 шагов · Singularity') : '12 шагов · Hybrid')
      : (root._mode === 'parts' ? (root._q === 'draft' ? '4 шага · черновик' : '8 шагов · Pika') : (root._q === 'draft' ? '4 шага · черновик' : '8 шагов · Pika'))) : cfg.mode === 'actor' || cfg.swap ? '10 · 1 проход' : cfg.mode === 'refs' ? (S.unet === 'fused' ? '4 шага · Fused Turbo' : `${S.steps} · 1 проход`) : (S.faithful ? 'точная копия' : '8 + 4 шага'),
    passes: () => cfg.scenes ? $$('.sc', root).map(() => ACT
      ? (S.actionModel === 'sing' && S.actionPika ? 8 : 12)
      : (root._q === 'draft' ? 4 : 8)) : cfg.mode === 'actor' || cfg.swap ? [10] : cfg.mode === 'refs' ? (S.unet === 'fused' ? [4] : [S.steps]) : S.faithful ? [13] : [8, 4],
    get twoPass() { return cfg.mode === 'action' && !S.faithful; },
    target: () => $('.ref img', imgsEl),
    meta: () => ({ mode: cfg.action ? 'long' : cfg.mode, prompt: promptEl().value.trim(), sub: '' }),
    onDone: item => { addToLib(item); if (cfg.scenes && root._partDone) root._partDone(item); },
    onShow: item => { S.shownId = item.id; },   // the stage remembers the clip that was on the screen
    validate: () => cfg.swap ? (!files('.rm-vids .slot').length ? 'Добавьте видео, в котором нужно заменить человека' : !$$('.ref.filled', imgsEl).length ? 'Добавьте изображение нового персонажа' : '')
      : !cfg.scenes && !$$('.ref.filled', imgsEl).length && !(vidsEl && files('.rm-vids .slot').length) ? 'Добавьте хотя бы одно референс-изображение'
      : $$('.ref.filled', imgsEl).length + (vidsEl ? files('.rm-vids .slot').length : 0) + files('.rm-auds .slot').length > 12 ? 'Всего не больше 12 референсов'
      : cfg.scenes ? (root._snd === 'own' && !files('.rm-auds .slot').length ? 'Загрузите аудио или выберите «Без аудио»' : sceneList().some(x => !x.prompt) ? 'Заполните описание каждой сцены или уберите лишнюю' : '') : !promptEl().value.trim() ? 'Напишите промпт'
      : cfg.mode === 'refs' && S.unet === 'fused' && S.fusedHave === false ? 'Скачайте файл модели Fused Turbo: кнопка «Скачать» под переключателем «Модель»' : '',
    real: {
      // the action studio is a scene-by-scene job of the Видеостудия's endpoint, sampled by the action model
      url: cfg.action ? '/api/generate/long?kind=action' : '/api/generate/' + cfg.mode,
      form: () => {
        const fd = new FormData();
        fd.append('prompt', cfg.swap ? ($('.rm-who[data-i="0"]', root).value.trim() || 'замена персонажа') : promptEl().value.trim());
        if (cfg.swap) {
          const own = promptEl().value.trim();   // the user's own prompt is used as it is, in one pass; empty: the program writes it (and swaps shot by shot)
          if (own) fd.append('raw_prompt', own);
          $$('.rm-who', root).forEach(w => fd.append('who' + w.dataset.i, w.value.trim()));
          const vd = (files('.rm-vids .slot')[0] || {})._dur || 0, nImg = $$('.ref.filled', imgsEl).length;
          const cuts = own ? [] : ($('.sw-cuts', root).value || '').split(/[;,\s]+/).map(Number).filter(x => x > 0.5 && vd && x < vd - 0.5).sort((a, b) => a - b);
          if (cuts.length && vd) {   // each plan is swapped on its own: plan k gets picture k (the last picture if there are fewer)
            let bounds = [0, ...cuts, vd], parts = [];
            for (let i = 0; i < bounds.length - 1; i++) {
              if (parts.length && bounds[i + 1] - bounds[i] < 2) parts[parts.length - 1].e = bounds[i + 1]; else parts.push({ s: bounds[i], e: bounds[i + 1] });
            }
            if (parts.length > 1) {
              const whos = $$('.rm-who', root).map(w => w.value.trim());
              const ae = S.sound === 'lock' ? parseFloat(($('.sw-aend', root).value || '').replace(',', '.')) : NaN;
              fd.append('shots', JSON.stringify(parts.map((p, k) => { const o = { s: p.s, e: p.e, pic: Math.min(k, nImg - 1), who: whos[Math.min(k, nImg - 1)] || whos[0] || '' }; if (k === 0 && ae > 0 && ae < p.e - p.s) o.a_end = ae; return o; })));
            }
          }
        }
        sizeForm(S, fd); fd.append('aspect', S.aspect); fd.append('quality', S.q); fd.append('duration', S.dur);
        fd.append('steps', S.steps); fd.append('ref_size', S.refSize); if (cfg.mode === 'refs' || cfg.swap) fd.append('accel', S.accel);
        if (cfg.mode === 'refs') fd.append('unet', S.unet === 'fused' ? 'fused' : '');
        if (cfg.swap) { fd.append('sound', S.sound); fd.append('small_ref', S.smallRef ? '1' : '0'); }
        if (cfg.scenes && root._mode === 'parts') {
          const all = $$('.sc', root);
          if (root._target == null) root._chainPart = null;   // the big button alone makes one scene, never a chain
          let k = root._target; root._target = null;
          const tst = root._test; root._test = null;
          if (k == null) k = root._ready();
          if (k >= all.length) throw new Error('Все сцены уже сделаны. Чтобы переделать одну, нажмите «Переделать» в её карточке.');
          if (!tst && !root._canGo(k)) throw new Error(`Сначала сделайте сцену ${k}: эта продолжает её конец. Или отметьте у сцены «Сменить план здесь».`);
          fd.append('part', k); fd.append('run', root._run); fd.append('variant', String(all[k]._var || 0));
          if (ACT) {   // the action studio: the action endpoint, its model and its speed-up
            fd.append('kind', 'action'); fd.append('amodel', S.actionModel);
            if (S.actionModel === 'sing' && S.actionPika) fd.append('apika', '1');
          } else {
            if (root._q === 'draft' && !tst) fd.append('draft', '1');
            if ($('.lm-acc .on', root)?.dataset.x === 'old') fd.append('accel', 'old');
          }
          if (tst) { fd.set('quality', tst.q); fd.delete('width'); fd.delete('height'); fd.append('test', '1'); }   // a size test: the scene alone, in the chosen size
          else {
            if (root._src) { fd.append('src_project', '1'); if (k === 0) { if (root._src.id) fd.append('src_item', root._src.id); else if (root._src.file) fd.append('srcvid', root._src.file); } }
            root._pending = { k, sig: root._sig(all[k]) };
            (root._sent = root._sent || {})[k] = root._pending.sig; (root._sentText = root._sentText || {})[k] = $('textarea', all[k]).value;
          }
        }
        if (cfg.scenes && root._mode !== 'parts') { fd.append('run', root._run); if (root._q === 'draft') fd.append('draft', '1'); if ($('.lm-acc .on', root)?.dataset.x === 'old') fd.append('accel', 'old'); }
        if (cfg.scenes) { fd.append('scenes', JSON.stringify(sceneList())); $$('.sc', root).forEach((c, i) => c._files.forEach((f, j) => fd.append(`simg${i}_${j}`, f))); if (!ACT) { fd.append('start', $('.lm-start', root).value || '0'); fd.append('vocals', root._snd === 'own' && $('.lm-vocals', root).checked ? '1' : '0'); } }
        else loraForm(S, fd);
        seedForm(S, fd);
        $$('.ref', imgsEl).filter(r => r._file).forEach((r, i) => fd.append('img' + i, r._file));
        if (vidsEl) files('.rm-vids .slot').forEach((s, i) => fd.append('vid' + i, s._file));
        (cfg.scenes && root._snd !== 'own' ? [] : files('.rm-auds .slot')).forEach((s, i) => fd.append('aud' + i, s._file));
        return fd;
      },
    },
  });
  onScenes = () => prev();
  const STEPS_HINT = {
    4: 'Быстро, но без запаса: в речи могут теряться слова. Для простых сцен — разговор, ходьба, предмет в руках.',
    6: 'Золотая середина: разговор на камеру, ходьба, медленные движения.',
    8: 'Высокая динамика: танцы, свободные взмахи руками, толпы, дождь, листва на фоне.',
  };
  const extraRow = cfg.swap ? `<div class="row"><div class="row-l">Скорость и качество</div>
      <div class="seg rm-acc" style="grid-template-columns:1fr 1fr"><button data-x="pika">Pika Flex<small>8 шагов, рекомендуется</small></button><button data-x="old">Прежнее<small>другое ускорение</small></button></div>
      <div class="hint rm-acc-hint"></div></div>
    <div class="row"><div class="row-l">Звук и губы</div>
      <div class="seg sw-snd" style="grid-template-columns:1fr 1fr 1fr"><button data-v="original">Звук видео<small>основной вариант</small></button><button data-v="lock">Подгонка губ<small>пробный, может сломать замену</small></button><button data-v="model">Звук модели<small>если в видео нет звука</small></button></div>
      <div class="hint sw-snd-hint"></div>
      <div class="sw-aend-row" hidden style="margin-top:8px"><label style="font-size:12.5px">Где первый герой замолкает, секунды <input class="sw-aend" inputmode="decimal" placeholder="например 1.75" style="width:110px"></label>
        <div class="hint">Только для «Подгонки губ» при смене плана: до этой секунды говорит первый герой, дальше он молчит и рот закрыт. Оставьте пустым, если не знаете.</div></div></div>
    <div class="row"><label class="cw-chk" style="display:flex;gap:8px;align-items:center"><input type="checkbox" class="sw-small" checked> Уменьшать загруженное видео до размера результата (быстрее)</label>
      <div class="hint">Быстрее, на результат почти не влияет.</div></div>
    <div class="rm-loras"></div>
    <div class="row"><label class="cw-chk" style="display:flex;gap:8px;align-items:center"><input type="checkbox" class="sw-auto" checked> Длина как у загруженного видео</label>
      <div class="hint">Выключите, чтобы задать длину ползунком ниже.</div></div>
    <div class="note warn rm-swapneed" hidden><span>Для этого пресета нужен маленький файл (0,16 ГБ). <button class="btn mini pri" type="button" id="swapGet">Скачать</button></span></div>`
    : cfg.mode === 'refs' ? `
    <div class="row"><div class="row-l">Модель <span class="hint">чем генерировать</span></div>
      <div class="seg rm-unet" style="grid-template-columns:1fr 1fr"><button data-u="fast">Singularity<small>обычная · LoRA 4 шага</small></button><button data-u="fused">Fused Turbo<small>4 шага · звук ярче</small></button></div>
      <div class="hint rm-unhint" hidden></div>
      <div class="hint">Fused Turbo — та же модель на 21 ГБ, что и в разделе «Видео»: она умеет и референсы, ускорение уже внутри файла, шаги не настраиваются.</div></div>
    <div class="row rm-acc-row"><div class="row-l">Ускорение</div>
      <div class="seg rm-acc" style="grid-template-columns:1fr 1fr"><button data-x="pika">Pika Flex<small>8 шагов, лучше звук</small></button><button data-x="old">Прежнее<small>4–8 шагов</small></button></div>
      <div class="hint rm-acc-hint"></div></div>
    <div class="row rm-steps-row"><div class="row-l">Шаги</div>
      <div class="seg rm-steps" style="grid-template-columns:repeat(3,1fr)">
        <button data-s="4">4<small>быстро</small></button><button data-s="6">6 ★<small>баланс</small></button><button data-s="8">8<small>динамика</small></button>
      </div><div class="hint rm-steps-hint"></div></div>
    <div class="row"><div class="row-l">Точность переноса с изображений</div>
      <div class="seg rm-rs" style="grid-template-columns:1fr 1fr"><button data-r="match">Быстро<small>изображения уменьшаются</small></button><button data-r="max">Максимум<small>лучшее сходство</small></button></div>
      <div class="hint rm-rs-hint"></div></div>
    <div class="rm-loras"></div>`
    : cfg.scenes ? `<details class="agvar" open><summary>Как работает</summary><div class="hint" style="font-size:12.5px;line-height:1.55">
      <b>1.</b> Загрузите изображения героя и места (необязательно: можно только текстом). <b>2.</b> Выберите формат и размер. <b>3.</b> Добавьте сцены: нажмите «+» на ленте, напишите, что происходит. Длину сцены меняйте, потянув правый край блока на ленте. У каждой сцены можно загрузить свои изображения, иначе берутся общие.<br>
      <b>Черновик</b> считается быстро (4 шага, меньший размер): так видно, что получится. Когда всё нравится, нажмите «Сделать всё в хорошем качестве»: сцены пересчитаются с теми же текстами, детали станут лучше. Одну сцену можно переделать отдельно кнопкой «Переделать».<br>
      Каждая следующая сцена по умолчанию продолжает конец прошлой, но при долгой цепочке изображение портится, поэтому смену плана программа ставит сама (знак ✂ на ленте). Ориентир по времени: около минуты расчёта на секунду на RTX 4060 Ti 16 ГБ.</div></details>`
    : cfg.mode === 'actor' ? `<details class="agvar"><summary>Как работает</summary><div class="hint" style="font-size:12.5px;line-height:1.5">Один проход, 10 шагов — настройки из вашего workflow «Direct AI Actor» (ER-SDE, разреженное внимание). Первое изображение — стартовый кадр. Голос берётся из загруженного аудио только как тембр, а слова — те, что вы напишете в промпте в &lt;d&gt;[Язык] …&lt;/d&gt;.</div></details>
    <div class="rm-loras"></div>`
    : `<div class="row"><div class="row-l">Как запускается</div><div class="status-box rm-act"><span class="hint">Проверяем ComfyUI…</span></div></div>
    <div class="rm-loras"></div>`;
  const upd = paramsCard($('.rm-params', root), S, {
    n: cfg.scenes ? 2 : 3, duration: !cfg.scenes, extraRow,
    onChange: () => {
      prev();
      $$('.rm-steps button', root).forEach(b => b.classList.toggle('on', +b.dataset.s === S.steps));
      $$('.rm-rs button', root).forEach(b => b.classList.toggle('on', b.dataset.r === S.refSize));
      const h = $('.rm-steps-hint', root); if (h) h.textContent = STEPS_HINT[S.steps];
      const rh = $('.rm-rs-hint', root); if (rh) rh.textContent = REF_HINT[S.refSize];
      if (cfg.swap) { const d = $('.dur', root); if (d) { d.max = 15; d.disabled = S.autoLen !== false; } root._paintSnd && root._paintSnd(); }
      $$('.rm-acc button', root).forEach(b => b.classList.toggle('on', b.dataset.x === S.accel));
      const ah = $('.rm-acc-hint', root); if (ah) ah.textContent = S.accel === 'pika'
        ? 'Pika H3 Flex Turbo: звук получается заметно лучше. Работает на 6–8 шагах (4 шага для неё поднимутся до 6).' + (S.pikaHave === false ? ' Файла Pika нет — скачайте его в разделе «Модели».' : '')
        : 'Прежнее ускорение (LoRA на 4 шага): быстрее, но звук проще.';
      const fused = cfg.mode === 'refs' && S.unet === 'fused';
      $$('.rm-unet button', root).forEach(b => b.classList.toggle('on', b.dataset.u === S.unet));
      const ar = $('.rm-acc-row', root), sr = $('.rm-steps-row', root);   // the fused file has its own 4 steps: no LoRA or step choice
      if (ar) ar.hidden = fused;
      if (sr) sr.hidden = fused;
      // the active section's header lives in the top bar, not in its own DOM (see «one header for the whole window»)
      const pill = $('.rm-pill', root) || (root.classList.contains('active') ? $('#tbHead .rm-pill') : null);
      if (pill && cfg.mode === 'refs') pill.textContent = fused ? 'Fused Turbo Ref2V · 1 проход' : cfg.pill;
    },
  });
  root._upd = upd;
  $$('.rm-prompt', root).forEach(attachRu);
  if (cfg.swap) {
    S.sound = 'original'; S.smallRef = true;
    const SND_HINT = { lock: 'Пробный режим: модель рисует рот под звук вашего видео, голоса остаются на своих героях. Но при двух героях и смене плана вторая замена может не получиться. Нужен звук в видео.',
      original: 'Основной вариант: в результат кладётся звук вашего видео, а замена героев работает надёжнее. Губы модель рисует сама, поэтому они могут не попадать в слова.',
      model: 'Звук придумает модель. Выберите, если в вашем видео нет звука.' };
    const paintSnd = () => { $('.sw-aend-row', root).hidden = S.sound !== 'lock'; $$('.sw-snd button', root).forEach(b => b.classList.toggle('on', b.dataset.v === S.sound)); $('.sw-snd-hint', root).textContent = SND_HINT[S.sound]; };
    $('.sw-snd', root).onclick = e => { const b = e.target.closest('button'); if (b) { S.sound = b.dataset.v; paintSnd(); } };
    $('.sw-small', root).onchange = e => { S.smallRef = e.target.checked; };
    $('.sw-auto', root).onchange = e => { S.autoLen = e.target.checked; changed(); upd(); };
    paintSnd(); root._paintSnd = paintSnd;
    mountLoras($('.rm-loras', root), S, ['realism', 'motion']);
    S.loras.realism.on = false; S.loras.motion.on = false; S._paintLoras && S._paintLoras();
  }
  $('.rm-params', root).addEventListener('click', e => {
    const s = e.target.closest('.rm-steps button'); if (s) { S.steps = +s.dataset.s; upd(); }
    const r = e.target.closest('.rm-rs button'); if (r) { S.refSize = r.dataset.r; upd(); }
    const x = e.target.closest('.rm-acc button'); if (x) { S.accel = x.dataset.x; if (S.accel === 'pika' && S.steps < 6) S.steps = 8; upd(); }
    const u = e.target.closest('.rm-unet button'); if (u) { S.unet = u.dataset.u; rmFusedCheck(); upd(); }
  });
  async function rmFusedCheck() {   // the fused file is optional: offer its download right here, as in the Video section
    if (cfg.mode !== 'refs') return;
    const hint = $('.rm-unhint', root); if (!hint) return;
    hint.hidden = true; hint.innerHTML = '';
    if (S.unet !== 'fused') { S.fusedHave = null; return; }
    try {
      const st = await apiGet('/api/status');
      const m = (st.models || []).find(x => x.role === 'fused_unet');
      S.fusedHave = m ? !!m.have : null;
      if (m && !m.have) {
        hint.hidden = false;
        hint.innerHTML = `Файла модели нет в ComfyUI (${GB(m.size)}). <button class="btn mini pri rm-fdl" type="button">Скачать</button> <span class="hint" id="rmFusedProg"></span>`;
        $('.rm-fdl', hint).onclick = () => mdStart(['fused_unet']);
      }
    } catch {}
  }
  if (cfg.mode === 'refs') { REF_FUSED_CHECK = rmFusedCheck; afterModules(rmFusedCheck); }
  upd();
  if (cfg.mode === 'refs') mountLoras($('.rm-loras', root), S, ['realism', 'motion']);
  if ((cfg.mode === 'refs' || cfg.swap) && API) afterModules(() => apiGet('/api/status').then(st => {  // Pika is the default when its file is there
    const m = (st.models || []).find(x => x.role === 'pika_lora'); S.pikaHave = !!(m && m.have);
    if (S.pikaHave) { S.accel = 'pika'; S.steps = 8; }
    upd();
  }).catch(() => {}));
  if (cfg.mode === 'actor') { S.loras = { realism: { on: true, v: .7 } }; mountLoras($('.rm-loras', root), S, ['realism']); }

  if (cfg.mode === 'action' && API) {
    const box = $('.rm-act', root);
    const check = async () => {
      try {
        const a = await apiGet('/api/action/info');
        S.faithful = a.faithful; upd();
        // only the LoRAs this ComfyUI actually has can be switched and tuned
        mountLoras($('.rm-loras', root), S, [...(a.dmad ? ['dmad'] : []), ...(a.speed ? ['speed'] : [])]);
        const row = (ok, t) => `<div class="${ok ? 'ok-t' : 'warn-t'}">${ok ? '✓' : '—'} ${t}</div>`;
        // the two node packs of the exact scheme ship with the app: its own ComfyUI gets them on the next start,
        // a user's ComfyUI needs them put into custom_nodes by hand
        const lack = a.engine === 'managed'
          ? 'Нод точной схемы ещё нет: закройте и откройте программу — она поставит их сама, и точный режим включится.'
          : 'Нод точной схемы нет в вашем ComfyUI: поставьте их в custom_nodes (ссылки — «Настройки» → «Ноды»), и приложение переключится само.';
        box.innerHTML = (a.faithful ? '<b class="ok-t">Точная копия вашего workflow</b>' : '<b class="warn-t">Упрощённая схема (то же 8 + 4 шага)</b>')
          + row(a.selflift, 'SelfLift сэмплер (8 шагов в низком разрешении + 4 в высоком)')
          + row(a.refiner, 'H3 Sigma Refiner (+1 финальный шаг)')
          + row(a.upscaler, 'апскейлер h3_upscaler_lms')
          + row(!!a.dmad, 'LoRA DMAD') + row(!!a.speed, 'LoRA Speed Slider')
          + (a.faithful ? '' : `<span class="hint">${lack} Пока считается упрощённая схема: тот же график 8 + 4, но картинка грязнее.</span>`)
          + (a.dmad ? '' : '<div class="dmad-box"></div>');
        if (!a.dmad) {   // the file is missing: three buttons that lead to it and put it in place
          const d = $('.dmad-box', box); d._after = check;
          dmadHelp(d, a.dmad_files);
        }
      } catch { box.innerHTML = '<span class="hint">ComfyUI не отвечает</span>'; }
    };
    root._check = check;
    $(`.nav[data-view="action"]`).addEventListener('click', check);
    afterModules(check);
  }
  return { root, S, upd, prev, voice: () => files('.rm-auds .slot').length, prompt: () => promptEl(), imgSlots: () => $$('.ref input', imgsEl) };
}
const RM = {
  refs: makeRefMode({
    mode: 'refs', title: 'Референсы', pill: 'Singularity Ref2V · 1 проход', dur: 6, maxImages: 9, videos: true,
    sub: 'Видео по изображениям, видео и аудио: персонаж, локация, движение, голос',
    refHint: 'Изображения — персонажи, локация, предметы (до 9). Видео задаёт движение или камеру, аудио — голос, ритм или атмосферу (всего до 12 файлов). В промпте ссылайтесь на изображения как &lt;Picture 1&gt;, &lt;Picture 2&gt;…',
    placeholder: '<Picture 1> идёт по ночной улице к камере, дождь, неон…',
  }),
  actor: makeRefMode({
    mode: 'actor', title: 'Актёрская игра', pill: 'Singularity Ref2V · 10 шагов', dur: 14, maxImages: 3, videos: false,
    sub: 'Диалоги и крупные планы: актёрская игра, эмоции и голос по образцу — лучше всего для реализма',
    refHint: 'Первое изображение — стартовый кадр видео (его композиция сохранится). Аудио — образец голоса: из него берётся только тембр и манера, а слова произносятся новые, из вашего промпта. Нужны два голоса — запишите оба в одно аудио. Промпт удобно писать в разделе «Агент» (цель «Актёрская игра»).',
    placeholder: '@image1 - keyframe, first frame of the target video… Лучше всего писать в «Агенте»: он знает формат этого режима',
  }),
  swap: makeRefMode({
    mode: 'swap', title: 'Замена персонажа', pill: 'Готовый пресет · Ref2V + LoRA замены', dur: 6, maxImages: 2, videos: true, swap: true,
    sub: 'Берём ваше видео и меняем в нём человека на своего персонажа',
    refHint: '<b>Важно:</b> видео до 15 секунд. Размер результата не больше 720p (1280×736), иначе очень долго. На изображении один герой и простой фон. Используйте лица людей только с их согласия.',
    placeholder: '',
  }),
  long: makeRefMode({
    mode: 'long', title: 'Видеостудия', pill: 'Сцены подряд · Pika', dur: 20, maxImages: 5, videos: false, scenes: true,
    sub: 'Клипы и ролики по сценам: каждая сцена до 15 секунд и продолжает прошлую. Можно с музыкой, можно без',
    refHint: 'До 5 изображений-референсов: персонаж, локация, предмет, одежда, стиль — всё, что должно сохраняться. Они подаются в каждую сцену, поэтому лицо и место не «плывут». В тексте сцены ссылайтесь на них как &lt;Picture 1&gt;, &lt;Picture 2&gt;… Чем больше референсов, тем тяжелее и дольше считается сцена.',
    placeholder: '',
  }),
  actionsc: makeRefMode({
    mode: 'actionsc', action: true, title: 'Экшн-видеостудия', pill: 'Сцены подряд · DMAD', dur: 20, maxImages: 5, videos: false, scenes: true,
    sub: 'Длинный экшн по сценам, как в Видеостудии: каждая сцена продолжает прошлую. Нужно одно видео — откройте «Экшн-сцены»',
    refHint: 'До 5 изображений: персонажи и локация. Они подаются в каждую сцену, поэтому герой и место не «плывут» от сцены к сцене. В тексте сцены ссылайтесь на них как &lt;Picture 1&gt;, &lt;Picture 2&gt;… Для экшна лучше короткие сцены по 3–5 секунд: считается быстрее, движение чище.',
    placeholder: '',
  }),
  action: makeRefMode({
    mode: 'action', title: 'Экшн-сцены', pill: 'Hybrid · 8 + 4 шага', dur: 8, maxImages: 6, videos: false,
    sub: 'Бой, погоня, бег, стрельба — динамичные сцены с вашими персонажами',
    refHint: 'До 6 изображений: персонажи и локация. До 3 аудио: голос, ритм или атмосфера (вместе с изображениями). В промпте: &lt;Picture 1&gt; или роли вида «@image1 - character reference».',
    placeholder: '@image1 - character reference. Девушка с @image1 убегает по ночному рынку, опрокидывает корзины…',
  }),
};

  window.RM = RM;

  /* the swap section owns this button; it is created with the section in `makeRefMode` above */
  $('#swapGet')?.addEventListener('click', () => mdStart(['swap_lora']));

  /* a model finished downloading: the optional Fused/Turbo hint and the action schema may be stale */
  function modelsChanged() {
    REF_FUSED_CHECK && REF_FUSED_CHECK();
    RM.action.root?._check?.();
    RM.actionsc.root?._check?.();
  }

  window.h3Refs = { modelsChanged };
  /* the header switched the project: the studio saves the film it was editing and opens the film of
     the new project (or starts a new one). The page awaits it before restoring a stage there. */
  window.h3LongProjectChanged = () => RM.long?.root?._projectChanged?.();
  window.h3ActionProjectChanged = () => RM.actionsc?.root?._projectChanged?.();

  function disposeMode(id) {
    const r = RM[id]; if (!r) return;
    window.h3Lifecycle.pauseMedia(r.root);
    r.root.querySelectorAll('img, video, audio').forEach(media => {
      const src = media.getAttribute('src') || '';
      if (src.startsWith('blob:')) { try { URL.revokeObjectURL(src); } catch (e) { /* already gone */ } }
    });
    try { r.prev?.dispose?.(); } catch (e) { /* already gone */ }
    if (id === 'long' || id === 'actionsc') r.root._flush?.();
  }

  ['refs', 'actor', 'swap', 'action', 'long', 'actionsc'].forEach(id => {
    window.h3Modes.register(id, {
      mount: () => {
        if (id === 'action') RM.action.root?._check?.();
        if (id === 'actionsc') RM.actionsc.root?._check?.();   // the DMAD file may have been downloaded since the last visit
      },
      leave: () => {
        if (id === 'long' || id === 'actionsc') RM[id].root?._save?.();
        window.h3Lifecycle.pauseMedia(RM[id].root);
      },
      dispose: () => disposeMode(id),
    });
  });
})();
