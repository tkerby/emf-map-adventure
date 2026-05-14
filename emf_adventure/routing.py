from __future__ import annotations

from dataclasses import dataclass
import heapq
import math
from typing import Any

from .geo import (
    Point,
    angular_difference,
    bearing_degrees,
    compass_name,
    destination_point,
    fmt_distance,
    haversine_m,
    local_xy_m,
    point_from_local_xy,
)
from .theme import Theme, load_theme


PATH_LAYERS = {"path_centreline"}
PATH_TYPES = {"trackway": 1.0, "grass": 1.18, None: 1.08}
OFF_PATH_WEIGHT = 1.5


@dataclass
class Edge:
    to_node: int
    length_m: float
    weight: float
    path_type: str


@dataclass
class NearestNode:
    node: int
    point: Point
    distance_m: float


class Router:
    def __init__(self, feature_collection: dict[str, Any], theme: Theme | None = None):
        self.origin = self._pick_origin(feature_collection)
        self.theme = theme or load_theme()
        self.nodes: list[Point] = []
        self.edges: dict[int, list[Edge]] = {}
        self._node_index: dict[tuple[int, int], int] = {}
        self._build_graph(feature_collection.get("features", []))
        self._stitch_nearby_nodes()

    @property
    def is_empty(self) -> bool:
        return not self.nodes

    def _pick_origin(self, feature_collection: dict[str, Any]) -> Point:
        metadata = feature_collection.get("metadata", {})
        center = metadata.get("center", {})
        if "lat" in center and "lon" in center:
            return Point(float(center["lat"]), float(center["lon"]))
        for feature in feature_collection.get("features", []):
            point = _first_point(feature.get("geometry", {}).get("coordinates"))
            if point:
                return point
        return Point(52.040163, -2.376955)

    def _node_for(self, point: Point) -> int:
        x, y = local_xy_m(self.origin, point)
        key = (round(x / 2), round(y / 2))
        if key in self._node_index:
            return self._node_index[key]
        node_id = len(self.nodes)
        self._node_index[key] = node_id
        self.nodes.append(point)
        self.edges[node_id] = []
        return node_id

    def _build_graph(self, features: list[dict[str, Any]]) -> None:
        for feature in features:
            props = feature.get("properties", {})
            if props.get("_layer") not in PATH_LAYERS:
                continue
            geom = feature.get("geometry", {})
            path_type = str(props.get("type") or "path")
            lines = _geometry_lines(geom)
            for line in lines:
                for a, b in zip(line, line[1:]):
                    length = haversine_m(a, b)
                    if length <= 0.4:
                        continue
                    a_id = self._node_for(a)
                    b_id = self._node_for(b)
                    multiplier = PATH_TYPES.get(props.get("type"), 1.12)
                    edge = Edge(b_id, length, length * multiplier, path_type)
                    reverse = Edge(a_id, length, length * multiplier, path_type)
                    self.edges[a_id].append(edge)
                    self.edges[b_id].append(reverse)

    def _stitch_nearby_nodes(self, max_gap_m: float = 25) -> None:
        for a_id, a in enumerate(self.nodes):
            for b_id in range(a_id + 1, len(self.nodes)):
                b = self.nodes[b_id]
                distance = haversine_m(a, b)
                if distance <= 0.5 or distance > max_gap_m:
                    continue
                edge = Edge(b_id, distance, distance * OFF_PATH_WEIGHT, "connector")
                reverse = Edge(a_id, distance, distance * OFF_PATH_WEIGHT, "connector")
                self.edges[a_id].append(edge)
                self.edges[b_id].append(reverse)

    def nearest_node(self, point: Point) -> NearestNode | None:
        best: NearestNode | None = None
        for index, node_point in enumerate(self.nodes):
            distance = haversine_m(point, node_point)
            if best is None or distance < best.distance_m:
                best = NearestNode(index, node_point, distance)
        return best

    def route(self, start: Point, goal: Point) -> dict[str, Any] | None:
        if self.is_empty:
            return None
        start_node = self.nearest_node(start)
        goal_node = self.nearest_node(goal)
        if not start_node or not goal_node:
            return None
        graph_result = self._dijkstra(start_node.node, goal_node.node)
        if graph_result is None:
            return None
        graph_distance, node_path = graph_result
        route_points = [start] + [self.nodes[node] for node in node_path] + [goal]
        direct_distance = haversine_m(start, goal)
        total_distance = start_node.distance_m + graph_distance + goal_node.distance_m
        return {
            "points": route_points,
            "distance_m": total_distance,
            "direct_m": direct_distance,
            "off_path_m": start_node.distance_m + goal_node.distance_m,
            "node_path": node_path,
        }

    def _dijkstra(self, start_node: int, goal_node: int) -> tuple[float, list[int]] | None:
        queue: list[tuple[float, int]] = [(0.0, start_node)]
        distances = {start_node: 0.0}
        previous: dict[int, int] = {}
        while queue:
            current_distance, node = heapq.heappop(queue)
            if node == goal_node:
                path = [node]
                while node in previous:
                    node = previous[node]
                    path.append(node)
                path.reverse()
                return current_distance, path
            if current_distance > distances.get(node, math.inf):
                continue
            for edge in self.edges.get(node, []):
                next_distance = current_distance + edge.weight
                if next_distance < distances.get(edge.to_node, math.inf):
                    distances[edge.to_node] = next_distance
                    previous[edge.to_node] = node
                    heapq.heappush(queue, (next_distance, edge.to_node))
        return None

    def describe_route(self, start: Point, goal: Point) -> str:
        route = self.route(start, goal)
        direct_bearing = bearing_degrees(start, goal)
        direct = self.theme.text(
            "direct_bearing",
            direction=compass_name(direct_bearing),
            distance=fmt_distance(haversine_m(start, goal)),
        )
        if not route:
            return direct + self.theme.text("route_missing")
        legs = _summarise_legs(route["points"])
        leg_text = "; ".join(
            f"{direction} for {fmt_distance(distance)}" for direction, distance in legs[:4] if distance >= 4
        )
        if not leg_text:
            leg_text = f"{compass_name(direct_bearing)} for {fmt_distance(route['distance_m'])}"
        off_path = route["off_path_m"]
        grass_note = ""
        if off_path > 8:
            grass_note = self.theme.text("off_path_note", distance=fmt_distance(off_path))
        return direct + " " + self.theme.text(
            "route_summary",
            distance=fmt_distance(route["distance_m"]),
            legs=leg_text,
            off_path=grass_note,
        )

    def step(self, position: Point, bearing: float, distance_m: float = 20) -> tuple[Point, str]:
        nearest = self.nearest_node(position)
        if not nearest or nearest.distance_m > 18:
            return destination_point(position, bearing, distance_m), self.theme.text("move_open")

        best: tuple[float, Edge] | None = None
        for edge in self.edges.get(nearest.node, []):
            edge_bearing = bearing_degrees(nearest.point, self.nodes[edge.to_node])
            difference = angular_difference(bearing, edge_bearing)
            if difference <= 75 and (best is None or difference < best[0]):
                best = (difference, edge)
        if not best:
            return destination_point(position, bearing, distance_m), self.theme.text("move_no_path")

        edge = best[1]
        target = self.nodes[edge.to_node]
        if edge.length_m <= distance_m:
            return target, self.theme.text(
                "move_path",
                path_type=edge.path_type,
                direction=compass_name(bearing_degrees(nearest.point, target)),
            )
        ratio = distance_m / edge.length_m
        ax, ay = local_xy_m(self.origin, nearest.point)
        bx, by = local_xy_m(self.origin, target)
        point = point_from_local_xy(self.origin, ax + (bx - ax) * ratio, ay + (by - ay) * ratio)
        return point, self.theme.text(
            "move_path",
            path_type=edge.path_type,
            direction=compass_name(bearing_degrees(nearest.point, target)),
        )


def _first_point(coords: Any) -> Point | None:
    if not isinstance(coords, list) or not coords:
        return None
    first = coords[0]
    if isinstance(first, (int, float)) and len(coords) >= 2:
        return Point(float(coords[1]), float(coords[0]))
    for item in coords:
        point = _first_point(item)
        if point:
            return point
    return None


def _geometry_lines(geom: dict[str, Any]) -> list[list[Point]]:
    geom_type = geom.get("type")
    coords = geom.get("coordinates", [])
    if geom_type == "LineString":
        return [_line_points(coords)]
    if geom_type == "MultiLineString":
        return [_line_points(line) for line in coords]
    return []


def _line_points(coords: Any) -> list[Point]:
    points = []
    for item in coords:
        if isinstance(item, list) and len(item) >= 2:
            points.append(Point(float(item[1]), float(item[0])))
    return points


def _summarise_legs(points: list[Point]) -> list[tuple[str, float]]:
    summaries: list[tuple[str, float]] = []
    current_direction = ""
    current_distance = 0.0
    for a, b in zip(points, points[1:]):
        distance = haversine_m(a, b)
        if distance < 1:
            continue
        direction = compass_name(bearing_degrees(a, b))
        if direction == current_direction:
            current_distance += distance
        else:
            if current_direction:
                summaries.append((current_direction, current_distance))
            current_direction = direction
            current_distance = distance
    if current_direction:
        summaries.append((current_direction, current_distance))
    return summaries
