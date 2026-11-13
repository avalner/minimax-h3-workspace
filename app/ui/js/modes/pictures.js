/* Pictures: "Изображение по тексту" (Krea 2), "Правка изображения" (Qwen Image) and "Инпейнт".
 *
 * A mode module: each section builds its own panel, batch list and mask canvas on the first visit.
 * The panels publish themselves in `IMG` (the page's details window, "Повторить" and the agent fill
 * a section before opening it, through `imagePanel`), the page keeps the empty map. The inpaint
 * canvas and the Qwen resize grip listen on window/document: those listeners belong to the mode's
 * scope and are removed when the window closes.
 *
 * Loaded before the page; the builds wait for the first visit.
 */

(function () {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

/* ---------- PICTURES: "Изображение по тексту" (Krea 2) and "Правка изображения" (Qwen Image 2.1) ---------- */
const IMG_ASPECTS = [['16:9', 'Горизонт', 16, 9], ['9:16', 'Вертикаль', 9, 16], ['1:1', 'Квадрат', 1, 1], ['4:3', '4:3', 4, 3], ['3:4', '3:4', 3, 4], ['3:2', '3:2', 3, 2], ['2:3', '2:3', 2, 3], ['21:9', 'Кино 21:9', 21, 9]];
const IMG_SIZES = { '16:9': [1376, 768], '9:16': [768, 1376], '1:1': [1024, 1024], '4:3': [1184, 896], '3:4': [896, 1184], '3:2': [1248, 832], '2:3': [832, 1248], '21:9': [1568, 672] };
const IMG_STAGE = {
  queued: ['В очереди ComfyUI…', 0.02], load: ['Загружаем модели в видеопамять… (первый запуск дольше)', 0.08], encode: ['Читаем промпт и изображение…', 0.2],
  sample: ['Рисуем', 0.25], upscale: ['Увеличиваем изображение…', 0.5], sample2: ['Второй проход: добавляем детали', 0.5], decode: ['Собираем изображение…', 0.92], save: ['Сохраняем…', 0.98],
};

/* MiniMax H3 Image: its own area in megapixels and its own ceiling (the model is made for 4-16 MP, while Krea and
   Qwen work at about 1-2 MP). `h3Size` repeats the server's `workflows.h3img_size`: sides in steps of 32. */
const H3IMG_MPS = ['4', '8', '16'];
const H3IMG_LIM = { high: 8192, maxPx: 20000000, bigMp: 16.5 };
const h3Size = (aspect, mp) => {
  const [aw, ah] = String(aspect || '1:1').split(':').map(Number).map(v => (v > 0 ? v : 1));
  const px = (parseFloat(mp) || 4) * 1e6, r = v => Math.max(32, Math.round(v / 32) * 32);
  return [r(Math.sqrt(px * aw / ah)), r(Math.sqrt(px * ah / aw))];
};
const h3Mp = v => (H3IMG_MPS.includes(String(v)) ? String(v) : '8');
// ComfyUI 0.45 is the first with MiniMaxH3ImageConditioning: an older engine cannot run this model at all
const comfyTooOld = v => {
  const m = /^(\d+)\.(\d+)/.exec(String(v || ''));
  return !!m && (+m[1] < 1 || (+m[1] === 0 && +m[2] < 45));
};

// one result window for both sections: sends the job, follows the progress, shows the picture
function imgPanel(el, o) {
  el.innerHTML = `
    <div class="card ip">
      <div class="ip-frame"><img class="ip-live" hidden alt=""><img class="ip-res" hidden alt="" title="Нажмите, чтобы увеличить"><div class="ip-empty">Здесь появится изображение</div>
        <button class="cw-zb ip-zb" type="button" title="Увеличить на весь экран" hidden><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4M11 8v6M8 11h6"/></svg></button></div>
      <div class="bar"><i class="ip-bar" style="width:0"></i></div>
      <div class="status ip-status"></div>
      <div class="hint ip-dim" style="text-align:center;min-height:16px"></div>
      <div class="ip-acts" hidden>
        <button class="btn pri" data-a="apply" type="button" hidden>Применить изменения</button>
        <button class="btn" data-a="addlib" type="button" hidden>Добавить в библиотеку</button>
        <button class="btn" data-a="save" type="button">Сохранить</button>
        <button class="btn" data-a="use" type="button">Использовать в…</button>
        <button class="btn pri" data-a="up" type="button" title="Увеличить изображение и добавить чёткости (SeedVR2)">Увеличить изображение</button>
        <button class="btn" data-a="lib" type="button">В Библиотеке</button>
      </div>
      <div class="cw-hh ip-hh" hidden>Прошлые изображения — нажмите, чтобы открыть</div>
      <div class="cw-hist ip-hist"></div>
      <button class="go ip-go" type="button"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4z"/></svg><span>Сгенерировать</span></button>
      <button class="stop ip-stop" type="button" hidden><i></i>Остановить генерацию</button>
    </div>`;
  const $q = s => $(s, el);
  const live = $q('.ip-live'), res = $q('.ip-res'), empty = $q('.ip-empty'), bar = $q('.ip-bar'), status = $q('.ip-status');
  const go = $q('.ip-go'), stopB = $q('.ip-stop'), acts = $q('.ip-acts'), frame = $q('.ip-frame');
  let run = null, last = null;
  const zb = $q('.ip-zb'), hist = $q('.ip-hist'), hh = $q('.ip-hh');
  const dim = $q('.ip-dim');
  res.onload = () => { if (res.naturalWidth) { frame.style.aspectRatio = `${res.naturalWidth} / ${res.naturalHeight}`; dim.textContent = 'Размер: ' + dimTxt(res.naturalWidth, res.naturalHeight); } };
  // the pictures of this section shown in its history: drafts first, then Library results
  const picItems = () => [...DRAFTS.filter(x => x.mode === o.mode && x.kind === 'image'),
                          ...LIB.filter(x => x.mode === o.mode && x.kind === 'image' && !x.missing && x.url)]
    .filter((x, i, a) => a.findIndex(y => y.id === x.id) === i)
    .filter(x => !window.h3Projects || window.h3Projects.ok(x))
    .sort((a, b) => b.date - a.date)
    .filter((it, i) => it.preview || i < 12);
  const refresh = () => {  // thumbnails of this section's earlier pictures
    const mine = picItems();
    hh.hidden = !mine.length;
    hist.innerHTML = mine.map(it => `<button draggable="true" class="cw-th${last && last.id === it.id ? ' on' : ''}${it.preview ? ' draft' : ''}" data-id="${it.id}" type="button" title="${esc((it.preview ? 'Черновик, не в Библиотеке · ' : '') + fmtDate(it.date) + ((it.prompt || '') ? ' · ' + it.prompt.slice(0, 70) : ''))}"><i class="cw-x" data-del="${it.id}" title="${it.preview ? 'Удалить черновик' : 'Удалить изображение из Библиотеки'}" role="button">✕</i><img src="${it.url}" alt="" loading="lazy"></button>`).join('');
  };
  IMG_PANELS.push(refresh);
  // which buttons fit the shown picture: a draft waits for «Применить изменения» / «Добавить в библиотеку»,
  // a Library picture has «В Библиотеке» and the enlarge it points at
  const refreshActs = () => {
    const draft = !!(last && last.preview);
    $q('[data-a="apply"]').hidden = !(o.apply && draft);
    const addB = $q('[data-a="addlib"]'); addB.hidden = !draft; addB.classList.toggle('pri', !o.apply);
    $q('[data-a="lib"]').hidden = draft;   // «Увеличить» works for a draft too: its result is a Library picture
  };
  const showItem = it => {
    last = it; live.hidden = true; empty.hidden = true; res.dataset.dragid = it.id; res.src = it.url; res.hidden = false; acts.hidden = false; zb.hidden = false;
    status.innerHTML = `${esc(fmtDate(it.date))} · Seed: <b style="font-family:var(--mono)">${esc(String(it.seed ?? '—'))}</b>` + (it.preview ? ' · <b>черновик</b>' : '');
    refreshActs(); refresh();
  };
  // the cross of a picture: the same question at the cursor as in «Прошлые клипы». Whatever was on the
  // screen gives way to the next picture — or the frame goes empty instead of keeping a deleted file.
  const clearShown = () => {
    last = null; res.hidden = true; res.removeAttribute('src'); res.removeAttribute('draggable'); delete res.dataset.dragid;
    acts.hidden = true; zb.hidden = true; empty.hidden = false; frame.style.aspectRatio = '16 / 9';
    dim.textContent = ''; status.textContent = 'Изображение удалено.';
    refreshActs(); refresh();
  };
  const deletePic = async (it, ev) => {
    const wasShown = !!(last && last.id === it.id);
    const at = ev && ev.clientX != null ? { x: ev.clientX, y: ev.clientY } : null;
    if (!(await deleteClipItem(it, at))) return;
    if (!wasShown) { refreshActs(); refresh(); return; }
    const next = picItems()[0];
    if (next) showItem(next); else clearShown();
  };
  hist.onclick = e => {
    const del = e.target.closest('[data-del]');
    if (del) {
      const it = byId(del.dataset.del) || draftById(del.dataset.del);
      if (it) deletePic(it, e);
      return;
    }
    const b = e.target.closest('.cw-th'); const it = b && (byId(b.dataset.id) || draftById(b.dataset.id)); if (it && !run) showItem(it);
  };
  res.onclick = zb.onclick = () => { if (res.src) zoomOpen(res.src); };
  const show = () => {
    if (!run) return;
    const [txt, base] = IMG_STAGE[run.stage] || IMG_STAGE.queued;
    let p = base, line = txt;
    const two = o.two && o.two();
    if ((run.stage === 'sample' || run.stage === 'sample2') && run.max) {
      const f = (run.step || 0) / run.max;
      p = run.stage === 'sample2' ? 0.5 + 0.4 * f : 0.25 + (two ? 0.25 : 0.65) * f; line = `${txt} · шаг ${run.step || 0} из ${run.max}`;
      if (run.step) line += ` · осталось ~${fmtTime((run.max - run.step) * (run.stepT - run.sampleT0) / run.step / 1000 + 3)}`;
    }
    bar.style.width = (p * 100) + '%';
    status.textContent = `${line} · прошло ${fmtTime((performance.now() - run.t0) / 1000)}`;
  };
  const end = () => { clearInterval(run.tick); offPrompt(run.id); previewHandlers.delete(el); run = null; go.hidden = false; stopB.hidden = true; bar.style.width = 0; };
  go.onclick = async () => {
    const err = o.validate(); if (err) { status.textContent = err; return; }
    go.disabled = true; if (!run) status.textContent = 'Отправляем задание в ComfyUI…';
    let r;
    try {
      r = await apiSend(o.url, { method: 'POST', body: await o.form(), timeout: 180000 });
    } catch (e) { go.disabled = false; status.textContent = 'Ошибка: ' + e.message; return; }
    go.disabled = false;
    if (run) { clearInterval(run.tick); offPrompt(run.id); if (window.h3State.has(run.id)) window.h3State.update(run.id, { detached: true }); run = null; }
    qAdd(r.prompt_id, o.mode, o.promptText());
    live.hidden = true; res.hidden = true; empty.hidden = true; acts.hidden = true; zb.hidden = true; dim.textContent = '';
    run = { id: r.prompt_id, t0: performance.now(), stage: 'queued' };
    stopB.hidden = false;
    run.tick = setInterval(show, 1000); show();
    previewHandlers.set(el, blob => {
      if (!run || run.stage === 'queued') return;
      URL.revokeObjectURL(live.dataset.url || ''); live.dataset.url = live.src = URL.createObjectURL(blob); live.hidden = false;
    });
    onPrompt(run.id, ev => {
      if (!run || run.id !== ev.prompt_id) return;
      if (ev.type === 'stage') {
        if (ev.stage.startsWith('sample') && run.stage !== ev.stage) run.sampleT0 = performance.now();
        run.stage = ev.stage; run.step = ev.step; run.max = ev.max; if (ev.step) run.stepT = performance.now();
        show();
      } else if (ev.type === 'done') {
        const took = fmtTime((performance.now() - run.t0) / 1000); end();
        const item = ev.item ? normItem(ev.item) : null;
        if (item) {
          last = item; live.hidden = true; res.dataset.dragid = item.id; res.src = item.url; res.hidden = false; acts.hidden = false; zb.hidden = false;
          status.innerHTML = item.preview
            ? `Готово за ${took}. В Библиотеку сама не попадёт: ${o.apply ? '«Применить изменения» или ' : ''}«Добавить в библиотеку». Seed: <b style="font-family:var(--mono)">${esc(String(item.seed ?? '—'))}</b>`
            : `Готово за ${took} — изображение в Библиотеке. Seed: <b style="font-family:var(--mono)">${esc(String(item.seed ?? '—'))}</b>`;
        }
        refreshActs(); refresh();
      } else if (ev.type === 'error') {
        end(); empty.hidden = !!last; res.hidden = !last; zb.hidden = !last; status.textContent = 'Ошибка ComfyUI: ' + ev.message;
      } else if (ev.type === 'interrupted') {
        end(); empty.hidden = !!last; res.hidden = !last; zb.hidden = !last; status.textContent = 'Генерация остановлена.';
      }
    });
  };
  stopB.onclick = () => { if (!run) return; status.textContent = 'Останавливаем…'; apiPost('/api/interrupt', { prompt_id: run.id }).catch(() => {}); };
  // «Добавить в библиотеку»: until this moment the picture is not in the Library and has no copy of its own
  const keepDraft = async it => {
    const b = $q('[data-a="addlib"]'); b.disabled = true;
    const kept = await keepDraftItem(it);
    b.disabled = false;
    if (kept && last && last.id === kept.id) showItem(kept);
    refreshActs(); refresh();
  };
  // «Применить изменения»: the result becomes the picture this section edits, and the work goes on
  const applyDraft = async it => {
    const b = $q('[data-a="apply"]'); b.disabled = true;
    try { status.textContent = await o.apply(it); }
    catch (err) { status.textContent = 'Не удалось применить изменения: ' + err.message; }
    b.disabled = false;
  };
  acts.onclick = async e => {
    const a = e.target.closest('[data-a]')?.dataset.a; if (!a || !last) return;
    const it = byId(last.id) || last;
    if (a === 'save') saveItems([it]);
    else if (a === 'use') useMenu(it);
    else if (a === 'up') openUpscale(it);
    else if (a === 'lib') goTo('library');
    else if (a === 'addlib') await keepDraft(it);
    else if (a === 'apply') await applyDraft(it);
  };
  frame.style.aspectRatio = '16 / 9'; refresh();
  el._dispose = () => {
    if (run) { clearInterval(run.tick); offPrompt(run.id); previewHandlers.delete(el); run = null; }
    try { URL.revokeObjectURL(live.dataset.url || ''); } catch (e) { /* already gone */ }
  };
  return { frame, show: showItem, ratio: (w, h) => { if (!run && !last) frame.style.aspectRatio = `${w} / ${h}`; } };
}

function imgSeedBox(S) {
  return `<details style="margin-top:16px"><summary>Дополнительно</summary>
    <div class="row" style="margin-top:14px"><div class="row-l">Seed — «номер варианта»</div>
      <div class="seg" style="grid-template-columns:1fr 1fr"><button data-sf="0">Случайный<small>каждый раз новое изображение</small></button><button data-sf="1">Фиксированный<small>тот же вариант снова</small></button></div>
      <div class="path on" data-seedrow hidden><input data-seedin inputmode="numeric" spellcheck="false" placeholder="число, например 123456789"></div>
    </div></details>`;
}
function imgSeedWire(root, S) {
  const paint = () => { $$('[data-sf]', root).forEach(b => b.classList.toggle('on', (b.dataset.sf === '1') === !!S.seedFixed)); $('[data-seedrow]', root).hidden = !S.seedFixed; };
  root.addEventListener('click', e => { const b = e.target.closest('[data-sf]'); if (b) { S.seedFixed = b.dataset.sf === '1'; paint(); } });
  $('[data-seedin]', root).oninput = e => { S.seed = e.target.value.replace(/\D/g, ''); e.target.value = S.seed; };
  paint();
}
const imgSeedForm = (S, fd) => { if (S.seedFixed && S.seed) fd.append('seed', S.seed); };
// seed из задания в очереди: тот же вариант повторится, если не менять промпт и настройки
function imgJobSeed(root, S, seed) {
  if (seed == null || seed === '') return;
  S.seedFixed = true; S.seed = String(seed);
  $('[data-sf="1"]', root)?.click();   // переключатель «Фиксированный» и поле
  const inp = $('[data-seedin]', root);
  if (inp) { inp.value = S.seed; inp.dispatchEvent(new Event('input')); }
}

/* --- Krea 2: by text --- */
function buildKrea(scope) {
  const root = $('#view-krea'), S = { aspect: '16:9', style: 'plain', scale: '2k', engine: 'krea', mp: '4' };
  root.innerHTML = `
    <div class="head"><div><h1>Изображение по тексту</h1><p>Опишите изображение словами — его нарисует Krea 2, FLUX.2 klein, Qwen 2.1 или MiniMax H3 Image. Загрузить своё изображение здесь нельзя: для этого раздел «Правка изображения»</p></div>
      <div class="mode-pill"><i></i><span>Krea 2 · 1 МП</span></div></div>
    <div class="grid"><div class="col">
      <div class="card"><div class="card-h"><h3><span class="n">1</span>Промпт</h3><span class="counter kr-count">0 символов</span></div>
        <div class="seg kr-bm" style="grid-template-columns:1fr 1fr;margin-bottom:8px"><button type="button" data-b="0" class="on">Один промпт</button><button type="button" data-b="1">Список промптов<small>серия изображений</small></button></div>
        <textarea class="kr-prompt" placeholder="Опишите изображение: кто или что в кадре, где, какой свет, какой стиль (фото, иллюстрация, постер)…"></textarea>
        <div class="kr-batch" hidden style="margin-top:10px">
          <div class="hint" style="margin-bottom:6px">Вставьте промпты одним куском: каждый отделите <b>пустой строкой</b> (или пронумеруйте: «1.», «Кадр 2:»). Приложение само разделит текст и поставит каждое изображение в очередь. Модель, формат и размер общие для всех.</div>
          <div style="display:flex;justify-content:space-between;align-items:center"><b class="kr-bcount"></b></div>
          <div class="kr-bl" style="max-height:220px;overflow:auto;margin:6px 0"></div>
          <div class="note warn kr-bwarn" hidden><span></span></div>
          <label style="display:flex;gap:8px;align-items:flex-start;margin:8px 0;cursor:pointer" title="Если нужна серия похожих кадров, включите. Для разных изображений оставьте выключенным: у каждой будет свой случайный seed."><input type="checkbox" class="kr-same" style="margin-top:3px"><span>Одинаковый seed для всей серии <span class="hint">(для серии кадров; для разных изображений не нужно). Если ниже задан фиксированный seed, возьмётся он.</span></span></label>
          <button class="go kr-bgo" type="button" disabled><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4z"/></svg><span>Сгенерировать</span></button>
          <div class="status kr-bstatus" style="margin-top:6px"></div>
        </div>
        <div class="ta-foot"><span class="hint">Лучше писать подробно, одним абзацем. Помощник в разделе «Агент» умеет писать такие промпты</span></div></div>
      <div class="card"><div class="card-h"><h3><span class="n">2</span>Параметры</h3></div>
        <div class="row"><div class="row-l">Модель</div>
          <div class="seg kr-eng" style="grid-template-columns:repeat(2,1fr)"><button data-e="krea">Krea 2<small>лучшее качество, можно в коммерцию</small></button><button data-e="flux">FLUX.2 klein<small>можно в коммерцию</small></button><button data-e="qwen">Qwen 2.1<small>текст на изображении, не для коммерции</small></button><button data-e="h3img">MiniMax-H3-Image<small>крупно, до 16 МП</small></button></div>
          <div class="hint kr-enghint" hidden></div>
          <div class="hint" style="margin-top:6px">Лицензии: <b>Krea 2</b> и <b>FLUX.2 klein</b> можно использовать в коммерции (Krea 2 — если годовой доход компании меньше 1 млн $). <b>Qwen 2.1</b> только для творчества, не для коммерции. <b>MiniMax-H3-Image</b> — открытая community-лицензия: коммерческое использование до 1 млн $ годовой выручки (иначе нужно отдельное разрешение MiniMax), в ЕС, Великобритании, Южной Корее и США модель не лицензирована.</div></div>
        <div class="row kr-neg" hidden><div class="row-l">Негативный промпт <span class="hint">что не должно быть на изображении</span></div>
          <textarea class="kr-negtxt" rows="2" placeholder="Пусто — быстрее и легче для видеокарты. Если нужно, впишите, например: blurry, low quality, extra fingers, text, watermark"></textarea><div class="hint" style="margin-top:4px">Негативный промпт замедляет Qwen примерно вдвое и требует больше видеопамяти. Заполняйте, только если без него не получается.</div></div>
        <div class="row kr-stylerow"><div class="row-l">Стиль</div>
          <div class="seg" style="grid-template-columns:1fr 1fr"><button data-st="plain">Обычный<small>быстро, любые изображения</small></button><button data-st="real">Реализм<small>люди и фото как с камеры, 2 прохода</small></button></div>
          <div class="hint kr-realhint" hidden></div></div>
        <div class="row kr-scale" hidden><div class="row-l">Размер результата</div>
          <div class="seg" style="grid-template-columns:1fr 1fr"><button data-sc="2k">Около 2K<small>быстрее</small></button><button data-sc="4k">4K<small>дольше и нужно больше видеопамяти</small></button></div></div>
        <div class="row kr-mprow" hidden><div class="row-l">Размер результата</div>
          <div class="seg kr-mp" style="grid-template-columns:repeat(3,1fr)"><button data-mp="4">4 МП<small>быстрее, видно зерно</small></button><button data-mp="8">8 МП<small>чисто</small></button><button data-mp="16">16 МП<small>самое чистое, дольше</small></button></div>
          <div class="hint">Модель рассчитана на 4–16 МП: ниже 8 МП заметно зерно. Площадь считается по выбранному формату, свой размер можно задать ниже.</div></div>
        <div class="row"><div class="row-l">Формат <span class="resv"></span></div><div class="chips kr-asp"></div></div>
        ${SZ_ROW}
        <div class="note kr-h3hint" hidden style="margin-top:10px"><span>В промпте картинки называются <b>&lt;Picture 1&gt;</b>…<b>&lt;Picture 5&gt;</b>, и первая задаёт форму результата. <b>Негативного промпта у MiniMax-H3-Image нет</b> — всё нужное пишите в положительной форме. Модель: MiniMax-H3-Image (Powered by MiniMax-H3-Image).</span></div>
        ${imgSeedBox()}</div>
    </div><div class="preview kr-prev"></div></div>`;
  gridResize($('.grid', root));
  $('.kr-asp', root).innerHTML = IMG_ASPECTS.map(([id, name, w, h]) => { const k = 18 / Math.max(w, h);
    return `<button class="chip" data-a="${id}"><i class="ar" style="width:${w * k}px;height:${h * k}px"></i>${name}${name !== id ? ` <small>${id}</small>` : ''}</button>`; }).join('');
  const krForm = text => { const fd = new FormData(); fd.append('prompt', text);
    fd.append('preview', '1');   // the Library waits for «Добавить в библиотеку»
    fd.append('aspect', S.aspect); fd.append('style', S.engine === 'krea' ? S.style : 'plain'); fd.append('scale', S.scale); fd.append('engine', S.engine); if (S.engine === 'h3img') fd.append('mp', S.mp); if (S.cw && S.ch) { fd.append('width', S.cw); fd.append('height', S.ch); } if (S.engine === 'qwen') fd.append('negative', $('.kr-negtxt', root).value.trim()); imgSeedForm(S, fd); return fd; };
  const P = imgPanel($('.kr-prev', root), {
    mode: 'krea', url: '/api/generate/krea', promptText: () => $('.kr-prompt', root).value.trim(),
    validate: () => $('.kr-prompt', root).value.trim() ? '' : 'Напишите промпт',
    form: () => { const fd = new FormData(); fd.append('prompt', $('.kr-prompt', root).value.trim()); fd.append('preview', '1'); fd.append('aspect', S.aspect); fd.append('style', S.engine === 'krea' ? S.style : 'plain'); fd.append('scale', S.scale); fd.append('engine', S.engine); if (S.engine === 'h3img') fd.append('mp', S.mp); if (S.cw && S.ch) { fd.append('width', S.cw); fd.append('height', S.ch); } if (S.engine === 'qwen') fd.append('negative', $('.kr-negtxt', root).value.trim()); imgSeedForm(S, fd); return fd; },
    two: () => S.engine === 'krea' && S.style === 'real',
  });
  const upd = () => {
    const h3 = S.engine === 'h3img';
    const base = h3 ? h3Size(S.aspect, S.mp) : IMG_SIZES[S.aspect];
    $$('.kr-asp .chip', root).forEach(c => c.classList.toggle('on', c.dataset.a === S.aspect));
    const [w, h] = S.cw && S.ch ? [S.cw, S.ch] : base, k = S.style === 'real' ? (S.scale === '4k' ? 2.5 : 1.5) : 1;
    $('.resv', root).textContent = `${Math.round(w * k)}×${Math.round(h * k)}`; P.ratio(w, h);
    $$('[data-st]', root).forEach(b => b.classList.toggle('on', b.dataset.st === S.style));
    $$('[data-sc]', root).forEach(b => b.classList.toggle('on', b.dataset.sc === S.scale));
    $('.kr-scale', root).hidden = !(S.engine === 'krea' && S.style === 'real');
    $('.kr-mprow', root).hidden = !h3;
    $$('.kr-mp button', root).forEach(b => b.classList.toggle('on', b.dataset.mp === S.mp));
    $$('.sz-p', root).forEach(b => b.hidden = h3);   // «Эскиз» и «2К» — это про 1-2 МП, у H3 Image другой счёт
    $$('.kr-eng button', root).forEach(b => b.classList.toggle('on', b.dataset.e === S.engine));
    $('.kr-stylerow', root).hidden = S.engine !== 'krea'; $('.kr-neg', root).hidden = S.engine !== 'qwen';
    $('.kr-h3hint', root).hidden = !h3;
    $('.mode-pill span', root).textContent = h3 ? `MiniMax-H3-Image · ${S.mp} МП`
      : { krea: 'Krea 2 · 1 МП', flux: 'FLUX.2 klein · 1 МП', qwen: 'Qwen Image 2.1 · 1 МП' }[S.engine];
  };
  const H3_FILE_HINT = 'Положите в папки моделей вашего ComfyUI: minimax_h3_image_pruned_w4a8.safetensors (diffusion_models), minimax_h3_image_vae_fp16.safetensors (vae) и энкодер qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors (text_encoders). Потом обновите раздел «Модели». Для этой модели нужен ComfyUI 0.45 или новее.';
  async function krEngCheck() {
    const hint = $('.kr-enghint', root); hint.hidden = true;
    if (S.engine === 'krea') return;
    const role = S.engine === 'flux' ? 'flux' : S.engine === 'h3img' ? 'h3img' : 'qwen';
    try {
      const st = await apiGet('/api/status');
      const miss = (st.models || []).filter(m => m.role.startsWith(role) && !m.have);
      const old = S.engine === 'h3img' && comfyTooOld(st.comfy_version);
      if (!(st.modes || []).includes(role) || miss.length || old) {
        hint.hidden = false;
        hint.textContent = old
          ? `Нужен ComfyUI 0.45 или новее: в ${st.comfy_version || 'этой версии'} нет ноды MiniMaxH3ImageConditioning. Обновите ComfyUI (в портативной сборке — update\\update_comfyui.bat), потом перезапустите программу.`
          : st.engine === 'managed'
            ? `Для этой модели нужны ещё файлы. Откройте «Модели» и нажмите «Добавить ${role === 'flux' ? 'Правка изображения: FLUX.2' : role === 'h3img' ? 'Изображения MiniMax H3 Image' : 'Правка изображения'}».`
            : role === 'flux'
              ? 'Положите в папки моделей вашего ComfyUI: flux-2-klein-4b-fp8.safetensors (diffusion_models), qwen_3_4b.safetensors (text_encoders) и flux2-vae.safetensors (vae). Потом обновите раздел «Модели».'
              : role === 'h3img' ? H3_FILE_HINT
              : 'Для Qwen 2.1 нужны её модели (раздел «Правка изображения»). Проверьте «Модели».';
      }
    } catch {}
  }
  root.addEventListener('click', async e => {
    const en = e.target.closest('.kr-eng button'); if (en) { S.engine = en.dataset.e; upd(); krEngCheck(); return; }
    const mpb = e.target.closest('.kr-mp button'); if (mpb) { S.mp = mpb.dataset.mp; upd(); return; }
    const sc = e.target.closest('[data-sc]'); if (sc) { S.scale = sc.dataset.sc; upd(); return; }
    const b = e.target.closest('[data-st]'); if (!b) return;
    S.style = b.dataset.st; upd();
    const hint = $('.kr-realhint', root); hint.hidden = true;
    if (S.style !== 'real') return;
    try {
      const st = await apiGet('/api/status');
      const miss = (st.models || []).filter(m => m.role.startsWith('kreareal') && !m.have);
      if (!(st.modes || []).includes('kreareal') || miss.length) {
        hint.hidden = false;
        hint.textContent = st.engine === 'managed'
          ? 'Для реализма нужны ещё две модели (около 15 ГБ). Откройте «Модели» и нажмите «Добавить Изображение: реализм».'
          : 'Для реализма положите в папки моделей вашего ComfyUI: redcraftKREA2RedMix_krea2Edition.safetensors (diffusion_models) и realism_engine_krea2_v2.safetensors (loras). Потом обновите раздел «Модели».';
      }
    } catch {}
  });
  $('.kr-asp', root).onclick = e => { const c = e.target.closest('.chip'); if (c) { S.aspect = c.dataset.a; S.cw = S.ch = 0; $('.sz-w', root).value = $('.sz-h', root).value = ''; $('.sz-reset', root).hidden = true; upd(); } };
  szWire(root, S, () => upd(), () => IMG_SIZES[S.aspect], () => (S.engine === 'h3img' ? H3IMG_LIM : null));
  $('.kr-prompt', root).oninput = e => $('.kr-count', root).textContent = e.target.value.length + ' символов';

  /* --- list of prompts: a series of pictures, one queue job per prompt --- */
  const K = { batch: false, items: [], removed: new Set() };
  const splitList = text => {
    const t = text.replace(/\r/g, '').trim(); if (!t) return [];
    let parts = t.split(/\n\s*\n/).map(x => x.trim()).filter(Boolean);
    if (parts.length < 2) { const m = t.split(/\n(?=\s*(?:кадр|frame|scene|сцена)?\s*\d+\s*[:.)\-–]\s)/i).map(x => x.trim()).filter(Boolean); if (m.length > 1) parts = m; }
    return parts.map(x => x.replace(/^\s*(?:кадр|frame|scene|сцена)?\s*\d+\s*[:.)\-–]\s*/i, '').replace(/\s*\n\s*/g, ' ').trim()).filter(Boolean);
  };
  const BATCH_MAX = 50;
  const kbRender = () => {
    const all = splitList($('.kr-prompt', root).value);
    K.items = all.filter(x => !K.removed.has(x));
    const over = K.items.length > BATCH_MAX;
    $('.kr-bl', root).innerHTML = K.items.map((x, i) => `<div class="kr-bi" style="display:flex;gap:8px;align-items:flex-start;padding:5px 0;border-bottom:1px solid var(--line)"><b style="min-width:26px">${i + 1}</b><span style="flex:1;font-size:12.5px">${esc(x.length > 120 ? x.slice(0, 120) + '…' : x)}</span><button class="btn" type="button" data-rm="${i}" title="Убрать из списка" style="padding:1px 8px">✕</button></div>`).join('')
      || '<div class="hint">Вставьте текст выше: промпты, разделённые пустой строкой.</div>';
    const n = K.items.length;
    $('.kr-bcount', root).textContent = n ? `Промптов: ${n}` : '';
    const w = $('.kr-bwarn', root); w.hidden = !over; w.textContent = over ? `Не больше ${BATCH_MAX} промптов за раз: уберите лишние или разбейте на части.` : '';
    const go = $('.kr-bgo', root); go.disabled = !n || over; go.querySelector('span').textContent = n ? `Сгенерировать ${n} ${n % 10 === 1 && n % 100 !== 11 ? 'изображение' : (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20)) ? 'изображения' : 'изображений'}` : 'Сгенерировать';
  };
  const kbMode = on => {
    K.batch = on; root.classList.toggle('kr-batching', on);
    $$('.kr-bm button', root).forEach(b => b.classList.toggle('on', (b.dataset.b === '1') === on));
    $('.kr-batch', root).hidden = !on;
    $('.kr-prompt', root).placeholder = on ? 'Вставьте сюда список промптов. Каждый промпт отделите пустой строкой: из каждого получится отдельное изображение.' : 'Опишите изображение: кто или что в кадре, где, какой свет, какой стиль (фото, иллюстрация, постер)…';
    if (on) kbRender();
  };
  $('.kr-bm', root).onclick = e => { const b = e.target.closest('button'); if (b) kbMode(b.dataset.b === '1'); };
  $('.kr-bl', root).onclick = e => { const b = e.target.closest('[data-rm]'); if (!b) return; K.removed.add(K.items[+b.dataset.rm]); kbRender(); };
  $('.kr-prompt', root).addEventListener('input', () => { if (K.batch) { K.removed.clear(); kbRender(); } });
  $('.kr-bgo', root).onclick = async () => {
    const st = $('.kr-bstatus', root), n = K.items.length; if (!n || n > BATCH_MAX) return;
    const same = $('.kr-same', root).checked, fixed = S.seedFixed && S.seed;
    const seed = same ? (fixed ? S.seed : String(Math.floor(Math.random() * 1e12))) : null;
    const go = $('.kr-bgo', root); go.disabled = true; let ok = 0;
    for (let i = 0; i < n; i++) {
      st.textContent = `Отправляем в очередь: ${i + 1} из ${n}…`;
      try {
        const fd = krForm(K.items[i]);
        if (seed) fd.set('seed', seed); else fd.delete('seed');   // every picture its own random seed unless it is a series
        fd.append('batch_label', `${i + 1} из ${n}`);
        const res = await apiSend('/api/generate/krea', { method: 'POST', body: fd, timeout: 180000 });
        qAdd(res.prompt_id, 'krea', K.items[i]); ok++;
      } catch (err) { st.textContent = `Ошибка на промпте ${i + 1}: ${err.message}. В очередь поставлено: ${ok}.`; go.disabled = false; return; }
    }
    const pl = k => k % 10 === 1 && k % 100 !== 11 ? 'изображение' : (k % 10 >= 2 && k % 10 <= 4 && (k % 100 < 10 || k % 100 >= 20)) ? 'изображения' : 'изображений';
    st.innerHTML = `В очереди ${ok} ${pl(ok)}. Готовые появятся ниже, в «Прошлых изображениях»; в Библиотеку их добавит кнопка «Добавить в библиотеку». Следить за ходом можно в разделе «Очередь».`; go.disabled = false;
  };
  const fillList = text => { kbMode(true); $('.kr-prompt', root).value = text; $('.kr-prompt', root).oninput({ target: $('.kr-prompt', root) }); K.removed.clear(); kbRender(); };
  imgSeedWire(root, S); upd();
  IMG.krea = { fillList, fill: it => {
    if (it.engine) S.engine = it.engine;
    else if (/^Изображение \(MiniMax H3/.test(it.sub || '')) S.engine = 'h3img';
    else if (/^(Картинка|Изображение) \(FLUX/.test(it.sub || '')) S.engine = 'flux';
    else if (/^(Картинка|Изображение) \(Qwen/.test(it.sub || '')) S.engine = 'qwen';
    else S.engine = 'krea';
    if (S.engine === 'h3img') S.mp = h3Mp(it.mp || (/^\s*(\d+)\s*МП/i.exec(it.q || '') || [])[1]);
    $('.kr-prompt', root).value = it.prompt || ''; $('.kr-prompt', root).oninput({ target: $('.kr-prompt', root) }); if (IMG_SIZES[it.aspect]) S.aspect = it.aspect; upd(); } };
  // задание из очереди: промпт, модель, стиль, формат, размер и seed — всё, что записано в его параметрах
  IMG.krea.fillJob = m => {
    const sub = String(m.sub || '');
    S.engine = /MiniMax H3 Image/i.test(sub) ? 'h3img' : /FLUX\.2/i.test(sub) ? 'flux' : /Qwen/i.test(sub) ? 'qwen' : 'krea';
    S.style = S.engine === 'krea' && /реализм/i.test(sub) ? 'real' : 'plain';
    if (S.style === 'real') S.scale = /^4K$/i.test(m.q || '') ? '4k' : '2k';
    if (S.engine === 'h3img') S.mp = h3Mp(m.mp || (/^\s*(\d+)\s*МП/i.exec(m.q || '') || [])[1]);
    if (IMG_SIZES[m.aspect]) S.aspect = m.aspect;
    kbMode(false);
    $('.kr-prompt', root).value = m.prompt || '';
    $('.kr-prompt', root).oninput({ target: $('.kr-prompt', root) });
    imgJobSeed(root, S, m.seed);
    upd();
  };
}

/* --- «Редактирование изображений»: a page of cards; a card opens the screen of ONE task --- */
const IMG_TASK_ICONS = {
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
  inpaint: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M9.06 11.9 3.6 13.44a1 1 0 0 0-.54 1.66l4.84 4.84a1 1 0 0 0 1.66-.54L11.1 14.94"/><path d="m14.47 12.53 6.43-6.43a2.12 2.12 0 0 0-3-3l-6.43 6.43"/><path d="m11.9 9.06-2.44.65a1 1 0 0 0-.54 1.66l1.58 1.58"/><path d="M19.07 4.93a2.12 2.12 0 0 1 3 3"/></svg>',
  bg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="12" cy="10" r="3.2"/><path d="M6 20c1.6-3 3.6-4.4 6-4.4s4.4 1.4 6 4.4"/></svg>',
  outpaint: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="8" width="8" height="8" rx="1.5"/><path d="M3 8V5a2 2 0 0 1 2-2h3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M21 16v3a2 2 0 0 1-2 2h-3"/></svg>',
  sheet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="18" rx="1.5"/><rect x="14" y="3" width="7" height="8" rx="1.5"/><rect x="14" y="15" width="7" height="6" rx="1.5"/></svg>',
  story: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="5" width="7" height="14" rx="1.5"/><rect x="9.5" y="5" width="7" height="14" rx="1.5"/><rect x="16.5" y="5" width="5" height="14" rx="1.5"/></svg>',
};
const IMG_TASKS = [
  { id: 'edit', title: 'Править по описанию',
    text: 'Измените на изображении одежду, цвет, место или детали словами. Всё, что не названо, остаётся прежним.', need: 'изображение + что изменить' },
  { id: 'inpaint', title: 'Инпейнт',
    text: 'Закрасьте кистью место и опишите, что там должно быть. Всё, что не закрашено, не тронется.', need: 'изображение + закраска' },
  { id: 'bg', title: 'Убрать фон',
    text: 'Объект остаётся на месте, фон становится чисто белым.', need: 'изображение' },
  { id: 'outpaint', title: 'Расширение',
    text: 'Достройте изображение до другого формата: программа дорисует края, а сам кадр останется целым.', need: 'изображение + новый формат' },
  { id: 'sheet', title: 'Лист персонажа',
    text: 'Из одного изображения героя получается лист из 4 или 6 видов с разных сторон.', need: 'изображение героя' },
  { id: 'story', title: 'Раскадровка',
    text: 'История в кадрах по изображениям героя и места: одним листом или по кадрам.', need: 'изображения героя и места' },
];

function buildImgEdit(scope) {
  const root = $('#view-imgedit');
  root.innerHTML = `
    <div class="pr-head"><h1>Редактирование изображений</h1><p>Выберите, что сделать с готовым изображением. У каждой задачи свой экран — программа откроет его с уже выбранным делом</p></div>
    <div class="pr-grid">${IMG_TASKS.map(t => `<div class="pr-card" role="button" tabindex="0" data-task="${t.id}">
      <div class="pr-pic">${IMG_TASK_ICONS[t.id]}</div>
      <div class="pr-body"><b>${t.title}</b><p>${t.text}</p><div class="pr-tags"><span>Изображение</span><span>нужно: ${t.need}</span></div></div></div>`).join('')}</div>
    <div class="note ie-miss" hidden style="margin-top:18px"><span></span></div>`;
  const open = id => {
    if (id === 'inpaint') { goTo('inpaint'); return; }
    goTo('qwen');               // builds the editor on the first visit and shows it
    const q = $('#view-qwen');
    if (q._solo) q._solo(id);   // the task is already chosen: the «Что делаем» switch is not shown
  };
  root.onclick = e => { const c = e.target.closest('[data-task]'); if (c) open(c.dataset.task); };
  root.onkeydown = e => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const c = e.target.closest('[data-task]');
    if (c) { e.preventDefault(); open(c.dataset.task); }
  };
  // «← Все варианты редактирования» on every task screen
  scope.listen(document, 'click', e => { if (e.target.closest('[data-imgedit]')) { e.preventDefault(); goTo('imgedit'); } });
  // is the picture editor installed at all? (a mode the user did not add has no models)
  (async () => {
    try {
      const st = await apiGet('/api/status');
      const miss = Array.isArray(st.modes) && !st.modes.includes('qwen');
      const box = $('.ie-miss', root);
      box.hidden = !miss;
      if (miss) $('span', box).innerHTML = 'Правка изображений не установлена. Откройте <b>Модели</b> и добавьте её — или подключите свой ComfyUI с готовыми файлами. Пока раздела нет, работают только те задачи, чьи модели уже скачаны.';
    } catch {}
  })();
}

/* --- Qwen Image 2.1: edit a picture, character sheet --- */
function buildQwen(scope) {
  const root = $('#view-qwen'), S = { kind: 'edit', size: '1', aspect: '', aspectOut: '16:9', headless: true, bg: 'grey', panels: 4, engine: 'qwen', frames: true, line: 'white', mp: '8' };
  root.innerHTML = `
    <div class="pr-bar"><button class="pr-back" type="button" data-imgedit>← Все варианты редактирования</button></div>
    <div class="head"><div><h1>Редактирование изображений</h1><p>Правьте готовые изображения: изменить по описанию, закрасить место (инпейнт), убрать фон, расширить картинку, собрать лист персонажа</p></div>
      <div class="mode-pill"><i></i><span>Qwen Image 2.1</span></div></div>
    <div class="grid"><div class="col">
      <div class="card"><div class="card-h"><h3><span class="n">1</span>Изображение</h3><button class="btn mini" id="qwClrAll" type="button" title="Убрать все изображения из ячеек">Очистить всё</button></div>
        <div class="qw-slots">
          <label class="drop" id="qwDrop"><input type="file" accept="image/*"><div class="ph"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="1.6"/><path d="m21 15-5-5L5 21"/></svg><b>Ваше изображение</b><span>перетащите или нажмите</span></div><span class="tag" hidden>Исходная</span><button class="x" type="button">✕</button></label>
          <label class="drop qw-s2" id="qwDrop2" title="Ещё одно изображение (референс, по желанию)"><input type="file" accept="image/*"><div class="ph"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></div><span class="tag" hidden>2</span><button class="x" type="button">✕</button></label>
          <label class="drop qw-s3" id="qwDrop3" title="Ещё одно изображение (референс, по желанию)"><input type="file" accept="image/*"><div class="ph"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></div><span class="tag" hidden>3</span><button class="x" type="button">✕</button></label>
          <label class="drop qw-s4" id="qwDrop4" title="Ещё одно изображение (референс, только для MiniMax H3 Image)"><input type="file" accept="image/*"><div class="ph"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></div><span class="tag" hidden>4</span><button class="x" type="button">✕</button></label>
          <label class="drop qw-s5" id="qwDrop5" title="Ещё одно изображение (референс, только для MiniMax H3 Image)"><input type="file" accept="image/*"><div class="ph"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></div><span class="tag" hidden>5</span><button class="x" type="button">✕</button></label>
        </div>
        <div class="hresize" id="qwResize" title="Потяните вверх или вниз, чтобы изменить высоту картинок. Двойной клик — вернуть как было"></div>
        <div class="hint qw-morehint" style="margin-top:8px"></div></div>
      <div class="card"><div class="card-h"><h3><span class="n">2</span>Что делаем</h3></div>
        <div class="seg qw-kind" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))"><button data-k="edit">Править по описанию<small>изменить по описанию</small></button><button data-k="bg">Убрать фон<small>фон станет чисто белым</small></button><button data-k="outpaint">Расширение<small>дорисовать края, сменить формат</small></button><button data-k="sheet">Лист персонажа<small>4 или 6 видов с разных сторон</small></button><button data-k="story">Раскадровка<small>история по изображениям героя и места</small></button></div>
        <div class="qw-edit" style="margin-top:14px">
          <div class="row qw-storyopts" hidden style="margin-bottom:12px"><div class="row-l">Как рисовать</div>
            <div class="seg qw-fr" style="grid-template-columns:1fr 1fr"><button data-fr="1">По кадрам<small>каждый кадр отдельно, лист склеит программа</small></button><button data-fr="0">Одним листом<small>одно изображение сразу</small></button></div>
            <div class="row qw-lnrow" style="margin-top:10px"><div class="row-l">Линия между кадрами</div>
              <div class="seg qw-ln" style="grid-template-columns:repeat(3,1fr)"><button data-ln="white">Тонкая белая</button><button data-ln="black">Тонкая чёрная</button><button data-ln="none">Без линии</button></div></div>
            <div class="hint" style="margin-top:6px">Кадры пишите по одному в строке: «1: ...», «2: ...» (2, 3, 4, 6, 8 или 9 кадров). Каждый кадр сохраняется отдельно в полном размере; в Библиотеку кадры добавляются кнопкой. FLUX.2 рисует кадр за секунды, Qwen 2.1 заметно дольше (минуты на кадр).</div></div>
          <div class="row" style="margin-bottom:12px"><div class="row-l">Модель</div><div class="seg qw-eng" style="grid-template-columns:repeat(3,1fr)"><button data-e="qwen">Qwen 2.1<small>для творчества, не для коммерции</small></button><button data-e="flux">FLUX.2 klein<small>можно в коммерцию</small></button><button data-e="h3img">MiniMax-H3-Image<small>крупно и по картинкам</small></button></div>
            <div class="hint qw-fluxhint" hidden></div></div>
          <div class="qw-ptxt">
          <textarea class="qw-prompt" placeholder="Что изменить и что оставить. Например: Этот человек сидит в кафе у окна и пьёт кофе. Лицо, причёска и одежда как на изображении."></textarea>
          <div class="ta-foot"><span class="hint qw-tahint">Скажите и что поменять, и что оставить прежним — так результат точнее</span></div>
        </div>
          <div class="row qw-editonly qw-asprow" style="margin-top:12px"><div class="row-l">Формат кадра <span class="hint qw-asphint">по умолчанию как у вашего изображения</span></div><div class="chips qw-asp"></div></div>
          <div class="note qw-outlora" hidden style="margin-top:10px"><span>Края получаются аккуратнее с LoRA «Расширение изображения» (159 МБ): она ждёт в разделе <b>Модели</b>. Пока её нет, программа дорисует края по-старому — тоже аккуратно, но проще.</span></div>
          <div class="qw-szwrap" style="margin-top:12px">${SZ_ROW}</div>
          <div class="row qw-editonly qw-sizerow" style="margin-top:12px"><div class="row-l">Размер результата</div><div class="seg qw-size" style="grid-template-columns:1fr 1fr"><button data-s="1">Обычный<small>~1 МП, быстро</small></button><button data-s="2">Крупный<small>~2 МП, дольше</small></button></div></div></div>
        <div class="row qw-mprow" hidden style="margin-top:12px"><div class="row-l">Размер результата</div>
          <div class="seg qw-mp" style="grid-template-columns:repeat(3,1fr)"><button data-mp="4">4 МП<small>быстрее, видно зерно</small></button><button data-mp="8">8 МП<small>чисто</small></button><button data-mp="16">16 МП<small>самое чистое, дольше</small></button></div>
          <div class="hint">MiniMax H3 Image рассчитан на 4–16 МП: ниже 8 МП заметно зерно. Площадь листа или кадра делится между панелями.</div></div>
        <div class="qw-sheet" style="margin-top:14px" hidden>
          <div class="note"><span>Нужен персонаж на изображении (лучше в полный рост, но портрет тоже подойдёт). Потом лист можно взять как референс в других разделах.</span></div>
          <div class="row"><div class="row-l">Сколько видов</div><div class="seg qw-pn" style="grid-template-columns:1fr 1fr"><button data-p="4">4 панели<small>полный рост спереди и сзади, два портрета</small></button><button data-p="6">6 панелей<small>полный рост спереди, в 3/4, сбоку, сзади + 2 портрета</small></button></div></div>
          <div class="row"><div class="row-l">Первая панель</div><div class="seg qw-head" style="grid-template-columns:1fr 1fr"><button data-h="1">Без головы<small>видна вся одежда</small></button><button data-h="0">С головой<small>для животных — так</small></button></div></div>
          <div class="row"><div class="row-l">Фон</div><div class="seg qw-bg" style="grid-template-columns:repeat(3,1fr)"><button data-b="grey">Серый</button><button data-b="white">Белый</button><button data-b="black">Чёрный</button></div></div>
          <div class="row"><div class="row-l">Особые приметы <span class="hint">необязательно</span></div><div class="path on"><input class="qw-marks" spellcheck="false" placeholder="например: жёлтые очки, шрам на брови"></div></div></div>
        ${imgSeedBox()}
        <div class="hint" style="margin-top:10px">Модель Qwen Image 2.1 — только для некоммерческого использования (лицензия Qwen Research). <b>MiniMax-H3-Image</b> (Powered by MiniMax-H3-Image) — открытая community-лицензия: коммерческое использование до 1 млн $ годовой выручки (иначе нужно отдельное разрешение MiniMax), в ЕС, Великобритании, Южной Корее и США модель не лицензирована. Негативного промпта у неё нет, а картинки называются <b>&lt;Picture 1&gt;</b>…<b>&lt;Picture 5&gt;</b>, и первая задаёт форму результата.</div></div>
    </div><div class="preview qw-prev"></div></div>`;
  gridResize($('.grid', root));
  const drop = $('#qwDrop');
  wireDrop(drop, () => {});
  const drop2 = $('#qwDrop2'), drop3 = $('#qwDrop3'), drop4 = $('#qwDrop4'), drop5 = $('#qwDrop5');
  const DROPS = [drop, drop2, drop3, drop4, drop5];
  szWire($('.qw-szwrap', root), S, () => upd(), () => IMG_SIZES[S.aspect] || [1376, 768],
         () => (S.engine === 'h3img' ? H3IMG_LIM : null));
  DROPS.slice(1).forEach(d => wireDrop(d, () => {}));
  wireSlotSwap(DROPS);
  $('#qwClrAll', root).onclick = () => clearSlots(DROPS);
  const P = imgPanel($('.qw-prev', root), {
    mode: 'qwen', url: '/api/generate/qwen',
    promptText: () => S.kind === 'sheet' ? 'Лист персонажа' : S.kind === 'bg' ? 'Убрать фон' : $('.qw-prompt', root).value.trim(),
    validate: () => !drop._file && !(S.kind === 'story' && S.frames) ? 'Загрузите изображение'
      : S.kind === 'outpaint' && !S.aspectOut ? 'Выберите, до какого формата расширить'
      : S.kind === 'edit' && !$('.qw-prompt', root).value.trim() ? 'Напишите, что изменить на изображении'
      : S.kind === 'story' && !$('.qw-prompt', root).value.trim() ? 'Опишите историю по кадрам' : '',
    form: async () => {
      const fd = new FormData(); fd.append('kind', S.kind); fd.append('preview', '1');   // the Library waits for a button
      if (S.kind === 'outpaint') {   // the picture is placed on the larger canvas by the client
        await outLora();             // the fill of the canvas depends on the LoRA the server will find
        const c = outCanvas();
        fd.append('image', c.file); fd.append('orig', c.orig);
        fd.append('width', c.w); fd.append('height', c.h); fd.append('x', c.x); fd.append('y', c.y);
        fd.append('ow', c.ow); fd.append('oh', c.oh);
        fd.append('prompt', $('.qw-prompt', root).value.trim());
        imgSeedForm(S, fd); return fd;
      }
      if (drop._file) fd.append('image', drop._file);
      if (S.kind === 'bg') { fd.append('size', S.size); imgSeedForm(S, fd); return fd; }
      if (S.kind === 'story') { fd.append('frames', S.frames ? '1' : '0'); fd.append('line', S.line); }
      // the sheet runs on Qwen or on MiniMax H3 Image, never on FLUX.2; the area in MP is H3 Image's own count
      const h3 = S.engine === 'h3img';
      fd.append('engine', S.kind === 'sheet' ? (h3 ? 'h3img' : 'qwen') : S.engine);
      if (h3) fd.append('size', S.mp);
      if (S.kind !== 'sheet' && S.cw && S.ch) { fd.append('width', S.cw); fd.append('height', S.ch); }
      if (S.kind !== 'sheet') DROPS.slice(1).forEach((d, i) => { if (d._file) fd.append('image' + (i + 2), d._file); });
      if (S.kind === 'sheet') { fd.append('panels', S.panels); fd.append('headless', S.headless ? '1' : '0'); fd.append('background', S.bg); fd.append('marks', $('.qw-marks', root).value.trim()); }
      else if (S.kind !== 'bg') { fd.append('prompt', $('.qw-prompt', root).value.trim()); if (!h3) fd.append('size', S.size); fd.append('aspect', S.aspect); }
      imgSeedForm(S, fd); return fd;
    },
    // «Применить изменения»: the result becomes the picture to edit and the work goes on with it
    apply: async it => {
      await setDropFile($('input[type=file]', drop), await libToFile(it));
      return 'Изменения применены: картинка стала исходной. Можно продолжать редактировать — или добавить этот результат в Библиотеку.';
    },
  });
  $('.qw-asp', root).innerHTML = `<button class="chip" data-qa="">Как у изображения</button>` + IMG_ASPECTS.map(([id, name, w, h]) => { const k = 18 / Math.max(w, h);
    return `<button class="chip" data-qa="${id}"><i class="ar" style="width:${w * k}px;height:${h * k}px"></i>${name}${name !== id ? ` <small>${id}</small>` : ''}</button>`; }).join('');
  const outCanvas = () => {   // the source placed on the larger canvas of the chosen aspect (centered)
    const img = $('img', drop), iw = img.naturalWidth, ih = img.naturalHeight;
    const asp = IMG_ASPECTS.find(a => a[0] === S.aspectOut) || IMG_ASPECTS[0];
    // the smallest canvas of the chosen shape that holds the whole picture at its own size (scale 1)
    const r = asp[2] / asp[3];
    let W = Math.max(iw, ih * r), H = W / r;
    // too large (long side over 2048 or over ~2 megapixels): everything is scaled down together; too small: enlarged
    let s = Math.min(1, 2048 / Math.max(W, H), Math.sqrt(2.0e6 / (W * H)));   // the model sees the canvas at its own size
    if (Math.min(W, H) * s < 384) s = 384 / Math.min(W, H);
    W *= s; H *= s;
    const dw = Math.max(1, Math.round(iw * s)), dh = Math.max(1, Math.round(ih * s));
    // the canvas and the place of the picture sit on a 32-pixel grid, so that what the model draws lines up with the original
    const w = Math.max(Math.ceil(W / 32) * 32, Math.ceil(dw / 32) * 32), h = Math.max(Math.ceil(H / 32) * 32, Math.ceil(dh / 32) * 32);
    const x = Math.max(0, Math.floor((w - dw) / 2 / 32) * 32), y = Math.max(0, Math.floor((h - dh) / 2 / 32) * 32);
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    const cx = c.getContext('2d');
    if (loraOn) {
      // the new edges are a FLAT GRAY (#808080): this is the fill the outpaint LoRA was trained on («replace the solid
      // gray areas with a continuation of the scene»). A blurred copy of the picture there made the model leave it as it was.
      cx.fillStyle = '#808080'; cx.fillRect(0, 0, w, h);
    } else {   // no LoRA yet: the old look (a blurred copy of the picture behind it), which needs no special fill
      cx.filter = 'blur(24px)'; cx.drawImage(img, x, y, dw, dh); cx.filter = 'none';
    }
    cx.drawImage(img, x, y, dw, dh);
    const o = document.createElement('canvas'); o.width = dw; o.height = dh;          // the untouched original at its place
    o.getContext('2d').drawImage(img, 0, 0, dw, dh);
    const png = cv => { const b = atob(cv.toDataURL('image/png').split(',')[1]), a = new Uint8Array(b.length);
      for (let i = 0; i < b.length; i++) a[i] = b.charCodeAt(i); return new File([a], 'out.png', { type: 'image/png' }); };
    return { file: png(c), orig: png(o), w, h, x, y, ow: dw, oh: dh };
  };
  // is the outpaint LoRA installed? The fill of the canvas and the instruction sent to the server depend on it
  let loraOn = null;
  const outLora = async () => {
    if (loraOn !== null) return loraOn;
    try {
      const st = await apiGet('/api/status');
      const m = (st.models || []).find(x => x.role === 'outpaint_lora');
      if (m) { loraOn = !!m.have; $('.qw-outlora', root).hidden = loraOn; }
    } catch {}
    return loraOn;
  };
  const upd = () => {
    const isBg = S.kind === 'bg', isOut = S.kind === 'outpaint', h3 = S.engine === 'h3img';
    const fewSlots = S.kind === 'sheet' || isBg || isOut;   // one picture only
    $$('.qw-asp .chip', root).forEach(c => { c.classList.toggle('on', isOut ? c.dataset.qa === S.aspectOut : c.dataset.qa === S.aspect); c.hidden = isOut && !c.dataset.qa; });
    $$('.qw-kind button', root).forEach(b => b.classList.toggle('on', b.dataset.k === S.kind));
    $('.qw-edit', root).hidden = S.kind === 'sheet'; $('.qw-sheet', root).hidden = S.kind !== 'sheet';
    $$('.qw-s2, .qw-s3', root).forEach(x => x.hidden = fewSlots);
    // the 4th and 5th pictures exist only for MiniMax H3 Image: the other models take three at most
    $$('.qw-s4, .qw-s5', root).forEach(x => x.hidden = fewSlots || !h3);
    $('.qw-slots', root).classList.toggle('one', fewSlots);
    $('.qw-slots', root).classList.toggle('five', h3 && !fewSlots);
    $('.qw-morehint', root).hidden = fewSlots;
    $('.qw-morehint', root).innerHTML = h3
      ? `${S.kind === 'story' ? 'Раскадровка' : 'Правка'}: картинки в тексте называются <b>&lt;Picture 1&gt;</b>…<b>&lt;Picture 5&gt;</b>; <b>первая задаёт форму результата</b> (или возьмите формат ниже). Первая — то, что правим, остальные — герой, место, стиль.`
      : S.kind === 'story'
        ? 'Раскадровка: 1-е изображение — герой (или лист персонажа), 2-я — локация, 3-я — по желанию. В тексте они называются <b>&lt;image1&gt;</b>, <b>&lt;image2&gt;</b>, <b>&lt;image3&gt;</b> (для FLUX.2: «image 1», «image 2»).'
        : 'Референсы (по желанию): 1-е изображение — то, что меняем, 2-я и 3-я — например, герой, предмет или одежда. В тексте они называются <b>&lt;image2&gt;</b>, <b>&lt;image3&gt;</b> (для FLUX.2: «image 2», «image 3»).';
    $('.qw-storyopts', root).hidden = S.kind !== 'story';
    $('.qw-lnrow', root).hidden = !S.frames;
    $$('.qw-fr button', root).forEach(b => b.classList.toggle('on', (b.dataset.fr === '1') === S.frames));
    $$('.qw-ln button', root).forEach(b => b.classList.toggle('on', b.dataset.ln === S.line));
    $('.qw-ptxt', root).hidden = isBg;
    $('.qw-eng', root).closest('.row').hidden = isBg || isOut;   // these two run on Qwen 2.1 only
    $('.qw-asprow', root).hidden = S.kind === 'story' || isBg || (S.engine === 'flux' && !isOut);
    $('.qw-asphint', root).textContent = isOut ? 'картинка останется целиком, края дорисуются до этого формата'
      : h3 ? 'необязательно: без него форму задаёт первая картинка' : 'по умолчанию как у вашего изображения';
    $('.qw-size', root).closest('.row').hidden = isOut || h3;   // 1/2 МП — счёт Qwen; у H3 Image своя площадь в МП
    $('.qw-mprow', root).hidden = !h3 || isBg || isOut;
    $$('.qw-mp button', root).forEach(b => b.classList.toggle('on', b.dataset.mp === S.mp));
    $$('.sz-p', root).forEach(b => b.hidden = h3);
    $('.qw-szwrap', root).hidden = isOut || isBg;
    // the outpaint LoRA wants a description of the scene, not an instruction: the hint under the field says so
    $('.qw-tahint', root).textContent = isOut
      ? 'Опишите сцену: что уже есть в кадре и что естественно продолжается по краям — без слов «расширь» и «дорисуй».'
      : 'Скажите и что поменять, и что оставить прежним — так результат точнее';
    if (isOut) outLora();   // the fill of the canvas depends on it: ask while the user is still choosing the format
    $('.qw-outlora', root).hidden = !isOut || loraOn === true;
    $$('.qw-editonly', root).forEach(r => { if (!r.classList.contains('qw-asprow') && !r.classList.contains('qw-sizerow')) r.hidden = S.kind === 'story' || isOut || (S.engine === 'flux' && r.contains($('.qw-asp', root))); });
    $('.qw-prompt', root).placeholder = isOut ? 'Что на картинке и что продолжается по краям. Например: светлая комната с белыми стенами и деревянным полом; комната продолжается вправо и влево, тот же дневной свет. Можно оставить пустым.'
      : S.kind === 'story' && S.frames ? (h3 ? '1: первый кадр по-английски...\n2: второй кадр...\n(героя и место называйте <Picture 1>, <Picture 2>). Или попросите Агента (Раскадровка → По кадрам).'
        : '1: первый кадр по-английски...\n2: второй кадр...\n(героя и место называйте image 1, image 2). Или попросите Агента (Раскадровка → По кадрам).')
      : S.kind === 'story' ? (h3 ? 'Расскажите историю по кадрам: первый кадр — …, второй кадр — …. Героя и место называйте <Picture 1>, <Picture 2>. Или попросите Агента (Раскадровка) написать промпт.'
        : 'Расскажите историю по кадрам: первый кадр — …, второй кадр — …. Героя и место называйте <image1>, <image2>. Или попросите Агента (Раскадровка) написать промпт.')
      : 'Что изменить и что оставить. Например: Этот человек сидит в кафе у окна и пьёт кофе. Лицо, причёска и одежда как на изображении.';
    $$('.qw-size button', root).forEach(b => b.classList.toggle('on', b.dataset.s === S.size));
    $$('.qw-head button', root).forEach(b => b.classList.toggle('on', (b.dataset.h === '1') === S.headless));
    $$('.qw-bg button', root).forEach(b => b.classList.toggle('on', b.dataset.b === S.bg));
    $$('.qw-eng button', root).forEach(b => b.classList.toggle('on', b.dataset.e === S.engine));
    $('.mode-pill span', root).textContent = { qwen: 'Qwen Image 2.1', flux: 'FLUX.2 klein',
      h3img: `MiniMax-H3-Image · ${S.mp} МП` }[S.engine];
    $$('.qw-pn button', root).forEach(b => b.classList.toggle('on', +b.dataset.p === S.panels));
    if (S.kind === 'sheet') {
      const sz = h3 ? h3Size(S.panels === 6 ? '11:5' : '16:9', S.mp) : (S.panels === 6 ? [2112, 960] : [1920, 1088]);
      P.ratio(sz[0], sz[1]);
    }
    else if (S.kind === 'story') P.ratio(...(h3 ? h3Size('16:9', Math.min(+h3Mp(S.mp), S.frames ? 8 : 16)) : [1920, 1088]));
    if (S.kind !== 'sheet' && S.cw && S.ch) P.ratio(S.cw, S.ch);
  };
  /* One task per screen: a card of «Редактирование изображений» opens this page with the task already chosen.
     The switch of tasks and the general title then step aside; «← Все варианты редактирования» leads back. */
  const head = $('.head h1', root), headSub = $('.head p', root);
  const stepH = $$('.card-h h3', root).find(h => /Что делаем/.test(h.textContent)) || null;
  const HEAD0 = { h1: head.textContent, sub: headSub.textContent, step: stepH ? stepH.innerHTML : '' };
  const TASK_HEAD = {
    edit: ['Править по описанию', 'Измените на изображении одежду, цвет, место или детали словами. Всё, что не названо, остаётся прежним'],
    bg: ['Убрать фон', 'Объект остаётся на месте, фон становится чисто белым'],
    outpaint: ['Расширение', 'Достройте изображение до другого формата: программа дорисует края, а сам кадр останется целым'],
    sheet: ['Лист персонажа', 'Лист из 4 или 6 видов героя с разных сторон — из одного изображения'],
    story: ['Раскадровка', 'История в кадрах по изображениям героя и места: одним листом или по кадрам'],
  };
  root._solo = id => {
    if (!TASK_HEAD[id]) return;
    S.kind = id; root.classList.add('solo');
    head.textContent = TASK_HEAD[id][0]; headSub.textContent = TASK_HEAD[id][1];
    if (stepH) stepH.innerHTML = `<span class="n">2</span>${TASK_HEAD[id][0]}`;
    upd();
  };
  root._plain = () => {   // back to the whole editor (opened from the Library, the queue or «Использовать в…»)
    if (!root.classList.contains('solo')) return;
    root.classList.remove('solo');
    head.textContent = HEAD0.h1; headSub.textContent = HEAD0.sub;
    if (stepH) stepH.innerHTML = HEAD0.step;
  };
  // the default area of MiniMax H3 Image: a sheet or a whole storyboard is big, a single edit is 8 MP
  const h3DefaultMp = () => (S.kind === 'sheet' || (S.kind === 'story' && !S.frames) ? '16' : '8');
  root.addEventListener('click', e => {
    const fr = e.target.closest('.qw-fr button'), ln = e.target.closest('.qw-ln button');
    if (fr) { S.frames = fr.dataset.fr === '1'; if (S.engine === 'h3img') S.mp = h3DefaultMp(); upd(); return; }
    if (ln) { S.line = ln.dataset.ln; upd(); return; }
    const qa = e.target.closest('.qw-asp .chip'); if (qa) { if (S.kind === 'outpaint') S.aspectOut = qa.dataset.qa; else S.aspect = qa.dataset.qa; upd(); return; }
    const mpb = e.target.closest('.qw-mp button');
    if (mpb) { S.mp = mpb.dataset.mp; upd(); return; }
    const k = e.target.closest('.qw-kind button'), s = e.target.closest('.qw-size button'), h = e.target.closest('.qw-head button'), b = e.target.closest('.qw-bg button'), pn = e.target.closest('.qw-pn button'), en = e.target.closest('.qw-eng button');
    if (en) { S.engine = en.dataset.e; if (S.engine === 'h3img') S.mp = h3DefaultMp(); upd(); qwModelCheck(); return; }
    if (pn) S.panels = +pn.dataset.p; else if (k) { S.kind = k.dataset.k; if (S.engine === 'h3img') S.mp = h3DefaultMp(); } else if (s) S.size = s.dataset.s; else if (h) S.headless = h.dataset.h === '1'; else if (b) S.bg = b.dataset.b; else return;
    upd();
  });
  /* Is the chosen picture model installed? FLUX.2 and MiniMax H3 Image are separate modes with their own files, and
     H3 Image needs ComfyUI 0.45 (the node MiniMaxH3ImageConditioning appeared there). Qwen is the section's own model. */
  async function qwModelCheck() {
    const hint = $('.qw-fluxhint', root); hint.hidden = true;
    if (S.engine === 'qwen') return;
    const role = S.engine === 'flux' ? 'flux' : 'h3img';
    try {
      const st = await apiGet('/api/status');
      const miss = (st.models || []).filter(m => m.role.startsWith(role) && !m.have);
      const old = role === 'h3img' && comfyTooOld(st.comfy_version);
      if (!(st.modes || []).includes(role) || miss.length || old) {
        hint.hidden = false;
        hint.textContent = old
          ? `Нужен ComfyUI 0.45 или новее: в ${st.comfy_version || 'этой версии'} нет ноды MiniMaxH3ImageConditioning. Обновите ComfyUI (в портативной сборке — update\\update_comfyui.bat), потом перезапустите программу.`
          : role === 'flux'
            ? (st.engine === 'managed'
              ? 'Для этой модели нужны ещё три файла (около 12 ГБ). Откройте «Модели» и нажмите «Добавить Правка изображения: FLUX.2».'
              : 'Положите в папки моделей вашего ComfyUI: flux-2-klein-4b-fp8.safetensors (diffusion_models), qwen_3_4b.safetensors (text_encoders) и flux2-vae.safetensors (vae). Потом обновите раздел «Модели».')
            : (st.engine === 'managed'
              ? 'Для MiniMax H3 Image нужны ещё файлы (около 14 ГБ: модель и VAE; текстовый энкодер у программы уже есть). Откройте «Модели» и нажмите «Добавить Изображения MiniMax H3 Image».'
              : 'Положите в папки моделей вашего ComfyUI: minimax_h3_image_pruned_w4a8.safetensors (diffusion_models), minimax_h3_image_vae_fp16.safetensors (vae) и энкодер qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors (text_encoders). Потом обновите раздел «Модели». Для этой модели нужен ComfyUI 0.45 или новее.');
      }
    } catch {}
  }
  imgSeedWire(root, S); upd();
  buildQwenFit(scope);
  // An outpaint prompt is kept as the whole instruction (the LoRA's own sentence + «Scene: …»): the field shows only
  // what the user wrote, or the server would put the instruction in front of it a second time on the next run.
  const outText = s => {
    const t = String(s || '').trim();
    if (/^Outpaint the image/i.test(t)) { const i = t.indexOf('Scene: '); return i >= 0 ? t.slice(i + 7).trim() : ''; }
    if (/^Extend and continue this picture/i.test(t)) return '';
    return t;
  };
  IMG.qwen = { fill: it => {
    if (it.engine) S.engine = it.engine;
    if (it.frames !== undefined) S.frames = !!it.frames;
    const sub = String(it.sub || '');
    if (sub.startsWith('Раскадровка по кадрам')) S.frames = true; else if (sub.startsWith('Раскадровка')) S.frames = false;
    if (it.kind === 'story' || sub.startsWith('Раскадровка')) { S.kind = 'story'; $('.qw-prompt', root).value = it.prompt || ''; }
    else if (sub === 'Лист персонажа') S.kind = 'sheet';
    else if (sub === 'Убрать фон') S.kind = 'bg';
    else if (sub.startsWith('Расширение')) { S.kind = 'outpaint'; $('.qw-prompt', root).value = outText(it.prompt); }
    else { S.kind = 'edit'; $('.qw-prompt', root).value = it.prompt || ''; }
    if (S.engine === 'h3img') S.mp = h3Mp(it.mp || (/^\s*(\d+)\s*МП/i.exec(it.q || '') || [])[1]);
    upd();
  } };
  // задание из очереди: что делали (правка, лист, раскадровка), по какому промпту, модель, формат и seed
  IMG.qwen.fillJob = m => {
    const sub = String(m.sub || '');
    // the model the job was made with: the meta says it, an older record only by the words of `sub`
    const engine = m.engine === 'h3img' || /MiniMax H3 Image/i.test(sub) ? 'h3img' : /FLUX\.2/i.test(sub) ? 'flux' : 'qwen';
    IMG.qwen.fill({ ...m, engine });
    if (m.aspect && IMG_SIZES[m.aspect]) S.aspect = m.aspect;
    imgJobSeed(root, S, m.seed);
    upd();
  };
}

/* "Правка картинки": a grip under the picture slots changes their height (like the first/last frames).
   With no height of the user's own, the slots start small enough for the whole page to fit the window. */
function buildQwenFit(scope) {
  const root = document.documentElement, KEY = 'h3.qwh', bar = $('#qwResize');
  const clampH = v => Math.round(Math.min(Math.max(v, 60), 420));
  const own = () => { try { return +localStorage.getItem(KEY) >= 60; } catch { return false; } };
  try { const v = +localStorage.getItem(KEY); if (v >= 60) root.style.setProperty('--qw-h', v + 'px'); } catch {}
  // shrink the slots by exactly the overflow of the page, down to the minimum
  const fit = () => {
    if (own()) return;
    root.style.removeProperty('--qw-h');
    requestAnimationFrame(() => {
      if (!$('#view-qwen').classList.contains('active')) return;
      const main = $('.main'), over = main.scrollHeight - main.clientHeight;
      if (over > 0) root.style.setProperty('--qw-h', clampH($('#qwDrop').getBoundingClientRect().height - over) + 'px');
    });
  };
  bar.addEventListener('pointerdown', e => { e.preventDefault(); bar.setPointerCapture(e.pointerId); bar.classList.add('drag'); });
  bar.addEventListener('pointermove', e => {
    if (!bar.hasPointerCapture(e.pointerId)) return;
    root.style.setProperty('--qw-h', clampH(e.clientY - $('#qwDrop').getBoundingClientRect().top) + 'px');
  });
  bar.addEventListener('pointerup', e => {
    bar.releasePointerCapture(e.pointerId); bar.classList.remove('drag');
    try { localStorage.setItem(KEY, parseInt(root.style.getPropertyValue('--qw-h'))); } catch {}
  });
  bar.addEventListener('dblclick', () => {
    root.style.removeProperty('--qw-h');
    try { localStorage.removeItem(KEY); } catch {}
    fit();
  });
  $('#view-qwen')._fit = fit;   // the page fits its own picture slots when it opens (see the mode's enter)
  scope.listen(window, 'resize', () => { if ($('#view-qwen').classList.contains('active')) fit(); });
}

/* --- Inpaint: redraw only the painted (masked) area, the rest stays pixel-identical (Qwen Image 2.1) --- */
function buildInpaint(scope) {
  const root = $('#view-inpaint'), S = { size: '1' };
  root.innerHTML = `
    <div class="pr-bar"><button class="pr-back" type="button" data-imgedit>← Все варианты редактирования</button></div>
    <div class="head"><div><h1>Инпейнт</h1><p>Закрасьте место на изображении, которое нужно переделать, и опишите, что там должно быть. Всё, что не закрашено, останется без изменений</p></div>
      <div class="mode-pill"><i></i><span>Qwen Image 2.1</span></div></div>
    <div class="grid"><div class="col">
      <div class="card"><div class="card-h"><h3><span class="n">1</span>Изображение и закраска</h3></div>
        <label class="drop" id="ipDrop"><input type="file" accept="image/*"><div class="ph"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="1.6"/><path d="m21 15-5-5L5 21"/></svg><b>Ваше изображение</b><span>перетащите или нажмите</span></div><span class="tag" hidden>Исходная</span><button class="x" type="button">✕</button></label>
        <div class="ip-edit" hidden>
          <div class="ip-stage"><div class="ip-fit"><canvas class="ip-img"></canvas><canvas class="ip-over" tabindex="0"></canvas><canvas class="ip-sel"></canvas><div class="ip-ring" hidden></div></div></div>
          <div class="ip-grip" title="Потяните вверх или вниз — высота холста"></div>
          <div class="ip-tools">
            <button class="btn ip-fullb" type="button" title="Редактор на всё окно (F). Выход — Esc или эта же кнопка">На весь экран</button>
            <div class="seg ip-mode" style="grid-template-columns:repeat(5,1fr);width:490px"><button data-m="select" class="on">Выделение</button><button data-m="paint">Закрасить</button><button data-m="erase">Ластик</button><button data-m="fill">Заливка</button><button data-m="hand" title="Двигать картинку (удерживайте пробел)">Рука</button></div>
            <div class="seg ip-selshape" style="grid-template-columns:1fr 1fr 1fr;width:300px"><button data-s="rect" class="on">Прямоугольник</button><button data-s="ellipse">Овал</button><button data-s="lasso">Лассо</button></div>
            <span class="hint">Кисть</span><input type="range" class="ip-brush" min="8" max="160" value="36"><b class="ip-brushv">36</b>
            <span class="hint">Холст</span><input type="range" class="ip-zoom" min="20" max="300" value="100"><b class="ip-zoomv">100 %</b>
            <button class="btn ip-fitb" type="button" title="Показать картинку целиком: холст по высоте окна">Вся картинка</button>
            <button class="btn ip-undo" type="button" title="Убрать последний мазок (Ctrl+Z)" disabled>Отменить</button>
            <button class="btn ip-clear" type="button">Очистить закраску</button>
            <button class="btn ip-unsel" type="button" title="Закраска снова на всей картинке" disabled>Снять выделение</button>
          </div>
          <div class="hint" style="margin-top:8px">Клавиши 1–5 выбирают инструмент (1 ещё раз — форма выделения), [ и ] меняют размер кисти и ластика. <b>«Рука»</b> (5) двигает картинку в любую сторону, а <b>пробел</b> включает её, пока зажат, — удобно на любом инструменте. <b>Колесо мыши</b> — масштаб вокруг указателя, «Вся картинка» возвращает картинку целиком и ставит её посередине. «Заливка» заполняет область, обведённую кистью. «Выделение» ограничивает работу выбранной областью: Ctrl добавляет ещё одну, Shift держит ровный круг или квадрат, Alt перетаскивает выделение, Esc снимает. Если не закрашено ничего, в модель уходит само выделение. Ctrl+Z — убрать последний шаг, Shift+Ctrl+Z — вернуть его. <b>«На весь экран» (F)</b> — то же самое на всё окно, Esc возвращает назад.</div>
        </div></div>
      <div class="card"><div class="card-h"><h3><span class="n">2</span>Что должно быть на закрашенном месте</h3></div>
        <textarea class="ip-prompt" placeholder="Например: белая майка вместо красной куртки. Лицо, фон и свет оставить как было."></textarea>
        <div class="ta-foot"><span class="hint">Скажите и что нарисовать, и что оставить прежним — так результат точнее</span></div>
        <div class="row" style="margin-top:12px"><div class="row-l">Размер результата</div>
          <div class="seg ip-q" style="grid-template-columns:1fr 1fr"><button data-s="1">Обычный<small>~1 МП, быстро</small></button><button data-s="2">Крупный<small>~2 МП, дольше</small></button></div></div>
        ${imgSeedBox(S)}
        <div class="hint" style="margin-top:10px">Модель Qwen Image 2.1 — только для некоммерческого использования (лицензия Qwen Research).</div></div>
    </div><div class="preview ip-prev"></div></div>`;
  gridResize($('.grid', root));
  const drop = $('#ipDrop');
  const edit = $('.ip-edit', root), imgC = $('.ip-img', root), overC = $('.ip-over', root);
  const selC = $('.ip-sel', root), sctx = selC.getContext('2d');
  const ictx = imgC.getContext('2d'), octx = overC.getContext('2d');
  let mode = 'select', brush = 36, hasMask = false;
  let isFull = false, spaceHeld = false, panning = null, modeBeforeSpace = null;   // «На весь экран», the held Space and its tool
  overC.style.opacity = .55;   // the strokes are fully opaque in the data, but the picture stays visible under them
  /* the tool cursors are drawn on a 32 px canvas: an eraser and a paint bucket, the way they look in editors.
     The hot spot is taken from the picture itself — the lowest point of the rubber and of the paint drop —
     so it stays right whatever the shapes above look like. */
  const curUrl = (draw, spot, fallback) => {
    const c = document.createElement('canvas'); c.width = c.height = 32;
    const g = c.getContext('2d');
    try {
      draw(g);
      const p = spot ? spot(g) : null;
      return `url(${c.toDataURL('image/png')}) ${p ? p[0] : 16} ${p ? p[1] : 16}, ${fallback}`;
    } catch { return fallback; }
  };
  const lowest = (g, test) => {   // bottom-most (then left-most) pixel of a given colour
    const d = g.getImageData(0, 0, 32, 32).data;
    let best = null;
    for (let y = 31; y >= 0; y--) for (let x = 0; x < 32; x++) {
      const i = (y * 32 + x) * 4;
      if (d[i + 3] > 200 && test(d[i], d[i + 1], d[i + 2]) && (!best || y > best[1] || (y === best[1] && x < best[0]))) best = [x, y];
    }
    return best;
  };
  const roundBox = (g, x, y, w, h, r) => {   // roundRect is new in some engines: a plain box will do there
    g.beginPath();
    if (g.roundRect) g.roundRect(x, y, w, h, r); else g.rect(x, y, w, h);
  };
  const CUR = {
    erase: curUrl(g => {
      g.save(); g.translate(16, 16); g.rotate(-38 * Math.PI / 180); g.translate(-16, -16);
      g.lineJoin = 'round'; g.strokeStyle = '#16171a'; g.lineWidth = 1.9;
      roundBox(g, 3.5, 10, 25, 11.5, 3.2); g.fillStyle = '#ffffff'; g.fill(); g.stroke();
      roundBox(g, 3.5, 10, 9.5, 11.5, 3.2); g.fillStyle = '#f2607f'; g.fill(); g.stroke();
      g.restore();
    }, g => lowest(g, (r, gr, b) => r > 200 && gr < 140 && b > 90 && b < 175), 'cell'),
    fill: curUrl(g => {
      g.save(); g.translate(16, 16); g.rotate(-30 * Math.PI / 180); g.translate(-16, -16);
      g.lineJoin = 'round'; g.strokeStyle = '#16171a'; g.lineWidth = 1.9;
      g.beginPath(); g.moveTo(8.5, 10); g.lineTo(24.5, 10); g.lineTo(21, 24.5); g.lineTo(12, 24.5); g.closePath();
      g.fillStyle = '#f7f9fc'; g.fill(); g.stroke();
      roundBox(g, 6.8, 6.8, 19.4, 6.2, 2.7); g.fillStyle = '#3f8efc'; g.fill(); g.stroke();
      g.restore();
      g.strokeStyle = '#16171a'; g.lineWidth = 1.5;
      g.beginPath(); g.moveTo(24, 16.4); g.lineTo(26.6, 21.4); g.arc(24, 21.4, 2.6, 0, Math.PI); g.closePath();
      g.fillStyle = '#3f8efc'; g.fill(); g.stroke();
    }, g => lowest(g, (r, gr, b) => b > 190 && r < 130), 'copy'),
    move: curUrl(g => {   // the usual four-way arrow: hold Alt to drag the selection
      const p = new Path2D();
      p.rect(14.3, 8.6, 3.4, 14.8);
      p.rect(8.6, 14.3, 14.8, 3.4);
      p.moveTo(16, 2.4); p.lineTo(20.6, 8.6); p.lineTo(11.4, 8.6); p.closePath();
      p.moveTo(16, 29.6); p.lineTo(11.4, 23.4); p.lineTo(20.6, 23.4); p.closePath();
      p.moveTo(2.4, 16); p.lineTo(8.6, 11.4); p.lineTo(8.6, 20.6); p.closePath();
      p.moveTo(29.6, 16); p.lineTo(23.4, 20.6); p.lineTo(23.4, 11.4); p.closePath();
      g.lineJoin = 'round';
      g.fillStyle = '#16171a'; g.strokeStyle = '#16171a'; g.lineWidth = 4.6;   // the dark rim
      g.fill(p); g.stroke(p);
      g.fillStyle = '#ffffff'; g.strokeStyle = '#ffffff'; g.lineWidth = 2.4;   // the white body on top of it
      g.fill(p); g.stroke(p);
    }, null, 'move'),
  };
  let altHeld = false;
  const cursorNow = () => {   // the arrow only where it will actually move something, in any tool
    if (mode === 'hand' || spaceHeld) { overC.style.cursor = panning ? 'grabbing' : 'grab'; return; }   // «Рука» / Space
    if (altHeld && sel) { overC.style.cursor = CUR.move; return; }
    if (hoverPt && (mode === 'paint' || mode === 'erase')) { overC.style.cursor = 'none'; return; }   // the ring is the cursor
    overC.style.cursor = CUR[mode] || 'crosshair';
  };

  /* the ring under the pointer is the brush itself: stroke width is its diameter, so the size is visible
     before the first dab (and after [ or ] without touching the picture). It lives in the page and is placed by
     the mouse coordinates themselves — no parent box, scroll or border can shift it off the pointer. */
  const ring = $('.ip-ring', root);
  let hoverPt = null;   // where the pointer is, in page coordinates
  /* In the app window the page may be scaled (UI zoom, display scaling), and then one unit of "left" is not one
     screen pixel — the ring drifts aside by exactly that factor. So the mapping is measured once, on the live
     element: two known positions, read back as rectangles. Works the same in a browser where the scale is 1. */
  let ringCal = null;
  const ringScale = () => {
    const z = document.documentElement.style.zoom || '1';
    if (ringCal && ringCal.z === z) return ringCal;
    const keepCss = ring.style.cssText, keepHidden = ring.hidden;
    ring.hidden = false;
    ring.style.cssText = 'position:fixed;left:0;top:0;width:0;height:0;border:0;box-shadow:none;transform:none';
    const a = ring.getBoundingClientRect();
    ring.style.left = '100px'; ring.style.top = '100px';
    const b = ring.getBoundingClientRect();
    ring.style.cssText = keepCss; ring.hidden = keepHidden;
    ringCal = { z, x0: a.left, y0: a.top, kx: ((b.left - a.left) / 100) || 1, ky: ((b.top - a.top) / 100) || 1 };
    return ringCal;
  };
  const paintRing = () => {
    const show = hoverPt && !edit.hidden && !altHeld && (mode === 'paint' || mode === 'erase');
    if (!show) { ring.hidden = true; cursorNow(); return; }
    const k = (overC.getBoundingClientRect().width / overC.width) || 1;   // canvas pixels -> screen pixels
    const d = Math.max(4, brush * k);
    const c = ringScale();
    ring.hidden = false;
    ring.style.width = ring.style.height = d + 'px';
    ring.style.left = (hoverPt[0] - c.x0) / c.kx + 'px';   // the ring sits exactly on the point the stroke starts from
    ring.style.top = (hoverPt[1] - c.y0) / c.ky + 'px';
    // and one exact fit on top of it: read back where it landed and shift by whatever is left over
    const r = ring.getBoundingClientRect();
    const dx = hoverPt[0] - (r.left + r.width / 2), dy = hoverPt[1] - (r.top + r.height / 2);
    if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) {
      ring.style.left = parseFloat(ring.style.left) + dx / c.kx + 'px';
      ring.style.top = parseFloat(ring.style.top) + dy / c.ky + 'px';
    }
    cursorNow();
  };
  scope.listen(window, 'resize', () => { ringCal = null; paintRing(); paintSel(); });
  scope.listen(window, 'scroll', () => { if (hoverPt) { hoverPt = null; paintRing(); } }, true);
  scope.listen(document, 'keydown', e => { if (e.key === 'Alt') { altHeld = true; cursorNow(); paintRing(); } });
  scope.listen(document, 'keyup', e => { if (e.key === 'Alt') { altHeld = false; cursorNow(); paintRing(); } });
  scope.listen(window, 'blur', () => { altHeld = false; cursorNow(); });   // Alt+Tab would leave it stuck
  /* The picture is seen through a camera: the stage is a window (it does not scroll), `zoom` says how wide the
     picture is shown in percent of that window, and `pan` where its top-left corner sits. Panning is therefore free
     in every direction — the picture can be pushed past the edges, the way a drawing program moves the canvas — and
     zoom keeps the point under the pointer where it is. Pan and zoom are one CSS transform on `.ip-fit`, so the
     picture itself stays in its own pixels and nothing about the mask changes. */
  const fitEl = $('.ip-fit', root), stageEl = $('.ip-stage', root);
  const zoomIn = $('.ip-zoom', root), zoomV = $('.ip-zoomv', root);
  let zoom = 100, pan = { x: 0, y: 0 };
  const ZOOM_MAX = 300;   // 300 % of the stage's width: for a 4000 px picture in a 1400 px window that is about 1:1
  const applyView = () => {
    fitEl.style.transformOrigin = '0 0';
    fitEl.style.transform = `translate(${pan.x}px, ${pan.y}px) scale(${zoom / 100})`;
  };
  const stagePoint = (clientX, clientY) => {   // client coordinates -> the stage's own box
    const r = stageEl.getBoundingClientRect();
    return { x: clientX - r.left - stageEl.clientLeft, y: clientY - r.top - stageEl.clientTop };
  };
  const baseSize = () => ({ w: fitEl.offsetWidth || 1, h: fitEl.offsetHeight || 1 });   // unscaled layout size
  const centerView = () => {   // the picture in the middle of the window
    const b = baseSize(), s = zoom / 100;
    pan = { x: Math.round((stageEl.clientWidth - b.w * s) / 2), y: Math.round((stageEl.clientHeight - b.h * s) / 2) };
  };
  root._view = () => ({ zoom, pan: { ...pan } });   // what the camera shows (the window check reads it)
  const syncZoomUi = () => { if (zoomIn.value !== String(zoom)) zoomIn.value = zoom; zoomV.textContent = zoom + '%'; };
  const setZoom = (z, keep, anchor) => {
    const next = Math.max(20, Math.min(ZOOM_MAX, Math.round(z)));
    if (anchor && next !== zoom) {   // the piece of the picture under the pointer stays under it
      const p = stagePoint(anchor.clientX, anchor.clientY), k = next / zoom;
      pan.x = p.x - (p.x - pan.x) * k;
      pan.y = p.y - (p.y - pan.y) * k;
    }
    zoom = next;
    syncZoomUi();
    applyView();
    if (!keep) { try { localStorage.setItem('h3-ip-zoom', String(zoom)); } catch {} }
    hoverPt = null; paintRing();   // the picture moved under the pointer: the ring comes back on the next move
    paintSel();                    // ...and the outline of the selection is redrawn at the new on-screen scale
  };
  const fitZoom = () => {   // the biggest view that still shows the whole picture
    if (!drop._file || !imgC.height) return 100;
    const panel = stageEl.clientWidth || 480;
    // on the whole window the stage fills it, so its own height is the room there is; in the card it is the
    // height the user dragged the panel to, or a share of the window
    const fixed = isFull ? stageEl.clientHeight - 2 : (parseFloat(stageEl.style.height) || 0);
    const maxH = fixed > 0 ? fixed - 4 : Math.min(window.innerHeight * .62, 720);
    return Math.max(20, Math.min(100, Math.floor(100 * maxH / (panel * (imgC.height / imgC.width)))));
  };
  const fitView = () => { setZoom(fitZoom(), true); centerView(); applyView(); paintSel(); };
  zoomIn.oninput = e => setZoom(+e.target.value);
  $('.ip-fitb', root).onclick = () => fitView();
  stageEl.addEventListener('wheel', e => {   // the wheel zooms around the pointer, in the card and on the whole window
    e.preventDefault();
    const d = e.deltaY || e.deltaX;   // Windows often sends a horizontal wheel as deltaX
    if (!d) return;
    setZoom(zoom * (d < 0 ? 1.12 : 1 / 1.12), false, { clientX: e.clientX, clientY: e.clientY });
  }, { passive: false });

  /* --- «На весь экран»: the same picture and the same tools, but the editor takes the whole program window. --- */
  const fullB = $('.ip-fullb', root);
  let viewBefore = null;
  const setFull = on => {
    const changed = on !== isFull;
    isFull = on;
    root.classList.toggle('ip-full', on);   // always: a section left in full screen must come back to the card
    fullB.textContent = on ? 'Свернуть' : 'На весь экран';
    fullB.title = on ? 'Вернуться к странице (Esc)' : 'Редактор на всё окно (F)';
    if (!changed) return false;
    if (on) {
      viewBefore = { zoom, pan: { ...pan } };
      fitView();   // the whole picture first: the working area is bigger than the card
    } else {
      spaceHeld = false;
      panning = null;
      if (viewBefore) { zoom = viewBefore.zoom; pan = { ...viewBefore.pan }; syncZoomUi(); applyView(); }
      viewBefore = null;
    }
    hoverPt = null; paintRing(); paintSel();
    if (on) overC.focus({ preventScroll: true });
    return true;
  };
  fullB.onclick = () => setFull(!isFull);
  root._exitFull = () => setFull(false);   // another section opened: the overlay must not stay on top of it
  overC.addEventListener('auxclick', e => { if (e.button === 1) e.preventDefault(); });   // the middle button only pans

  /* the height of the canvas panel: drag the handle under it, the picture scrolls inside */
  const grip = $('.ip-grip', root);
  let gripFrom = null;
  const gripEnd = () => { gripFrom = null; grip.classList.remove('drag'); };
  grip.addEventListener('pointerdown', e => {
    e.preventDefault(); grip.setPointerCapture(e.pointerId); grip.classList.add('drag');
    gripFrom = { y: e.clientY, h: stageEl.getBoundingClientRect().height };
  });
  grip.addEventListener('pointermove', e => {
    if (!gripFrom) return;
    const h = Math.max(160, Math.min(window.innerHeight * .92, gripFrom.h + e.clientY - gripFrom.y));
    stageEl.style.maxHeight = 'none';
    stageEl.style.height = Math.round(h) + 'px';
    try { localStorage.setItem('h3-ip-stage-h', String(Math.round(h))); } catch {}
  });
  grip.addEventListener('pointerup', gripEnd); grip.addEventListener('pointercancel', gripEnd);

  /* --- selection: a rectangle, an ellipse or a lasso, and Shift adds one more area to the same selection.
     While it exists the brush, the eraser and the bucket stay inside it; if nothing was painted at all, the
     selection itself becomes the mask. Its outline lives on a canvas of its own, so it is never sent. --- */
  let sel = null, selKind = 'rect', selMask = null, selDrag = null, selMove = null;
  const selShape = $('.ip-selshape', root), unselB = $('.ip-unsel', root);
  const shapeOf = d => {   // one dragged shape: a path plus its bounding box
    const p = new Path2D();
    if (selKind === 'lasso') {
      if (d.pts.length < 3) return null;
      let x0 = d.pts[0][0], y0 = d.pts[0][1], x1 = x0, y1 = y0;
      p.moveTo(d.pts[0][0], d.pts[0][1]);
      for (let i = 1; i < d.pts.length; i++) {
        const x = d.pts[i][0], y = d.pts[i][1];
        p.lineTo(x, y);
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
      }
      p.closePath();
      return { path: p, box: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } };
    }
    let x1 = d.x1, y1 = d.y1;
    if (d.round) {   // Shift: a square instead of a rectangle, a circle instead of an ellipse
      const dx = x1 - d.x0, dy = y1 - d.y0;
      const m = Math.max(Math.abs(dx), Math.abs(dy));
      x1 = d.x0 + (dx < 0 ? -m : m);
      y1 = d.y0 + (dy < 0 ? -m : m);
    }
    const x = Math.min(d.x0, x1), y = Math.min(d.y0, y1);
    const w = Math.abs(x1 - d.x0), h = Math.abs(y1 - d.y0);
    if (w < 3 || h < 3) return null;
    if (selKind === 'ellipse') p.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2);
    else p.rect(x, y, w, h);
    return { path: p, box: { x, y, w, h } };
  };
  const mergeSel = (a, b) => {   // both shapes as subpaths of one path: they fill and clip as a union
    if (!a) return b;
    const p = new Path2D(); p.addPath(a.path); p.addPath(b.path);
    const x0 = Math.min(a.box.x, b.box.x), y0 = Math.min(a.box.y, b.box.y);
    const x1 = Math.max(a.box.x + a.box.w, b.box.x + b.box.w);
    const y1 = Math.max(a.box.y + a.box.h, b.box.y + b.box.h);
    return { path: p, box: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } };
  };
  /* The outline of the selection is a SCREEN-space line: the canvas holds picture pixels, so a line of 1.4 of them
     would grow into a thick blurry band at 300 % and shrink into a hairline at 20 %. The width, the dashes and their
     offset are therefore divided by the on-screen scale of the canvas, and the line stays the same on screen at any
     zoom — the same trick the brush ring uses. `root._antsWidth` reports what was actually drawn (a test reads it). */
  let antsDrawn = { canvas: 0, screen: 0 };
  root._antsWidth = () => antsDrawn;
  function paintSel() {   // marching ants over the mask (and the shape being dragged)
    sctx.clearRect(0, 0, selC.width, selC.height);
    const drag = selDrag && shapeOf(selDrag);
    if (!sel && !drag) { antsDrawn = { canvas: 0, screen: 0 }; return; }
    const p = new Path2D();
    if (sel) p.addPath(sel.path);
    if (drag) p.addPath(drag.path);
    const k = (selC.getBoundingClientRect().width / selC.width) || 1;   // canvas pixels -> screen pixels
    const w = Math.max(0.5, 1.6 / k);   // 0.5 px is the thinnest that still survives the canvas raster
    sctx.save();
    sctx.lineWidth = w;
    sctx.setLineDash([5 / k, 4 / k]);
    sctx.strokeStyle = 'rgba(0,0,0,.85)';
    sctx.stroke(p);
    sctx.lineDashOffset = 4.5 / k;
    sctx.strokeStyle = 'rgba(255,255,255,.95)';
    sctx.stroke(p);
    sctx.restore();
    antsDrawn = { canvas: w, screen: w * k };
  }
  const selSet = s => { sel = s; selMask = null; unselB.disabled = !s; cursorNow(); paintSel(); };
  const selCommit = s => {   // a change of the selection is a step of the same history as the strokes
    selSet(s);
    undone = [];
    strokes.push({ selStep: true, sel: s });
    undoB.disabled = false;
  };
  const selArray = () => {   // 1 inside the selection, for the bucket
    if (!sel) return null;
    if (selMask) return selMask;
    const t = document.createElement('canvas'); t.width = selC.width; t.height = selC.height;
    const g = t.getContext('2d');
    g.fillStyle = '#fff'; g.fill(sel.path);
    const d = g.getImageData(0, 0, t.width, t.height).data;
    selMask = new Uint8Array(t.width * t.height);
    for (let p = 0; p < selMask.length; p++) if (d[p * 4 + 3] > 127) selMask[p] = 1;
    return selMask;
  };
  unselB.onclick = () => { if (sel) selCommit(null); };
  wireDrop(drop, (ok, img) => {
    if (!ok) { edit.hidden = true; return; }
    const setup = () => {
      let w = img.naturalWidth, h = img.naturalHeight;   // keep the mask light: cap the long side
      const k = Math.min(1, 1600 / Math.max(w, h));
      w = Math.round(w * k); h = Math.round(h * k);
      imgC.width = overC.width = w; imgC.height = overC.height = h;
      selC.width = w; selC.height = h; selSet(null);   // a new picture starts with no selection
      ictx.drawImage(img, 0, 0, w, h);
      octx.clearRect(0, 0, w, h);
      hasMask = false; edit.hidden = false;
      cursorNow();
      strokes = []; undone = []; undoB.disabled = true;   // a new picture starts with an empty mask
      let savedH = 0; try { savedH = +localStorage.getItem('h3-ip-stage-h'); } catch {}
      if (savedH >= 160) { stageEl.style.maxHeight = 'none'; stageEl.style.height = savedH + 'px'; }
      let saved = 0; try { saved = +localStorage.getItem('h3-ip-zoom'); } catch {}
      // a new picture is shown WHOLE: the saved zoom is used only when it is smaller than the fit (a smaller view
      // is fine, a larger one would hide part of the picture behind the edge of the stage), and it starts centered
      const fit0 = fitZoom();
      setZoom(Math.min(saved >= 20 && saved <= ZOOM_MAX ? saved : fit0, fit0), true);
      centerView(); applyView();
    };
    if (img.complete && img.naturalWidth) setup(); else img.onload = setup;
  });
  const modeBtns = $$('.ip-mode button', root), shapeBtns = $$('.ip-selshape button', root);
  const SHAPES = ['rect', 'ellipse', 'lasso'];
  const TOOL_KEYS = { Digit1: 'select', Digit2: 'paint', Digit3: 'erase', Digit4: 'fill', Digit5: 'hand',
                      Numpad1: 'select', Numpad2: 'paint', Numpad3: 'erase', Numpad4: 'fill', Numpad5: 'hand' };
  const setShape = k => {
    selKind = k;
    shapeBtns.forEach(x => x.classList.toggle('on', x.dataset.s === k));
  };
  const cycleShape = () => setShape(SHAPES[(SHAPES.indexOf(selKind) + 1) % SHAPES.length]);
  const setMode = m => {
    mode = m;
    modeBtns.forEach(x => x.classList.toggle('on', x.dataset.m === m));
    selShape.hidden = mode !== 'select';   // the shape choice only makes sense while selecting
    cursorNow();
    paintRing();
  };
  modeBtns.forEach(b => b.onclick = () => setMode(b.dataset.m));
  shapeBtns.forEach(b => b.onclick = () => setShape(b.dataset.s));
  const brushIn = $('.ip-brush', root), brushV = $('.ip-brushv', root);
  const setBrush = v => {   // [ and ] step it by a quarter each time, so both 9 px and 160 px are easy to reach
    brush = Math.max(8, Math.min(160, Math.round(v)));
    if (brushIn.value !== String(brush)) brushIn.value = brush;
    brushV.textContent = brush;
    paintRing();
  };
  brushIn.oninput = e => setBrush(+e.target.value);
  setBrush(brush);

  /* --- strokes and undo: every stroke is kept as a path, so Ctrl+Z drops the whole last stroke (or the whole
     «clear») and the mask is redrawn from scratch — the very same pixels, no per-step snapshots in memory. --- */
  const undoB = $('.ip-undo', root);
  let strokes = [], undone = [];   // what is painted now, and what Ctrl+Z took away — Shift+Ctrl+Z puts it back
  /* bucket fill: the area enclosed by the strokes. It is found on the mask itself, so a fill is just another
     step in the history — Ctrl+Z / Shift+Ctrl+Z work with it exactly like with a stroke. */
  const floodRegion = (px, py, S) => {   // all open pixels connected to the point; nothing is written yet
    const w = overC.width, h = overC.height;
    const sx = Math.floor(px), sy = Math.floor(py);
    if (sx < 0 || sy < 0 || sx >= w || sy >= h) return null;
    const img = octx.getImageData(0, 0, w, h), d = img.data;
    const A = p => d[p * 4 + 3], WALL = 8;
    // a selection is a wall too: the bucket stops at its edge, and the outline may be left open
    const free = p => A(p) <= WALL && (!S || S[p]);
    if (!free(sy * w + sx)) return null;          // on the stroke, or outside the selection: nothing to fill
    const seen = new Uint8Array(w * h), stack = [sy * w + sx];
    let count = 0, border = false;
    while (stack.length) {
      const p = stack.pop();
      if (seen[p] || !free(p)) continue;
      const y = (p / w) | 0;
      let x0 = p - y * w, x1 = x0;
      while (x0 > 0 && !seen[y * w + x0 - 1] && free(y * w + x0 - 1)) x0--;
      while (x1 < w - 1 && !seen[y * w + x1 + 1] && free(y * w + x1 + 1)) x1++;
      for (let x = x0; x <= x1; x++) { seen[y * w + x] = 1; count++; }
      if (y === 0 || y === h - 1 || x0 === 0 || x1 === w - 1) border = true;
      for (const ny of [y - 1, y + 1]) {
        if (ny < 0 || ny >= h) continue;
        let run = false;
        for (let x = x0; x <= x1; x++) {   // one seed per open run of the row above / below
          const k = ny * w + x, open = !seen[k] && free(k);
          if (open && !run) { stack.push(k); run = true; } else if (!open) run = false;
        }
      }
    }
    return { img, seen, count, border };
  };
  const fillAt = (x, y, S) => {
    if (S === undefined) S = selArray();
    const r = floodRegion(x, y, S);
    if (!r || !r.count) return 'empty';
    // without a selection a fill that runs to the edges means the outline is not closed; a selection is the bound itself
    if (!S && r.border && r.count > overC.width * overC.height * 0.5) return 'leak';
    const w = overC.width, h = overC.height, d = r.img.data;
    /* Where two brush strokes touch, their soft edges overlap to about half opacity, and the fill stops short of
       such a seam — it showed as a hairline of the picture inside the painted area. So the fill creeps up to 3 px
       into soft pixels; a solid stroke (alpha 250+) is never crossed, so it cannot leak through the outline. */
    let edge = [];
    for (let p = 0; p < r.seen.length; p++) {
      if (!r.seen[p]) continue;
      const y0 = (p / w) | 0, x0 = p - y0 * w;
      if ((x0 > 0 && !r.seen[p - 1]) || (x0 < w - 1 && !r.seen[p + 1]) ||
          (y0 > 0 && !r.seen[p - w]) || (y0 < h - 1 && !r.seen[p + w])) edge.push(p);
    }
    for (let pass = 0; pass < 3 && edge.length; pass++) {
      const next = [];
      const grow = k => {
        const a = d[k * 4 + 3];
        if (r.seen[k] || a > 250 || a === 0 || (S && !S[k])) return;   // solid stroke, open space or outside the selection
        r.seen[k] = 1; next.push(k);
      };
      for (const p of edge) {
        const y0 = (p / w) | 0, x0 = p - y0 * w;
        if (x0 > 0) grow(p - 1);
        if (x0 < w - 1) grow(p + 1);
        if (y0 > 0) grow(p - w);
        if (y0 < h - 1) grow(p + w);
      }
      edge = next;
    }
    for (let p = 0; p < r.seen.length; p++) if (r.seen[p]) {
      const i = p * 4; d[i] = 232; d[i + 1] = 38; d[i + 2] = 79; d[i + 3] = 255;
    }
    octx.putImageData(r.img, 0, 0);
    return 'filled';
  };
  const withClip = (path, fn) => {   // the selection: the brush and the eraser never paint outside it
    if (!path) { fn(); return; }
    octx.save(); octx.clip(path);
    try { fn(); } finally { octx.restore(); }
  };
  const paintStroke = s => {
    if (s.selStep) { sel = s.sel; selMask = null; unselB.disabled = !sel; return; }   // only the outline moves
    if (s.fill) { fillAt(s.x, s.y, s.mask); return; }
    if (s.clear) { octx.clearRect(0, 0, overC.width, overC.height); return; }
    withClip(s.sel, () => {
      octx.globalCompositeOperation = s.mode === 'erase' ? 'destination-out' : 'source-over';
      octx.fillStyle = octx.strokeStyle = '#e8264f';   // opaque: the stroke's own alpha is the mask the model reads
      octx.lineWidth = s.brush; octx.lineCap = octx.lineJoin = 'round';
      const [x0, y0] = s.pts[0];
      octx.beginPath(); octx.arc(x0, y0, s.brush / 2, 0, 7); octx.fill();
      for (let i = 1; i < s.pts.length; i++) {
        octx.beginPath(); octx.moveTo(s.pts[i - 1][0], s.pts[i - 1][1]); octx.lineTo(s.pts[i][0], s.pts[i][1]); octx.stroke();
      }
    });
  };
  const redraw = () => {
    octx.globalCompositeOperation = 'source-over';
    octx.clearRect(0, 0, overC.width, overC.height);
    sel = null; selMask = null;   // the history is replayed from the very beginning, so the selection starts empty
    strokes.forEach(paintStroke);
    unselB.disabled = !sel;
    paintSel();                   // the ants follow the selection the history has led to
    hasMask = !!bbox();
    undoB.disabled = !strokes.length;
  };
  const undo = () => { const s = strokes.pop(); if (!s) return; undone.push(s); redraw(); };
  const redo = () => { const s = undone.pop(); if (!s) return; strokes.push(s); redraw(); };
  const pos = e => { const r = overC.getBoundingClientRect(); return [(e.clientX - r.left) * overC.width / r.width, (e.clientY - r.top) * overC.height / r.height]; };
  let drawing = false, live = null;
  /* «Рука»: the picture follows the pointer. The drag starts on the STAGE, not on the canvas, so it works on the
     empty area around the picture too; every pointer event of the canvas bubbles up to the stage, and the canvas
     leaves these gestures alone. The middle button does the same at any tool. */
  const handNow = e => mode === 'hand' || spaceHeld || e.button === 1;
  const panStart = e => {
    if (edit.hidden || !handNow(e)) return;
    e.preventDefault();
    try { stageEl.setPointerCapture(e.pointerId); } catch (err) { /* a pointer that is already gone */ }
    panning = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };   // free: no edges to stop at
    cursorNow();
  };
  stageEl.addEventListener('pointerdown', panStart);
  stageEl.addEventListener('pointermove', e => {
    if (!panning) return;
    e.preventDefault();
    pan.x = panning.px + (e.clientX - panning.x);
    pan.y = panning.py + (e.clientY - panning.y);
    applyView();
  });
  const panEnd = () => { if (!panning) return; panning = null; cursorNow(); };
  stageEl.addEventListener('pointerup', panEnd);
  stageEl.addEventListener('pointercancel', panEnd);
  overC.addEventListener('pointerdown', e => {
    if (edit.hidden) return;
    if (handNow(e)) return;                 // the stage starts the pan; the canvas must not paint or select
    e.preventDefault();
    overC.focus({ preventScroll: true });   // the prompt field must let go: Ctrl+Z now belongs to the canvas, not the text
    if (e.altKey && sel) {                  // Alt: drag the whole selection to another place, in any tool
      const [sx, sy] = pos(e);
      try { overC.setPointerCapture(e.pointerId); } catch (err) { /* a pointer that is already gone */ }
      selMove = { x0: sx, y0: sy, orig: sel, moved: false };
      return;
    }
    if (mode === 'select') {                // drag a rectangle, an ellipse or a lasso; Ctrl adds a second area
      const [sx, sy] = pos(e);
      try { overC.setPointerCapture(e.pointerId); } catch (err) { /* a pointer that is already gone */ }
      selDrag = { x0: sx, y0: sy, x1: sx, y1: sy, pts: [[sx, sy]],
                  add: e.ctrlKey || e.metaKey, round: e.shiftKey };
      return;
    }
    if (mode === 'fill') {                  // fill the area the brush has outlined, then remember it as one step
      const [fx, fy] = pos(e);
      const S = selArray();                 // the bucket stops at the edge of the selection
      const res = fillAt(fx, fy, S);
      if (res === 'filled') { undone = []; strokes.push({ fill: true, x: fx, y: fy, mask: S }); undoB.disabled = false; hasMask = true; }
      else if (res === 'leak') toast('Заливка не выполнена: обведите область кистью целиком, без разрывов');
      return;
    }
    try { overC.setPointerCapture(e.pointerId); } catch (err) { /* a pointer that is already gone */ }
    drawing = true;
    const [x, y] = pos(e);
    live = { mode, brush, pts: [[x, y]], sel: sel ? sel.path : null };   // the selection goes into the history too, so a replay is exact
    undone = [];                             // a new stroke drops the redo history
    strokes.push(live); undoB.disabled = false;
    paintStroke(live);
  });
  overC.addEventListener('pointermove', e => {
    hoverPt = [e.clientX, e.clientY]; paintRing();
    if (selMove) {                          // the selection travels with the mouse
      const [x, y] = pos(e);
      const dx = x - selMove.x0, dy = y - selMove.y0;
      if (!dx && !dy) return;
      selMove.moved = true;
      const b = selMove.orig.box;
      const p = new Path2D();
      p.addPath(selMove.orig.path, new DOMMatrix().translate(dx, dy));
      selSet({ path: p, box: { x: b.x + dx, y: b.y + dy, w: b.w, h: b.h } });
      return;
    }
    if (selDrag) {                          // the new shape is drawn straight away, the old one stays until the drop
      const [x, y] = pos(e);
      selDrag.x1 = x; selDrag.y1 = y; selDrag.round = e.shiftKey;   // Shift limits it to a square or a circle
      if (selKind === 'lasso') selDrag.pts.push([x, y]);
      paintSel();
      return;
    }
    if (!drawing || !live) return;
    const [x, y] = pos(e);
    const p = live.pts[live.pts.length - 1];
    if (Math.abs(x - p[0]) < 0.5 && Math.abs(y - p[1]) < 0.5) return;
    live.pts.push([x, y]);
    withClip(live.sel, () => {
      octx.globalCompositeOperation = live.mode === 'erase' ? 'destination-out' : 'source-over';
      octx.lineWidth = live.brush; octx.lineCap = octx.lineJoin = 'round';
      octx.beginPath(); octx.moveTo(p[0], p[1]); octx.lineTo(x, y); octx.stroke();
    });
  });
  const stop = () => {
    if (selMove) {                          // the move is a step of the history too
      const moved = selMove.moved;
      selMove = null;
      if (moved) selCommit(sel);
      return;
    }
    if (selDrag) {                          // a shape too small to mean anything changes nothing
      const s = shapeOf(selDrag), add = selDrag.add;
      selDrag = null;
      if (s) selCommit(add ? mergeSel(sel, s) : s); else paintSel();
      return;
    }
    if (!drawing) return; drawing = false; live = null;
    hasMask = !!bbox();
  };
  overC.addEventListener('pointerup', stop); overC.addEventListener('pointercancel', stop);
  overC.addEventListener('pointerleave', () => { hoverPt = null; paintRing(); });
  undoB.onclick = undo;
  $('.ip-clear', root).onclick = () => { undone = []; strokes.push({ clear: true }); redraw(); };
  scope.listen(document, 'keydown', e => {
    if (!root.classList.contains('active') || edit.hidden) return;
    if (e.key === 'Escape') {
      // Esc drops the selection first; with nothing selected it leaves «На весь экран» (unless a zoom window
      // or a question is on top of the editor)
      const onTop = $('#zoomv')?.hidden !== false && $('#confirm')?.hidden !== false;
      if (sel && onTop) { e.preventDefault(); selCommit(null); return; }
      if (isFull && onTop) { e.preventDefault(); setFull(false); }
      return;
    }
    const t = e.target;   // a real text field keeps its own keys; a slider or a button does not
    const typing = t && (t.tagName === 'TEXTAREA' || t.isContentEditable
      || (t.tagName === 'INPUT' && /^(text|search|email|url|tel|password|number)$/i.test(t.type || 'text')));
    if (typing) return;
    if (!e.ctrlKey && !e.metaKey && !e.altKey) {   // 1–4 pick a tool; 1 again cycles the shape of the selection
      const tool = TOOL_KEYS[e.code];
      if (tool) {
        e.preventDefault();
        if (tool === 'select' && mode === 'select') cycleShape(); else setMode(tool);
        return;
      }
      if (e.code === 'KeyF') {   // the whole window, and back
        e.preventDefault();
        setFull(!isFull);
        return;
      }
      if (e.code === 'Space') {   // hold Space: the hand, in any tool and in both views — release brings the tool back
        e.preventDefault();
        if (!spaceHeld) {
          spaceHeld = true;
          modeBeforeSpace = mode;
          if (mode !== 'hand') setMode('hand');
          else { cursorNow(); paintRing(); }
        }
        return;
      }
      if (e.code === 'BracketLeft' || e.code === 'BracketRight') {   // [ smaller, ] bigger — any keyboard layout
        e.preventDefault();
        setBrush(e.code === 'BracketLeft' ? brush / 1.25 : brush * 1.25);
        return;
      }
    }
    if (!(e.ctrlKey || e.metaKey) || !(e.code === 'KeyZ' || String(e.key).toLowerCase() === 'z')) return;
    e.preventDefault();
    if (e.shiftKey) redo(); else undo();
  });
  scope.listen(document, 'keyup', e => {
    if (e.code !== 'Space' || !spaceHeld) return;
    spaceHeld = false;
    if (mode === 'hand' && modeBeforeSpace) setMode(modeBeforeSpace);   // the held hand lets go: the tool comes back
    modeBeforeSpace = null;
    cursorNow(); paintRing();
  });
  scope.listen(window, 'blur', () => {
    if (spaceHeld) {
      spaceHeld = false;
      if (mode === 'hand' && modeBeforeSpace) setMode(modeBeforeSpace);
      modeBeforeSpace = null;
    }
    panning = null; cursorNow();
  });
  $$('.ip-q button', root).forEach(b => b.onclick = () => { S.size = b.dataset.s; $$('.ip-q button', root).forEach(x => x.classList.toggle('on', x === b)); });
  $('.ip-q button', root).classList.add('on');
  const bbox = () => {   // painted region, canvas coordinates
    const d = octx.getImageData(0, 0, overC.width, overC.height).data;
    let x0 = overC.width, y0 = overC.height, x1 = -1, y1 = -1;
    for (let y = 0; y < overC.height; y++) for (let x = 0; x < overC.width; x++)
      if (d[(y * overC.width + x) * 4 + 3] > 8) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
    return x1 < 0 ? null : { x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1 };
  };
  const pngFile = (canvas, name) => {
    const bin = atob(canvas.toDataURL('image/png').split(',')[1]), arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return new File([arr], name, { type: 'image/png' });
  };
  const prep = () => {   // cut the picture around the painted area (with margin), like proven inpaint workflows
    const painted = bbox();
    if (!painted && !sel) return null;
    /* whole pixels only: a selection arrives as fractional mouse coordinates, and a crop cut at 123.4 px would
       hand the server a size it cannot parse — that is how the picture ended up pasted at the corner */
    const b = painted || {
      x: Math.floor(sel.box.x), y: Math.floor(sel.box.y),
      w: Math.ceil(sel.box.w), h: Math.ceil(sel.box.h),
    };
    const px = Math.max(32, Math.round(b.w * .25)), py = Math.max(32, Math.round(b.h * .25));
    const cx = Math.max(0, Math.round(b.x - px)), cy = Math.max(0, Math.round(b.y - py));
    const cw = Math.min(overC.width - cx, Math.ceil((b.w + px * 2) / 8) * 8), ch = Math.min(overC.height - cy, Math.ceil((b.h + py * 2) / 8) * 8);
    const cropC = document.createElement('canvas'); cropC.width = cw; cropC.height = ch;
    cropC.getContext('2d').drawImage(imgC, cx, cy, cw, ch, 0, 0, cw, ch);
    const maskC = document.createElement('canvas'); maskC.width = cw; maskC.height = ch;   // only the painted strokes
    const mg = maskC.getContext('2d');
    mg.drawImage(overC, cx, cy, cw, ch, 0, 0, cw, ch);
    if (!painted && sel) {   // a selection with no strokes in it: the whole selected area goes to the model
      mg.save(); mg.translate(-cx, -cy);
      mg.fillStyle = '#e8264f'; mg.fill(sel.path);
      mg.restore();
    }
    return { crop: pngFile(cropC, 'crop.png'), mask: pngFile(maskC, 'mask.png'), x: cx, y: cy, w: cw, h: ch };
  };
  imgPanel($('.ip-prev', root), {
    mode: 'qwen', url: '/api/generate/inpaint',
    promptText: () => $('.ip-prompt', root).value.trim(),
    validate: () => !drop._file ? 'Загрузите изображение' : !hasMask && !sel ? 'Закрасьте кисточкой место, которое нужно переделать'
      : !$('.ip-prompt', root).value.trim() ? 'Напишите, что должно быть в закрашенном месте' : '',
    form: () => {
      const p = prep();
      const fd = new FormData();
      fd.append('preview', '1');   // the Library waits for «Добавить в библиотеку»
      fd.append('image', pngFile(imgC, 'image.png'));   // the exact picture the mask coordinates belong to
      fd.append('crop', p.crop); fd.append('mask', p.mask); fd.append('x', p.x); fd.append('y', p.y);
      fd.append('crop_w', p.w); fd.append('crop_h', p.h);   // the model works at ~megapixels, the paste at the real size
      fd.append('prompt', $('.ip-prompt', root).value.trim()); fd.append('size', S.size);
      imgSeedForm(S, fd); return fd;
    },
    // «Применить изменения»: the result becomes the picture on the canvas, the mask is cleared, work goes on
    apply: async it => {
      await setDropFile($('input[type=file]', drop), await libToFile(it));
      return 'Изменения применены: картинка стала исходной, закраска сброшена. Можно продолжать — или добавить результат в Библиотеку.';
    }
  });

  imgSeedWire(root, S);
}
  function pause(selector) {
    window.h3Lifecycle.pauseMedia($(selector));
  }

  function disposePictures(selector) {
    const view = $(selector); if (!view) return;
    pause(selector);
    view.querySelectorAll('img[src^="blob:"]').forEach(img => {
      try { URL.revokeObjectURL(img.src); } catch (e) { /* already gone */ }
    });
    ['.kr-prev', '.qw-prev', '.ip-prev'].forEach(sel => {
      const el = view.querySelector(sel);
      if (el && el._dispose) el._dispose();
    });
  }

  window.h3Modes.register("krea", {
    mount: buildKrea,
    leave: () => pause('#view-krea'),
    dispose: () => disposePictures('#view-krea'),
  });
  window.h3Modes.register("imgedit", {
    mount: buildImgEdit,
  });
  window.h3Modes.register("qwen", {
    mount: buildQwen,
    // opened any other way (the Library, the queue, «Использовать в…»): the whole editor, not one task
    enter: () => { const q = $('#view-qwen'); q._plain?.(); q._fit?.(); },
    leave: () => pause('#view-qwen'),
    dispose: () => disposePictures('#view-qwen'),
  });
  window.h3Modes.register("inpaint", {
    mount: buildInpaint,
    // the editor is never shown over another section, and it always comes back as a card, not as the layer
    enter: () => $('#view-inpaint')._exitFull?.(),
    leave: () => { pause('#view-inpaint'); $('#view-inpaint')._exitFull?.(); },
    dispose: () => disposePictures('#view-inpaint'),
  });
})();
