# Следующие шаги после MVP-мастера

## 1. Карта выбора дома

Frontend: MapLibre/Leaflet/Yandex Maps JS API.

Пользователь выбирает точку → backend вызывает:

```bash
python master.py init --project <id> --lat <lat> --lon <lon> --levels <n>
python master.py fetch-osm --project <id>
```

Дальше UI показывает контур ближайшего OSM-здания и просит подтвердить.

## 2. Источник спутника

Сейчас мастер ожидает файл:

```text
projects/<project>/source/satellite_medium.png
```

Для production нужен provider:

- официальный API/тайлы с лицензией;
- собственная дрон-съёмка;
- муниципальные ортофото;
- разрешённый коммерческий поставщик.

## 3. Yandex панорамы

Подключается внешний `yandex-pano-downloader`. Для коммерческого продукта проверьте лицензию/разрешение.

## 4. Улучшение 3D-болванки

- Более точная ориентация камеры на главный дом.
- Выбор 3–5 ракурсов и preview перед ComfyUI.
- Depth pass из Blender.
- Маска главного дома для отдельного inpaint.

## 5. WebVR слой

После получения `output/aerial_panorama_360.jpg` нужно собрать Three.js viewer:

- сферический skybox;
- плоскость SVG/PNG-планировки над крышей;
- переключение этажей/секций;
- POI-маркеры по координатам;
- карточки квартир и форма заявки.
