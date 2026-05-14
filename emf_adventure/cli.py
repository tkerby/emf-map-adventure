from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import sys

from .aliases import DEFAULT_ALIASES_PATH, AliasBook, load_aliases
from .cache import DEFAULT_CACHE_PATH, load_cache, refresh_cache
from .display import render_display
from .geo import COMPASS_BEARINGS, DEFAULT_LAT, DEFAULT_LON, DEFAULT_ZOOM, Point, fmt_distance
from .routing import Router
from .speech import Speaker
from .theme import DEFAULT_THEME_PATH, Theme, load_theme
from .world import World, normalize


PROMPT = "emf> "


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "refresh":
        return command_refresh(args)
    if args.command == "play":
        return command_play(args)
    parser.print_help()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Text adventure navigation over the EMF map.")
    sub = parser.add_subparsers(dest="command")

    refresh = sub.add_parser("refresh", help="Fetch current map tiles and rebuild the local cache.")
    refresh.add_argument("--lat", type=float, default=DEFAULT_LAT)
    refresh.add_argument("--lon", type=float, default=DEFAULT_LON)
    refresh.add_argument("--radius", type=float, default=900, help="Cache radius in metres.")
    refresh.add_argument("--zoom", type=int, default=DEFAULT_ZOOM)
    refresh.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    refresh.add_argument("--keep-tiles", action="store_true")

    play = sub.add_parser("play", help="Start the interactive navigator.")
    play.add_argument("--lat", type=float, default=DEFAULT_LAT)
    play.add_argument("--lon", type=float, default=DEFAULT_LON)
    play.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    play.add_argument("--theme", type=Path, default=DEFAULT_THEME_PATH)
    play.add_argument("--aliases", type=Path, default=DEFAULT_ALIASES_PATH)
    play.add_argument("--speak", action="store_true", help="Speak shell output using the system TTS command.")
    play.add_argument("--refresh-if-missing", action="store_true")
    return parser


def command_refresh(args: argparse.Namespace) -> int:
    center = Point(args.lat, args.lon)
    cache = refresh_cache(args.cache, center, args.radius, args.zoom, keep_tiles=args.keep_tiles)
    meta = cache["metadata"]
    print(
        f"Cached {meta['feature_count']} features from {meta['tile_count']} live tiles "
        f"around {args.lat:.6f}, {args.lon:.6f}."
    )
    if meta["errors"]:
        print(f"{len(meta['errors'])} tile errors occurred; the partial cache was still written.")
    print(f"Cache: {args.cache}")
    return 0


def command_play(args: argparse.Namespace) -> int:
    if not args.cache.exists():
        if not args.refresh_if_missing:
            print(f"No cache found at {args.cache}. Run `python3 -m emf_adventure refresh` first.")
            return 2
        refresh_cache(args.cache, Point(args.lat, args.lon))

    feature_collection = load_cache(args.cache)
    theme = load_theme(args.theme)
    aliases = load_aliases(args.aliases)
    world = World(feature_collection, theme)
    router = Router(feature_collection, theme)
    speaker = Speaker(enabled=args.speak)
    state = AdventureState(Point(args.lat, args.lon), world, router, theme, speaker, aliases)
    state.output(theme.text("welcome"))
    if args.speak and not speaker.available:
        state.output("Speech requested, but I could not find a system text-to-speech command.", speak=False)
    state.output(world.describe_nearby(state.position, 60, limit=6))

    while True:
        try:
            raw = input(PROMPT)
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not raw.strip():
            continue
        try:
            should_continue = state.handle(raw)
        except ValueError as exc:
            state.output(str(exc))
            continue
        if not should_continue:
            return 0


class AdventureState:
    def __init__(
        self,
        position: Point,
        world: World,
        router: Router,
        theme: Theme,
        speaker: Speaker,
        aliases: AliasBook,
    ):
        self.position = position
        self.heading: float | None = None
        self.world = world
        self.router = router
        self.theme = theme
        self.speaker = speaker
        self.aliases = aliases

    def output(self, text: str, speak: bool = True) -> None:
        print(text)
        if speak:
            self.speaker.say(text)

    def handle(self, raw: str) -> bool:
        parts = shlex.split(raw)
        if not parts:
            return True
        command = parts[0].lower()
        rest = parts[1:]

        if command in {"quit", "exit"}:
            self.output(self.theme.text("goodbye"))
            return False
        if command == "help":
            self.output(HELP_TEXT)
        elif command == "speak":
            self.speak(rest)
        elif command == "repeat":
            self.repeat()
        elif command in {"where", "position"}:
            self.print_position()
        elif command in {"look", "l"}:
            radius = _optional_float(rest, 80)
            self.output(self.world.describe_nearby(self.position, radius))
        elif command == "nearby":
            radius = _optional_float(rest, 120)
            self.output(self.world.describe_nearby(self.position, radius, limit=18))
        elif command in {"display", "map", "scan"}:
            radius = _optional_float(rest, 120)
            display = render_display(self.position, self.world, self.router, radius, self.heading)
            self.output(display)
        elif command in {"gps", "set"}:
            self.set_gps(rest)
        elif command in {"heading", "face"}:
            self.set_heading(rest)
        elif command in {"go", "walk", "move"}:
            self.go(rest)
        elif command in {"path", "follow"}:
            self.follow_path(rest)
        elif command in {"teleport", "tp"}:
            self.teleport(" ".join(rest))
        elif command == "exits":
            self.output(self.router.describe_exits(self.position))
        elif command in {"find", "route", "goto", "ask"}:
            self.find_or_route(" ".join(rest), route=command != "find")
        else:
            maybe_bearing = COMPASS_BEARINGS.get(command)
            if maybe_bearing is None:
                self.output(f"I do not know `{command}`. Type `help` for commands.")
            else:
                distance = _optional_float(rest, 20)
                self.move(maybe_bearing, distance)
        return True

    def speak(self, args: list[str]) -> None:
        if not args:
            status = "on" if self.speaker.enabled else "off"
            command = self.speaker.command or "not found"
            self.output(f"Speech is {status}. TTS command: {command}.")
            return
        setting = args[0].lower()
        if setting in {"on", "yes", "true", "1"}:
            self.speaker.enabled = True
            if self.speaker.available:
                self.output("Speech enabled.")
            else:
                self.output("Speech enabled, but no system text-to-speech command was found.", speak=False)
        elif setting in {"off", "no", "false", "0"}:
            self.speaker.enabled = False
            self.output("Speech disabled.", speak=False)
        else:
            raise ValueError("Usage: speak on|off")

    def repeat(self) -> None:
        if not self.speaker.last_text:
            self.output("Nothing to repeat.")
            return
        self.output(self.speaker.last_text)

    def print_position(self) -> None:
        heading = "" if self.heading is None else f", heading {self.heading:.0f} degrees"
        self.output(self.theme.text("position", lat=self.position.lat, lon=self.position.lon, heading=heading))
        nearest = self.router.nearest_node(self.position)
        if nearest:
            self.output(self.theme.text("nearest_path", distance=fmt_distance(nearest.distance_m)))

    def set_gps(self, args: list[str]) -> None:
        if len(args) < 2:
            raise ValueError("Usage: gps LAT LON [HEADING_DEGREES]")
        self.position = Point(float(args[0]), float(args[1]))
        if len(args) >= 3:
            self.heading = float(args[2]) % 360
        self.print_position()

    def set_heading(self, args: list[str]) -> None:
        if not args:
            raise ValueError("Usage: heading DEGREES")
        self.heading = float(args[0]) % 360
        self.output(f"Facing {self.heading:.0f} degrees.")

    def go(self, args: list[str]) -> None:
        if not args:
            if self.heading is None:
                raise ValueError("Usage: go DIRECTION [METRES], or set `heading DEGREES` first.")
            bearing = self.heading
            distance = 20
        else:
            direction = args[0].lower()
            if direction in COMPASS_BEARINGS:
                bearing = COMPASS_BEARINGS[direction]
                distance = _optional_float(args[1:], 20)
            elif self.heading is not None:
                bearing = self.heading
                distance = float(direction)
            else:
                raise ValueError("Use one of n, ne, e, se, s, sw, w, nw.")
        self.move(bearing, distance)

    def follow_path(self, args: list[str]) -> None:
        if not args:
            if self.heading is None:
                raise ValueError("Usage: path DIRECTION [METRES], or set `heading DEGREES` first.")
            bearing = self.heading
            distance = 20
        else:
            direction = args[0].lower()
            if direction in COMPASS_BEARINGS:
                bearing = COMPASS_BEARINGS[direction]
                distance = _optional_float(args[1:], 20)
            elif self.heading is not None:
                bearing = self.heading
                distance = float(direction)
            else:
                raise ValueError("Use one of n, ne, e, se, s, sw, w, nw.")

        result = self.router.follow_path(self.position, bearing, distance)
        self.position = result.point
        self.output(result.narration)
        self.print_position()
        self.output(self.router.describe_exits(self.position))
        self.output(self.world.describe_nearby(self.position, 50, limit=5))

    def move(self, bearing: float, distance: float) -> None:
        self.position, narration = self.router.step(self.position, bearing, distance)
        self.output(narration)
        self.print_position()
        self.output(self.world.describe_nearby(self.position, 50, limit=5))

    def find_or_route(self, query: str, route: bool) -> None:
        if not query:
            raise ValueError("Tell me what to find, for example `route main stage`.")
        resolved = self.resolve_query(query)
        if resolved is None:
            return
        matches = self.sort_alias_matches(query, resolved, self.world.find(resolved))
        if not matches:
            self.output(f"I could not find `{query}` in the cached map data.")
            return
        target = matches[0]
        self.output(self.world.describe_target(self.position, target))
        if route:
            self.output(self.router.describe_route(self.position, target.point))
        if len(matches) > 1:
            others = ", ".join(match.name for match in matches[1:4])
            if others:
                self.output(f"Other possible matches: {others}.")

    def teleport(self, query: str) -> None:
        if not query:
            raise ValueError("Tell me where to teleport, for example `teleport site entrance`.")
        resolved = self.resolve_query(query)
        if resolved is None:
            return
        matches = self.sort_alias_matches(query, resolved, self.world.find(resolved))
        if not matches:
            self.output(f"I could not find `{query}` in the cached map data.")
            return
        target = matches[0]
        self.position = target.point
        self.output(f"Teleported to {target.name}.")
        self.print_position()
        self.output(self.world.describe_nearby(self.position, 60, limit=8))
        self.output(self.router.describe_exits(self.position))

    def resolve_query(self, query: str) -> str | None:
        ambiguous = self.aliases.ambiguity(query)
        if ambiguous:
            self.output(ambiguous.prompt)
            return None
        return self.aliases.resolve(query)

    def sort_alias_matches(self, original_query: str, resolved_query: str, matches):
        preferred = self.aliases.preferred(original_query) or self.aliases.preferred(resolved_query)
        if not preferred:
            return matches
        rank = {name: index for index, name in enumerate(preferred)}
        return sorted(matches, key=lambda landmark: rank.get(normalize(landmark.name), len(rank)))


def _optional_float(args: list[str], default: float) -> float:
    if not args:
        return default
    return float(args[0])


HELP_TEXT = """Commands:
  look [metres]           Describe named things around you.
  nearby [metres]         Wider nearby search.
  scan [metres]           Show an 8-bit local display.
  speak on|off            Toggle text-to-speech output.
  repeat                  Repeat the last spoken output.
  find NAME               Say where a feature is.
  route NAME              Give direct bearing and path-aware route summary.
  gps LAT LON [HEADING]   Set your GPS-like position.
  heading DEGREES         Set the direction you are facing.
  go DIRECTION [METRES]   Move with n/ne/e/se/s/sw/w/nw.
  go [METRES]             Move along your current heading.
  path DIRECTION [METRES] Follow mapped paths in a compass direction.
  follow DIRECTION [M]    Alias for path.
  exits                   List mapped path directions from nearby.
  teleport NAME           Jump directly to a named map feature.
  where                   Show current coordinates.
  quit                    Leave the shell.
"""


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
