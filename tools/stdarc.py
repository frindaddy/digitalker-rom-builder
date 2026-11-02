"""Parse the DVSS STDARC archive (National's own Digitalker encodings).

STDARC.IDX: 24-byte records, 21 per 512-byte block after an 8-byte block header:
    u16le offset_lo, char name[14], char qualifier[3], char "ose"[3], u16le offset_hi
  The file also contains stale duplicate blocks; the first record that parses wins.

STDARC.DAT: at each offset:
    u16le length, u16 0, then `length` bytes of:
      5-byte entries until one has the end bit:  v1, v3 (mode bits), u16be blob id, n
      the complete blob (n+1 waveforms) for each non-silence entry, in entry order;
      an entry may play only the first S of them, and shared blobs are stored again

Usage:
    python tools/stdarc.py ARCHIVE_DIR [--stats] [--rom OUT.bin NAME...] [--wav OUTDIR]
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dkrom import codec, decoder  # noqa: E402
from dkrom.codec import Instruction  # noqa: E402

RECORD = re.compile(rb"(?s)(..)([ -~]{14})([ -~]{3})ose(..)")


@dataclass
class Entry:
    ins: Instruction          # addr is 0 for silence, 1 otherwise (placeholder)
    blob: int
    flag: int


@dataclass
class Word:
    name: str
    entries: list[Entry]
    blobs: dict[int, bytes]


def read_index(path: Path, dat_size: int) -> dict[str, list[int]]:
    """Candidate DAT offsets per name, in file order (stale blocks add duplicates)."""
    data = path.read_bytes()
    out: dict[str, list[int]] = {}
    for blk in range(1, len(data) // 512):
        for i in range(21):
            rec = data[blk * 512 + 8 + 24 * i:blk * 512 + 32 + 24 * i]
            m = RECORD.fullmatch(rec)
            if not m:
                continue
            off = struct.unpack("<H", m.group(4))[0] << 16 | struct.unpack("<H", m.group(1))[0]
            if off >= dat_size:
                continue
            name = m.group(2).decode().strip()
            qual = m.group(3).decode().strip()
            out.setdefault(f"{name}.{qual}" if qual else name, []).append(off)
    return out


def read_word(dat: bytes, name: str, off: int) -> Word:
    length = struct.unpack("<H", dat[off:off + 2])[0]
    body = dat[off + 4:off + 4 + length]
    entries = []
    pos = 0
    while True:
        if pos + 5 > len(body):
            raise ValueError("no end entry")
        v1, v3, hi, lo, flag = body[pos:pos + 5]
        pos += 5
        blob = hi << 8 | lo
        ins = Instruction.unpack(bytes((v1, 0 if blob == 0 else 1, v3 & 0xC0)))
        entries.append(Entry(ins, blob, flag))
        if v1 & 0x80:
            break
    blobs: dict[int, bytes] = {}
    for e in entries:
        if e.ins.is_silence:
            continue
        full = Instruction(1, e.ins.mode, e.flag + 1)
        size = full.data_size()
        blobs[e.blob] = body[pos:pos + size]
        pos += size
    if pos != length:
        raise ValueError(f"{name}: parsed {pos} of {length} bytes")
    return Word(name, entries, blobs)


def load(archive: Path) -> dict[str, Word]:
    dat = (archive / "STDARC.DAT").read_bytes()
    index = read_index(archive / "STDARC.IDX", len(dat))
    words = {}
    for name, offsets in index.items():
        for off in offsets:
            try:
                words[name] = read_word(dat, name, off)
                break
            except (ValueError, KeyError):
                continue
        else:
            print(f"skipping {name}: no valid record", file=sys.stderr)
    return words


def build_rom(words: list[Word]) -> bytes:
    """Minimal ROM with one message per word (message i = words[i])."""
    table = 2 * len(words)
    ins_size = sum(3 * len(w.entries) for w in words)
    data_pos = table + ins_size
    rom = bytearray(table)
    blob_addr: dict[bytes, int] = {}
    data = bytearray()
    progs = []
    for w in words:
        prog = bytearray()
        for e in w.entries:
            ins = e.ins
            if not ins.is_silence:
                blob = w.blobs[e.blob]
                if blob not in blob_addr:
                    blob_addr[blob] = data_pos + len(data)
                    data += blob
                ins = Instruction(blob_addr[blob], ins.mode, ins.count, ins.repeats, ins.last)
            prog += ins.pack()
        progs.append(prog)
    pos = table
    for i, prog in enumerate(progs):
        rom[2 * i:2 * i + 2] = pos.to_bytes(2, "big")
        rom += prog
        pos += len(prog)
    rom += data
    return bytes(rom)


def stats(words: dict[str, Word]) -> None:
    modes, reps, counts, vols, pitches, flags = (Counter() for _ in range(6))
    slides = Counter()
    for w in words.values():
        for e in w.entries:
            flags[e.flag] += 1
            if e.ins.is_silence:
                modes["sil"] += 1
                continue
            modes[e.ins.mode] += 1
            reps[(e.ins.mode, e.ins.repeats)] += 1
            counts[(e.ins.mode, e.ins.count)] += 1
            blob = w.blobs[e.blob]
            vols[(e.ins.mode, blob[0] >> 5)] += 1
            if e.ins.mode == codec.MODE_UNVOICED:
                pitches[(3, blob[0] & 0x1F)] += 1
            else:
                for s in range(e.ins.count):
                    h = blob[9 * s]
                    if s == 0:
                        pitches[(e.ins.mode, h & 0x1F)] += 1
                    else:
                        slides[("-" if h & 0x10 else "+") + str(h & 0xF)] += 1
    print("words", len(words))
    for label, c in (("modes", modes), ("repeats (mode, R)", reps), ("counts (mode, S)", counts),
                     ("volumes (mode, V)", vols), ("pitch (mode, P)", pitches),
                     ("slides", slides), ("entry flag", flags)):
        print(f"\n{label}:")
        for k, v in sorted(c.items(), key=lambda kv: str(kv[0])):
            print(f"  {k}: {v}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("archive", type=Path)
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--rom", type=Path)
    ap.add_argument("--wav", type=Path, help="decode every word to OUTDIR/<name>.wav")
    ap.add_argument("names", nargs="*")
    a = ap.parse_args()
    words = load(a.archive)
    if a.stats:
        stats(words)
    if a.rom:
        sel = [words[n] for n in a.names]
        a.rom.write_bytes(build_rom(sel))
    if a.wav:
        import soundfile as sf
        a.wav.mkdir(parents=True, exist_ok=True)
        for name, w in words.items():
            rom = build_rom([w])
            safe = name.replace("'", "").replace("/", "_")
            sf.write(a.wav / f"{safe}.wav", decoder.render_message(rom, 0), 48000)


if __name__ == "__main__":
    main()
