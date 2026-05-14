from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


DEFAULT_THEME_PATH = Path("theme.json")

DEFAULT_PHRASES = {
    "welcome": "EMF Map Adventure. Type `help` for commands.",
    "nearby_header": "Within {radius}:",
    "nearby_empty": "Nothing named within {radius}. Try `nearby 150`.",
    "nearby_item": "- {name}: {direction}, {distance} ({layer})",
    "target_direction": "{name} is {direction} of you, approx {distance} away.",
    "direct_bearing": "Direct bearing: {direction}, {distance}.",
    "route_summary": "Walking route: approx {distance}; {legs}.{off_path}",
    "route_missing": " I do not have a connected path route in the current cache.",
    "off_path_note": " Includes approx {distance} off-path/grass at the ends.",
    "move_open": "You cross grass.",
    "move_no_path": "No path points that way; you cross grass.",
    "move_path": "You follow a {path_type} path {direction}.",
    "position": "You are at {lat:.6f}, {lon:.6f}{heading}.",
    "nearest_path": "Nearest mapped path node: {distance} away.",
    "goodbye": "Bye.",
}


@dataclass(frozen=True)
class Theme:
    name: str
    phrases: dict[str, str]

    def text(self, key: str, **values: Any) -> str:
        template = self.phrases.get(key, DEFAULT_PHRASES[key])
        return template.format(**values)


def load_theme(path: Path = DEFAULT_THEME_PATH) -> Theme:
    if not path.exists():
        return Theme("default", dict(DEFAULT_PHRASES))
    data = json.loads(path.read_text(encoding="utf-8"))
    phrases = dict(DEFAULT_PHRASES)
    phrases.update(data.get("phrases", {}))
    return Theme(str(data.get("name", path.stem)), phrases)

