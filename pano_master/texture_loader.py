# -*- coding: utf-8 -*-
"""PBR texture loader for Blender materials."""
from pathlib import Path
from typing import Optional, Tuple
import bpy


TEXTURES_DIR = Path(__file__).parent.parent / "assets" / "textures"


def load_pbr_texture(texture_name: str) -> Optional[dict]:
    """Load PBR texture set (diffuse, normal, roughness).
    
    Returns dict with keys: 'diffuse', 'normal', 'roughness' (bpy.types.Image or None)
    """
    texture_dir = TEXTURES_DIR / texture_name
    if not texture_dir.exists():
        return None
    
    # Common filename patterns
    patterns = {
        'diffuse': ['diff', 'albedo', 'color', 'basecolor'],
        'normal': ['nor', 'normal', 'norm'],
        'roughness': ['rough', 'roughness'],
    }
    
    result = {}
    for map_type, keywords in patterns.items():
        found = None
        for file in texture_dir.rglob("*.jpg"):
            filename_lower = file.stem.lower()
            if any(kw in filename_lower for kw in keywords):
                found = file
                break
        if not found:
            for file in texture_dir.rglob("*.png"):
                filename_lower = file.stem.lower()
                if any(kw in filename_lower for kw in keywords):
                    found = file
                    break
        
        if found:
            img = bpy.data.images.load(str(found), check_existing=True)
            if map_type == 'normal':
                img.colorspace_settings.name = 'Non-Color'
            result[map_type] = img
        else:
            result[map_type] = None
    
    return result if any(result.values()) else None


def apply_pbr_to_bsdf(nt: bpy.types.NodeTree, bsdf: bpy.types.Node, texture_set: dict):
    """Apply PBR texture set to Principled BSDF node."""
    # Diffuse/Albedo
    if texture_set.get('diffuse'):
        tex_node = nt.nodes.new('ShaderNodeTexImage')
        tex_node.image = texture_set['diffuse']
        tex_node.location = (-600, 300)
        
        # Texture coordinate + mapping for proper tiling
        tex_coord = nt.nodes.new('ShaderNodeTexCoord')
        tex_coord.location = (-1000, 300)
        mapping = nt.nodes.new('ShaderNodeMapping')
        mapping.location = (-800, 300)
        mapping.inputs['Scale'].default_value = (2.0, 2.0, 2.0)  # Tile 2x
        
        nt.links.new(tex_coord.outputs['UV'], mapping.inputs['Vector'])
        nt.links.new(mapping.outputs['Vector'], tex_node.inputs['Vector'])
        nt.links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
    
    # Normal map
    if texture_set.get('normal'):
        normal_tex = nt.nodes.new('ShaderNodeTexImage')
        normal_tex.image = texture_set['normal']
        normal_tex.location = (-600, 0)
        
        normal_map = nt.nodes.new('ShaderNodeNormalMap')
        normal_map.location = (-300, 0)
        normal_map.inputs['Strength'].default_value = 0.8
        
        nt.links.new(normal_tex.outputs['Color'], normal_map.inputs['Color'])
        nt.links.new(normal_map.outputs['Normal'], bsdf.inputs['Normal'])
    
    # Roughness map
    if texture_set.get('roughness'):
        rough_tex = nt.nodes.new('ShaderNodeTexImage')
        rough_tex.image = texture_set['roughness']
        rough_tex.location = (-600, -300)
        
        nt.links.new(rough_tex.outputs['Color'], bsdf.inputs['Roughness'])
