# ComfyUI: два workflow для VR-аэропанорамы

Теперь мастер рассчитан на два API-workflow:

```text
workflow_templates/first_pass.json  — общий проход по всей грани cubemap
workflow_templates/main_pass.json   — второй проход/inpaint главного дома по маске
```

Укажите их в `projects/<project>/config.yaml`:

```yaml
comfy:
  first_pass_workflow_api_json: workflow_templates/first_pass.json
  main_pass_workflow_api_json: workflow_templates/main_pass.json
```

## 1. first_pass.json — общий проход

Назначение: сделать всю грань cubemap фотореалистичной, но не слишком сильно переносить фасад главного дома на весь район.

### Входные placeholder'ы

В экспортированном ComfyUI API JSON должны быть строки:

```text
FACE_COLOR       # blockout/<face>_color.png, img2img основа
FACE_CANNY       # control/<face>_canny.png, ControlNet Canny
STREET_REFERENCE # source/street_reference_collage.png, IP-Adapter reference
SAVE_PREFIX      # префикс SaveImage
POSITIVE_PROMPT  # общий positive prompt
NEGATIVE_PROMPT  # общий negative prompt
```

### Правильная логика графа

```text
CheckpointLoaderSimple: Realistic_Vision_V5.1.safetensors
  ├─ MODEL → IPAdapter Advanced → KSampler.model
  ├─ CLIP  → CLIPTextEncode POSITIVE_PROMPT → Apply ControlNet Advanced.positive
  ├─ CLIP  → CLIPTextEncode NEGATIVE_PROMPT → Apply ControlNet Advanced.negative
  └─ VAE   → VAE Encode / VAE Decode

Load Image FACE_COLOR → ImageScale 1024x1024 → VAE Encode → KSampler.latent_image
Load Image FACE_CANNY → ImageScale 1024x1024 → Apply ControlNet Advanced.image
Load ControlNet Model diffusion_pytorch_model.safetensors → Apply ControlNet Advanced.control_net
Load Image STREET_REFERENCE → CLIP Vision / IP-Adapter → modified MODEL
KSampler → VAE Decode → Save Image SAVE_PREFIX
```

### Рекомендуемые значения

```text
denoise: 0.50–0.62
steps: 28–35
cfg: 5.5–6.5
ControlNet Canny strength: 0.45–0.65
IP-Adapter weight: 0.25–0.45
```

## 2. main_pass.json — главный дом/фасад

Назначение: взять результат `first_pass` и доработать только главный дом по маске `blockout/<face>_mask_main.png`.

### Входные placeholder'ы

```text
FIRST_PASS_IMAGE     # comfy_output/<face>_first.png
FACE_COLOR           # то же самое, для совместимости, если ваш workflow использует старое имя
MAIN_MASK            # blockout/<face>_mask_main.png, белый = главный дом
FACE_MASK            # то же самое, для совместимости
FACE_CANNY           # control/<face>_canny.png
STREET_REFERENCE     # source/street_reference_collage.png
SAVE_PREFIX
MAIN_POSITIVE_PROMPT
MAIN_NEGATIVE_PROMPT
```

### Вариант A: inpaint workflow

Лучший вариант, если используете VAE Encode for Inpaint / Set Latent Noise Mask:

```text
Load Image FIRST_PASS_IMAGE → VAE Encode for Inpaint / VAE Encode
Load Image MAIN_MASK → mask input / Set Latent Noise Mask
Load Image FACE_CANNY → ControlNet Canny
Load Image STREET_REFERENCE → IP-Adapter с большим весом
KSampler denoise 0.35–0.50
VAE Decode → Save Image SAVE_PREFIX
```

### Вариант B: обычный img2img + маска через mask-ноды

Если inpaint-ноды отсутствуют, можно собрать обычный img2img, но обязательно использовать маску, чтобы изменения ограничивались главным домом.

### Рекомендуемые значения

```text
denoise: 0.35–0.50
steps: 24–32
cfg: 5.0–6.2
ControlNet Canny strength: 0.35–0.55
IP-Adapter weight: 0.70–0.90
```

## 3. Запуск

Только общий проход:

```bash
python master.py run-comfy --project tyumen_house --stage first
```

Только проход главного дома:

```bash
python master.py run-comfy --project tyumen_house --stage main
```

Оба прохода:

```bash
python master.py run-comfy --project tyumen_house --stage all
```

Ограничить грани:

```bash
python master.py run-comfy --project tyumen_house --stage main --faces front,right
```

По умолчанию `main`-проход автоматически пропускает грани, где маска главного дома пустая:

```bash
--auto-skip-empty-mask
```

Если нужно принудительно прогнать даже пустые маски:

```bash
--no-auto-skip-empty-mask
```

## 4. Что делает мастер с результатами

После каждого ComfyUI prompt мастер теперь сам скачивает первое изображение из ComfyUI history:

```text
first pass → projects/<project>/comfy_output/<face>_first.png
main pass  → projects/<project>/comfy_output/<face>_final.png
```

Если `main`-проход пропущен для грани, мастер копирует:

```text
<face>_first.png → <face>_final.png
```

Поэтому `stitch` больше не требует ручного копирования файлов из ComfyUI/output.
