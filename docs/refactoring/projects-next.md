# Проекты: что сделано и что осталось (передача в новый чат)

Ветка `feature/projects` (запушена), `main` = `05b18e2`. Схема базы — **4**. Проверки на момент
передачи: `tests/run.py` — **37/37**, браузерная группа `ui` проходит.

## Что уже сделано

Коммиты `6d10507 … dfa85d0`:

- **Миграция 0003**: таблица `projects`, проект «Основное», колонка `project` в `library` с
  переносом старых записей; `project` в payload записей и черновиков.
- **Активный проект** в `config.json` (`active_project`), откат к «Основному» при неизвестном id.
  `App.projects()/active_project()/set_active_project()/create_project()`, маршруты
  `GET /api/projects`, `POST /api/projects`, `POST /api/projects/active`.
- **Захват проекта при отправке** (не при готовности): `App.submission_meta` вызывается из
  `attach_job`, `queue_cloud`, `begin_submission`, `hold_preview`; монтаж FFmpeg кладёт проект в
  item. Так проект получают все режимы.
- **Агент**: `GET /api/agent/v1/projects`, необязательный `project` в `POST /jobs`
  (`400 PROJECT_UNKNOWN`), `project` в ответах задания/Библиотеки, фильтр `?project=` у `/library`.
- **Интерфейс**: переключатель проекта в шапке (рядом с полосой разделов, `.tb-row1`), переключатель
  «Этот проект / Все проекты», фильтр `projectOk()` в Библиотеке, «Прошлых клипах», истории
  картинок, своих workflow, списке монтажа, ленте и просмотрщике. Бейдж «Библиотека» — «3 +2».
- **Удаление клипов** прямо из «Прошлых клипов»: крестик на превьюшке и кнопка «Удалить» под
  показанным клипом. Черновик — `DELETE /api/preview/{id}` (запись + media + файлы в корзину);
  клип Библиотеки — `DELETE /api/library/{id}`. Поповер у курсора (заголовок + две кнопки),
  учитывает зум интерфейса (`document.documentElement.style.zoom`), закрывается Esc/кликом мимо.
- **Этапы в базе** (миграция 0004): таблица `stages` (`id, project, mode, auto, created_at,
  payload`), `GET/POST /api/stages`, `DELETE /api/stages/{id}`; «было до открытия» заменяется,
  лимит 15 на раздел. Старые этапы из `localStorage` переносятся при первом запуске.
- **Файлы этапов на сервере**: `POST /api/stages/{id}/file?key=video/first` (multipart, потоком),
  `GET /api/stages/{id}/file?key=…`, хранение `DATA_DIR/stages/<id>/<mode>/<name>`, имена в
  `names`. Удаление/лимит/замена auto удаляют папку; старые этапы догружают файлы из IndexedDB при
  открытии.

## Границы: что работает не во всех режимах

| Возможность | Где работает |
|---|---|
| Проект у результата, фильтр Библиотеки, бейдж | все режимы |
| Фильтр «Прошлых клипов» | video, lipsync, refs, actor, swap, action (панель `previewPanel`); long — свои карточки сцен |
| Удаление клипа из списка | там же, где «Прошлые клипы»; **нет** в картинках, своих workflow, монтаже |
| «Этапы» в разделе | video, lipsync, refs, actor, action, long; **нет** у swap, custom, картинок, монтажа |
| Возврат клипа при открытии этапа | **только video** |
| Файлы этапа на сервере | video/lipsync/refs/actor/action — общий механизм; long — `/api/long/stage` (refs + state) |

## Пять задач

### 1. Удаление в остальных списках (картинки, свои workflow, монтаж)

- `app/ui/js/modes/pictures.js`, `refresh()`: на превьюшку добавить `<i class="cw-x" data-del="…">`
  (как в `components.js`), в `hist.onclick` — `window.deleteClipItem(it, { x: e.clientX,
  y: e.clientY })`. Если удалён показанный (`last.id === it.id`), показать следующий или очистить.
- `app/ui/js/modes/custom.js`, `cwHist()`: то же для `#cwHist .cw-th`; если удалён `CW.shown` —
  после удаления `cwShowLast()`.
- `app/ui/js/modes/montage.js`, `mt-card`: добавить отдельную кнопку удаления (не путать с
  `data-hide` — «убрать из списка»). `window.deleteClipItem(it, …)`; список перерисуется сам, а
  `MT.ids` подчистится в `render()` по `get(id)`.
- Проверить: `dropDraft`/`dropLib` уже обновляют `IMG_PANELS`, `DRAFT_HOOKS`, лоток; удаление
  библиотечного клипа убирает его и из Библиотеки.
- Тест: UI smoke с засеянной БД (как делали для видео) либо точечная проверка в браузере.

### 2. Этапы для swap

- `app/ui/index.html`: добавить `'swap'` в `BM.MODES`.
- Проверить, что `memStates()['swap']` и `memSlots()` уже покрывают swap (RM содержит swap, слоты
  `swap/img*`, `swap/vid*`); `#view-swap` существует, бар этапов создаётся циклом.
- Тест: в UI smoke открыть «Замена персонажа», сохранить этап, увидеть его в `.st-n`, удалить.

### 3. Возврат клипа при открытии этапа (lipsync и refs-семейство)

- `app/ui/js/modes/lipsync.js`: в состояние добавить `shownId: null`; в опции `previewPanel` —
  `onShow: it => { L.shownId = it.id; }`; экспортировать `showItem(id)` (как `window.h3Video`).
- `app/ui/js/modes/refs.js`: в опции панели `onShow: it => { S.shownId = it.id; }`; панель уже
  лежит в `RM[m].prev`, поэтому `window.RM[mode].prev.show(id)` доступен.
- `bmOpen` в `index.html`: после `apply()` — `video` как сейчас; `lipsync` —
  `window.h3Lipsync?.showItem?.(b.s?.shownId)`; остальные — `window.RM?.[b.mode]?.prev?.show?.(b.s?.shownId)`.
- `previewPanel` уже умеет `update.show(id)` с откатом на самый свежий клип, если сохранённый удалён.

### 4. Видеостудия: фильм принадлежит проекту

Сейчас `LONG_DIR/<run>/project.json` не знает про проект, а студия грузит «последний» run — можно
продолжить фильм другого проекта.

- `app/routes/generate.py`, `h_long_project`: при POST писать `project` из тела или активный;
  при GET принимать `?project=<id>` и отдавать самый свежий run этого проекта (скан
  `LONG_DIR/*/project.json`), `?last=1` оставить для старых.
- `app/ui/js/modes/refs.js`, `saveNow()`: добавить `project: window.h3Projects?.active?.()` в
  `proj`; стартовую загрузку (`setTimeout` в конце секции long) делать по активному проекту, а не
  `h3-long-last`/`?last=1` (их — только как fallback для старых записей).
- Переключение проекта (`selectProject` в `index.html`) должно перезагрузить студию: вызвать
  `window.h3LongProjectChanged?.()`, который делает `root._flush()` и открывает run этого проекта
  или начинает новый (`root._run = новый id`, сцены очищены).
- Этапы уже переключают проект, и `bmOpen` вызывает `selectProject` до `_openStage` — проверить,
  что порядок не ломает восстановление.

### 5. Проект в очереди

- `ui_snapshot` (`app/application.py`): в элемент `jobs` добавить `project` (из `job["meta"]`) и
  `project_name`, если удобно.
- `app/ui/js/state.js`: поле `project` в `FIELDS`, в `make`/`resync`/`addJob`; `components.js` и
  `index.html` при `qAdd`/`addJob` передавать проект из `meta`.
- `index.html`: в строке очереди — маленький бейдж проекта; фильтр не обязателен, очередь
  остаётся общей (это очередь машины).

## Правила, которые нельзя сломать

- Проект ставится **при отправке**: `attach_job` / `begin_submission` / `queue_cloud` /
  `hold_preview` / монтаж. Не переносить на момент готовности.
- Этапы: ключи слотов проверяет `KEY_RE` в `routes/stages.py`; файл льётся после `postStage`,
  локальная копия `bm/<id>/…` удаляется только после успешной загрузки.
- Поповер удаления считается в координатах раскладки (делится на зум) — иначе уезжает.
- Удаление: черновик → `/api/preview/{id}`, библиотечный → `/api/library/{id}`; файлы в корзину,
  при неудаче запись остаётся.
- `projectOk()` — единственный фильтр списков; после смены проекта вызывать
  `refreshProjectLists()`.

## Проверки и как их гонять

```powershell
$env:PYTHONPATH = 'I:\Video Generation\ComfyUI_windows_portable\python_embeded\Lib\site-packages'
& '_test\venv\Scripts\python.exe' tests/run.py            # всё, 37 проверок
& '_test\venv\Scripts\python.exe' tests/run.py --only ui  # только окно в браузере
```

- `tests/unit/test_projects.py`, `test_stages.py`, `test_draft_delete.py` — новые проверки фичи.
- UI smoke (`tests/ui_smoke.py`) уже проверяет: шапку и переключение проекта, создание проекта,
  поповер удаления и его позицию при зуме 110 %, удаление клипа Библиотеки, этап с файлом
  (загрузка/выдача/404), перенос этапа из `localStorage`.
- Скриншоты для визуальной проверки удобно снимать headless Chrome через `tests/ui_smoke.py`
  (`CDP.screenshot`), как делали: `_test/*.py` (не коммитятся).

## Договорённости по стилю

- Один коммит — одна законченная вещь, сообщение по-русски с «что и почему», в теле — проверки.
- Документация для пользователя — README; контракт агента — `docs/agent-api.md`.
- Не менять схему без миграции; сейчас версия 4.

## Стартовое сообщение для нового чата

> Прочитай `docs/refactoring/projects-next.md`. Мы в ветке `feature/projects`; сделай задачи 1–5
> по порядку, каждую отдельным коммитом, с проверками `tests/run.py` и UI smoke. Ничего не мержи в
> `main` без моего слова.
