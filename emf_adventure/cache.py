from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .geo import DEFAULT_LAT, DEFAULT_LON, DEFAULT_ZOOM, Point, haversine_m, lonlat_to_tile, tile_coord_to_lonlat
from .mvt import decode_tile


TILE_URL = "https://map.emfcamp.org/tiles/_main/{z}/{x}/{y}"
DEFAULT_CACHE_PATH = Path("data/emf_map_cache.json")
USER_AGENT = "emf-map-adventure/0.1"


@dataclass(frozen=True)
class TileRef:
    z: int
    x: int
    y: int


def tile_refs_for_radius(center: Point, radius_m: float, zoom: int) -> list[TileRef]:
    center_x, center_y = lonlat_to_tile(center.lon, center.lat, zoom)
    west_lon, north_lat = tile_coord_to_lonlat(zoom, center_x, center_y, 4096, 0, 0)
    east_lon, south_lat = tile_coord_to_lonlat(zoom, center_x, center_y, 4096, 4096, 4096)
    width_m = haversine_m(Point(center.lat, west_lon), Point(center.lat, east_lon))
    height_m = haversine_m(Point(north_lat, center.lon), Point(south_lat, center.lon))
    dx = max(1, int(radius_m / max(width_m, 1)) + 2)
    dy = max(1, int(radius_m / max(height_m, 1)) + 2)
    refs = []
    for x in range(center_x - dx, center_x + dx + 1):
        for y in range(center_y - dy, center_y + dy + 1):
            refs.append(TileRef(zoom, x, y))
    return refs


def fetch_tile(tile: TileRef, timeout: float = 20) -> bytes:
    url = TILE_URL.format(z=tile.z, x=tile.x, y=tile.y)
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _feature_key(feature: dict[str, Any]) -> str:
    props = feature.get("properties", {})
    geom = feature.get("geometry", {})
    nameish = "|".join(
        str(props.get(key, "")) for key in ("_layer", "name", "text", "camping", "ref", "type")
    )
    coords = json.dumps(geom.get("coordinates", []), separators=(",", ":"))[:240]
    return f"{props.get('_layer')}|{feature.get('id')}|{nameish}|{coords}"


def refresh_cache(
    cache_path: Path = DEFAULT_CACHE_PATH,
    center: Point = Point(DEFAULT_LAT, DEFAULT_LON),
    radius_m: float = 900,
    zoom: int = DEFAULT_ZOOM,
    keep_tiles: bool = False,
) -> dict[str, Any]:
    tiles = tile_refs_for_radius(center, radius_m, zoom)
    features: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[str] = []
    raw_dir = cache_path.parent / "raw_tiles"
    if keep_tiles:
        raw_dir.mkdir(parents=True, exist_ok=True)

    for tile in tiles:
        try:
            data = fetch_tile(tile)
            if keep_tiles:
                (raw_dir / f"{tile.z}-{tile.x}-{tile.y}.pbf").write_bytes(data)
            decoded = decode_tile(data, tile.z, tile.x, tile.y)
            for feature in decoded["features"]:
                key = _feature_key(feature)
                if key in seen:
                    continue
                seen.add(key)
                features.append(feature)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            errors.append(f"{tile.z}/{tile.x}/{tile.y}: {exc}")

    cache = {
        "type": "FeatureCollection",
        "metadata": {
            "source": TILE_URL,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "center": {"lat": center.lat, "lon": center.lon},
            "radius_m": radius_m,
            "zoom": zoom,
            "tile_count": len(tiles),
            "feature_count": len(features),
            "errors": errors,
        },
        "features": features,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    return cache


def load_cache(cache_path: Path = DEFAULT_CACHE_PATH) -> dict[str, Any]:
    return json.loads(cache_path.read_text(encoding="utf-8"))

