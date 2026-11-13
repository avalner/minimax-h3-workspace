/* Video: text-to-video, a first and an optional last frame, the model choice and the acceleration.
 *
 * A mode module. The section's markup lives in the page (it is static), the panel and the parameters
 * card are built on the first visit; the state object `V` exists from load, because the saved
 * settings are restored while the page is parsed, before anyone opens the section. The module owns
 * the state, the panel wiring and the model checks; the page asks `window.h3Video` for the few things
 * it needs (the remembered settings, the details window's form, "repeat this clip").
 *
 * Loaded before the page so `h3Video.state` exists during parse; the build waits for `mount`.
 */

(function () {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  const V = { aspect: '16:9', q: '720p', dur: 5, first: false, last: false, unet: 'fast', len: () => V.dur,
              shownId: null };   // the clip in the player: a stage saves it and brings it back
  let ui = null;   // the built panel: null until the section is opened for the first time

  function mount(scope) {
  const vPrev = previewPanel($('#vPreview'), V, {
    extraLabel: 'Режим',
    extra: () => V.first && V.last ? 'Кадр → кадр' : V.first ? 'Изображение → видео' : 'Текст → видео',
    validate: () => !$('#vPrompt').value.trim() ? 'Напишите промпт'
      : V.unet === 'fused' && V.fusedHave === false ? 'Скачайте файл модели Fused Turbo: кнопка «Скачать» под переключателем «Модель»' : '',
    passes: () => [V.unet === 'fused' || V.accel === 'sla' ? 4 : 8],
    target: () => $('#dFirst img'),
    real: {
      url: '/api/generate/video',
      form: () => {
        const fd = new FormData();
        fd.append('prompt', $('#vPrompt').value.trim());
        sizeForm(V, fd); fd.append('aspect', V.aspect); fd.append('quality', V.q); fd.append('duration', V.dur);
        seedForm(V, fd);
        if ($('#dFirst')._file) fd.append('first', $('#dFirst')._file);
        fd.append('unet', V.unet === 'fused' ? 'fused' : '');
        fd.append('accel', V.unet !== 'fused' && V.accel === 'sla' ? 'sla' : '');
        const loop = $('#vLoop').checked && $('#dFirst')._file;
        if (loop) fd.append('loop', '1');
        else if ($('#dFirst')._file && $('#dLast')._file) fd.append('last', $('#dLast')._file);
        return fd;
      },
    },
    meta: () => ({ mode: 'video', prompt: $('#vPrompt').value.trim(), sub: ($('#vLoop').checked && V.first ? 'Зацикленное видео' : V.first && V.last ? 'Кадр → кадр' : V.first ? 'Изображение → видео' : 'Текст → видео') + (V.unet === 'fused' ? ' · Fused Turbo' : '') }),
    onDone: item => addToLib(item),
    onShow: item => { V.shownId = item.id; },
  });
  $('#vLoop').onchange = () => { $('#dLast').style.opacity = $('#vLoop').checked ? .4 : ''; vPrev(); };
  const vUpd = paramsCard($('#vParams'), V, { n: 3, duration: true, onChange: () => { vPrev(); vMode(); },
    extraRow: `<div class="row">
      <div class="row-l">Модель <span class="hint">чем генерировать</span></div>
      <div class="seg v-unet" style="grid-template-columns:1fr 1fr"><button type="button" data-un="fast">FastH3<small>обычная · 8 шагов</small></button><button type="button" data-un="fused">Fused Turbo<small>4 шага · звук ярче</small></button></div>
      <div class="hint v-unhint" hidden></div>
      <div class="hint">Fused Turbo — одна модель на 21 ГБ: LoRA ускорения и сглаживания движения уже внутри, поэтому хватает 4 шагов. Скачивается отдельно, в установку не входит.</div></div>
    <div class="row v-accel-row"><div class="row-l">Ускорение <span class="hint">для слабых видеокарт</span></div>
      <div class="seg v-accel" style="grid-template-columns:1fr 1fr"><button type="button" data-ac="normal">Обычное<small>как раньше</small></button><button type="button" data-ac="sla">Быстрый черновик<small>в 1,3 раза быстрее, качество ниже</small></button></div>
      <div class="hint v-achint" hidden></div><div class="hint" style="margin-top:6px">«Быстрый черновик» нужен для проб: это скорость, а не увеличение размера. Для готового ролика выбирайте «Обычное».</div></div>` });
  function vAccUpd() { $$('.v-accel button').forEach(b => b.classList.toggle('on', (b.dataset.ac === 'sla') === (V.accel === 'sla'))); }
  function vUnetUpd() {
    $$('.v-unet button').forEach(b => b.classList.toggle('on', b.dataset.un === V.unet));
    $('.v-accel-row').hidden = V.unet === 'fused';   // the fused file has its turbo inside: no LoRA acceleration to add
    if (V.unet === 'fused' && V.accel === 'sla') { V.accel = 'normal'; vAccUpd(); }
  }
  async function vAccCheck() {
    const hint = $('.v-achint'); hint.hidden = true; hint.innerHTML = '';
    if (V.accel !== 'sla') return;
    try {
      const st = await apiGet('/api/status');
      const m = (st.models || []).find(x => x.role === 'sla_lora');
      if (m && !m.have) {
        hint.hidden = false;
        hint.innerHTML = `Для быстрого режима нужен файл ускорения (${GB(m.size)}). <button class="btn mini pri" type="button" id="vAccDl">Скачать</button> <span class="hint" id="vAccProg"></span>`;
        $('#vAccDl').onclick = () => { mdStart(['sla_lora']); };
      }
    } catch {}
  }
  async function vFusedCheck() {   // the model is chosen in this section, so it is downloaded from here too («Модели» works as well)
    const hint = $('.v-unhint'); if (!hint) return;
    hint.hidden = true; hint.innerHTML = '';
    if (V.unet !== 'fused') { V.fusedHave = null; return; }
    try {
      const st = await apiGet('/api/status');
      const m = (st.models || []).find(x => x.role === 'fused_unet');
      V.fusedHave = m ? !!m.have : null;
      if (m && !m.have) {
        hint.hidden = false;
        hint.innerHTML = `Файла модели нет в ComfyUI (${GB(m.size)}). <button class="btn mini pri" type="button" id="vFusedDl">Скачать</button> <span class="hint" id="vFusedProg"></span>`;
        $('#vFusedDl').onclick = () => { mdStart(['fused_unet']); };
      }
    } catch {}
  }
  scope.listen($('#vParams'), 'click', e => {
    const b = e.target.closest('.v-accel button');
    if (b) { V.accel = b.dataset.ac; vAccUpd(); vAccCheck(); vPrev(); window.h3ModelRows?.refresh('video'); return; }
    const u = e.target.closest('.v-unet button');
    if (u) { V.unet = u.dataset.un; vUnetUpd(); vFusedCheck(); vPrev(); window.h3ModelRows?.refresh('video'); }
  });
  ui = { update: vUpd, refresh: vPrev, mode: vMode,
         accCheck: vAccCheck, fusedCheck: vFusedCheck,
         unetUpd: vUnetUpd, accUpd: vAccUpd };
  $('#vCount').textContent = $('#vPrompt').value.length + ' символов';   // a prompt restored before the build
  vAccUpd(); vAccCheck(); vUnetUpd(); vFusedCheck();
  function vMode() {
    $('#vMode').textContent = 'Режим: ' + (V.first && V.last ? 'видео между двумя кадрами' : V.first ? 'изображение → видео' : 'видео по тексту');
    $('#dLast').classList.toggle('disabled', !V.first);
    $('#lastHint').textContent = V.first ? 'необязательно' : 'сначала загрузите первый';
  }
  const lastDrop = wireDrop($('#dLast'), on => { V.last = on; vUpd(); });
  // the first frame decides the composition, so the format always follows it
  wireDrop($('#dFirst'), (on, img) => { V.first = on; if (!on) lastDrop.clear(); vUpd(); if (on) fitAspect(V, img, vUpd, true); });
  $('#vPrompt').oninput = e => $('#vCount').textContent = e.target.value.length + ' символов';
  vUpd();
  }

  /* the details window fills this section with the job's settings; the page opens the section first */
  async function edit(meta, files) {
      const loop = /зацикл/i.test(meta.sub || '') || files.image.some(f => /зацикл/i.test(f.role));
      const first = files.image.find(f => /перв/i.test(f.role)) || files.image[0] || null;
      const last = loop ? null : (files.image.find(f => /послед/i.test(f.role)) || (first && files.image.find(f => f !== first)) || null);
      const put = async (zone, f) => {
        if (f) { await jobPut($(zone + ' input'), f.file); return; }
        if ($(zone).classList.contains('filled')) $(zone + ' .x').click();   // этого кадра в задании не было
      };
      await put('#dFirst', first);
      await put('#dLast', last);
      $('#vLoop').checked = loop; $('#vLoop').onchange();
      $('#vPrompt').value = String(meta.prompt || '');
      $('#vPrompt').oninput({ target: $('#vPrompt') });
      if (ASPECTS.some(a => a.id === meta.aspect)) V.aspect = meta.aspect;
      if (QUALITY_ALL.some(q => q.id === meta.q)) V.q = meta.q;
      if (meta.len > 0) V.dur = Math.min(25, Math.max(3, Math.round(meta.len)));
      if (meta.seed != null && meta.seed !== '') useSeed(V, meta.seed);
      const params = jobParams(meta);
      V.unet = params.model;                          // the model the job was made with, not a caption guess
      V.accel = V.unet === 'fast' ? params.acceleration : 'normal';
      ui?.unetUpd(); ui?.accUpd(); ui?.accCheck(); ui?.fusedCheck();
      window.h3ModelRows?.refresh('video');   // the model row shows the acceleration state too
      if (ui) { ui.update(); ui.refresh(); ui.mode(); }
      noticeJobParams(params);
  }

  /* «Повторить» in the viewer: same prompt, format and length, then open the section */
  function repeat(it) {
    Object.assign(V, { aspect: it.aspect, q: it.q, dur: Math.round(it.len) });
    const prompt = $('#vPrompt'); prompt.value = it.prompt || '';
    goTo('video');                       // the section is built on the first visit
    if (ui) {
      $('#vCount').textContent = prompt.value.length + ' символов';
      ui.update(); ui.refresh(); ui.mode();
    }
  }

  /* a model finished downloading: the two hints under the switches may be out of date */
  function modelsChanged() {
    if (!ui) return;
    ui.accCheck(); ui.fusedCheck();
  }

  function update() { if (ui) ui.update(); }
  function refresh() { if (ui) ui.refresh(); }
  /* the clip of a saved stage: the page asks for it when the stage is opened */
  function showItem(id) { return ui && ui.refresh.show ? ui.refresh.show(id) : false; }

  window.h3Video = {
    get state() { return V; },
    update, refresh, edit, repeat, modelsChanged, showItem,
  };

  window.h3Modes.register("video", {
    mount,
    leave: () => {
      ui?.refresh.pause();
      window.h3Lifecycle.pauseMedia($('#view-video'));
    },
    dispose: () => { ui?.refresh.dispose(); },
  });
})();
