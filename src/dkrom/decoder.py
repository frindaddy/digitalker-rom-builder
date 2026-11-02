"""Bit-exact MM54104 playback model (port of MAME digitalk.cpp, minus its bugs).

Differences from MAME:
  * mode 3 data addresses use the full 14 bits (MAME truncates to 8);
  * playback stops after the tail silence instead of replaying stale data.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal

from . import codec
from .codec import Instruction


@dataclass
class Frame:
    """One played waveform period (or a run of silence when values is None)."""
    values: list[int] | None  # 128 accumulator values
    vol: int = 0
    pitch: int = 0            # index into PITCH_VALS
    ticks: int = 0            # silence length when values is None

    @property
    def duration(self) -> int:
        if self.values is None:
            return self.ticks
        return codec.WAVE_LEN * codec.PITCH_VALS[self.pitch]


def pointer(rom: bytes, index: int) -> int:
    return ((rom[2 * index] << 8) | rom[2 * index + 1]) & codec.ADDR_MASK


def message_count(rom: bytes) -> int:
    """Number of pointer-table entries, assuming the table precedes all instructions."""
    lo = len(rom)
    n = 0
    while n < 256 and 2 * n + 1 < lo:
        p = pointer(rom, n)
        if p >= 2 * (n + 1):
            lo = min(lo, p)
        n += 1
    return n


def parse_message(rom: bytes, index: int, limit: int = 4096) -> list[Instruction]:
    pos = pointer(rom, index)
    out = []
    while len(out) < limit:
        if pos + 3 > len(rom):
            raise ValueError(f"message {index}: instruction list runs off the ROM")
        ins = Instruction.unpack(rom[pos:pos + 3])
        out.append(ins)
        pos += 3
        if ins.last:
            return out
    raise ValueError(f"message {index}: no end instruction within {limit}")


def _byte(rom: bytes, addr: int) -> int:
    return rom[addr] if addr < len(rom) else 0xFF


def instruction_frames(rom: bytes, ins: Instruction, prev_pitch: int = 0) -> tuple[list[Frame], int]:
    """Frames played by one instruction, plus the pitch carried to the next one."""
    if ins.is_silence:
        return [Frame(None, ticks=ins.silence_ticks)], prev_pitch

    frames = []
    if ins.mode in (codec.MODE_VOICED_ODD, codec.MODE_VOICED_EVEN):
        apos = ins.addr
        for seg in range(ins.count):
            h = _byte(rom, apos)
            codes = codec.unpack_codes(bytes(_byte(rom, apos + k) for k in range(1, 9)))
            wave = codec.voiced_waveform(codec.voiced_trajectory(codes), ins.mode)
            pitch = prev_pitch
            for rep in range(ins.repeats):
                pitch = codec.pitch_next(h, prev_pitch, rep) if seg else h & 0x1F
                frames.append(Frame(wave, h >> 5, pitch))
            prev_pitch = pitch
            apos += codec.VOICED_BYTES
    elif ins.mode == codec.MODE_UNVOICED:
        h = _byte(rom, ins.addr)
        prev = codec.UNVOICED_SEED
        for _ in range(ins.repeats):
            for seg in range(ins.count):
                base = ins.addr + 1 + codec.UNVOICED_BYTES * seg
                codes = codec.unpack_codes(bytes(_byte(rom, base + k) for k in range(32)))
                frames.append(Frame(codec.unvoiced_values(codes, prev), h >> 5, h & 0x1F))
                prev = codes[-1]
    else:
        # Mode 1 with data is undocumented; MAME skips the instruction.
        pass
    return frames, prev_pitch


def message_frames(rom: bytes, index: int) -> list[Frame]:
    frames: list[Frame] = []
    prev_pitch = 0
    for ins in parse_message(rom, index):
        f, prev_pitch = instruction_frames(rom, ins, prev_pitch)
        frames.extend(f)
    frames.append(Frame(None, ticks=codec.TAIL_SILENCE))
    return frames


def render_frames(frames: list[Frame], tail: bool = True) -> np.ndarray:
    """Chip output at 1 MHz as int16 (zero-order hold of the DAC)."""
    parts = []
    for f in frames:
        if f.values is None:
            if tail or f is not frames[-1]:
                parts.append(np.zeros(f.ticks, dtype=np.int16))
            continue
        amps = np.array([codec.dac_amplitude(f.vol, v) for v in f.values], dtype=np.int16)
        parts.append(np.repeat(amps, codec.PITCH_VALS[f.pitch]))
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)


def resample(x: np.ndarray, rate: int = 48000, analog_filter: bool = False) -> np.ndarray:
    """Downsample 1 MHz chip output to `rate`, optionally applying the DT1050 output filter."""
    y = x.astype(np.float64)
    if analog_filter:
        # Datasheet reference filter: 7.2 kHz low-pass, 194 Hz high-pass.
        sos = signal.butter(4, [194, 7200], btype="bandpass", fs=codec.CLOCK_HZ, output="sos")
        y = signal.sosfilt(sos, y)
    g = np.gcd(rate, codec.CLOCK_HZ)
    y = signal.resample_poly(y, rate // g, codec.CLOCK_HZ // g)
    return np.clip(np.round(y), -32768, 32767).astype(np.int16)


def render_message(rom: bytes, index: int, rate: int = 48000, analog_filter: bool = False,
                   tail: bool = True) -> np.ndarray:
    return resample(render_frames(message_frames(rom, index), tail), rate, analog_filter)
