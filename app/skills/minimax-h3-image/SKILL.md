---
name: minimax-h3-image
description: Пишет промпт для MiniMax H3 Image (изображения по тексту и правка/генерация по референсам, до 5 картинок, теги <Picture 1>…<Picture 5>). Подробное описание всего кадра на английском, без негативного промпта. Включай, когда чат настроен на «MiniMax H3 Image: по тексту» или «MiniMax H3 Image: правка».
---

# MiniMax H3 Image: промпт для изображения

Модель ждёт ПОДРОБНОЕ описание ВСЕГО кадра на АНГЛИЙСКОМ языке, одним связным абзацем (или двумя). Промпт используется как есть, поэтому никаких «улучшателей» не будет: всё, что нужно в кадре, ты пишешь сам. Короткий или русский запрос ты сначала сам разворачиваешь в полное описание.

Что модель НЕ умеет: негативный промпт не работает (фразы «no X», «without X» бесполезны, пиши, что ЕСТЬ в кадре); веса в скобках и теги через запятую не нужны; 6 шагов, CFG 1 в приложении уже стоят.

## Порядок описания (для текста без референсов)

1. Носитель и жанр: "cinematic photograph", "ink and watercolor illustration", "punk zine poster collage", "3D animated film still", "product photo".
2. Главный объект и действие: кто или что, возраст, поза, выражение лица, жест. Конкретно: не "a beautiful woman", а "a young woman with softly wavy dark hair, eyes half closed, holding a small white espresso cup near her lips".
3. Одежда, предметы, материалы, фактуры (cotton scarf, brushed steel, cracked paint).
4. Место и фон: что на заднем плане и насколько он проработан ("loosely indicated café details", "sharp Parisian street behind").
5. Свет и цвет: источник, направление, температура, палитра ("warm amber window light from the left, muted teal shadows").
6. Камера и композиция: крупность, ракурс, объектив, где в кадре главный объект, ориентация ("tight close-up, slightly angled head and shoulders, subject in the lower third, portrait orientation 3:4").
7. Стиль и настроение одной фразой в конце. Если нужен текст на изображении, пиши его в кавычках и укажи шрифт, цвет и место ("the word \"STYLE\" in solid bright red distressed grotesque type directly below").

Всё главное ставь в начало. Объём: от 80 до 250 слов, для постеров и коллажей можно больше. Одно главное решение на кадр: не склеивай три разных сцены.

## Размер и формат

Модель настроена на площадь от 4 мегапикселей (около 2048×2048), но 4 МП — её нижний предел: картинка получается с зерном и «недопечённой». Чисто от 8 МП, лучше всего около 16 МП (проверено: 16 МП ≈ 3456×4608 за ~2 минуты на видеокарте 16 ГБ). В приложении формат (16:9, 9:16, 1:1, 4:3, 3:4, 3:2, 2:3, 21:9) и размер (4 / 8 / 16 МП) выбираются отдельно, поэтому одной строкой перед блоком подскажи формат кадра и размер 8 или 16 МП. В сам промпт можно вписать ориентацию («portrait orientation 3:4»). Мелкие детали и надписи лучше на большем размере.

## Референсы (картинки к чату), теги <Picture N>

Картинки, приложенные к чату, подаются как <Picture 1>, <Picture 2>… в порядке слотов (до 5). В промпте называй их ТОЛЬКО так. Для каждой картинки ясно скажи ЕЁ РОЛЬ: ЧТО брать и ЧЕГО НЕ брать.

- Стиль: "Using <Picture 1> as the complete style reference: adopt its entire visual language — [перечисли: линия, штрих, палитра, фактура бумаги, свет, зерно]. Do NOT copy the person or the composition from <Picture 1>, only the style."
- Герой/лицо: "the woman from <Picture 2>, keeping her face, hairstyle and clothing exactly".
- Предмет или одежда: "wearing the jacket from <Picture 3>".
- Место: "in the location shown in <Picture 4>, same architecture and light".
- Правка: "Edit <Picture 1>: [что изменить]. Keep the face, hairstyle, clothing, pose, composition and lighting exactly as in <Picture 1>." Одна правка за раз.

Первая картинка задаёт форму результата, если пользователь не выбрал формат кадра сам. Если картинок две и больше, в КАЖДОЙ роли ссылайся на номер; не пересказывай подробно внешность героя с картинки (её даёт референс), но стиль, одежду и сцену описывай словами.

Типовой пример (стиль + два героя + место): «<Picture 1> — образец стиля, <Picture 2> и <Picture 3> — герои, место описано словами»:
"Using <Picture 1> as the complete style reference: loose gestural black ink linework with energetic overlapping strokes, dense hand-hatching, warm amber and ochre watercolor washes over the ink, cream textured paper showing through, occasional paint splatter, an unfinished sketchbook quality. Do NOT copy the people or the composition from <Picture 1>, only the style. Draw the young man from <Picture 2> and the woman from <Picture 3>, keeping their faces and hair, sitting at a small round table of a Parisian street café, he in a dark coat, she in a light scarf, both holding small white espresso cups; behind them only loosely indicated café details, a rattan chair, a distant building facade. Medium shot, slightly angled, portrait orientation 3:4."

## Формат ответа в приложении

- Один блок prompt с английским промптом. Негатив не выводи. Русскую версию и настройки модели не выводи.
- Одной строкой перед блоком: формат кадра и (если нужны референсы) в каком порядке класть картинки в слоты («1 — стиль, 2 — первый герой, 3 — второй герой»). Не больше 5 картинок.
- Если пользователь просит несколько вариантов, напиши каждый отдельным блоком prompt.
