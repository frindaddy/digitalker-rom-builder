import numpy as np
import pytest
from scipy import signal

from dkrom import audio, codec, decoder, encoder, rombuild

FS = audio.FS


def vowel(f0: float, seconds: float, formants=(700, 1200, 2600)) -> np.ndarray:
    """Glottal pulse train through formant resonators."""
    n = int(seconds * FS)
    x = np.zeros(n)
    x[(np.arange(0, seconds, 1 / f0) * FS).astype(int)] = 1.0
    for f in formants:
        r = np.exp(-np.pi * 100 / FS)
        x = signal.lfilter([1], [1, -2 * r * np.cos(2 * np.pi * f / FS), r * r], x)
    return x


def word(f0: float = 110) -> np.ndarray:
    rng = np.random.default_rng(1)
    hiss = signal.lfilter(*signal.butter(4, 3000, "highpass", fs=FS), rng.normal(size=int(0.12 * FS)))
    gap = np.zeros(int(0.08 * FS))
    v = vowel(f0, 0.3)
    v /= np.abs(v).max()
    return audio.condition(np.concatenate([gap, 0.3 * hiss, v, gap, 0.3 * hiss, gap]))


def expected(ops):
    return [(f.values, f.vol, f.pitch, f.ticks) for op in ops for f in op.frames]


def decoded(rom, index=0):
    return [(f.values, f.vol, f.pitch, f.ticks) for f in decoder.message_frames(rom, index)[:-1]]


@pytest.mark.parametrize("quality", list(encoder.QUALITY))
def test_roundtrip_bit_exact(quality):
    ops = encoder.finish(encoder.encode_audio(word(), FS, encoder.QUALITY[quality]))
    lay = rombuild.layout({0: ops})
    assert decoded(lay.image) == expected(ops)


def test_even_mode_roundtrip():
    st = encoder.Settings(allow_even=True)
    ops = encoder.finish(encoder.encode_audio(word(f0=260), FS, st))
    assert any(op.ins.mode == codec.MODE_VOICED_EVEN for op in ops)
    lay = rombuild.layout({0: ops})
    assert decoded(lay.image) == expected(ops)


def test_structure_and_duration():
    x = word()
    ops = encoder.finish(encoder.encode_audio(x, FS, encoder.QUALITY["normal"]))
    kinds = ["S" if op.ins.is_silence else op.ins.mode for op in ops]
    assert codec.MODE_VOICED_ODD in kinds and codec.MODE_UNVOICED in kinds and "S" in kinds
    assert ops[-1].ins.last and not any(op.ins.last for op in ops[:-1])
    played = sum(f.ticks if f.values is None else 128 * codec.PITCH_VALS[f.pitch]
                 for op in ops for f in op.frames) / 1e6
    trimmed = len(x) / FS - 0.08 - 0.08          # leading/trailing silence removed
    assert abs(played - trimmed) < 0.05


def test_voiced_pitch_tracks_input():
    ops = encoder.encode_audio(audio.condition(vowel(150, 0.4)), FS, encoder.QUALITY["high"])
    pitches = [f.pitch for op in ops if op.ins.mode == 0 for f in op.frames]
    f0 = np.median(encoder.CHIP_F0[pitches])
    assert abs(f0 - 150) / 150 < 0.03


@pytest.mark.parametrize("seed", range(5))
def test_viterbi_stays_in_range(seed):
    rng = np.random.default_rng(seed)
    tau = rng.uniform(-12, 12, 32)                   # deliberately out of range
    codes, _ = encoder.viterbi_voiced(tau)
    d = codec.voiced_trajectory(codes)
    assert all(codec.DAC_MIN <= v <= codec.DAC_MAX for v in d)


def test_viterbi_is_optimal_for_reachable_target():
    rng = np.random.default_rng(0)
    while True:
        d = codec.voiced_trajectory(list(rng.integers(0, 4, 32)))
        if all(codec.DAC_MIN <= v <= codec.DAC_MAX for v in d):
            break
    tau = np.array(d[1:], dtype=float)
    _, err = encoder.viterbi_voiced(tau)
    assert err == 0


def test_noise_codes_pin_last_code():
    codes = encoder._noise_codes(np.full(128, 5000.0), 1000.0)
    assert codes[-1] == 1 and set(codes[:-1]) == {3}


def test_silence_ops():
    ops = encoder.encode_silence(0.5)
    assert sum(op.ins.silence_ticks for op in ops) == pytest.approx(500_000, abs=codec.SILENCE_UNIT)
