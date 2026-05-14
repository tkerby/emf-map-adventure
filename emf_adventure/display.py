from __future__ import annotations

from dataclasses import dataclass

from .geo import Point, fmt_distance, haversine_m, local_xy_m
from .routing import Router
from .world import Landmark, World


DEFAULT_WIDTH = 39
DEFAULT_HEIGHT = 19


@dataclass(frozen=True)
class DisplayItem:
    symbol: str
    label: str
    distance_m: float


def render_display(
    position: Point,
    world: World,
    router: Router,
    radius_m: float = 120,
    heading: float | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> str:
    width = _force_odd(max(15, min(width, 79)))
    height = _force_odd(max(9, min(height, 39)))
    grid = [[" " for _ in range(width)] for _ in range(height)]
    center_col = width // 2
    center_row = height // 2

    for node in router.nodes:
        plotted = _plot_point(position, node, radius_m, width, height)
        if plotted is None:
            continue
        row, col = plotted
        if grid[row][col] == " ":
            grid[row][col] = "."

    legend: list[DisplayItem] = []
    for distance, landmark in world.nearby(position, radius_m, limit=24):
        plotted = _plot_point(position, landmark.point, radius_m, width, height)
        if plotted is None:
            continue
        symbol = _symbol_for(landmark)
        row, col = plotted
        if row == center_row and col == center_col:
            continue
        grid[row][col] = symbol
        if not any(item.symbol == symbol and item.label == landmark.name for item in legend):
            legend.append(DisplayItem(symbol, landmark.name, distance))

    grid[center_row][center_col] = _you_symbol(heading)
    border = "+" + "-" * width + "+"
    lines = [
        "8-bit local scanner",
        f"range {fmt_distance(radius_m)} | north is up | @ is you | . walkway",
        border,
    ]
    lines.extend("|" + "".join(row) + "|" for row in grid)
    lines.append(border)
    if legend:
        lines.append("contacts:")
        for item in sorted(legend, key=lambda entry: entry.distance_m)[:8]:
            lines.append(f" {item.symbol} {item.label} ({fmt_distance(item.distance_m)})")
    else:
        lines.append("contacts: none")
    return "\n".join(lines)


def _plot_point(
    origin: Point, point: Point, radius_m: float, width: int, height: int
) -> tuple[int, int] | None:
    if haversine_m(origin, point) > radius_m:
        return None
    x, y = local_xy_m(origin, point)
    half_width = width // 2
    half_height = height // 2
    col = round((x / radius_m) * half_width) + half_width
    row = half_height - round((y / radius_m) * half_height)
    if row < 0 or row >= height or col < 0 or col >= width:
        return None
    return row, col


def _symbol_for(landmark: Landmark) -> str:
    layer = landmark.layer
    name = landmark.name.lower()
    if "stage" in name:
        return "S"
    if layer == "water_points":
        return "W"
    if layer == "areas_camping":
        return "C"
    if layer == "gates":
        return "G"
    if layer == "parking":
        return "P"
    if "toilet" in name:
        return "T"
    if layer == "street_names":
        return "="
    return "*"


def _you_symbol(heading: float | None) -> str:
    if heading is None:
        return "@"
    heading = heading % 360
    if heading < 45 or heading >= 315:
        return "^"
    if heading < 135:
        return ">"
    if heading < 225:
        return "v"
    return "<"


def _force_odd(value: int) -> int:
    return value if value % 2 else value + 1

