#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VR Pano Master — 360° aerial panorama generator for real estate.

Pipeline:
  OSM data → Blender PBR scene → Equirectangular render → (optional) AI polish → 360° panorama

Usage:
  python master.py init --project my_house --lat 57.153 --lon 65.542 --levels 16
  python master.py fetch-osm --project my_house
  python master.py render-pbr --project my_house
  python master.py ai-polish --project my_house  # optional
  python master.py run-auto --project my_house --lat 57.153 --lon 65.542 --levels 16
"""
from __future__ import annotations

import argparse
import json
import math
import os
import py360convert
import random
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests
import yaml

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return False

from PIL import Image, ImageDraw, ImageOps

# ============================================================
# CONSTANTS
# ============================================================

ROOT = Path(__file__).resolve().parent
PROJECTS = ROOT / "projects"

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

CAMERA_PRESETS: Dict[str, Dict[str, Any]] = {
    "sales_se": {
        "description": "Sales view: SE diagonal, above roof — shows facade + courtyard + POI area",
        "placement": "main_relative",
        "offset_east_m": 60.0,
        "offset_north_m": -45.0,
        "height_above_main_m": 25.0,
    },
    "sales_sw": {
        "description": "Sales view: SW diagonal, above roof",
        "placement": "main_relative",
        "offset_east_m": -60.0,
        "offset_north_m": -45.0,
        "height_above_main_m": 25.0,
    },
    "sales_ne": {
        "description": "Sales view: NE diagonal, above roof",
        "placement": "main_relative",
        "offset_east_m": 60.0,
        "offset_north_m": 45.0,
        "height_above_main_m": 25.0,
    },
    "sales_nw": {
        "description": "Sales view: NW diagonal, above roof",
        "placement": "main_relative",
        "offset_east_m": -60.0,
        "offset_north_m": 45.0,
        "height_above_main_m": 25.0,
    },
    "district_south": {
        "description": "Higher south view — full district with POI markers visible",
        "placement": "main_relative",
        "offset_east_m": 20.0,
        "offset_north_m": -70.0,
        "height_above_main_m": 35.0,
    },
    "district_overview": {
        "description": "High overview — maximum district context for POI",
        "placement": "main_relative",
        "offset_east_m": 80.0,
        "offset_north_m": -70.0,
        "height_above_main_m": 40.0,
    },
}


# ============================================================
# UTILITIES
# ============================================================

def load_cfg(project: str) -> Dict[str, Any]:
    path = PROJECTS / project / "config.yaml"
    if not path.exists():
        raise SystemExit(f"Config not found: {path}\nRun: python master.py init --project {project} ...")
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
        "source/street", "source/osm", "pbr_output", "output", "logs",
        "web/assets/panorama",
    ]:
        (base / d).mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))


def default_yandex_pano_script() -> Path:
    return ROOT / "tools" / "yandex-pano-downloader" / "pano.py"


def find_file(base: Path, filename: str) -> Path | None:
    if not base.exists() or not filename:
        return None
    direct = base / filename
    if direct.exists():
        return direct
    for p in base.rglob(filename):
        return p
    return None


# ============================================================
# DOCTOR
# ============================================================

def cmd_doctor(args: argparse.Namespace) -> None:
    """Check local paths and configuration."""
    load_dotenv(ROOT / ".env")
    comfy_path = Path(args.comfy or os.getenv("COMFYUI_PATH", ""))
    blender = Path(args.blender or os.getenv("BLENDER_EXE", "blender"))
    checkpoint = args.checkpoint or os.getenv("COMFY_CHECKPOINT", "Realistic_Vision_V5.1.safetensors")

    print("🔎 VR Pano Master — Doctor\n")
    checks = [
        ("Blender", blender.exists(), blender),
        ("ComfyUI (optional)", comfy_path.exists() if comfy_path.name else False, comfy_path or "(not set)"),
        ("Yandex pano script",
         default_yandex_pano_script().exists() or bool(os.getenv("YANDEX_PANO_SCRIPT")),
         os.getenv("YANDEX_PANO_SCRIPT") or default_yandex_pano_script()),
    ]
    if comfy_path.exists():
        ckpt_path = find_file(comfy_path / "models" / "checkpoints", checkpoint)
        checks.append((f"Checkpoint {checkpoint}", ckpt_path is not None,
                        ckpt_path or comfy_path / "models/checkpoints" / checkpoint))

    for name, ok, val in checks:
        print(("  ✅" if ok else "  ❌") + f" {name}: {val}")

    if not all(ok for _, ok, _ in checks[:1]):  # Blender is required
        print("\n⚠ Blender is required. Set BLENDER_EXE in .env")
    print()


# ============================================================
# INIT PROJECT
# ============================================================

def cmd_init(args: argparse.Namespace) -> None:
    """Create a new project with default configuration."""
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
            "preset": "sales_se",
            "placement": "main_relative",
            "offset_east_m": 60.0,
            "offset_north_m": -45.0,
            "height_above_main_m": 25.0,
        },
        "render": {
            "equirect_width": 4096,
            "engine": "CYCLES",
            "ground_source": "osm_vector",
            "ground_size_m": int(args.radius) * 2,
            "vector_offset_east_m": 0.0,
            "vector_offset_north_m": 0.0,
            "vector_scale_multiplier": 1.0,
        },
        "prompts": {
            "polish_positive": (
                "photorealistic aerial panorama of Russian residential district, "
                "detailed apartment buildings with balconies and air conditioners, "
                "consistent natural colors, concrete panels and brick facades, "
                "dark asphalt roads, green lawns, trees, clear blue sky, "
                "real estate drone photography quality"
            ),
            "polish_negative": (
                "cartoon, illustration, distorted, melted, blurry, low quality, "
                "text, watermark, unrealistic colors, oversaturated"
            ),
        },
        "comfy": {
            "checkpoint": "Realistic_Vision_V5.1.safetensors",
        },
    }

    save_cfg(project, cfg)
    print(f"✅ Project created: {project_root(project)}")
    print(f"\nNext: python master.py fetch-osm --project {project}")


# ============================================================
# OSM DATA
# ============================================================

def overpass_query(lat: float, lon: float, radius: int) -> str:
    return f"""
[out:json][timeout:60];
(
  way(around:{radius},{lat},{lon})["building"];
  relation(around:{radius},{lat},{lon})["building"];
  way(around:{radius},{lat},{lon})["highway"];
  way(around:{radius},{lat},{lon})["landuse"];
  way(around:{radius},{lat},{lon})["leisure"];
  way(around:{radius},{lat},{lon})["natural"];
);
out body geom;
"""


def overpass_request(query: str, endpoints: List[str], timeout: int = 120) -> Dict[str, Any]:
    """Request Overpass API with fallback mirrors."""
    headers = {
        "User-Agent": "VR-Pano-Master/1.0",
        "Accept": "application/json, */*;q=0.8",
    }
    errors: List[str] = []
    for url in endpoints:
        print(f"  ↪ {url}")
        try:
            r = requests.post(url, data={"data": query}, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            errors.append(f"{url} -> HTTP {r.status_code}: {r.text[:200]}")
        except Exception as e:
            errors.append(f"{url} -> {e}")
    raise SystemExit("Could not fetch OSM data.\n\n" + "\n".join(errors[-6:]))


def overpass_to_geojson(data: Dict[str, Any]) -> Tuple[Dict, Dict, Dict]:
    """Convert Overpass response to GeoJSON FeatureCollections."""
    buildings, roads, areas = [], [], []

    for el in data.get("elements", []):
        tags = el.get("tags", {}) or {}
        if el.get("type") == "way" and "geometry" in el:
            coords = [[p["lon"], p["lat"]] for p in el["geometry"]]
            if not coords:
                continue
            is_closed = coords[0] == coords[-1]
            if "building" in tags and is_closed:
                buildings.append({
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [coords]},
                    "properties": {**tags, "osm_id": el.get("id")},
                })
            elif "highway" in tags:
                roads.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {**tags, "osm_id": el.get("id")},
                })
            elif any(k in tags for k in ["landuse", "leisure", "natural"]) and is_closed:
                areas.append({
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [coords]},
                    "properties": {**tags, "osm_id": el.get("id")},
                })

    def fc(features):
        return {"type": "FeatureCollection", "features": features}

    return fc(buildings), fc(roads), fc(areas)


def cmd_fetch_osm(args: argparse.Namespace) -> None:
    """Download OSM geometry data via Overpass API."""
    cfg = load_cfg(args.project)
    lat = cfg["project"]["lat"]
    lon = cfg["project"]["lon"]
    radius = int(args.radius or cfg["project"].get("radius_m", 500))
    ensure_dirs(args.project)

    endpoints = []
    if getattr(args, "endpoint", None):
        endpoints.append(args.endpoint)
    endpoints.extend([e for e in OVERPASS_ENDPOINTS if e not in endpoints])

    print(f"🌍 Fetching OSM data around {lat},{lon}, radius {radius}m...")
    query = overpass_query(lat, lon, radius)
    log_dir = project_root(args.project) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "overpass_query.ql").write_text(query, encoding="utf-8")

    data = overpass_request(query, endpoints=endpoints, timeout=int(args.timeout))
    raw_path = project_root(args.project) / "source/osm/overpass_raw.json"
    write_json(raw_path, data)

    buildings, roads, areas = overpass_to_geojson(data)
    osm_dir = project_root(args.project) / "source/osm"
    write_json(osm_dir / "buildings.geojson", buildings)
    write_json(osm_dir / "roads.geojson", roads)
    write_json(osm_dir / "areas.geojson", areas)

    print(f"✅ OSM data saved to {osm_dir}")
    print(f"   Buildings: {len(buildings.get('features', []))}")
    print(f"   Roads: {len(roads.get('features', []))}")
    print(f"   Areas: {len(areas.get('features', []))}")
    print(f"\nNext: python master.py render-pbr --project {args.project}")


def cmd_fetch_2gis(args: argparse.Namespace) -> None:
    """Download building data from 2GIS API."""
    import os
    from pano_master.twogis_client import fetch_2gis_buildings, merge_osm_and_2gis

    load_dotenv(ROOT / ".env")
    api_key = os.getenv("TWOGIS_API_KEY")
    if not api_key:
        raise SystemExit("TWOGIS_API_KEY not set in .env\nGet your key at: https://dev.2gis.com/")

    project_dir = project_root(args.project)
    ensure_dirs(args.project)
    cfg = load_cfg(args.project)
    lat = cfg["project"]["lat"]
    lon = cfg["project"]["lon"]
    radius = args.radius

    print(f"🏢 Fetching 2GIS data: center=({lat}, {lon}), radius={radius}m")
    twogis_path = project_dir / "source/2gis/buildings.geojson"
    fetch_2gis_buildings(api_key, lat, lon, radius, twogis_path)

    if args.merge_osm:
        osm_path = project_dir / "source/osm/buildings.geojson"
        if osm_path.exists():
            merged_path = project_dir / "source/osm/buildings_merged.geojson"
            merge_osm_and_2gis(osm_path, twogis_path, merged_path)
            # Replace OSM with merged
            import shutil
            shutil.copy2(merged_path, osm_path)
            print(f"✅ Merged data saved to {osm_path}")
        else:
            print("⚠ OSM data not found, skipping merge. Run fetch-osm first.")


# ============================================================
# CAMERA
# ============================================================

def cmd_set_camera_preset(args: argparse.Namespace) -> None:
    """Set camera preset for the project."""
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
    print(f"✅ Camera preset: {args.preset}")
    if description:
        print(f"   {description}")
    for k in ["offset_east_m", "offset_north_m", "height_above_main_m"]:
        print(f"   {k}: {camera.get(k)}")


def cmd_list_camera_presets(args: argparse.Namespace) -> None:
    """List available camera presets."""
    for name, preset in CAMERA_PRESETS.items():
        print(f"  {name}")
        print(f"    {preset.get('description', '')}")
        print(f"    east={preset.get('offset_east_m')}m, north={preset.get('offset_north_m')}m, "
              f"height_above_main={preset.get('height_above_main_m')}m")


def _building_visibility_score(buildings_geojson: Dict, main_osm_id: Any,
                               cam_x: float, cam_y: float,
                               lon0: float, lat0: float) -> float:
    """Calculate how visible the main building is from a camera position.

    Returns a score based on:
    - Angular size of main building from camera (bigger = better, up to a point)
    - Distance (30-80m ideal)
    - Occlusion by other buildings (fewer blockers = better)
    """
    R = 6378137.0
    main_coords = None
    other_buildings = []

    for f in buildings_geojson.get("features", []):
        osm_id = f.get("properties", {}).get("osm_id")
        coords = f["geometry"]["coordinates"][0]
        cx = sum(p[0] for p in coords) / len(coords)
        cy = sum(p[1] for p in coords) / len(coords)
        bx = math.radians(cx - lon0) * R * math.cos(math.radians(lat0))
        by = math.radians(cy - lat0) * R

        if osm_id == main_osm_id:
            main_coords = coords
            main_center = (bx, by)
        else:
            other_buildings.append((bx, by, coords))

    if main_coords is None:
        return -100.0

    # Distance from camera to main building center
    dist = math.hypot(cam_x - main_center[0], cam_y - main_center[1])

    # Ideal distance: 40-60m
    if 30 <= dist <= 80:
        dist_score = 100.0
    elif 20 <= dist < 30 or 80 < dist <= 120:
        dist_score = 60.0
    elif dist < 20:
        dist_score = 20.0  # too close
    else:
        dist_score = 10.0  # too far

    # Angular size: approximate by building perimeter subtended angle
    perimeter_pts = [
        (math.radians(p[0] - lon0) * R * math.cos(math.radians(lat0)),
         math.radians(p[1] - lat0) * R)
        for p in main_coords
    ]
    angles = []
    for px, py in perimeter_pts:
        angle = math.atan2(py - cam_y, px - cam_x)
        angles.append(angle)
    if angles:
        angular_spread = max(angles) - min(angles)
        if angular_spread > math.pi:
            angular_spread = 2 * math.pi - angular_spread
    else:
        angular_spread = 0

    # Ideal angular spread: 0.3-1.0 radians (17°-57°)
    if 0.3 <= angular_spread <= 1.0:
        angle_score = 100.0
    elif 0.15 <= angular_spread < 0.3:
        angle_score = 60.0
    elif angular_spread > 1.0:
        angle_score = 40.0  # too close, building fills view
    else:
        angle_score = 20.0

    # Simple occlusion check: count buildings between camera and main
    cam_to_main_angle = math.atan2(main_center[1] - cam_y, main_center[0] - cam_x)
    occluders = 0
    for bx, by, _ in other_buildings:
        d_to_blocker = math.hypot(bx - cam_x, by - cam_y)
        if d_to_blocker > dist:
            continue  # behind main building
        angle_to_blocker = math.atan2(by - cam_y, bx - cam_x)
        angle_diff = abs(angle_to_blocker - cam_to_main_angle)
        if angle_diff > math.pi:
            angle_diff = 2 * math.pi - angle_diff
        if angle_diff < 0.15:  # within ~8.5° of line of sight
            occluders += 1

    occlusion_penalty = occluders * 25.0

    return dist_score * 0.4 + angle_score * 0.4 + max(0, 100 - occlusion_penalty) * 0.2


def cmd_auto_select_camera(args: argparse.Namespace) -> None:
    """Automatically select the best camera preset based on building visibility."""
    cfg = load_cfg(args.project)
    lat0 = cfg["project"]["lat"]
    lon0 = cfg["project"]["lon"]
    R = 6378137.0

    # Load buildings
    buildings_path = project_root(args.project) / "source/osm/buildings.geojson"
    if not buildings_path.exists():
        raise SystemExit(f"Buildings not found: {buildings_path}\nRun fetch-osm first.")
    buildings = json.loads(buildings_path.read_text(encoding="utf-8"))

    # Find main building (nearest to project center)
    main_osm_id = None
    best_dist = float("inf")
    for f in buildings.get("features", []):
        coords = f["geometry"]["coordinates"][0]
        cx = sum(p[0] for p in coords) / len(coords)
        cy = sum(p[1] for p in coords) / len(coords)
        d = haversine_m(lat0, lon0, cy, cx)
        if d < best_dist:
            best_dist = d
            main_osm_id = f.get("properties", {}).get("osm_id")

    if main_osm_id is None:
        raise SystemExit("No buildings found in OSM data.")

    # Main building center in local coordinates
    for f in buildings.get("features", []):
        if f.get("properties", {}).get("osm_id") == main_osm_id:
            coords = f["geometry"]["coordinates"][0]
            mc_lon = sum(p[0] for p in coords) / len(coords)
            mc_lat = sum(p[1] for p in coords) / len(coords)
            main_x = math.radians(mc_lon - lon0) * R * math.cos(math.radians(lat0))
            main_y = math.radians(mc_lat - lat0) * R
            break

    main_height = float(cfg.get("main_building", {}).get("height_m", 45))

    # Test each preset
    candidates = args.presets.split(",") if args.presets else list(CAMERA_PRESETS.keys())
    results = []

    print(f"📷 Testing {len(candidates)} camera presets...\n")
    for preset_name in candidates:
        preset_name = preset_name.strip()
        if preset_name not in CAMERA_PRESETS:
            continue
        preset = CAMERA_PRESETS[preset_name]
        cam_x = main_x + preset["offset_east_m"]
        cam_y = main_y + preset["offset_north_m"]

        score = _building_visibility_score(buildings, main_osm_id, cam_x, cam_y, lon0, lat0)
        dist = math.hypot(cam_x - main_x, cam_y - main_y)
        results.append((preset_name, score, dist))
        print(f"  {preset_name:25s}  score={score:6.1f}  dist={dist:.0f}m")

    if not results:
        raise SystemExit("No valid presets tested.")

    results.sort(key=lambda r: r[1], reverse=True)
    best_name, best_score, best_dist = results[0]

    print(f"\n🏆 Best: {best_name} (score={best_score:.1f}, distance={best_dist:.0f}m)")

    if args.apply:
        preset = dict(CAMERA_PRESETS[best_name])
        preset.pop("description", None)
        cfg["camera"] = {**cfg.get("camera", {}), **preset, "preset": best_name}
        save_cfg(args.project, cfg)
        print(f"✅ Applied camera preset: {best_name}")


# ============================================================
# CALIBRATION
# ============================================================

def cmd_calibrate_geometry(args: argparse.Namespace) -> None:
    """Adjust OSM geometry offset/scale for alignment."""
    cfg = load_cfg(args.project)
    render = cfg.setdefault("render", {})
    for key, attr in [
        ("vector_offset_east_m", "vector_east"),
        ("vector_offset_north_m", "vector_north"),
        ("vector_scale_multiplier", "vector_scale"),
    ]:
        val = getattr(args, attr, None)
        if val is None:
            continue
        old = float(render.get(key, 1.0 if "scale" in key else 0.0))
        if args.add and "scale" not in key:
            render[key] = old + float(val)
        elif args.add and "scale" in key:
            render[key] = old * float(val)
        else:
            render[key] = float(val)

    save_cfg(args.project, cfg)
    print("✅ Calibration updated:")
    for key in ["vector_offset_east_m", "vector_offset_north_m", "vector_scale_multiplier"]:
        print(f"   {key}: {render.get(key)}")
    print(f"\nNext: python master.py render-pbr --project {args.project}")


# ============================================================
# YANDEX PANORAMAS
# ============================================================

def find_street_points(lat0: float, lon0: float, roads_geojson: Dict,
                       radius_min_m: float = 15.0, radius_max_m: float = 80.0,
                       max_points: int = 4) -> List[Tuple[float, float, str]]:
    """Find street points around the building for panorama capture."""
    features = roads_geojson.get("features", [])
    if not features:
        R = 6371000.0
        points = []
        for bearing, label in [(0, "north"), (90, "east"), (180, "south"), (270, "west")]:
            dlat = (30.0 * math.cos(math.radians(bearing))) / R
            dlon = (30.0 * math.sin(math.radians(bearing))) / (R * math.cos(math.radians(lat0)))
            points.append((lat0 + math.degrees(dlat), lon0 + math.degrees(dlon), label))
        return points

    candidates: List[Tuple[float, float, float, float]] = []
    for f in features:
        if f.get("geometry", {}).get("type") != "LineString":
            continue
        for lon, lat in f["geometry"]["coordinates"]:
            d_m = haversine_m(lat0, lon0, lat, lon)
            if radius_min_m <= d_m <= radius_max_m:
                dlon_r = math.radians(lon - lon0)
                lat1_r, lat2_r = math.radians(lat0), math.radians(lat)
                x = math.sin(dlon_r) * math.cos(lat2_r)
                y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon_r)
                bearing = math.degrees(math.atan2(x, y)) % 360
                candidates.append((lat, lon, d_m, bearing))

    if not candidates:
        R = 6371000.0
        return [(lat0 + 30.0/R*180/math.pi, lon0, "fallback")]

    n_sectors = 8
    sector_size = 360.0 / n_sectors
    sectors: Dict[int, list] = {i: [] for i in range(n_sectors)}
    for c in candidates:
        sector = int((c[3] + sector_size / 2) % 360 / sector_size)
        sectors[sector].append(c)

    direction_labels = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    selected = []
    for i in range(n_sectors):
        if not sectors[i]:
            continue
        best = min(sectors[i], key=lambda c: abs(c[2] - 30.0))
        selected.append((best[0], best[1], direction_labels[i]))

    if len(selected) > max_points:
        selected.sort(key=lambda p: abs(haversine_m(lat0, lon0, p[0], p[1]) - 30.0))
        selected = selected[:max_points]

    return selected


def cmd_fetch_yandex_pano(args: argparse.Namespace) -> None:
    """Download street panoramas from multiple points around the building."""
    load_dotenv(ROOT / ".env")
    cfg = load_cfg(args.project)
    lat = cfg["project"]["lat"]
    lon = cfg["project"]["lon"]
    script = args.script or os.getenv("YANDEX_PANO_SCRIPT")
    if not script:
        candidate = default_yandex_pano_script()
        if candidate.exists():
            script = str(candidate)
        else:
            raise SystemExit("YANDEX_PANO_SCRIPT not set. Run: python master.py setup-yandex-pano --install-deps")

    street_dir = project_root(args.project) / "source/street"
    street_dir.mkdir(parents=True, exist_ok=True)

    if args.exact_coords:
        out = street_dir / "yandex_pano_main.jpg"
        cmd = [sys.executable, script, "-c", f"{lat},{lon}", "-z", str(args.zoom), "-a", "-o", str(out)]
        subprocess.check_call(cmd)
        print(f"✅ Saved: {out}")
        return

    # Multi-point mode
    roads_path = project_root(args.project) / "source/osm/roads.geojson"
    roads_geojson = {}
    if roads_path.exists():
        try:
            roads_geojson = json.loads(roads_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    points = find_street_points(float(lat), float(lon), roads_geojson, max_points=args.max_points)
    print(f"📍 Found {len(points)} street panorama points:\n")

    success = 0
    for i, (plat, plon, label) in enumerate(points):
        dist = haversine_m(float(lat), float(lon), plat, plon)
        print(f"   [{i+1}] {label}: {plat:.6f}, {plon:.6f} ({dist:.0f}m)")
        out = street_dir / f"yandex_pano_{label.lower()}.jpg"
        cmd = [sys.executable, script, "-c", f"{plat},{plon}", "-z", str(args.zoom), "-a", "-o", str(out)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                err_msg = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
                # Show last 3 lines of error
                err_lines = err_msg.split("\n")[-3:]
                print(f"       ⚠ Failed: {' | '.join(err_lines)}")
            elif out.exists() and out.stat().st_size > 1000:
                print(f"       ✅ {out.name}")
                success += 1
            else:
                print(f"       ⚠ Output too small or missing")
        except Exception as e:
            print(f"       ⚠ Exception: {e}")

    print(f"\n✅ Downloaded {success} panoramas")


def cmd_make_collage(args: argparse.Namespace) -> None:
    """Assemble street reference collage from downloaded panoramas."""
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

    n = len(imgs)
    cols = 2 if n <= 4 else (3 if n <= 9 else 4)
    rows = cols
    tile = 512
    canvas = Image.new("RGB", (cols * tile, rows * tile), (20, 20, 20))
    draw = ImageDraw.Draw(canvas)

    for i, (name, im) in enumerate(imgs[:cols * rows]):
        im_cropped = ImageOps.fit(im, (tile, tile), method=Image.Resampling.LANCZOS, centering=(0.5, 0.48))
        x = (i % cols) * tile
        y = (i // cols) * tile
        canvas.paste(im_cropped, (x, y))
        draw.rectangle([x, y, x + tile - 1, y + 28], fill=(0, 0, 0))
        draw.text((x + 8, y + 8), name[:50], fill=(255, 255, 255))

    out = project_root(args.project) / "source/street_reference_collage.png"
    canvas.save(out)
    print(f"✅ Collage saved: {out} ({canvas.size[0]}x{canvas.size[1]})")


# ============================================================
# YANDEX PANO DOWNLOADER SETUP
# ============================================================

def cmd_setup_yandex_pano(args: argparse.Namespace) -> None:
    """Download yandex-pano-downloader from GitHub."""
    target = Path(args.dir) if args.dir else ROOT / "tools" / "yandex-pano-downloader"
    target.parent.mkdir(parents=True, exist_ok=True)
    repo = "https://github.com/zer0-dev/yandex-pano-downloader.git"

    if target.exists() and (target / "pano.py").exists() and not args.force:
        print(f"✅ Already exists: {target}")
    else:
        if target.exists() and args.force:
            shutil.rmtree(target)
        try:
            subprocess.check_call(["git", "clone", "--depth", "1", repo, str(target)])
        except Exception as e:
            print(f"⚠ git clone failed: {e}, downloading raw files...")
            target.mkdir(parents=True, exist_ok=True)
            raw_base = "https://raw.githubusercontent.com/zer0-dev/yandex-pano-downloader/main"
            for name in ["pano.py", "requirements.txt"]:
                urllib.request.urlretrieve(f"{raw_base}/{name}", target / name)

    if args.install_deps:
        req = target / "requirements.txt"
        if req.exists():
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(req)])

    print(f"\nAdd to .env:\nYANDEX_PANO_SCRIPT={str(target / 'pano.py').replace(chr(92), '/')}")


# ============================================================
# PBR RENDER
# ============================================================

def cmd_render_pbr(args: argparse.Namespace) -> None:
    """Render PBR-textured equirectangular panorama via Blender."""
    cfg = load_cfg(args.project)
    render_cfg = cfg.setdefault("render", {})
    render_cfg["equirect_width"] = int(args.width)

    scene_cfg = project_root(args.project) / "scene_config.json"
    write_json(scene_cfg, cfg)
    save_cfg(args.project, cfg)

    load_dotenv(ROOT / ".env")
    blender = args.blender or os.getenv("BLENDER_EXE") or "blender"
    script = ROOT / "scripts/blender_pbr_scene.py"

    cmd = [blender, "--background", "--addons", "cycles", "--python", str(script), "--", "--project", args.project]
    print(f"🎨 PBR Render: {render_cfg['equirect_width']}x{render_cfg['equirect_width'] // 2}")
    print(f"▶ {' '.join(cmd)}\n")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit(f"Blender render failed (exit code {result.returncode})")

    out = project_root(args.project) / "pbr_output/equirectangular.png"
    if out.exists():
        size_mb = out.stat().st_size / (1024 * 1024)
        print(f"\n✅ Render saved: {out} ({size_mb:.1f} MB)")
        print(f"\nNext: python master.py ai-polish --project {args.project}")
    else:
        raise SystemExit(f"Expected output not found: {out}")


# ============================================================
# AI POLISH
# ============================================================

def cmd_ai_polish(args: argparse.Namespace) -> None:
    """AI polish with ControlNet (Depth preferred, Canny fallback)."""
    from pano_master.comfy_client import ComfyClient

    load_dotenv(ROOT / ".env")
    cfg = load_cfg(args.project)
    base = project_root(args.project)
    url = args.url or os.getenv("COMFYUI_URL", "http://127.0.0.1:8188")

    source = base / "pbr_output/equirectangular.png"
    depth_source = base / "pbr_output/depth.png"

    if not source.exists():
        raise SystemExit(f"PBR render not found: {source}\nRun render-pbr first.")

    denoise = float(args.denoise)
    cn_strength = float(getattr(args, "cn_strength", 0.92))
    checkpoint = cfg.get("comfy", {}).get("checkpoint", "Realistic_Vision_V5.1.safetensors")

    # Choose ControlNet type: Depth (best) or Canny (fallback)
    use_depth = depth_source.exists()
    if use_depth:
        controlnet_name = os.getenv("COMFY_CONTROLNET_DEPTH", "control_v11f1p_sd15_depth.pth")
        print(f"🖌️  AI Polish: Depth ControlNet (best geometry preservation)")
    else:
        controlnet_name = os.getenv("COMFY_CONTROLNET_CANNY", "diffusion_pytorch_model.safetensors")
        print(f"🖌️  AI Polish: Canny ControlNet (depth map not found)")
    print(f"   denoise={denoise}, cn_strength={cn_strength}")

    # Prompts: enhance textures, NOT geometry
    positive = cfg.get("prompts", {}).get("polish_positive",
        "photorealistic aerial drone photograph of a Russian residential neighborhood, "
        "highly detailed building facades with realistic concrete and brick textures, "
        "natural weathering and subtle stains, realistic glass windows with sky reflections, "
        "lush green trees and well-maintained lawns, clean asphalt roads with lane markings, "
        "warm afternoon sunlight, soft natural shadows, professional real estate photography, "
        "sharp focus, vibrant but natural colors, 8k ultra high detail")

    negative = cfg.get("prompts", {}).get("polish_negative",
        "changed geometry, warped buildings, distorted windows, melted walls, extra floors, "
        "missing windows, different building shape, broken architecture, artifacts, noise, "
        "cartoon, illustration, anime, 3d render, painting, artistic, oversaturated, "
        "text, watermark, logo, blurry, low quality, out of focus, grain, film grain")

    workflow = {
        # Checkpoint
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint}
        },
        # Load source color image
        "2": {
            "class_type": "LoadImage",
            "inputs": {"image": "__PLACEHOLDER_COLOR__"}
        },
        # Scale color to model resolution
        "3": {
            "class_type": "ImageScale",
            "inputs": {
                "upscale_method": "lanczos",
                "width": 2048, "height": 1024,
                "crop": "disabled",
                "image": ["2", 0]
            }
        },
    }

    if use_depth:
        # Depth ControlNet path (best for geometry preservation)
        workflow.update({
            # Load depth map
            "4": {
                "class_type": "LoadImage",
                "inputs": {"image": "__PLACEHOLDER_DEPTH__"}
            },
            # Scale depth to match color
            "5": {
                "class_type": "ImageScale",
                "inputs": {
                    "upscale_method": "lanczos",
                    "width": 2048, "height": 1024,
                    "crop": "disabled",
                    "image": ["4", 0]
                }
            },
            # Load Depth ControlNet
            "6": {
                "class_type": "ControlNetLoader",
                "inputs": {"control_net_name": controlnet_name}
            },
            # Positive prompt
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": positive, "clip": ["1", 1]}
            },
            # Negative prompt
            "8": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative, "clip": ["1", 1]}
            },
            # Apply Depth ControlNet
            "9": {
                "class_type": "ControlNetApplyAdvanced",
                "inputs": {
                    "strength": cn_strength,
                    "start_percent": 0.0,
                    "end_percent": 0.90,
                    "positive": ["7", 0],
                    "negative": ["8", 0],
                    "control_net": ["6", 0],
                    "image": ["5", 0],
                    "vae": ["1", 2]
                }
            },
            # Encode source to latent
            "10": {
                "class_type": "VAEEncode",
                "inputs": {"pixels": ["3", 0], "vae": ["1", 2]}
            },
            # KSampler with depth guidance
            "11": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": random.randint(0, 2**32 - 1),
                    "steps": 28,
                    "cfg": 4.5,
                    "sampler_name": "dpmpp_2m",
                    "scheduler": "karras",
                    "denoise": denoise,
                    "model": ["1", 0],
                    "positive": ["9", 0],
                    "negative": ["9", 1],
                    "latent_image": ["10", 0]
                }
            },
            # Decode
            "12": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["11", 0], "vae": ["1", 2]}
            },
            # Save
            "13": {
                "class_type": "SaveImage",
                "inputs": {
                    "filename_prefix": f"polish_{args.project}",
                    "images": ["12", 0]
                }
            }
        })
    else:
        # Canny ControlNet path (fallback)
        workflow.update({
            # Canny edge detection
            "4": {
                "class_type": "Canny",
                "inputs": {
                    "low_threshold": 0.08,
                    "high_threshold": 0.25,
                    "image": ["3", 0]
                }
            },
            # Load Canny ControlNet
            "5": {
                "class_type": "ControlNetLoader",
                "inputs": {"control_net_name": controlnet_name}
            },
            # Positive prompt
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": positive, "clip": ["1", 1]}
            },
            # Negative prompt
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative, "clip": ["1", 1]}
            },
            # Apply Canny ControlNet
            "8": {
                "class_type": "ControlNetApplyAdvanced",
                "inputs": {
                    "strength": cn_strength,
                    "start_percent": 0.0,
                    "end_percent": 0.85,
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "control_net": ["5", 0],
                    "image": ["4", 0],
                    "vae": ["1", 2]
                }
            },
            # Encode
            "9": {
                "class_type": "VAEEncode",
                "inputs": {"pixels": ["3", 0], "vae": ["1", 2]}
            },
            # KSampler
            "10": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": random.randint(0, 2**32 - 1),
                    "steps": 28,
                    "cfg": 4.5,
                    "sampler_name": "dpmpp_2m",
                    "scheduler": "karras",
                    "denoise": denoise,
                    "model": ["1", 0],
                    "positive": ["8", 0],
                    "negative": ["8", 1],
                    "latent_image": ["9", 0]
                }
            },
            # Decode
            "11": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["10", 0], "vae": ["1", 2]}
            },
            # Save
            "12": {
                "class_type": "SaveImage",
                "inputs": {
                    "filename_prefix": f"polish_{args.project}",
                    "images": ["11", 0]
                }
            }
        })

    client = ComfyClient(url)

    # Upload color image
    color_ref = client.upload_image(source, subfolder=f"vr_pano_master/{args.project}/polish")
    workflow["2"]["inputs"]["image"] = color_ref

    # Upload depth if available
    if use_depth:
        depth_ref = client.upload_image(depth_source, subfolder=f"vr_pano_master/{args.project}/polish")
        workflow["4"]["inputs"]["image"] = depth_ref
        print(f"   Depth map: {depth_source}")

    print(f"   Running ComfyUI...")
    result = client.queue_and_wait(workflow)

    # Save result
    save_key = "13" if use_depth else "12"
    # Find SaveImage output
    images = []
    for node_id, node_out in result.get("outputs", {}).items():
        if isinstance(node_out, dict):
            for img in node_out.get("images", []):
                if isinstance(img, dict) and img.get("filename"):
                    images.append(img)

    if images:
        saved = base / "pbr_output/polished.png"
        saved.parent.mkdir(parents=True, exist_ok=True)
        client.download_image(images[0], saved)

        # Upscale back to original resolution
        from PIL import Image as PILImage
        orig_size = PILImage.open(source).size
        polished = PILImage.open(saved).resize(orig_size, PILImage.LANCZOS)
        polished.save(saved)

        # Save final output
        final = base / "output/aerial_panorama_360.jpg"
        final.parent.mkdir(parents=True, exist_ok=True)
        polished.convert("RGB").save(final, quality=92)
        shutil.copy2(final, base / "web/assets/panorama/aerial_panorama_360.jpg")
        print(f"✅ Final panorama: {final}")
        print(f"\nCompare:")
        print(f"   Original: {source}")
        print(f"   Polished: {final}")
    else:
        print("⚠ No output from ComfyUI")


# ============================================================
# FULL AUTO PIPELINE
# ============================================================

def cmd_run_auto(args: argparse.Namespace) -> None:
    """Full automatic pipeline: coordinates → 360° panorama."""
    import types

    print("🚀 VR Pano Master — Full Auto Pipeline\n")

    # 1. Init
    print("=" * 50 + "\nSTEP 1/5: Initialize project\n")
    cmd_init(types.SimpleNamespace(
        project=args.project, lat=args.lat, lon=args.lon,
        radius=getattr(args, "radius", 500), levels=getattr(args, "levels", 9),
        floor_height=getattr(args, "floor_height", 3.0),
        height=getattr(args, "height", None),
        roof_type=getattr(args, "roof_type", "flat"),
    ))

    # 2. Fetch OSM
    print("\n" + "=" * 50 + "\nSTEP 2/5: Fetch OSM data\n")
    cmd_fetch_osm(types.SimpleNamespace(
        project=args.project, radius=getattr(args, "radius", 500),
        endpoint=None, timeout=120,
    ))

    # 3. Auto-select camera
    print("\n" + "=" * 50 + "\nSTEP 3/5: Auto-select camera\n")
    try:
        cmd_auto_select_camera(types.SimpleNamespace(
            project=args.project, presets=None, apply=True,
        ))
    except Exception as e:
        print(f"⚠ Camera auto-select failed: {e}, using default")

    # 4. Fetch panoramas (optional)
    print("\n" + "=" * 50 + "\nSTEP 4/5: Fetch street panoramas\n")
    try:
        cmd_fetch_yandex_pano(types.SimpleNamespace(
            project=args.project, script=None, zoom=0,
            exact_coords=False, max_points=4,
        ))
        cmd_make_collage(types.SimpleNamespace(project=args.project))
    except Exception as e:
        print(f"⚠ Street panoramas failed (non-critical): {e}")

    # 5. PBR Render
    print("\n" + "=" * 50 + "\nSTEP 5/5: PBR render\n")
    cmd_render_pbr(types.SimpleNamespace(
        project=args.project, blender=None, width=4096,
    ))

    print("\n" + "=" * 50)
    print(f"\n🎉 Done! Panorama: {project_root(args.project)}/pbr_output/equirectangular.png")
    print(f"\nOptional: python master.py ai-polish --project {args.project}")


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="VR Pano Master — 360° aerial panorama generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # doctor
    s = sub.add_parser("doctor", help="Check configuration and paths")
    s.add_argument("--comfy")
    s.add_argument("--blender")
    s.add_argument("--checkpoint")
    s.set_defaults(func=cmd_doctor)

    # init
    s = sub.add_parser("init", help="Create a new project")
    s.add_argument("--project", required=True)
    s.add_argument("--lat", required=True, type=float)
    s.add_argument("--lon", required=True, type=float)
    s.add_argument("--radius", type=int, default=500)
    s.add_argument("--levels", type=int, default=9)
    s.add_argument("--floor-height", type=float, default=3.0)
    s.add_argument("--height", type=float)
    s.add_argument("--roof-type", default="flat", choices=["flat", "gable", "unknown"])
    s.set_defaults(func=cmd_init)

    # fetch-osm
    s = sub.add_parser("fetch-2gis", help="Download building data from 2GIS API")
    s.add_argument("--project", required=True)
    s.add_argument("--radius", type=int, default=500, help="Search radius in meters (max 5000)")
    s.add_argument("--merge-osm", action="store_true", help="Merge with existing OSM data")
    s.set_defaults(func=cmd_fetch_2gis)

    s = sub.add_parser("fetch-osm", help="Download OSM geometry data")
    s.add_argument("--project", required=True)
    s.add_argument("--radius", type=int)
    s.add_argument("--endpoint")
    s.add_argument("--timeout", type=int, default=120)
    s.set_defaults(func=cmd_fetch_osm)

    # camera
    s = sub.add_parser("list-camera-presets", help="List camera presets")
    s.set_defaults(func=cmd_list_camera_presets)

    s = sub.add_parser("set-camera-preset", help="Set camera preset")
    s.add_argument("--project", required=True)
    s.add_argument("--preset", required=True, choices=list(CAMERA_PRESETS.keys()),
                   help="Camera preset: sales_* for selling view, district_* for POI overview")
    s.add_argument("--height-above-main", type=float)
    s.add_argument("--offset-scale", type=float)
    s.set_defaults(func=cmd_set_camera_preset)

    s = sub.add_parser("auto-select-camera", help="Auto-select best camera preset")
    s.add_argument("--project", required=True)
    s.add_argument("--presets", help="Comma-separated presets to test")
    s.add_argument("--apply", action="store_true", help="Apply best preset")
    s.set_defaults(func=cmd_auto_select_camera)

    # calibrate
    s = sub.add_parser("calibrate-geometry", help="Adjust OSM geometry offset/scale")
    s.add_argument("--project", required=True)
    s.add_argument("--vector-east", type=float)
    s.add_argument("--vector-north", type=float)
    s.add_argument("--vector-scale", type=float)
    s.add_argument("--add", action="store_true")
    s.set_defaults(func=cmd_calibrate_geometry)

    # yandex pano
    s = sub.add_parser("setup-yandex-pano", help="Install yandex-pano-downloader")
    s.add_argument("--dir")
    s.add_argument("--force", action="store_true")
    s.add_argument("--install-deps", action="store_true")
    s.set_defaults(func=cmd_setup_yandex_pano)

    s = sub.add_parser("fetch-yandex-pano", help="Download street panoramas")
    s.add_argument("--project", required=True)
    s.add_argument("--script")
    s.add_argument("--zoom", type=int, default=0)
    s.add_argument("--max-points", type=int, default=4)
    s.add_argument("--exact-coords", action="store_true")
    s.set_defaults(func=cmd_fetch_yandex_pano)

    s = sub.add_parser("make-collage", help="Assemble street reference collage")
    s.add_argument("--project", required=True)
    s.set_defaults(func=cmd_make_collage)

    # render
    s = sub.add_parser("render-pbr", help="Render PBR equirectangular panorama")
    s.add_argument("--project", required=True)
    s.add_argument("--blender")
    s.add_argument("--width", type=int, default=4096)
    s.set_defaults(func=cmd_render_pbr)

    # ai polish
    s = sub.add_parser("ai-polish", help="AI polish pass with ControlNet")
    s.add_argument("--project", required=True)
    s.add_argument("--url")
    s.add_argument("--denoise", type=float, default=0.08,
                   help="Denoise (0.05-0.15). Lower = more original preserved")
    s.add_argument("--cn-strength", type=float, default=0.95,
                   help="ControlNet strength (0.85-1.0). Higher = locks geometry harder")
    s.set_defaults(func=cmd_ai_polish)

    # full auto
    s = sub.add_parser("run-auto", help="Full automatic pipeline")
    s.add_argument("--project", required=True)
    s.add_argument("--lat", required=True, type=float)
    s.add_argument("--lon", required=True, type=float)
    s.add_argument("--radius", type=int, default=500)
    s.add_argument("--levels", type=int, default=9)
    s.add_argument("--floor-height", type=float, default=3.0)
    s.add_argument("--height", type=float)
    s.add_argument("--roof-type", default="flat", choices=["flat", "gable", "unknown"])
    s.set_defaults(func=cmd_run_auto)

    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
