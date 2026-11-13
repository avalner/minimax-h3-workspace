/* Agent: the chat, its attachments, the skills and the local model's status.
 *
 * A mode module. The section's markup is static in the page; the module owns the conversation
 * state `AG` (messages, attachments, the answered target, the running request), the streaming
 * reader of `/api/agent/chat`, the chat history and the model/skill cards. Everything is wired on
 * the first visit: the agent is not needed before the user opens it, except for one page path — a
 * picture from the Library "into the chat" — which mounts the module on demand (`attachFile`).
 *
 * Two page-wide names stay in the page because other sections use them: `setDropFile` (a file into a
 * drop zone) and `confirmBox`. The download progress of the model arrives as an event and is handed
 * here through `window.h3Agent.setup`.
 *
 * Loaded before the page; the wiring waits for `mount`.
 */

(function () {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  let api = null;   // the built section: null until the first visit
  let onEnter = () => {}, onLeave = () => {}, onDispose = () => {};

function build(scope) {
const AG = { msgs: [], att: [], target: 'video', busy: false, ctrl: null, status: null };
const fileToDataURL = (file, max = 1024) => new Promise(res => {
  const img = new Image();
  img.onload = () => {
    const k = Math.min(1, max / Math.max(img.width, img.height));
    const c = document.createElement('canvas'); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
    c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
    res(c.toDataURL('image/jpeg', 0.88)); URL.revokeObjectURL(img.src);
  };
  img.src = URL.createObjectURL(file);
});
function agImgCount() { return AG.msgs.reduce((n, m) => n + (m.images?.length || 0) - (m.nframes || 0), 0); }   // frames of a video are not pictures of heroes
function renderAtt() {
  const base = agImgCount(); let k = 0;
  $('#agAtt').innerHTML = AG.att.map((a, i) => a.frame
    ? `<div title="Кадр из видео «${esc(a.vname)}»"><img src="${a.url}"><b style="background:#3a5a8c">🎞 ${esc(a.label)}</b><button data-i="${i}">✕</button></div>`
    : `<div><img src="${a.url}"><b>Picture ${base + (++k)}</b><button data-i="${i}">✕</button></div>`).join('');
  if (window._agUpdX) window._agUpdX();   // keep the clear-field cross in sync
}
// a video cannot be shown to the model as a file: several frames are cut out of it and sent as pictures, with the seconds written down
async function agVideoFrames(file, n = 8) {
  const v = document.createElement('video'); v.muted = true; v.preload = 'auto'; v.src = URL.createObjectURL(file);
  const ok = await new Promise(r => { v.onloadedmetadata = () => r(true); v.onerror = () => r(false); });
  if (!ok || !(v.duration > 0)) { URL.revokeObjectURL(v.src); throw new Error('Не получилось открыть видео «' + file.name + '». Попробуйте MP4 (H.264).'); }
  const dur = Math.min(v.duration, 30), w = Math.min(640, v.videoWidth || 640), h = Math.round(w * (v.videoHeight || 360) / (v.videoWidth || 640));
  const c = document.createElement('canvas'); c.width = w; c.height = h; const g = c.getContext('2d'), out = [];
  for (let i = 0; i < n; i++) {
    const t = Math.min(dur - 0.1, Math.max(0.1, dur * (i + 0.5) / n));
    v.currentTime = t; await new Promise(r => { v.onseeked = r; setTimeout(r, 1500); });
    g.drawImage(v, 0, 0, w, h);
    const blob = await new Promise(r => c.toBlob(r, 'image/jpeg', 0.82));
    out.push({ t, file: new File([blob], `video_${file.name.replace(/\.[^.]+$/, '')}_${t.toFixed(1)}s.jpg`, { type: 'image/jpeg' }), url: await fileToDataURL(blob) });
  }
  URL.revokeObjectURL(v.src);
  return { frames: out, dur: v.duration };
}
$('#agVidFile').onchange = async e => {
  const f = e.target.files[0]; e.target.value = ''; if (!f) return;
  AG.att = AG.att.filter(a => !a.frame);   // one video at a time
  toast('Вырезаем кадры из видео…');
  try {
    const { frames, dur } = await agVideoFrames(f);
    frames.forEach(fr => AG.att.push({ file: fr.file, url: fr.url, frame: true, label: fr.t.toFixed(1) + ' с', vname: f.name, vdur: dur, vfile: f }));
    renderAtt(); agCtx(); toast(`Из видео взято ${frames.length} кадров. Теперь напишите, кого на что заменить`);
  } catch (err) { toast(err.message); }
};
$('#agAtt').onclick = e => { const b = e.target.closest('button[data-i]'); if (b) { AG.att.splice(+b.dataset.i, 1); renderAtt(); } };
$('#agFile').onchange = async e => {
  for (const f of e.target.files) if (f.type.startsWith('image/')) AG.att.push({ file: f, url: await fileToDataURL(f) });
  e.target.value = ''; renderAtt();
};
$('#agEmpty').onclick = e => { const s = e.target.closest('span'); if (s) { $('#agIn').value = s.textContent.replace(/[«»]/g, '').replace(/ \+ фото героинь$/, ''); $('#agIn').focus(); } };
scope.listen($('#agIn'), 'keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#agSend').click(); } });
const AG_NAMES = { plist: 'список кадров', long: 'сцены длинного видео', video: 'Генерацию видео', lipsync: 'Lipsync', refs: 'Референсы', actor: 'Актёрскую игру', swap: 'Замену персонажа', action: 'Экшн-сцены', krea: 'Изображение по тексту', qwen: 'Правку изображения', h3t: 'Изображение по тексту (H3 Image)', h3e: 'правку по картинкам (H3 Image)', sheet: 'Изображение по тексту', sheet6: 'Изображение по тексту', fluxt: 'Изображение по тексту', qwent: 'Изображение по тексту', story: 'Изображение по тексту' };
const AG_IMG = ['plist', 'krea', 'qwen', 'h3t', 'h3e', 'sheet', 'sheet6', 'fluxt', 'qwent', 'story'];
// the picture targets whose prompt goes into the editor as MiniMax H3 Image (the section's model, not the pictures page)
const AG_H3IMG = ['h3t', 'h3e'];
AG.storyModel = 'krea'; AG.storyFrames = true;
AG.think = (() => { try { return localStorage.getItem('h3-ag-think') === 'deep' ? 'deep' : 'fast'; } catch { return 'fast'; } })();
function agThinkPaint() {
  $$('#agThink button').forEach(b => b.classList.toggle('on', b.dataset.k === AG.think));
  $('#agThinkHint').textContent = AG.think === 'fast' ? 'Агент отвечает сразу. Для обычных сцен и промптов этого хватает.' : 'Агент сначала долго рассуждает, потом отвечает: на одну сцену может уйти пара минут. Включайте для сложных историй.';
}
$('#agThink').onclick = e => { const b = e.target.closest('button'); if (!b) return; AG.think = b.dataset.k; try { localStorage.setItem('h3-ag-think', AG.think); } catch {} agThinkPaint(); };
agThinkPaint();
const agTargetClick = e => {
  const b = e.target.closest('button[data-t]'); if (!b) return;
  AG.target = b.dataset.t; $$('#agTarget button, #agTarget2 button').forEach(x => x.classList.toggle('on', x === b)); agCtx();
};
$('#agStoryF').onclick = e => { const b = e.target.closest('button[data-f]'); if (!b) return; AG.storyFrames = b.dataset.f === '1'; agCtx(); };
$('#agStoryM').onclick = e => { const b = e.target.closest('button[data-m]'); if (!b) return; AG.storyModel = b.dataset.m; agCtx(); };
$('#agTarget').onclick = agTargetClick; $('#agTarget2').onclick = agTargetClick;
function agCtx() {
  const n = agImgCount() + AG.att.filter(a => !a.frame).length;
  $('#agStoryF').hidden = AG.target !== 'story';
  $('#agStoryM').hidden = AG.target !== 'story';
  $$('#agStoryF button').forEach(b => b.classList.toggle('on', (b.dataset.f === '1') === AG.storyFrames));
  $$('#agStoryM button').forEach(b => b.classList.toggle('on', b.dataset.m === AG.storyModel));
  $('#agCtx').textContent = AG_IMG.includes(AG.target)
    ? ({ krea: 'Опишите изображение словами: агент напишет промпт для Krea 2. Изображения в чате — только образцы настроения.',
         qwen: `Приложите изображение, которое нужно изменить (скрепка или «Из Библиотеки»), и напишите, что поменять. Изображений в чате: ${n}.`,
         h3t: 'Опишите изображение словами: агент напишет промпт для MiniMax H3 Image — крупно (8 или 16 МП), без негативного промпта. Изображения в чате — только образцы настроения.',
         h3e: `Приложите картинки (до 5: первая — то, что правим, остальные — герой, место или стиль) и напишите, что сделать. В промпте они станут <Picture 1>…<Picture 5>, и первая задаёт форму результата. Изображений в чате: ${n}.`,
         story: 'Расскажите историю по кадрам («первый кадр — …, второй кадр — …») и скажите, сколько кадров нужно. Агент напишет промпт одного изображения-раскадровки без подписей и номеров для выбранной модели.'
           + (AG.storyModel === 'h3img' ? ' Для MiniMax H3 Image кадры и герои называются <Picture 1>, <Picture 2>.' : ''),
         fluxt: 'Опишите изображение словами: агент напишет промпт для FLUX.2 klein (без негатива, описание по порядку: главное сначала). Изображения в чате — только образцы настроения.',
         qwent: 'Опишите изображение словами: агент напишет промпт для Qwen 2.1. Негатив впишите отдельным полем в разделе. Изображения в чате — только образцы настроения.',
         sheet: `Опишите персонажа: агент напишет промпт листа из 4 панелей. Изображений в чате: ${n}.`,
         sheet6: `Опишите персонажа: агент напишет промпт листа из 6 панелей (в полный рост спереди, в три четверти, сбоку, сзади + два портрета). Изображений в чате: ${n}.` })[AG.target]
    : AG.target === 'plist'
    ? `Опишите историю или набор изображений («герой утром, героиня днём, вечер у моря…») и скажите, сколько нужно. Агент напишет промпт для каждого изображения, а кнопка вставит их в «Изображение по тексту» в режим «Список промптов».`
    : AG.target === 'swap'
    ? `Скажите, кого в видео заменить (например, «старика в пиджаке») и каким изображением. Изображения героев приложите скрепкой по порядку: они станут Picture 1, 2. Своё видео приложите кнопкой с камерой: программа вырежет из него кадры, и Агент увидит, кто в кадре (видео само уйдёт в «Замену персонажа»). Изображений героев в чате: ${n}.`
    : AG.target === 'long'
    ? `Опишите историю по сценам («сначала… потом… в конце…») и скажите, с песней ролик или без. Агент разобьёт её на сцены и подпишет длину каждой. Изображения в чате: ${n} — станут референсами героя и места (Picture 1, 2…).`
    : AG.target === 'video'
    ? `Длительность ${window.h3Video.state.dur} с (как в «Генерации видео»). Изображения в чате: ${n} — 1-я станет первым кадром, 2-я последним.`
    : AG.target === 'lipsync'
      ? `Длина видео = длине аудио${window.h3Lipsync.state.dur0 ? ` (${window.h3Lipsync.state.len().toFixed(1)} с)` : ''}. Изображения в чате: ${n} — станут референсами Picture 1, 2…`
      : `Длительность ${RM[AG.target].S.dur} с (как в разделе «${{ refs: 'Референсы', actor: 'Актёрская игра', action: 'Экшн-сцены' }[AG.target]}»). Изображения в чате: ${n} — станут Picture 1, 2…`;
}
function mdLite(s) {
  return esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').split(/\n{2,}/).map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
}
function renderAssistant(el, m) {
  let html = m.skills?.length ? `<div class="skc">${m.skills.map(s => `<span>${esc(s)}</span>`).join('')}</div>` : '';
  const parts = (m.content || '').split(/```(?:prompt|text)?\s*\n?/);
  parts.forEach((p, i) => {
    if (i % 2 === 0) { if (p.trim()) html += mdLite(p.trim()); return; }
    const closed = i < parts.length - 1;
    const text = m.edits?.[i] ?? p.replace(/\n$/, '');
    html += `<div class="pblock"><pre${closed ? ` contenteditable="plaintext-only" spellcheck="false" data-b="${i}" title="Текст можно править прямо здесь"` : ''}>${esc(text)}</pre>${closed ? `<div class="pb-act">
      <button class="btn pri" data-send="${AG.target}">В ${AG_NAMES[AG.target]}</button>
      <select class="pb-other"><option value="">Отправить в…</option><option value="video">Image-to-Video</option><option value="lipsync">Lipsync</option><option value="refs">Референсы</option><option value="actor">Актёрская игра</option><option value="swap">Замена персонажа</option><option value="action">Экшн-сцены</option><option value="long">Видеостудия (сцены)</option><option value="krea">Изображение по тексту</option><option value="qwen">Правка изображения</option></select>
      <button class="btn" data-ru title="Русский перевод только для чтения: в модель уходит английский текст">Показать по-русски</button><button class="btn" data-copy>Копировать</button><button class="btn" data-paste title="Вставить скопированный текст на место выделенного (или в конец)">Вставить</button><small class="pb-n">${text.length} симв.</small></div>` : ''}${m.ru?.[i] ? `<div class="pb-ru"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:4px"><b>По-русски (только для чтения):</b><button class="btn" data-copy-ru type="button" style="padding:3px 10px;font-size:12px">Копировать русский</button></div><span class="pb-ru-t">${esc(m.ru[i]).replace(/\n/g, '<br>')}</span></div>` : ''}</div>`;
  });
  if (m.status && !m.content) html += `<div class="st">${esc(m.status)}</div>`;
  if (m.error) html += `<p style="color:var(--pink)">${esc(m.error)}</p>`;
  el.innerHTML = html || '<div class="st">…</div>';
}
function renderMsgs() {
  $('#agEmpty').hidden = AG.msgs.length > 0;
  $$('#agMsgs .msg').forEach(x => x.remove());
  AG.msgs.forEach((m, idx) => {
    const d = document.createElement('div'); d.className = 'msg ' + (m.role === 'user' ? 'u' : 'a'); d.dataset.i = idx;
    if (m.role === 'user') {
      d.textContent = m.content;
      if (m.images?.length) d.insertAdjacentHTML('beforeend', `<div class="imgs">${m.images.map(u => `<img src="${u}">`).join('')}</div>`);
    } else renderAssistant(d, m);
    $('#agMsgs').append(d);
  });
  $('#agMsgs').scrollTop = 1e9;
}
/* audio in the chat: words from a clip (for lipsync prompts) and voice input */
async function transcribeWav(blob, lang = 'auto') {
  const fd = new FormData(); fd.append('file', blob, 'a.wav'); fd.append('lang', lang);
  return await apiSend('/api/speech/transcribe', { method: 'POST', body: fd });
}
function needSpeech() {
  if (AG.status?.speech_ready) return false;
  toast('Распознавание речи ещё не скачано — карточка «Модель» справа, кнопка «Докачать»'); return true;
}
function showAudioBox() {
  const a = AG.audio; $('#agAudioBox').hidden = !a; if (!a) return;
  $('#agAudioName').textContent = a.name; $('#agAudioDur').textContent = a.dur.toFixed(1) + ' с';
  $('#agAudioText').value = a.text; $('#agAudioText').placeholder = a.busy ? 'Распознаём слова…' : 'Слов не найдено — впишите их сами или оставьте пустым (музыка)';
  $('#agAudioText').disabled = a.busy;
}
async function setChatAudio(wavBlob, name, dur) {
  if (needSpeech()) return;
  const a = AG.audio = { name, dur, text: '', busy: true, lang: '', langName: '', file: new File([wavBlob], name.replace(/\.[^.]+$/, '') + '.wav', { type: 'audio/wav' }) };
  showAudioBox();
  try { const j = await transcribeWav(wavBlob); if (AG.audio === a) { a.text = j.text; a.lang = j.lang; a.langName = j.lang_name; } }
  catch (e) { toast(e.message); }
  if (AG.audio === a) { a.busy = false; showAudioBox(); }
}
$('#agAudioFile').onchange = async e => {
  const f = e.target.files[0]; e.target.value = ''; if (!f) return;
  try {
    const buf = await new AudioContext().decodeAudioData(await f.arrayBuffer());
    if (buf.duration > 120) toast('Длинное аудио: берём первые 2 минуты. Для нужного куска выделите его в разделе Lipsync и нажмите значок микрофона-фрагмента.');
    await setChatAudio(wavSlice(buf, 0, Math.min(buf.duration, 120)), f.name, Math.min(buf.duration, 120));
  } catch { toast('Не удалось прочитать аудиофайл'); }
};
// attach something from the Library to the chat: a picture (or the last frame of a video), or the sound of a video
const agAttachFile = async f => { AG.att.push({ file: f, url: await fileToDataURL(f) }); renderAtt(); agCtx(); };
$('#agLib').onclick = () => {
  let m = $('#agLibM'); if (!m) { m = document.createElement('div'); m.className = 'modal'; m.id = 'agLibM'; document.body.append(m); }
  m.hidden = false; let tab = 'img';
  const paint = () => {
    const items = LIB.filter(it => !it.missing && it.url && (tab === 'img' ? it.kind === 'image' : it.kind !== 'image')).sort((a, b) => b.date - a.date);
    m.innerHTML = '<div class="box" style="padding:20px"><div class="card-h"><h3>Из Библиотеки</h3><button class="btn" data-x type="button">Закрыть</button></div>'
      + `<div class="seg" style="grid-template-columns:1fr 1fr;margin-bottom:12px"><button data-tab="img" class="${tab === 'img' ? 'on' : ''}">Изображения</button><button data-tab="snd" class="${tab === 'snd' ? 'on' : ''}">Звук из видео<small>голос или песня</small></button></div>`
      + (items.length ? `<div class="cw-pickgrid">${items.map(it => `<button class="cw-th" data-id="${it.id}" type="button" title="${esc((it.label ? it.label + ' · ' : '') + (it.prompt || '').slice(0, 80))}">${it.kind === 'image' ? `<img src="${it.url}" alt="" loading="lazy">` : `<video src="${it.url}#t=0.5" preload="metadata" muted></video>`}</button>`).join('')}</div>`
        : `<div class="hint" style="padding:20px 4px">${tab === 'img' ? 'Изображений в Библиотеке пока нет.' : 'Видео в Библиотеке пока нет.'}</div>`)
      + (tab === 'snd' ? '<div class="hint" style="margin-top:10px">Слова из звука будут распознаны, как при загрузке аудио.</div>' : '') + '</div>';
  };
  paint();
  m.onclick = async ev => {
    if (ev.target === m || ev.target.closest('[data-x]')) { m.hidden = true; return; }
    const t = ev.target.closest('[data-tab]'); if (t) { tab = t.dataset.tab; paint(); return; }
    const th = ev.target.closest('.cw-th'); if (!th) return;
    const it = LIB.find(x => x.id === th.dataset.id); if (!it) return;
    try {
      if (tab === 'img') { await agAttachFile(await libToFile(it)); toast('Изображение добавлена в чат'); }
      else {
        toast('Берём звук из видео…');
        const buf = await new AudioContext().decodeAudioData(await apiGet(it.url, { raw: 'arrayBuffer' }));
        if (buf.duration > 120) toast('Длинное аудио: берём первые 2 минуты.');
        const d = Math.min(buf.duration, 120);
        await setChatAudio(wavSlice(buf, 0, d), (it.label || it.sub || 'видео из Библиотеки'), d);
      }
      m.hidden = true;
    } catch { toast(tab === 'img' ? 'Не удалось взять файл' : 'В этом видео нет звука или его не удалось прочитать'); }
  };
};
$('#agFromLip').onclick = () => {
  const clip = window.h3Lipsync.audioClip();
  if (!clip) return;
  setChatAudio(clip, 'фрагмент из Lipsync', window.h3Lipsync.state.len());
};
$('#agAudioText').oninput = e => { if (AG.audio) AG.audio.text = e.target.value; };
$('#agAudioX').onclick = () => { AG.audio = null; showAudioBox(); };

let micRec = null, micTimer = null;
$('#agMic').onclick = async () => {
  if (micRec) { micRec.stop(); return; }
  if (needSpeech()) return;
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch { toast('Нет доступа к микрофону. Разрешите его в окне Windows или подключите микрофон.'); return; }
  const chunks = [], mr = new MediaRecorder(stream), t0 = Date.now(), btn = $('#agMic');
  micRec = mr; btn.classList.add('rec');
  const tick = () => { btn.title = `Идёт запись ${Math.round((Date.now() - t0) / 1000)} с — нажмите, чтобы закончить`; };
  tick(); micTimer = setInterval(tick, 500);
  mr.ondataavailable = e => chunks.push(e.data);
  mr.onstop = async () => {
    clearInterval(micTimer); stream.getTracks().forEach(t => t.stop()); micRec = null; btn.classList.remove('rec');
    btn.title = 'Голосовой ввод: нажмите, говорите, нажмите ещё раз';
    const ta = $('#agIn'), before = ta.placeholder; ta.placeholder = 'Распознаём речь…';
    try {
      const buf = await new AudioContext().decodeAudioData(await new Blob(chunks).arrayBuffer());
      if (buf.duration < 0.4) throw new Error('Запись слишком короткая');
      const j = await transcribeWav(wavSlice(buf, 0, buf.duration));
      if (!j.text) toast('Речь не распознана — скажите ещё раз'); else { ta.value = (ta.value.trim() ? ta.value.trim() + ' ' : '') + j.text; ta.focus(); }
    } catch (e) { toast(e.message); }
    ta.placeholder = before;
  };
  mr.start();
};

async function agSend() {
  let text = $('#agIn').value.trim();
  if (AG.busy) { AG.ctrl?.abort(); return; }
  const au = AG.audio;
  if (au?.busy) { toast('Подождите, слова из аудио ещё распознаются'); return; }
  if (!text && !AG.att.length && !au) return;
  if (!AG.status?.installed) { toast('Сначала скачайте модель агента — справа, в карточке «Модель»'); return; }
  if (au) {
    text += `\n\n[Аудио для клипа: ${au.name}, ${au.dur.toFixed(1)} с. ` + (au.text.trim()
      ? `Слова из аудио${au.langName ? ` (язык: ${au.langName})` : ''}: «${au.text.trim()}»]` : 'Слов в аудио нет (музыка или голос не распознан).]');
    text = text.trim();
  }
  const pics = AG.att.filter(a => !a.frame), frs = AG.att.filter(a => a.frame);   // frames of the video go last, so Picture 1, 2… stay the heroes
  if (frs.length) {
    text += `\n\n[Видео «${frs[0].vname}», ${frs[0].vdur.toFixed(1)} с (это <Video 1>). К сообщению приложены последние ${frs.length} изображений: кадры из этого видео в моменты ${frs.map(a => a.label).join(', ')}. Это кадры исходного видео, а не изображения героев: в Picture N они не входят.]`;
    text = text.trim();
  }
  AG.msgs.push({ role: 'user', content: text, images: [...pics, ...frs].map(a => a.url), files: pics.map(a => a.file), nframes: frs.length, videoFile: frs[0]?.vfile, audioFile: au?.file });
  AG.att = []; renderAtt(); $('#agIn').value = ''; AG.audio = null; showAudioBox();
  const reply = { role: 'assistant', content: '', status: 'Отправляем…' };
  AG.msgs.push(reply); renderMsgs();
  const el = $('#agMsgs .msg:last-child');
  AG.busy = true; $('#agSend').classList.add('busy'); $('#agSend').title = 'Остановить'; AG.ctrl = new AbortController();
  const ctx = { model: AG.storyFrames && AG.storyModel === 'krea' ? 'flux' : AG.storyModel, frames: AG.target === 'story' && AG.storyFrames, duration: AG_IMG.includes(AG.target) ? null : AG.target === 'video' ? window.h3Video.state.dur : AG.target === 'lipsync' ? (window.h3Lipsync.state.dur0 ? window.h3Lipsync.state.len() : null) : RM[AG.target].S.dur };
  if (AG.target === 'actor') ctx.voice = RM.actor.voice() ? 1 : 0;
  if (AG.target === 'long') ctx.long = { sound: RM.long.root._snd, secs: $$('.sc input[type=range]', RM.long.root).map(r => +r.value) };
  try {
    const r = await apiSend('/api/agent/chat', {
      method: 'POST', signal: AG.ctrl.signal, raw: 'response', timeout: 0,
      body: { target: AG.target, ctx, think: AG.think, messages: AG.msgs.slice(0, -1).map(m => ({ role: m.role, content: m.content, images: m.images, frames: m.nframes || 0 })) },
    });
    const reader = r.body.getReader(), dec = new TextDecoder(); let buf = '';
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let k;
      while ((k = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, k).trim(); buf = buf.slice(k + 2);
        if (!line.startsWith('data:')) continue;
        const ev = JSON.parse(line.slice(5));
        if (ev.type === 'status') reply.status = ev.text;
        else if (ev.type === 'skills') reply.skills = ev.names;
        else if (ev.type === 'delta') reply.content += ev.text;
        else if (ev.type === 'error') reply.error = ev.message;
        renderAssistant(el, reply); $('#agMsgs').scrollTop = 1e9;
      }
    }
  } catch (e) { if (e.name !== 'AbortError') reply.error = e.message; }
  AG.busy = false; $('#agSend').classList.remove('busy'); $('#agSend').title = 'Отправить (Enter)'; reply.status = null;
  if (!reply.content && !reply.error) reply.error = 'Остановлено';
  renderAssistant(el, reply); agStatus(); saveChat();
}
$('#agSend').onclick = agSend;
/* ChatGPT-style input: paperclip menu, auto-grow, clear-field cross in the corner */
(() => {
  const box = $('.ag-box'), menu = $('#agMenu'), tin = $('#agIn');
  $('#agPlus').onclick = e => { e.stopPropagation(); menu.hidden = !menu.hidden; };
  scope.listen(document, 'click', e => { if (!menu.hidden && !e.target.closest('.ag-box')) menu.hidden = true; });
  scope.listen(menu, 'click', () => { if (!$('#agMic').classList.contains('rec')) menu.hidden = true; });
  const grow = () => { tin.style.height = 'auto'; tin.style.height = Math.min(220, Math.max(64, tin.scrollHeight + 2)) + 'px'; };
  const updX = () => { $('#agClrField').hidden = !tin.value && !AG.att.length; };
  scope.listen(tin, 'input', grow); scope.listen(tin, 'input', updX);
  $('#agClrField').onclick = () => { tin.value = ''; AG.att = []; renderAtt(); grow(); updX(); tin.focus(); };
  window._agUpdX = updX;   // renderAtt calls this to keep the cross in sync
  grow(); updX();
})();

/* chat history */
async function saveChat() {
  if (!API || !AG.msgs.length) return;
  AG.id = AG.id || Math.random().toString(36).slice(2, 14).replace(/[^a-z0-9]/g, 'x');
  await apiSend(`/api/agent/chats/${AG.id}`, { method: 'PUT',
    body: { target: AG.target, messages: AG.msgs.map(({ files, status, audioFile, ...m }) => m) } }).catch(() => {});
  loadChats();
}
function fmtShort(iso) { return iso ? fmtDate(new Date(iso)) : ''; }
async function loadChats() {
  if (!API) return;
  const list = await apiGet('/api/agent/chats');
  $('#agHist').innerHTML = list.length ? list.map(c => `<div class="ag-h${c.id === AG.id ? ' on' : ''}" data-id="${c.id}">
      <div><b title="${esc(c.title)}">${esc(c.title)}</b><small>${fmtShort(c.updated)}</small></div>
      <button title="Удалить чат">✕</button></div>`).join('')
    : '<div class="ag-hempty">Здесь будут ваши переписки с агентом</div>';
}
function newChat() {
  if (AG.busy) AG.ctrl?.abort();
  AG.id = null; AG.msgs = []; AG.att = []; renderAtt(); renderMsgs(); agCtx(); loadChats(); $('#agIn').focus();
}
async function openChat(id) {
  if (AG.busy) AG.ctrl?.abort();
  const c = await apiGet(`/api/agent/chats/${id}`);
  AG.id = id; AG.msgs = c.messages || []; AG.att = []; renderAtt();
  const b = $(`#agTarget button[data-t="${c.target}"], #agTarget2 button[data-t="${c.target}"]`); if (b) b.click();
  renderMsgs(); agCtx(); loadChats();
}
scope.listen($('#agHist'), 'click', e => {
  const row = e.target.closest('.ag-h'); if (!row) return;
  if (e.target.closest('button')) {
    confirmBox('Удалить чат?', `«${$('b', row).textContent}» будет удалён без возможности восстановления.`, 'Удалить', async () => {
      await apiSend(`/api/agent/chats/${row.dataset.id}`, { method: 'DELETE' }).catch(() => {});
      if (row.dataset.id === AG.id) newChat(); else loadChats();
      toast('Чат удалён');
    });
    return;
  }
  openChat(row.dataset.id);
});
$('#agClear').onclick = () => confirmBox('Очистить всю историю?', 'Все переписки с агентом будут удалены.', 'Очистить', async () => {
  await apiSend('/api/agent/chats', { method: 'DELETE' }).catch(() => {}); newChat(); toast('История очищена');
});
$('#agNew').onclick = newChat;
$('#agNew2').onclick = newChat;

// send a finished prompt into a generator, with the chat's pictures
async function sendPrompt(blk, to) {
  let prompt = $('pre', blk).textContent;
  // pictures of a reopened chat exist only as data URLs; turn them back into files
  const toFile = (u, i) => { const [h, b] = u.split(','); const bin = atob(b); const a = new Uint8Array(bin.length); for (let k = 0; k < bin.length; k++) a[k] = bin.charCodeAt(k); return new File([a], `picture${i + 1}.jpg`, { type: h.slice(5, h.indexOf(';')) }); };
  const perMsg = AG.msgs.map(m => { const ims = m.files || (m.images || []).map(toFile); return { ims, frames: m.nframes || 0 }; });
  const allFiles = perMsg.flatMap(x => x.ims);                       // chat numbering counts ALL images, frames included
  const noFrames = perMsg.flatMap(x => x.ims.slice(0, x.ims.length - x.frames));
  // transfer only the pictures the prompt actually references, in the order they appear, into slots 1, 2, 3…,
  // and renumber the references to match the slots — otherwise «Picture 3» points at the wrong cell (Олег's report)
  const order = [];
  prompt = prompt.replace(/<image(\d+)>|Picture\s+(\d+)|image\s+(\d+)/gi, (m, t, p, f) => {
    const n = +(t || p || f);
    if (n < 1 || n > allFiles.length) return m;
    if (!order.includes(n)) order.push(n);
    return m.replace(/\d+/, String(order.indexOf(n) + 1));
  });
  const files = order.length ? order.map(n => allFiles[n - 1]) : noFrames;
  if (to === 'story' && (AG.storyFrames || (files.length && AG.storyModel !== 'krea'))) {
    imagePanel('qwen').fill({ prompt, kind: 'story', engine: AG.storyFrames && AG.storyModel === 'krea' ? 'flux' : AG.storyModel, frames: AG.storyFrames });
    for (const [i, sel] of ['#qwDrop', '#qwDrop2', '#qwDrop3'].entries()) {
      $(sel + ' .x')?.click();
      if (files[i]) await setDropFile($(sel + ' input'), files[i]);
    }
    to = 'qwen';
  } else if (to === 'plist') {
    imagePanel('krea').fillList(prompt); to = 'krea';
  } else if (to === 'krea' || to === 'story' || to === 'sheet' || to === 'sheet6' || to === 'fluxt' || to === 'qwent') {
    const six = AG.target === 'sheet6' || to === 'sheet6';
    const engine = to === 'fluxt' ? 'flux' : to === 'qwent' ? 'qwen' : (AG.target === 'fluxt' ? 'flux' : AG.target === 'qwent' ? 'qwen' : AG.target === 'story' ? AG.storyModel : 'krea');
    imagePanel('krea').fill({ prompt, engine, aspect: six ? '21:9' : AG.target === 'sheet' || to === 'sheet' || AG.target === 'story' ? '16:9' : undefined }); to = 'krea';
  } else if (to === 'qwen' || to === 'h3e') {
    // H3 Image edit: the pictures go into the editor's slots (the first one is the picture to edit)
    imagePanel('qwen').fill({ prompt, engine: to === 'h3e' ? 'h3img' : 'qwen', kind: 'edit' });
    for (const [i, sel] of ['#qwDrop', '#qwDrop2', '#qwDrop3', '#qwDrop4', '#qwDrop5'].entries()) {
      $(sel + ' .x')?.click();
      if (files[i]) await setDropFile($(sel + ' input'), files[i]);
    }
    to = 'qwen';
  } else if (to === 'h3t') {
    imagePanel('krea').fill({ prompt, engine: 'h3img', aspect: '16:9' }); to = 'krea';
  } else if (to === 'video') {
    $('#vPrompt').value = prompt; $('#vPrompt').oninput({ target: $('#vPrompt') });
    if (files[0]) { $('#dFirst .x')?.click(); await setDropFile($('#dFirst input'), files[0]); }
    if (files[1]) await setDropFile($('#dLast input'), files[1]);
  } else if (to === 'long') {
    const L2 = RM.long, list = []; let cur = null;
    const hasHead = /^\s*SCENE\s*\d+\s*\|/im.test(prompt);   // text before "SCENE 1" (a stray roles line) is not a scene
    for (const line of prompt.split('\n')) {
      if (hasHead && !cur && !/^\s*SCENE\s*\d+\s*\|/i.test(line)) continue;
      const mm = line.match(/^\s*SCENE\s*(\d+)\s*\|\s*(\d+(?:\.\d+)?)\s*(\|\s*new)?\s*$/i);
      if (mm) { cur = { sec: +mm[2], fresh: !!mm[3], text: [] }; list.push(cur); } else if (cur) cur.text.push(line); else if (line.trim()) { cur = { sec: 10, text: [line] }; list.push(cur); }
    }
    const clean = t => t.split('\n').filter(l => !/^\s*(overall_soundscape|non_diegetic_music|integrated_multimodal_description)\s*:/i.test(l)).join('\n').trim();
    L2.root._setScenes(list.map(x => ({ prompt: clean(x.text.join('\n')), sec: x.sec, fresh: x.fresh })).filter(x => x.prompt));
    for (const f of files) {   // existing references (e.g. the last frame of the video being continued) stay; new pictures take the free slots
      const have = $$('label.ref.filled', L2.root).some(r => r._file && r._file.name === f.name && r._file.size === f.size);
      const free = $$('label.ref:not(.filled) input', L2.root)[0];
      if (have || !free) continue;
      await setDropFile(free, f);
    }
  } else {
    const box = to === 'lipsync' ? refsEl : RM[to].root;
    (to === 'lipsync' ? $('#lPrompt') : RM[to].prompt()).value = prompt;
    $$('.ref.filled .x', box).forEach(x => x.click());   // always: <Picture N> numbering of the new prompt must match its pictures
    if (files.length) {
      const slots = $$('.ref input', box);
      for (let i = 0; i < Math.min(files.length, slots.length); i++) await setDropFile(slots[i], files[i]);
    }
  }
  // the song itself goes to Lipsync together with the prompt (the whole clip; trim it there if needed)
  const au = [...AG.msgs].reverse().find(m => m.audioFile)?.audioFile;
  if (to === 'lipsync' && au) await setDropFile($('#aFile'), au);
  const vf = [...AG.msgs].reverse().find(m => m.videoFile)?.videoFile;   // the video shown to the agent goes to the swap form too
  if (to === 'swap' && vf) await setDropFile($('.rm-vids input', RM.swap.root), vf);
  goTo(to);
  const name = { video: '«Генерацию видео»', lipsync: 'Lipsync', refs: '«Референсы»', actor: '«Актёрскую игру»', swap: '«Замену персонажа»', action: '«Экшн-сцены»', long: '«Видеостудия»', krea: '«Изображение по тексту»', qwen: '«Правку изображения»' }[to];
  toast(`Промпт перенесён в ${name}` + (files.length && to !== 'video' && to !== 'krea' ? ' вместе с изображениями' : '') + (to === 'lipsync' ? (au ? ' и аудио — выделите нужный фрагмент' : ' — добавьте аудио') : ''));
}
// the finished prompt can be edited in place; the edit is kept when the chat is redrawn
scope.listen($('#agMsgs'), 'input', e => {
  const pre = e.target.closest?.('pre[data-b]'); if (!pre) return;
  const m = AG.msgs[+pre.closest('.msg')?.dataset.i];
  if (m) { (m.edits = m.edits || {})[pre.dataset.b] = pre.textContent; }
  const n = $('.pb-n', pre.closest('.pblock')); if (n) n.textContent = pre.textContent.length + ' симв.';
});
scope.listen($('#agMsgs'), 'mousedown', e => { if (e.target.closest('[data-paste]')) e.preventDefault(); });  // keep the selection in the prompt
scope.listen($('#agMsgs'), 'click', async e => {
  const blk = e.target.closest('.pblock'); if (!blk) return;
  if (e.target.closest('[data-paste]')) {
    const pre = $('pre', blk), t = await clipText();
    if (!t) { toast('Буфер обмена пуст: сначала скопируйте текст'); return; }
    const sel = getSelection();
    if (!sel.rangeCount || !pre.contains(sel.anchorNode)) { const r = document.createRange(); r.selectNodeContents(pre); r.collapse(false); sel.removeAllRanges(); sel.addRange(r); }
    pre.focus();
    if (!document.execCommand('insertText', false, t)) { pre.textContent += t; }
    pre.dispatchEvent(new Event('input', { bubbles: true }));
    return;
  }
  if (e.target.closest('[data-copy-ru]')) {
    const m = AG.msgs[+blk.closest('.msg')?.dataset.i], t = m?.ru?.[$('pre', blk).dataset.b];
    if (t) { navigator.clipboard?.writeText(t); toast('Русский текст скопирован'); }
    return;
  }
  const ruBtn = e.target.closest('[data-ru]');
  if (ruBtn) {
    const pre = $('pre', blk), m = AG.msgs[+blk.closest('.msg')?.dataset.i], bi = pre.dataset.b;
    if (!m) return;
    if (m.ru?.[bi]) { delete m.ru[bi]; renderAssistant(blk.closest('.msg'), m); return; }   // second click hides it
    ruBtn.disabled = true; ruBtn.textContent = 'Переводим… (первый раз до минуты)';
    try {
      const r = await postJSON('/api/agent/translate', { text: pre.textContent });
      (m.ru = m.ru || {})[bi] = r.text || '(пустой ответ)';
    } catch (err) { toast(err.message); }
    renderAssistant(blk.closest('.msg'), m); return;
  }
  if (e.target.closest('[data-copy]')) { navigator.clipboard?.writeText($('pre', blk).textContent); toast('Промпт скопирован'); return; }
  const to = e.target.closest('[data-send]')?.dataset.send; if (to) sendPrompt(blk, to);
});
scope.listen($('#agMsgs'), 'change', e => {
  const s = e.target.closest('.pb-other'); if (s && s.value) { sendPrompt(s.closest('.pblock'), s.value); s.value = ''; }
});


// model status / download
async function agStatus() {
  if (!API) return;
  const s = AG.status = await apiGet('/api/agent/status');
  const st = s.installing ? ['weak', 'скачивается'] : !s.installed ? ['unsupported', 'не скачана'] : s.running ? ['ok', 'в памяти'] : ['ok', 'готова'];
  $('#agState').className = 'verdict ' + st[0]; $('#agState').textContent = st[1];
  const big = $('#agBig'), curName = s.custom ? s.title : ((s.variants || []).find(o => o.current) || {}).title || s.title;
  big.className = 'agbig ' + (s.installing ? 'wait' : s.installed ? 'ok' : 'bad');
  big.innerHTML = `<div class="t">${s.installing ? '⏳ Скачивается…' : s.installed ? '✓ ГОТОВО' : '✗ Не скачана'}</div>`
    + `<div class="n"><b>${esc(curName || '')}</b>${s.installed ? (s.running ? ' — работает сейчас' : ' — запустится, когда нужна') : s.installing ? '' : ' — нажмите «Скачать» ниже'}</div>`;
  $('#agModel').innerHTML = s.custom
    ? `<b style="color:var(--text)">${esc(s.title)}</b> — ваша модель<br><span style="font-family:var(--mono);font-size:11px;word-break:break-all">${esc(s.custom.model)}</span><br>`
      + (s.vision ? 'Изображения видит (найден mmproj). ' : '<span class="warn-t">Файла mmproj нет — изображения агент не увидит.</span> ')
      + (s.installed ? '' : 'Осталось скачать движок llama.cpp (37 МБ).')
    : `<b style="color:var(--text)">${esc(s.title)}</b> — бесплатная локальная модель (Apache 2.0), понимает русский и изображения. `
      + (s.installed ? 'Пока агент работает, генерация видео ждёт: обе модели вместе не помещаются в видеопамять — приложение переключает их само.' : `Нужно скачать один раз: ~${(s.size / 1e9).toFixed(1)} ГБ.`);
  const nReady = (s.variants || []).filter(o => o.have).length + (s.my_model ? 1 : 0);
  $('#agVarBox > summary').textContent = `Выбор моделей агента (готово: ${nReady} из ${(s.variants || []).length + (s.my_model ? 1 : 0)})`;
  $('#agVariants').innerHTML = (s.my_model ? `<button class="agv${s.my_model.current ? ' on' : ''}" type="button" data-my="1"><b>Моя модель: ${esc(s.my_model.title)}</b><span class="tg">ваша</span><span class="rd ok">✓ ГОТОВО${s.my_model.current ? ' · ВЫБРАНА' : ''}</span></button>` : '')
    + (s.variants || []).map(o => `<button class="agv${o.current ? ' on' : ''}" type="button" data-v="${o.key}"><b>${esc(o.title)}</b>`
      + `<span class="tg${o.uncensored ? ' unc' : ''}">${o.uncensored ? 'без цензуры' : 'обычная'}</span>`
      + `<span class="rd ${o.have ? 'ok' : 'no'}">${o.have ? '✓ ГОТОВО' : `Нужно скачать · ${(o.size / 1e9).toFixed(1)} ГБ`}${o.current ? ' · ВЫБРАНА' : ''}</span>`
      + (o.min_vram && !o.fits ? `<small>⚠ нужно от ${o.min_vram} ГБ видеопамяти: у вас может быть медленно</small>` : '') + `</button>`).join('')
    + ((s.variants || []).some(o => o.current && o.uncensored) ? `<div class="hint" style="margin-top:8px">Эта модель не отказывается писать любые описания. Вы отвечаете за содержание: не используйте её для запрещённого и для людей без их согласия. Сам MiniMax H3 часть запросов всё равно может отклонить.</div>` : '');
  const needModel = !s.installed;
  $('#agInstall').hidden = s.installing || (s.custom && !s.has_model) || (!needModel && s.speech_ready);
  $('#agInstall').textContent = !needModel ? `Докачать распознавание речи (~${Math.round(s.speech_size / 1e6)} МБ)`
    : s.custom ? 'Скачать движок llama.cpp (37 МБ) и распознавание речи' : `Скачать модель (~${((s.size + s.speech_size) / 1e9).toFixed(1)} ГБ, вместе с распознаванием речи)`;
  $('#agFromLip').hidden = !window.h3Lipsync.state.dur0;
  const sp = s.speech || {};
  $('#agWhReset').hidden = !sp.custom;
  if (sp.custom && !$('#agWhDir').value) $('#agWhDir').value = sp.custom;
  $('#agWhInfo').innerHTML = sp.ready
    ? `<span class="ok-t">✓ Программа и модель найдены${sp.model ? ': ' + esc(sp.model) : ''}</span>`
    : sp.custom ? `<span class="warn-t">Не найдено: ${esc(sp.missing.join(', '))} — нажмите «Докачать» в карточке выше, скачается только это.</span>` : '';
  $('#agModel').insertAdjacentHTML('beforeend', s.speech_ready
    ? '<br><span class="ok-t">✓ Распознавание речи готово: можно приложить аудио и говорить в микрофон.</span>'
    : '<br><span class="warn-t">Распознавание речи не скачано: без него нельзя приложить аудио и говорить голосом.</span>');
  $('#agOwnReset').hidden = !s.custom;
  // own ComfyUI: the folder is always shown before a download, so it can be changed; an empty field keeps the current one
  $('#agDirWrap').hidden = s.managed || s.installed || s.installing;
  if (s.dir_chosen && !s.managed) {
    const m = /^(.*)[\\/]MinimaxH3_assistant$/.exec(s.dir || '');
    if (m && !$('#agDir').value) $('#agDir').value = m[1];
    $('#agDirHint').innerHTML = `Сейчас скачается в: <b style="word-break:break-all">${esc(s.dir)}</b>. Чтобы скачать в другое место, поменяйте папку (внутри создастся MinimaxH3_assistant).`;
  }
  $('#agModel').insertAdjacentHTML('beforeend', s.dir_chosen || s.installed ? `<br><span style="font-size:11px;word-break:break-all">Папка помощника: ${esc(s.dir)}</span>` : '');
  const cands = s.candidates || [];
  $('#agOwnPickWrap').hidden = cands.length < 2;
  $('#agOwnPick').innerHTML = cands.map(c =>
    `<option value="${esc(c.path)}" ${c.current ? 'selected' : ''}>${esc(c.name)} · ${c.gb} ГБ${c.fits ? '' : ' · ⚠ может не поместиться в видеопамять'}</option>`).join('');
  if (s.custom && cands.some(c => c.current && !c.fits)) {
    $('#agModel').insertAdjacentHTML('beforeend', `<br><span class="warn-t">Эта модель крупная для вашей видеокарты (${s.vram} ГБ): она может работать медленно или не запуститься. Лучше Gemma 4 12B Q4 (~7 ГБ).</span>`);
  }
  if (s.custom && !$('#agOwnDir').value) $('#agOwnDir').value = s.custom.model.replace(/[\\/][^\\/]+$/, '');
  $('#agDl').hidden = !s.installing;
  renderSkills(s.skills);
}
$('#agVariants').onclick = async e => {
  const b = e.target.closest('.agv'); if (!b || b.classList.contains('on')) return;
  if (b.dataset.my) { try { await postJSON('/api/agent/model', { restore: true }); agStatus(); } catch (err) { toast(err.message); } return; }
  try { await postJSON('/api/agent/variant', { variant: b.dataset.v }); agStatus(); } catch (err) { toast(err.message); }
};
$('#agOwnUse').onclick = async () => {
  try { await postJSON('/api/agent/model', { dir: $('#agOwnDir').value }); toast('Модель подключена'); agStatus(); }
  catch (e) { toast(e.message); }
};
$('#agWhUse').onclick = async () => {
  try { await postJSON('/api/speech/folder', { dir: $('#agWhDir').value }); toast('Папка whisper подключена'); agStatus(); }
  catch (e) { toast(e.message); }
};
$('#agWhReset').onclick = async () => { await postJSON('/api/speech/folder', { reset: true }); $('#agWhDir').value = ''; toast('Снова встроенный whisper'); agStatus(); };
$('#agOwnPick').onchange = async e => {
  try { await postJSON('/api/agent/model', { dir: $('#agOwnDir').value, model: e.target.value }); toast('Модель выбрана'); agStatus(); }
  catch (err) { toast(err.message); }
};
$('#agOwnReset').onclick = async () => { await postJSON('/api/agent/model', { reset: true }); $('#agOwnDir').value = ''; toast('Снова встроенная Gemma'); agStatus(); };
$('#agInstall').onclick = async () => {
  const dir = $('#agDir').value.trim(), s = AG.status || {};
  if (!s.managed && !s.installed && !s.dir_chosen && !dir) { toast('Сначала укажите папку, куда скачать'); $('#agDir').focus(); return; }
  try { await postJSON('/api/agent/install', { dir }); agStatus(); } catch (e) { toast(e.message); }
};
$('#agDlStop').onclick = () => postJSON('/api/agent/install/cancel');
const agProg = { t: 0, b: 0 };
function onAgentSetup(ev) {
  $('#agDl').hidden = ev.state !== 'progress'; $('#agInstall').hidden = true;
  if (ev.state === 'progress' && ev.done != null && ev.total) {
    $('#agBar').style.width = (ev.done / ev.total * 100) + '%';
    const now = performance.now(); let sp = '';
    if (agProg.t && now > agProg.t && ev.done >= agProg.b) sp = ` · ${((ev.done - agProg.b) / ((now - agProg.t) / 1000) / 1e6).toFixed(1)} МБ/с`;
    agProg.t = now; agProg.b = ev.done;
    $('#agDlTxt').textContent = `${ev.label.split(': ').pop()} — ${GB(ev.done)} из ${GB(ev.total)}${sp}`;
  } else if (ev.state !== 'progress') { if (ev.state !== 'done') toast(ev.label); agStatus(); }
}

// skills
const SK_MODES = { always: 'Всегда', auto: 'Авто', off: 'Выкл' };
const AG_SK_IMG = { 'storyboard': 'Раскадровка', 'krea-2': 'Изображение по тексту', 'flux2-klein': 'Изображение: FLUX.2 klein', 'qwen-image-2-1-director-prompt': 'Правка изображения', 'karta-personazha': 'Лист персонажа', 'minimax-h3-image': 'MiniMax H3 Image' };
function renderSkills(list) {
  $('#skCnt').textContent = `(${list.length})`;
  $('#skList').innerHTML = list.map(s => `<div class="sk" data-id="${esc(s.id)}">
    <div class="sk-top"><b title="${esc(s.name)}">${esc(s.name)}</b>
      ${AG_SK_IMG[s.id] ? `<span class="hint" title="Подключается сама, когда выбрана кнопка «${AG_SK_IMG[s.id]}» в «Промпт для»">${AG_SK_IMG[s.id]}</span>` : `<select>${Object.entries(SK_MODES).map(([k, v]) => `<option value="${k}" ${k === s.mode ? 'selected' : ''}>${v}</option>`).join('')}</select>`}
      ${s.builtin ? '' : '<button class="x" title="Удалить скилл">✕</button>'}</div>
    <p title="${esc(s.description)}">${esc(s.description)}</p>
    ${s.mode === 'auto' && !AG_SK_IMG[s.id] ? `<input value="${esc(s.keywords.join(', '))}" placeholder="слова через запятую">` : ''}
    <small class="hint">${s.refs.length ? `+ справочники: ${s.refs.length}` : ''}</small></div>`).join('');
}
function skShow(on) { $('#skList').hidden = !on; $('#skArr').textContent = on ? '▾' : '▸'; try { localStorage.setItem('h3-sk-open', on ? '1' : ''); } catch {} }
$('#skToggle').onclick = () => skShow($('#skList').hidden);
try { skShow(localStorage.getItem('h3-sk-open') === '1'); } catch {}
scope.listen($('#skList'), 'change', async e => {
  const card = e.target.closest('.sk'); if (!card) return;
  const body = e.target.tagName === 'SELECT' ? { mode: e.target.value } : { keywords: e.target.value.split(',') };
  await apiSend(`/api/agent/skills/${encodeURIComponent(card.dataset.id)}`, { method: 'PATCH', body }).catch(() => {});
  agStatus();
});
scope.listen($('#skList'), 'click', async e => {
  if (!e.target.closest('.x')) return;
  const card = e.target.closest('.sk');
  await apiSend(`/api/agent/skills/${encodeURIComponent(card.dataset.id)}`, { method: 'DELETE' }).catch(() => {});
  toast('Скилл удалён'); agStatus();
});
$('#skUp').onchange = async e => {
  const fd = new FormData();
  for (const f of e.target.files) if (/\.(md|txt)$/i.test(f.name)) fd.append('f', f, f.webkitRelativePath || f.name);
  e.target.value = '';
  try { const j = await apiSend('/api/agent/skills', { method: 'POST', body: fd }); toast('Скилл загружен'); agStatus(); }
  catch (err) { toast(err.message); }
};
/* every visit: the model's state, the target line and the chat list are read again; the section
   used to do it from a click listener on its navigation button */
onEnter = () => { agStatus(); agCtx(); loadChats(); };
  onLeave = () => window.h3Lifecycle.pauseMedia($('#view-agent'));
  onDispose = () => {
    if (AG.busy) AG.ctrl?.abort();
    if (micTimer) { clearInterval(micTimer); micTimer = null; }
    if (micRec) { try { micRec.stop(); } catch {} micRec = null; }
    const modal = $('#agLibM');
    if (modal) modal.remove();
    delete window._agUpdX;
    onLeave();
  };

  api = {
    attachFile: agAttachFile,
    setup: onAgentSetup,
    status: agStatus,
    ctx: agCtx,
    state: () => AG,
  };
}

  /* the page's two doors into the chat: a picture from the Library and the model's download progress */
  window.h3Agent = {
    async attachFile(file) {
      window.h3Modes.ready("agent");
      if (api) await api.attachFile(file);
    },
    setup(ev) {
      if (!api) window.h3Modes.ready("agent");
      if (api) api.setup(ev);
    },
    get state() { return api ? api.state() : null; },
    status() { if (api) api.status(); },
    ctx() { if (api) api.ctx(); },
  };

  window.h3Modes.register("agent", {
    mount: build,
    enter: () => onEnter(),
    leave: () => onLeave(),
    dispose: () => onDispose(),
  });
})();