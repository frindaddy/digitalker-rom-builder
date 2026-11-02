"""MM54104 Digitalker speech data format.

Tables and bit layouts follow MAME's src/devices/sound/digitalk.cpp
(O. Galibert, from chip analysis by K. Horton and Lord Nightmare).

ROM layout:
    pointer table   big-endian 16-bit, message n at bytes 2n/2n+1 (14 bits used)
    instructions    3 bytes each:  ERRRSSSS  AAAAAAAA  MMAAAAAA
                    E = last instruction of the message, R+1 = repeats,
                    S+1 = waveform count, A = 14-bit data address, M = mode
    speech data     mode 0/2: 9 bytes per waveform (header + 32 2-bit codes)
                    mode 3:   1 header byte + 32 bytes (128 codes) per waveform
"""

from __future__ import annotations

from dataclasses import dataclass

CLOCK_HZ = 1_000_000          # 4 MHz crystal / 4: one DAC tick per microsecond
WAVE_LEN = 128                # DAC values per waveform period
SILENCE_UNIT = 40 * 128       # ticks per silence unit (5.12 ms)
TAIL_SILENCE = 81920          # ticks of silence appended after the last instruction
ADDR_MASK = 0x3FFF
MAX_ROM = 0x4000

MODE_VOICED_ODD = 0           # _/\_  odd harmonics
MODE_VOICED_EVEN = 2          # /\/\  even harmonics
MODE_UNVOICED = 3

VOICED_BYTES = 9
UNVOICED_BYTES = 32

PCM_LEVELS = (
    (473, 945, 1418, 1890, 2363, 2835, 3308, 3781),
    (655, 1310, 1966, 2621, 3276, 3931, 4586, 5242),
    (925, 1851, 2776, 3702, 4627, 5553, 6478, 7404),
    (1249, 2498, 3747, 4996, 6245, 7494, 8743, 9992),
    (1638, 3276, 4914, 6552, 8190, 9828, 11466, 13104),
    (2252, 4504, 6757, 9009, 11261, 13514, 15766, 18018),
    (2989, 5979, 8968, 11957, 14947, 17936, 20925, 23915),
    (4095, 8190, 12285, 16380, 20475, 24570, 28665, 32760),
)

# Indexed by prev_code | (cur_code << 2).
DELTA1 = (-4, -4, -1, -1, -2, -2, 0, 0, 0, 0, 2, 2, 1, 1, 4, 4)
DELTA2 = (0, -1, -2, -3, 1, 0, -1, -2, 2, 1, 0, -1, 3, 2, 1, 0)

VOICED_SEED = 2               # initial "previous code" for delta1 streams
UNVOICED_SEED = 1             # initial "previous code" for delta2 streams

# Microseconds each DAC value is held; period = PITCH_VALS[p] * 128 us.
PITCH_VALS = (
    97, 95, 92, 89, 87, 84, 82, 80, 77, 75, 73, 71, 69, 67, 65, 63,
    61, 60, 58, 56, 55, 53, 52, 50, 49, 48, 46, 45, 43, 42, 41, 40,
)

# Lowest signed DAC value that doesn't wrap, highest likewise.
DAC_MIN = -7
DAC_MAX = 8


def dac_amplitude(vol: int, dac: int) -> int:
    """Output level for a DAC accumulator value (wraps modulo 16 like the chip)."""
    dac &= 15
    if dac >= 9:
        return -PCM_LEVELS[vol][15 - dac]
    if dac:
        return PCM_LEVELS[vol][dac - 1]
    return 0


@dataclass(frozen=True)
class Instruction:
    addr: int                 # 0 = silence
    mode: int = 0
    count: int = 1            # waveforms, 1-16
    repeats: int = 1          # 1-8
    last: bool = False

    @property
    def is_silence(self) -> bool:
        return self.addr == 0

    def pack(self) -> bytes:
        if not 1 <= self.count <= 16 or not 1 <= self.repeats <= 8:
            raise ValueError(f"count/repeats out of range: {self}")
        if not 0 <= self.addr <= ADDR_MASK or not 0 <= self.mode <= 3:
            raise ValueError(f"addr/mode out of range: {self}")
        v1 = (0x80 if self.last else 0) | (self.repeats - 1) << 4 | (self.count - 1)
        return bytes((v1, self.addr & 0xFF, self.mode << 6 | self.addr >> 8))

    @classmethod
    def unpack(cls, b: bytes) -> Instruction:
        v1, v2, v3 = b[0], b[1], b[2]
        return cls(
            addr=v2 | (v3 & 0x3F) << 8,
            mode=v3 >> 6,
            count=(v1 & 15) + 1,
            repeats=((v1 >> 4) & 7) + 1,
            last=bool(v1 & 0x80),
        )

    @property
    def silence_ticks(self) -> int:
        return SILENCE_UNIT * self.count * self.repeats

    def data_size(self) -> int:
        if self.is_silence:
            return 0
        if self.mode == MODE_UNVOICED:
            return 1 + UNVOICED_BYTES * self.count
        return VOICED_BYTES * self.count


def silence(units: int, last: bool = False, mode: int = 1) -> list[Instruction]:
    """Instructions totalling `units` x 5.12 ms of silence (DVSS writes mode 1)."""
    out = []
    while units > 0:
        n = min(units, 128)
        count, repeats = _factor_silence(n)
        out.append(Instruction(0, mode, count, repeats))
        units -= count * repeats
    if out and last:
        out[-1] = Instruction(0, out[-1].mode, out[-1].count, out[-1].repeats, True)
    return out


def _factor_silence(n: int) -> tuple[int, int]:
    """Largest count*repeats <= n with count <= 16, repeats <= 8."""
    best = (1, 1)
    for r in range(1, 9):
        c = min(16, n // r)
        if c >= 1 and c * r > best[0] * best[1]:
            best = (c, r)
    return best


def unpack_codes(data: bytes) -> list[int]:
    """2-bit codes, lowest bit pair of each byte first."""
    return [(b >> s) & 3 for b in data for s in (0, 2, 4, 6)]


def pack_codes(codes: list[int]) -> bytes:
    if len(codes) % 4:
        raise ValueError("code count must be a multiple of 4")
    return bytes(
        codes[i] | codes[i + 1] << 2 | codes[i + 2] << 4 | codes[i + 3] << 6
        for i in range(0, len(codes), 4)
    )


def voiced_trajectory(codes: list[int]) -> list[int]:
    """Accumulator values d0..d32 for 32 delta1 codes (d0 = 0)."""
    d = [0]
    prev = VOICED_SEED
    for c in codes:
        d.append(d[-1] + DELTA1[prev | c << 2])
        prev = c
    return d


def voiced_waveform(d: list[int], mode: int) -> list[int]:
    """Expand a 33-value trajectory into the 128 accumulator values the chip plays."""
    if mode == MODE_VOICED_ODD:
        return [0] * 32 + d[1:33] + [d[32]] + d[31::-1] + [0] * 31
    if mode == MODE_VOICED_EVEN:
        half = d[1:33] + [d[32]] + d[31:0:-1]
        return half + half
    raise ValueError(f"not a voiced mode: {mode}")


def unvoiced_values(codes: list[int], prev: int) -> list[int]:
    """Accumulator values for one 128-code delta2 waveform (dac reset to 0, as MAME)."""
    d = 0
    out = []
    for c in codes:
        d += DELTA2[prev | c << 2]
        out.append(d)
        prev = c
    return out


def pitch_next(header: int, prev: int, step: int) -> int:
    delta = min(header & 0xF, step + 1)
    if header & 0x10:
        delta = -delta
    return max(0, min(31, prev + delta))
