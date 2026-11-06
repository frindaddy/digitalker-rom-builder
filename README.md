# Digitalker ROM Tools

This repository contains tools for working with speech ROMs for the National
Semiconductor MM54104 Digitalker. Use them to build a ROM from your own
recordings, inspect or decode an existing ROM, or explore the original DVSS
speech archive and its CP/M disk images.

The main command-line program is `dkrom`. Supporting tools extract and decode
National's STDARC archive and compare recordings. Together, they cover both
creating new ROMs and understanding the ROMs and speech data that already
exist.

## Tools

| Tool | What it does |
|---|---|
| `dkrom build` | Encode WAV or MP3 recordings into a Digitalker ROM and dictionary. |
| `dkrom inspect` | List a ROM's messages, instructions, durations, and space usage. |
| `dkrom decode` | Render ROM messages to WAV files. |
| `dkrom preview` | Encode one recording and write a WAV preview of how the chip would say it. |
| `tools/cpm_extract.py` | Extract files from the DVSS CP/M disk images. |
| `tools/stdarc.py` | Read National's STDARC word archive, report encoding statistics, and export words as WAV or ROM files. |
| `tools/evaluate.py` | Compare recordings using mel-cepstral distance. |

The ROM and archive formats are described in [docs/FORMAT.md](docs/FORMAT.md).

## Install

Install the project and its dependencies with [uv](https://docs.astral.sh/uv/):

```sh
uv sync
uv run dkrom --help
```

Python 3.11 or later is required. The project uses NumPy, SciPy, and
SoundFile.

## Build a ROM

Start with a manifest. It is a plain-text file, similar to the DVSS `.VOC`
files:

```
# my vocabulary
rom_size: 16K          # 2K, 4K, 8K, 16K, or split8K (two 8K files, e.g. SSR/JE520 sockets)
quality: normal        # high, normal, low, tiny

1: hello.wav
2: world.mp3
3: hello.wav sil200 world.mp3 | hello world
4: "alarm tone.wav"[gain_db=-3,voiced_repeats=1]
```

Message numbers are 0–255, matching the value you put on SW0–7. Unused
numbers play 5 ms of silence. Use `silN` to insert N milliseconds of silence,
and `| label` to give a message a name in the dictionary. Paths are relative
to the manifest. A message can contain multiple clips, and speech data shared
between messages is stored only once.

Encoder options can be set for an individual clip in brackets after its path.
In the example above, the fourth message uses a gain of -3 dB and one voiced
repeat.

Build the ROM with:

```sh
uv run dkrom build vocab.voc -o vocab.bin --wav-dir preview/
```

This writes `vocab.bin` and `vocab_dictionary.json`. With `--wav-dir`, it also
writes a WAV preview of each message, rendered by a bit-exact model of the
chip. The output is a 2–16 KiB ROM image that you can program into an EPROM
or load on a [Digichiver](https://github.com/frindaddy/digichiver) board.

If the ROM does not have enough room, the build steps down through the quality
presets until all messages fit. Use `--no-auto-fit` to turn this off and get
an error instead. The error includes the encoded size of each message.

## Inspect and decode ROMs

`inspect` lists message durations, instruction counts, and remaining ROM
space. Add `-v` to see the individual instructions. `decode` writes messages
as WAV files; a Digichiver dictionary can be used to name the files. Both
commands accept a single ROM image or two files for an 8K + 8K pair.

```sh
uv run dkrom inspect rom.bin [--dictionary dictionary.json] [-v]
uv run dkrom decode rom.bin -o wavs/ [--dictionary dictionary.json] [--filter]
uv run dkrom decode lo.bin hi.bin -o wavs/
```

Use `--index` with `decode` to render only selected message numbers. `--filter`
applies the DT1050 datasheet output filter (194 Hz–7.2 kHz).

## Preview a recording

`preview` encodes one clip and writes a WAV file of how the chip would play
it:

```sh
uv run dkrom preview clip.wav
```

## Explore the DVSS archive

The helper tools work with the original Digitalker Voice Selection Software
(DVSS) archive. First extract the files from a CP/M disk image, then point
`stdarc.py` at the directory containing `STDARC.IDX` and `STDARC.DAT`:

```sh
python tools/cpm_extract.py nsc_digitalker_archive_disk.img extracted/
python tools/stdarc.py extracted/ --stats
```

`stdarc.py` can also export archived words as WAV files or build one-message
ROM images. Run `python tools/stdarc.py --help` for its options.

## Getting good results

- **Recordings:** Use clean, close-miked speech with little reverb or
  background noise. Trim long pauses; leading and trailing silence is removed
  automatically.
- **Voice pitch:** The chip plays pitches from 80 to 195 Hz. Lower voices are
  clamped to 80 Hz. Higher voices are moved down into range with their formants
  kept, so they still sound right but deeper. `preview --allow-even` tries
  mode 2, which supports pitches up to about 390 Hz. National did not use this
  mode, so test it on hardware first.
- **Quality presets:** Compared with National's encoding of the same words,
  `high` matches its quality at about three times the size. `low` is about the
  same size as National's encoding, with slightly lower quality.

  | Quality | Voiced repeats | Noise repeats | Approx. bytes per second of sound |
  |---|---:|---:|---:|
  | high | 1 | 1 | ~2000 |
  | normal | 2 | 3 | ~850 |
  | low | 3 | 4 | ~650 |
  | tiny | 4 | 4 (13 kHz) | ~470 |

## How ROM encoding works

`dkrom` finds the pitch with YIN below 1 kHz, then splits each clip into
voiced, unvoiced, and silent parts. For voiced audio, it marks each pitch
pulse and measures that period's harmonic magnitudes. It re-synthesizes each
period as a zero-phase, symmetric 128-sample waveform at the nearest chip
pitch, using Mozer's method. A Viterbi search picks the 2-bit ADPCM codes and
volume that best reproduce the waveform.

The encoder groups periods into instructions with repeat counts and pitch
slides. Unvoiced audio is stored as mode-3 noise blocks, and silence is stored
as length-only instructions. The ROM contains the pointer table, instructions,
and speech data; identical speech data is stored once.

## Tests

```sh
uv run pytest
```
