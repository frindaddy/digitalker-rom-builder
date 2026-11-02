"""Manifest files: which audio goes to which message index.

    # comment
    rom_size: 16K             option lines are `name: value` with a non-numeric name
    quality: normal
    1: hello.wav              message lines are `index: item item ... [| label]`
    2: "my file.mp3"
    3: hello.wav sil40 world.mp3 | hello world
    4: alarm.wav[gain_db=3,voiced_repeats=1]

Items are audio paths (relative to the manifest) or silences `silN` (N ms).
Per-clip encoder overrides go in brackets directly after the path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from pathlib import Path

from .encoder import QUALITY, Settings

OPTION_KEYS = {"rom_size", "quality", "auto_fit"} | {f.name for f in fields(Settings)}
TOKEN = re.compile(r'"[^"]*"(?:\[[^\]]*\])?|\S+')
SILENCE = re.compile(r"sil(\d+)$", re.IGNORECASE)
COMMENT = re.compile(r"(^|\s)#.*")


class ManifestError(Exception):
    pass


@dataclass(frozen=True)
class Clip:
    path: Path
    overrides: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True)
class Silence:
    ms: int


@dataclass
class Message:
    index: int
    items: list[Clip | Silence]
    label: str


@dataclass
class Manifest:
    messages: dict[int, Message]
    options: dict[str, object] = field(default_factory=dict)
    base: Path = Path(".")

    @property
    def rom_size(self) -> str:
        return str(self.options.get("rom_size", "16K")).upper()

    @property
    def quality(self) -> str:
        return str(self.options.get("quality", "normal")).lower()

    def settings(self, quality: str | None = None, overrides=()) -> Settings:
        base = QUALITY[quality or self.quality]
        values = vars(base) | {k: v for k, v in self.options.items() if k in vars(base)}
        values |= dict(overrides)
        return Settings(**values)


def _convert(key: str, value: str) -> object:
    if key in ("rom_size", "quality"):
        return value
    default = getattr(Settings(), key, None)
    if key == "auto_fit" or isinstance(default, bool):
        if value.lower() in ("1", "true", "yes", "on"):
            return True
        if value.lower() in ("0", "false", "no", "off"):
            return False
        raise ManifestError(f"{key}: expected true/false, got {value!r}")
    try:
        return type(default)(value) if default is not None else int(value)
    except ValueError:
        raise ManifestError(f"{key}: bad value {value!r}") from None


def _parse_overrides(text: str) -> tuple[tuple[str, object], ...]:
    out = []
    for part in filter(None, (p.strip() for p in text.split(","))):
        if "=" not in part:
            raise ManifestError(f"bad clip option {part!r}")
        k, v = (s.strip() for s in part.split("=", 1))
        if k not in {f.name for f in fields(Settings)}:
            raise ManifestError(f"unknown clip option {k!r}")
        out.append((k, _convert(k, v)))
    return tuple(out)


def parse(text: str, base: Path = Path(".")) -> Manifest:
    messages: dict[int, Message] = {}
    options: dict[str, object] = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = COMMENT.sub("", raw).strip()
        if not line:
            continue
        key, sep, rest = line.partition(":")
        if not sep:
            raise ManifestError(f"line {lineno}: expected 'index: items' or 'option: value'")
        key, rest = key.strip(), rest.strip()
        try:
            if not key.isdigit():
                if key not in OPTION_KEYS:
                    raise ManifestError(f"unknown option {key!r}")
                options[key] = _convert(key, rest)
                continue
            index = int(key)
            if not 0 <= index <= 255:
                raise ManifestError("message index must be 0-255")
            if index in messages:
                raise ManifestError(f"message {index} defined twice")
            body, _, label = rest.partition("|")
            items: list[Clip | Silence] = []
            for tok in TOKEN.findall(body):
                if m := SILENCE.match(tok):
                    items.append(Silence(int(m.group(1))))
                    continue
                opts = ""
                if tok.endswith("]") and "[" in tok:
                    tok, opts = tok[:-1].split("[", 1)
                path = Path(tok.strip('"'))
                items.append(Clip(path if path.is_absolute() else base / path, _parse_overrides(opts)))
            if not items:
                raise ManifestError("no items")
            label = label.strip() or " ".join(
                i.path.stem if isinstance(i, Clip) else f"sil{i.ms}" for i in items)
            messages[index] = Message(index, items, label)
        except ManifestError as e:
            raise ManifestError(f"line {lineno}: {e}") from None
    if not messages:
        raise ManifestError("manifest defines no messages")
    return Manifest(messages, options, base)


def load(path: str | Path) -> Manifest:
    path = Path(path)
    return parse(path.read_text(encoding="utf-8"), path.parent)
