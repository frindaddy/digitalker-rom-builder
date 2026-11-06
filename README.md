# Digitalker ROM Tools

`dkrom` builds speech ROMs for the National Semiconductor **MM54104 Digitalker**
from WAV or MP3 recordings. National never released its audio encoder, and the
Digitalker Voice Selection Software (DVSS) could only assemble ROMs from its
stock vocabulary. This tool lets you encode your own recordings instead.

The output is a 2–16 KiB ROM image that you can program into an EPROM or load
on a [Digichiver](https://github.com/frindaddy/digichiver) board. A matching
Digichiver dictionary JSON file is written alongside the image.

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
chip.

If the ROM does not have enough room, the build steps down through the quality
presets until all messages fit. Use `--no-auto-fit` to turn this off and get
an error instead. The error includes the encoded size of each message.

## Other commands

```sh
uv run dkrom preview clip.wav                   # hear one clip as the chip would say it
uv run dkrom inspect rom.bin [--dictionary d.json] [-v]
uv run dkrom decode rom.bin -o wavs/ [--dictionary d.json] [--filter]
uv run dkrom decode lo.bin hi.bin -o wavs/      # 8K + 8K pair
```

`preview` plays a recording as the chip would encode it. `inspect` shows
information about a ROM, and `decode` writes its messages as WAV files.
`--filter` applies the DT1050 datasheet output filter (194 Hz–7.2 kHz).

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

## How it works

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

See [docs/FORMAT.md](docs/FORMAT.md) for details about the ROM and DVSS
STDARC archive formats.

## Reference tools

- `tools/cpm_extract.py` extracts files from the DVSS CP/M disk images on
  bitsavers.
- `tools/stdarc.py` decodes National's STDARC word archive. It can also report
  statistics or write WAV files and ROM images.
- `tools/evaluate.py` calculates mel-cepstral distance between recordings
  for tuning.
