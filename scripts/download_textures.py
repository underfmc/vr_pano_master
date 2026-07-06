#!/usr/bin/env python3
"""Download PBR textures from Poly Haven (CC0 license)."""
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

# Texture URLs from AmbientCG (CC0 license)
TEXTURES = {
    "concrete_wall": {
        "url": "https://ambientcg.com/get?file=Concrete032_2K-JPG.zip",
        "files": {
            "diffuse": "Concrete032_2K-JPG_Color.jpg",
            "normal": "Concrete032_2K-JPG_NormalGL.jpg",
            "roughness": "Concrete032_2K-JPG_Roughness.jpg",
        }
    },
    "brick_wall": {
        "url": "https://ambientcg.com/get?file=Bricks059_2K-JPG.zip",
        "files": {
            "diffuse": "Bricks059_2K-JPG_Color.jpg",
            "normal": "Bricks059_2K-JPG_NormalGL.jpg",
            "roughness": "Bricks059_2K-JPG_Roughness.jpg",
        }
    },
    "plaster": {
        "url": "https://ambientcg.com/get?file=Plaster004_2K-JPG.zip",
        "files": {
            "diffuse": "Plaster004_2K-JPG_Color.jpg",
            "normal": "Plaster004_2K-JPG_NormalGL.jpg",
            "roughness": "Plaster004_2K-JPG_Roughness.jpg",
        }
    },
    "asphalt": {
        "url": "https://ambientcg.com/get?file=Asphalt009_2K-JPG.zip",
        "files": {
            "diffuse": "Asphalt009_2K-JPG_Color.jpg",
            "normal": "Asphalt009_2K-JPG_NormalGL.jpg",
            "roughness": "Asphalt009_2K-JPG_Roughness.jpg",
        }
    },
}


def download_texture(name: str, info: dict, textures_dir: Path):
    """Download and extract a single texture."""
    texture_dir = textures_dir / name
    if texture_dir.exists():
        print(f"  ✓ {name} already exists")
        return True
    
    print(f"  ↓ Downloading {name}...")
    zip_path = textures_dir / f"{name}.zip"
    
    try:
        # Add User-Agent to avoid 403 Forbidden
        req = urllib.request.Request(
            info["url"],
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) VR-Pano-Master/1.0'}
        )
        with urllib.request.urlopen(req) as response:
            with open(zip_path, 'wb') as f:
                f.write(response.read())
    except Exception as e:
        print(f"  ✗ Failed to download {name}: {e}")
        return False
    
    # Extract
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(texture_dir)
        zip_path.unlink()
        print(f"  ✓ {name} extracted")
        return True
    except Exception as e:
        print(f"  ✗ Failed to extract {name}: {e}")
        return False


def main():
    """Download all textures."""
    textures_dir = Path(__file__).parent.parent / "assets" / "textures"
    textures_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Downloading PBR textures to: {textures_dir}")
    print("Source: AmbientCG (CC0 license)")
    print()
    
    success = 0
    for name, info in TEXTURES.items():
        if download_texture(name, info, textures_dir):
            success += 1
    
    print()
    print(f"Downloaded {success}/{len(TEXTURES)} textures")
    
    if success == len(TEXTURES):
        print("\n✓ All textures ready!")
        print("Run: python master.py render-pbr --project <name>")
    else:
        print("\n⚠ Some textures failed. You can retry or proceed with procedural materials.")


if __name__ == "__main__":
    main()
