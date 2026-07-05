# -*- coding: utf-8 -*-
"""Blender script: builds a simple 3D blockout and renders cubemap faces.
Run:
  blender --background --python scripts/blender_blockout.py -- --project my_project
"""
from __future__ import annotations
import argparse
import json
import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
VECTOR_OFFSET_EAST_M = 0.0
VECTOR_OFFSET_NORTH_M = 0.0
VECTOR_SCALE_MULTIPLIER = 1.0
FACES = {
    "front": (math.radians(90), 0, 0),   # look north-ish; adjusted by camera object orientation conventions
    "right": (math.radians(90), 0, math.radians(-90)),
    "back": (math.radians(90), 0, math.radians(180)),
    "left": (math.radians(90), 0, math.radians(90)),
    "up": (0, 0, 0),
    "down": (math.radians(180), 0, 0),
}


def parse_args():
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    return p.parse_args(argv)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def local_xy(lon, lat, lon0, lat0):
    # Equirectangular approximation, good enough for district scale.
    # X = east, Y = north. Vector offset is used to calibrate OSM geometry
    # against the satellite raster when providers have different georeferencing.
    R = 6378137.0
    x = math.radians(lon - lon0) * R * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * R
    x = x * VECTOR_SCALE_MULTIPLIER + VECTOR_OFFSET_EAST_M
    y = y * VECTOR_SCALE_MULTIPLIER + VECTOR_OFFSET_NORTH_M
    return x, y


def clear_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()


def make_mat(name, color, roughness=0.7):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    try:
        bsdf.inputs['Base Color'].default_value = color
        bsdf.inputs['Roughness'].default_value = roughness
    except Exception:
        pass
    return mat


def make_emission_mat(name, color):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    em = nt.nodes.new('ShaderNodeEmission')
    em.inputs['Color'].default_value = color
    em.inputs['Strength'].default_value = 1.0
    nt.links.new(em.outputs['Emission'], out.inputs['Surface'])
    return mat


def apply_main_mask_materials(white_mat, black_mat):
    for obj in bpy.context.scene.objects:
        if not hasattr(obj.data, 'materials'):
            continue
        obj.data.materials.clear()
        if obj.name == "MAIN_BUILDING":
            obj.data.materials.append(white_mat)
        else:
            obj.data.materials.append(black_mat)


def create_ground(project_dir: Path, radius_m: float, cfg=None):
    cfg = cfg or {}
    render_cfg = cfg.get("render", {}) if isinstance(cfg, dict) else {}
    ground_source = render_cfg.get("ground_source", "satellite")
    meta = read_json(project_dir / "source/satellite_metadata.json", {}) or {}

    if ground_source == "osm_vector":
        plane_size = float(render_cfg.get("ground_size_m") or (radius_m * 2))
        offset_e = 0.0
        offset_n = 0.0
        mat_ground = make_mat("osm_ground", (0.30, 0.33, 0.29, 1))
        name = "osm_vector_ground"
        print(f"OSM vector ground plane: size={plane_size:.2f}m. Satellite texture is disabled for accuracy mode.")
    else:
        plane_size = float(meta.get("coverage_width_m") or (radius_m * 2))
        plane_size *= float(render_cfg.get("satellite_scale_multiplier", 1.0))
        offset_e = float(render_cfg.get("satellite_offset_east_m", 0.0))
        offset_n = float(render_cfg.get("satellite_offset_north_m", 0.0))
        mat_ground = make_mat("ground", (0.25, 0.28, 0.23, 1))
        name = "satellite_ground"
        print(f"Satellite ground plane: size={plane_size:.2f}m, offset=({offset_e:.2f},{offset_n:.2f}), metadata={'yes' if meta else 'no'}")

    bpy.ops.mesh.primitive_plane_add(size=plane_size, location=(offset_e, offset_n, 0))
    ground = bpy.context.object
    ground.name = name
    ground.data.materials.append(mat_ground)

    if ground_source != "osm_vector":
        sat = project_dir / "source/satellite_medium.png"
        if not sat.exists():
            sat = project_dir / "source/satellite/satellite_medium.png"
        if sat.exists():
            mat = bpy.data.materials.new("satellite_texture")
            mat.use_nodes = True
            nt = mat.node_tree
            bsdf = nt.nodes.get('Principled BSDF')
            tex = nt.nodes.new('ShaderNodeTexImage')
            tex.image = bpy.data.images.load(str(sat))
            nt.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
            ground.data.materials.clear()
            ground.data.materials.append(mat)
    return ground


def polygon_centroid(coords):
    pts = coords[:-1] if coords and coords[0] == coords[-1] else coords
    if not pts:
        return (0, 0)
    return (sum(p[0] for p in pts)/len(pts), sum(p[1] for p in pts)/len(pts))


def nearest_feature(features, lon0, lat0):
    best = None
    bd = 1e18
    for f in features:
        geom = f.get("geometry", {})
        if geom.get("type") != "Polygon":
            continue
        coords = geom["coordinates"][0]
        cx, cy = polygon_centroid(coords)
        d = (cx-lon0)**2 + (cy-lat0)**2
        if d < bd:
            best, bd = f, d
    return best


def polygon_area_m2(coords_lonlat, lon0, lat0):
    pts = [local_xy(lon, lat, lon0, lat0) for lon, lat in coords_lonlat]
    if len(pts) < 3:
        return 0.0
    if pts[0] == pts[-1]:
        pts = pts[:-1]
    area = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        area += x1 * y2 - x2 * y1
    return abs(area) * 0.5


def height_from_tags(props, default=12.0, area_m2=0.0):
    props = props or {}
    h = props.get("height")
    if h:
        try:
            return float(str(h).replace("m", "").replace(",", "."))
        except Exception:
            pass
    levels = props.get("building:levels") or props.get("levels")
    if levels:
        try:
            return max(3.0, float(levels) * 3.0)
        except Exception:
            pass

    b = props.get("building", "")
    amenity = props.get("amenity", "")

    # Heuristic fallback when OSM lacks building:levels. This avoids turning
    # every apartment block into a tiny one-storey box.
    if b in ["apartments", "residential", "dormitory"]:
        if area_m2 > 2500:
            return 36.0
        if area_m2 > 1200:
            return 27.0
        if area_m2 > 600:
            return 18.0
        return 12.0
    if b in ["school", "kindergarten"] or amenity in ["school", "kindergarten"]:
        return 10.0
    if b in ["commercial", "retail", "industrial", "warehouse"]:
        return 9.0 if area_m2 > 800 else 6.0
    if b in ["garage", "garages", "shed", "service", "roof"]:
        return 3.5
    if area_m2 > 2500:
        return 24.0
    if area_m2 > 1000:
        return 18.0
    if area_m2 > 400:
        return 12.0
    return default


def create_building_from_polygon(coords_lonlat, lon0, lat0, height, name, mat):
    verts2 = [local_xy(lon, lat, lon0, lat0) for lon, lat in coords_lonlat]
    if verts2[0] == verts2[-1]:
        verts2 = verts2[:-1]
    if len(verts2) < 3:
        return None
    verts = [(x, y, 0) for x, y in verts2] + [(x, y, height) for x, y in verts2]
    n = len(verts2)
    faces = []
    faces.append(tuple(range(n-1, -1, -1)))
    faces.append(tuple(range(n, 2*n)))
    for i in range(n):
        faces.append((i, (i+1)%n, (i+1)%n + n, i+n))
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return obj


def road_width_m(props):
    highway = (props or {}).get("highway", "")
    if highway in ["motorway", "trunk"]:
        return 14.0
    if highway in ["primary"]:
        return 10.0
    if highway in ["secondary"]:
        return 8.0
    if highway in ["tertiary"]:
        return 7.0
    if highway in ["residential", "unclassified", "living_street"]:
        return 5.0
    if highway in ["service"]:
        return 3.5
    if highway in ["footway", "path", "pedestrian", "cycleway", "steps"]:
        return 1.4
    return 4.0


def create_road_segment(x1, y1, x2, y2, width, z, mat, name):
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < 0.05:
        return None
    nx, ny = -dy / length, dx / length
    hw = width / 2.0
    verts = [
        (x1 + nx * hw, y1 + ny * hw, z),
        (x1 - nx * hw, y1 - ny * hw, z),
        (x2 - nx * hw, y2 - ny * hw, z),
        (x2 + nx * hw, y2 + ny * hw, z),
    ]
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return obj


def create_roads(roads_fc, lon0, lat0, mat):
    """Create roads as flat strips, not raised tubes.

    Earlier versions used bevelled curves, which looked like black pipes and
    polluted ControlNet/Canny. Flat ribbons are much better as a geometric cue.
    """
    z = 0.12
    for idx, f in enumerate(roads_fc.get("features", [])):
        geom = f.get("geometry", {})
        if geom.get("type") != "LineString":
            continue
        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue
        props = f.get("properties", {}) or {}
        width = road_width_m(props)
        pts = [local_xy(lon, lat, lon0, lat0) for lon, lat in coords]
        for j in range(len(pts) - 1):
            x1, y1 = pts[j]
            x2, y2 = pts[j + 1]
            create_road_segment(x1, y1, x2, y2, width, z, mat, f"road_{idx}_{j}")


def area_material_for_tags(props, mats):
    props = props or {}
    landuse = props.get("landuse")
    leisure = props.get("leisure")
    natural = props.get("natural")
    amenity = props.get("amenity")
    if natural == "water" or landuse in ["reservoir", "basin"]:
        return mats["water"]
    if leisure in ["park", "garden", "playground", "pitch"] or landuse in ["grass", "forest", "meadow", "recreation_ground"]:
        return mats["green"]
    if landuse in ["residential", "commercial", "retail", "industrial"]:
        return mats["urban"]
    if amenity in ["school", "kindergarten", "hospital", "clinic"]:
        return mats["urban"]
    return mats["urban"]


def create_flat_polygon(coords_lonlat, lon0, lat0, z, mat, name):
    pts = [local_xy(lon, lat, lon0, lat0) for lon, lat in coords_lonlat]
    if len(pts) < 3:
        return None
    if pts[0] == pts[-1]:
        pts = pts[:-1]
    verts = [(x, y, z) for x, y in pts]
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(verts, [], [tuple(range(len(verts)))])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return obj


def create_areas(areas_fc, lon0, lat0, mats):
    # Draw OSM landuse/leisure polygons as subtle flat surfaces. This makes
    # OSM-only mode useful without relying on a satellite raster.
    count = 0
    for idx, f in enumerate(areas_fc.get("features", [])):
        geom = f.get("geometry", {})
        if geom.get("type") != "Polygon":
            continue
        coords = geom.get("coordinates", [[]])[0]
        if len(coords) < 3:
            continue
        mat = area_material_for_tags(f.get("properties", {}) or {}, mats)
        obj = create_flat_polygon(coords, lon0, lat0, 0.06, mat, f"area_{idx}")
        if obj:
            count += 1
    print(f"OSM area polygons created: {count}")


def add_simple_trees(areas_fc, lon0, lat0, mat_trunk, mat_leaf):
    count = 0
    for f in areas_fc.get("features", [])[:80]:
        props = f.get("properties", {})
        if props.get("leisure") not in ["park", "garden"] and props.get("landuse") not in ["grass", "forest"]:
            continue
        coords = f.get("geometry", {}).get("coordinates", [[]])[0]
        if len(coords) < 3:
            continue
        cx, cy = polygon_centroid(coords)
        x, y = local_xy(cx, cy, lon0, lat0)
        bpy.ops.mesh.primitive_cylinder_add(vertices=8, radius=0.5, depth=4, location=(x, y, 2))
        trunk = bpy.context.object; trunk.name = "tree_trunk"; trunk.data.materials.append(mat_trunk)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=6, radius=3.0, location=(x, y, 5.5))
        leaf = bpy.context.object; leaf.name = "tree_leaf"; leaf.data.materials.append(mat_leaf)
        count += 1
        if count > 120:
            break


def set_world_color(color, strength=0.9):
    world = bpy.context.scene.world or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.color = color[:3]
    try:
        world.use_nodes = True
        bg = world.node_tree.nodes.get('Background')
        if bg:
            bg.inputs['Color'].default_value = color
            bg.inputs['Strength'].default_value = strength
    except Exception:
        pass


def set_lighting():
    bpy.ops.object.light_add(type='SUN', location=(0, 0, 100))
    sun = bpy.context.object
    sun.name = "soft_sun"
    sun.data.energy = 2.5
    sun.rotation_euler = (math.radians(40), 0, math.radians(130))
    # Proper Blender background sky. Do not paint sky in postprocessing: that can
    # cover buildings and corrupt inpaint masks.
    set_world_color((0.50, 0.68, 0.95, 1.0), strength=0.9)
    try:
        bpy.context.scene.eevee.use_gtao = True
    except Exception:
        pass
    try:
        bpy.context.scene.render.film_transparent = False
        bpy.context.scene.view_settings.view_transform = 'Standard'
        bpy.context.scene.view_settings.look = 'Medium High Contrast'
        bpy.context.scene.view_settings.exposure = 0
        bpy.context.scene.view_settings.gamma = 1
    except Exception:
        pass


def direction_to_euler(direction):
    # Camera looks along local -Z; keep local Y as up.
    return Vector(direction).to_track_quat('-Z', 'Y').to_euler()


def render_face(name, rotation, cam_loc, out_dir, size, suffix="color"):
    cam_data = bpy.data.cameras.new(f"cam_{name}_{suffix}")
    cam = bpy.data.objects.new(f"cam_{name}_{suffix}", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = cam_loc
    cam.rotation_euler = rotation
    cam_data.type = 'PERSP'
    cam_data.angle = math.radians(90)
    bpy.context.scene.camera = cam
    bpy.context.scene.render.filepath = str(out_dir / f"{name}_{suffix}.png")
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(cam, do_unlink=True)


def set_render_engine(requested: str):
    """Set Blender render engine with version-safe fallback.

    Blender 4.x may expose BLENDER_EEVEE_NEXT, while Blender 5.0.1 on the
    user's machine exposes BLENDER_EEVEE. Older configs can therefore break.
    """
    requested = requested or "BLENDER_EEVEE"
    try:
        enum_items = bpy.context.scene.render.bl_rna.properties['engine'].enum_items
        available = {item.identifier for item in enum_items}
    except Exception:
        available = {"BLENDER_EEVEE", "BLENDER_WORKBENCH", "CYCLES"}

    candidates = [requested]
    if requested == "BLENDER_EEVEE_NEXT":
        candidates += ["BLENDER_EEVEE", "BLENDER_WORKBENCH"]
    elif requested == "BLENDER_EEVEE":
        candidates += ["BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH"]
    else:
        candidates += ["BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH"]

    for engine in candidates:
        if engine in available:
            bpy.context.scene.render.engine = engine
            print(f"Render engine: {engine} (requested: {requested}; available: {sorted(available)})")
            return engine
    raise RuntimeError(f"No supported render engine found. Requested={requested}; available={sorted(available)}")


def set_render_samples():
    # Blender versions expose EEVEE settings under different properties.
    for attr in ["eevee", "eevee_next"]:
        settings = getattr(bpy.context.scene, attr, None)
        if settings is not None and hasattr(settings, "taa_render_samples"):
            settings.taa_render_samples = 32
            return


def main():
    args = parse_args()
    project_dir = ROOT / "projects" / args.project
    cfg = read_json(project_dir / "scene_config.json") or {}
    if not cfg:
        # fallback: YAML is not always installed in Blender Python, so master.py writes scene_config.json before launching.
        raise RuntimeError("scene_config.json missing")
    lat0 = cfg["project"]["lat"]
    lon0 = cfg["project"]["lon"]
    radius = cfg["project"].get("radius_m", 800)
    size = cfg.get("render", {}).get("cube_face_size", 1024)
    render_cfg = cfg.get("render", {}) or {}
    global VECTOR_OFFSET_EAST_M, VECTOR_OFFSET_NORTH_M, VECTOR_SCALE_MULTIPLIER
    VECTOR_OFFSET_EAST_M = float(render_cfg.get("vector_offset_east_m", 0.0))
    VECTOR_OFFSET_NORTH_M = float(render_cfg.get("vector_offset_north_m", 0.0))
    VECTOR_SCALE_MULTIPLIER = float(render_cfg.get("vector_scale_multiplier", 1.0))
    print(f"Vector/OSM transform: scale={VECTOR_SCALE_MULTIPLIER:.5f}, east={VECTOR_OFFSET_EAST_M:.2f}m, north={VECTOR_OFFSET_NORTH_M:.2f}m")
    out_dir = project_dir / "blockout"
    out_dir.mkdir(parents=True, exist_ok=True)

    clear_scene()
    set_render_engine(cfg.get("render", {}).get("engine", "BLENDER_EEVEE"))
    bpy.context.scene.render.resolution_x = size
    bpy.context.scene.render.resolution_y = size
    set_render_samples()

    # Dark blockout buildings are intentional: they separate silhouettes from sky
    # and reduce the chance that img2img treats buildings as sky/blank white slabs.
    # Final materials are generated by ComfyUI, not by this clay blockout.
    mat_main = make_mat("main_building_mat", tuple(render_cfg.get("main_building_color", [0.22, 0.20, 0.18, 1])))
    mat_building = make_mat("building_mat", tuple(render_cfg.get("building_color", [0.18, 0.19, 0.20, 1])))
    mat_road = make_mat("road_mat", tuple(render_cfg.get("road_color", [0.12, 0.13, 0.13, 1])))
    mat_trunk = make_mat("tree_trunk", (0.28, 0.14, 0.06, 1))
    mat_leaf = make_mat("tree_leaf", (0.10, 0.25, 0.10, 1))
    area_mats = {
        "green": make_mat("area_green", (0.10, 0.32, 0.12, 1)),
        "water": make_mat("area_water", (0.08, 0.18, 0.28, 1)),
        "urban": make_mat("area_urban", (0.40, 0.40, 0.37, 1)),
    }

    create_ground(project_dir, radius, cfg)
    areas = read_json(project_dir / "source/osm/areas.geojson", {"features": []})
    create_areas(areas, lon0, lat0, area_mats)
    buildings = read_json(project_dir / "source/osm/buildings.geojson", {"features": []})
    main_feature = nearest_feature(buildings.get("features", []), lon0, lat0)
    main_id = None
    if main_feature:
        main_id = main_feature.get("properties", {}).get("osm_id")
    for i, f in enumerate(buildings.get("features", [])):
        geom = f.get("geometry", {})
        if geom.get("type") != "Polygon":
            continue
        is_main = f.get("properties", {}).get("osm_id") == main_id
        area_m2 = polygon_area_m2(geom["coordinates"][0], lon0, lat0)
        h = cfg.get("main_building", {}).get("height_m", 45) if is_main else height_from_tags(f.get("properties", {}), default=12.0, area_m2=area_m2)
        create_building_from_polygon(geom["coordinates"][0], lon0, lat0, h, "MAIN_BUILDING" if is_main else f"building_{i}", mat_main if is_main else mat_building)

    roads = read_json(project_dir / "source/osm/roads.geojson", {"features": []})
    create_roads(roads, lon0, lat0, mat_road)
    add_simple_trees(areas, lon0, lat0, mat_trunk, mat_leaf)
    set_lighting()

    cam_cfg = cfg.get("camera", {})
    main_center_xy = (0.0, 0.0)
    main_height = float(cfg.get("main_building", {}).get("height_m", 45))
    if main_feature:
        main_coords = main_feature.get("geometry", {}).get("coordinates", [[]])[0]
        mc_lon, mc_lat = polygon_centroid(main_coords)
        main_center_xy = local_xy(mc_lon, mc_lat, lon0, lat0)
    if cam_cfg.get("placement", "main_relative") == "main_relative":
        cam_loc = Vector((
            main_center_xy[0] + float(cam_cfg.get("offset_east_m", 60)),
            main_center_xy[1] + float(cam_cfg.get("offset_north_m", -45)),
            main_height + float(cam_cfg.get("height_above_main_m", 6.0)),
        ))
        print(f"Camera main-relative: loc=({cam_loc.x:.2f},{cam_loc.y:.2f},{cam_loc.z:.2f}), main_center=({main_center_xy[0]:.2f},{main_center_xy[1]:.2f}), main_height={main_height:.2f}")
    else:
        cam_loc = Vector((float(cam_cfg.get("offset_east_m", 80)), float(cam_cfg.get("offset_north_m", -70)), float(cam_cfg.get("altitude_m", 100))))
        print(f"Camera absolute: loc=({cam_loc.x:.2f},{cam_loc.y:.2f},{cam_loc.z:.2f})")

    # True cubemap axes. Previous versions used oblique rotations and had up/down inverted.
    # front=+Y, right=+X, back=-Y, left=-X, up=+Z/sky, down=-Z/nadir.
    rotations = {
        "front": direction_to_euler((0, 1, 0)),
        "right": direction_to_euler((1, 0, 0)),
        "back": direction_to_euler((0, -1, 0)),
        "left": direction_to_euler((-1, 0, 0)),
        "up": direction_to_euler((0, 0, 1)),
        "down": direction_to_euler((0, 0, -1)),
    }
    # 1) Color blockout faces for first-pass img2img.
    for face, rot in rotations.items():
        render_face(face, rot, cam_loc, out_dir, size, suffix="color")

    # 2) Black/white masks of the main building for second/main facade pass.
    # White = editable main building, black = keep untouched.
    mask_white = make_emission_mat("mask_white", (1, 1, 1, 1))
    mask_black = make_emission_mat("mask_black", (0, 0, 0, 1))
    apply_main_mask_materials(mask_white, mask_black)
    # Mask pass must have strictly black background. If world nodes remain blue,
    # ImageToMask(red channel) treats sky as editable mask, corrupting main_pass.
    set_world_color((0, 0, 0, 1), strength=1.0)
    for face, rot in rotations.items():
        render_face(face, rot, cam_loc, out_dir, size, suffix="mask_main")

    print(f"Saved blockout color renders and main masks to {out_dir}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
