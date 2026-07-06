# -*- coding: utf-8 -*-
"""2GIS API client for building data.

2GIS provides detailed building data for Russian cities:
- Building geometry (WKT format)
- Number of floors
- Building purpose
- Address

API docs: https://docs.2gis.com/ru/api/search
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


class TwoGISClient:
    """Client for 2GIS Catalog API v3.0."""

    BASE_URL = "https://catalog.api.2gis.com/3.0"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()

    def search_buildings_in_radius(self, lat: float, lon: float, radius_m: float = 500) -> List[Dict[str, Any]]:
        """Search for buildings within a radius from a point."""
        # Use /items/geocode endpoint with radius parameter
        params = {
            "lat": lat,
            "lon": lon,
            "type": "building",
            "radius": int(radius_m),
            "fields": "items.point,items.full_name,items.purpose_name,items.structure_info,items.geometry.centroid,items.geometry.hover,items.geometry.selection,items.address_name",
            "key": self.api_key,
        }

        url = f"{self.BASE_URL}/items/geocode"
        print(f"  2GIS API request: {url}")
        print(f"    lat={lat}, lon={lon}, radius={radius_m}m")

        try:
            response = self.session.get(url, params=params, timeout=30)
        except Exception as e:
            print(f"  ⚠ Request failed: {e}")
            return []

        if response.status_code != 200:
            print(f"  ⚠ 2GIS API error: {response.status_code}")
            print(f"    Response: {response.text[:500]}")
            return []

        data = response.json()

        # Debug: print raw response structure
        print(f"  Response keys: {list(data.keys())}")

        result = data.get("result", {})
        items = result.get("items", [])
        total = result.get("total", 0)

        print(f"  Total buildings found: {total}")

        if not items:
            print(f"  No items in response")
            return []

        all_buildings = []
        for i, item in enumerate(items):
            # Debug: print first item structure
            if i == 0:
                print(f"  First item keys: {list(item.keys())}")
                print(f"    type: {item.get('type')}")
                print(f"    purpose_name: {item.get('purpose_name')}")
                geom = item.get("geometry")
                if geom:
                    print(f"    geometry keys: {list(geom.keys())}")
                    hover = geom.get("hover")
                    selection = geom.get("selection")
                    centroid = geom.get("centroid")
                    print(f"    hover: {hover[:100] if hover else None}")
                    print(f"    selection: {selection[:100] if selection else None}")
                    print(f"    centroid: {centroid[:100] if centroid else None}")
                else:
                    print(f"    geometry: None")

            building = self._parse_building(item)
            if building:
                all_buildings.append(building)
            else:
                # Debug: count why buildings are skipped
                if i < 20:  # Only log first 20
                    geom = item.get("geometry")
                    if not geom:
                        print(f"    Item {i}: no geometry field")
                    elif not geom.get("hover") and not geom.get("selection"):
                        print(f"    Item {i}: no hover/selection in geometry")
                    else:
                        print(f"    Item {i}: parse failed for other reason")

        print(f"  2GIS: Parsed {len(all_buildings)} buildings (with polygons)")
        return all_buildings

    def _parse_building(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse 2GIS building item to GeoJSON feature."""
        geometry = item.get("geometry")
        if not geometry:
            return None

        # Try to get polygon from hover or selection
        wkt = geometry.get("hover") or geometry.get("selection")
        
        coords = None
        if wkt and "POLYGON" in wkt:
            coords = self._parse_wkt_polygon(wkt)
            if coords and len(coords) < 3:
                coords = None
        
        # Fallback: use centroid to create a small rectangular polygon
        if not coords:
            centroid = geometry.get("centroid")
            if centroid and "POINT" in centroid:
                # Parse POINT(lon lat)
                point_match = re.search(r"POINT\(([\d.]+) ([\d.]+)\)", centroid)
                if point_match:
                    lon = float(point_match.group(1))
                    lat = float(point_match.group(2))
                    # Create small rectangle (~20m x 15m typical building)
                    # Approximate: 0.0003° lon ≈ 20m, 0.00015° lat ≈ 15m at this latitude
                    d_lon = 0.0003
                    d_lat = 0.00015
                    coords = [
                        [lon - d_lon, lat - d_lat],
                        [lon + d_lon, lat - d_lat],
                        [lon + d_lon, lat + d_lat],
                        [lon - d_lon, lat + d_lat],
                        [lon - d_lon, lat - d_lat],  # Close polygon
                    ]

        if not coords or len(coords) < 3:
            return None

        # Extract building info
        structure_info = item.get("structure_info", {})
        floors = structure_info.get("floor_count")
        purpose = item.get("purpose_name", "residential")

        # Build GeoJSON feature
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [coords]
            },
            "properties": {
                "osm_id": item.get("id", f"2gis_{hash(str(coords))}"),
                "building": "apartments" if "жилой" in purpose.lower() else "yes",
                "source": "2gis",
            }
        }

        if floors:
            feature["properties"]["building:levels"] = str(floors)

        address = item.get("address_name")
        if address:
            feature["properties"]["addr:full"] = address

        return feature

    @staticmethod
    def _parse_wkt_polygon(wkt: str) -> List[List[float]]:
        """Parse WKT POLYGON to coordinate list.

        Example: POLYGON((lon1 lat1, lon2 lat2, ...))
        """
        match = re.search(r"POLYGON\(\((.*?)\)\)", wkt)
        if not match:
            return []

        coords_str = match.group(1)
        coords = []
        for point in coords_str.split(","):
            parts = point.strip().split()
            if len(parts) >= 2:
                lon, lat = float(parts[0]), float(parts[1])
                coords.append([lon, lat])

        # Ensure closed polygon
        if coords and coords[0] != coords[-1]:
            coords.append(coords[0])

        return coords


def fetch_2gis_buildings(api_key: str, lat: float, lon: float, radius_m: float = 500,
                          output_path: Optional[Path] = None) -> Dict[str, Any]:
    """Fetch buildings from 2GIS API and return as GeoJSON.

    Args:
        api_key: 2GIS API key
        lat: Center latitude
        lon: Center longitude
        radius_m: Search radius in meters
        output_path: Optional path to save GeoJSON

    Returns:
        GeoJSON FeatureCollection
    """
    client = TwoGISClient(api_key)
    buildings = client.search_buildings_in_radius(lat, lon, radius_m)

    geojson = {
        "type": "FeatureCollection",
        "features": buildings
    }

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(geojson, f, ensure_ascii=False, indent=2)
        print(f"✅ 2GIS data saved: {output_path}")

    return geojson


def merge_osm_and_2gis(osm_path: Path, twogis_path: Path, output_path: Path) -> None:
    """Merge OSM and 2GIS building data.

    Strategy: Use 2GIS data as primary (more detailed), fill gaps with OSM.
    """
    with open(osm_path, "r", encoding="utf-8") as f:
        osm_data = json.load(f)

    with open(twogis_path, "r", encoding="utf-8") as f:
        twogis_data = json.load(f)

    # Start with 2GIS buildings
    merged = twogis_data["features"]

    # Add OSM buildings that don't overlap with 2GIS
    # (simplified: just add all OSM buildings for now)
    merged.extend(osm_data["features"])

    result = {
        "type": "FeatureCollection",
        "features": merged
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"✅ Merged {len(twogis_data['features'])} 2GIS + {len(osm_data['features'])} OSM = {len(merged)} buildings")