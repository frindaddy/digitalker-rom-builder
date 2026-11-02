# digitalker-rom-builder

`dkrom` converts WAV/MP3 recordings into speech ROMs for the National
Semiconductor **MM54104 Digitalker**. It replaces the audio encoder that
National never released. DVSS could only assemble ROMs from its 500 stock words;
this tool encodes any recording you give it.

The output is a plain 2–16 KiB image. Program it into an EPROM, or load it on a
[digichiver](https://github.com/frindaddy/digichiver) board. A matching
digichiver dictionary JSON is written alongside it.

## Install

```sh
uv sync                     # Python 3.11+, numpy, scipy, soundfile (MP3 support built in)
uv run dkrom --help
```

## Build a ROM

Write a manifest. It is a plain-text file in the style of DVSS `.VOC` files:

```
# my vocabulary
rom_size: 16K          # 2K, 4K, 8K, 16K, or split8K (two 8K files, e.g. SSR/JE520 sockets)
quality: normal        # high, normal, low, tiny

1: hello.wav
2: world.mp3
3: hello.wav sil200 world.mp3 | hello world     # phrase; speech data is shared
4: "alarm tone.wav"[gain_db=-3,voiced_repeats=1] # per-clip encoder options
```

**Manifest syntax**
- **Message numbers** are 0–255, the value you put on SW0–7. Unused numbers play 5 ms of silence.
- **`silN`** inserts N ms of silence.
- **`| label`** names the message in the dictionary.
- **Paths** are relative to the manifest.

```sh
uv run dkrom build vocab.voc -o vocab.bin --wav-dir preview/
```

This writes `vocab.bin` and `vocab_dictionary.json`. With `--wav-dir`, it also
writes a WAV of every message, rendered by a bit-exact model of the chip.

**If the ROM is too full**
- The build steps down through the quality levels until everything fits.
- Pass `--no-auto-fit` to get an error instead.
- Either way, the error lists bytes per message.

## Other commands

```sh
uv run dkrom preview clip.wav                   # hear one clip as the chip would say it
uv run dkrom inspect rom.bin [--dictionary d.json] [-v]
uv run dkrom decode rom.bin -o wavs/ [--dictionary d.json] [--filter]
uv run dkrom decode lo.bin hi.bin -o wavs/      # 8K + 8K pair
```

`--filter` applies the DT1050 datasheet output filter (194 Hz – 7.2 kHz).

## Getting good results

- **Recordings:** use clean, close-miked speech with little reverb or background noise. Trim long pauses; leading and trailing silence is removed automatically.
- **Voice pitch:** the chip only plays pitches from 80 to 195 Hz. Lower voices are clamped to 80 Hz. Higher voices are moved down into range with their formants kept, so they still sound right but deeper. `preview --allow-even` tries mode 2, which supports up to about 390 Hz, but National never used it, so test it on hardware first.
- **Quality presets:** compared against National's own encoding of the same words, `high` matches its quality at about 3× the size. `low` is about the same size as National's, with slightly lower quality.

  | quality | voiced repeats | noise repeats | approx. bytes per second of sound |
  |---|---|---|---|
  | high | 1 | 1 | ~2000 |
  | normal | 2 | 3 | ~850 |
  | low | 3 | 4 | ~650 |
  | tiny | 4 | 4 (13 kHz) | ~470 |

## How it works

1. Find the pitch with YIN on the band below 1 kHz, and split the clip into voiced, unvoiced and silent parts.
2. For voiced parts, mark each pitch pulse and measure that period's harmonic magnitudes. Re-synthesize the period as a zero-phase, symmetric 128-sample waveform at the nearest chip pitch (Mozer's method). A Viterbi search then picks the 2-bit ADPCM codes and volume that best reproduce it.
3. Group periods into instructions with repeat counts and pitch slides.
4. Store unvoiced parts as mode-3 noise blocks, and silences as length-only instructions.
5. Lay out the pointer table, instructions and speech data, storing identical data only once.

[docs/FORMAT.md](docs/FORMAT.md) documents the ROM format and the DVSS STDARC archive format.

## Reference tools

- `tools/cpm_extract.py`: extract files from the DVSS CP/M disk images on bitsavers.
- `tools/stdarc.py`: decode National's STDARC word archive (`--stats`, `--wav`, `--rom`).
- `tools/evaluate.py`: mel-cepstral distance between recordings, for tuning.

## Tests

```sh
uv run pytest
```
