from __future__ import annotations

from dataclasses import dataclass
import math


EARTH_RADIUS_M = 6_371_008.8
DEFAULT_LAT = 52.040163
DEFAULT_LON = -2.376955
DEFAULT_ZOOM = 17


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float

    def as_lonlat(self) -> tuple[float, float]:
        return self.lon, self.lat


def haversine_m(a: Point, b: Point) -> float:
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def bearing_degrees(a: Point, b: Point) -> float:
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlon = math.radians(b.lon - a.lon)
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def destination_point(start: Point, bearing: float, distance_m: float) -> Point:
    angular = distance_m / EARTH_RADIUS_M
    theta = math.radians(bearing)
    lat1 = math.radians(start.lat)
    lon1 = math.radians(start.lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular)
        + math.cos(lat1) * math.sin(angular) * math.cos(theta)
    )
    lon2 = lon1 + math.atan2(
        math.sin(theta) * math.sin(angular) * math.cos(lat1),
        math.cos(angular) - math.sin(lat1) * math.sin(lat2),
    )
    return Point(math.degrees(lat2), ((math.degrees(lon2) + 540) % 360) - 180)


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def tile_coord_to_lonlat(
    zoom: int, tile_x: int, tile_y: int, extent: int, local_x: int, local_y: int
) -> tuple[float, float]:
    n = 2**zoom
    world_x = (tile_x + local_x / extent) / n
    world_y = (tile_y + local_y / extent) / n
    lon = world_x * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * world_y))))
    return lon, lat


def meters_per_tile(lat: float, zoom: int) -> tuple[float, float]:
    x, y = lonlat_to_tile(DEFAULT_LON, lat, zoom)
    west, north = tile_coord_to_lonlat(zoom, x, y, 4096, 0, 0)
    east, _ = tile_coord_to_lonlat(zoom, x, y, 4096, 4096, 0)
    _, south = tile_coord_to_lonlat(zoom, x, y, 4096, 0, 4096)
    return (
        haversine_m(Point(lat, west), Point(lat, east)),
        haversine_m(Point(north, DEFAULT_LON), Point(south, DEFAULT_LON)),
    )


def local_xy_m(origin: Point, point: Point) -> tuple[float, float]:
    lat0 = math.radians(origin.lat)
    x = math.radians(point.lon - origin.lon) * EARTH_RADIUS_M * math.cos(lat0)
    y = math.radians(point.lat - origin.lat) * EARTH_RADIUS_M
    return x, y


def point_from_local_xy(origin: Point, x: float, y: float) -> Point:
    lat = origin.lat + math.degrees(y / EARTH_RADIUS_M)
    lon = origin.lon + math.degrees(x / (EARTH_RADIUS_M * math.cos(math.radians(origin.lat))))
    return Point(lat, lon)


COMPASS_BEARINGS = {
    "n": 0.0,
    "north": 0.0,
    "ne": 45.0,
    "northeast": 45.0,
    "north-east": 45.0,
    "e": 90.0,
    "east": 90.0,
    "se": 135.0,
    "southeast": 135.0,
    "south-east": 135.0,
    "s": 180.0,
    "south": 180.0,
    "sw": 225.0,
    "southwest": 225.0,
    "south-west": 225.0,
    "w": 270.0,
    "west": 270.0,
    "nw": 315.0,
    "northwest": 315.0,
    "north-west": 315.0,
}

COMPASS_NAMES = [
    "north",
    "north-east",
    "east",
    "south-east",
    "south",
    "south-west",
    "west",
    "north-west",
]


def compass_name(bearing: float) -> str:
    index = int((bearing + 22.5) // 45) % 8
    return COMPASS_NAMES[index]


def angular_difference(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def fmt_distance(distance_m: float) -> str:
    if distance_m < 10:
        return f"{distance_m:.0f}m"
    if distance_m < 1000:
        return f"{round(distance_m / 5) * 5:.0f}m"
    return f"{distance_m / 1000:.1f}km"

