"""Lay out encoded messages as an MM54104 ROM image."""

from __future__ import annotations

from dataclasses import dataclass

from . import codec
from .codec import Instruction
from .encoder import Op

ROM_SIZES = {"2K": 2048, "4K": 4096, "8K": 8192, "16K": 16384, "SPLIT8K": 16384}
EMPTY_MESSAGE = Instruction(0, 0, 1, 1, True)     # 5 ms of silence, as DVSS uses for gaps


class RomFullError(Exception):
    def __init__(self, needed: int, size: int):
        super().__init__(f"needs {needed} bytes but the ROM holds {size}")
        self.needed = needed
        self.size = size


@dataclass
class Layout:
    image: bytes                  # padded to the ROM size with 0xFF
    pointers: int                 # table entries
    instructions: int
    data_bytes: int               # distinct speech data stored
    reused_bytes: int             # speech data shared instead of stored again
    size: int

    @property
    def used(self) -> int:
        return 2 * self.pointers + 3 * self.instructions + self.data_bytes

    def summary(self) -> str:
        return (f"  Message pointers: {self.pointers:5} ({2 * self.pointers} bytes)\n"
                f"      Instructions: {self.instructions:5} ({3 * self.instructions} bytes)\n"
                f"  Speech data used: {self.data_bytes:5} bytes\n"
                f"Speech data reused: {self.reused_bytes:5} bytes\n"
                f"  Total bytes used: {self.used:5} ({100 * self.used // self.size}%)\n"
                f"  Total bytes left: {self.size - self.used:5}")


def layout(messages: dict[int, list[Op]], size: int = 16384) -> Layout:
    """Build the image. `messages` maps index (0-255) to finished op lists."""
    if not messages:
        raise ValueError("no messages")
    if max(messages) > 255 or min(messages) < 0:
        raise ValueError("message indices must be 0-255")
    if size > codec.MAX_ROM:
        raise ValueError("the MM54104 addresses at most 16 KiB")
    count = max(messages) + 1
    table = 2 * count

    programs: list[list[Op] | None] = [messages.get(i) for i in range(count)]
    n_ins = sum(len(p) for p in programs if p) + (1 if None in programs else 0)
    data_start = table + 3 * n_ins

    addr_of: dict[bytes, int] = {}
    data = bytearray()
    reused = 0
    for prog in programs:
        for op in prog or []:
            if op.blob is None:
                continue
            if op.blob in addr_of:
                reused += len(op.blob)
            else:
                addr_of[op.blob] = data_start + len(data)
                data += op.blob

    total = data_start + len(data)
    if total > size:
        raise RomFullError(total, size)

    image = bytearray(table)
    empty_at = None
    for i, prog in enumerate(programs):
        if prog is None:
            if empty_at is None:
                empty_at = len(image)
                image += EMPTY_MESSAGE.pack()
            ptr = empty_at
        else:
            ptr = len(image)
            for op in prog:
                ins = op.ins
                addr = 0 if op.blob is None else addr_of[op.blob]
                image += Instruction(addr, ins.mode, ins.count, ins.repeats, ins.last).pack()
        image[2 * i:2 * i + 2] = ptr.to_bytes(2, "big")
    assert len(image) == data_start
    image += data
    image += b"\xff" * (size - len(image))
    return Layout(bytes(image), count, n_ins, len(data), reused, size)
