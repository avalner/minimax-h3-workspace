/* LoRA: what a file means, which combinations fight, the catalog and the download.
 *
 * A mode module: the section is empty in the page and is built on the first visit, with its own
 * library list, its conflict notes and the search of Civitai and Hugging Face. The list of LoRAs is
 * read from ComfyUI on every visit (enter), the download progress comes through the page-wide
 * `window.onLoraDownload` hook the module installs while the window lives.
 *
 * Loaded before the page; nothing here runs until the section is opened.
 */

(function () {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  let root = null;
  let onEnter = () => {}, onLeave = () => {}, onDispose = () => {};
function build(scope) {
  root = $('#view-loras');
  if (!root) return;
  const LR = { items: [], conflicts: [], custom: [], src: 'civitai', busy: false, dl: null, dir: '' };
  root.innerHTML = `
    <div class="head"><div><h1>LoRA</h1><p>Что за файл, какие у него слова-ключи и с каким весом включать. Здесь же поиск и скачивание с Civitai и Hugging Face — файл ляжет прямо в папку LoRA вашего ComfyUI.</p></div></div>
    <div class="grid"><div class="col">
      <div class="card"><div class="card-h"><h3><span class="n">1</span>Проверка сочетаний</h3><span class="hint lr-cf-n"></span></div><div class="lr-cf"></div></div>
      <div class="card"><div class="card-h"><h3><span class="n">2</span>Мои LoRA</h3><span class="hint">включённая LoRA подмешивается во все режимы</span></div><div class="lr-mine"></div><div class="hint lr-mine-h" style="margin-top:8px"></div></div>
      <div class="card"><div class="card-h"><h3>Каталоги</h3></div>
        <div class="fld"><label>Токен Civitai <span class="hint">(необязательно: часть моделей без него не скачивается)</span></label><div class="path on"><input class="lr-tok-civ" type="password" placeholder="вставьте токен" autocomplete="off"></div></div>
        <div class="fld"><label>Токен Hugging Face <span class="hint">(для закрытых репозиториев)</span></label><div class="path on"><input class="lr-tok-hf" type="password" placeholder="вставьте токен" autocomplete="off"></div></div>
        <div class="wz-go"><span class="hint lr-tok-msg"></span><button class="btn lr-tok-save" type="button">Сохранить токены</button><button class="btn lr-folder" type="button">Папка LoRA</button></div>
      </div>
    </div><div class="preview">
      <div class="card"><div class="card-h"><h3><span class="n">3</span>Каталог</h3><span class="mt-eng-tabs lr-src"><button type="button" class="btn mini on" data-src="civitai">Civitai</button><button type="button" class="btn mini" data-src="hf">Hugging Face</button></span></div>
        <div class="lr-qrow"><div class="path on"><input class="lr-q" placeholder="Что искать: film grain, realism, camera motion"><button class="btn pri lr-find" type="button">Искать</button></div></div>
        <div class="hint lr-dl" style="margin-top:8px"></div>
        <div class="lr-res"></div>
      </div>
    </div></div>`;

  const fileOf = it => String(it && it.file || '');
  const customOf = f => LR.custom.find(x => x.file === f);
  const recOf = f => {
    const c = customOf(f);
    return c && typeof c.strength === 'number' ? c.strength : 0.8;
  };

  async function load() {
    const box = $('.lr-mine', root);
    try {
      const reg = await apiGet('/api/loras/registry');
      LR.items = reg.items || []; LR.conflicts = reg.conflicts || []; LR.dir = reg.dir || '';
    } catch { LR.items = []; LR.conflicts = []; }
    try {
      const tok = await apiGet('/api/loras/tokens');
      $('.lr-tok-civ', root).placeholder = tok.civitai ? 'токен сохранён (введите новый, чтобы заменить)' : 'вставьте токен';
      $('.lr-tok-hf', root).placeholder = tok.hf ? 'токен сохранён (введите новый, чтобы заменить)' : 'вставьте токен';
    } catch {}
    try {
      const c = await apiGet('/api/loras/custom');
      LR.custom = c.items || [];
    } catch {
      LR.custom = LR.items.filter(it => it.mine).map(it => ({ file: it.file, strength: it.recommended_weight || 0.8, on: true }));
    }
    $('.lr-mine-h', root).textContent = LR.dir ? `Папка LoRA: ${LR.dir}` : 'Папка models вашего ComfyUI не выбрана: укажите её в разделе «Модели».';
    renderMine(); renderConflicts();
  }

  function renderMine() {
    const box = $('.lr-mine', root);
    const known = new Map(LR.items.map(it => [it.file, it]));
    const files = LR.items.map(it => it.file);
    for (const c of LR.custom) if (!files.includes(c.file)) files.push(c.file);
    if (!files.length) { box.innerHTML = '<div class="lr-empty">ComfyUI не видит ни одной LoRA: положите файл .safetensors в папку LoRA и нажмите «Обновить» в «Моделях», либо скачайте из каталога справа.</div>'; return; }
    files.sort((a, b) => {   // enabled ones first, then by name
      const onA = (customOf(a) || {}).on !== false && !!customOf(a), onB = (customOf(b) || {}).on !== false && !!customOf(b);
      if (onA !== onB) return onA ? -1 : 1;
      return (known.get(a)?.name || a).localeCompare(known.get(b)?.name || b, 'ru');
    });
    box.innerHTML = files.map((f, i) => {
      const it = known.get(f) || { file: f, name: f, category: '', desc: '', triggers: [], recommended_weight: 0.8 };
      const c = customOf(f) || { file: f, strength: it.recommended_weight || 0.8, on: false };
      const on = c.on !== false;
      return `<div class="lr-item" data-file="${esc(f)}">
        <div class="lr-top"><span><span class="lr-name">${esc(it.name || f)}</span> <span class="btn mini lr-sw${on ? ' on' : ''}" role="button" title="Включить или выключить эту LoRA">${on ? 'включена' : 'выключена'}</span></span><span class="hint">${esc(it.category || '')}</span></div>
        <div class="lr-file">${esc(f)}</div>
        <div class="lr-row"><input type="range" class="lr-w" min="0" max="2" step="0.05" value="${c.strength ?? 0.8}"><b class="lr-wv">${(+c.strength || 0).toFixed(2)}</b></div>
        <div class="lr-tags">${(it.triggers || []).map(t => `<span class="lr-chip">${esc(t)}</span>`).join('') || '<span class="hint">слова-ключи неизвестны: добавьте их ниже, чтобы агент подставлял их в промпт</span>'}</div>
        <details class="lr-meta"><summary>Слова-ключи, название и вес</summary>
          <div class="fld"><label>Название</label><input class="lr-m-name" value="${esc(it.name || '')}"></div>
          <div class="fld"><label>Слова-ключи <span class="hint">(через запятую)</span></label><input class="lr-m-trig" value="${esc((it.triggers || []).join(', '))}"></div>
          <div class="fld"><label>Рекомендуемый вес</label><input class="lr-m-rec" type="number" min="0" max="2" step="0.05" value="${(+it.recommended_weight || 0.8).toFixed(2)}"></div>
          <div class="wz-go"><span class="hint lr-m-msg"></span><button class="btn lr-m-save" type="button">Сохранить</button></div>
        </details>
      </div>`;
    }).join('');
  }

  function renderConflicts() {
    const box = $('.lr-cf', root), n = $('.lr-cf-n', root);
    const list = LR.conflicts || [];
    n.textContent = list.length ? `${list.length} замечани${list.length === 1 ? 'е' : list.length < 5 ? 'я' : 'й'}` : '';
    box.innerHTML = list.length ? list.map(c => `<div class="lr-cf ${esc(c.level || 'caution')}"><b>${esc(c.title || '')}</b>${esc(c.desc || '')}${(c.culprits || []).length ? `<div class="hint" style="margin-top:4px">${(c.culprits || []).map(esc).join(', ')}</div>` : ''}</div>`).join('')
      : '<div class="hint">Включённые LoRA не мешают друг другу.</div>';
  }

  async function saveCustom() {
    try {
      await apiPost('/api/loras/custom', { items: LR.custom });
      const cf = await apiGet('/api/loras/conflicts');
      LR.conflicts = cf.conflicts || []; renderConflicts();
    } catch (e) { toast('Не удалось сохранить список LoRA: ' + e.message); }
  }

  scope.listen(root, 'click', async e => {
    const item = e.target.closest('.lr-item');
    if (item) {
      const f = item.dataset.file;
      if (e.target.closest('.lr-sw')) {
        const c = customOf(f) || (LR.custom.push({ file: f, strength: recOf(f), on: false }), LR.custom[LR.custom.length - 1]);
        c.on = !(c.on !== false);
        await saveCustom(); renderMine(); return;
      }
      if (e.target.closest('.lr-m-save')) {
        const msg = $('.lr-m-msg', item);
        msg.textContent = 'Сохраняем…';
        try {
          await apiPost('/api/loras/meta', {
            file: f, name: $('.lr-m-name', item).value, triggers: $('.lr-m-trig', item).value,
            recommended_weight: +$('.lr-m-rec', item).value || 0.8 });
          msg.textContent = 'Сохранено';
          await load();
        } catch (err) { msg.textContent = 'Не удалось: ' + err.message; }
        return;
      }
      return;
    }
    if (e.target.closest('.lr-find')) { find(); return; }
    if (e.target.closest('.lr-folder')) { apiPost('/api/loras/open-folder', {}).then(j => { if (j.error) toast(j.error); }).catch(() => {}); return; }
    if (e.target.closest('.lr-tok-save')) {
      const msg = $('.lr-tok-msg', root);
      const body = { civitai: $('.lr-tok-civ', root).value.trim(), hf: $('.lr-tok-hf', root).value.trim() };
      try {
        await apiPost('/api/loras/tokens', body);
        msg.textContent = 'Токены сохранены';
        $('.lr-tok-civ', root).value = ''; $('.lr-tok-hf', root).value = '';
      } catch (err) { msg.textContent = 'Не удалось: ' + err.message; }
      return;
    }
    const src = e.target.closest('[data-src]');
    if (src) {
      LR.src = src.dataset.src;
      $$('[data-src]', root).forEach(b => b.classList.toggle('on', b === src));
      find();
    }
  });

  scope.listen(root, 'input', e => {
    const item = e.target.closest('.lr-item');
    if (!item || !e.target.classList.contains('lr-w')) return;
    const f = item.dataset.file;
    $('.lr-wv', item).textContent = (+e.target.value).toFixed(2);
  });
  scope.listen(root, 'change', async e => {
    const item = e.target.closest('.lr-item');
    if (!item || !e.target.classList.contains('lr-w')) return;
    const f = item.dataset.file;
    const c = customOf(f) || (LR.custom.push({ file: f, strength: 0.8, on: false }), LR.custom[LR.custom.length - 1]);
    c.strength = Math.round((+e.target.value) * 100) / 100;
    await saveCustom(); renderMine();
  });
  scope.listen($('.lr-q', root), 'keydown', e => { if (e.key === 'Enter') find(); });

  async function find() {
    const box = $('.lr-res', root), q = $('.lr-q', root).value.trim();
    box.innerHTML = '<div class="hint">Ищем…</div>';
    const url = LR.src === 'civitai' ? `/api/civitai/search?q=${encodeURIComponent(q)}` : `/api/hf/search?q=${encodeURIComponent(q)}`;
    let data;
    try { data = await apiGet(url); }
    catch { box.innerHTML = '<div class="hint">Нет связи с каталогом: проверьте интернет.</div>'; return; }
    const items = data.items || [];
    if (!items.length) {
      box.innerHTML = `<div class="hint">${esc(data.error || 'Ничего не нашлось: попробуйте другие слова.')}</div>`
        + (data.needsToken ? '<div class="hint">Для этого каталога нужен токен: вставьте его слева.</div>' : '')
        + '<div class="hint">Подсказка: LoRA для H3 ищутся по тегам вроде «minimax h3» или по названию эффекта.</div>';
      return;
    }
    box.innerHTML = items.map((it, i) => {
      const v = (it.versions || [])[0] || {};
      const img = (it.images || [])[0] || '';
      const trig = (v.trainedWords || []).slice(0, 6).join(', ');
      return `<div class="lr-card"><img src="${esc(img)}" alt="" loading="lazy" onerror="this.style.display='none'">
        <div class="lr-b"><div class="lr-n">${esc(it.name || '')}</div>
          <div class="lr-m">${esc(it.creator || '')}${v.sizeMB ? ' · ' + v.sizeMB + ' МБ' : ''}${v.baseModel ? ' · ' + esc(v.baseModel) : ''}</div>
          <div class="lr-t">${esc(trig || (it.description || '').slice(0, 140))}</div>
          <button class="btn pri lr-dl" type="button" data-i="${i}">Скачать</button></div></div>`;
    }).join('');
    $$('.lr-dl', box).forEach(b => b.onclick = () => download(items[+b.dataset.i]));
  }

  async function download(it) {
    const v = (it.versions || [])[0] || {};
    if (!v.downloadUrl) { toast('У этой записи нет файла для скачивания'); return; }
    const msg = $('.lr-dl', root);
    msg.textContent = 'Скачиваем…';
    try {
      const j = await apiPost('/api/loras/download', { url: v.downloadUrl, filename: v.filename });
      LR.dl = j.name;
      msg.textContent = `Скачиваем ${j.name}: 0%`;
    } catch (e) {
      msg.textContent = '';
      toast('Не удалось скачать: ' + e.message);
    }
  }

  window.onLoraDownload = m => {
    const msg = $('.lr-dl', root);
    if (!msg) return;
    if (m.error) { msg.textContent = ''; toast('Скачивание не удалось: ' + m.error); return; }
    if (m.cancelled) { msg.textContent = ''; toast('Скачивание остановлено'); return; }
    if (m.finished) { msg.textContent = ''; toast('LoRA скачана: ' + (m.name || '') + '. Она появится в папке LoRA — нажмите «Обновить список» в «Моделях».'); load(); return; }
    msg.textContent = `Скачиваем ${m.name || ''}: ${m.percent || 0}%${m.speed ? ' · ' + m.speed : ''}`;
  };

  // the list of LoRAs comes from ComfyUI: refresh it every time the section is opened
  onEnter = () => load();
  onLeave = () => window.h3Lifecycle.pauseMedia(root);
  onDispose = () => {
    onLeave();
    delete window.onLoraDownload;
    if (root) root.innerHTML = '';
  };
}

  window.h3Modes.register("loras", {
    mount: build,
    enter: () => onEnter(),
    leave: () => onLeave(),
    dispose: () => onDispose(),
  });
})();