from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .world import normalize


DEFAULT_ALIASES_PATH = Path("aliases.json")


@dataclass(frozen=True)
class AmbiguousQuery:
    prompt: str
    options: dict[str, str]


class AliasBook:
    def __init__(
        self,
        aliases: dict[str, str],
        ambiguous: dict[str, AmbiguousQuery],
        preferred_names: dict[str, list[str]],
    ):
        self.aliases = aliases
        self.ambiguous = ambiguous
        self.preferred_names = preferred_names

    def resolve(self, query: str) -> str:
        normalized = normalize(query)
        return self.aliases.get(normalized, query)

    def ambiguity(self, query: str) -> AmbiguousQuery | None:
        return self.ambiguous.get(normalize(query))

    def preferred(self, query: str) -> list[str]:
        return self.preferred_names.get(normalize(query), [])


def load_aliases(path: Path = DEFAULT_ALIASES_PATH) -> AliasBook:
    if not path.exists():
        return AliasBook({}, {}, {})
    data = json.loads(path.read_text(encoding="utf-8"))
    aliases = {normalize(key): value for key, value in data.get("aliases", {}).items()}
    ambiguous = {
        normalize(key): AmbiguousQuery(
            prompt=str(value.get("prompt", "")),
            options={str(k): str(v) for k, v in value.get("options", {}).items()},
        )
        for key, value in data.get("ambiguous", {}).items()
    }
    preferred_names = {
        normalize(key): [normalize(item) for item in value]
        for key, value in data.get("prefer", {}).items()
    }
    return AliasBook(aliases, ambiguous, preferred_names)
