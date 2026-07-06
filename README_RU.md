# VR Pano Master

Полуавтоматический генератор 360° аэропанорам жилых районов для VR-просмотра и недвижимости.

## Возможности

- Автоматическое получение геометрии района из OpenStreetMap
- Построение 3D-сцены с процедурными PBR-текстурами (бетон, кирпич, штукатурка)
- Рендер equirectangular панорамы в Blender Cycles (GPU OptiX)
- Опциональная AI-полировка для добавления деталей
- Поддержка Яндекс.Панорам для reference стиля фасадов

## Архитектура

```
Координаты дома
  ↓
OSM/Overpass API → здания, дороги, зоны, POI
  ↓
Blender: 3D-сцена с PBR-текстурами + Panoramic Camera
  ↓
Equirectangular render (4096×2048)
  ↓ (опционально)
AI Polish (img2img, denoise 0.25)
  ↓
360° панорама (aerial_panorama_360.jpg)
```

## Быстрый старт

### 1. Установка

```bash
cd vr_pano_master
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/Mac

pip install -r requirements.txt

copy .env.example .env
# Отредактируйте .env: укажите пути к Blender и ComfyUI
```

### 2. Создание проекта

```bash
python master.py init --project my_house --lat 57.153 --lon 65.542 --levels 16
```

### 3. Получение OSM-данных

```bash
python master.py fetch-osm --project my_house
```

### 4. Рендер панорамы

```bash
# PBR рендер (основной метод)
python master.py render-pbr --project my_house --width 4096

# Результат: projects/my_house/pbr_output/equirectangular.png
```

### 5. (Опционально) AI-полировка

```bash
# Добавляет мелкие детали: балконы, кондиционеры, текстуры
python master.py ai-polish --project my_house --denoise 0.25

# Результат: projects/my_house/output/aerial_panorama_360.jpg
```

### 6. (Опционально) Автоматический выбор камеры

```bash
# Тестирует разные ракурсы и выбирает лучший
python master.py auto-select-camera --project my_house --apply
```

## Команды

### Основные

- `init` — создать новый проект
- `fetch-osm` — скачать геометрию из OpenStreetMap
- `render-pbr` — рендер PBR equirectangular панорамы
- `ai-polish` — AI-полировка (опционально)
- `doctor` — проверить конфигурацию и пути

### Камера и геометрия

- `auto-select-camera` — автоматический выбор лучшего ракурса
- `set-camera-preset` — установить preset камеры вручную
- `list-camera-presets` — показать доступные presets
- `calibrate-geometry` — калибровка смещения OSM-геометрии

### Устаревшие (legacy cubemap pipeline)

- `render-blockout` — рендер cubemap (6 граней)
- `make-control-maps` — создать Canny-карты для ControlNet
- `run-comfy` — запустить ComfyUI (first pass + main pass)
- `stitch` — склеить cubemap в equirectangular

## Конфигурация

### .env

Скопируйте `.env.example` в `.env` и укажите:

```env
COMFYUI_URL=http://127.0.0.1:8188
COMFYUI_PATH=C:/path/to/ComfyUI
BLENDER_EXE=C:/Program Files/Blender Foundation/Blender 4.0/blender.exe
COMFY_CHECKPOINT=Realistic_Vision_V5.1.safetensors
```

### config.yaml (project)

Каждый проект имеет `projects/<name>/config.yaml`:

```yaml
project:
  lat: 57.153
  lon: 65.542
  radius_m: 500

main_building:
  levels: 16
  height_m: 48

camera:
  preset: facade_se_low
  offset_east_m: 55.0
  offset_north_m: -40.0
  height_above_main_m: 6.0

render:
  equirect_width: 4096
  engine: CYCLES
```

## Требования

- **Python 3.10+**
- **Blender 4.0+** (с поддержкой Cycles GPU)
- **ComfyUI** (опционально, для AI-полировки)
- **GPU**: NVIDIA RTX 3060+ (12GB VRAM рекомендуется)

### Python-пакеты

```bash
pip install -r requirements.txt
```

Основные зависимости:
- `requests`, `Pillow`, `PyYAML`, `python-dotenv`
- `opencv-python`, `numpy` (обработка изображений)
- `openai` (AI-ассистент, опционально)
- `py360convert` (legacy cubemap stitching)

## Структура проекта

```
vr_pano_master/
├── master.py                    # Главный CLI-скрипт
├── pano_master/
│   ├── comfy_client.py          # ComfyUI API клиент
│   └── ai_assistant.py          # AI-ассистент (OpenAI-compatible)
├── scripts/
│   ├── blender_pbr_scene.py     # Blender PBR рендер (новый)
│   └── blender_blockout.py      # Blender cubemap (legacy)
├── providers/
│   └── yandex_static.py         # Яндекс.Static Maps API
├── workflow_templates/          # ComfyUI workflows
├── projects/                    # Данные проектов
│   └── my_house/
│       ├── config.yaml
│       ├── source/osm/          # OSM данные
│       ├── pbr_output/          # PBR рендеры
│       └── output/              # Финальные панорамы
└── configs/
    └── pipeline.example.yaml    # Пример конфигурации
```

## Лицензия

MIT

## Автор

underfmc
