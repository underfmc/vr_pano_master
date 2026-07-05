# ComfyUI workflow для одной грани cubemap

Мастер ожидает, что вы экспортируете workflow из ComfyUI в **API format** и укажете путь в `projects/<project>/config.yaml`:

```yaml
comfy:
  workflow_api_json: workflow_templates/my_face_workflow_api.json
```

## Обязательные placeholder-строки

В API JSON должны встречаться такие строки, чтобы `master.py run-comfy` заменил их автоматически:

- `FACE_COLOR` — путь к `blockout/front_color.png`, `right_color.png` и т.д.
- `FACE_CANNY` — путь к `control/front_canny.png`, `right_canny.png` и т.д.
- `STREET_REFERENCE` — путь к `source/street_reference_collage.png`.
- `SAVE_PREFIX` — префикс сохранения для текущей грани.

## Рекомендуемый граф

```text
Load Image FACE_COLOR
  -> VAE Encode
  -> KSampler latent

Load Image FACE_CANNY
  -> Apply ControlNet Canny

Load Image STREET_REFERENCE
  -> CLIP Vision Encode
  -> IP-Adapter Apply

CheckpointLoaderSimple
CLIPTextEncode positive/negative
KSampler
VAEDecode
SaveImage SAVE_PREFIX
```

## Модели

- Checkpoint: Realistic Vision / epiCRealism / Photon SD 1.5.
- ControlNet: `control_v11p_sd15_canny`; позже можно добавить `depth`.
- IP-Adapter: `ip-adapter-plus_sd15.safetensors` или `ip-adapter_sd15.safetensors`.
- CLIP Vision: ViT-H-14.

## Стартовые настройки

```text
face size: 1024x1024
sampler: DPM++ 2M Karras
steps: 28–35
CFG: 5.5–6.5
denoise: 0.45–0.65
ControlNet Canny strength: 0.45–0.65
IP-Adapter weight общий: 0.25–0.45
IP-Adapter weight для главного дома/inpaint: 0.75–0.90
```

## Prompt

```text
photorealistic aerial drone 360 panorama of a Russian residential district, realistic apartment buildings, courtyards, roads, parking lots, sidewalks, trees, overcast daylight, natural colors, real estate aerial photography, consistent lighting, realistic roofs and facades, high detail
```

## Negative

```text
cartoon, illustration, 3d render, game asset, fantasy city, american suburb, distorted buildings, melted windows, broken roads, text, labels, watermark, logo, fake map markers, satellite map texture, blurry, low quality
```
