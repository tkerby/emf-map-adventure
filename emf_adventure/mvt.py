from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .geo import tile_coord_to_lonlat


GEOM_TYPES = {
    1: "Point",
    2: "LineString",
    3: "Polygon",
}


@dataclass
class ProtoField:
    number: int
    wire_type: int
    value: Any


class ProtoError(ValueError):
    pass


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise ProtoError("Unexpected end of varint")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 70:
            raise ProtoError("Varint is too long")


def _read_fixed32(data: bytes, pos: int) -> tuple[int, int]:
    if pos + 4 > len(data):
        raise ProtoError("Unexpected end of fixed32")
    return int.from_bytes(data[pos : pos + 4], "little"), pos + 4


def _read_fixed64(data: bytes, pos: int) -> tuple[int, int]:
    if pos + 8 > len(data):
        raise ProtoError("Unexpected end of fixed64")
    return int.from_bytes(data[pos : pos + 8], "little"), pos + 8


def _fields(data: bytes) -> list[ProtoField]:
    pos = 0
    fields = []
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        number = key >> 3
        wire_type = key & 0x7
        if wire_type == 0:
            value, pos = _read_varint(data, pos)
        elif wire_type == 1:
            value, pos = _read_fixed64(data, pos)
        elif wire_type == 2:
            length, pos = _read_varint(data, pos)
            if pos + length > len(data):
                raise ProtoError("Unexpected end of length-delimited field")
            value = data[pos : pos + length]
            pos += length
        elif wire_type == 5:
            value, pos = _read_fixed32(data, pos)
        else:
            raise ProtoError(f"Unsupported protobuf wire type {wire_type}")
        fields.append(ProtoField(number, wire_type, value))
    return fields


def _packed_varints(data: bytes) -> list[int]:
    pos = 0
    values = []
    while pos < len(data):
        value, pos = _read_varint(data, pos)
        values.append(value)
    return values


def _zigzag(value: int) -> int:
    return (value >> 1) ^ (-(value & 1))


def _parse_value(data: bytes) -> Any:
    import struct

    value: Any = None
    for field in _fields(data):
        if field.number == 1:
            value = field.value.decode("utf-8", errors="replace")
        elif field.number == 2:
            value = struct.unpack("<f", field.value.to_bytes(4, "little"))[0]
        elif field.number == 3:
            value = struct.unpack("<d", field.value.to_bytes(8, "little"))[0]
        elif field.number == 4:
            value = field.value
        elif field.number == 5:
            value = field.value
        elif field.number == 6:
            value = _zigzag(field.value)
        elif field.number == 7:
            value = bool(field.value)
    return value


def _parse_feature(data: bytes) -> dict[str, Any]:
    feature: dict[str, Any] = {"id": None, "tags": [], "type": None, "geometry": []}
    for field in _fields(data):
        if field.number == 1:
            feature["id"] = field.value
        elif field.number == 2:
            feature["tags"] = _packed_varints(field.value)
        elif field.number == 3:
            feature["type"] = field.value
        elif field.number == 4:
            feature["geometry"] = _packed_varints(field.value)
    return feature


def _decode_geometry(
    commands: list[int], geom_type: int, zoom: int, tile_x: int, tile_y: int, extent: int
) -> dict[str, Any] | None:
    cursor_x = 0
    cursor_y = 0
    index = 0
    parts: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] | None = None

    while index < len(commands):
        command_integer = commands[index]
        index += 1
        command = command_integer & 0x7
        count = command_integer >> 3

        if command in (1, 2):
            for _ in range(count):
                if index + 1 >= len(commands):
                    raise ProtoError("Geometry command ended mid-coordinate")
                dx = _zigzag(commands[index])
                dy = _zigzag(commands[index + 1])
                index += 2
                cursor_x += dx
                cursor_y += dy
                point = tile_coord_to_lonlat(zoom, tile_x, tile_y, extent, cursor_x, cursor_y)
                if command == 1:
                    current = [point]
                    parts.append(current)
                elif current is not None:
                    current.append(point)
        elif command == 7:
            if current and current[0] != current[-1]:
                current.append(current[0])
        else:
            raise ProtoError(f"Unsupported geometry command {command}")

    if geom_type == 1:
        points = [part[0] for part in parts if part]
        if not points:
            return None
        if len(points) == 1:
            return {"type": "Point", "coordinates": list(points[0])}
        return {"type": "MultiPoint", "coordinates": [list(point) for point in points]}

    if geom_type == 2:
        lines = [[list(point) for point in part] for part in parts if len(part) >= 2]
        if not lines:
            return None
        if len(lines) == 1:
            return {"type": "LineString", "coordinates": lines[0]}
        return {"type": "MultiLineString", "coordinates": lines}

    if geom_type == 3:
        rings = [[list(point) for point in part] for part in parts if len(part) >= 4]
        if not rings:
            return None
        return {"type": "Polygon", "coordinates": rings}

    return None


def decode_tile(data: bytes, zoom: int, tile_x: int, tile_y: int) -> dict[str, Any]:
    """Decode enough Mapbox Vector Tile data for the EMF site-plan tiles."""

    features: list[dict[str, Any]] = []
    tile_version = None
    for top_field in _fields(data):
        if top_field.number != 3:
            continue

        name = ""
        keys: list[str] = []
        values: list[Any] = []
        raw_features: list[dict[str, Any]] = []
        extent = 4096
        version = 1

        for layer_field in _fields(top_field.value):
            if layer_field.number == 1:
                name = layer_field.value.decode("utf-8", errors="replace")
            elif layer_field.number == 2:
                raw_features.append(_parse_feature(layer_field.value))
            elif layer_field.number == 3:
                keys.append(layer_field.value.decode("utf-8", errors="replace"))
            elif layer_field.number == 4:
                values.append(_parse_value(layer_field.value))
            elif layer_field.number == 5:
                extent = layer_field.value
            elif layer_field.number == 15:
                version = layer_field.value
        tile_version = version

        for raw in raw_features:
            props = {"_layer": name, "_mvt_type": GEOM_TYPES.get(raw["type"], str(raw["type"]))}
            tags = raw["tags"]
            for tag_index in range(0, len(tags) - 1, 2):
                key_index = tags[tag_index]
                value_index = tags[tag_index + 1]
                if key_index < len(keys) and value_index < len(values):
                    props[keys[key_index]] = values[value_index]
            geometry = _decode_geometry(raw["geometry"], raw["type"], zoom, tile_x, tile_y, extent)
            if geometry is None:
                continue
            features.append(
                {
                    "type": "Feature",
                    "id": raw["id"],
                    "geometry": geometry,
                    "properties": props,
                }
            )

    return {
        "type": "FeatureCollection",
        "properties": {
            "zoom": zoom,
            "tile": [tile_x, tile_y],
            "decoder": "emf-adventure",
            "version": tile_version,
        },
        "features": features,
    }
