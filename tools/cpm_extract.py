"""Extract files from an 8" SSSD CP/M disk image (IBM 3740 format).

Used to pull STDARC.IDX/.DAT and the DVSS programs off the bitsavers images:
    python tools/cpm_extract.py nsc_digitalker_archive_disk.img outdir/
"""

import sys
from pathlib import Path

SECTOR = 128
SECTORS_PER_TRACK = 26
RESERVED_TRACKS = 2
BLOCK = 1024
DIR_ENTRIES = 64
# Standard CP/M 2.2 skew of 6 for 8" single density, 1-based physical sectors.
SKEW = [1, 7, 13, 19, 25, 5, 11, 17, 23, 3, 9, 15, 21,
        2, 8, 14, 20, 26, 6, 12, 18, 24, 4, 10, 16, 22]


def logical_sector(img: bytes, n: int) -> bytes:
    track, sec = divmod(n, SECTORS_PER_TRACK)
    off = ((RESERVED_TRACKS + track) * SECTORS_PER_TRACK + SKEW[sec] - 1) * SECTOR
    return img[off:off + SECTOR]


def block(img: bytes, b: int) -> bytes:
    per = BLOCK // SECTOR
    return b"".join(logical_sector(img, b * per + i) for i in range(per))


def extract(img: bytes) -> dict[str, bytes]:
    directory = block(img, 0) + block(img, 1)
    extents: dict[str, list[tuple[int, int, list[int]]]] = {}
    for i in range(DIR_ENTRIES):
        e = directory[i * 32:(i + 1) * 32]
        if e[0] == 0xE5:
            continue
        name = bytes(c & 0x7F for c in e[1:9]).decode("ascii").strip()
        ext = bytes(c & 0x7F for c in e[9:12]).decode("ascii").strip()
        blocks = [b for b in e[16:32] if b]
        extents.setdefault(f"{name}.{ext}", []).append((e[12] | e[14] << 5, e[15], blocks))
    files = {}
    for fname, exts in extents.items():
        data = b""
        for _, records, blocks in sorted(exts):
            chunk = b"".join(block(img, b) for b in blocks)
            data += chunk[:records * SECTOR]
        files[fname] = data
    return files


def main() -> None:
    img = Path(sys.argv[1]).read_bytes()
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    for name, data in extract(img).items():
        (out / name).write_bytes(data)
        print(f"{name:14} {len(data):7}")


if __name__ == "__main__":
    main()
