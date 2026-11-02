import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from dkrom import build, codec, decoder, encoder, manifest, rombuild
from dkrom.codec import Instruction
from dkrom.encoder import Op

from test_encoder import FS, word


def voiced_op(blob: bytes, last=False) -> Op:
    return Op(Instruction(1, 0, len(blob) // 9, 1, last), blob)


def test_layout_pointers_gaps_and_sharing():
    blob = bytes([0x1F]) + bytes(8)
    msgs = {
        1: [voiced_op(blob, last=True)],
        3: [Op(Instruction(0, 1, 4, 1)), voiced_op(blob, last=True)],
    }
    lay = rombuild.layout(msgs, 2048)
    rom = lay.image
    assert len(rom) == 2048 and rom[-1] == 0xFF
    assert lay.pointers == 4
    assert decoder.pointer(rom, 0) == decoder.pointer(rom, 2)          # gaps share one entry
    assert decoder.parse_message(rom, 0) == [rombuild.EMPTY_MESSAGE]
    a = decoder.parse_message(rom, 1)[0].addr
    b = decoder.parse_message(rom, 3)[1].addr
    assert a == b and rom[a:a + 9] == blob                               # data stored once
    assert lay.reused_bytes == 9 and lay.data_bytes == 9


def test_layout_overflow():
    big = [voiced_op(bytes(9 * 16), last=True)]
    with pytest.raises(rombuild.RomFullError):
        rombuild.layout({i: [voiced_op(bytes([i]) * 144, last=True)] for i in range(20)}, 2048)
    rombuild.layout({0: big}, 2048)


def test_manifest_parse(tmp_path):
    text = """
    # comment
    rom_size: 8k
    quality: low
    voiced_repeats: 2
    1: a.wav            # trailing comment
    2: "b c.mp3"[gain_db=3, voiced_repeats=1] sil40 a.wav | Phrase Two
    """
    man = manifest.parse(text, tmp_path)
    assert man.rom_size == "8K" and man.quality == "low"
    assert man.settings().voiced_repeats == 2
    m2 = man.messages[2]
    assert m2.label == "Phrase Two"
    clip, sil, clip2 = m2.items
    assert clip.path == tmp_path / "b c.mp3"
    assert dict(clip.overrides) == {"gain_db": 3.0, "voiced_repeats": 1}
    assert sil == manifest.Silence(40)
    assert man.messages[1].label == "a"


@pytest.mark.parametrize("bad", ["300: a.wav", "1: a.wav\n1: b.wav", "bogus: 1", "1: a.wav[nope=1]", "1:"])
def test_manifest_errors(bad):
    with pytest.raises(manifest.ManifestError):
        manifest.parse(bad)


def test_build_end_to_end(tmp_path):
    sf.write(tmp_path / "w.wav", word(), FS)
    sf.write(tmp_path / "w2.wav", word(140), FS)
    (tmp_path / "v.voc").write_text("rom_size: 4K\n1: w.wav\n2: w2.wav\n3: w.wav sil100 w2.wav | both\n")
    man = manifest.load(tmp_path / "v.voc")
    res = build.Builder(man).build()
    paths = build.write_outputs(res, tmp_path / "out.bin", man.rom_size)
    rom = paths[0].read_bytes()
    assert len(rom) == 4096
    assert json.loads(paths[1].read_text()) == {"1": "w", "2": "w2", "3": "both"}
    assert res.layout.reused_bytes > 0
    for i in (1, 2, 3):
        assert decoder.parse_message(rom, i)[-1].last


def test_build_auto_fit_steps_down(tmp_path):
    sf.write(tmp_path / "w.wav", np.tile(word(), 3), FS)
    lines = "\n".join(f"{i}: w.wav[gain_db={i}]" for i in range(1, 5))
    (tmp_path / "v.voc").write_text("rom_size: 4K\nquality: high\n" + lines + "\n")
    res = build.Builder(manifest.load(tmp_path / "v.voc")).build()
    assert res.quality != "high"
    with pytest.raises(build.FitError):
        build.Builder(manifest.load(tmp_path / "v.voc")).build(auto_fit=False)


def test_split8k(tmp_path):
    sf.write(tmp_path / "w.wav", word(), FS)
    (tmp_path / "v.voc").write_text("rom_size: split8k\n0: w.wav\n")
    man = manifest.load(tmp_path / "v.voc")
    paths = build.write_outputs(build.Builder(man).build(), tmp_path / "o.bin", man.rom_size)
    assert [p.name for p in paths] == ["o_lo.bin", "o_hi.bin", "o_dictionary.json"]
    assert all(len(p.read_bytes()) == 8192 for p in paths[:2])
