# EMF Map Adventure

A dependency-light Python prototype for a text-adventure style navigator over the
live EMF map tiles.

The app fetches current vector tiles from `map.emfcamp.org`, decodes the map
features locally, builds a simple footpath graph from `path_centreline`, and then
lets you query what is nearby or ask for directions to named map features.

## Quick Start

Refresh the local cache from the live map:

```bash
python3 -m emf_adventure refresh
```

Start the adventure shell at the map URL position:

```bash
python3 -m emf_adventure play
```

Enable text-to-speech from startup:

```bash
python3 -m emf_adventure play --speak
```

Try commands such as:

```text
look
nearby 80
scan
speak on
repeat
find main stage
route stage a
go south
go ne 30
path north 50
follow west 30
exits
teleport site entrance
gps 52.040163 -2.376955
where
help
quit
```

The cache lives at `data/emf_map_cache.json`. Run `refresh` whenever the live
GeoJSON/vector-tile data changes.

## Movement

There are two movement styles:

```text
go north 50
path north 50
follow west 30
```

`go` moves directly by compass bearing, useful for open grass or testing GPS-like
movement. `path` and `follow` snap to the nearest mapped walkway if you are close
enough, then progress along the path graph in the requested compass direction.

Useful navigation helpers:

```text
exits
teleport main stage
teleport site entrance
```

The main site entrance is at the south of the map; `site entrance`, `main
entrance`, and `south entrance` are search aliases for it.

## 8-bit Display

Inside the shell, `scan` prints a small local display centered on your position:

```text
scan
scan 180
```

The display uses `@` for you, `.` for mapped walkway nodes, and compact symbols
for contacts such as `S` stages, `W` water points, `C` camping, `G` gates, and
`P` parking. If you set a heading, your marker changes to `^`, `>`, `v`, or `<`.

## Text To Speech

The shell can speak its printed output for accessibility:

```text
speak on
speak off
repeat
```

You can also start with speech already enabled:

```bash
python3 -m emf_adventure play --speak
```

Speech uses the first system command it can find: `say` on macOS, then
`spd-say`, `espeak`, or PowerShell speech on Windows. The app still prints all
text, and the 8-bit display is simplified before speaking so it does not read a
wall of symbols.

## Theme Voice

Year-specific language lives in [theme.json](theme.json). The current file uses
the 2026 space theme, so the shell can say things like "Welcome astronaut" while
the map, routing, and feature search code stay unchanged.

You can pass another theme file with:

```bash
python3 -m emf_adventure play --theme path/to/theme.json
```

## Aliases

Search synonyms and ambiguous terms live in [aliases.json](aliases.json). This
keeps event-specific language out of the navigation code. Examples:

- `main stage` resolves to `Stage A`.
- `pub`, `beer`, and `robot arms` resolve to `Bar`.
- `loo`, `portaloo`, `bathroom`, and `wc` resolve to toilets.
- `food` asks whether you mean food vendors or the volunteer kitchen.

You can pass another alias file with:

```bash
python3 -m emf_adventure play --aliases path/to/aliases.json
```

## Design Notes

- The runtime has no third-party Python dependencies.
- The vector-tile decoder is intentionally small and handles the geometry and
  value types used by the EMF site-plan tiles.
- Routing prefers `path_centreline` segments. The first and last hop may be
  grass/off-path so you can start or finish away from the mapped paths.
- The schedule/programme lookup is intentionally not implemented yet, but the
  command shape is ready for it: a later `programme` or `talk` command can use
  the same target lookup and route output once we wire in the API.
- The package is split so a future MicroPython badge port can reuse the cached
  JSON plus the lightweight bearing, distance, and text logic while replacing
  network fetching and the richer routing graph.
