# Локальная настройка (Windows)

## Требования

- **Python 3.10+** (установлен в `C:\Users\lol07\AppData\Local\Programs\Python\Python312`)
- **Blender 4.0+** (установлен в `C:\Users\lol07\AppData\Local\Programs\Blender 3D\`)
- **ComfyUI** (опционально, для AI-полировки): `C:\dev_shir\IRR_2026\furn_gen\ComfyUI-master`
- **GPU**: NVIDIA RTX 5070 (12GB VRAM)

## Установка

```bash
cd C:\dev_shir\IRR_2026\vr_pano_master
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

## .env

Отредактируйте `.env` (или скопируйте из `.env.example`):

```env
# ComfyUI (опционально, для ai-polish)
COMFYUI_PATH=C:/dev_shir/IRR_2026/furn_gen/ComfyUI-master
COMFYUI_URL=http://127.0.0.1:8188

# Blender
BLENDER_EXE=C:/Users/lol07/AppData/Local/Programs/Blender 3D/blender.exe

# ComfyUI models
COMFY_CHECKPOINT=Realistic_Vision_V5.1.safetensors
COMFY_CONTROLNET_CANNY=diffusion_pytorch_model.safetensors
COMFY_IPADAPTER=ip-adapter_sd15.safetensors
COMFY_CLIP_VISION=model.safetensors

# Яндекс API (опционально)
YANDEX_STATIC_API_KEY=

# AI (опционально)
AITUNNEL_API_KEY=
AITUNNEL_BASE_URL=https://api.aitunnel.ru/v1
AITUNNEL_MODEL=claude-sonnet-5
AITUNNEL_VISION_MODEL=gemini-3.5-flash
```

## Проверка

```bash
python master.py doctor
```

## Полный проход (новый PBR pipeline)

```bash
# 1. Создать проект
python master.py init --project my_house --lat 57.153 --lon 65.542 --levels 16

# 2. Скачать OSM-данные
python master.py fetch-osm --project my_house

# 3. Скачать Яндекс.Панорамы (опционально, для reference)
python master.py fetch-yandex-pano --project my_house

# 4. Рендер PBR equirectangular панорамы
python master.py render-pbr --project my_house --width 4096

# 5. (Опционально) AI-полировка
python master.py ai-polish --project my_house --denoise 0.25
```

## Полный проход (одной командой)

```bash
python master.py run-auto --project my_house --lat 57.153 --lon 65.542 --levels 16
```

## Legacy: Cubemap pipeline

Старый пайплайн (cubemap + ComfyUI two-pass) всё ещё доступен:

```bash
python master.py render-blockout --project my_house
python master.py make-control-maps --project my_house
python master.py run-comfy --project my_house --stage all
python master.py stitch --project my_house
```

Но рекомендуется использовать новый PBR pipeline.

## Запуск ComfyUI (для AI-полировки)

```bash
cd C:\dev_shir\IRR_2026\furn_gen\ComfyUI-master
python main.py --listen 127.0.0.1 --port 8188
```

## Установка yandex-pano-downloader

```bash
python master.py setup-yandex-pano --install-deps
```
