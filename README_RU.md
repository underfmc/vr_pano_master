# VR Pano Master — полуавтоматический мастер генерации 360° аэропанорамы

Цель проекта: по выбранному дому собрать данные района, построить простую 3D-болванку, отрендерить cubemap, прогнать его через ComfyUI и получить 360° аэропанораму для дальнейшего Three.js/VR-viewer с планировками квартир и POI-маркерами.

```text
координаты дома
  ↓
OSM/Overpass: здания, дороги, POI
  ↓
Yandex Static / вручную: спутниковая подложка
  ↓
Yandex panoramas / вручную: уличная панорама фасада
  ↓
Blender: 3D-болванка + cubemap + маски
  ↓
Control maps: Canny
  ↓
ComfyUI:
  first_pass.json — общий проход
  main_pass.json  — главный дом/фасад
  ↓
stitch → aerial_panorama_360.jpg
```

> Важно: использование материалов Яндекс.Карт/Панорам в коммерческом продукте нужно согласовать с условиями Яндекса. В мастере источники данных сделаны заменяемыми: Яндекс можно заменить на собственную съёмку, лицензированные ортофото или другой provider.

---

## 1. Что умеет мастер

1. Создаёт проектную папку.
2. Скачивает геометрию района из OpenStreetMap через Overpass:
   - здания;
   - дороги;
   - зоны;
   - POI.
3. Скачивает спутниковую/картографическую подложку или принимает её вручную.
4. Подключает `yandex-pano-downloader` для уличной панорамы.
5. Собирает `street_reference_collage.png` для IP-Adapter.
6. Запускает Blender и строит 3D-болванку района.
7. Рендерит 6 граней cubemap:
   - `front`;
   - `right`;
   - `back`;
   - `left`;
   - `up`;
   - `down`.
8. Рендерит маски главного здания:
   - `front_mask_main.png`;
   - `right_mask_main.png`;
   - и т.д.
9. Создаёт Canny-карты для ControlNet.
10. Загружает входные изображения в ComfyUI через `/upload/image`.
11. Запускает два ComfyUI workflow:
   - `first_pass.json`;
   - `main_pass.json`.
12. Скачивает результаты из ComfyUI history в проектную папку.
13. Склеивает cubemap в equirectangular 360° панораму.
14. Позволяет вручную калибровать смещение OSM-геометрии относительно спутника.

---

## 2. Быстрый старт

```bash
cd vr_pano_master
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Проверьте окружение:

```bash
python master.py doctor
```

---

## 3. Настройка `.env`

Пример под текущую машину:

```env
COMFYUI_PATH=C:/dev_shir/IRR_2026/furn_gen/ComfyUI-master
COMFYUI_URL=http://127.0.0.1:8188

BLENDER_EXE=C:/Users/lol07/AppData/Local/Programs/Blender 3D/blender.exe

COMFY_CHECKPOINT=Realistic_Vision_V5.1.safetensors
COMFY_CONTROLNET_CANNY=diffusion_pytorch_model.safetensors

YANDEX_STATIC_API_KEY=ваш_ключ
YANDEX_MAPS_API_KEY=ваш_ключ_если_используете_общее_имя

AITUNNEL_API_KEY=ваш_ключ_aitunnel
AITUNNEL_BASE_URL=https://api.aitunnel.ru/v1
AITUNNEL_MODEL=claude-sonnet-5
AITUNNEL_VISION_MODEL=gemini-3-5-flash
AITUNNEL_CHEAP_MODEL=qwen3-7-plus
AITUNNEL_CODE_MODEL=claude-sonnet-5
AITUNNEL_REVIEW_MODEL=gpt-5-5
```

Не коммитьте реальные ключи.

---

## 4. Установка yandex-pano-downloader

```bash
python master.py setup-yandex-pano --install-deps
```

Мастер скачает репозиторий/файлы в:

```text
vr_pano_master/tools/yandex-pano-downloader/
```

Если нужно указать путь вручную, добавьте в `.env`:

```env
YANDEX_PANO_SCRIPT=C:/.../yandex-pano-downloader/pano.py
```

---

## 5. Создание проекта

Пример:

```bash
python master.py init --project tyumen_house_pyat --lat 57.150207 --lon 65.552968 --levels 16
```

Будет создано:

```text
projects/tyumen_house_pyat/config.yaml
```

---

## 6. Получение OSM-данных

```bash
python master.py fetch-osm --project tyumen_house_pyat
```

Если Overpass вернул ошибку или перегружен:

```bash
python master.py fetch-osm --project tyumen_house_pyat --radius 500
```

Или указать зеркало:

```bash
python master.py fetch-osm --project tyumen_house_pyat --radius 500 --endpoint https://overpass.kumi.systems/api/interpreter
```

На выходе:

```text
projects/tyumen_house_pyat/source/osm/buildings.geojson
projects/tyumen_house_pyat/source/osm/roads.geojson
projects/tyumen_house_pyat/source/osm/areas.geojson
projects/tyumen_house_pyat/source/osm/poi.geojson
projects/tyumen_house_pyat/source/poi.json
projects/tyumen_house_pyat/logs/overpass_query.ql
```

### Что такое Overpass

Overpass — публичный API для выборки данных OpenStreetMap. Он нужен для получения контуров зданий, дорог и POI. Это не Яндекс.

---

## 7. Режим точности: всё метрическое из OSM

Мы приняли новую схему:

```text
точность / здания / дороги / зоны → OSM
фасад и стиль дома → Яндекс.Панорамы
POI → позже отдельным слоем
```

То есть спутниковый снимок Яндекса больше не должен быть «фундаментом точности». Главная причина: Яндекс-спутник и OSM могут иметь разные смещения/масштаб. Если один дом совпал, а остальные съезжают к нему — это конфликт источников.

Включить OSM accuracy mode:

```bash
python master.py set-osm-accuracy-mode --project tyumen_house_pyat
```

После этого Blender будет строить ground не из спутникового растра, а из OSM-векторов:

```text
- OSM buildings → серые 3D-коробки;
- OSM highways → плоские дороги;
- OSM landuse/leisure/natural → зелёные/городские/водные полигоны;
- Яндекс.Панорамы → только reference для IP-Adapter.
```

### 7.1. Получить OSM-геометрию без POI

Так как POI делаем позже, можно облегчить запрос:

```bash
python master.py fetch-osm --project tyumen_house_pyat --radius 500 --no-poi
```

### 7.2. Спутник Яндекс теперь опционален

Спутник можно всё ещё скачать для визуального сравнения/отладки, но не использовать как метрическую основу:

```bash
python master.py fetch-satellite-yandex --project tyumen_house_pyat --zoom 17 --size 2048 --api-version 1x --layer sat --no-key-param
```

Если `render.ground_source: osm_vector`, Blender не будет натягивать этот спутник на ground plane.

### 7.3. Если всё-таки нужен satellite mode

Можно вручную вернуть в `config.yaml`:

```yaml
render:
  ground_source: satellite
```

Но тогда снова возможна проблема OSM ↔ satellite alignment, которую придётся калибровать через `vector_scale_multiplier`, `vector_offset_*` или `satellite_*`.

---

## 8. Уличная панорама и reference collage

Скачать панораму:

```bash
python master.py fetch-yandex-pano --project tyumen_house_pyat
```

Собрать collage для IP-Adapter:

```bash
python master.py make-collage --project tyumen_house_pyat
```

На выходе:

```text
projects/tyumen_house_pyat/source/street_reference_collage.png
```

Если панорама Яндекса не скачивается, можно вручную положить фасадное фото сюда:

```text
projects/tyumen_house_pyat/source/street/facade.jpg
```

и затем выполнить:

```bash
python master.py make-collage --project tyumen_house_pyat
```

---

## 9. Blender blockout

Запуск:

```bash
python master.py render-blockout --project tyumen_house_pyat
```

На выходе:

```text
projects/tyumen_house_pyat/blockout/front_color.png
projects/tyumen_house_pyat/blockout/right_color.png
projects/tyumen_house_pyat/blockout/back_color.png
projects/tyumen_house_pyat/blockout/left_color.png
projects/tyumen_house_pyat/blockout/up_color.png
projects/tyumen_house_pyat/blockout/down_color.png
```

И маски главного здания:

```text
projects/tyumen_house_pyat/blockout/front_mask_main.png
projects/tyumen_house_pyat/blockout/right_mask_main.png
...
```

### Правильный cubemap

```text
up_color.png   — небо / верх
 down_color.png — земля / надир
front/right/back/left — боковые виды
```

Если `up_color.png` содержит землю, а `down_color.png` небо, запустите:

```bash
python master.py validate-blockout --project tyumen_house_pyat --fix
```

После `render-blockout` эта проверка выполняется автоматически.

---

## 10. Калибровка геометрии

Если серые 3D-коробки зданий не совпадают со спутниковыми крышами, сначала нужно откалибровать болванку. Не запускайте ComfyUI, пока `down_color.png` явно съехал.

### Сдвинуть OSM-векторы

Если 3D-дома стоят севернее крыш, сдвинуть OSM на юг:

```bash
python master.py calibrate-geometry --project tyumen_house_pyat --vector-north -10 --add
```

Если дома восточнее крыш, сдвинуть OSM на запад:

```bash
python master.py calibrate-geometry --project tyumen_house_pyat --vector-east -5 --add
```

После каждой калибровки:

```bash
python master.py render-blockout --project tyumen_house_pyat
```

Смотрите:

```text
projects/tyumen_house_pyat/blockout/down_color.png
```

Цель — совпадение зданий со спутником хотя бы до ±3–5 метров.

### Параметры в config.yaml

```yaml
render:
  vector_offset_east_m: 0
  vector_offset_north_m: 0
  vector_scale_multiplier: 1.0
  satellite_offset_east_m: 0
  satellite_offset_north_m: 0
  satellite_scale_multiplier: 1.0
```

Обычно лучше двигать `vector_offset_*`, а не спутник.

### Если 3D-болванка меньше/больше спутника

Если один дом совпал, а остальные здания и дороги постепенно съезжают к нему, это не offset, а ошибка масштаба.

Если 3D-дома/дороги выглядят сжатыми и находятся ближе к центру, увеличьте масштаб OSM-векторов:

```bash
python master.py calibrate-geometry --project tyumen_house_pyat --vector-scale 1.03
python master.py render-blockout --project tyumen_house_pyat
```

Если нужно добавить +3% к текущему масштабу:

```bash
python master.py calibrate-geometry --project tyumen_house_pyat --vector-scale 1.03 --add
```

Если 3D-болванка наоборот больше спутника:

```bash
python master.py calibrate-geometry --project tyumen_house_pyat --vector-scale 0.97
```

После изменения масштаба обычно нужно немного поправить offset:

```bash
python master.py calibrate-geometry --project tyumen_house_pyat --vector-east 2 --vector-north -4 --add
```


---

## Камера: преднастройки для автоматического pipeline

Доступные preset'ы:

```bash
python master.py list-camera-presets
```

Рекомендуемый стартовый продающий ракурс:

```bash
python master.py set-camera-preset --project tyumen_house_pyat --preset facade_se_low
```

Если нужно дальше от дома:

```bash
python master.py set-camera-preset --project tyumen_house_pyat --preset facade_se_low --offset-scale 1.25
```

Если нужно ниже/выше относительно крыши главного здания:

```bash
python master.py set-camera-preset --project tyumen_house_pyat --preset facade_se_low --height-above-main 4
```

Основные preset'ы:

```text
facade_se_low    — базовый продающий диагональный ракурс
facade_sw_low    — диагональ с другой стороны
facade_ne_low    — диагональ с северо-востока
facade_nw_low    — диагональ с северо-запада
courtyard_south  — акцент на двор/улицу с юга
courtyard_east   — боковой фасад с востока
roof_near        — близко к крыше, полезно для теста планировок
 district_overview — выше, больше района, меньше фасада
```

Для будущей автоматизации pipeline можно рендерить несколько preset'ов, анализировать preview через vision-модель и выбирать тот, где главный дом/фасад виден лучше.

---

## 11. Создание Canny-карт

После каждого нового `render-blockout` нужно заново создавать ControlNet-карты:

```bash
python master.py make-control-maps --project tyumen_house_pyat
```

На выходе:

```text
projects/tyumen_house_pyat/control/front_canny.png
projects/tyumen_house_pyat/control/right_canny.png
...
```

---

## 12. AITunnel: опциональный анализ и prompt'ы

После спутника и street-collage можно запустить vision-анализ:

```bash
python master.py ai-analyze-inputs --project tyumen_house_pyat
```

Сгенерировать prompt'ы и применить их в `config.yaml`:

```bash
python master.py ai-suggest-prompts --project tyumen_house_pyat --apply
```

Рекомендуемые роли:

```text
claude-sonnet-5     — главный агент / код / prompt engineering
gemini-3-5-flash    — анализ изображений
qwen3-7-plus        — дешёвые JSON/POI-задачи
gpt-5-5             — редкий дорогой аудит
```

---

## 13. ComfyUI workflow

Нужны два workflow в API-формате:

```text
workflow_templates/first_pass.json
workflow_templates/main_pass.json
```

Подробная инструкция:

```text
docs/COMFY_TWO_PASS_WORKFLOWS_RU.md
```

### 13.1. first_pass.json

Назначение: общий проход по всей грани.

Обязательные placeholder'ы:

```text
FACE_COLOR
FACE_CANNY
STREET_REFERENCE
POSITIVE_PROMPT
NEGATIVE_PROMPT
SAVE_PREFIX
```

### 13.2. main_pass.json

Назначение: доработка главного дома/фасада по маске.

Обязательные placeholder'ы:

```text
FIRST_PASS_IMAGE
MAIN_MASK
STREET_REFERENCE
MAIN_POSITIVE_PROMPT
MAIN_NEGATIVE_PROMPT
SAVE_PREFIX
```

Желательно также:

```text
FACE_CANNY
```

Совместимые алиасы:

```text
FACE_COLOR = FIRST_PASS_IMAGE
FACE_MASK  = MAIN_MASK
```

### 13.3. Экспорт из ComfyUI

В ComfyUI:

```text
Settings → Enable Dev Mode
Save (API Format)
```

Обычный workflow JSON не подходит. Нужен именно API JSON.


---

## ComfyUI presets против галлюцинаций

Если `first_pass` начинает менять геометрию, рисовать гигантские фасады или добавлять здания, используйте безопасный preset:

```bash
python master.py set-comfy-preset --project tyumen_house_pyat --preset geometry_safe
```

Посмотреть все preset'ы:

```bash
python master.py list-comfy-presets
```

Доступные preset'ы:

```text
geometry_safe — максимум геометрии, слабый IP-Adapter в first pass
texture_safe  — рекомендуемый режим: заметное текстурирование без сильной галлюцинации
balanced      — средний режим после стабилизации геометрии
no_ip_first   — полностью выключить IP-Adapter в first pass; фасад только в main pass
```

Для отладки рекомендуется:

```bash
python master.py set-comfy-preset --project tyumen_house_pyat --preset geometry_safe
python master.py run-comfy --project tyumen_house_pyat --stage first --faces front
```

Если даже `geometry_safe` галлюцинирует, используйте:

```bash
python master.py set-comfy-preset --project tyumen_house_pyat --preset no_ip_first
```

В этом режиме первый проход делает только фотореалистичный район по геометрии, а фасадный стиль применяется позже в `main_pass` по маске главного здания.

---

## 14. Запуск ComfyUI

Сначала запустите ComfyUI:

```bash
cd C:/dev_shir/IRR_2026/furn_gen/ComfyUI-master
python main.py --listen 127.0.0.1 --port 8188
```

Проверить, что URL совпадает с `.env`:

```env
COMFYUI_URL=http://127.0.0.1:8188
```

---


Проверить, виден ли главный дом на гранях для `main_pass`:

```bash
python master.py inspect-masks --project tyumen_house_pyat
```

Если у нужной грани coverage около `0%`, значит `main_pass` почти ничего не изменит на этой грани — главный дом туда не попал. Нужно поменять camera preset/offset и заново сделать `render-blockout`.

## 15. Запуск генерации

Сначала только first pass:

```bash
python master.py run-comfy --project tyumen_house_pyat --stage first
```

Проверить:

```text
projects/tyumen_house_pyat/comfy_output/front_first.png
```

Если first pass плохой — не запускайте main pass. Сначала чините геометрию/ControlNet/denoise.

Затем main pass:

```bash
python master.py run-comfy --project tyumen_house_pyat --stage main
```

Или оба прохода сразу:

```bash
python master.py run-comfy --project tyumen_house_pyat --stage all
```

Ограничить грани:

```bash
python master.py run-comfy --project tyumen_house_pyat --stage first --faces front,right
```

Мастер сам загружает картинки в ComfyUI через `/upload/image`, поэтому вручную копировать `front_color.png`, `front_canny.png` и `street_reference_collage.png` в ComfyUI/input не нужно.

---

## 16. Склейка панорамы

```bash
python master.py stitch --project tyumen_house_pyat
```

Итог:

```text
projects/tyumen_house_pyat/output/aerial_panorama_360.jpg
projects/tyumen_house_pyat/web/assets/panorama/aerial_panorama_360.jpg
```

---

## 17. Полная рабочая последовательность

```bash
python master.py doctor

python master.py setup-yandex-pano --install-deps

python master.py init --project tyumen_house_pyat --lat 57.150207 --lon 65.552968 --levels 16

python master.py set-osm-accuracy-mode --project tyumen_house_pyat

python master.py fetch-osm --project tyumen_house_pyat --radius 500 --no-poi

python master.py fetch-yandex-pano --project tyumen_house_pyat

python master.py make-collage --project tyumen_house_pyat

python master.py set-camera-preset --project tyumen_house_pyat --preset facade_se_low

python master.py render-blockout --project tyumen_house_pyat

# если нужно — калибровать/менять camera preset, потом снова render-blockout
python master.py calibrate-geometry --project tyumen_house_pyat --vector-north -10 --add
python master.py render-blockout --project tyumen_house_pyat

python master.py make-control-maps --project tyumen_house_pyat

python master.py run-comfy --project tyumen_house_pyat --stage first

python master.py run-comfy --project tyumen_house_pyat --stage main

python master.py stitch --project tyumen_house_pyat
```

---

## 18. Частые ошибки

### `Config not found`

Вы указали не тот project id.

```bash
python master.py render-blockout --project tyumen_house_pyat
```

а не:

```bash
python master.py render-blockout --project tyumen_house
```

---

### `Overpass HTTP 406 / 429 / 504`

Публичный Overpass перегружен или отклонил запрос.

Решения:

```bash
python master.py fetch-osm --project tyumen_house_pyat --radius 300
```

или:

```bash
python master.py fetch-osm --project tyumen_house_pyat --endpoint https://overpass.kumi.systems/api/interpreter
```

---

### `Yandex Static API 400 Bad Request`

Не используйте `v1` с `l=sat`. Для спутника:

```bash
python master.py fetch-satellite-yandex --project tyumen_house_pyat --api-version 1x --layer sat --no-key-param
```

Для официального v1 используйте только карту:

```bash
python master.py fetch-satellite-yandex --project tyumen_house_pyat --api-version v1 --layer map
```

---

### `front_canny.png not found`

Не выполнена команда:

```bash
python master.py make-control-maps --project tyumen_house_pyat
```

---

### `street_reference_collage.png not found`

Не выполнена команда:

```bash
python master.py make-collage --project tyumen_house_pyat
```

Перед ней нужна панорама/фото в:

```text
projects/tyumen_house_pyat/source/street/
```

---

### ComfyUI `/prompt HTTP 400`

Теперь мастер показывает подробное тело ошибки ComfyUI. Частые причины:

```text
- workflow сохранён не как API Format;
- в workflow остались неправильные placeholder'ы;
- модель отсутствует в ComfyUI/models;
- ControlNet не того типа;
- IP-Adapter/CLIP Vision ноды не установлены;
- Load Image не получил файл.
```

---

### Панорама получилась бредовой

Почти всегда причина в одном из пунктов:

```text
- OSM-здания съехали относительно спутника;
- down_color.png не совпадает с крышами;
- up/down cubemap перепутаны;
- дороги слишком доминируют в Canny;
- ControlNet strength слишком высокий;
- denoise слишком низкий, и модель сохраняет серые коробки;
- IP-Adapter слишком сильный на общем проходе.
```

Сначала проверьте:

```text
blockout/down_color.png
```

Если геометрия съехала — используйте:

```bash
python master.py calibrate-geometry --project tyumen_house_pyat --vector-north -10 --add
python master.py render-blockout --project tyumen_house_pyat
python master.py make-control-maps --project tyumen_house_pyat
```

---

## 19. Рекомендуемые ComfyUI-настройки

### first_pass

```text
denoise: 0.62–0.72
steps: 28–35
cfg: 5.5–6.5
ControlNet Canny strength: 0.45–0.55
IP-Adapter weight: 0.25–0.35
```

Цель: сделать весь район фотореалистичным, не натягивая фасад главного дома на все здания.

### main_pass

```text
denoise: 0.35–0.50
steps: 24–32
cfg: 5.0–6.2
ControlNet Canny strength: 0.35–0.50
IP-Adapter weight: 0.70–0.90
```

Цель: доработать главный дом по маске и фасадному reference.

---

## 20. Что такое 3D-болванка района

Это не финальная 3D-модель. Это техническая сцена:

```text
- спутник на плоскости;
- здания как серые коробки;
- дороги как плоские полосы;
- примерные высоты;
- камера/cubemap;
- маска главного здания.
```

Она нужна, чтобы ComfyUI получил:

```text
- правильную перспективу;
- расположение зданий;
- контуры;
- маску главного дома;
- основу для ControlNet.
```

---

## 21. Что дальше для production

- UI выбора дома на карте.
- Подтверждение контура дома.
- Ручная/визуальная калибровка OSM ↔ спутник.
- Depth pass из Blender.
- Более аккуратные высоты зданий.
- Автоматический выбор фасадных кадров.
- Three.js viewer:
  - 360° skybox;
  - планировки на крыше;
  - POI-маркеры;
  - карточки квартир;
  - форма заявки;
  - аналитика.
