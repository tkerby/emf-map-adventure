from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

from .geo import Point, bearing_degrees, compass_name, fmt_distance, haversine_m
from .theme import Theme, load_theme


USEFUL_UNNAMED_LAYERS = {
    "water_points": "water point",
    "labels_gate_labels_point": "gate",
    "gates": "gate",
    "areas_event": "event area",
    "areas_camping": "camping",
    "parking": "parking",
}

LANDMARK_LAYERS = {
    "areas_camping",
    "areas_event",
    "camping_centroid",
    "gates",
    "labels_gate_labels_point",
    "parking",
    "street_names",
    "structure_centroid",
    "water_points",
}


@dataclass
class Landmark:
    name: str
    point: Point
    layer: str
    properties: dict[str, Any]

    @property
    def search_text(self) -> str:
        bits = [self.name, self.layer]
        for key in ("type", "text", "camping", "ref"):
            value = self.properties.get(key)
            if value is not None:
                bits.append(str(value))
        canonical_name = normalize(self.name)
        if canonical_name == "stage a":
            bits.append("main stage")
        if canonical_name == "first aid":
            bits.append("medical")
        if canonical_name == "shop":
            bits.append("store")
        if canonical_name in {"entrance", "main gate"}:
            bits.append("site entrance main entrance south entrance")
        return normalize(" ".join(bits))


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def feature_point(feature: dict[str, Any]) -> Point | None:
    geom = feature.get("geometry", {})
    coords = geom.get("coordinates")
    geom_type = geom.get("type")
    if not coords:
        return None
    if geom_type == "Point":
        lon, lat = coords
        return Point(lat, lon)
    points = list(_walk_coords(coords))
    if not points:
        return None
    lon = sum(point[0] for point in points) / len(points)
    lat = sum(point[1] for point in points) / len(points)
    return Point(lat, lon)


def _walk_coords(coords: Any) -> Iterable[tuple[float, float]]:
    if not isinstance(coords, list) or not coords:
        return
    first = coords[0]
    if isinstance(first, (int, float)) and len(coords) >= 2:
        yield (float(coords[0]), float(coords[1]))
        return
    for item in coords:
        yield from _walk_coords(item)


def feature_name(feature: dict[str, Any]) -> str | None:
    props = feature.get("properties", {})
    layer = props.get("_layer", "")
    if layer not in LANDMARK_LAYERS:
        return None
    for key in ("name", "text", "camping", "ref"):
        value = props.get(key)
        if value not in (None, ""):
            if key == "camping":
                return f"Camping {value}"
            if layer == "parking":
                return f"{value} parking"
            if layer == "gates":
                return f"{value} gate"
            return str(value)
    if layer in USEFUL_UNNAMED_LAYERS:
        label = USEFUL_UNNAMED_LAYERS[layer]
        feature_type = props.get("type")
        if feature_type:
            return f"{label} ({feature_type})"
        return label
    return None


class World:
    def __init__(self, feature_collection: dict[str, Any], theme: Theme | None = None):
        self.features = feature_collection.get("features", [])
        self.metadata = feature_collection.get("metadata", {})
        self.theme = theme or load_theme()
        self.landmarks = self._build_landmarks()

    def _build_landmarks(self) -> list[Landmark]:
        landmarks: list[Landmark] = []
        seen: set[tuple[str, str, int, int]] = set()
        for feature in self.features:
            name = feature_name(feature)
            point = feature_point(feature)
            if not name or point is None:
                continue
            props = feature.get("properties", {})
            layer = str(props.get("_layer", "unknown"))
            key = (normalize(name), layer, round(point.lat * 100000), round(point.lon * 100000))
            if key in seen:
                continue
            seen.add(key)
            landmarks.append(Landmark(name, point, layer, props))
        return landmarks

    def nearby(self, position: Point, radius_m: float = 80, limit: int = 12) -> list[tuple[float, Landmark]]:
        matches = []
        for landmark in self.landmarks:
            distance = haversine_m(position, landmark.point)
            if distance <= radius_m:
                matches.append((distance, landmark))
        return sorted(matches, key=lambda item: item[0])[:limit]

    def describe_nearby(self, position: Point, radius_m: float = 80, limit: int = 12) -> str:
        matches = self.nearby(position, radius_m, limit)
        if not matches:
            return self.theme.text("nearby_empty", radius=fmt_distance(radius_m))
        lines = [self.theme.text("nearby_header", radius=fmt_distance(radius_m))]
        for distance, landmark in matches:
            bearing = bearing_degrees(position, landmark.point)
            lines.append(
                self.theme.text(
                    "nearby_item",
                    name=landmark.name,
                    direction=compass_name(bearing),
                    distance=fmt_distance(distance),
                    layer=landmark.layer,
                )
            )
        return "\n".join(lines)

    def find(self, query: str, limit: int = 6) -> list[Landmark]:
        needle = normalize(query)
        if not needle:
            return []
        scored: list[tuple[float, Landmark]] = []
        tokens = {token for token in needle.split() if len(token) > 1}
        for landmark in self.landmarks:
            haystack = landmark.search_text
            if needle in haystack:
                score = 100 + len(needle) / max(len(haystack), 1)
            else:
                overlap = tokens.intersection(haystack.split())
                if not overlap:
                    continue
                if len(tokens) > 1 and len(overlap) < len(tokens):
                    continue
                score = 10 + len(overlap) / max(len(tokens), 1)
            scored.append((score, landmark))
        scored.sort(key=lambda item: item[0], reverse=True)
        unique: list[Landmark] = []
        seen: set[tuple[str, str]] = set()
        for _, landmark in scored:
            key = (normalize(landmark.name), landmark.layer)
            if key in seen:
                continue
            seen.add(key)
            unique.append(landmark)
            if len(unique) >= limit:
                break
        return unique

    def describe_target(self, position: Point, target: Landmark) -> str:
        distance = haversine_m(position, target.point)
        bearing = bearing_degrees(position, target.point)
        return self.theme.text(
            "target_direction",
            name=target.name,
            direction=compass_name(bearing),
            distance=fmt_distance(distance),
        )
