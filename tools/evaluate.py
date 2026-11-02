"""Objective comparison of two recordings: mel-cepstral distance after DTW alignment.

    python tools/evaluate.py reference.wav candidate.wav [...more candidates]

Lower is better.  Both signals are band-limited to the chip's 194 Hz - 7.2 kHz
output band first so the comparison ignores what the hardware filters out.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal
from scipy.fft import dct

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

FS = 16000
N_MELS = 30
N_CEPS = 13


def _load(path) -> np.ndarray:
    x, rate = sf.read(str(path), dtype="float64", always_2d=True)
    x = x.mean(axis=1)
    g = np.gcd(int(rate), FS)
    return signal.resample_poly(x, FS // g, int(rate) // g)


def _mel_fb(nfft: int) -> np.ndarray:
    mel = lambda f: 2595 * np.log10(1 + f / 700)
    inv = lambda m: 700 * (10 ** (m / 2595) - 1)
    edges = inv(np.linspace(mel(194), mel(7200), N_MELS + 2))
    bins = np.fft.rfftfreq(nfft, 1 / FS)
    fb = np.zeros((N_MELS, len(bins)))
    for i in range(N_MELS):
        lo, c, hi = edges[i:i + 3]
        fb[i] = np.clip(np.minimum((bins - lo) / (c - lo), (hi - bins) / (hi - c)), 0, None)
    return fb


def mfcc(x: np.ndarray) -> np.ndarray:
    sos = signal.butter(4, [194, 7200], btype="bandpass", fs=FS, output="sos")
    x = signal.sosfiltfilt(sos, x)
    _, _, z = signal.stft(x, FS, nperseg=400, noverlap=240, nfft=512)
    p = np.abs(z) ** 2
    e = p.sum(axis=0)
    keep = e > e.max() * 1e-4                      # drop silent frames
    m = np.log(_mel_fb(512) @ p[:, keep] + 1e-10)
    m -= m.mean()                                  # level-independent
    return dct(m, axis=0, norm="ortho")[1:N_CEPS].T


def dtw_distance(a: np.ndarray, b: np.ndarray) -> float:
    d = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1))
    n, m = d.shape
    acc = np.full((n + 1, m + 1), np.inf)
    acc[0, 0] = 0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            acc[i, j] = d[i - 1, j - 1] + min(acc[i - 1, j], acc[i, j - 1], acc[i - 1, j - 1])
    return float(acc[n, m] / (n + m))


def compare(ref, cand) -> float:
    return dtw_distance(mfcc(_load(ref)), mfcc(_load(cand)))


if __name__ == "__main__":
    ref = sys.argv[1]
    for c in sys.argv[2:]:
        print(f"{compare(ref, c):6.3f}  {c}")
