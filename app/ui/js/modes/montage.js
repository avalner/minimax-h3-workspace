/* Montage: clips from the Library and the computer are joined in a chosen order.
 *
 * A mode module: it owns the section's markup, its preview player, the FFmpeg poll and the uploaded
 * clips' blob URLs. `h3Modes` builds it on the first visit and gives the resources back when the
 * window closes or the user leaves (the FFmpeg poll and playback stop on leave; the queue keeps
 * joining behind the scenes). Loaded after the page, like the other mode modules.
 */

(function () {
  "use strict";

  let onLeave = () => {};
  let onDispose = () => {};

  function build(scope) {
  const root = $('#view-montage'), MT = { ids: [], cuts: [], drag: null, busy: false, up: {}, n: 0, px: 26, res: false };
  try { MT.px = +localStorage.getItem('h3-mt-px') || 26; } catch {}
  const wOf = d => Math.max(Math.round(MT.px * 2.85), Math.round(d * MT.px));   // block width: the clip length at the chosen scale (short clips keep room for buttons)
   // cuts[i]: seconds the clip is trimmed to (null: whole clip)
  const get = id => id.startsWith('up:') ? MT.up[id.slice(3)] : (byId(id) || draftById(id));   // a Library clip, a draft, or a file from the computer
  root.innerHTML = `
    <div class="head"><div><h1>Монтаж</h1><p>Склейте клипы из Библиотеки в один ролик: добавьте их по порядку, при необходимости поменяйте местами и нажмите «Склеить»</p></div></div>
    <div class="grid"><div class="col">
      <div class="card"><div class="card-h"><h3><span class="n">1</span>Клипы из Библиотеки и черновики</h3><span class="hint">нажмите на клип, чтобы добавить</span><button type="button" class="btn mini mt-pc" title="Добавить видео со своего компьютера">+ С компьютера</button><input type="file" class="mt-file" accept="video/*" multiple hidden></div><div class="mt-lib"></div><div class="hint mt-hid" style="margin-top:8px"></div></div>
      <div class="card"><div class="card-h"><h3><span class="n">2</span>Порядок склейки</h3><span class="counter mt-total"></span><span class="mt-zoom"><button type="button" class="btn mini" data-z="-1" title="Мельче">−</button><button type="button" class="btn mini" data-z="0" title="Уместить всю ленту по ширине">По ширине</button><button type="button" class="btn mini" data-z="1" title="Крупнее">+</button></span></div>
        <div class="mt-strip"></div>
        <div class="mt-vh" title="Потяните вниз или вверх, чтобы клипы стали крупнее или мельче. Двойной щелчок: как было"><i></i></div>
        <div class="hint mt-hint" style="margin-top:8px">Нажмите на клип в ленте, чтобы посмотреть с этого места (справа). Ширина блока равна длине клипа. Порядок меняется перетаскиванием или стрелками. Потяните правый край блока, чтобы сделать клип короче (или вернуть длину). Клипы разного размера приводятся к размеру первого.</div></div>
    </div><div class="preview">
      <div class="screen"><div class="frame" style="aspect-ratio:16/9;width:100%"><div class="res mt-empty"><b>Монтаж</b>добавьте клипы: здесь можно будет посмотреть, как они идут подряд, а потом появится готовый ролик</div><video class="result mt-pv" playsinline hidden></video><video class="result mt-res" controls loop playsinline hidden></video></div></div>
      <div class="mt-bar" hidden><button type="button" class="btn mt-play" title="Играть / пауза (пробел)">▶</button><input type="range" class="mt-seek" min="0" max="0" value="0" title="Перемотка"><span class="mt-time">0:00 / 0:00</span></div>
      <div class="mt-tabs" hidden><button type="button" class="btn mini on" data-pv="seq">Предпросмотр</button><button type="button" class="btn mini" data-pv="res">Готовый ролик</button></div>
      <div class="mt-eng">
        <div class="mt-eng-tabs"><button type="button" class="btn mini on" data-eng="comfy">ComfyUI</button><button type="button" class="btn mini" data-eng="ff" title="Собрать тем же FFmpeg: переходы, формат, титры и музыка за секунды, видеокарта не нужна">FFmpeg (быстро)</button></div>
        <div class="mt-ff" hidden>
          <div class="hint ff-state" style="margin-bottom:10px"></div>
          <div class="ff-grid">
            <label class="fld"><span>Переход</span><select class="ff-transition"><option value="none">без перехода</option><option value="fade" selected>растворение</option><option value="dissolve">наплыв</option><option value="wipeleft">шторка влево</option><option value="wiperight">шторка вправо</option><option value="slideup">скольжение вверх</option><option value="circleopen">круг</option></select></label>
            <label class="fld"><span>Длина перехода: <b class="ff-tdv">0.4</b> с</span><input type="range" class="ff-trans-dur" min="0.1" max="1.5" step="0.1" value="0.4"></label>
            <label class="fld"><span>Формат</span><select class="ff-aspect"><option value="original" selected>как первый клип</option><option value="16:9">16:9</option><option value="9:16">9:16 вертикальный</option><option value="1:1">1:1</option><option value="21:9">21:9</option></select></label>
            <label class="fld"><span>Как вписать в формат</span><select class="ff-fit"><option value="blur" selected>размытый фон</option><option value="crop">обрезать по краям</option><option value="pad">чёрные поля</option></select></label>
            <label class="fld"><span>Скорость</span><select class="ff-speed"><option value="1" selected>1×</option><option value="0.5">0.5×</option><option value="0.75">0.75×</option><option value="1.25">1.25×</option><option value="1.5">1.5×</option><option value="2">2×</option></select></label>
            <label class="fld"><span>Цвет</span><select class="ff-filter"><option value="none" selected>как есть</option><option value="warm">тёплый</option><option value="cold">холодный</option><option value="bw">чёрно-белый</option><option value="vintage">винтаж</option><option value="vignette">виньетка</option></select></label>
            <label class="fld"><span>Надпись</span><input class="ff-title" maxlength="200" placeholder="необязательно"></label>
            <label class="fld"><span>Где надпись</span><select class="ff-title-pos"><option value="bottom" selected>внизу</option><option value="center">по центру</option><option value="top">вверху</option></select></label>
            <label class="fld"><span>Музыка</span><input type="file" class="ff-music" accept="audio/*"></label>
            <label class="fld"><span>Музыка: <b class="ff-mv">35</b>%</span><input type="range" class="ff-mvol" min="0" max="100" step="5" value="35"></label>
            <label class="fld"><span>Звук клипов: <b class="ff-vv">100</b>%</span><input type="range" class="ff-vvol" min="0" max="100" step="5" value="100"></label>
          </div>
          <div class="hint">Сборка идёт на процессоре и видеокарту не занимает: длинная лента собирается за секунды, а переходы и титры накладывает сам FFmpeg.</div>
        </div>
      </div>
      <button class="go mt-go"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4z"/></svg><span class="mt-go-t">Склеить</span></button>
      <div class="mt-upbox"><select class="mt-upeng" title="Чем увеличивать каждый клип"><option value="relay">Быстро (RelayVSR, в 2 раза)</option><option value="seed">Качественно (SeedVR2, в 2 раза, долго)</option></select><button type="button" class="btn mt-upgo" title="Каждый клип увеличивается отдельно (быстро, память не переполняется), потом увеличенные склеиваются. Исходные клипы остаются.">Увеличить каждый клип и склеить</button></div>
      <div class="mt-sets">
        <label class="mt-s-detail"><span><b>Детали: <i class="mt-dv">70</i>%</b> меньше — мягче, без пересвета лиц; больше — чётче</span><input type="range" class="mt-detail" min="0" max="100" step="5" value="70"></label>
        <label class="mt-s-sharp" hidden><span><b>Чёткость: <i class="mt-sv">100</i>%</b> меньше — мягче; 100 — как есть</span><input type="range" class="mt-sharp" min="0" max="100" step="5" value="100"></label>
        <label><span><b>Зерно плёнки: <i class="mt-gv">0</i>%</b> 0 — выключено. Накладывается на каждый клип отдельно, потом клипы склеиваются</span><input type="range" class="mt-grain" min="0" max="100" step="5" value="0"></label>
      </div>
      <div class="mt-pipe" hidden></div>
      <div class="status mt-status"></div>
    </div></div>`;
  const hidden = () => { try { return JSON.parse(localStorage.getItem('h3-mt-hide') || '[]'); } catch { return []; } };
  const hideSet = a => { try { localStorage.setItem('h3-mt-hide', JSON.stringify(a)); } catch {} };
  const fullOf = i => get(MT.ids[i]).len || 5, durOf = i => MT.cuts[i] != null ? MT.cuts[i] : fullOf(i);
  const lenTxt = it => it.len ? it.len.toFixed(1).replace('.0', '') + ' с' : '';
  const onDraft = () => { try { render(); } catch {} };   // a new draft appears in the clip list above at once
  DRAFT_HOOKS.push(onDraft);
  scope.add(() => { const i = DRAFT_HOOKS.indexOf(onDraft); if (i >= 0) DRAFT_HOOKS.splice(i, 1); });
  function render() {
    const keep = MT.ids.map(id => !!get(id) && !get(id).missing); MT.cuts = MT.cuts.filter((_, i) => keep[i]); MT.ids = MT.ids.filter((_, i) => keep[i]);
    const hid = hidden();
    const seen = new Set(), all = [...DRAFTS.filter(it => it.kind !== 'image' && !it.missing && it.url),
                                 ...LIB.filter(it => it.kind !== 'image' && !it.missing && it.url)]
      .filter(it => !seen.has(it.id) && seen.add(it.id));
    const vids = all.filter(it => !hid.includes(it.id)), nh = all.length - vids.length;
    $('.mt-hid', root).innerHTML = nh ? `Убрано из списка: ${nh} · <button type="button" class="a-change" data-showall>Вернуть все</button>` : '';
    $('.mt-lib', root).innerHTML = vids.length ? vids.map(it => `<button type="button" class="mt-card" data-id="${it.id}" title="${esc(it.prompt || '')}"><video src="${it.url}#t=0.5" preload="metadata" muted playsinline></video><span class="mt-plus">+</span><span class="mt-x" data-hide="${it.id}" role="button" title="Убрать из этого списка (в Библиотеке клип останется)">✕</span><span class="mt-len">${lenTxt(it)}</span><small>${esc(it.prompt || MODE_NAME[it.mode] || '')}${it.preview ? ' · черновик' : ''}</small></button>`).join('')
      : '<div class="hint">Видео пока нет: сгенерируйте клипы в любом видеоразделе — готовые появятся здесь.</div>';
    $('.mt-strip', root).innerHTML = MT.ids.length ? MT.ids.map((id, i) => { const it = get(id), d = durOf(i); return `<div class="mt-item" draggable="true" data-i="${i}" style="width:${wOf(d)}px"><video src="${it.url}#t=0.5" preload="metadata" muted playsinline></video><span class="mt-n">${i + 1}</span>${window.mtPipeDone && window.mtPipeDone(id) ? '<span class="mt-ok" title="Клип обработан">✓</span>' : ''}<span class="mt-len">${d.toFixed(1).replace('.0', '')} с${MT.cuts[i] != null ? ' ✂' : ''}</span><div class="mt-b"><button type="button" data-mv="-1" title="Влево">◀</button><button type="button" data-rm title="Убрать">✕</button><button type="button" data-mv="1" title="Вправо">▶</button></div><i class="mt-rh" title="Потяните, чтобы сделать клип короче или длиннее (длиннее оригинала нельзя)"></i></div>`; }).join('')
      : '<div class="hint">Пока пусто: нажмите на клипы сверху.</div>';
    const total = MT.ids.reduce((a, id, i) => a + durOf(i), 0);
    $('.mt-total', root).textContent = MT.ids.length ? `${MT.ids.length} клипов · ≈ ${Math.round(total)} с` : '';
    if (MT.ids.length) $('.mt-strip', root).insertAdjacentHTML('beforeend', '<i class="mt-ph"></i>');
    syncPlayer();
    if (window.mtSaveState) { window.mtSaveState(); window.mtPipePaint(); }
  }

  /* preview player: the clips in the chosen order play one after another; the slider, a click on a clip and the playhead seek in the whole montage */
  const P = { g: 0, i: 0, playing: false, raf: 0, drag: false, mode: 'seq', loading: false, tok: 0 };
  const pv = $('.mt-pv', root), bar = $('.mt-bar', root), seek = $('.mt-seek', root), playBtn = $('.mt-play', root), timeEl = $('.mt-time', root);
  const mmss = s => { s = Math.max(0, s); return Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0'); };
  const totalLen = () => MT.ids.reduce((a, _, i) => a + durOf(i), 0);
  const startOf = i => { let a = 0; for (let j = 0; j < i; j++) a += durOf(j); return a; };
  function locate(g) { let a = 0; const n = MT.ids.length; for (let i = 0; i < n; i++) { const d = durOf(i); if (g < a + d - 1e-6 || i === n - 1) return { i, off: Math.min(d, Math.max(0, g - a)) }; a += d; } return { i: 0, off: 0 }; }
  function loadAt(i, off, play) {
    const id = MT.ids[i], it = get(id); if (!it) return;
    const tok = ++P.tok, go = () => { if (tok !== P.tok) return; P.loading = false; try { pv.currentTime = off; } catch {} if (play) pv.play().catch(() => {}); };
    if (pv.dataset.id !== id) { P.loading = true; pv.dataset.id = id; pv.src = it.url; pv.addEventListener('loadedmetadata', go, { once: true }); pv.load(); } else go();
  }
  function seekTo(g, play) {
    if (!MT.ids.length) return;
    g = Math.min(Math.max(0, g), totalLen()); const L = locate(g); P.g = g; P.i = L.i; loadAt(L.i, L.off, play); updateUI();
  }
  function updateUI() {
    const tot = totalLen(); if (!P.drag) seek.value = Math.round(P.g * 10);
    timeEl.textContent = mmss(P.g) + ' / ' + mmss(tot);
    const strip = $('.mt-strip', root), ph = $('.mt-ph', strip), items = $$('.mt-item', strip); if (!ph || !items.length) return;
    items.forEach((x, k) => x.classList.toggle('cur', k === P.i));
    const el = items[P.i]; if (!el) return;
    const x = el.offsetLeft + Math.min(1, Math.max(0, (P.g - startOf(P.i)) / durOf(P.i))) * el.offsetWidth; ph.style.left = x + 'px';
    if (P.playing && (x < strip.scrollLeft + 20 || x > strip.scrollLeft + strip.clientWidth - 20)) strip.scrollLeft = Math.max(0, x - 60);
  }
  function setPlaying(on) { P.playing = on; playBtn.textContent = on ? '⏸' : '▶'; clearInterval(P.raf); if (on) P.raf = setInterval(tick, 40); else pv.pause(); }
  function tick() {
    if (!P.playing) return;
    if (!P.loading) {
      const d = durOf(P.i), t = pv.currentTime;
      if (!pv.seeking && (t >= d - 0.04 || pv.ended)) {
        if (P.i < MT.ids.length - 1) { P.i++; P.g = startOf(P.i); loadAt(P.i, 0, true); }
        else { setPlaying(false); P.g = totalLen(); updateUI(); return; }
      } else P.g = startOf(P.i) + t;
      updateUI();
    }
  }
  function togglePlay() {
    if (!MT.ids.length || P.mode !== 'seq') return;
    if (P.playing) { setPlaying(false); return; }
    seekTo(P.g >= totalLen() - 0.05 ? 0 : P.g, true); setPlaying(true);
  }
  function showMode(m) {
    P.mode = m; const n = MT.ids.length, seq = m === 'seq' && n > 0, res = m === 'res' && MT.res;
    if (!seq) setPlaying(false);
    pv.hidden = !seq; bar.hidden = !seq; $('.mt-res', root).hidden = !res; $('.mt-empty', root).hidden = seq || res;
    if (!res) hideSoundChip($('.mt-res', root)?.parentElement);
    const tabs = $('.mt-tabs', root); tabs.hidden = !MT.res; $$('[data-pv]', tabs).forEach(b => b.classList.toggle('on', b.dataset.pv === m));
    if (m === 'seq' && !$('.mt-res', root).paused) $('.mt-res', root).pause();
  }
  function syncPlayer() {
    const n = MT.ids.length, tot = totalLen(); seek.max = Math.round(tot * 10);
    if (!n) { setPlaying(false); pv.removeAttribute('src'); delete pv.dataset.id; P.g = 0; P.i = 0; if (P.mode === 'seq') P.mode = MT.res ? 'res' : 'seq'; }
    else { setPlaying(false); seekTo(Math.min(P.g, tot), false); }
    showMode(P.mode); updateUI();
  }
  playBtn.onclick = togglePlay;
  seek.oninput = () => { P.drag = true; seekTo(seek.value / 10, P.playing); };
  seek.onchange = () => { P.drag = false; };
  scope.listen(window, 'keydown', e => {
    if (e.code !== 'Space' || root.offsetParent === null || e.target.closest('input,textarea,select,button,[contenteditable]')) return;
    e.preventDefault(); togglePlay();
  });
  function fitPx() {
    const W = $('.mt-strip', root).clientWidth - 8, n = MT.ids.length; if (!n) return 26;
    for (let px = 90; px > 8; px--) { const w = MT.ids.reduce((a, _, i) => a + Math.max(Math.round(px * 2.85), Math.round(durOf(i) * px)), 0) + 8 * (n - 1); if (w <= W) return px; }
    return 8;
  }
  const setPx = v => { MT.px = Math.min(90, Math.max(8, Math.round(v))); try { localStorage.setItem('h3-mt-px', MT.px); } catch {} render(); };
  window.montageLoad = ids => { MT.ids = ids.slice(); MT.cuts = ids.map(() => null); render(); const st = $('.mt-status', root); st.textContent = 'Сцены из Видеостудии уже стоят по порядку. Нажмите «Склеить»: готовый ролик появится справа — его можно сохранить или добавить в Библиотеку.'; };
  $('.mt-pc', root).onclick = () => $('.mt-file', root).click();
  $('.mt-file', root).onchange = async e => {
    const files = [...e.target.files]; e.target.value = '';
    for (const f of files) {
      const url = URL.createObjectURL(f);
      const meta = await new Promise(res => { const v = document.createElement('video'); v.preload = 'metadata'; v.muted = true; v.onloadedmetadata = () => res({ len: v.duration || 0, res: v.videoWidth ? `${v.videoWidth}×${v.videoHeight}` : '' }); v.onerror = () => res(null); v.src = url; });
      if (!meta) { toast('Не удалось открыть файл «' + f.name + '». Нужно видео (mp4, mov, webm).'); URL.revokeObjectURL(url); continue; }
      const k = 'k' + (++MT.n); MT.up[k] = { id: 'up:' + k, file: f, url, len: meta.len, res: meta.res, prompt: f.name };
      MT.ids.push('up:' + k); MT.cuts.push(null);
    }
    render();
  };
  const move = (i, j) => { if (j < 0 || j >= MT.ids.length) return; const [x] = MT.ids.splice(i, 1), [c] = MT.cuts.splice(i, 1); MT.ids.splice(j, 0, x); MT.cuts.splice(j, 0, c); render(); };
  root.addEventListener('click', e => {
    if (e.target.closest('[data-showall]')) { hideSet([]); render(); return; }
    const zb = e.target.closest('[data-z]'); if (zb) { const k = +zb.dataset.z; setPx(k > 0 ? MT.px * 1.25 : k < 0 ? MT.px * 0.8 : fitPx()); return; }
    const tb = e.target.closest('[data-pv]'); if (tb) { showMode(tb.dataset.pv); return; }
    const x = e.target.closest('[data-hide]'); if (x) { e.stopPropagation(); const id = x.dataset.hide; hideSet([...hidden(), id]); const k2 = MT.ids.map(v => v !== id); MT.cuts = MT.cuts.filter((_, i) => k2[i]); MT.ids = MT.ids.filter((_, i) => k2[i]); render(); return; }
    const c = e.target.closest('.mt-card'); if (c) { MT.ids.push(c.dataset.id); MT.cuts.push(null); render(); return; }
    const it = e.target.closest('.mt-item'); if (!it) return; const i = +it.dataset.i;
    if (e.target.closest('[data-rm]')) { MT.ids.splice(i, 1); MT.cuts.splice(i, 1); render(); return; }
    const mv = e.target.closest('[data-mv]'); if (mv) { move(i, i + +mv.dataset.mv); return; }
    if (e.target.closest('.mt-rh') || P.mode !== 'seq') { if (P.mode !== 'seq') showMode('seq'); else return; }
    const r = it.getBoundingClientRect(); seekTo(startOf(i) + Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * durOf(i), P.playing);   // a click on a clip: watch from that place
  });
  root.addEventListener('pointerdown', e => {
    const h = e.target.closest('.mt-rh'); if (!h) return;
    e.preventDefault(); e.stopPropagation();
    const el = h.closest('.mt-item'), i = +el.dataset.i, x0 = e.clientX, d0 = durOf(i), full = fullOf(i);
    el.draggable = false; try { h.setPointerCapture(e.pointerId); } catch {}
    const move2 = ev => { const d = Math.min(full, Math.max(0.5, Math.round((d0 + (ev.clientX - x0) / MT.px) * 10) / 10)); MT.cuts[i] = d; el.style.width = wOf(d) + 'px'; $('.mt-len', el).textContent = d.toFixed(1).replace('.0', '') + ' с ✂'; };
    const up = () => { h.removeEventListener('pointermove', move2); h.removeEventListener('pointerup', up); if (MT.cuts[i] != null && MT.cuts[i] >= full - 0.05) MT.cuts[i] = null; render(); };
    h.addEventListener('pointermove', move2); h.addEventListener('pointerup', up);
  });
  root.addEventListener('dragstart', e => { const it = e.target.closest('.mt-item'); if (!it) return; MT.drag = +it.dataset.i; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', 'mt'); });
  root.addEventListener('dragover', e => { const it = e.target.closest('.mt-item'); if (it && MT.drag != null) { e.preventDefault(); $$('.mt-item', root).forEach(x => x.classList.toggle('over', x === it)); } });
  root.addEventListener('drop', e => { const it = e.target.closest('.mt-item'); if (!it || MT.drag == null) return; e.preventDefault(); const from = MT.drag; MT.drag = null; move(from, +it.dataset.i); });
  root.addEventListener('dragend', () => { MT.drag = null; $$('.mt-item', root).forEach(x => x.classList.remove('over')); });

  let PEND = null; try { PEND = JSON.parse(localStorage.getItem('h3-mt-state') || 'null'); } catch {}
  function applyPend() {   // the order of clips from the last time, as soon as the Library is loaded
    if (!PEND || !LIB.length) return; const p = PEND; PEND = null;
    if (MT.ids.length || !Array.isArray(p.ids)) return;
    const ok = p.ids.map(id => !!byId(id)); MT.ids = p.ids.filter((_, i) => ok[i]); MT.cuts = (p.cuts || []).filter((_, i) => ok[i]); render();
  }
  const durList = () => MT.ids.map((_, i) => MT.cuts[i] != null && MT.cuts[i] < fullOf(i) - 0.05 ? MT.cuts[i] : null);
  const orderKey = () => JSON.stringify([MT.ids, durList()]);   // what the joined video was made from: another order or trim makes it out of date

  /* the state of the work (which clips are enlarged, grained, joined) is kept, so it survives closing the program: the finished parts lie in the Library */
  let PIPE = { sig: '', doUp: false, grain: 0, steps: {}, joined: null };
  try { const s = JSON.parse(localStorage.getItem('h3-mt-pipe') || 'null'); if (s && s.steps) PIPE = s; } catch {}
  const pipeSave = () => { try { localStorage.setItem('h3-mt-pipe', JSON.stringify(PIPE)); } catch {} pipePaint(); };
  window.mtSaveState = () => { if (PEND) return; try { const k = MT.ids.map(id => !id.startsWith('up:')); localStorage.setItem('h3-mt-state', JSON.stringify({ ids: MT.ids.filter((_, i) => k[i]), cuts: MT.cuts.filter((_, i) => k[i]) })); } catch {} };
  window.mtPipeDone = id => { const s = PIPE.steps[id]; return !!(s && (PIPE.grain > 0 ? s.gr : PIPE.doUp ? s.up : false)); };
  function pipePaint() {
    const box = $('.mt-pipe', root), n = MT.ids.length, line = (t, ok) => `<div class="${ok ? 'ok' : ''}">${ok ? '✓ ' : '• '}${t}</div>`;
    let h = '';
    const have = id => PIPE.steps[id] || {};
    if (PIPE.doUp && n) { const c = MT.ids.filter(id => have(id).up).length; h += line(`Увеличение: ${c} из ${n} клипов${c === n ? ' — завершено' : ''}`, c === n); }
    if (PIPE.grain > 0 && n) { const c = MT.ids.filter(id => have(id).gr).length; h += line(`Зерно: ${c} из ${n} клипов${c === n ? ' — завершено' : ''}`, c === n); }
    const j = PIPE.joined && (byId(PIPE.joined.id) || draftById(PIPE.joined.id)) ? PIPE.joined : null;
    if (MT.joining) h += line('Склейка: идёт…', false);
    else if (j && j.key === orderKey()) h += line('Склейка завершена, ролик можно сохранять', true);
    else if (j) h += line('Склейка устарела: порядок или обрезка изменились, склейте заново', false);
    else if (h) h += line('Склейка: ещё не сделана', false);
    box.innerHTML = h; box.hidden = !h;
  }
  window.mtPipePaint = pipePaint;
  function showDone(item, play = true) {
    const st = $('.mt-status', root), v = $('.mt-res', root);
    v.src = item.url; MT.res = true; showMode('res');
    if (play) playWithSound(v);
    else {   // a reel from the last run waits on its first frame: the sound must not start by itself after a restart
      v.preload = 'metadata';
      v.addEventListener('loadedmetadata', () => { try { v.currentTime = .04; } catch {} }, { once: true });
    }
    const draft = !!item.preview && !byId(item.id);
    st.innerHTML = (draft ? 'Склейка завершена: черновик, в Библиотеку не добавлен. ' : 'Склейка завершена: ролик лежит в Библиотеке. ')
      + (draft ? '<button class="btn pri" id="mtAddLib" type="button" style="padding:4px 12px;font-size:12px">Добавить в библиотеку</button> ' : '')
      + '<button class="btn pri" id="mtSave" type="button" style="padding:4px 12px;font-size:12px" title="Сохранить готовый ролик целиком на компьютер">Сохранить на компьютер</button> '
      + '<button class="btn" id="mtUp" type="button" style="padding:4px 12px;font-size:12px" title="Увеличить разрешение (долго)">Увеличить</button>';
    $('#mtSave', root).onclick = () => saveItems([byId(item.id) || normItem(item)]);
    $('#mtUp', root).onclick = () => openUpscale(byId(item.id) || normItem(item));
    $('#mtAddLib', root)?.addEventListener('click', async () => {
      const b = $('#mtAddLib', root); b.disabled = true;
      const kept = await keepDraftItem(item);
      if (!kept) { b.disabled = false; return; }
      if (PIPE.joined) PIPE.joined = { ...PIPE.joined, id: kept.id };
      pipeSave();
      showDone(kept, false);
    });
  }
  window.mtRestoreResult = () => {   // after a restart: show the joined video again if it is still in the Library
    const j = PIPE.joined, it = j && byId(j.id); if (!it || MT.res || it.missing) return;
    showDone(it, false);   // shown, but paused: only a join finished right now plays by itself
  };

  $('.mt-go', root).onclick = () => {
    if (FF.eng === 'ff') { ffJoin(MT.ids.slice(), durList()); return; }
    if (+$('.mt-grain', root).value > 0) chain('none'); else joinNow(MT.ids.slice(), durList());
  };

  /* ---------- the same reel assembled by the portable FFmpeg: seconds, no video card ---------- */
  const FF = { eng: 'comfy', status: null, busy: false, error: '' };
  try { FF.eng = localStorage.getItem('h3-mt-eng') || 'comfy'; } catch {}
  const ffBox = $('.mt-ff', root);
  function ffPaint() {
    $$('.mt-eng-tabs [data-eng]', root).forEach(b => b.classList.toggle('on', b.dataset.eng === FF.eng));
    ffBox.hidden = FF.eng !== 'ff';
    $('.mt-upbox', root).hidden = FF.eng === 'ff';
    $('.mt-sets', root).hidden = FF.eng === 'ff';
    $('.mt-go-t', root).textContent = FF.eng === 'ff' ? 'Собрать (FFmpeg)' : 'Склеить';
    $('.mt-go', root).disabled = FF.eng === 'ff' && !(FF.status && FF.status.installed);
  }
  async function ffStatus() {
    let s = null;
    try { s = await apiGet('/api/ffmpeg/status'); } catch {}
    FF.status = s;
    if (s && s.error) FF.error = s.error;   // the reason stays visible after a refresh, not only in a toast
    const box = $('.ff-state', root);
    if (!box) return;
    if (s && s.downloading && s.progress) {
      box.textContent = `Скачиваем FFmpeg: ${s.progress.pct}% — ${s.progress.text || ''}`;
    } else if (s && s.installed) {
      box.innerHTML = `FFmpeg ${esc(s.version || '')} готов. <button type="button" class="btn mini ff-open">Показать папку</button> <button type="button" class="btn mini ff-inst" data-again="1">Обновить</button>`;
      box.classList.add('on');
    } else {
      box.innerHTML = 'FFmpeg не установлен: он нужен только для этой быстрой сборки. <button type="button" class="btn mini pri ff-inst">Установить (~100 МБ)</button>'
        + (FF.error ? `<div class="ff-err">Не удалось установить: ${esc(FF.error)}</div>` : '');
      box.classList.remove('on');
    }
    ffPaint();
  }
  window.onFFDownload = m => {
    const box = $('.ff-state', root);
    if (!box) return;
    if (m.done) {
      FF.error = m.error || '';   // a failed install keeps its reason on screen, not only in a toast
      ffStatus();
      if (m.error) toast('FFmpeg: ' + m.error);
      return;
    }
    if (FF.eng === 'ff') box.textContent = `Скачиваем FFmpeg: ${m.pct}% — ${m.text || ''}`;
  };
  let ffPoll = 0;
  function ffPollStart() {   // if the websocket message is late or lost, the panel still updates itself
    clearInterval(ffPoll);
    ffPoll = setInterval(async () => {
      let s = null;
      try { s = await apiGet('/api/ffmpeg/status'); } catch { return; }
      if (s && s.downloading) {
        const b = $('.ff-state', root);
        if (b && FF.eng === 'ff' && s.progress) b.textContent = `Скачиваем FFmpeg: ${s.progress.pct}% — ${s.progress.text || ''}`;
        return;
      }
      clearInterval(ffPoll);
      ffStatus();
    }, 2500);
  }
  root.addEventListener('click', e => {
    if (e.target.closest('.ff-inst')) {
      e.target.disabled = true;
      FF.error = '';
      $('.ff-state', root).textContent = 'Начинаем скачивание…';
      apiPost('/api/ffmpeg/download', {})
        .catch(err => window.onFFDownload({ done: true, error: err.message || String(err) }));
      setTimeout(ffPollStart, 1500);
      return;
    }
    if (e.target.closest('.ff-open')) apiPost('/api/ffmpeg/open-folder', {}).catch(() => {});
  });
  $$('.mt-eng-tabs [data-eng]', root).forEach(b => b.onclick = () => {
    FF.eng = b.dataset.eng;
    try { localStorage.setItem('h3-mt-eng', FF.eng); } catch {}
    ffPaint();
    if (FF.eng === 'ff') ffStatus();
  });
  [['.ff-trans-dur', '.ff-tdv'], ['.ff-mvol', '.ff-mv'], ['.ff-vvol', '.ff-vv']].forEach(([a, b]) => {
    $(a, root).oninput = () => { $(b, root).textContent = $(a, root).value; };
  });
  async function ffJoin(ids, durs) {
    const st = $('.mt-status', root);
    if (FF.busy) return;
    if (!ids.length) { st.textContent = 'Добавьте хотя бы один клип'; return; }
    if (!(FF.status && FF.status.installed)) { st.textContent = 'Сначала установите FFmpeg: кнопка в блоке «FFmpeg».'; return; }
    FF.busy = true; $('.mt-go', root).disabled = true; st.textContent = 'Готовим клипы…';
    const fd = new FormData();
    fd.append('ids', JSON.stringify(ids));
    fd.append('durs', JSON.stringify(durs || []));
    fd.append('preview', '1');
    const put = (key, sel) => { const el = $(sel, root); if (el && el.value !== '') fd.append(key, el.value); };
    put('transition', '.ff-transition'); put('transition_dur', '.ff-trans-dur'); put('aspect', '.ff-aspect');
    put('fit_mode', '.ff-fit'); put('speed', '.ff-speed'); put('filter', '.ff-filter');
    put('title', '.ff-title'); put('title_pos', '.ff-title-pos');
    fd.append('music_vol', String((+$('.ff-mvol', root).value || 0) / 100));
    fd.append('video_vol', String((+$('.ff-vvol', root).value || 0) / 100));
    const music = $('.ff-music', root).files[0];
    if (music) fd.append('music', music, music.name);
    ids.filter(id => id.startsWith('up:')).forEach(id => {
      const u = get(id), k = id.slice(3);
      fd.append('res_' + k, u.res || ''); fd.append('len_' + k, String(u.len || 0)); fd.append('file_' + k, u.file, u.file.name);
    });
    let res;
    try {
      res = await apiSend('/api/ffmpeg/montage', { method: 'POST', body: fd, timeout: 180000 });
    } catch (err) {
      FF.busy = false; $('.mt-go', root).disabled = false;
      st.textContent = 'Сборка не удалась: ' + err.message; return;
    }
    qAdd(res.prompt_id, 'montage', 'FFmpeg · ' + ids.length + ' клипов');
    st.textContent = 'Собираем FFmpeg…';
    onPrompt(res.prompt_id, ev => {
      if (ev.prompt_id !== res.prompt_id) return;
      if (ev.type === 'stage') { st.textContent = `Сборка FFmpeg: ${ev.step || 1}%`; return; }
      if (ev.type === 'done') {
        offPrompt(res.prompt_id); FF.busy = false; $('.mt-go', root).disabled = false;
        st.textContent = 'Сборка завершена: ролик ждёт «Добавить в библиотеку».';
        showDone(normItem(ev.item));
      } else if (ev.type === 'error') {
        offPrompt(res.prompt_id); FF.busy = false; $('.mt-go', root).disabled = false;
        st.textContent = 'Сборка не удалась: ' + ev.message;
      }
    });
  }
  ffPaint();
  ffStatus();
  async function joinNow(ids, durs) {
    const st = $('.mt-status', root);
    if (MT.busy && !MT.chaining) return;
    if (ids.length < 2) { st.textContent = 'Добавьте хотя бы два клипа'; return; }
    const key = orderKey();
    MT.busy = true; MT.joining = true; $('.mt-go', root).disabled = true; st.textContent = 'Отправляем клипы в ComfyUI…'; pipePaint();
    const fail = msg => { MT.busy = false; MT.joining = false; MT.chaining = false; $('.mt-go', root).disabled = false; st.textContent = msg; pipePaint(); };
    try {
      const fd = new FormData(); fd.append('ids', JSON.stringify(ids)); fd.append('durs', JSON.stringify(durs)); fd.append('preview', '1');
      ids.filter(id => id.startsWith('up:')).forEach(id => { const u = get(id), k = id.slice(3); fd.append('res_' + k, u.res || ''); fd.append('len_' + k, String(u.len || 0)); fd.append('file_' + k, u.file, u.file.name); });
      const res = await apiSend('/api/montage', { method: 'POST', body: fd, timeout: 180000 });
      qAdd(res.prompt_id, 'montage', `${ids.length} клипов`);
      st.textContent = 'Склеиваем…';
      onPrompt(res.prompt_id, ev => {
        if (ev.prompt_id !== res.prompt_id) return;
        if (ev.type === 'done') {
          offPrompt(res.prompt_id); MT.busy = false; MT.joining = false; MT.chaining = false; $('.mt-go', root).disabled = false;
          PIPE.joined = { id: ev.item.id, key }; pipeSave();
          showDone(byId(ev.item.id) || normItem(ev.item));
        } else if (ev.type === 'error') fail('Склейка не удалась: ' + ev.message);
        else if (ev.type === 'interrupted') fail('Склейка остановлена');
      });
    } catch (err) { fail('Склейка не удалась: ' + err.message); }
  }

  /* «Увеличить каждый клип и склеить» / зерно: the clips are done one by one (each is short, so memory is enough), then the finished ones are joined.
     What is finished is remembered, so after an error or a restart the work goes on from the first clip that is not done. */
  const upBtn = $('.mt-upgo', root), upTxt = upBtn.textContent;
  const setsPaint = () => { const relay = $('.mt-upeng', root).value === 'relay'; $('.mt-s-detail', root).hidden = !relay; $('.mt-s-sharp', root).hidden = relay; };   // the detail slider belongs to RelayVSR, the sharpness one to SeedVR2
  $('.mt-upeng', root).onchange = setsPaint; setsPaint();
  [['.mt-detail', '.mt-dv'], ['.mt-sharp', '.mt-sv'], ['.mt-grain', '.mt-gv']].forEach(([a, b]) => { $(a, root).oninput = () => { $(b, root).textContent = $(a, root).value; }; });
  upBtn.onclick = () => chain($('.mt-upeng', root).value);
  const waitItem = r => new Promise((res, rej) => onPrompt(r.prompt_id, ev => {
    if (ev.prompt_id !== r.prompt_id) return;
    if (ev.type === 'done') { offPrompt(r.prompt_id); res(ev.item); }
    else if (ev.type === 'error') { offPrompt(r.prompt_id); rej(new Error('ошибка ComfyUI: ' + ev.message)); }
    else if (ev.type === 'interrupted') { offPrompt(r.prompt_id); rej(new Error('остановлено')); }
  }));
  async function chain(eng) {   // eng: 'relay' | 'seed' (enlarge each clip) | 'none' (only the grain)
    const st = $('.mt-status', root);
    if (MT.upBusy) { MT.cancel = true; st.textContent = 'Останавливаем…'; if (MT.curPrompt) apiPost('/api/interrupt', { prompt_id: MT.curPrompt }).catch(() => {}); return; }
    if (MT.busy) return;
    if (MT.ids.length < 2) { st.textContent = 'Добавьте хотя бы два клипа'; return; }
    const doUp = eng !== 'none', grain = +$('.mt-grain', root).value;
    if (doUp) {
      let stt = {}; try { stt = await apiGet('/api/status'); } catch {}
      const haveRelay = (stt.modes || []).includes('relayvsr') && !(stt.models || []).some(m => m.role.startsWith('relayvsr') && !m.have);
      const haveSeed = (stt.modes || []).includes('upscale') && !(stt.models || []).some(m => m.role.startsWith('seedvr') && !m.have);
      if (eng === 'relay' ? !haveRelay : !haveSeed) { st.textContent = `Нужна модель ${eng === 'relay' ? 'RelayVSR' : 'SeedVR2'}: её можно скачать кнопкой «Увеличить» у любого видео в Библиотеке.`; return; }
    }
    const opt = { scale: '2', method: '', engine: eng === 'relay' ? 'relay' : '', detail: +$('.mt-detail', root).value / 100, sharp: +$('.mt-sharp', root).value / 100 };
    const sig = [doUp ? eng : '-', doUp ? (eng === 'relay' ? opt.detail : opt.sharp) : '-', grain].join('|');
    if (PIPE.sig !== sig) PIPE = { sig, doUp, grain, steps: {}, joined: null };   // other settings: the clips are done again
    const ids = MT.ids.slice(), durs = durList(), out = [], todo = ids.filter(id => !(PIPE.steps[id] && (!doUp || PIPE.steps[id].up) && (!grain || PIPE.steps[id].gr))).length;
    MT.upBusy = true; MT.busy = true; MT.chaining = true; MT.cancel = false; $('.mt-go', root).disabled = true; upBtn.textContent = 'Остановить'; pipeSave();
    const job = async (url, id, srcFile, extra, label, k) => {   // one job on ComfyUI; returns the finished Library item
      let r;
      if (srcFile) {
        const fd = new FormData(); Object.entries(extra).forEach(([a, b]) => fd.append(a, b)); fd.append('res', srcFile.res || ''); fd.append('len', srcFile.len || 0); fd.append('file', srcFile.file, srcFile.file.name);
        r = await apiSend(url + (url === '/api/upscale' ? '_file' : ''), { method: 'POST', body: fd, timeout: 180000 });
      } else if (url === '/api/grain') {
        const fd = new FormData(); fd.append('id', id); fd.append('strength', extra.strength);
        r = await apiSend(url, { method: 'POST', body: fd, timeout: 180000 });
      } else r = await postJSON(url, { id, ...extra });
      qAdd(r.prompt_id, label === 'grain' ? 'montage' : 'upscale', (get(ids[k]).prompt || '') || (label === 'grain' ? 'Зерно плёнки' : 'Увеличение')); MT.curPrompt = r.prompt_id;
      return waitItem(r);
    };
    try {
      for (let k = 0; k < ids.length; k++) {
        if (MT.cancel) throw new Error('остановлено');
        const src = ids[k], step = PIPE.steps[src] || (PIPE.steps[src] = { up: null, gr: null }), u = src.startsWith('up:') ? get(src) : null;
        let cur = src;
        if (doUp) {
          if (!step.up) { st.textContent = `Клип ${k + 1} из ${ids.length}: увеличиваем…`; step.up = (await job('/api/upscale', src, u, opt, 'up', k)).id; pipeSave(); }
          cur = step.up;
        }
        if (grain > 0) {
          if (!step.gr) { st.textContent = `Клип ${k + 1} из ${ids.length}: накладываем зерно…`; step.gr = (await job('/api/grain', cur, doUp ? null : u, { strength: grain }, 'grain', k)).id; pipeSave(); }
          cur = step.gr;
        }
        out.push(cur);
      }
    } catch (err) {
      MT.upBusy = false; MT.busy = false; MT.chaining = false; MT.curPrompt = null; $('.mt-go', root).disabled = false; upBtn.textContent = upTxt; pipePaint();
      const left = ids.filter(id => !(PIPE.steps[id] && (!doUp || PIPE.steps[id].up) && (!grain || PIPE.steps[id].gr))).length;
      st.textContent = `Обработка прервана: ${err.message}. Готово ${ids.length - left} из ${ids.length} клипов, они сохранены. Нажмите кнопку ещё раз: работа продолжится с первого неготового клипа.`;
      return;
    }
    MT.upBusy = false; MT.curPrompt = null; upBtn.textContent = upTxt;
    st.textContent = `${doUp ? 'Увеличение' : 'Зерно'} завершено для всех ${ids.length} клипов. Склеиваем…`;
    await joinNow(out, durs);
  }
  const onRefresh = () => { applyPend(); pipePaint(); window.mtRestoreResult(); };
  IMG_PANELS.push(onRefresh);
  scope.add(() => { const i = IMG_PANELS.indexOf(onRefresh); if (i >= 0) IMG_PANELS.splice(i, 1); });
  pipePaint();
  /* a handle under the strip: drag down or up and the clips grow or shrink (the same scale as «−» «+»); the divider before the preview moves left and right */
  (() => {
    const h = $('.mt-vh', root); let y0 = 0, px0 = 0, on = false;
    h.addEventListener('pointerdown', e => { e.preventDefault(); h.setPointerCapture(e.pointerId); on = true; y0 = e.clientY; px0 = MT.px; });
    h.addEventListener('pointermove', e => {
      if (!on) return;
      MT.px = Math.min(90, Math.max(8, px0 + (e.clientY - y0) * 0.35));
      $$('.mt-item', root).forEach((el, i) => { el.style.width = wOf(durOf(i)) + 'px'; });
      updateUI();
    });
    h.addEventListener('pointerup', e => { if (!on) return; on = false; try { h.releasePointerCapture(e.pointerId); } catch {} setPx(MT.px); });
    h.addEventListener('dblclick', () => setPx(26));
  })();
  if (window.gridResize) window.gridResize($('.grid', root));
  IMG_PANELS.push(render);
  scope.add(() => { const i = IMG_PANELS.indexOf(render); if (i >= 0) IMG_PANELS.splice(i, 1); });
  render();

  // leaving the section: stop the FFmpeg poll and the preview, pause the clips; the queue keeps working
  onLeave = () => {
    try { clearInterval(ffPoll); setPlaying(false); } catch (e) { console.error(e); }
    window.h3Lifecycle.pauseMedia(root);
  };
  // closing the window: the uploaded clips' blob URLs and the page-wide hooks go away with the section
  onDispose = () => {
    onLeave();
    Object.values(MT.up).forEach(u => { try { URL.revokeObjectURL(u.url); } catch {} });
    delete window.montageLoad; delete window.mtSaveState; delete window.mtRestoreResult;
  };
  }

  window.h3Modes.register("montage", {
    mount: build,
    leave: () => onLeave(),
    dispose: () => onDispose(),
  });
})();
