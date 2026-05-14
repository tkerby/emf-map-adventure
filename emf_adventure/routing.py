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


PATH_LAYERS = {"path_centreline", "street_names"}
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


@dataclass
class FollowResult:
    point: Point
    distance_m: float
    narration: str


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
            path_type = str(props.get("name") or props.get("type") or "path")
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
                edge = Edge(b_id, distance, distance * OFF_PATH_WEIGHT, "link")
                reverse = Edge(a_id, distance, distance * OFF_PATH_WEIGHT, "link")
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

    def exits(self, position: Point, snap_distance_m: float = 45) -> list[tuple[str, float, str]]:
        nearby_nodes = self._nearby_nodes(position, snap_distance_m)
        if not nearby_nodes:
            return []
        exits = []
        best_by_direction: dict[str, tuple[float, float, str]] = {}
        for nearest in nearby_nodes[:8]:
            for edge in self.edges.get(nearest.node, []):
                target = self.nodes[edge.to_node]
                direction = compass_name(bearing_degrees(nearest.point, target))
                link_penalty = 80 if edge.path_type == "link" else 0
                score = nearest.distance_m + edge.length_m * 0.1 + link_penalty
                existing = best_by_direction.get(direction)
                if existing is None or score < existing[0]:
                    best_by_direction[direction] = (score, edge.length_m, edge.path_type)
        for direction, (_, length, path_type) in best_by_direction.items():
            exits.append((direction, length, path_type))
        return sorted(exits, key=lambda item: item[0])

    def describe_exits(self, position: Point, snap_distance_m: float = 45) -> str:
        nearest = self.nearest_node(position)
        if not nearest:
            return "No mapped paths in the current cache."
        if nearest.distance_m > snap_distance_m:
            return f"Nearest mapped path is {fmt_distance(nearest.distance_m)} away."
        exits = self.exits(position, snap_distance_m)
        if not exits:
            return "You are near a mapped path, but I cannot see any onward path exits."
        parts = [f"{direction} ({path_type})" for direction, _, path_type in exits]
        return "Paths lead " + ", ".join(parts) + "."

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

    def follow_path(
        self, position: Point, bearing: float, distance_m: float = 20, snap_distance_m: float = 45
    ) -> FollowResult:
        nearest = self._nearest_node_for_bearing(position, bearing, snap_distance_m)
        if nearest is None:
            nearest = self.nearest_node(position)
        if not nearest:
            return FollowResult(position, 0, "No mapped paths in the current cache.")
        if nearest.distance_m > snap_distance_m:
            return FollowResult(
                position,
                0,
                f"Nearest mapped path is {fmt_distance(nearest.distance_m)} away; move closer or use `go`.",
            )

        node = nearest.node
        previous_node: int | None = None
        remaining = distance_m
        travelled = 0.0
        current_point = nearest.point
        directions: list[tuple[str, float, str]] = []
        snap_text = ""
        if nearest.distance_m > 4:
            snap_text = f"Snapped {fmt_distance(nearest.distance_m)} to the nearest mapped path. "

        while remaining > 0.5:
            edge = self._best_follow_edge(node, bearing, previous_node)
            if edge is None:
                break
            target = self.nodes[edge.to_node]
            edge_bearing = bearing_degrees(current_point, target)
            direction = compass_name(edge_bearing)
            step_distance = min(remaining, edge.length_m)
            directions.append((direction, step_distance, edge.path_type))
            travelled += step_distance

            if step_distance >= edge.length_m:
                previous_node = node
                node = edge.to_node
                current_point = target
            else:
                ratio = step_distance / edge.length_m
                ax, ay = local_xy_m(self.origin, current_point)
                bx, by = local_xy_m(self.origin, target)
                current_point = point_from_local_xy(
                    self.origin, ax + (bx - ax) * ratio, ay + (by - ay) * ratio
                )
                remaining = 0
                break
            remaining -= step_distance

        if travelled <= 0:
            return FollowResult(
                nearest.point,
                0,
                snap_text + "No mapped path continues that way from here.",
            )

        summary = _summarise_follow(directions)
        return FollowResult(current_point, travelled, snap_text + f"Followed mapped paths {summary}.")

    def _best_follow_edge(self, node: int, bearing: float, previous_node: int | None) -> Edge | None:
        candidates = [edge for edge in self.edges.get(node, []) if edge.to_node != previous_node]
        if not candidates:
            candidates = list(self.edges.get(node, []))
        best: tuple[float, Edge] | None = None
        for edge in candidates:
            edge_bearing = bearing_degrees(self.nodes[node], self.nodes[edge.to_node])
            difference = angular_difference(bearing, edge_bearing)
            if difference > 115:
                continue
            if best is None or difference < best[0]:
                best = (difference, edge)
        return None if best is None else best[1]

    def _nearby_nodes(self, point: Point, max_distance_m: float) -> list[NearestNode]:
        nearby = []
        for index, node_point in enumerate(self.nodes):
            distance = haversine_m(point, node_point)
            if distance <= max_distance_m:
                nearby.append(NearestNode(index, node_point, distance))
        return sorted(nearby, key=lambda item: item.distance_m)

    def _nearest_node_for_bearing(
        self, point: Point, bearing: float, max_distance_m: float
    ) -> NearestNode | None:
        best: tuple[float, NearestNode] | None = None
        for candidate in self._nearby_nodes(point, max_distance_m):
            edge = self._best_follow_edge(candidate.node, bearing, None)
            if edge is None:
                continue
            edge_bearing = bearing_degrees(candidate.point, self.nodes[edge.to_node])
            score = candidate.distance_m + angular_difference(bearing, edge_bearing) * 0.35
            if best is None or score < best[0]:
                best = (score, candidate)
        return None if best is None else best[1]


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


def _summarise_follow(steps: list[tuple[str, float, str]]) -> str:
    if not steps:
        return "nowhere"
    merged: list[tuple[str, float, str]] = []
    for direction, distance, path_type in steps:
        if merged and merged[-1][0] == direction and merged[-1][2] == path_type:
            prev_direction, prev_distance, prev_type = merged[-1]
            merged[-1] = (prev_direction, prev_distance + distance, prev_type)
        else:
            merged.append((direction, distance, path_type))
    return "; ".join(
        f"{direction} on {path_type} for {fmt_distance(distance)}"
        for direction, distance, path_type in merged[:5]
    )
