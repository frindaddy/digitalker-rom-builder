"""Manifest -> ROM image, stepping down quality until everything fits."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import encoder, rombuild
from .encoder import QUALITY, Op
from .manifest import Clip, Manifest, Silence

LADDER = list(QUALITY)                    # best first


class FitError(Exception):
    pass


@dataclass
class Result:
    layout: rombuild.Layout
    quality: str
    message_bytes: dict[int, int]         # instruction + speech bytes before sharing
    labels: dict[int, str]

    def dictionary(self) -> dict[str, str]:
        """digichiver-style {"<index>": "<label>"} word list."""
        return {str(i): self.labels[i] for i in sorted(self.labels)}


class Builder:
    def __init__(self, man: Manifest, log=lambda msg: None):
        self.man = man
        self.log = log
        self._cache: dict[tuple, list[Op]] = {}

    def clip_ops(self, clip: Clip, quality: str) -> list[Op]:
        key = (clip.path, clip.overrides, quality)
        if key not in self._cache:
            self.log(f"  encoding {clip.path.name} ({quality})")
            self._cache[key] = encoder.encode_file(clip.path, self.man.settings(quality, clip.overrides))
        return self._cache[key]

    def messages(self, quality: str) -> dict[int, list[Op]]:
        out = {}
        for idx, msg in self.man.messages.items():
            ops: list[Op] = []
            for item in msg.items:
                if isinstance(item, Silence):
                    ops += encoder.encode_silence(item.ms / 1000)
                else:
                    ops += self.clip_ops(item, quality)
            if not ops:                       # e.g. a lone sil0
                ops = encoder.encode_silence(0.005)
            out[idx] = encoder.finish(ops)
        return out

    def build(self, quality: str | None = None, auto_fit: bool | None = None) -> Result:
        size = rombuild.ROM_SIZES.get(self.man.rom_size)
        if size is None:
            raise ValueError(f"rom_size must be one of {', '.join(rombuild.ROM_SIZES)}")
        start = quality or self.man.quality
        if start not in QUALITY:
            raise ValueError(f"quality must be one of {', '.join(QUALITY)}")
        if auto_fit is None:
            auto_fit = bool(self.man.options.get("auto_fit", True))
        tries = LADDER[LADDER.index(start):] if auto_fit else [start]
        err = None
        for q in tries:
            msgs = self.messages(q)
            try:
                lay = rombuild.layout(msgs, size)
            except rombuild.RomFullError as e:
                self.log(f"  {q}: {e}")
                err = e
                continue
            sizes = {i: sum(3 + len(op.blob or b"") for op in ops) for i, ops in msgs.items()}
            labels = {i: m.label for i, m in self.man.messages.items()}
            return Result(lay, q, sizes, labels)
        breakdown = "\n".join(
            f"  {i:3}: {sum(3 + len(op.blob or b'') for op in ops):6} bytes  {self.man.messages[i].label}"
            for i, ops in sorted(msgs.items()))
        raise FitError(f"{err} at quality '{tries[-1]}'; per-message sizes:\n{breakdown}")


def write_outputs(res: Result, out: Path, rom_size: str) -> list[Path]:
    """Write the image (or two 8K halves for SPLIT8K) and the dictionary JSON."""
    out.parent.mkdir(parents=True, exist_ok=True)
    written = []
    if rom_size == "SPLIT8K":
        for half, name in ((0, "_lo"), (1, "_hi")):
            p = out.with_name(out.stem + name + out.suffix)
            p.write_bytes(res.layout.image[half * 8192:(half + 1) * 8192])
            written.append(p)
    else:
        out.write_bytes(res.layout.image)
        written.append(out)
    d = out.with_name(out.stem + "_dictionary.json")
    d.write_text(json.dumps(res.dictionary(), indent=4) + "\n", encoding="utf-8")
    written.append(d)
    return written
