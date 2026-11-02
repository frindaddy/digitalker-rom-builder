"""dkrom command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import build, decoder, encoder, manifest, rombuild

FIELD_HELP = {
    "voiced_repeats": "times each voiced waveform plays (1-8)",
    "unvoiced_repeats": "times each set of noise blocks plays (1-8)",
    "unvoiced_blocks": "distinct noise blocks per instruction (1-16)",
    "unvoiced_pitch": "noise sample period code (0-31; 8 = 13 kHz, 23 = 20 kHz)",
    "silence_db": "level below the loudest frame treated as silence",
    "gain_db": "extra gain applied before encoding",
}


def _load_rom(paths: list[Path]) -> bytes:
    rom = b"".join(p.read_bytes() for p in paths)
    if len(rom) > 0x4000:
        sys.exit("error: ROM images larger than 16 KiB must be split into 16 KiB banks")
    return rom


def _labels(path: Path | None) -> dict[int, str]:
    if not path:
        return {}
    return {int(k): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_.'" else "_" for c in name)


def cmd_build(a) -> None:
    man = manifest.load(a.manifest)
    res = build.Builder(man, log=print).build(a.quality, False if a.no_auto_fit else None)
    out = a.output or Path(a.manifest).with_suffix(".bin")
    for p in build.write_outputs(res, out, man.rom_size):
        print(f"wrote {p}")
    print(f"quality: {res.quality}")
    print(res.layout.summary())
    if a.wav_dir:
        _render_all(res.layout.image, res.labels, a.wav_dir, a.filter, sorted(res.labels))


def _render_all(rom: bytes, labels: dict[int, str], out: Path, analog: bool, only=None) -> None:
    import soundfile as sf
    out.mkdir(parents=True, exist_ok=True)
    count = decoder.message_count(rom)
    for i in only if only is not None else range(count):
        name = f"{i}_{_safe(labels[i])}" if i in labels else str(i)
        try:
            y = decoder.render_message(rom, i, analog_filter=analog)
        except ValueError as e:
            print(f"  {i}: {e}")
            continue
        sf.write(out / f"{name}.wav", y, 48000)
    print(f"rendered {len(only) if only is not None else count} message(s) to {out}")


def cmd_decode(a) -> None:
    rom = _load_rom(a.rom)
    _render_all(rom, _labels(a.dictionary), a.output, a.filter, a.index or None)


def cmd_inspect(a) -> None:
    rom = _load_rom(a.rom)
    labels = _labels(a.dictionary)
    count = decoder.message_count(rom)
    end = 2 * count
    data_end = 0
    print(f"{count} messages")
    for i in range(count):
        try:
            prog = decoder.parse_message(rom, i)
        except ValueError as e:
            print(f"{i:3}: {e}")
            continue
        frames = decoder.message_frames(rom, i)
        ms = sum(f.duration for f in frames[:-1]) / 1000
        end = max(end, decoder.pointer(rom, i) + 3 * len(prog))
        data_end = max([data_end] + [ins.addr + ins.data_size() for ins in prog])
        print(f"{i:3}: {ms:7.1f} ms  {len(prog):3} instructions  {labels.get(i, '')}")
        if a.verbose:
            for ins in prog:
                kind = "silence" if ins.is_silence else f"mode {ins.mode} @ {ins.addr:04x}"
                print(f"       {kind:16} S={ins.count:2} R={ins.repeats}{'  end' if ins.last else ''}")
    used = max(end, data_end)
    print(f"instructions end at {end:#06x}, speech data ends at {data_end:#06x}; "
          f"{len(rom) - used} bytes free of {len(rom)}")


def cmd_preview(a) -> None:
    import soundfile as sf
    st = encoder.QUALITY[a.quality]
    over = {k: getattr(a, k) for k in FIELD_HELP if getattr(a, k) is not None}
    st = encoder.Settings(**(vars(st) | over | {"allow_even": a.allow_even or st.allow_even}))
    ops = encoder.finish(encoder.encode_file(a.audio, st))
    lay = rombuild.layout({0: ops}, rombuild.ROM_SIZES["16K"])
    y = decoder.render_message(lay.image, 0, analog_filter=a.filter)
    out = a.output or Path(a.audio).with_name(Path(a.audio).stem + "_digitalker.wav")
    sf.write(out, y, 48000)
    print(f"wrote {out}: {len(y) / 48000:.2f} s, {lay.used - 2} bytes in {len(ops)} instructions")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="dkrom", description="Build MM54104 Digitalker speech ROMs from audio.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build a ROM from a manifest")
    b.add_argument("manifest", type=Path)
    b.add_argument("-o", "--output", type=Path, help="output .bin (default: next to the manifest)")
    b.add_argument("-q", "--quality", choices=list(encoder.QUALITY))
    b.add_argument("--no-auto-fit", action="store_true", help="fail instead of lowering quality")
    b.add_argument("--wav-dir", type=Path, help="also render every message to WAVs here")
    b.add_argument("--filter", action="store_true", help="apply the DT1050 output filter to WAVs")
    b.set_defaults(func=cmd_build)

    d = sub.add_parser("decode", help="render ROM messages to WAV files")
    d.add_argument("rom", type=Path, nargs="+", help="ROM image (two files for an 8K+8K pair)")
    d.add_argument("-o", "--output", type=Path, required=True)
    d.add_argument("--dictionary", type=Path, help="digichiver dictionary JSON for file names")
    d.add_argument("--index", type=int, action="append", help="only this message (repeatable)")
    d.add_argument("--filter", action="store_true", help="apply the DT1050 output filter")
    d.set_defaults(func=cmd_decode)

    i = sub.add_parser("inspect", help="list a ROM's messages and space usage")
    i.add_argument("rom", type=Path, nargs="+")
    i.add_argument("--dictionary", type=Path)
    i.add_argument("-v", "--verbose", action="store_true", help="show every instruction")
    i.set_defaults(func=cmd_inspect)

    p = sub.add_parser("preview", help="encode one clip and write how the chip would say it")
    p.add_argument("audio", type=Path)
    p.add_argument("-o", "--output", type=Path)
    p.add_argument("-q", "--quality", choices=list(encoder.QUALITY), default="normal")
    p.add_argument("--filter", action="store_true", help="apply the DT1050 output filter")
    p.add_argument("--allow-even", action="store_true", help="use mode 2 for voices above ~200 Hz")
    for name, help_ in FIELD_HELP.items():
        kind = float if name.endswith("_db") else int
        p.add_argument("--" + name.replace("_", "-"), type=kind, help=help_)
    p.set_defaults(func=cmd_preview)

    a = ap.parse_args(argv)
    try:
        a.func(a)
    except (manifest.ManifestError, build.FitError, rombuild.RomFullError, ValueError, OSError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
