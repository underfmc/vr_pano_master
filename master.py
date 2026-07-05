#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Semi-automatic wizard for VR aerial panorama generation."""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import requests
import yaml
try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*args, **kwargs):
        return False
from PIL import Image, ImageOps, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent
PROJECTS = ROOT / "projects"
FACES = ["front", "right", "back", "left", "up", "down"]

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

# Camera presets are intentionally expressed in project-local meters.
# In main_relative placement, offsets are relative to the main building center:
# X/east positive, Y/north positive, Z = main building height + height_above_main_m.
CAMERA_PRESETS: Dict[str, Dict[str, Any]] = {
    "facade_se_low": {
        "description": "Sales default: southeast diagonal, low drone, facade + courtyard visibility",
        "placement": "main_relative",
        "offset_east_m": 55.0,
        "offset_north_m": -40.0,
        "height_above_main_m": 6.0,
    },
    "facade_sw_low": {
        "description": "Southwest diagonal, low drone",
        "placement": "main_relative",
        "offset_east_m": -55.0,
        "offset_north_m": -40.0,
        "height_above_main_m": 6.0,
    },
    "facade_ne_low": {
        "description": "Northeast diagonal, low drone",
        "placement": "main_relative",
        "offset_east_m": 55.0,
        "offset_north_m": 40.0,
        "height_above_main_m": 6.0,
    },
    "facade_nw_low": {
        "description": "Northwest diagonal, low drone",
        "placement": "main_relative",
        "offset_east_m": -55.0,
        "offset_north_m": 40.0,
        "height_above_main_m": 6.0,
    },
    "courtyard_south": {
        "description": "Camera south of the building, good for courtyard/street side",
        "placement": "main_relative",
        "offset_east_m": 0.0,
        "offset_north_m": -65.0,
        "height_above_main_m": 8.0,
    },
    "courtyard_east": {
        "description": "Camera east of the building, side facade emphasis",
        "placement": "main_relative",
        "offset_east_m": 65.0,
        "offset_north_m": 0.0,
        "height_above_main_m": 8.0,
    },
    "roof_near": {
        "description": "Very close to roof, useful for plan overlay tests, not best for full marketing panorama",
        "placement": "main_relative",
        "offset_east_m": 28.0,
        "offset_north_m": -22.0,
        "height_above_main_m": 4.0,
    },
    "district_overview": {
        "description": "Higher overview, less facade detail, more district context",
        "placement": "main_relative",
        "offset_east_m": 90.0,
        "offset_north_m": -80.0,
        "height_above_main_m": 28.0,
    },
}

COMFY_PRESETS: Dict[str, Dict[str, Any]] = {
    "geometry_safe": {
        "description": "Maximum geometry preservation, weak IP-Adapter; use when first pass hallucinates facades/buildings",
        "steps": 30,
        "cfg": 6.0,
        "denoise": 0.52,
        "controlnet_strength": 0.72,
        "controlnet_start": 0.0,
        "controlnet_end": 0.78,
        "ipadapter_weight": 0.06,
        "ipadapter_start": 0.0,
        "ipadapter_end": 0.45,
        "main_steps": 28,
        "main_cfg": 5.8,
        "main_denoise": 0.46,
        "main_controlnet_strength": 0.38,
        "main_controlnet_start": 0.0,
        "main_controlnet_end": 0.55,
        "main_ipadapter_weight": 0.78,
        "main_ipadapter_start": 0.0,
        "main_ipadapter_end": 0.80,
        "main_min_mask_coverage_pct": 1.5,
    },
    "texture_safe": {
        "description": "Recommended next step: visible texturing without strong hallucination; blue sky prompt",
        "steps": 32,
        "cfg": 6.0,
        "denoise": 0.64,
        "controlnet_strength": 0.58,
        "controlnet_start": 0.0,
        "controlnet_end": 0.70,
        "ipadapter_weight": 0.12,
        "ipadapter_start": 0.0,
        "ipadapter_end": 0.55,
        "main_steps": 28,
        "main_cfg": 5.8,
        "main_denoise": 0.40,
        "main_controlnet_strength": 0.45,
        "main_controlnet_start": 0.0,
        "main_controlnet_end": 0.60,
        "main_ipadapter_weight": 0.68,
        "main_ipadapter_start": 0.0,
        "main_ipadapter_end": 0.75,
        "main_min_mask_coverage_pct": 1.5,
    },
    "balanced": {
        "description": "Balanced first pass after geometry is stable",
        "steps": 32,
        "cfg": 6.0,
        "denoise": 0.60,
        "controlnet_strength": 0.58,
        "controlnet_start": 0.0,
        "controlnet_end": 0.70,
        "ipadapter_weight": 0.18,
        "ipadapter_start": 0.0,
        "ipadapter_end": 0.60,
        "main_steps": 28,
        "main_cfg": 5.8,
        "main_denoise": 0.46,
        "main_controlnet_strength": 0.36,
        "main_controlnet_end": 0.55,
        "main_ipadapter_weight": 0.80,
        "main_ipadapter_end": 0.80,
        "main_min_mask_coverage_pct": 1.5,
    },
    "no_ip_first": {
        "description": "Disable IP-Adapter influence in first pass; style only in main masked pass",
        "steps": 30,
        "cfg": 6.0,
        "denoise": 0.54,
        "controlnet_strength": 0.75,
        "controlnet_start": 0.0,
        "controlnet_end": 0.82,
        "ipadapter_weight": 0.0,
        "ipadapter_start": 0.0,
        "ipadapter_end": 0.0,
        "main_steps": 28,
        "main_cfg": 5.8,
        "main_denoise": 0.48,
        "main_controlnet_strength": 0.38,
        "main_controlnet_end": 0.55,
        "main_ipadapter_weight": 0.82,
        "main_ipadapter_end": 0.80,
        "main_min_mask_coverage_pct": 1.5,
    },
}


def load_cfg(project: str) -> Dict[str, Any]:
    path = PROJECTS / project / "config.yaml"
    if not path.exists():
        raise SystemExit(f"Config not found: {path}. Run init first.")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_cfg(project: str, cfg: Dict[str, Any]) -> None:
    path = PROJECTS / project / "config.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def project_root(project: str) -> Path:
    return PROJECTS / project


def ensure_dirs(project: str) -> None:
    base = project_root(project)
    for d in [
        "source/street",
        "source/osm",
        "source/satellite",
        "blockout",
        "control",
        "comfy_output",
        "output",
        "logs",
        "web/assets/panorama",
    ]:
        (base / d).mkdir(parents=True, exist_ok=True)


def default_yandex_pano_script() -> Path:
    return ROOT / "tools" / "yandex-pano-downloader" / "pano.py"


def setup_yandex_pano(args: argparse.Namespace) -> None:
    """Download yandex-pano-downloader from GitHub into local tools/.

    Uses git clone when available; otherwise downloads raw pano.py and requirements.txt.
    """
    load_dotenv(ROOT / ".env")
    target = Path(args.dir) if args.dir else ROOT / "tools" / "yandex-pano-downloader"
    target.parent.mkdir(parents=True, exist_ok=True)
    repo = "https://github.com/zer0-dev/yandex-pano-downloader.git"
    if target.exists() and (target / "pano.py").exists() and not args.force:
        print(f"✅ yandex-pano-downloader already exists: {target}")
    else:
        if target.exists() and args.force:
            shutil.rmtree(target)
        try:
            print(f"⬇ Cloning {repo} -> {target}")
            subprocess.check_call(["git", "clone", "--depth", "1", repo, str(target)])
        except Exception as e:
            print(f"⚠ git clone failed: {e}")
            print("⬇ Falling back to raw GitHub downloads")
            target.mkdir(parents=True, exist_ok=True)
            raw_base = "https://raw.githubusercontent.com/zer0-dev/yandex-pano-downloader/main"
            for name in ["pano.py", "requirements.txt", "LICENSE", "README.md"]:
                url = f"{raw_base}/{name}"
                try:
                    urllib.request.urlretrieve(url, target / name)
                    print(f"  saved {name}")
                except Exception as ee:
                    if name in ["pano.py", "requirements.txt"]:
                        raise RuntimeError(f"Failed to download {url}: {ee}")
                    print(f"  skip {name}: {ee}")
    if args.install_deps:
        req = target / "requirements.txt"
        if req.exists():
            print(f"📦 Installing yandex-pano-downloader dependencies from {req}")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(req)])
        else:
            print("⚠ requirements.txt not found; installing common dependencies")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "aiohttp", "Pillow", "progressbar2"])
    print("\nAdd this to .env if it is not set:")
    pano_path = str(target / "pano.py").replace("\\", "/")
    print(f"YANDEX_PANO_SCRIPT={pano_path}")


def find_file(base: Path, filename: str) -> Path | None:
    if not base.exists() or not filename:
        return None
    direct = base / filename
    if direct.exists():
        return direct
    for p in base.rglob(filename):
        return p
    return None


def doctor(args: argparse.Namespace) -> None:
    """Check local paths, keys and model files."""
    load_dotenv(ROOT / ".env")
    comfy_path = Path(args.comfy or os.getenv("COMFYUI_PATH", "C:/dev_shir/IRR_2026/furn_gen/ComfyUI-master"))
    blender = Path(args.blender or os.getenv("BLENDER_EXE", "C:/Users/lol07/AppData/Local/Programs/Blender 3D/blender.exe"))
    checkpoint = args.checkpoint or os.getenv("COMFY_CHECKPOINT", "Realistic_Vision_V5.1.safetensors")
    canny = args.canny or os.getenv("COMFY_CONTROLNET_CANNY", "diffusion_pytorch_model.safetensors")
    print("🔎 VR Pano Master doctor")
    checks = []
    checks.append(("ComfyUI path", comfy_path.exists(), comfy_path))
    checks.append(("Blender exe", blender.exists(), blender))
    checks.append(("Yandex pano script", default_yandex_pano_script().exists() or bool(os.getenv("YANDEX_PANO_SCRIPT")), os.getenv("YANDEX_PANO_SCRIPT") or default_yandex_pano_script()))
    checks.append(("Yandex Static API key", bool(os.getenv("YANDEX_STATIC_API_KEY") or os.getenv("YANDEX_MAPS_API_KEY")), "YANDEX_STATIC_API_KEY / YANDEX_MAPS_API_KEY"))
    ckpt_path = find_file(comfy_path / "models" / "checkpoints", checkpoint)
    cn_path = find_file(comfy_path / "models" / "controlnet", canny)
    checks.append((f"Checkpoint {checkpoint}", ckpt_path is not None, ckpt_path or comfy_path / "models/checkpoints" / checkpoint))
    checks.append((f"ControlNet canny {canny}", cn_path is not None, cn_path or comfy_path / "models/controlnet" / canny))
    for name, ok, val in checks:
        print(("✅" if ok else "❌") + f" {name}: {val}")
    if not all(ok for _, ok, _ in checks):
        print("\nИсправьте ❌ пункты. Для yandex-pano-downloader можно выполнить:")
        print("python master.py setup-yandex-pano --install-deps")


def init_project(args: argparse.Namespace) -> None:
    project = args.project
    ensure_dirs(project)
    height = args.height or int(args.levels * args.floor_height)
    cfg = {
        "project": {
            "id": project,
            "root": str(project_root(project).as_posix()),
            "lat": float(args.lat),
            "lon": float(args.lon),
            "radius_m": int(args.radius),
        },
        "main_building": {
            "levels": int(args.levels),
            "floor_height_m": float(args.floor_height),
            "height_m": float(height),
            "roof_type": args.roof_type,
        },
        "camera": {
            "preset": args.camera_preset,
            "altitude_m": float(args.camera_altitude),
            "offset_east_m": float(args.camera_east),
            "offset_north_m": float(args.camera_north),
            "placement": "main_relative",
            "height_above_main_m": float(args.camera_height_above_main),
        },
        "camera_presets": CAMERA_PRESETS,
        "providers": {
            "accuracy_geometry": "osm",
            "ground": "osm_vector",
            "facade_reference": "yandex_panorama",
            "poi": "disabled_for_now",
        },
        "render": {
            "cube_face_size": int(args.face_size),
            "engine": "BLENDER_EEVEE",
            "ground_source": "osm_vector",
            "ground_size_m": int(args.radius) * 2,
            "skip_ai_up_face": True,
            "postprocess_sky": False,
            "building_color": [0.18, 0.19, 0.20, 1],
            "main_building_color": [0.22, 0.20, 0.18, 1],
            "road_color": [0.12, 0.13, 0.13, 1],
            "vector_offset_east_m": 0.0,
            "vector_offset_north_m": 0.0,
            "vector_scale_multiplier": 1.0,
            "satellite_offset_east_m": 0.0,
            "satellite_offset_north_m": 0.0,
            "satellite_scale_multiplier": 1.0,
        },
        "ai": {
            "main_model": "claude-sonnet-5",
            "vision_model": "gemini-3-5-flash",
            "cheap_model": "qwen3-7-plus",
            "code_model": "claude-sonnet-5",
            "review_model": "gpt-5-5",
        },
        "prompts": {
            "first_positive": "photorealistic low altitude drone cubemap face of a Russian residential district, preserve exact blockout geometry and building footprints, same camera perspective, same roads and courtyards layout, realistic mid-rise apartment buildings, realistic concrete and brick facades, realistic roofs, asphalt roads, sidewalks, grass lawns, trees, clear pale blue sky above horizon, bright natural daylight, real estate aerial photography, high detail",
            "first_negative": "cartoon, illustration, anime, 3d render, game asset, lowpoly, grey clay render, untextured buildings, white plastic blocks, fantasy city, american suburb, changed building footprint, changed camera angle, extra buildings, extra floors, giant facade wall, close-up facade filling the frame, distorted buildings, melted windows, broken roads, duplicated buildings, fake labels, text, watermark, logo, map markers, satellite map texture, flat map, orthographic top down view, indoor ceiling, dark wall in the sky, dark gray sky, black sky, overcast ceiling, blurry, low quality, oversaturated, unrealistic shadows",
            "main_positive": "the main residential building matches the street reference photo, realistic facade material, same brick color, same window style, realistic roof, accurate building shape, photorealistic drone view, natural daylight, high detail",
            "main_negative": "wrong facade, wrong roof, extra floors, american style, luxury mansion, distorted windows, melted walls, text, logo, watermark, fake labels, blurry, low quality",
        },
        "comfy": {
            "first_pass_workflow_api_json": "workflow_templates/first_pass.json",
            "main_pass_workflow_api_json": "workflow_templates/main_pass.json",
            "workflow_api_json": "workflow_templates/first_pass.json",  # backward compatibility
            "output_prefix": "vrpano",
            "checkpoint": "Realistic_Vision_V5.1.safetensors",
            "controlnet_canny": "diffusion_pytorch_model.safetensors",
            "ipadapter": "ip-adapter_sd15.safetensors",
            "clip_vision": "model.safetensors",
            "face_size": int(args.face_size),
            "steps": 30,
            "cfg": 6.0,
            "denoise": 0.64,
            "controlnet_strength": 0.58,
            "controlnet_start": 0.0,
            "controlnet_end": 0.70,
            "ipadapter_weight": 0.12,
            "ipadapter_start": 0.0,
            "ipadapter_end": 0.55,
            "main_steps": 28,
            "main_cfg": 5.8,
            "main_denoise": 0.40,
            "main_controlnet_strength": 0.45,
            "main_controlnet_start": 0.0,
            "main_controlnet_end": 0.60,
            "main_ipadapter_weight": 0.68,
            "main_ipadapter_start": 0.0,
            "main_ipadapter_end": 0.75,
            "main_faces": "auto",
            "main_min_mask_coverage_pct": 1.5
        },
    }
    if args.camera_preset in CAMERA_PRESETS:
        preset = dict(CAMERA_PRESETS[args.camera_preset])
        preset.pop("description", None)
        cfg["camera"].update(preset)
        cfg["camera"]["preset"] = args.camera_preset
    save_cfg(project, cfg)
    print(f"✅ Project created: {project_root(project)}")
    print("Next: python master.py fetch-osm --project", project)


def overpass_query(lat: float, lon: float, radius: int, include_poi: bool = False) -> str:
    # Geometry around selected point. In OSM-accuracy mode, POI can be disabled
    # to reduce query size; POI will be handled later by a dedicated provider.
    poi_part = ""
    if include_poi:
        poi_part = f"""
  node(around:{radius},{lat},{lon})["amenity"];
  node(around:{radius},{lat},{lon})["shop"];
  node(around:{radius},{lat},{lon})["healthcare"];
  node(around:{radius},{lat},{lon})["public_transport"];
"""
    return f"""
[out:json][timeout:60];
(
  way(around:{radius},{lat},{lon})["building"];
  relation(around:{radius},{lat},{lon})["building"];
  way(around:{radius},{lat},{lon})["highway"];
  way(around:{radius},{lat},{lon})["landuse"];
  way(around:{radius},{lat},{lon})["leisure"];
  way(around:{radius},{lat},{lon})["natural"];
  way(around:{radius},{lat},{lon})["amenity"];
{poi_part});
out body geom;
"""


def overpass_request(query: str, endpoints: List[str], timeout: int = 120) -> Dict[str, Any]:
    """Request Overpass API with mirrors and better diagnostics.

    Overpass public endpoints sometimes return 406/429/504 because of load,
    temporary bans, query limits, or endpoint-specific policy. This helper tries
    several mirrors and two POST encodings before failing.
    """
    headers = {
        "User-Agent": "VR-Pano-Master/0.2 (+local real-estate panorama generator)",
        "Accept": "application/json, */*;q=0.8",
    }
    errors: List[str] = []
    for url in endpoints:
        if not url:
            continue
        print(f"  ↪ Overpass endpoint: {url}")
        attempts = [
            ("form", {"data": query}, headers),
            ("raw", query.encode("utf-8"), {**headers, "Content-Type": "text/plain; charset=utf-8"}),
        ]
        for mode, payload, hdrs in attempts:
            try:
                r = requests.post(url, data=payload, headers=hdrs, timeout=timeout)
                if r.status_code == 200:
                    return r.json()
                snippet = (r.text or "")[:500].replace("\n", " ")
                errors.append(f"{url} [{mode}] -> HTTP {r.status_code}: {snippet}")
                print(f"    ⚠ {mode} failed: HTTP {r.status_code}")
                # If endpoint says too many requests / unavailable, don't waste raw attempt too long.
                if r.status_code in {429, 502, 503, 504}:
                    time.sleep(2)
            except Exception as e:
                errors.append(f"{url} [{mode}] -> {type(e).__name__}: {e}")
                print(f"    ⚠ {mode} exception: {e}")
    raise SystemExit(
        "Could not fetch OSM data from Overpass.\n\n"
        + "\n".join(errors[-12:])
        + "\n\nTry: reduce radius (`--radius 300`), retry later, or use another endpoint with `--endpoint URL`."
    )


def fetch_osm(args: argparse.Namespace) -> None:
    cfg = load_cfg(args.project)
    lat = cfg["project"]["lat"]
    lon = cfg["project"]["lon"]
    radius = int(args.radius or cfg["project"].get("radius_m", 800))
    ensure_dirs(args.project)
    endpoints = []
    if getattr(args, "endpoint", None):
        endpoints.append(args.endpoint)
    endpoints.extend([e for e in OVERPASS_ENDPOINTS if e not in endpoints])
    print(f"🌍 Fetching OSM/Overpass around {lat},{lon}, radius {radius}m ...")
    query = overpass_query(lat, lon, radius, include_poi=not bool(args.no_poi))
    log_dir = project_root(args.project) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "overpass_query.ql").write_text(query, encoding="utf-8")
    data = overpass_request(query, endpoints=endpoints, timeout=int(args.timeout))
    raw_path = project_root(args.project) / "source/osm/overpass_raw.json"
    raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    buildings, roads, poi, areas = overpass_to_geojson(data, lat, lon)
    write_json(project_root(args.project) / "source/osm/buildings.geojson", buildings)
    write_json(project_root(args.project) / "source/osm/roads.geojson", roads)
    write_json(project_root(args.project) / "source/osm/poi.geojson", poi)
    write_json(project_root(args.project) / "source/osm/areas.geojson", areas)
    # Also create app-level poi.json with simplified fields.
    poi_simple = simplify_poi(poi, lat, lon)
    write_json(project_root(args.project) / "source/poi.json", poi_simple)
    print(f"✅ Saved OSM data to {project_root(args.project) / 'source/osm'}")
    print(f"✅ Buildings: {len(buildings.get('features', []))}")
    print(f"✅ Roads: {len(roads.get('features', []))}")
    print(f"✅ POI count: {len(poi_simple)}")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def overpass_to_geojson(data: Dict[str, Any], lat0: float, lon0: float) -> Tuple[Dict, Dict, Dict, Dict]:
    buildings, roads, poi, areas = [], [], [], []
    for el in data.get("elements", []):
        tags = el.get("tags", {}) or {}
        if el.get("type") == "way" and "geometry" in el:
            coords = [[p["lon"], p["lat"]] for p in el["geometry"]]
            if not coords:
                continue
            is_closed = coords[0] == coords[-1]
            if "building" in tags and is_closed:
                buildings.append(feature_polygon(coords, tags, el.get("id")))
            elif "highway" in tags:
                roads.append(feature_line(coords, tags, el.get("id")))
            elif any(k in tags for k in ["landuse", "leisure", "amenity"]) and is_closed:
                areas.append(feature_polygon(coords, tags, el.get("id")))
        elif el.get("type") == "node":
            tags = el.get("tags", {}) or {}
            if any(k in tags for k in ["amenity", "shop", "healthcare", "public_transport"]):
                poi.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [el["lon"], el["lat"]]},
                    "properties": {**tags, "osm_id": el.get("id")},
                })
    return fc(buildings), fc(roads), fc(poi), fc(areas)


def feature_polygon(coords: List[List[float]], props: Dict[str, Any], osm_id: Any) -> Dict:
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [coords]}, "properties": {**props, "osm_id": osm_id}}


def feature_line(coords: List[List[float]], props: Dict[str, Any], osm_id: Any) -> Dict:
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": {**props, "osm_id": osm_id}}


def fc(features: List[Dict]) -> Dict:
    return {"type": "FeatureCollection", "features": features}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))


def normalize_category(props: Dict[str, Any]) -> str:
    amenity = props.get("amenity")
    shop = props.get("shop")
    healthcare = props.get("healthcare")
    public_transport = props.get("public_transport")
    if amenity in ["school", "college", "university"]: return "school"
    if amenity == "kindergarten": return "kindergarten"
    if amenity in ["hospital", "clinic", "doctors"] or healthcare: return "hospital"
    if amenity == "pharmacy" or shop == "chemist": return "pharmacy"
    if shop in ["supermarket", "convenience", "mall"]: return "supermarket"
    if amenity in ["bus_station"] or public_transport: return "transport"
    if amenity in ["cafe", "restaurant", "fast_food"]: return "cafe"
    if amenity in ["bank", "atm"]: return "bank"
    return amenity or shop or healthcare or "other"


def simplify_poi(poi_fc: Dict, lat0: float, lon0: float) -> List[Dict]:
    items = []
    for f in poi_fc.get("features", []):
        lon, lat = f["geometry"]["coordinates"]
        props = f.get("properties", {})
        d = haversine_m(lat0, lon0, lat, lon)
        items.append({
            "id": str(props.get("osm_id") or uuid.uuid4()),
            "name": props.get("name") or props.get("name:ru") or normalize_category(props),
            "category": normalize_category(props),
            "lat": lat,
            "lon": lon,
            "distance_m": round(d),
            "walk_time_min": max(1, round(d / 80)),
            "source": "openstreetmap",
        })
    items.sort(key=lambda x: x["distance_m"])
    return items


def fetch_satellite_yandex(args: argparse.Namespace) -> None:
    """Download satellite image through official Yandex Static Maps API."""
    from providers.yandex_static import StaticMapOptions, download_satellite_mosaic, meters_per_pixel, effective_api_version
    load_dotenv(ROOT / ".env")
    cfg = load_cfg(args.project)
    lat = args.lat or cfg["project"]["lat"]
    lon = args.lon or cfg["project"]["lon"]
    key = args.api_key or os.getenv("YANDEX_STATIC_API_KEY") or os.getenv("YANDEX_MAPS_API_KEY")
    if args.no_key_param:
        key = ""
    if not key and args.api_version in {"v1", "enterprise-1x"}:
        raise SystemExit("Set YANDEX_STATIC_API_KEY / YANDEX_MAPS_API_KEY in .env or pass --api-key. For public legacy 1x you may use --no-key-param.")
    ensure_dirs(args.project)
    out = project_root(args.project) / "source/satellite_medium.png"
    opts = StaticMapOptions(
        api_key=key,
        lat=float(lat),
        lon=float(lon),
        zoom=int(args.zoom),
        out_size=int(args.size),
        tile_w=int(args.tile_w),
        tile_h=int(args.tile_h),
        layer=args.layer,
        lang=args.lang,
        scale=args.scale,
        sleep_s=float(args.sleep),
        draw_center=bool(args.draw_center),
        api_version=args.api_version,
        allow_legacy_sat=not bool(args.no_legacy_sat),
    )
    download_satellite_mosaic(opts, out)
    mpp = meters_per_pixel(float(lat), int(args.zoom))
    meta = {
        "provider": "yandex_static",
        "api_version": effective_api_version(opts),
        "center_lat": float(lat),
        "center_lon": float(lon),
        "zoom": int(args.zoom),
        "out_size_px": int(args.size),
        "meters_per_pixel": mpp,
        "coverage_width_m": int(args.size) * mpp,
        "coverage_height_m": int(args.size) * mpp,
        "north_up": True,
        "note": "Used by Blender to size the satellite texture plane correctly."
    }
    write_json(project_root(args.project) / "source/satellite_metadata.json", meta)
    # Keep also a copy in source/satellite for compatibility with older instructions.
    sat_dir = project_root(args.project) / "source/satellite"
    sat_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(out, sat_dir / "satellite_medium.png")
    print(f"✅ Satellite saved: {out}")
    print(f"✅ Satellite metadata: mpp={mpp:.3f}, coverage≈{meta['coverage_width_m']:.1f}m")


def fetch_yandex_pano(args: argparse.Namespace) -> None:
    load_dotenv(ROOT / ".env")
    cfg = load_cfg(args.project)
    lat = args.lat or cfg["project"]["lat"]
    lon = args.lon or cfg["project"]["lon"]
    script = args.script or os.getenv("YANDEX_PANO_SCRIPT")
    if not script:
        candidate = default_yandex_pano_script()
        if candidate.exists():
            script = str(candidate)
        else:
            raise SystemExit("YANDEX_PANO_SCRIPT is not set and tools/yandex-pano-downloader/pano.py is missing. Run: python master.py setup-yandex-pano --install-deps")
    out = project_root(args.project) / "source/street/yandex_pano_main.jpg"
    cmd = [sys.executable, script, "-c", f"{lat},{lon}", "-z", str(args.zoom), "-a", "-o", str(out)]
    print("▶", " ".join(cmd))
    subprocess.check_call(cmd)
    print(f"✅ Saved: {out}")


def make_collage(args: argparse.Namespace) -> None:
    src_dir = project_root(args.project) / "source/street"
    imgs = []
    for p in sorted(src_dir.glob("*.jpg")) + sorted(src_dir.glob("*.png")):
        try:
            im = Image.open(p).convert("RGB")
            imgs.append((p.name, im))
        except Exception:
            pass
    if not imgs:
        raise SystemExit(f"No street images found in {src_dir}")
    tile = 512
    canvas = Image.new("RGB", (1024, 1024), (20, 20, 20))
    draw = ImageDraw.Draw(canvas)
    for i, (name, im) in enumerate(imgs[:4]):
        # center-crop square; for equirect pano this is crude, but good for a first IP-Adapter style collage
        im = ImageOps.fit(im, (tile, tile), method=Image.Resampling.LANCZOS, centering=(0.5, 0.48))
        x = (i % 2) * tile
        y = (i // 2) * tile
        canvas.paste(im, (x, y))
        draw.rectangle([x, y, x+tile-1, y+28], fill=(0, 0, 0))
        draw.text((x+8, y+8), name[:50], fill=(255, 255, 255))
    out = project_root(args.project) / "source/street_reference_collage.png"
    canvas.save(out)
    print(f"✅ Collage saved: {out}")


def image_detail_score(path: Path) -> float:
    """Rough texture/detail score for blockout face validation."""
    from PIL import ImageFilter, ImageStat
    if not path.exists():
        return -1.0
    im = Image.open(path).convert("L").resize((256, 256))
    edges = im.filter(ImageFilter.FIND_EDGES)
    edge_mean = ImageStat.Stat(edges).mean[0]
    std = ImageStat.Stat(im).stddev[0]
    return float(edge_mean + 0.35 * std)


def swap_files(a: Path, b: Path) -> None:
    tmp = a.with_suffix(a.suffix + ".swap_tmp")
    if not a.exists() or not b.exists():
        return
    a.rename(tmp)
    b.rename(a)
    tmp.rename(b)


def validate_or_fix_blockout_orientation(project: str, fix: bool = True) -> None:
    """Detect common up/down inversion in blockout cubemap and optionally fix it.

    In a correct blockout cubemap:
      up_color.png   ≈ smooth sky
      down_color.png ≈ detailed ground/buildings
    If up is much more detailed than down, we swap up/down color and masks.
    """
    base = project_root(project) / "blockout"
    up = base / "up_color.png"
    down = base / "down_color.png"
    if not up.exists() or not down.exists():
        print("⚠ blockout orientation check skipped: up/down color files missing")
        return
    up_score = image_detail_score(up)
    down_score = image_detail_score(down)
    print(f"🔎 Cubemap up/down detail: up={up_score:.2f}, down={down_score:.2f}")
    # Sky is almost flat in our Blender blockout; ground has lots of satellite/geometry detail.
    inverted = up_score > max(18.0, down_score * 1.8) and down_score < 35.0
    if inverted:
        msg = "Detected likely up/down inversion: up_color contains ground, down_color contains sky."
        if fix:
            print("⚠ " + msg + " Swapping up/down color and masks.")
            for suffix in ["color", "mask_main"]:
                swap_files(base / f"up_{suffix}.png", base / f"down_{suffix}.png")
        else:
            print("⚠ " + msg)
    else:
        print("✅ Cubemap up/down orientation looks plausible")


def clean_sky(args: argparse.Namespace) -> None:
    print("⛔ clean-sky legacy postprocess is disabled permanently.")
    print("Reason: sky-mask detection can cover buildings and corrupt masks.")
    print("Use Blender World/Background sky only: run render-blockout again.")
    cfg = load_cfg(args.project)
    cfg.setdefault("render", {})["postprocess_sky"] = False
    save_cfg(args.project, cfg)


def mask_coverage_pct(mask_path: Path, threshold: int = 16) -> float:
    if not mask_path.exists():
        return 0.0
    im = Image.open(mask_path).convert("L")
    total = im.size[0] * im.size[1]
    if total <= 0:
        return 0.0
    nonzero = sum(1 for v in im.getdata() if v > threshold)
    return nonzero / total * 100.0


def inspect_masks(args: argparse.Namespace) -> None:
    """Report main-building mask coverage per face.

    If a face has near-zero mask coverage, main_pass cannot visibly change the
    main building on that face. Move the camera or choose another face/preset.
    """
    base = project_root(args.project) / "blockout"
    faces = selected_faces(args) if getattr(args, "faces", "all") else FACES
    for face in faces:
        p = base / f"{face}_mask_main.png"
        if not p.exists():
            print(f"❌ {face}: missing {p}")
            continue
        im = Image.open(p).convert("L")
        total = im.size[0] * im.size[1]
        vals = im.point(lambda v: 255 if v > 16 else 0)
        nonzero = sum(1 for v in vals.getdata() if v)
        pct = nonzero / total * 100
        status = "OK" if pct >= 1.5 else "LOW/EMPTY"
        print(f"{status:9s} {face:5s}: main mask coverage {pct:.2f}% ({nonzero}/{total})")


def list_comfy_presets(args: argparse.Namespace) -> None:
    print("Available Comfy presets:")
    for name, preset in COMFY_PRESETS.items():
        print(f"  {name}")
        print(f"    {preset.get('description', '')}")
        print(f"    first: denoise={preset.get('denoise')}, cn={preset.get('controlnet_strength')}, ip={preset.get('ipadapter_weight')}")
        print(f"    main: denoise={preset.get('main_denoise')}, cn={preset.get('main_controlnet_strength')}, ip={preset.get('main_ipadapter_weight')}, min_mask={preset.get('main_min_mask_coverage_pct', 1.5)}%")


def set_comfy_preset(args: argparse.Namespace) -> None:
    cfg = load_cfg(args.project)
    preset = dict(COMFY_PRESETS[args.preset])
    description = preset.pop("description", "")
    comfy = cfg.setdefault("comfy", {})
    comfy.update(preset)
    comfy["preset"] = args.preset
    # Always enforce the known local model names unless the user opts out later by editing config.
    comfy.setdefault("checkpoint", "Realistic_Vision_V5.1.safetensors")
    comfy.setdefault("controlnet_canny", "diffusion_pytorch_model.safetensors")
    comfy.setdefault("ipadapter", "ip-adapter_sd15.safetensors")
    comfy.setdefault("clip_vision", "model.safetensors")
    save_cfg(args.project, cfg)
    print(f"✅ Comfy preset applied: {args.preset}")
    if description:
        print(f"  {description}")
    for k in ["denoise", "controlnet_strength", "ipadapter_weight", "main_denoise", "main_controlnet_strength", "main_ipadapter_weight", "main_min_mask_coverage_pct"]:
        print(f"  {k}: {comfy.get(k)}")


def list_camera_presets(args: argparse.Namespace) -> None:
    print("Available camera presets:")
    for name, preset in CAMERA_PRESETS.items():
        print(f"  {name}")
        print(f"    {preset.get('description', '')}")
        print(f"    offset_east={preset.get('offset_east_m')}m, offset_north={preset.get('offset_north_m')}m, height_above_main={preset.get('height_above_main_m')}m")


def set_camera_preset(args: argparse.Namespace) -> None:
    cfg = load_cfg(args.project)
    preset = dict(CAMERA_PRESETS[args.preset])
    description = preset.pop("description", "")
    camera = cfg.setdefault("camera", {})
    camera.update(preset)
    camera["preset"] = args.preset
    if args.height_above_main is not None:
        camera["height_above_main_m"] = float(args.height_above_main)
    if args.offset_scale is not None:
        camera["offset_east_m"] = float(camera.get("offset_east_m", 0.0)) * float(args.offset_scale)
        camera["offset_north_m"] = float(camera.get("offset_north_m", 0.0)) * float(args.offset_scale)
    save_cfg(args.project, cfg)
    print(f"✅ Camera preset applied: {args.preset}")
    if description:
        print(f"  {description}")
    for k in ["placement", "offset_east_m", "offset_north_m", "height_above_main_m", "altitude_m"]:
        if k in camera:
            print(f"  {k}: {camera[k]}")
    print("Next: python master.py render-blockout --project", args.project)


def set_camera(args: argparse.Namespace) -> None:
    """Configure cubemap camera position.

    Recommended for facade visibility: main-relative placement, altitude a few
    meters above main building roof, and horizontal offset 35-80m from it.
    Cubemap itself is 360°, so there is no single target direction; position is
    what matters.
    """
    cfg = load_cfg(args.project)
    camera = cfg.setdefault("camera", {})
    camera["placement"] = args.placement
    if args.offset_east is not None:
        camera["offset_east_m"] = float(args.offset_east)
    if args.offset_north is not None:
        camera["offset_north_m"] = float(args.offset_north)
    if args.altitude is not None:
        camera["altitude_m"] = float(args.altitude)
    if args.height_above_main is not None:
        camera["height_above_main_m"] = float(args.height_above_main)
    save_cfg(args.project, cfg)
    print("✅ Camera updated:")
    for k, v in camera.items():
        print(f"  {k}: {v}")
    print("Next: python master.py render-blockout --project", args.project)


def set_osm_accuracy_mode(args: argparse.Namespace) -> None:
    """Configure project to use OSM as the single accuracy source.

    Blender will not use the satellite raster as the metric foundation. It will
    render a synthetic OSM-vector ground: buildings, roads and landuse polygons
    all come from OSM coordinates. Yandex panoramas remain only as facade/style
    reference for IP-Adapter.
    """
    cfg = load_cfg(args.project)
    cfg.setdefault("providers", {})
    cfg["providers"].update({
        "accuracy_geometry": "osm",
        "ground": "osm_vector",
        "facade_reference": "yandex_panorama",
        "poi": "disabled_for_now",
    })
    render = cfg.setdefault("render", {})
    render["ground_source"] = "osm_vector"
    render["ground_size_m"] = int(args.ground_size or (cfg.get("project", {}).get("radius_m", 800) * 2))
    render["postprocess_sky"] = False
    if args.reset_calibration:
        render["vector_offset_east_m"] = 0.0
        render["vector_offset_north_m"] = 0.0
        render["vector_scale_multiplier"] = 1.0
        render["satellite_offset_east_m"] = 0.0
        render["satellite_offset_north_m"] = 0.0
        render["satellite_scale_multiplier"] = 1.0
    save_cfg(args.project, cfg)
    print("✅ OSM accuracy mode enabled")
    print("  ground_source: osm_vector")
    print("  geometry: OSM buildings/roads/areas")
    print("  facade reference: Yandex panoramas / street collage")
    print("  POI: disabled_for_now")
    print("Next:")
    print(f"  python master.py fetch-osm --project {args.project} --no-poi")
    print(f"  python master.py render-blockout --project {args.project}")


def calibrate_geometry(args: argparse.Namespace) -> None:
    """Set or add calibration offsets between OSM vectors and satellite raster.

    Coordinate convention:
    - vector_offset_* moves OSM buildings/roads/areas.
    - satellite_offset_* moves only the satellite ground plane.
    - X/east positive = to the right/east.
    - Y/north positive = up/north.

    If the 3D building boxes are shifted north of the roofs on satellite,
    usually apply vector_offset_north_m negative, e.g. --vector-north -10 --add.
    """
    cfg = load_cfg(args.project)
    render = cfg.setdefault("render", {})
    fields = {
        "vector_offset_east_m": args.vector_east,
        "vector_offset_north_m": args.vector_north,
        "vector_scale_multiplier": args.vector_scale,
        "satellite_offset_east_m": args.satellite_east,
        "satellite_offset_north_m": args.satellite_north,
        "satellite_scale_multiplier": args.satellite_scale,
    }
    for key, val in fields.items():
        if val is None:
            continue
        is_scale = key in {"satellite_scale_multiplier", "vector_scale_multiplier"}
        old = float(render.get(key, 1.0 if is_scale else 0.0))
        if args.add and not is_scale:
            render[key] = old + float(val)
        elif args.add and is_scale:
            render[key] = old * float(val)
        else:
            render[key] = float(val)
    save_cfg(args.project, cfg)
    print("✅ Calibration saved to config.yaml:")
    for key in ["vector_offset_east_m", "vector_offset_north_m", "vector_scale_multiplier", "satellite_offset_east_m", "satellite_offset_north_m", "satellite_scale_multiplier"]:
        print(f"  {key}: {render.get(key)}")
    print("Next: python master.py render-blockout --project", args.project)


def validate_blockout(args: argparse.Namespace) -> None:
    validate_or_fix_blockout_orientation(args.project, fix=args.fix)


def render_blockout(args: argparse.Namespace) -> None:
    cfg = load_cfg(args.project)
    scene_cfg = project_root(args.project) / "scene_config.json"
    write_json(scene_cfg, cfg)
    load_dotenv(ROOT / ".env")
    blender = args.blender or os.getenv("BLENDER_EXE") or "blender"
    script = ROOT / "scripts/blender_blockout.py"
    cmd = [blender, "--background", "--python", str(script), "--", "--project", args.project]
    print("▶", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit(f"Blender blockout failed with exit code {result.returncode}")
    base = project_root(args.project)
    missing = [str(base / f"blockout/{face}_color.png") for face in FACES if not (base / f"blockout/{face}_color.png").exists()]
    missing += [str(base / f"blockout/{face}_mask_main.png") for face in FACES if not (base / f"blockout/{face}_mask_main.png").exists()]
    if missing:
        raise SystemExit("Blender finished, but expected blockout files are missing:\n" + "\n".join(missing[:20]))
    validate_or_fix_blockout_orientation(args.project, fix=True)
    if (cfg.get("render", {}) or {}).get("postprocess_sky", False):
        print("⚠ render.postprocess_sky is set in config, but legacy sky postprocess is disabled permanently to avoid covering buildings.")
        cfg.setdefault("render", {})["postprocess_sky"] = False
        save_cfg(args.project, cfg)
    print("✅ Blender blockout render complete")


def make_control_maps(args: argparse.Namespace) -> None:
    import cv2
    import numpy as np
    base = project_root(args.project)
    out_dir = base / "control"
    out_dir.mkdir(exist_ok=True)
    for face in FACES:
        img_path = base / f"blockout/{face}_color.png"
        if not img_path.exists():
            print(f"⚠ skip missing {img_path}")
            continue
        img = cv2.imread(str(img_path))
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, args.low, args.high)
        # thicken a little for ControlNet Canny/Lineart
        kernel = np.ones((2, 2), np.uint8)
        edges = cv2.dilate(edges, kernel, iterations=1)
        cv2.imwrite(str(out_dir / f"{face}_canny.png"), edges)
        print(f"✅ {face}_canny.png")


def ai_analyze_inputs(args: argparse.Namespace) -> None:
    """Use AITunnel vision model to analyze satellite/street references."""
    from pano_master.ai_assistant import ask_vision, extract_json_object
    load_dotenv(ROOT / ".env")
    base = project_root(args.project)
    images: List[Path] = []
    for rel in ["source/satellite_medium.png", "source/street_reference_collage.png"]:
        p = base / rel
        if p.exists():
            images.append(p)
    if not images:
        raise SystemExit("No input images found. Expected source/satellite_medium.png and/or source/street_reference_collage.png")
    system = "You are a strict visual QA assistant for a real-estate VR aerial panorama generation pipeline. Return only valid JSON."
    prompt = """
Analyze the provided satellite/street reference images for a ComfyUI pipeline.
Return JSON with keys:
- satellite_quality: good/medium/bad/unknown
- street_reference_quality: good/medium/bad/unknown
- facade_material
- facade_color
- roof_type
- roof_color
- window_style
- weather_lighting
- problems: array of strings
- recommended_first_prompt_additions
- recommended_main_prompt_additions
- recommended_ipadapter_weight_first: number 0.2-0.65
- recommended_ipadapter_weight_main: number 0.55-0.95
Do not invent legal/POI facts.
"""
    text = ask_vision(system, prompt, images)
    out_txt = base / "source/ai_input_analysis.txt"
    out_txt.write_text(text, encoding="utf-8")
    try:
        data = extract_json_object(text)
        write_json(base / "source/ai_input_analysis.json", data)
        print(f"✅ AI input analysis JSON: {base / 'source/ai_input_analysis.json'}")
    except Exception as e:
        print(f"⚠ Could not parse JSON: {e}")
        print(f"Raw answer saved: {out_txt}")


def ai_suggest_prompts(args: argparse.Namespace) -> None:
    """Use AITunnel main model to generate/update ComfyUI prompts and recommended params."""
    from pano_master.ai_assistant import ask_ai, extract_json_object
    load_dotenv(ROOT / ".env")
    cfg = load_cfg(args.project)
    base = project_root(args.project)
    poi_path = base / "source/poi.json"
    analysis_path = base / "source/ai_input_analysis.json"
    poi = []
    if poi_path.exists():
        try:
            poi = json.loads(poi_path.read_text(encoding="utf-8"))[:30]
        except Exception:
            poi = []
    analysis = {}
    if analysis_path.exists():
        try:
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        except Exception:
            analysis = {}
    system = "You are a prompt engineer for SD1.5 ComfyUI ControlNet/IP-Adapter real-estate aerial panorama generation. Return only valid JSON."
    user = json.dumps({
        "project_config": cfg,
        "visual_analysis": analysis,
        "top_poi_sample": poi,
        "task": "Create robust first-pass and main-building-pass prompts for cubemap faces. Keep labels/text/markers out of generated image. Russian residential district, photorealistic, no fake signs. Also recommend denoise/control/ipadapter settings. Return JSON keys: first_positive, first_negative, main_positive, main_negative, steps, cfg, denoise, main_steps, main_cfg, main_denoise."
    }, ensure_ascii=False, indent=2)
    text = ask_ai(system, user, role="main", temperature=0.15)
    out_txt = base / "source/ai_prompt_suggestion.txt"
    out_txt.write_text(text, encoding="utf-8")
    try:
        data = extract_json_object(text)
    except Exception as e:
        raise SystemExit(f"Could not parse AI JSON: {e}. Raw saved: {out_txt}")
    write_json(base / "source/ai_prompt_suggestion.json", data)
    print(f"✅ AI prompt suggestion: {base / 'source/ai_prompt_suggestion.json'}")
    if args.apply:
        cfg.setdefault("prompts", {})
        for k in ["first_positive", "first_negative", "main_positive", "main_negative"]:
            if k in data:
                cfg["prompts"][k] = data[k]
        cfg.setdefault("comfy", {})
        for k in ["steps", "cfg", "denoise", "main_steps", "main_cfg", "main_denoise"]:
            if k in data:
                cfg["comfy"][k] = data[k]
        save_cfg(args.project, cfg)
        print("✅ Applied prompts/settings to config.yaml")


def resolve_workflow_path(cfg: Dict[str, Any], key: str) -> Path:
    raw = cfg.get("comfy", {}).get(key)
    if not raw and key == "first_pass_workflow_api_json":
        raw = cfg.get("comfy", {}).get("workflow_api_json")
    if not raw:
        raise SystemExit(f"Missing comfy.{key} in project config.yaml")
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists() or "placeholder" in path.name:
        raise SystemExit(
            f"Workflow not found or placeholder: {path}\n"
            f"Put your exported ComfyUI API workflow there or update comfy.{key} in project config.yaml"
        )
    return path


def load_workflow(cfg: Dict[str, Any], key: str) -> Dict[str, Any]:
    path = resolve_workflow_path(cfg, key)
    print(f"📄 Workflow {key}: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def selected_faces(args: argparse.Namespace) -> List[str]:
    raw = getattr(args, "faces", None)
    if not raw or raw == "all":
        return list(FACES)
    result = [x.strip() for x in raw.split(",") if x.strip()]
    bad = [x for x in result if x not in FACES]
    if bad:
        raise SystemExit(f"Unknown faces: {bad}. Available: {FACES}")
    return result


def image_has_nonblack_pixels(path: Path, threshold: int = 8) -> bool:
    if not path.exists():
        return False
    try:
        im = Image.open(path).convert("L")
        extrema = im.getextrema()
        return bool(extrema and extrema[1] > threshold)
    except Exception:
        return False


def comfy_prompts(cfg: Dict[str, Any]) -> Dict[str, str]:
    p = cfg.get("prompts", {}) or {}
    return {
        "POSITIVE_PROMPT": p.get("first_positive", "photorealistic aerial drone view, realistic residential district"),
        "NEGATIVE_PROMPT": p.get("first_negative", "cartoon, text, watermark, blurry, low quality"),
        "MAIN_POSITIVE_PROMPT": p.get("main_positive", "the main residential building matches the street reference photo, photorealistic"),
        "MAIN_NEGATIVE_PROMPT": p.get("main_negative", "wrong facade, text, watermark, blurry, low quality"),
    }


SIDE_FACES = ["front", "right", "back", "left"]


def _rgb_dist(a, b) -> float:
    return ((int(a[0]) - int(b[0])) ** 2 + (int(a[1]) - int(b[1])) ** 2 + (int(a[2]) - int(b[2])) ** 2) ** 0.5


def detect_sky_mask(blockout_path: Path, threshold: float = 82.0) -> Image.Image:
    """Detect sky area connected to the top border in a blockout side face.

    We intentionally use the blockout, not generated image, because generated
    images may hallucinate windows/buildings in the sky. The mask is then used
    to force clean sky into first/final outputs.
    """
    im = Image.open(blockout_path).convert("RGB")
    w, h = im.size
    px = im.load()
    # Median-ish top color: use average of every 16th pixel in top 8 rows.
    samples = []
    for y in range(min(8, h)):
        for x in range(0, w, max(1, w // 64)):
            samples.append(px[x, y])
    if not samples:
        return Image.new("L", (w, h), 0)
    seed = tuple(sorted([c[i] for c in samples])[len(samples)//2] for i in range(3))

    from collections import deque
    visited = bytearray(w * h)
    mask = bytearray(w * h)
    q = deque()

    def qualifies(x, y):
        c = px[x, y]
        # Sky in blockout is low-saturation background; allow blue or gray.
        mx, mn = max(c), min(c)
        low_sat = (mx - mn) < 70
        # Restrict very bottom to avoid flood leaking through pale ground.
        return y < int(h * 0.88) and low_sat and _rgb_dist(c, seed) < threshold

    for x in range(w):
        if qualifies(x, 0):
            q.append((x, 0))
            visited[x] = 1

    while q:
        x, y = q.popleft()
        idx = y * w + x
        mask[idx] = 255
        for nx, ny in ((x+1, y), (x-1, y), (x, y+1), (x, y-1)):
            if nx < 0 or nx >= w or ny < 0 or ny >= h:
                continue
            nidx = ny * w + nx
            if visited[nidx]:
                continue
            visited[nidx] = 1
            if qualifies(nx, ny):
                q.append((nx, ny))

    m = Image.frombytes("L", (w, h), bytes(mask))
    # Slight feather avoids hard seam at skyline.
    return m.filter(ImageFilter.GaussianBlur(radius=2.0))


def make_blue_sky_image(size, top=(78, 135, 205), bottom=(205, 232, 250)) -> Image.Image:
    w, h = size
    sky = Image.new("RGB", (w, h))
    pix = sky.load()
    for y in range(h):
        t = y / max(1, h - 1)
        col = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        for x in range(w):
            pix[x, y] = col
    return sky


def force_blue_sky_on_face(project: str, face: str, image_path: Path) -> None:
    if face not in SIDE_FACES or not image_path.exists():
        return
    blockout = project_root(project) / f"blockout/{face}_color.png"
    if not blockout.exists():
        return
    im = Image.open(image_path).convert("RGB")
    mask = detect_sky_mask(blockout)
    if mask.size != im.size:
        mask = mask.resize(im.size, Image.Resampling.LANCZOS)
    sky = make_blue_sky_image(im.size)
    out = Image.composite(sky, im, mask)
    out.save(image_path)


def clean_blockout_sky(project: str) -> None:
    """Paint side-face blockout skies blue before Canny/Comfy.

    This avoids gray sky being interpreted as a wall/ceiling and later getting
    facade-window texture from img2img/IP-Adapter.
    """
    for face in SIDE_FACES:
        p = project_root(project) / f"blockout/{face}_color.png"
        if p.exists():
            force_blue_sky_on_face(project, face, p)


def create_sky_face(path: Path, size: int = 1024, top=(78, 135, 205), bottom=(190, 225, 250)) -> Path:
    """Create a clean cubemap up face. AI often paints building texture into the sky."""
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (size, size))
    pix = im.load()
    for y in range(size):
        t = y / max(1, size - 1)
        col = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        for x in range(size):
            pix[x, y] = col
    im.save(path)
    return path


def apply_comfy_env_overrides(params: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(params)
    env_map = {
        "checkpoint": "COMFY_CHECKPOINT",
        "controlnet_canny": "COMFY_CONTROLNET_CANNY",
        "ipadapter": "COMFY_IPADAPTER",
        "clip_vision": "COMFY_CLIP_VISION",
    }
    for key, env in env_map.items():
        if os.getenv(env):
            params[key] = os.getenv(env)
    return params


def run_comfy_first(args: argparse.Namespace) -> None:
    from pano_master.comfy_client import ComfyClient, patch_workflow_basic, extract_output_images
    load_dotenv(ROOT / ".env")
    cfg = load_cfg(args.project)
    url = args.url or os.getenv("COMFYUI_URL", "http://127.0.0.1:8188")
    client = ComfyClient(url)
    base = project_root(args.project)
    out_dir = base / "comfy_output"
    out_dir.mkdir(exist_ok=True)
    workflow = load_workflow(cfg, "first_pass_workflow_api_json")
    params = apply_comfy_env_overrides(dict(cfg.get("comfy", {}) or {}))

    for face in selected_faces(args):
        if face == "up" and (cfg.get("render", {}) or {}).get("skip_ai_up_face", True):
            sky = create_sky_face(out_dir / "up_first.png", size=int(params.get("face_size", 1024)))
            print(f"☁ Skipping AI for up face; generated clean sky: {sky}")
            continue
        print(f"🎨 ComfyUI FIRST PASS face: {face}")
        upload_subfolder = f"vr_pano_master/{args.project}/{face}/first"
        face_color_ref = client.upload_image(base / f"blockout/{face}_color.png", subfolder=upload_subfolder)
        face_canny_ref = client.upload_image(base / f"control/{face}_canny.png", subfolder=upload_subfolder)
        street_ref = client.upload_image(base / "source/street_reference_collage.png", subfolder=f"vr_pano_master/{args.project}/references")
        print(f"   uploaded: FACE_COLOR={face_color_ref}, FACE_CANNY={face_canny_ref}, STREET_REFERENCE={street_ref}")
        replacements = {
            **comfy_prompts(cfg),
            "FACE_COLOR": face_color_ref,
            "FACE_CANNY": face_canny_ref,
            "STREET_REFERENCE": street_ref,
            "SAVE_PREFIX": f"{cfg['comfy'].get('output_prefix','vrpano')}_{face}_first",
        }
        wf = patch_workflow_basic(workflow, replacements=replacements, params=params)
        result = client.queue_and_wait(wf)
        meta = out_dir / f"{face}_first_comfy_result.json"
        meta.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        saved = client.save_first_output_image(result, out_dir / f"{face}_first.png")
        if saved:
            print(f"✅ First pass image: {saved}")
        else:
            print(f"⚠ No image found in ComfyUI history for {face}. Metadata saved: {meta}")
            print(f"   Images in history: {extract_output_images(result)}")


def copy_first_to_final(project: str, faces: Iterable[str]) -> None:
    base = project_root(project)
    for face in faces:
        first = base / f"comfy_output/{face}_first.png"
        final = base / f"comfy_output/{face}_final.png"
        if first.exists() and not final.exists():
            shutil.copy2(first, final)
            print(f"↪ copied first→final for {face}: {final}")


def run_comfy_main(args: argparse.Namespace) -> None:
    from pano_master.comfy_client import ComfyClient, patch_workflow_basic, extract_output_images
    load_dotenv(ROOT / ".env")
    cfg = load_cfg(args.project)
    url = args.url or os.getenv("COMFYUI_URL", "http://127.0.0.1:8188")
    client = ComfyClient(url)
    base = project_root(args.project)
    out_dir = base / "comfy_output"
    out_dir.mkdir(exist_ok=True)
    workflow = load_workflow(cfg, "main_pass_workflow_api_json")
    c = cfg.get("comfy", {}) or {}
    params = apply_comfy_env_overrides(dict(c))
    # Patch main-pass KSampler if your node inputs are called steps/cfg/denoise.
    params["steps"] = c.get("main_steps", c.get("steps", 28))
    params["cfg"] = c.get("main_cfg", c.get("cfg", 5.8))
    params["denoise"] = c.get("main_denoise", 0.46)
    params["controlnet_strength"] = c.get("main_controlnet_strength", 0.38)
    params["controlnet_start"] = c.get("main_controlnet_start", 0.0)
    params["controlnet_end"] = c.get("main_controlnet_end", 0.55)
    params["ipadapter_weight"] = c.get("main_ipadapter_weight", 0.78)
    params["ipadapter_start"] = c.get("main_ipadapter_start", 0.0)
    params["ipadapter_end"] = c.get("main_ipadapter_end", 0.80)

    requested = selected_faces(args)
    processed: List[str] = []
    skipped: List[str] = []
    for face in requested:
        first = base / f"comfy_output/{face}_first.png"
        if not first.exists():
            # allow manual fallback if user named it final already
            alt = base / f"comfy_output/{face}_final.png"
            if alt.exists():
                first = alt
            else:
                raise SystemExit(f"Missing first pass image for {face}: {first}. Run --stage first first.")
        mask = base / f"blockout/{face}_mask_main.png"
        min_mask_pct = float(c.get("main_min_mask_coverage_pct", 1.5))
        mask_pct = mask_coverage_pct(mask)
        if args.auto_skip_empty_mask and mask_pct < min_mask_pct:
            print(f"⏭ main pass skip {face}: main mask coverage {mask_pct:.2f}% < {min_mask_pct:.2f}% ({mask})")
            skipped.append(face)
            continue

        print(f"🏢 ComfyUI MAIN PASS face: {face} (main mask coverage {mask_pct:.2f}%)")
        upload_subfolder = f"vr_pano_master/{args.project}/{face}/main"
        first_ref = client.upload_image(first, subfolder=upload_subfolder)
        mask_ref = client.upload_image(mask, subfolder=upload_subfolder)
        canny_ref = client.upload_image(base / f"control/{face}_canny.png", subfolder=upload_subfolder)
        street_ref = client.upload_image(base / "source/street_reference_collage.png", subfolder=f"vr_pano_master/{args.project}/references")
        print(f"   uploaded: FIRST_PASS_IMAGE={first_ref}, MAIN_MASK={mask_ref}, FACE_CANNY={canny_ref}, STREET_REFERENCE={street_ref}")
        replacements = {
            **comfy_prompts(cfg),
            "FIRST_PASS_IMAGE": first_ref,
            "FACE_COLOR": first_ref,        # compatibility with workflows using FACE_COLOR as input
            "MAIN_MASK": mask_ref,
            "FACE_MASK": mask_ref,
            "FACE_CANNY": canny_ref,
            "STREET_REFERENCE": street_ref,
            "SAVE_PREFIX": f"{cfg['comfy'].get('output_prefix','vrpano')}_{face}_main",
        }
        wf = patch_workflow_basic(workflow, replacements=replacements, params=params)
        result = client.queue_and_wait(wf)
        meta = out_dir / f"{face}_main_comfy_result.json"
        meta.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        saved = client.save_first_output_image(result, out_dir / f"{face}_final.png")
        if saved:
            print(f"✅ Main/final image: {saved}")
            processed.append(face)
        else:
            print(f"⚠ No image found in ComfyUI history for {face}. Metadata saved: {meta}")
            print(f"   Images in history: {extract_output_images(result)}")

    # Faces not processed by main pass still need final images for stitching.
    copy_first_to_final(args.project, skipped)
    print(f"✅ Main pass done. processed={processed}, skipped={skipped}")


def run_comfy(args: argparse.Namespace) -> None:
    """Run ComfyUI pipeline.

    Stages:
    - first: first_pass.json for all cubemap faces
    - main: main_pass.json for masked main building/facade enhancement
    - all: first then main
    """
    stage = getattr(args, "stage", "all")
    if stage in ["first", "all"]:
        run_comfy_first(args)
    if stage in ["main", "all"]:
        run_comfy_main(args)
    if stage == "first":
        print("ℹ First pass complete. Run `python master.py run-comfy --project ... --stage main` or `--stage all` next.")


def stitch(args: argparse.Namespace) -> None:
    import numpy as np
    import py360convert
    base = project_root(args.project)
    faces = {}
    mapping = {
        "front": "F",
        "right": "R",
        "back": "B",
        "left": "L",
        "up": "U",
        "down": "D",
    }
    for face, key in mapping.items():
        p = base / f"comfy_output/{face}_final.png"
        if not p.exists():
            p = base / f"comfy_output/{face}_first.png"
        if not p.exists():
            # fallback to blockout color for dry-run
            p = base / f"blockout/{face}_color.png"
        if not p.exists():
            raise SystemExit(f"Missing cube face: {face}. Expected {p}")
        faces[key] = np.array(Image.open(p).convert("RGB"))
    h = args.height
    w = h * 2
    eq = py360convert.c2e(faces, h, w, mode="bilinear", cube_format="dict")
    out = base / "output/aerial_panorama_360.jpg"
    Image.fromarray(eq.astype("uint8")).save(out, quality=92)
    shutil.copy2(out, base / "web/assets/panorama/aerial_panorama_360.jpg")
    print(f"✅ Panorama saved: {out}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="VR Pano Master semi-automatic wizard")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("doctor")
    s.add_argument("--comfy")
    s.add_argument("--blender")
    s.add_argument("--checkpoint")
    s.add_argument("--canny")
    s.set_defaults(func=doctor)

    s = sub.add_parser("setup-yandex-pano")
    s.add_argument("--dir", help="Where to download/clone yandex-pano-downloader. Default: tools/yandex-pano-downloader")
    s.add_argument("--force", action="store_true", help="Remove existing directory and download again")
    s.add_argument("--install-deps", action="store_true", help="Install downloader requirements into current Python environment")
    s.set_defaults(func=setup_yandex_pano)

    s = sub.add_parser("init")
    s.add_argument("--project", required=True)
    s.add_argument("--lat", required=True, type=float)
    s.add_argument("--lon", required=True, type=float)
    s.add_argument("--radius", type=int, default=800)
    s.add_argument("--levels", type=int, default=9)
    s.add_argument("--floor-height", type=float, default=3.0)
    s.add_argument("--height", type=float)
    s.add_argument("--roof-type", default="flat", choices=["flat", "gable", "unknown"])
    s.add_argument("--camera-preset", choices=list(CAMERA_PRESETS.keys()), default="facade_se_low")
    s.add_argument("--camera-altitude", type=float, default=100)
    s.add_argument("--camera-east", type=float, default=55)
    s.add_argument("--camera-north", type=float, default=-40)
    s.add_argument("--camera-height-above-main", type=float, default=6.0)
    s.add_argument("--face-size", type=int, default=1024)
    s.set_defaults(func=init_project)

    s = sub.add_parser("fetch-osm")
    s.add_argument("--project", required=True)
    s.add_argument("--radius", type=int, help="Override OSM radius in meters for this run")
    s.add_argument("--endpoint", help="Custom Overpass endpoint URL")
    s.add_argument("--timeout", type=int, default=120)
    s.add_argument("--no-poi", action="store_true", help="Fetch only accuracy geometry now; POI will be added later")
    s.set_defaults(func=fetch_osm)

    s = sub.add_parser("fetch-satellite-yandex")
    s.add_argument("--project", required=True)
    s.add_argument("--lat", type=float)
    s.add_argument("--lon", type=float)
    s.add_argument("--api-key")
    s.add_argument("--zoom", type=int, default=17)
    s.add_argument("--size", type=int, default=2048, help="Output square size in px")
    s.add_argument("--tile-w", type=int, default=650, help="Static API request width, max usually 650")
    s.add_argument("--tile-h", type=int, default=450, help="Static API request height, max usually 450")
    s.add_argument("--layer", default="sat", help="sat for legacy 1.x satellite; map/driving/transit/admin for official v1")
    s.add_argument("--api-version", choices=["auto", "v1", "1x", "enterprise-1x"], default="auto", help="auto uses 1x for sat, v1 for map")
    s.add_argument("--no-legacy-sat", action="store_true", help="Do not fallback to legacy 1.x for satellite; useful to verify official v1 behavior")
    s.add_argument("--no-key-param", action="store_true", help="Do not send apikey/key in the Static Maps URL; useful for public legacy 1.x tests")
    s.add_argument("--lang", default="ru_RU")
    s.add_argument("--scale", type=int, choices=[1, 2, 4])
    s.add_argument("--sleep", type=float, default=0.12)
    s.add_argument("--draw-center", action="store_true")
    s.set_defaults(func=fetch_satellite_yandex)

    s = sub.add_parser("fetch-yandex-pano")
    s.add_argument("--project", required=True)
    s.add_argument("--lat", type=float)
    s.add_argument("--lon", type=float)
    s.add_argument("--script")
    s.add_argument("--zoom", type=int, default=0)
    s.set_defaults(func=fetch_yandex_pano)

    s = sub.add_parser("make-collage")
    s.add_argument("--project", required=True)
    s.set_defaults(func=make_collage)

    s = sub.add_parser("clean-sky")
    s.add_argument("--project", required=True)
    s.set_defaults(func=clean_sky)

    s = sub.add_parser("inspect-masks")
    s.add_argument("--project", required=True)
    s.add_argument("--faces", default="all")
    s.set_defaults(func=inspect_masks)

    s = sub.add_parser("list-comfy-presets")
    s.set_defaults(func=list_comfy_presets)

    s = sub.add_parser("set-comfy-preset")
    s.add_argument("--project", required=True)
    s.add_argument("--preset", required=True, choices=list(COMFY_PRESETS.keys()))
    s.set_defaults(func=set_comfy_preset)

    s = sub.add_parser("list-camera-presets")
    s.set_defaults(func=list_camera_presets)

    s = sub.add_parser("set-camera-preset")
    s.add_argument("--project", required=True)
    s.add_argument("--preset", required=True, choices=list(CAMERA_PRESETS.keys()))
    s.add_argument("--height-above-main", type=float, help="Override preset altitude above main roof")
    s.add_argument("--offset-scale", type=float, help="Multiply preset horizontal offset, e.g. 1.3 farther or 0.8 closer")
    s.set_defaults(func=set_camera_preset)

    s = sub.add_parser("set-camera")
    s.add_argument("--project", required=True)
    s.add_argument("--placement", choices=["main_relative", "absolute"], default="main_relative")
    s.add_argument("--offset-east", type=float, help="Camera east offset in meters. In main_relative mode this is relative to main building center")
    s.add_argument("--offset-north", type=float, help="Camera north offset in meters. In main_relative mode this is relative to main building center")
    s.add_argument("--altitude", type=float, help="Absolute altitude in meters, used in absolute placement")
    s.add_argument("--height-above-main", type=float, help="Camera altitude above main building roof in meters")
    s.set_defaults(func=set_camera)

    s = sub.add_parser("set-osm-accuracy-mode")
    s.add_argument("--project", required=True)
    s.add_argument("--ground-size", type=int, help="OSM vector ground plane size in meters; default radius_m*2")
    s.add_argument("--reset-calibration", action=argparse.BooleanOptionalAction, default=True)
    s.set_defaults(func=set_osm_accuracy_mode)

    s = sub.add_parser("calibrate-geometry")
    s.add_argument("--project", required=True)
    s.add_argument("--vector-east", type=float, help="Move OSM vector geometry east/west in meters")
    s.add_argument("--vector-north", type=float, help="Move OSM vector geometry north/south in meters")
    s.add_argument("--vector-scale", type=float, help="Set/multiply OSM vector scale around project origin. Use >1 if vectors are too small")
    s.add_argument("--satellite-east", type=float, help="Move satellite raster plane east/west in meters")
    s.add_argument("--satellite-north", type=float, help="Move satellite raster plane north/south in meters")
    s.add_argument("--satellite-scale", type=float, help="Set/multiply satellite plane scale. With --add, multiplies current scale")
    s.add_argument("--add", action="store_true", help="Add offsets to existing values instead of replacing them; scale is multiplied")
    s.set_defaults(func=calibrate_geometry)

    s = sub.add_parser("validate-blockout")
    s.add_argument("--project", required=True)
    s.add_argument("--fix", action=argparse.BooleanOptionalAction, default=True, help="Auto-fix detected up/down inversion")
    s.set_defaults(func=validate_blockout)

    s = sub.add_parser("render-blockout")
    s.add_argument("--project", required=True)
    s.add_argument("--blender")
    s.set_defaults(func=render_blockout)

    s = sub.add_parser("make-control-maps")
    s.add_argument("--project", required=True)
    s.add_argument("--low", type=int, default=80)
    s.add_argument("--high", type=int, default=180)
    s.set_defaults(func=make_control_maps)

    s = sub.add_parser("ai-analyze-inputs")
    s.add_argument("--project", required=True)
    s.set_defaults(func=ai_analyze_inputs)

    s = sub.add_parser("ai-suggest-prompts")
    s.add_argument("--project", required=True)
    s.add_argument("--apply", action="store_true", help="Apply suggested prompts/settings into project config.yaml")
    s.set_defaults(func=ai_suggest_prompts)

    s = sub.add_parser("run-comfy")
    s.add_argument("--project", required=True)
    s.add_argument("--url")
    s.add_argument("--stage", choices=["first", "main", "all"], default="all", help="first_pass.json, main_pass.json, or both")
    s.add_argument("--faces", default="all", help="Comma-separated faces: front,right,back,left,up,down; default=all")
    s.add_argument("--auto-skip-empty-mask", action=argparse.BooleanOptionalAction, default=True, help="For main pass, skip faces where main building mask is empty")
    s.set_defaults(func=run_comfy)

    s = sub.add_parser("stitch")
    s.add_argument("--project", required=True)
    s.add_argument("--height", type=int, default=2048)
    s.set_defaults(func=stitch)

    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
