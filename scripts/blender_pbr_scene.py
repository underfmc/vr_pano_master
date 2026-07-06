# -*- coding: utf-8 -*-
"""Blender PBR scene renderer — equirectangular panorama via cubemap stitch.

Renders 6 cubemap faces in EEVEE, stitches to equirectangular via py360convert.
Buildings have windows, floor ledges, balconies (main), and roof parapets.
Trees use linked duplicates for speed.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import random
import sys
from pathlib import Path

import bpy
from mathutils import Vector, Euler

# Add parent directory to sys.path so we can import pano_master
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Auto-install required packages into Blender's Python
import subprocess, importlib
for pkg, pip_name in [("PIL", "Pillow"), ("py360convert", "py360convert"), ("numpy", "numpy")]:
    try:
        importlib.import_module(pkg)
    except ImportError:
        print(f"Installing {pip_name} into Blender Python...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name, "--quiet"])

ROOT = Path(__file__).resolve().parents[1]
VECTOR_OFFSET_EAST_M = 0.0
VECTOR_OFFSET_NORTH_M = 0.0
VECTOR_SCALE_MULTIPLIER = 1.0


# ============================================================
# HELPERS
# ============================================================

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
    R = 6378137.0
    x = math.radians(lon - lon0) * R * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * R
    x = x * VECTOR_SCALE_MULTIPLIER + VECTOR_OFFSET_EAST_M
    y = y * VECTOR_SCALE_MULTIPLIER + VECTOR_OFFSET_NORTH_M
    return x, y


def clear_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    for block in bpy.data.meshes:
        if block.users == 0:
            bpy.data.meshes.remove(block)
    for block in bpy.data.materials:
        if block.users == 0:
            bpy.data.materials.remove(block)


def polygon_centroid(coords):
    pts = coords[:-1] if coords and coords[0] == coords[-1] else coords
    if not pts:
        return (0, 0)
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def nearest_feature(features, lon0, lat0):
    best, bd = None, 1e18
    for f in features:
        geom = f.get("geometry", {})
        if geom.get("type") != "Polygon":
            continue
        cx, cy = polygon_centroid(geom["coordinates"][0])
        d = (cx - lon0) ** 2 + (cy - lat0) ** 2
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
    if b in ["apartments", "residential", "dormitory"]:
        if area_m2 > 2500: return 36.0
        if area_m2 > 1200: return 27.0
        if area_m2 > 600: return 18.0
        return 12.0
    if b in ["school", "kindergarten"] or amenity in ["school", "kindergarten"]:
        return 10.0
    if b in ["commercial", "retail", "industrial", "warehouse"]:
        return 9.0 if area_m2 > 800 else 6.0
    if b in ["garage", "garages", "shed", "service", "roof"]:
        return 3.5
    if area_m2 > 2500: return 24.0
    if area_m2 > 1000: return 18.0
    if area_m2 > 400: return 12.0
    return default


# ============================================================
# MATERIALS
# ============================================================

def _init_mat(name):
    """Create material with Principled BSDF + Output. Version-safe."""
    mat = bpy.data.materials.new(name)
    try:
        mat.use_nodes = True
    except (DeprecationWarning, Exception):
        pass
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    nt.links.new(bsdf.outputs[0], out.inputs['Surface'])
    return mat, nt, bsdf


def _set_input(bsdf, name, value):
    """Set BSDF input, version-safe."""
    try:
        bsdf.inputs[name].default_value = value
    except (KeyError, TypeError):
        aliases = {'Specular IOR Level': 'Specular'}
        alt = aliases.get(name)
        if alt:
            try:
                bsdf.inputs[alt].default_value = value
            except Exception:
                pass


def _node(nt, type_name):
    return nt.nodes.new(type_name)


def _link(nt, a, b):
    nt.links.new(a, b)


def make_facade_material(seed=0):
    """PBR facade material - uses downloaded textures if available, else procedural."""
    rng = random.Random(seed)
    
    # Try to load PBR textures
    try:
        from pano_master.texture_loader import load_pbr_texture, apply_pbr_to_bsdf
        texture_options = ["concrete_wall", "brick_wall", "plaster"]
        texture_name = rng.choice(texture_options)
        texture_set = load_pbr_texture(texture_name)
        
        if texture_set and texture_set.get('diffuse'):
            # Use downloaded PBR texture
            mat, nt, bsdf = _init_mat(f"facade_{seed}_{texture_name}_pbr")
            apply_pbr_to_bsdf(nt, bsdf, texture_set)
            
            # Lighten the texture by mixing with white
            mix_node = nt.nodes.new('ShaderNodeMixRGB')
            mix_node.blend_type = 'MIX'
            mix_node.inputs['Fac'].default_value = 0.3  # 30% lighter
            
            white_node = nt.nodes.new('ShaderNodeRGB')
            white_node.outputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
            
            # Find the image texture node
            for node in nt.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    # Insert mix node between texture and BSDF
                    for link in nt.links:
                        if link.from_node == node and link.from_socket.name == 'Color':
                            nt.links.remove(link)
                            nt.links.new(node.outputs['Color'], mix_node.inputs[1])
                            nt.links.new(white_node.outputs[0], mix_node.inputs[2])
                            nt.links.new(mix_node.outputs[0], bsdf.inputs['Base Color'])
                            break
                    break
            
            _set_input(bsdf, 'Roughness', 0.75)
            return mat
    except Exception as e:
        print(f"  Note: Could not load PBR texture: {e}")
    
    # Fallback to procedural materials with lighter colors
    mat_type = rng.choice(["concrete", "brick", "plaster", "concrete", "plaster"])
    mat, nt, bsdf = _init_mat(f"facade_{seed}_{mat_type}")

    # Shared noise for variation
    noise = _node(nt, 'ShaderNodeTexNoise')
    noise.inputs['Scale'].default_value = 2.5 + rng.uniform(-0.5, 0.5)
    noise.inputs['Detail'].default_value = 6.0
    noise.inputs['Roughness'].default_value = 0.7

    # Second noise for larger-scale variation (stains, weathering)
    weather_noise = _node(nt, 'ShaderNodeTexNoise')
    weather_noise.inputs['Scale'].default_value = 0.8
    weather_noise.inputs['Detail'].default_value = 4.0
    weather_noise.inputs['Roughness'].default_value = 0.6

    if mat_type == "concrete":
        # Light concrete color
        base_r = 0.65 + rng.uniform(-0.05, 0.05)
        base_g = 0.63 + rng.uniform(-0.05, 0.05)
        base_b = 0.60 + rng.uniform(-0.05, 0.05)

        # Base color
        base_rgb = _node(nt, 'ShaderNodeRGB')
        base_rgb.outputs[0].default_value = (base_r, base_g, base_b, 1)

        # Noise-based color variation
        color_mix = _node(nt, 'ShaderNodeMixRGB')
        color_mix.blend_type = 'MULTIPLY'
        color_mix.inputs['Fac'].default_value = 0.15
        _link(nt, base_rgb.outputs[0], color_mix.inputs[1])
        _link(nt, noise.outputs['Fac'], color_mix.inputs[2])

        # Panel joints using Brick texture
        brick = _node(nt, 'ShaderNodeTexBrick')
        brick.inputs['Scale'].default_value = 0.33  # ~3m panels
        brick.inputs['Mortar Size'].default_value = 0.012
        brick.inputs['Mortar Smooth'].default_value = 0.1
        brick.offset = 0  # No offset for panels

        # Darken panel joints
        groove_mix = _node(nt, 'ShaderNodeMixRGB')
        groove_mix.blend_type = 'MIX'
        groove_mix.inputs['Fac'].default_value = 0.35
        groove_rgb = _node(nt, 'ShaderNodeRGB')
        groove_rgb.outputs[0].default_value = (0.30, 0.28, 0.26, 1)

        _link(nt, color_mix.outputs[0], groove_mix.inputs[1])
        _link(nt, groove_rgb.outputs[0], groove_mix.inputs[2])
        _link(nt, brick.outputs['Color'], groove_mix.inputs['Fac'])

        # Weathering/stains overlay
        stain_mix = _node(nt, 'ShaderNodeMixRGB')
        stain_mix.blend_type = 'MULTIPLY'
        stain_mix.inputs['Fac'].default_value = 0.12
        stain_rgb = _node(nt, 'ShaderNodeRGB')
        stain_rgb.outputs[0].default_value = (0.7, 0.65, 0.6, 1)

        _link(nt, groove_mix.outputs[0], stain_mix.inputs[1])
        _link(nt, stain_rgb.outputs[0], stain_mix.inputs[2])
        _link(nt, weather_noise.outputs['Fac'], stain_mix.inputs['Fac'])

        _link(nt, stain_mix.outputs[0], bsdf.inputs['Base Color'])

        # Bump map for panel joints
        bump = _node(nt, 'ShaderNodeBump')
        bump.inputs['Strength'].default_value = 0.25
        bump.inputs['Distance'].default_value = 0.02
        _link(nt, brick.outputs['Fac'], bump.inputs['Height'])
        _link(nt, bump.outputs[0], bsdf.inputs['Normal'])

        _set_input(bsdf, 'Roughness', 0.82)

    elif mat_type == "brick":
        # Lighter brick facade
        brick_r = 0.65 + rng.uniform(-0.08, 0.08)
        brick_g = 0.40 + rng.uniform(-0.05, 0.05)
        brick_b = 0.30 + rng.uniform(-0.04, 0.04)
        brick_b = 0.16 + rng.uniform(-0.05, 0.05)

        # Brick texture
        brick = _node(nt, 'ShaderNodeTexBrick')
        brick.inputs['Scale'].default_value = 4.0  # Standard brick size
        brick.inputs['Mortar Size'].default_value = 0.025
        brick.inputs['Mortar Smooth'].default_value = 0.05

        # Color variation per brick
        color_ramp = _node(nt, 'ShaderNodeValToRGB')
        color_ramp.color_ramp.elements[0].color = (brick_r * 0.65, brick_g * 0.65, brick_b * 0.65, 1)
        color_ramp.color_ramp.elements[1].color = (brick_r, brick_g, brick_b, 1)
        # Add mid-tone
        if len(color_ramp.color_ramp.elements) > 2:
            color_ramp.color_ramp.elements.remove(color_ramp.color_ramp.elements[2])
        mid = color_ramp.color_ramp.elements.new(0.4)
        mid.color = (brick_r * 0.82, brick_g * 0.82, brick_b * 0.82, 1)

        _link(nt, brick.outputs['Color'], color_ramp.inputs['Fac'])

        # Add noise-based weathering
        weather_mix = _node(nt, 'ShaderNodeMixRGB')
        weather_mix.blend_type = 'MULTIPLY'
        weather_mix.inputs['Fac'].default_value = 0.1
        weather_rgb = _node(nt, 'ShaderNodeRGB')
        weather_rgb.outputs[0].default_value = (0.75, 0.7, 0.65, 1)

        _link(nt, color_ramp.outputs[0], weather_mix.inputs[1])
        _link(nt, weather_rgb.outputs[0], weather_mix.inputs[2])
        _link(nt, weather_noise.outputs['Fac'], weather_mix.inputs['Fac'])

        _link(nt, weather_mix.outputs[0], bsdf.inputs['Base Color'])

        # Strong bump for brick texture
        bump = _node(nt, 'ShaderNodeBump')
        bump.inputs['Strength'].default_value = 0.4
        bump.inputs['Distance'].default_value = 0.015
        _link(nt, brick.outputs['Fac'], bump.inputs['Height'])
        _link(nt, bump.outputs[0], bsdf.inputs['Normal'])

        _set_input(bsdf, 'Roughness', 0.78)

    else:  # plaster
        # Smooth plaster/stucco facade
        colors = [
            (0.78, 0.76, 0.70),  # Warm white
            (0.75, 0.70, 0.58),  # Cream/yellow
            (0.68, 0.70, 0.74),  # Light blue-grey
            (0.73, 0.68, 0.60),  # Beige
            (0.72, 0.72, 0.68),  # Neutral grey
        ]
        base = colors[rng.randint(0, len(colors) - 1)]

        base_rgb = _node(nt, 'ShaderNodeRGB')
        base_rgb.outputs[0].default_value = (*base, 1)

        # Subtle noise variation
        color_mix = _node(nt, 'ShaderNodeMixRGB')
        color_mix.blend_type = 'MULTIPLY'
        color_mix.inputs['Fac'].default_value = 0.08
        _link(nt, base_rgb.outputs[0], color_mix.inputs[1])
        _link(nt, noise.outputs['Fac'], color_mix.inputs[2])

        # Stains and weathering
        stain_mix = _node(nt, 'ShaderNodeMixRGB')
        stain_mix.blend_type = 'MULTIPLY'
        stain_mix.inputs['Fac'].default_value = 0.15
        stain_rgb = _node(nt, 'ShaderNodeRGB')
        stain_rgb.outputs[0].default_value = (0.65, 0.6, 0.55, 1)

        _link(nt, color_mix.outputs[0], stain_mix.inputs[1])
        _link(nt, stain_rgb.outputs[0], stain_mix.inputs[2])
        _link(nt, weather_noise.outputs['Fac'], stain_mix.inputs['Fac'])

        _link(nt, stain_mix.outputs[0], bsdf.inputs['Base Color'])

        # Subtle bump for plaster texture
        bump = _node(nt, 'ShaderNodeBump')
        bump.inputs['Strength'].default_value = 0.12
        bump.inputs['Distance'].default_value = 0.01
        _link(nt, noise.outputs['Fac'], bump.inputs['Height'])
        _link(nt, bump.outputs[0], bsdf.inputs['Normal'])

        _set_input(bsdf, 'Roughness', 0.72)

    return mat


def make_window_material():
    """Realistic window glass with reflections."""
    mat, nt, bsdf = _init_mat("window_glass")
    _set_input(bsdf, 'Base Color', (0.04, 0.06, 0.08, 1))  # Very dark
    _set_input(bsdf, 'Metallic', 0.0)
    _set_input(bsdf, 'Roughness', 0.05)  # Very smooth for reflections
    _set_input(bsdf, 'Specular IOR Level', 0.9)  # Strong specular
    # Add slight blue tint
    _set_input(bsdf, 'Transmission Weight', 0.15)  # Slight transparency
    return mat


def make_simple_material(name, color, roughness=0.85):
    """Simple solid color material."""
    mat, nt, bsdf = _init_mat(name)
    _set_input(bsdf, 'Base Color', (*color, 1))
    _set_input(bsdf, 'Roughness', roughness)
    return mat


def make_road_material():
    """Realistic asphalt road with PBR texture."""
    # Try to load PBR texture
    try:
        from pano_master.texture_loader import load_pbr_texture, apply_pbr_to_bsdf
        texture_set = load_pbr_texture("asphalt")
        
        if texture_set and texture_set.get('diffuse'):
            mat, nt, bsdf = _init_mat("road_asphalt_pbr")
            apply_pbr_to_bsdf(nt, bsdf, texture_set)
            _set_input(bsdf, 'Roughness', 0.88)
            return mat
    except Exception as e:
        print(f"  Note: Could not load asphalt PBR texture: {e}")
    
    # Fallback to procedural
    mat, nt, bsdf = _init_mat("road_asphalt")
    
    # Base dark asphalt color
    base_rgb = _node(nt, 'ShaderNodeRGB')
    base_rgb.outputs[0].default_value = (0.08, 0.08, 0.09, 1)
    
    # Noise for asphalt texture variation
    noise = _node(nt, 'ShaderNodeTexNoise')
    noise.inputs['Scale'].default_value = 8.0
    noise.inputs['Detail'].default_value = 8.0
    noise.inputs['Roughness'].default_value = 0.8
    
    # Mix noise with base color
    color_mix = _node(nt, 'ShaderNodeMixRGB')
    color_mix.blend_type = 'MIX'
    color_mix.inputs['Fac'].default_value = 0.15
    lighter_rgb = _node(nt, 'ShaderNodeRGB')
    lighter_rgb.outputs[0].default_value = (0.14, 0.14, 0.15, 1)
    
    _link(nt, base_rgb.outputs[0], color_mix.inputs[1])
    _link(nt, lighter_rgb.outputs[0], color_mix.inputs[2])
    _link(nt, noise.outputs['Fac'], color_mix.inputs['Fac'])
    _link(nt, color_mix.outputs[0], bsdf.inputs['Base Color'])
    
    # Bump for asphalt texture
    bump = _node(nt, 'ShaderNodeBump')
    bump.inputs['Strength'].default_value = 0.2
    bump.inputs['Distance'].default_value = 0.005
    _link(nt, noise.outputs['Fac'], bump.inputs['Height'])
    _link(nt, bump.outputs[0], bsdf.inputs['Normal'])
    
    _set_input(bsdf, 'Roughness', 0.88)
    return mat


def make_roof_material(seed=0):
    """Roof material with slight variation."""
    rng = random.Random(seed)
    mat, nt, bsdf = _init_mat(f"roof_{seed}")
    
    # Darker roof color with variation
    base_val = 0.12 + rng.uniform(-0.02, 0.02)
    base_rgb = _node(nt, 'ShaderNodeRGB')
    base_rgb.outputs[0].default_value = (base_val, base_val, base_val + 0.01, 1)
    
    # Add noise variation
    noise = _node(nt, 'ShaderNodeTexNoise')
    noise.inputs['Scale'].default_value = 3.0
    noise.inputs['Detail'].default_value = 4.0
    
    color_mix = _node(nt, 'ShaderNodeMixRGB')
    color_mix.blend_type = 'MULTIPLY'
    color_mix.inputs['Fac'].default_value = 0.1
    
    _link(nt, base_rgb.outputs[0], color_mix.inputs[1])
    _link(nt, noise.outputs['Fac'], color_mix.inputs[2])
    _link(nt, color_mix.outputs[0], bsdf.inputs['Base Color'])
    
    _set_input(bsdf, 'Roughness', 0.90)
    return mat


# ============================================================
# BUILDING GENERATION
# ============================================================

def create_building(coords_lonlat, lon0, lat0, height, name, facade_mat, window_mat,
                    roof_mat, levels=None, is_main=False, add_detail=True, camera_pos=None, max_window_dist=150.0):
    """Create building body + windows + ledges + balconies + parapet."""
    verts2 = [local_xy(lon, lat, lon0, lat0) for lon, lat in coords_lonlat]
    if verts2[0] == verts2[-1]:
        verts2 = verts2[:-1]
    if len(verts2) < 3:
        return None

    # Check distance to camera for window detail
    if camera_pos and add_detail:
        center_x = sum(v[0] for v in verts2) / len(verts2)
        center_y = sum(v[1] for v in verts2) / len(verts2)
        dist = math.hypot(center_x - camera_pos[0], center_y - camera_pos[1])
        if dist > max_window_dist:
            add_detail = False  # Too far, skip windows

    n = len(verts2)
    verts = [(x, y, 0) for x, y in verts2] + [(x, y, height) for x, y in verts2]
    faces = [tuple(range(n - 1, -1, -1)), tuple(range(n, 2 * n))]
    for i in range(n):
        faces.append((i, (i + 1) % n, (i + 1) % n + n, i + n))

    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(facade_mat)
    obj.data.materials.append(roof_mat)
    for poly in obj.data.polygons:
        poly.material_index = 1 if poly.index == 1 else 0

    if not add_detail:
        return obj

    if levels is None:
        levels = max(1, int(height / 3.0))
    floor_h = height / levels

    for i in range(n):
        x1, y1 = verts2[i]
        x2, y2 = verts2[(i + 1) % n]
        edge_len = math.hypot(x2 - x1, y2 - y1)
        if edge_len < 1.5:  # Lower threshold for small buildings
            continue
        dx, dy = (x2 - x1) / edge_len, (y2 - y1) / edge_len
        nx, ny = dy, -dx  # outward normal

        # Floor ledges
        for fl in range(1, levels):
            z = fl * floor_h
            _add_quad(f"{name}_ldg_{i}_{fl}", [
                (x1 + nx * 0.07, y1 + ny * 0.07, z - 0.04),
                (x2 + nx * 0.07, y2 + ny * 0.07, z - 0.04),
                (x2 + nx * 0.07, y2 + ny * 0.07, z + 0.04),
                (x1 + nx * 0.07, y1 + ny * 0.07, z + 0.04),
            ], roof_mat)

        # Windows
        n_win = max(1, int(edge_len / 2.8))
        spacing = edge_len / (n_win + 1)
        for w in range(n_win):
            t = spacing * (w + 1)
            cx, cy = x1 + dx * t + nx * 0.05, y1 + dy * t + ny * 0.05
            ww, wh = 1.1, floor_h * 0.42
            angle = math.atan2(dy, dx)

            for fl in range(levels):
                cz = fl * floor_h + floor_h * 0.48
                _add_quad(f"{name}_w_{i}_{fl}_{w}", [
                    (cx - dx * ww / 2, cy - dy * ww / 2, cz - wh / 2),
                    (cx + dx * ww / 2, cy + dy * ww / 2, cz - wh / 2),
                    (cx + dx * ww / 2, cy + dy * ww / 2, cz + wh / 2),
                    (cx - dx * ww / 2, cy - dy * ww / 2, cz + wh / 2),
                ], window_mat)

                # Balconies on main building every 3rd floor
                if is_main and fl > 0 and fl % 3 == 0 and w % 2 == 0:
                    bw, bd = 1.3, 0.9
                    _add_quad(f"{name}_bal_{i}_{fl}_{w}", [
                        (cx - dx * bw / 2, cy - dy * bw / 2, cz - wh / 2 - 0.12),
                        (cx + dx * bw / 2, cy + dy * bw / 2, cz - wh / 2 - 0.12),
                        (cx + dx * bw / 2 + nx * bd, cy + dy * bw / 2 + ny * bd, cz - wh / 2 - 0.12),
                        (cx - dx * bw / 2 + nx * bd, cy - dy * bw / 2 + ny * bd, cz - wh / 2 - 0.12),
                    ], roof_mat)
                    # Railing
                    rh = 1.0
                    _add_quad(f"{name}_rl_{i}_{fl}_{w}", [
                        (cx - dx * bw / 2 + nx * bd, cy - dy * bw / 2 + ny * bd, cz - wh / 2 - 0.12),
                        (cx + dx * bw / 2 + nx * bd, cy + dy * bw / 2 + ny * bd, cz - wh / 2 - 0.12),
                        (cx + dx * bw / 2 + nx * bd, cy + dy * bw / 2 + ny * bd, cz - wh / 2 - 0.12 + rh),
                        (cx - dx * bw / 2 + nx * bd, cy - dy * bw / 2 + ny * bd, cz - wh / 2 - 0.12 + rh),
                    ], window_mat)

        # Roof parapet
        ph = 0.5
        _add_quad(f"{name}_par_{i}", [
            (x1 + nx * 0.12, y1 + ny * 0.12, height),
            (x2 + nx * 0.12, y2 + ny * 0.12, height),
            (x2 + nx * 0.12, y2 + ny * 0.12, height + ph),
            (x1 + nx * 0.12, y1 + ny * 0.12, height + ph),
        ], roof_mat)

    return obj


def _add_quad(name, verts, mat):
    """Add a single quad mesh object."""
    m = bpy.data.meshes.new(name)
    m.from_pydata(verts, [], [(0, 1, 2, 3)])
    m.update()
    o = bpy.data.objects.new(name, m)
    bpy.context.collection.objects.link(o)
    o.data.materials.append(mat)
    return o


# ============================================================
# ROADS & AREAS
# ============================================================

def road_width_m(props):
    hw = (props or {}).get("highway", "")
    widths = {"motorway": 14, "trunk": 14, "primary": 10, "secondary": 8,
              "tertiary": 7, "residential": 5, "unclassified": 5,
              "living_street": 5, "service": 3.5}
    return widths.get(hw, 4.0)


def create_roads(roads_fc, lon0, lat0, mat):
    z = 0.04
    count = 0
    for idx, f in enumerate(roads_fc.get("features", [])):
        geom = f.get("geometry", {})
        if geom.get("type") != "LineString":
            continue
        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue
        width = road_width_m(f.get("properties", {}))
        pts = [local_xy(lon, lat, lon0, lat0) for lon, lat in coords]
        for j in range(len(pts) - 1):
            x1, y1 = pts[j]
            x2, y2 = pts[j + 1]
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy)
            if length < 0.05:
                continue
            nx_n, ny_n = -dy / length, dx / length
            hw = width / 2
            _add_quad(f"road_{idx}_{j}", [
                (x1 + nx_n * hw, y1 + ny_n * hw, z),
                (x1 - nx_n * hw, y1 - ny_n * hw, z),
                (x2 - nx_n * hw, y2 - ny_n * hw, z),
                (x2 + nx_n * hw, y2 + ny_n * hw, z),
            ], mat)
            count += 1
    print(f"Roads: {count} segments")


def create_areas(areas_fc, lon0, lat0, mats):
    count = 0
    for idx, f in enumerate(areas_fc.get("features", [])):
        geom = f.get("geometry", {})
        if geom.get("type") != "Polygon":
            continue
        coords = geom.get("coordinates", [[]])[0]
        if len(coords) < 3:
            continue
        props = f.get("properties", {}) or {}
        landuse = props.get("landuse")
        leisure = props.get("leisure")
        natural = props.get("natural")
        if natural == "water" or landuse in ["reservoir", "basin"]:
            mat = mats["water"]
        elif (leisure in ["park", "garden", "playground", "pitch"]
              or landuse in ["grass", "forest", "meadow", "recreation_ground"]):
            mat = mats["grass"]
        else:
            mat = mats["urban"]
        pts = [local_xy(lon, lat, lon0, lat0) for lon, lat in coords]
        if pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) < 3:
            continue
        verts = [(x, y, 0.01) for x, y in pts]
        m = bpy.data.meshes.new(f"area_{idx}")
        m.from_pydata(verts, [], [tuple(range(len(verts)))])
        m.update()
        o = bpy.data.objects.new(f"area_{idx}", m)
        bpy.context.collection.objects.link(o)
        o.data.materials.append(mat)
        count += 1
    print(f"Areas: {count}")


# ============================================================
# TREES (fast linked duplicates)
# ============================================================

def add_trees(areas_fc, lon0, lat0, trunk_mat, leaf_mat, max_trees=50):
    """Add trees using linked duplicates for speed."""
    # Create template meshes ONCE
    bpy.ops.mesh.primitive_cylinder_add(vertices=6, radius=0.15, depth=3.0, location=(0, 0, -9999))
    trunk_tmpl = bpy.context.object
    trunk_tmpl.data.materials.append(trunk_mat)
    trunk_mesh = trunk_tmpl.data
    # Move templates far away so they don't appear in render
    trunk_tmpl.location = (0, 0, -9999)

    bpy.ops.mesh.primitive_uv_sphere_add(segments=8, ring_count=5, radius=2.5, location=(0, 0, -9999))
    crown_tmpl = bpy.context.object
    crown_tmpl.data.materials.append(leaf_mat)
    crown_mesh = crown_tmpl.data
    crown_tmpl.location = (0, 0, -9999)

    count = 0
    for f in areas_fc.get("features", []):
        props = f.get("properties", {})
        is_green = (props.get("leisure") in ["park", "garden"]
                    or props.get("landuse") in ["grass", "forest"])
        if not is_green:
            continue
        coords = f.get("geometry", {}).get("coordinates", [[]])[0]
        if len(coords) < 3:
            continue
        pts = [local_xy(lon, lat, lon0, lat0) for lon, lat in coords]
        if pts[0] == pts[-1]:
            pts = pts[:-1]
        min_x, max_x = min(p[0] for p in pts), max(p[0] for p in pts)
        min_y, max_y = min(p[1] for p in pts), max(p[1] for p in pts)

        area = (max_x - min_x) * (max_y - min_y)
        n_trees = min(int(area / 150), 12)
        rng = random.Random(count * 73 + 17)

        for _ in range(n_trees):
            if count >= max_trees:
                break
            x = min_x + rng.random() * (max_x - min_x)
            y = min_y + rng.random() * (max_y - min_y)
            th = 5 + rng.random() * 7

            # Trunk
            to = bpy.data.objects.new(f"tr_{count}", trunk_mesh)
            bpy.context.collection.objects.link(to)
            to.location = (x, y, th * 0.2)

            # Crown
            co = bpy.data.objects.new(f"cr_{count}", crown_mesh)
            bpy.context.collection.objects.link(co)
            co.location = (x, y, th * 0.6)
            s = 0.7 + rng.random() * 0.5
            co.scale = (s, s, s * (0.6 + rng.random() * 0.5))

            count += 1
        if count >= max_trees:
            break

    print(f"Trees: {count}")


# ============================================================
# LIGHTING
# ============================================================

def set_lighting():
    world = bpy.context.scene.world or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    try:
        world.use_nodes = True
    except Exception:
        pass
    nt = world.node_tree
    nt.nodes.clear()

    out = nt.nodes.new('ShaderNodeOutputWorld')
    bg = nt.nodes.new('ShaderNodeBackground')
    bg.inputs['Strength'].default_value = 1.2

    # Sky gradient
    tc = nt.nodes.new('ShaderNodeTexCoord')
    grad = nt.nodes.new('ShaderNodeTexGradient')
    grad.gradient_type = 'QUADRATIC_SPHERE'
    ramp = nt.nodes.new('ShaderNodeValToRGB')
    ramp.color_ramp.elements[0].color = (0.35, 0.42, 0.35, 1)   # ground
    ramp.color_ramp.elements[1].color = (0.52, 0.70, 0.95, 1)   # zenith blue
    e1 = ramp.color_ramp.elements.new(0.47)
    e1.color = (0.72, 0.80, 0.92, 1)  # near horizon
    e2 = ramp.color_ramp.elements.new(0.50)
    e2.color = (0.85, 0.84, 0.80, 1)  # horizon warm

    _link(nt, tc.outputs['Generated'], grad.inputs['Vector'])
    _link(nt, grad.outputs['Fac'], ramp.inputs['Fac'])
    _link(nt, ramp.outputs[0], bg.inputs['Color'])
    _link(nt, bg.outputs[0], out.inputs['Surface'])

    # Main sun
    bpy.ops.object.light_add(type='SUN', location=(0, 0, 100))
    sun = bpy.context.object
    sun.data.energy = 1.2
    sun.data.color = (1.0, 0.96, 0.90)
    sun.rotation_euler = (math.radians(50), 0, math.radians(140))
    try:
        sun.data.angle = math.radians(6)
    except Exception:
        pass

    # Fill light (opposite side)
    bpy.ops.object.light_add(type='SUN', location=(0, 0, 80))
    fill = bpy.context.object
    fill.data.energy = 0.5
    fill.data.color = (0.88, 0.90, 1.0)
    fill.rotation_euler = (math.radians(60), 0, math.radians(-40))


# ============================================================
# CUBEMAP RENDER + STITCH
# ============================================================

# Correct cubemap directions in Blender (Z-up) coordinate system.
# Each face: (name, camera_rotation_euler)
#
# py360convert uses Y-up, Z-forward:
#   F=+Z, R=+X, B=-Z, L=-X, U=+Y, D=-Y
# Blender uses Z-up, Y-forward:
#   forward=+Y, right=+X, up=+Z
#
# Mapping: Blender+Y → py360+F, Blender+X → py360+R, Blender+Z → py360+U

def _cam_rotation(forward_dir, up_hint='Z'):
    """Get camera Euler rotation looking in forward_dir with specified up."""
    v = Vector(forward_dir)
    q = v.to_track_quat('-Z', up_hint)
    return q.to_euler()


# Pre-computed rotations for each cubemap face
# Using to_track_quat('-Z', 'Y') for side faces ensures camera up = world +Z
FACE_ROTATIONS = {
    "front": _cam_rotation((0, 1, 0), 'Y'),     # look +Y, up=+Z
    "right": _cam_rotation((1, 0, 0), 'Y'),     # look +X, up=+Z
    "back":  _cam_rotation((0, -1, 0), 'Y'),    # look -Y, up=+Z
    "left":  _cam_rotation((-1, 0, 0), 'Y'),    # look -X, up=+Z
    "up":    Euler((math.pi, 0, 0), 'XYZ'),     # explicit: look +Z, up=-Y
    "down":  Euler((0, 0, 0), 'XYZ'),           # default: look -Z, up=+Y
}

# Mapping: our face name → py360convert key
FACE_TO_PY360 = {
    "front": "F",
    "right": "R",
    "back":  "B",
    "left":  "L",
    "up":    "U",
    "down":  "D",
}


def render_cubemap_and_stitch(out_path: Path, eq_width: int, eq_height: int, cam_location):
    """Render 6 cubemap faces in EEVEE, stitch to equirectangular."""
    import numpy as np
    from PIL import Image as PILImage
    try:
        import py360convert
    except ImportError:
        raise RuntimeError("py360convert required. Install: pip install py360convert numpy")

    face_size = eq_height  # 2048 for 4096x2048
    face_dir = out_path.parent / "cubemap_faces"
    face_dir.mkdir(parents=True, exist_ok=True)

    faces = {}
    for face_name in ["front", "right", "back", "left", "up", "down"]:
        print(f"  Face: {face_name}...")
        cam_data = bpy.data.cameras.new(f"cam_{face_name}")
        cam = bpy.data.objects.new(f"cam_{face_name}", cam_data)
        bpy.context.collection.objects.link(cam)

        cam.location = cam_location
        cam.rotation_euler = FACE_ROTATIONS[face_name]
        cam_data.type = 'PERSP'
        cam_data.angle = math.radians(90)

        bpy.context.scene.camera = cam
        bpy.context.scene.render.resolution_x = face_size
        bpy.context.scene.render.resolution_y = face_size
        face_path = face_dir / f"{face_name}.png"
        bpy.context.scene.render.filepath = str(face_path)
        bpy.context.scene.render.image_settings.file_format = 'PNG'
        bpy.ops.render.render(write_still=True)
        bpy.data.objects.remove(cam, do_unlink=True)

        img = PILImage.open(face_path).convert("RGB")
        faces[FACE_TO_PY360[face_name]] = np.array(img)

    # Stitch
    print("  Stitching...")
    eq = py360convert.c2e(faces, eq_height, eq_width, mode="bilinear", cube_format="dict")

    # Auto-orient: ensure sky (bright) is at top, ground (dark) at bottom
    eq = _auto_orient(np.array(eq))
    PILImage.fromarray(eq).save(out_path)
    print(f"✅ Saved: {out_path} ({eq_width}x{eq_height})")


def _auto_orient(eq_rgb):
    """Detect and fix equirectangular orientation so sky is at top."""
    import numpy as np
    from PIL import Image as PILImage

    h, w = eq_rgb.shape[:2]
    gray = eq_rgb.mean(axis=2)

    best_score = -1e9
    best_v_shift = 0

    # Try vertical shifts (0 to h-1 in steps of h//16)
    for shift in range(0, h, max(1, h // 16)):
        rolled = np.roll(gray, -shift, axis=0)
        top_band = rolled[:h // 8, :].mean()       # should be bright (sky)
        top_std = rolled[:h // 8, :].std()           # should be low (uniform sky)
        bot_band = rolled[-h // 8:, :].mean()        # should be darker (ground)
        mid_band = rolled[h // 4:3 * h // 4, :].std()  # should be high (buildings)

        score = top_band * 0.3 - top_std * 0.3 - bot_band * 0.2 + mid_band * 0.2
        if score > best_score:
            best_score = score
            best_v_shift = shift

    if best_v_shift > 0:
        print(f"  Auto-orient: vertical shift {best_v_shift}px")
        return np.roll(eq_rgb, -best_v_shift, axis=0).astype("uint8")

    return eq_rgb.astype("uint8")


def render_cubemap_depth(out_path: Path, eq_width: int, eq_height: int, cam_location):
    """Render depth map via Mist pass for ControlNet Depth."""
    import numpy as np
    from PIL import Image as PILImage
    try:
        import py360convert
    except ImportError:
        print("⚠ py360convert not available, skipping depth map")
        return

    face_size = eq_height
    face_dir = out_path.parent / "depth_faces"
    face_dir.mkdir(parents=True, exist_ok=True)

    # Enable Mist pass
    bpy.context.scene.view_layers[0].use_pass_mist = True
    world = bpy.context.scene.world
    world.mist_settings.start = 5
    world.mist_settings.depth = 400
    world.mist_settings.falloff = 'LINEAR'

    # Setup compositor to save Mist pass
    bpy.context.scene.use_nodes = True
    tree = bpy.context.scene.node_tree
    tree.nodes.clear()

    depth_faces = {}

    for face_name in ["front", "right", "back", "left", "up", "down"]:
        print(f"  Depth face: {face_name}...")

        # Setup camera
        cam_data = bpy.data.cameras.new(f"depth_cam_{face_name}")
        cam = bpy.data.objects.new(f"depth_cam_{face_name}", cam_data)
        bpy.context.collection.objects.link(cam)
        cam.location = cam_location
        cam.rotation_euler = FACE_ROTATIONS[face_name]
        cam_data.type = 'PERSP'
        cam_data.angle = math.radians(90)
        bpy.context.scene.camera = cam

        bpy.context.scene.render.resolution_x = face_size
        bpy.context.scene.render.resolution_y = face_size

        # Clear and setup compositor for this face
        tree.nodes.clear()
        rl = tree.nodes.new('CompositorNodeRLayers')
        out_node = tree.nodes.new('CompositorNodeOutputFile')
        out_node.base_path = str(face_dir)
        out_node.file_slots[0].path = f"depth_{face_name}_"
        out_node.format.file_format = 'PNG'
        out_node.format.color_mode = 'BW'
        out_node.format.color_depth = '8'
        tree.links.new(rl.outputs['Mist'], out_node.inputs[0])

        # Render
        bpy.ops.render.render(write_still=False)

        # Find the saved file (CompositorNodeOutputFile adds frame number)
        depth_file = list(face_dir.glob(f"depth_{face_name}_*.png"))
        if depth_file:
            img = PILImage.open(depth_file[0]).convert("L")
            depth_faces[FACE_TO_PY360[face_name]] = np.array(img)
            depth_file[0].unlink()  # cleanup
        else:
            print(f"    ⚠ No depth file for {face_name}")

        bpy.data.objects.remove(cam, do_unlink=True)

    # Disable compositor
    bpy.context.scene.use_nodes = False

    if depth_faces:
        # Stitch depth cubemap
        print("  Stitching depth map...")
        eq_depth = py360convert.c2e(depth_faces, eq_height, eq_width, mode="bilinear", cube_format="dict")

        # Apply same orientation fix as color
        # (use the same shift that was applied to color)
        eq_depth_uint8 = eq_depth.astype("uint8") if eq_depth.dtype != np.uint8 else eq_depth
        PILImage.fromarray(eq_depth_uint8).save(out_path)
        print(f"✅ Depth map saved: {out_path}")
    else:
        print("⚠ No depth faces rendered")


# ============================================================
# MAIN
# ============================================================

def main():
    args = parse_args()
    project_dir = ROOT / "projects" / args.project
    cfg = read_json(project_dir / "scene_config.json") or {}
    if not cfg:
        raise RuntimeError("scene_config.json missing")

    lat0 = cfg["project"]["lat"]
    lon0 = cfg["project"]["lon"]
    radius = cfg["project"].get("radius_m", 500)
    render_cfg = cfg.get("render", {}) or {}

    global VECTOR_OFFSET_EAST_M, VECTOR_OFFSET_NORTH_M, VECTOR_SCALE_MULTIPLIER
    VECTOR_OFFSET_EAST_M = float(render_cfg.get("vector_offset_east_m", 0.0))
    VECTOR_OFFSET_NORTH_M = float(render_cfg.get("vector_offset_north_m", 0.0))
    VECTOR_SCALE_MULTIPLIER = float(render_cfg.get("vector_scale_multiplier", 1.0))

    out_dir = project_dir / "pbr_output"
    out_dir.mkdir(parents=True, exist_ok=True)

    eq_width = int(render_cfg.get("equirect_width", 4096))
    eq_height = eq_width // 2

    clear_scene()

    # Set render engine: prefer Cycles GPU for speed
    engine_set = False
    try:
        # Try Cycles with GPU
        bpy.context.scene.render.engine = 'CYCLES'
        prefs = bpy.context.preferences.addons['cycles'].preferences
        prefs.compute_device_type = 'OPTIX'
        prefs.get_devices()
        for device in prefs.devices:
            if device.type == 'OPTIX':
                device.use = True
        bpy.context.scene.cycles.device = 'GPU'
        bpy.context.scene.cycles.samples = 128
        print("Render engine: CYCLES (GPU)")
        engine_set = True
    except Exception as e:
        print(f"Cycles GPU not available: {e}")
        # Fallback to EEVEE
        try:
            bpy.context.scene.render.engine = 'BLENDER_EEVEE'
            print("Render engine: BLENDER_EEVEE (fallback)")
            for attr in ['eevee', 'eevee_next']:
                s = getattr(bpy.context.scene, attr, None)
                if s and hasattr(s, 'taa_render_samples'):
                    s.taa_render_samples = 64
                    break
        except Exception:
            pass
    except Exception:
        pass

    # ---- Materials ----
    print("Creating materials...")
    facade_mats = [make_facade_material(seed=i) for i in range(12)]
    window_mat = make_window_material()
    road_mat = make_road_material()
    grass_mat = make_simple_material("grass", (0.15, 0.28, 0.10), 0.85)
    sidewalk_mat = make_simple_material("sidewalk", (0.52, 0.50, 0.47), 0.85)
    water_mat = make_simple_material("water", (0.08, 0.15, 0.25), 0.30)
    urban_mat = make_simple_material("urban", (0.40, 0.38, 0.35), 0.85)
    trunk_mat = make_simple_material("trunk", (0.25, 0.14, 0.06), 0.90)
    leaf_mat = make_simple_material("leaf", (0.10, 0.25, 0.08), 0.80)
    roof_mat = make_roof_material(seed=42)

    area_mats = {"grass": grass_mat, "water": water_mat, "urban": urban_mat}

    # ---- Ground ----
    ground_size = float(render_cfg.get("ground_size_m", radius * 2))
    bpy.ops.mesh.primitive_plane_add(size=ground_size, location=(0, 0, -0.05))
    gnd = bpy.context.object
    gnd.name = "ground"
    gnd_mat = make_simple_material("ground", (0.25, 0.30, 0.20), 0.90)
    gnd.data.materials.append(gnd_mat)

    # ---- Areas ----
    areas = read_json(project_dir / "source/osm/areas.geojson", {"features": []})
    create_areas(areas, lon0, lat0, area_mats)

    # ---- Buildings ----
    buildings = read_json(project_dir / "source/osm/buildings.geojson", {"features": []})
    # Find main building - support multiple buildings (complex)
    main_ids = []
    main_buildings_config = cfg.get("main_building", {})

    # Strategy: Find nearest building, then cluster all buildings within radius
    search_lat = main_buildings_config.get("lat", lat0)
    search_lon = main_buildings_config.get("lon", lon0)

    print(f"Searching for main building cluster near: ({search_lon:.6f}, {search_lat:.6f})")

    # Find nearest building (main corpus)
    main_feature = nearest_feature(buildings.get("features", []), search_lon, search_lat)

    if main_feature:
        main_osm_id = main_feature.get("properties", {}).get("osm_id")
        main_ids.append(main_osm_id)

        # Get main building center
        main_coords = main_feature["geometry"]["coordinates"][0]
        main_cx, main_cy = polygon_centroid(main_coords)
        main_local_x, main_local_y = local_xy(main_cx, main_cy, lon0, lat0)

        print(f"✓ Main corpus found: osm_id={main_osm_id}")
        print(f"  Center: ({main_cx:.6f}, {main_cy:.6f})")

    # Filter: exclude non-residential buildings from main cluster
    cluster_radius = float(main_buildings_config.get("cluster_radius_m", 80))
    print(f"  Searching for residential corpora within {cluster_radius}m...")

    # Tags that indicate non-residential buildings
    non_residential_tags = {
        'amenity': ['police', 'fire_station', 'substation', 'transformer', 'plant',
                   'hospital', 'clinic', 'school', 'kindergarten', 'university',
                   'bank', 'atm', 'post_office', 'restaurant', 'cafe', 'bar',
                   'fuel', 'car_wash', 'parking', 'toilets'],
        'building': ['industrial', 'commercial', 'retail', 'warehouse', 'garage',
                    'garages', 'shed', 'service', 'transformer_tower', 'substation'],
        'power': ['substation', 'transformer', 'plant', 'generator'],
        'office': ['government', 'company', 'estate_agent'],
    }

    def is_residential(props):
        """Check if building is residential."""
        if not props:
            return True  # Assume residential if no tags

        # Check explicit residential tags
        building = props.get('building', '')
        if building in ['residential', 'apartments', 'house', 'dormitory', 'yes']:
            return True

        # Check non-residential tags
        for tag, values in non_residential_tags.items():
            if props.get(tag) in values:
                return False

        # If has building tag but not in non-residential list, assume residential
        if building:
            return True

        return True

    for f in buildings.get("features", []):
        osm_id = f.get("properties", {}).get("osm_id")
        if osm_id == main_osm_id or osm_id in main_ids:
            continue

        coords = f["geometry"]["coordinates"][0]
        cx, cy = polygon_centroid(coords)
        bx, by = local_xy(cx, cy, lon0, lat0)

        dist = math.hypot(bx - main_local_x, by - main_local_y)
        if dist <= cluster_radius:
            # Check if residential
            props = f.get("properties", {})
            if is_residential(props):
                main_ids.append(osm_id)
                print(f"    + Residential corpus: osm_id={osm_id}, distance={dist:.1f}m")
            else:
                print(f"    - Skipped non-residential: osm_id={osm_id}, type={props.get('amenity') or props.get('building')}")

        print(f"✓ Total main buildings: {len(main_ids)}")

    else:
        # Main building not found in OSM data
        # Check if manual coordinates provided
        manual_lat = main_buildings_config.get("manual_lat")
        manual_lon = main_buildings_config.get("manual_lon")

        if manual_lat and manual_lon:
            print(f"⚠ Main building not in OSM data, using manual coordinates")
            print(f"  Manual center: ({manual_lon:.6f}, {manual_lat:.6f})")
            main_local_x, main_local_y = local_xy(manual_lon, manual_lat, lon0, lat0)

            # Create placeholder building
            manual_height = float(main_buildings_config.get("height_m", 27))
            manual_levels = int(main_buildings_config.get("levels", 9))

            # Create rectangular polygon (~30m x 15m typical apartment building)
            # Approximate degrees: 30m ≈ 0.0004° longitude, 15m ≈ 0.00015° latitude
            placeholder_coords = [
                [manual_lon - 0.0004, manual_lat - 0.00015],  # SW
                [manual_lon + 0.0004, manual_lat - 0.00015],  # SE
                [manual_lon + 0.0004, manual_lat + 0.00015],  # NE
                [manual_lon - 0.0004, manual_lat + 0.00015],  # NW
                [manual_lon - 0.0004, manual_lat - 0.00015],  # Close polygon
            ]

            placeholder_feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [placeholder_coords]
                },
                "properties": {
                    "osm_id": "manual_main_building",
                    "building": "apartments",
                    "building:levels": str(manual_levels),
                    "source": "manual"
                }
            }

            # Add to buildings list
            buildings.get("features", []).append(placeholder_feature)
            main_ids.append("manual_main_building")

            print(f"  Created placeholder: ~30m × 15m × {manual_height}m")

        else:
            print("⚠ WARNING: No main building found!")
            print("  To fix: add manual_lat/manual_lon to main_building config")
            main_local_x, main_local_y = 0.0, 0.0

    # Calculate center of all main buildings for camera positioning
    main_center_local = (0.0, 0.0)
    if main_ids:
        main_features = [f for f in buildings.get("features", [])
                        if f.get("properties", {}).get("osm_id") in main_ids]
        if main_features:
            all_coords = []
            for f in main_features:
                cx, cy = polygon_centroid(f["geometry"]["coordinates"][0])
                all_coords.append(local_xy(cx, cy, lon0, lat0))
            main_center_local = (
                sum(c[0] for c in all_coords) / len(all_coords),
                sum(c[1] for c in all_coords) / len(all_coords)
            )
            print(f"✓ Main buildings found: {len(main_ids)}")
            print(f"  Center: ({main_center_local[0]:.1f}, {main_center_local[1]:.1f})m from project center")
        else:
            print("⚠ WARNING: No main buildings found in OSM data!")
    else:
        print("⚠ WARNING: No main building specified!")

    max_dist = float(render_cfg.get("max_building_dist_m", 400))
    visible = []
    for f in buildings.get("features", []):
        coords = f["geometry"]["coordinates"][0]
        cx, cy = polygon_centroid(coords)
        bx, by = local_xy(cx, cy, lon0, lat0)
        dist = math.hypot(bx, by)
        is_main = f.get("properties", {}).get("osm_id") in main_ids
        if is_main or dist <= max_dist:
            visible.append((f, dist))

    visible.sort(key=lambda x: (0 if x[0].get("properties", {}).get("osm_id") in main_ids else 1, x[1]))
    print(f"Buildings: {len(visible)} (from {len(buildings.get('features', []))}, max {max_dist:.0f}m)")

    # ---- Camera (compute early for window distance check) ----
    cam_cfg = cfg.get("camera", {})
    main_height = float(cfg.get("main_building", {}).get("height_m", 45))

    if cam_cfg.get("placement", "main_relative") == "main_relative":
        cam_loc = Vector((
            main_center_local[0] + float(cam_cfg.get("offset_east_m", 60)),
            main_center_local[1] + float(cam_cfg.get("offset_north_m", -45)),
            main_height + float(cam_cfg.get("height_above_main_m", 25.0)),
        ))
    else:
        cam_loc = Vector((
            float(cam_cfg.get("offset_east_m", 80)),
            float(cam_cfg.get("offset_north_m", -70)),
            float(cam_cfg.get("altitude_m", 100)),
        ))

    print(f"Camera: ({cam_loc.x:.1f}, {cam_loc.y:.1f}, {cam_loc.z:.1f})")

    rng = random.Random(42)
    main_levels = int(cfg.get("main_building", {}).get("levels", 9))

    for i, (f, dist) in enumerate(visible):
        geom = f.get("geometry", {})
        if geom.get("type") != "Polygon":
            continue
        is_main = f.get("properties", {}).get("osm_id") in main_ids
        area_m2 = polygon_area_m2(geom["coordinates"][0], lon0, lat0)

        if is_main:
            h = float(cfg.get("main_building", {}).get("height_m", 45))
            facade = facade_mats[0]
            levels = main_levels
        else:
            h = height_from_tags(f.get("properties", {}), default=12.0, area_m2=area_m2)
            facade = facade_mats[rng.randint(0, len(facade_mats) - 1)]
            levels = None

        create_building(
            geom["coordinates"][0], lon0, lat0, h,
            "MAIN" if is_main else f"bld_{i}",
            facade, window_mat, roof_mat,
            levels=levels, is_main=is_main,
            add_detail=True,
            camera_pos=(cam_loc.x, cam_loc.y),
            max_window_dist=150.0,
        )

    # ---- Roads ----
    roads = read_json(project_dir / "source/osm/roads.geojson", {"features": []})
    create_roads(roads, lon0, lat0, road_mat)

    # ---- Trees ----
    add_trees(areas, lon0, lat0, trunk_mat, leaf_mat)

    # ---- Lighting ----
    set_lighting()

    # ---- Render cubemap color ----
    render_cubemap_and_stitch(out_dir / "equirectangular.png", eq_width, eq_height, cam_loc)

    print("✅ Done!")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
