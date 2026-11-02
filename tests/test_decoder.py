from pathlib import Path

import pytest

from dkrom import codec, decoder
from dkrom.codec import Instruction

FIXTURES = Path(__file__).parent / "fixtures"
ROMS = ["DT1052", "DVSSROM1", "DVSSROM2", "DVSSROM3", "DVSSROM4", "DVSSROM5"]
COUNTS = {"DT1052": 17, "DVSSROM1": 144, "DVSSROM2": 133, "DVSSROM3": 129,
          "DVSSROM4": 140, "DVSSROM5": 108}


def load(name):
    return (FIXTURES / f"{name}.bin").read_bytes()


@pytest.mark.parametrize("raw", [b"\x21\xce\x0d", b"\x80\x00\x00", b"\x30\x00\x40", b"\x00\xf2\xcd"])
def test_instruction_roundtrip(raw):
    assert Instruction.unpack(raw).pack() == raw


def test_instruction_fields():
    ins = Instruction.unpack(b"\x00\xf2\xcd")
    assert (ins.addr, ins.mode, ins.count, ins.repeats, ins.last) == (0x0DF2, 3, 1, 1, False)


@pytest.mark.parametrize("name", ROMS)
def test_all_messages_parse(name):
    rom = load(name)
    n = decoder.message_count(rom)
    assert n == COUNTS[name]
    for i in range(n):
        prog = decoder.parse_message(rom, i)
        assert prog[-1].last
        for ins in prog:
            assert ins.mode != 1 or ins.is_silence
            assert ins.addr + ins.data_size() <= len(rom)


def test_dt1052_silence_ladder():
    rom = load("DT1052")
    ms = []
    for i in range(11, 17):
        frames = decoder.message_frames(rom, i)
        ms.append(round(sum(f.duration for f in frames[:-1]) / 1000))
    assert ms == [20, 41, 82, 164, 323, 655]


def test_codes_roundtrip():
    codes = [i % 4 for i in range(32)]
    assert codec.unpack_codes(codec.pack_codes(codes)) == codes


def test_voiced_waveform_shapes():
    d = codec.voiced_trajectory([3] * 4 + [0] * 4 + [2] * 24)
    odd = codec.voiced_waveform(d, codec.MODE_VOICED_ODD)
    even = codec.voiced_waveform(d, codec.MODE_VOICED_EVEN)
    assert len(odd) == len(even) == 128
    assert odd[:32] == [0] * 32 and odd[-31:] == [0] * 31
    assert even[:64] == even[64:]


def test_render_lengths():
    rom = load("DT1052")
    frames = decoder.message_frames(rom, 1)
    assert len(decoder.render_frames(frames)) == sum(f.duration for f in frames)


@pytest.mark.parametrize("units", [1, 4, 8, 17, 128, 200, 1000])
def test_silence_builder(units):
    prog = codec.silence(units, last=True)
    assert sum(i.count * i.repeats for i in prog) == units
    assert prog[-1].last and all(i.is_silence for i in prog)
