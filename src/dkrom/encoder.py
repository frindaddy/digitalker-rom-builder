"""Audio -> Digitalker instruction stream.

Voiced speech is cut into pitch periods, each period's harmonic magnitudes are
measured and re-synthesized as a zero-phase (symmetric) 128-sample waveform at
the nearest chip pitch, and a Viterbi search picks the 2-bit ADPCM codes whose
decoded waveform best matches it.  Unvoiced sounds are stored as mode 3 noise
blocks and silences as length-only instructions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import analysis, audio, codec
from .codec import Instruction

FULL_SCALE = 8 * codec.PCM_LEVELS[7][0]
BASES = np.array([row[0] for row in codec.PCM_LEVELS], dtype=float)
CHIP_F0 = np.array([codec.CLOCK_HZ / (codec.WAVE_LEN * pv) for pv in codec.PITCH_VALS])
EVEN_MODE_MIN_F0 = 200.0


@dataclass
class Settings:
    voiced_repeats: int = 2       # times each voiced waveform is played (1-8)
    unvoiced_repeats: int = 3     # times each set of noise blocks is played (1-8)
    unvoiced_blocks: int = 2      # distinct noise blocks per mode 3 instruction (1-16)
    unvoiced_pitch: int = 23      # pitch code for noise: 49 us/sample, ~20 kHz
    allow_even: bool = False      # use mode 2 for voices above ~200 Hz
    silence_db: float = 35.0
    trim: bool = True
    gain_db: float = 0.0


QUALITY = {
    "high": Settings(voiced_repeats=1, unvoiced_repeats=1),
    "normal": Settings(),
    "low": Settings(voiced_repeats=3, unvoiced_repeats=4),
    "tiny": Settings(voiced_repeats=4, unvoiced_repeats=4, unvoiced_blocks=1, unvoiced_pitch=8),
}


@dataclass
class Frame:
    """Expected decoder output for one waveform period (mirrors decoder.Frame)."""
    values: list[int] | None
    vol: int = 0
    pitch: int = 0
    ticks: int = 0


@dataclass
class Op:
    """One instruction before address assignment."""
    ins: Instruction              # addr is 0 for silence, 1 as a placeholder otherwise
    blob: bytes | None = None
    frames: list[Frame] = field(default_factory=list)

    def with_last(self, last: bool) -> Op:
        i = self.ins
        return Op(Instruction(i.addr, i.mode, i.count, i.repeats, last), self.blob, self.frames)


@dataclass
class Period:
    f0: float
    amps: np.ndarray              # harmonic magnitudes at k*f0, k = 1..K


# ---------------------------------------------------------------- waveforms

def nearest_pitch(f0: float) -> int:
    return int(np.argmin(np.abs(np.log(CHIP_F0) - np.log(f0))))


def synthesize(periods: list[Period], f0_chip: float, cycles: int) -> np.ndarray:
    """Average zero-phase waveform (128 samples) for `periods` played at `f0_chip`.

    The spectral envelope of each period is sampled at the new harmonic
    frequencies, so formants stay put when the pitch has to move.
    """
    n = codec.WAVE_LEN // cycles
    f_cycle = f0_chip * cycles
    jmax = min(n // 2 - 1, int(analysis.HARMONIC_CUTOFF // f_cycle))
    j = np.arange(1, jmax + 1)
    pos = (np.arange(n) - (n - 1) / 2)[:, None]
    basis = np.cos(2 * np.pi * pos * j[None, :] / n)
    acc = np.zeros(n)
    for p in periods:
        freqs = p.f0 * np.arange(1, len(p.amps) + 1)
        logs = np.log(np.maximum(p.amps, 1e-9))
        a = np.exp(np.interp(j * f_cycle, freqs, logs, right=np.log(1e-9)))
        acc += basis @ a
    w = acc / max(1, len(periods))
    return np.tile(w, cycles)


def _targets(w: np.ndarray, mode: int) -> np.ndarray:
    """Per-code target tau_1..tau_32 (mean of the positions where d_k is played)."""
    k = np.arange(1, 33)
    if mode == codec.MODE_VOICED_ODD:
        return (w[31 + k] + w[96 - k]) / 2
    half = (w[:64] + w[64:]) / 2
    return (half[k - 1] + half[64 - k]) / 2


def _transitions(step: np.ndarray, n_hist: int, hist_of_code) -> tuple[np.ndarray, ...]:
    """Static trellis over states (dac index, history) for a delta table."""
    src, dst, code = [], [], []
    for di in range(_NDAC):
        for h in range(n_hist):
            for c in range(4):
                nd = di + step[h, c]
                if 0 <= nd < _NDAC:
                    src.append(di * n_hist + h)
                    dst.append(nd * n_hist + hist_of_code(c))
                    code.append(c)
    src, dst, code = map(np.array, (src, dst, code))
    n_states = _NDAC * n_hist
    width = max(np.bincount(dst, minlength=n_states))
    incoming = np.full((n_states, width), len(src))     # index len(src) = dummy, cost inf
    fill = np.zeros(n_states, dtype=int)
    for t, d in enumerate(dst):
        incoming[d, fill[d]] = t
        fill[d] += 1
    return src, dst, code, incoming


_NDAC = codec.DAC_MAX - codec.DAC_MIN + 1
_DVALS = np.arange(codec.DAC_MIN, codec.DAC_MAX + 1)
# delta1 depends only on the current code and bit 1 of the previous code.
_STEP1 = np.array([[codec.DELTA1[(2 * h) | c << 2] for c in range(4)] for h in range(2)])
_TRELLIS1 = _transitions(_STEP1, 2, lambda c: c >> 1)


def viterbi_voiced(tau: np.ndarray) -> tuple[list[int], float]:
    """Codes minimizing sum (d_k - tau_k)^2 with the accumulator kept in -7..+8."""
    src, dst, code, incoming = _TRELLIS1
    n_states = incoming.shape[0]
    cost = np.full(n_states, np.inf)
    cost[(0 - codec.DAC_MIN) * 2 + (codec.VOICED_SEED >> 1)] = 0.0
    state_dac = _DVALS[np.arange(n_states) // 2]
    back = []
    for t in tau:
        cand = np.append(cost[src] + (state_dac[dst] - t) ** 2, np.inf)
        pick = incoming[np.arange(n_states), np.argmin(cand[incoming], axis=1)]
        cost = cand[pick]
        back.append(pick)
    s = int(np.argmin(cost))
    err = float(cost[s])
    codes = []
    for pick in reversed(back):
        tr = pick[s]
        codes.append(int(code[tr]))
        s = int(src[tr])
    return codes[::-1], err


def quantize_voiced(w: np.ndarray, mode: int) -> tuple[int, list[int], float]:
    """Pick volume and codes for a target waveform (output units). Returns (vol, codes, err)."""
    tau = _targets(w, mode)
    hi = max(tau.max() / codec.DAC_MAX, -tau.min() / -codec.DAC_MIN, 1e-9)
    v0 = int(np.clip(np.searchsorted(BASES, hi), 0, 7))
    best = None
    for v in sorted({max(0, v0 - 1), v0, min(7, v0 + 1)}):
        codes, err = viterbi_voiced(tau / BASES[v])
        err *= BASES[v] ** 2
        if best is None or err < best[2]:
            best = (v, codes, err)
    return best


# ---------------------------------------------------------------- segments

def _voiced_periods(x: np.ndarray, tr: analysis.Track, seg: analysis.Segment) -> list[Period]:
    marks = analysis.pitch_marks(x, tr, seg)
    out = []
    for i, m in enumerate(marks):
        if len(marks) > 1:
            gaps = np.diff(marks[max(0, i - 1):i + 2])
            f0 = tr.fs / float(np.mean(gaps))
        else:
            f0 = analysis.f0_at(tr, m)
        out.append(Period(f0, analysis.harmonic_amplitudes(x, m, f0, tr.fs)))
    return out


def encode_voiced(periods: list[Period], gain: float, st: Settings) -> list[Op]:
    """Group periods into voiced instructions with repeats and pitch slides."""
    if not periods:
        return []
    f0s = np.array([p.f0 for p in periods])
    even = st.allow_even and np.median(f0s) > EVEN_MODE_MIN_F0
    mode = codec.MODE_VOICED_EVEN if even else codec.MODE_VOICED_ODD
    cycles = 2 if even else 1

    ops: list[Op] = []
    cur: list[tuple[int, bytes, list[Frame]]] = []   # (pitch after last repeat, bytes, frames)
    cur_repeats = 0

    def flush():
        nonlocal cur
        if cur:
            blob = b"".join(c[1] for c in cur)
            frames = [f for c in cur for f in c[2]]
            ops.append(Op(Instruction(1, mode, len(cur), cur_repeats), blob, frames))
        cur = []

    # Each played waveform spans `cycles` pitch periods (two in mode 2).
    r = max(1, min(8, st.voiced_repeats))
    span = r * cycles
    groups = [periods[i:i + span] for i in range(0, len(periods), span)]
    for g in groups:
        reps = max(1, round(len(g) / cycles))
        if reps != cur_repeats or len(cur) == 16:
            flush()
            cur_repeats = reps
        target = nearest_pitch(float(np.exp(np.mean(np.log([p.f0 for p in g])))) / cycles)
        if cur:
            prev = cur[-1][0]
            diff = target - prev
            if abs(diff) > min(reps, 15):
                flush()
                cur_repeats = reps
        if cur:
            header_pitch = (0x10 if diff < 0 else 0) | abs(diff)
            pitches = [codec.pitch_next(header_pitch, prev, k) for k in range(reps)]
        else:
            header_pitch = target
            pitches = [target] * reps
        w = synthesize(g, CHIP_F0[pitches[-1]], cycles) * gain
        vol, codes, _ = quantize_voiced(w, mode)
        values = codec.voiced_waveform(codec.voiced_trajectory(codes), mode)
        frames = [Frame(values, vol, p) for p in pitches]
        cur.append((pitches[-1], bytes([vol << 5 | header_pitch]) + codec.pack_codes(codes), frames))
    flush()
    return ops


def _noise_codes(block: np.ndarray, base: float) -> list[int]:
    """4-level PCM: code c plays as (c - 1) * base; the last code is pinned to 1.

    Pinning the last code makes the result independent of whether the chip
    resets its accumulator between noise blocks (MAME says it does).
    """
    v = np.clip(np.floor(block / base) + 1, -1, 2).astype(int)
    codes = [int(c) + 1 for c in v]
    codes[-1] = 1
    return codes


def encode_unvoiced(y: np.ndarray, gain: float, st: Settings) -> list[Op]:
    """Mode 3 instructions for noise `y` sampled at the chip rate for st.unvoiced_pitch."""
    pitch = st.unvoiced_pitch
    n_blocks = max(1, int(round(len(y) / codec.WAVE_LEN)))
    y = np.pad(y * gain, (0, n_blocks * codec.WAVE_LEN - len(y) + codec.WAVE_LEN))
    blocks = [y[i * codec.WAVE_LEN:(i + 1) * codec.WAVE_LEN] for i in range(n_blocks)]
    r = max(1, min(8, st.unvoiced_repeats))
    s = max(1, min(16, st.unvoiced_blocks))
    ops = []
    i = 0
    while i < n_blocks:
        span = min(n_blocks - i, s * r)
        reps = min(r, span)
        count = span // reps
        chosen = blocks[i:i + count]
        rms = float(np.sqrt(np.mean(np.concatenate(blocks[i:i + span]) ** 2)))
        vol = int(np.argmin(np.abs(np.log(BASES) - np.log(max(rms, 1.0)))))
        codes = [_noise_codes(b, BASES[vol]) for b in chosen]
        blob = bytes([vol << 5 | pitch]) + b"".join(codec.pack_codes(c) for c in codes)
        frames = []
        prev = codec.UNVOICED_SEED
        for _ in range(reps):
            for c in codes:
                frames.append(Frame(codec.unvoiced_values(c, prev), vol, pitch))
                prev = c[-1]
        ops.append(Op(Instruction(1, codec.MODE_UNVOICED, count, reps), blob, frames))
        i += count * reps
    return ops


def encode_silence(seconds: float) -> list[Op]:
    units = int(round(seconds * codec.CLOCK_HZ / codec.SILENCE_UNIT))
    return [Op(ins, None, [Frame(None, ticks=ins.silence_ticks)]) for ins in codec.silence(units)]


# ---------------------------------------------------------------- clips

def encode_audio(x: np.ndarray, fs: int, st: Settings) -> list[Op]:
    """Encode conditioned mono audio into a complete message (last op has the end bit)."""
    tr = analysis.track(x, fs)
    segs = analysis.segment(tr, st.silence_db)
    if st.trim:
        while segs and segs[0].kind == "silence":
            segs.pop(0)
        while segs and segs[-1].kind == "silence":
            segs.pop()

    analysed: list[tuple[analysis.Segment, list[Period] | None]] = []
    for seg in segs:
        periods = _voiced_periods(x, tr, seg) if seg.kind == "voiced" else None
        if periods is not None and len(periods) < 2:
            seg = analysis.Segment("unvoiced", seg.start, seg.end)
            periods = None
        analysed.append((seg, periods))

    # One gain for the whole clip: loudest voiced periods reach the top DAC step.
    peaks = []
    for seg, periods in analysed:
        for p in periods or []:
            f0 = CHIP_F0[nearest_pitch(p.f0)]
            peaks.append(np.max(np.abs(synthesize([p], f0, 1))))
    if peaks:
        gain = 0.95 * FULL_SCALE / np.percentile(peaks, 98)
    else:
        gain = 0.95 * FULL_SCALE / max(np.max(np.abs(x)), 1e-9) / 4
    gain *= 10 ** (st.gain_db / 20)

    ops: list[Op] = []
    for seg, periods in analysed:
        if seg.kind == "silence":
            ops += encode_silence((seg.end - seg.start) / fs)
        elif periods is not None:
            ops += encode_voiced(periods, gain, st)
        else:
            rate = codec.CLOCK_HZ / codec.PITCH_VALS[st.unvoiced_pitch]
            y = audio.resample_to(x[seg.start:seg.end], fs, rate)
            ops += encode_unvoiced(y, gain, st)
    if not ops:
        ops = encode_silence(codec.SILENCE_UNIT / codec.CLOCK_HZ)
    return ops


def encode_file(path, st: Settings) -> list[Op]:
    return encode_audio(audio.load(path), audio.FS, st)


def finish(ops: list[Op]) -> list[Op]:
    """Copy of `ops` with only the final instruction flagged as last."""
    return [op.with_last(i == len(ops) - 1) for i, op in enumerate(ops)]
