"""Speech analysis: pitch tracking, voicing segmentation, pitch marks, harmonic spectra."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal

HOP_S = 0.005
WIN_S = 0.020
F0_MIN = 60.0
F0_MAX = 600.0
YIN_THRESHOLD = 0.15
VOICED_APERIODICITY = 0.3
PITCH_LOWPASS = 1000.0        # Hz; periodicity is judged on the low band only
MIN_LOW_SHARE = 0.15          # voiced frames keep at least this share of energy below it
WEAK_APERIODICITY = 0.65      # frames this periodic count as voiced next to voiced frames
WEAK_LOW_SHARE = 0.2
MIN_RUN_S = 0.015
HARMONIC_CUTOFF = 7000.0      # chip output filter is 7.2 kHz


@dataclass
class Track:
    fs: int
    hop: int
    f0: np.ndarray            # Hz per frame, 0 where unvoiced
    aperiodicity: np.ndarray
    rms_db: np.ndarray
    low_share: np.ndarray     # fraction of frame energy below PITCH_LOWPASS

    def frame_center(self, i: int) -> int:
        return i * self.hop


@dataclass
class Segment:
    kind: str                 # "voiced" | "unvoiced" | "silence"
    start: int                # samples
    end: int


def _yin(frame: np.ndarray, w: int, tau_min: int, tau_max: int) -> tuple[float, float]:
    c = np.correlate(frame[:w + tau_max], frame[:w], mode="valid")
    e = np.cumsum(np.concatenate(([0.0], frame[:w + tau_max] ** 2)))
    etau = e[w:w + tau_max + 1] - e[:tau_max + 1]
    d = etau[0] + etau - 2 * c
    d[0] = 0.0
    cum = np.cumsum(d[1:])
    cmnd = np.ones_like(d)
    with np.errstate(divide="ignore", invalid="ignore"):
        cmnd[1:] = np.where(cum > 0, d[1:] * np.arange(1, tau_max + 1) / cum, 1.0)
    region = cmnd[tau_min:tau_max]
    below = np.nonzero(region < YIN_THRESHOLD)[0]
    if len(below):
        t = below[0] + tau_min
        while t + 1 < tau_max and cmnd[t + 1] < cmnd[t]:
            t += 1
    else:
        t = int(np.argmin(region)) + tau_min
    # Parabolic interpolation around the minimum.
    if tau_min < t < tau_max - 1:
        a, b, cc = cmnd[t - 1], cmnd[t], cmnd[t + 1]
        den = a - 2 * b + cc
        shift = 0.5 * (a - cc) / den if den > 0 else 0.0
    else:
        shift = 0.0
    return t + shift, float(cmnd[t])


def track(x: np.ndarray, fs: int) -> Track:
    hop = int(HOP_S * fs)
    w = int(WIN_S * fs)
    tau_min = int(fs / F0_MAX)
    tau_max = int(fs / F0_MIN)
    span = w + tau_max
    low = signal.sosfiltfilt(signal.butter(6, PITCH_LOWPASS, fs=fs, output="sos"), x)
    pad = np.concatenate((np.zeros(span // 2), x, np.zeros(span)))
    pad_low = np.concatenate((np.zeros(span // 2), low, np.zeros(span)))
    n = len(x) // hop + 1
    f0 = np.zeros(n)
    ap = np.ones(n)
    rms = np.zeros(n)
    share = np.zeros(n)
    core = slice(tau_max // 2, tau_max // 2 + w)
    for i in range(n):
        frame = pad[i * hop:i * hop + span]
        frame_low = pad_low[i * hop:i * hop + span]
        e = np.mean(frame[core] ** 2) + 1e-12
        rms[i] = np.sqrt(e)
        share[i] = min(1.0, np.mean(frame_low[core] ** 2) / e)
        if rms[i] < 1e-5:
            continue
        tau, a = _yin(frame_low, w, tau_min, tau_max)
        ap[i] = a
        if a < VOICED_APERIODICITY and share[i] >= MIN_LOW_SHARE:
            f0[i] = fs / tau
    rms_db = 20 * np.log10(rms + 1e-12)
    return Track(fs, hop, f0, ap, rms_db, share)


def segment(tr: Track, silence_db: float = 35.0) -> list[Segment]:
    """Label frames voiced/unvoiced/silence and merge into runs."""
    floor = tr.rms_db.max() - silence_db
    voiced = tr.f0 > 0
    weak = (tr.aperiodicity < WEAK_APERIODICITY) & (tr.low_share >= WEAK_LOW_SHARE)
    # Grow voiced runs through weakly periodic frames (pitch jumps, transitions).
    for order in (range(1, len(voiced)), range(len(voiced) - 2, -1, -1)):
        for i in order:
            j = i - 1 if order.step == 1 else i + 1
            if not voiced[i] and weak[i] and voiced[j]:
                voiced[i] = True
    labels = np.where(tr.rms_db < floor, 0, np.where(voiced, 2, 1))
    labels = signal.medfilt(labels, 5).astype(int)
    runs = _runs(labels)
    min_run = max(1, int(round(MIN_RUN_S / HOP_S)))
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for k, (lab, a, b) in enumerate(runs):
            if b - a >= min_run:
                continue
            # Absorb a short run into its longer neighbour.
            left = runs[k - 1] if k else None
            right = runs[k + 1] if k + 1 < len(runs) else None
            nb = max((r for r in (left, right) if r), key=lambda r: r[2] - r[1])
            labels[a:b] = nb[0]
            runs = _runs(labels)
            changed = True
            break
    names = {0: "silence", 1: "unvoiced", 2: "voiced"}
    hop = tr.hop
    out = []
    for lab, a, b in runs:
        out.append(Segment(names[lab], a * hop, b * hop))
    return out


def _runs(labels: np.ndarray) -> list[tuple[int, int, int]]:
    runs = []
    start = 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[start]:
            runs.append((int(labels[start]), start, i))
            start = i
    return runs


def f0_at(tr: Track, pos: float) -> float:
    """Interpolated f0 at a sample position, using nearest voiced frames."""
    voiced = np.nonzero(tr.f0 > 0)[0]
    if not len(voiced):
        return 100.0
    centers = voiced * tr.hop
    return float(np.interp(pos, centers, tr.f0[voiced]))


def pitch_marks(x: np.ndarray, tr: Track, seg: Segment) -> list[int]:
    """Pitch pulse positions within a voiced segment."""
    fs = tr.fs
    sos = signal.butter(4, 1500, fs=fs, output="sos")
    lo = signal.sosfiltfilt(sos, x[max(0, seg.start - fs // 50):min(len(x), seg.end + fs // 50)])
    off = max(0, seg.start - fs // 50)
    # Pick the polarity whose pulses stand out more.
    s = lo if np.max(lo) >= -np.min(lo) else -lo
    marks = []
    t0 = fs / f0_at(tr, seg.start)
    a = seg.start - off
    m = a + int(np.argmax(s[a:a + int(t0)])) if a + int(t0) <= len(s) else a
    while m + off < seg.end:
        marks.append(m + off)
        t = fs / f0_at(tr, m + off)
        lo_i = m + int(0.75 * t)
        hi_i = m + int(1.25 * t) + 1
        if hi_i >= len(s):
            break
        m = lo_i + int(np.argmax(s[lo_i:hi_i]))
    return marks


def harmonic_amplitudes(x: np.ndarray, center: int, f0: float, fs: int,
                        nfft: int = 8192) -> np.ndarray:
    """Amplitudes of harmonics k*f0 (k = 1..K, below HARMONIC_CUTOFF) around `center`."""
    t = fs / f0
    half = int(round(t))
    idx = np.arange(center - half, center + half)
    frame = np.where((idx >= 0) & (idx < len(x)), x[np.clip(idx, 0, len(x) - 1)], 0.0)
    win = np.hanning(len(frame))
    spec = np.abs(np.fft.rfft(frame * win, nfft)) * 2 / win.sum()
    df = fs / nfft
    k_max = int(HARMONIC_CUTOFF // f0)
    amps = np.zeros(k_max)
    for k in range(1, k_max + 1):
        lo = int((k - 0.4) * f0 / df)
        hi = int((k + 0.4) * f0 / df) + 1
        amps[k - 1] = spec[lo:hi].max()
    return amps
