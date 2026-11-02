"""Audio file loading and conditioning."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

FS = 32000                    # working sample rate for analysis


def load(path: str | Path, fs: int = FS) -> np.ndarray:
    """Read WAV/MP3/FLAC/etc. as mono float64 at `fs`, high-passed and peak-normalized."""
    x, rate = sf.read(str(path), dtype="float64", always_2d=True)
    x = x.mean(axis=1)
    if rate != fs:
        g = np.gcd(int(rate), fs)
        x = signal.resample_poly(x, fs // g, int(rate) // g)
    return condition(x, fs)


def condition(x: np.ndarray, fs: int = FS) -> np.ndarray:
    sos = signal.butter(2, 70, btype="highpass", fs=fs, output="sos")
    x = signal.sosfiltfilt(sos, x)
    peak = np.max(np.abs(x)) if len(x) else 0.0
    return x / peak if peak > 0 else x


def resample_to(x: np.ndarray, fs_in: float, fs_out: float) -> np.ndarray:
    """Resample by an arbitrary (non-integer) ratio."""
    n = int(round(len(x) * fs_out / fs_in))
    if n <= 0:
        return np.zeros(0)
    t = np.arange(n) * (fs_in / fs_out)
    # Band-limit first when downsampling, then interpolate.
    if fs_out < fs_in:
        sos = signal.butter(8, 0.45 * fs_out, fs=fs_in, output="sos")
        x = signal.sosfiltfilt(sos, x)
    return np.interp(t, np.arange(len(x)), x)
