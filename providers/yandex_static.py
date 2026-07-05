# -*- coding: utf-8 -*-
"""Yandex Static Maps downloader for VR Pano Master.

Important distinction:

1) Current official Static API v1:
   https://static-maps.yandex.ru/v1?apikey=...
   Supports vector/map styles via maptype=map|driving|transit|admin.
   It does NOT use the old `l=sat` layer parameter.

2) Legacy/enterprise Static API 1.x:
   https://static-maps.yandex.ru/1.x/?l=sat...
   https://enterprise.static-maps.yandex.ru/1.x/?key=...
   Historically supports satellite layer `l=sat`.

For commercial usage, verify Yandex license/plan. If you need official
commercial satellite static images, you likely need the enterprise 1.x endpoint
and a `key` for it, not a regular v1 `apikey`.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

import requests
from PIL import Image, ImageDraw

WEB_MERCATOR_SIZE = 256


@dataclass
class StaticMapOptions:
    api_key: str
    lat: float
    lon: float
    zoom: int = 17
    out_size: int = 2048
    tile_w: int = 650
    tile_h: int = 450
    layer: str = "sat"          # sat/map for 1.x; map/driving/transit/admin for v1 maptype
    lang: str = "ru_RU"         # v1: ru_RU; 1.x: converted to ru-RU automatically
    scale: Optional[int] = None
    sleep_s: float = 0.12
    retries: int = 3
    draw_center: bool = False
    api_version: str = "auto"   # auto|v1|1x|enterprise-1x
    allow_legacy_sat: bool = True


def meters_per_pixel(lat: float, z: int) -> float:
    # WebMercator ground resolution at latitude. Good enough for district-scale alignment.
    return math.cos(math.radians(lat)) * 2 * math.pi * 6378137.0 / (WEB_MERCATOR_SIZE * (2 ** z))


def lonlat_to_world_px(lon: float, lat: float, z: int) -> tuple[float, float]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    siny = math.sin(math.radians(lat))
    n = WEB_MERCATOR_SIZE * (2 ** z)
    x = (lon + 180.0) / 360.0 * n
    y = (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi)) * n
    return x, y


def world_px_to_lonlat(x: float, y: float, z: int) -> tuple[float, float]:
    n = WEB_MERCATOR_SIZE * (2 ** z)
    lon = x / n * 360.0 - 180.0
    yy = 0.5 - y / n
    lat = 90.0 - 360.0 * math.atan(math.exp(-yy * 2.0 * math.pi)) / math.pi
    return lon, lat


def effective_api_version(opts: StaticMapOptions) -> str:
    if opts.api_version != "auto":
        return opts.api_version
    # The modern official v1 Static API does not support old l=sat. Use legacy 1.x
    # for satellite if explicitly allowed, otherwise use v1 map.
    if opts.layer in {"sat", "satellite", "hybrid", "skl"}:
        if opts.allow_legacy_sat:
            return "1x"
        return "v1"
    return "v1"


def legacy_lang(lang: str) -> str:
    return (lang or "ru_RU").replace("_", "-")


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    q = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        if k.lower() in {"apikey", "key"}:
            v = "***REDACTED***"
        q.append((k, v))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def build_static_url(opts: StaticMapOptions, center_lon: float, center_lat: float) -> str:
    version = effective_api_version(opts)

    if version == "v1":
        if opts.layer in {"sat", "satellite", "hybrid", "skl"}:
            raise ValueError(
                "Yandex Static API v1 does not support satellite layer `l=sat`. "
                "Use --api-version 1x / enterprise-1x for satellite, or --layer map for official v1 map."
            )
        params = {
            "apikey": opts.api_key,
            "ll": f"{center_lon:.7f},{center_lat:.7f}",
            "z": str(opts.zoom),
            "size": f"{opts.tile_w},{opts.tile_h}",
            "lang": opts.lang,
            "maptype": opts.layer or "map",
        }
        if opts.scale:
            params["scale"] = str(opts.scale)
        return "https://static-maps.yandex.ru/v1?" + urlencode(params)

    if version == "enterprise-1x":
        base = "https://enterprise.static-maps.yandex.ru/1.x/"
        key_param = "key"
    elif version == "1x":
        base = "https://static-maps.yandex.ru/1.x/"
        # Public 1.x historically does not require a key. If provided, keep `key`
        # instead of v1 `apikey` for compatibility with enterprise-like configs.
        key_param = "key"
    else:
        raise ValueError(f"Unknown Yandex static api_version: {opts.api_version}")

    layer = opts.layer
    if layer == "satellite":
        layer = "sat"
    params = {
        "ll": f"{center_lon:.7f},{center_lat:.7f}",
        "z": str(opts.zoom),
        "size": f"{opts.tile_w},{opts.tile_h}",
        "l": layer,
        "lang": legacy_lang(opts.lang),
    }
    if opts.api_key:
        params[key_param] = opts.api_key
    if opts.scale:
        # old API may ignore this; retained only if user passes it
        params["scale"] = str(opts.scale)
    return base + "?" + urlencode(params)


def fetch_image(url: str, retries: int = 3) -> Image.Image:
    headers = {"User-Agent": "VR-Pano-Master/0.2"}
    last_msg = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code >= 400:
                body = (r.text or "")[:1000].replace("\n", " ")
                last_msg = f"HTTP {r.status_code}: {body}; URL={redact_url(url)}"
                time.sleep(0.5 * attempt)
                continue
            im = Image.open(BytesIO(r.content)).convert("RGB")
            return im
        except Exception as e:
            last_msg = f"{type(e).__name__}: {e}; URL={redact_url(url)}"
            time.sleep(0.5 * attempt)
    raise RuntimeError(f"Yandex Static image fetch failed after {retries} retries: {last_msg}")


def download_satellite_mosaic(opts: StaticMapOptions, out_path: Path) -> Path:
    """Download a stitched image centered on opts.lat/lon.

    The requested output is `out_size x out_size`. Internally we request
    small static-map images and place them by WebMercator pixel offsets.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    version = effective_api_version(opts)
    if opts.layer in {"sat", "satellite", "hybrid"} and version == "1x":
        print("⚠ Satellite requested. Using legacy static-maps.yandex.ru/1.x with l=sat.")
        print("   For commercial official usage, verify license or use enterprise.static-maps.yandex.ru/1.x with --api-version enterprise-1x.")
    elif version == "v1":
        print("ℹ Using official Yandex Static API v1. This is map style, not satellite, unless Yandex adds satellite maptype in your plan.")

    cx, cy = lonlat_to_world_px(opts.lon, opts.lat, opts.zoom)

    cols = math.ceil(opts.out_size / opts.tile_w) + 2
    rows = math.ceil(opts.out_size / opts.tile_h) + 2
    mosaic_w = cols * opts.tile_w
    mosaic_h = rows * opts.tile_h
    canvas = Image.new("RGB", (mosaic_w, mosaic_h), (0, 0, 0))

    canvas_cx = mosaic_w / 2
    canvas_cy = mosaic_h / 2

    for row in range(rows):
        for col in range(cols):
            tile_center_canvas_x = col * opts.tile_w + opts.tile_w / 2
            tile_center_canvas_y = row * opts.tile_h + opts.tile_h / 2
            dx = tile_center_canvas_x - canvas_cx
            dy = tile_center_canvas_y - canvas_cy
            center_lon, center_lat = world_px_to_lonlat(cx + dx, cy + dy, opts.zoom)
            url = build_static_url(opts, center_lon, center_lat)
            print(f"⬇ Yandex Static tile {row+1}/{rows}, {col+1}/{cols}: {center_lat:.6f},{center_lon:.6f}")
            im = fetch_image(url, retries=opts.retries)
            if im.size != (opts.tile_w, opts.tile_h):
                im = im.resize((opts.tile_w, opts.tile_h), Image.Resampling.LANCZOS)
            canvas.paste(im, (col * opts.tile_w, row * opts.tile_h))
            if opts.sleep_s:
                time.sleep(opts.sleep_s)

    left = int((mosaic_w - opts.out_size) / 2)
    top = int((mosaic_h - opts.out_size) / 2)
    crop = canvas.crop((left, top, left + opts.out_size, top + opts.out_size))

    if opts.draw_center:
        d = ImageDraw.Draw(crop)
        c = opts.out_size // 2
        d.line((c - 18, c, c + 18, c), fill=(255, 40, 40), width=3)
        d.line((c, c - 18, c, c + 18), fill=(255, 40, 40), width=3)

    crop.save(out_path, quality=95)
    return out_path
