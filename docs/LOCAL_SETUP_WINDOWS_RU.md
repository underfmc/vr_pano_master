# Локальная настройка под вашу машину

## Пути

ComfyUI:

```text
C:\dev_shir\IRR_2026\furn_gen\ComfyUI-master
```

Blender:

```text
C:\Users\lol07\AppData\Local\Programs\Blender 3D\blender.exe
```

Checkpoint:

```text
Realistic_Vision_V5.1.safetensors
```

ControlNet Canny:

```text
diffusion_pytorch_model.safetensors
```

## .env

В `vr_pano_master/.env` добавьте:

```env
COMFYUI_PATH=C:/dev_shir/IRR_2026/furn_gen/ComfyUI-master
COMFYUI_URL=http://127.0.0.1:8188
BLENDER_EXE=C:/Users/lol07/AppData/Local/Programs/Blender 3D/blender.exe

COMFY_CHECKPOINT=Realistic_Vision_V5.1.safetensors
COMFY_CONTROLNET_CANNY=diffusion_pytorch_model.safetensors

YANDEX_STATIC_API_KEY=ваш_ключ_яндекс_static_maps
YANDEX_MAPS_API_KEY=ваш_ключ_если_используете_общее_имя

AITUNNEL_MODEL=claude-sonnet-5
AITUNNEL_VISION_MODEL=gemini-3-5-flash
AITUNNEL_CHEAP_MODEL=qwen3-7-plus
AITUNNEL_CODE_MODEL=claude-sonnet-5
AITUNNEL_REVIEW_MODEL=gpt-5-5
```

## Проверка окружения

```bash
python master.py doctor
```

Команда проверит:

- путь к ComfyUI;
- путь к Blender;
- наличие checkpoint;
- наличие ControlNet Canny;
- наличие ключа Yandex Static API;
- наличие `yandex-pano-downloader`.

## Установка yandex-pano-downloader из GitHub

```bash
python master.py setup-yandex-pano --install-deps
```

Команда скачает репозиторий/файлы в:

```text
vr_pano_master/tools/yandex-pano-downloader/
```

И выведет строку для `.env`:

```env
YANDEX_PANO_SCRIPT=C:/.../vr_pano_master/tools/yandex-pano-downloader/pano.py
```

## Полный тестовый проход

```bash
python master.py init --project tyumen_house --lat 57.153 --lon 65.542 --levels 16
python master.py fetch-osm --project tyumen_house
python master.py fetch-satellite-yandex --project tyumen_house --zoom 17 --size 2048
python master.py fetch-yandex-pano --project tyumen_house
python master.py make-collage --project tyumen_house
python master.py render-blockout --project tyumen_house
python master.py make-control-maps --project tyumen_house
```

Дальше нужны два экспортированных ComfyUI workflow в API JSON:

```text
workflow_templates/first_pass.json
workflow_templates/main_pass.json
```

Подробная схема: `docs/COMFY_TWO_PASS_WORKFLOWS_RU.md`.

Затем:

```bash
python master.py run-comfy --project tyumen_house --stage all
python master.py stitch --project tyumen_house
```

## Запуск ComfyUI

```bash
cd C:\dev_shir\IRR_2026\furn_gen\ComfyUI-master
python main.py --listen 127.0.0.1 --port 8188
```


## Опционально: AITunnel для анализа входов и prompt'ов

После спутника и street-collage можно запустить vision-анализ:

```bash
python master.py ai-analyze-inputs --project tyumen_house
```

Затем сгенерировать prompt'ы для ComfyUI и применить их в `config.yaml`:

```bash
python master.py ai-suggest-prompts --project tyumen_house --apply
```
