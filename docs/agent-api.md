# Агенты: как программе управлять Видеостудией

Приложение умеет принимать задания от других программ на этом компьютере — например, от
DeepSeek Harness, Codex или своего скрипта. Агент делает то же, что человек: кладёт картинку
во входы, запускает раздел, следит за очередью и забирает готовый файл. Задание при этом
видно в окне, его можно остановить, а результат сам попадает в Библиотеку.

Сейчас агент умеет: найти приложение, отдать входные файлы, следить за заданиями и **запускать
видео по картинке** (image-to-video, а также текст-в-видео). Длинное видео, монтаж и прочие
разделы — следующими шагами.

## Включить

Настройки → **Агенты** → «Разрешить управление агентам». По умолчанию включено.
Выключенное приложение не принимает ни одного запроса и отвечает `403 AGENTS_DISABLED`.

То же самое можно сделать в `config.json` в папке данных: `"allow_agents": false`.

## Найти приложение

Порт при каждом запуске новый, поэтому приложение само пишет, где оно сидит. Файл:

```
%LOCALAPPDATA%\MinimaxH3Workspace\agent.json
```

```json
{
 "app": "minimax-h3-workspace",
 "api": 1,
 "version": "1.1.12",
 "port": 8765,
 "token": "…",
 "instance": "79d644e8d135",
 "pid": 12345,
 "started": "2026-11-11T14:27:19",
 "data_dir": "C:\\…\\data",
 "url": "http://127.0.0.1:8765/api/agent/v1/"
}
```

- `token` — ключ доступа, новый после каждого запуска;
- `instance` — меняется при перезапуске: по нему видно, что ключ устарел;
- файл удаляется при штатном выходе, права — только у текущего пользователя.

Если файла нет (приложение ещё не запускалось или упало), его можно найти перебором
портов 8765…8814 запросом `GET /api/agent/v1/ping` — он единственный не требует ключа.

Пути можно переопределить переменными окружения: `H3STUDIO_AGENT_FILE` (путь к файлу),
`H3STUDIO_AGENT_ROOTS` (дополнительные папки для входных файлов, через `;`).

## Ключ

Каждый запрос, кроме `ping`, несёт ключ:

```
X-Agent-Token: <token>
```

или `Authorization: Bearer <token>`. Без ключа — `401 BAD_TOKEN`.

## Эндпоинты

Базовый адрес: `http://127.0.0.1:<port>/api/agent/v1/`.

### `GET /ping` — жив ли, чей порт

```json
{"app": "minimax-h3-workspace", "api": 1, "version": "1.1.12", "instance": "79d644e8d135"}
```

### `GET /capabilities` — что установлено и что можно

```json
{
 "app": "minimax-h3-workspace", "api": 1, "version": "1.1.12", "instance": "…",
 "agents": {"enabled": true, "asset_roots": ["C:\\Users\\…", "C:\\…\\data", "C:\\Users\\…\\Videos\\Minimax H3\\output"],
            "max_asset_bytes": 2147483648},
 "comfy": {"online": true, "url": "http://127.0.0.1:8188"},
 "cloud": {"enabled": false},
 "gpu": {"name": "RTX 5090", "vram_used_gb": 1.2, "vram_total_gb": 31.8, "temp": 41, "util": 3},
 "modes": ["video", "lipsync", "refs", "long", "krea", "qwen", "upscale"],
 "jobs": {"active": 0, "can_start": ["video"]},
 "limits": {"max_seconds": 25.0, "aspects": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
            "qualities": ["480p", "720p", "1080p"], "image_sizes": ["16:9", "9:16", "1:1", "4:3"]},
 "models": {"video_unet": {"file": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "have": true}}
}
```

Это ответ на вопрос «что здесь вообще можно», чтобы не запускать заведомо невозможное:
неизвестный режим, слишком длинный ролик или отсутствующая модель видны сразу.

### `POST /assets` — входной файл

Агент сначала отдаёт файл, потом ссылается на него по имени. Имя — хэш содержимого,
поэтому один и тот же кадр, отправленный дважды, остаётся одним ассетом.

```json
{"path": "E:\\frames\\s01.png"}
```

```json
{
 "asset": "9f2c1a77b0d34e51", "kind": "image", "bytes": 1559523,
 "width": 1344, "height": 768, "created": "2026-11-11T14:31:02",
 "path": "C:\\…\\data\\assets\\9f\\9f2c1a77b0d34e51.png",
 "url": "http://127.0.0.1:8765/api/agent/v1/assets/9f2c1a77b0d34e51/content",
 "deduplicated": false
}
```

Можно и файлом: `multipart/form-data`, поле с любым именем и `filename`.

Читать можно только из разрешённых папок (`agents.asset_roots` в `capabilities`: профиль
пользователя, папка результатов, папка данных). Свои папки добавляются в `config.json`:

```json
"agent_asset_roots": ["E:\\frames", "D:\\projects\\film1"]
```

Подходят png, jpg, jpeg, webp, bmp, gif, mp4, webm, mov, mkv, wav, mp3, flac, m4a, ogg, latent.

- `GET /assets` — последние входные файлы;
- `GET /assets/{asset}` — описание;
- `GET /assets/{asset}/content` — сам файл.

### `GET /jobs` и `GET /jobs/{id}` — что с заданием

`{id}` — это `prompt_id`, который окно показывает в очереди (он же приходит в событиях окна).

```json
{
 "job": "0f4c…", "state": "running",
 "mode": "video", "prompt": "…", "seed": 1234567, "origin": null,
 "progress": {"stage": "sample", "step": 3, "total": 8, "scene": 2, "scenes": 4, "percent": 0.41},
 "queue_position": null, "elapsed": 84.3, "results": []
}
```

- `state`: `queued`, `running`, `done`, `failed`, `canceled`;
- `progress.percent` — та же грубая оценка, что показывает окно (точное число знает только ComfyUI);
- `queue_position` — сколько заданий впереди, пока задание ждёт;
- готовое: `results[]` с `library_id`, `path` (файл на диске), `url` (окно умеет отдавать его по ссылке),
  `kind`, `res`, `seconds`, `date`;
- если задание упало, есть `error.code` и `error.message` (например, «ComfyUI не видит модели»).

`GET /jobs/{id}/wait?timeout=60` — то же, но отвечает, когда задание закончилось или вышло
время (не больше 120 секунд): удобно для инструмента, у которого свой таймаут.

`GET /jobs` — `{"active": [...], "finished": [{"job": "…", "state": "done", "at": "…"}]}`.

Приложение помнит последние 200 завершённых заданий в этом запуске. Задания до перезапуска
ищутся в Библиотеке.

### `POST /jobs` — запустить видео по картинке

```json
{
  "mode": "video",
  "prompt": "subject_definitions:\n<Subject 1> …\n\nsummary:\n…\n\ndetailed_description:\n…\n\noverall_soundscape:\n…\n\nnon_diegetic_music:\n…",
  "params": {"seconds": 6, "aspect": "16:9", "quality": "720p", "seed": 101},
  "inputs": {"first_frame": {"asset": "9f2c1a77b0d34e51"}},
  "idempotency_key": "dsh-run-42-shot-1",
  "client": "deepseek-harness"
}
```

```json
{
  "job": "0f4c…", "state": "queued", "deduplicated": false, "seed": 101, "seconds": 6.0,
  "aspect": "16:9", "quality": "720p", "res": "1280×736",
  "inputs": {"first_frame": {"asset": "9f2c1a77b0d34e51", "comfy": "h3studio/h3s_e2ed9944.png"},
             "last_frame": null},
  "origin": {"kind": "agent", "idempotency_key": "dsh-run-42-shot-1"}
}
```

| Поле | Что значит |
|---|---|
| `prompt` | обязателен; формат H3 — сцены, камера, звук (см. `app/skills/hailuo-h3`) |
| `params.seconds` | 3…25, по умолчанию 5; больше лимита — обрезается до лимита |
| `params.aspect` | `16:9`, `9:16`, `1:1`, `4:3`, `3:4`, `21:9` |
| `params.quality` | из `limits.qualities` в `capabilities` (`480p`, `720p`, `1080p` и старые идентификаторы) |
| `params.seed` | целое; без него — случайный (вернётся в ответе) |
| `params.loop` | зациклить: последний кадр = первый, только вместе с `first_frame` |
| `inputs.first_frame` | картинка: `{"asset": "…"}` (после `POST /assets`) или `{"path": "E:\\…\\s01.png"}` |
| `inputs.last_frame` | необязательный последний кадр, только вместе с первым |
| `idempotency_key` | тот же ключ → то же задание, второго рендера не будет |

Ответ — `202` с `job`, тем же, что в очереди окна. Задание появляется в окне («Очередь»),
а результат — в Библиотеке с пометкой «· агент». Повторный запрос с тем же
`idempotency_key` вернёт `200` и уже существующее задание (`deduplicated: true`), даже если
оно уже закончилось.

Без картинки (`first_frame` не задан) — обычное текст-в-видео.

### `POST /jobs/{id}/cancel` — остановить

```json
{"job": "0f4c…", "state": "canceled"}
```

Ждущее задание удаляется из очереди, идущее — прерывается (`state: "canceling"`, дальше
`GET /jobs/{id}` покажет `canceled`). Завершённое отменить нельзя — `409 ALREADY_FINISHED`.

### `GET /library?limit=20&mode=video` — готовое

```json
{"items": [{"library_id": "ccb8eb00977f", "kind": "video", "mode": "montage", "prompt": "",
            "res": "832×1472", "seconds": 59.7, "date": "2026-11-10T10:35:26",
            "path": "C:\\Users\\…\\Videos\\Minimax H3\\output\\20261110_ccb8eb00977f_montage_00005_.mp4",
            "url": "http://127.0.0.1:8765/api/file/ccb8eb00977f"}], "total": 12}
```

## Ошибки

| Код | HTTP | Что значит |
|---|---|---|
| `BAD_TOKEN` | 401 | нет ключа или он от прошлого запуска |
| `AGENTS_DISABLED` | 403 | выключено в настройках |
| `BAD_REQUEST` | 400 | не тот JSON, относительный путь, нет файла в форме |
| `INVALID_PARAM` | 400 | параметр вне допустимых значений (в `hint` — что разрешено) |
| `MODE_UNSUPPORTED` | 400 | этот раздел агентам пока недоступен |
| `MODE_UNAVAILABLE` | 409 | раздел не установлен в программе |
| `CLOUD_UNSUPPORTED` | 409 | включено облако RunningHub: агенты туда задания пока не ставят |
| `ASSET_NOT_FOUND` | 404 | файла по пути нет (или ассета с таким именем) |
| `ASSET_FORBIDDEN` | 403 | путь вне разрешённых папок |
| `ASSET_BAD_TYPE` | 415 | расширение не подходит |
| `ASSET_TOO_BIG` | 413 | больше 2 ГБ |
| `GENERATION_REFUSED` | 400/502 | ComfyUI не принял граф (нет нод, ошибка модели) |
| `COMFY_OFFLINE` | 503 | ComfyUI не отвечает; повторите с тем же `idempotency_key` |
| `ALREADY_FINISHED` | 409 | задание уже закончилось, отменять нечего |
| `JOB_NOT_FOUND` | 404 | приложение такого задания не помнит |
| `INTERNAL` | 500 | непредвиденная ошибка приложения (попадает в отчёт для разработчика) |

```json
{"error": {"code": "ASSET_FORBIDDEN", "message": "Из этой папки приложению файлы читать нельзя",
           "hint": "Разрешено: C:\\Users\\…; …"}}
```

## Безопасность

- сервер слушает только `127.0.0.1`;
- ключ новый при каждом запуске и лежит в файле, доступном только текущему пользователю;
- приложение доверяет программам того же пользователя: это защита от случайных вызовов и от
  чужих машин в сети, а не от вредоносного процесса, запущенного вами же;
- входные файлы читаются только из разрешённых папок;
- выключатель в настройках действует сразу: следующий же запрос получит `AGENTS_DISABLED`;
- всё, что делает агент, видно в окне — в очереди и в Библиотеке — и останавливается кнопкой.

## Проверка

```powershell
# указать python, в котором есть aiohttp (подойдёт python_embeded из ComfyUI)
python tests\agent_api_smoke.py --python "I:\Video Generation\ComfyUI_windows_portable\python_embeded\python.exe"
```

Тест поднимает настоящий сервер во временной папке и поддельный ComfyUI (несколько HTTP-ответов,
без рендера): ничего не считается и рабочий ComfyUI не трогается. Проверяются ключ, выключатель,
импорт файлов (включая запрещённые папки), запуск image-to-video (загрузка кадра, собранный граф,
дедупликация по ключу), отмена и Библиотека.

## Бандл для DeepSeek Harness

Для чатов DSH есть готовый бандл `@local/h3studio`: MCP-сервер `mcp/h3studio.js` в этом
репозитории, инструменты `studio_status`, `studio_capabilities`, `studio_asset`,
`studio_image_to_video`, `studio_job`, `studio_wait`, `studio_cancel`, `studio_library`.
Он ничего не рендерит сам — говорит с запущенным приложением через этот же API, поэтому
задание видно в «Очереди», а результат попадает в Библиотеку.

```powershell
dsh plugin --profile desktop add "file:C:/Users/avaln/Documents/deepseek-harness/default-workspace/bundles/h3studio"
```

Обычно инструменты появляются в чате сразу; если нет — перезапустите DSH (серверные плагины
читаются при старте профиля). Проверить сервер без DSH: `python tests/mcp_stdio_smoke.py --render`.

## Что дальше (P2)

- длинное видео по сценам: проект, продолжение сцены, переделка одной сцены;
- монтаж и увеличение как задания агента;
- картинки (`krea`, `qwen`) как задания — сейчас их запускает только окно;
- tool-calls для встроенного агента на те же операции.
