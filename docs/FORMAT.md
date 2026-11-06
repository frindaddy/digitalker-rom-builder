# MM54104 Digitalker ROM and DVSS archive formats

This document describes the ROM and archive formats used by `dkrom`. The
codec description follows MAME's `src/devices/sound/digitalk.cpp`, written by
Olivier Galibert from chip analysis by Kevin Horton and Lord Nightmare. The
format has been checked against the DT1052 and DVSS ROMs, and against real-chip
recordings in the
[Digitalker Audio Archive](https://github.com/frindaddy/digitalker-audio-archive).

## ROM layout

The MM54104 addresses 16 KiB through A0–A13. The byte on SW0–7 selects one of
256 messages.

| Region | Contents |
|---|---|
| `0 .. 2N-1` | Pointer table. Each message has a big-endian 16-bit pointer at bytes `2n` and `2n+1`; only the low 14 bits are used. |
| next | Instruction lists, with 3 bytes per instruction |
| next | Speech data |
| rest | `0xFF` fill, as used by DVSS |

Each instruction has the following layout:

```
byte0     byte1     byte2
ERRRSSSS  AAAAAAAA  MMAAAAAA
```

- `E` marks the last instruction of the message.
- `R+1` is the repeat count, from 1 to 8.
- `S+1` is the number of waveforms, from 1 to 16.
- `A` is the 14-bit data address.
- `M` is the mode.

### Silence

An address of `A = 0` means silence. Its duration is
`(S+1)(R+1) * 5.12 ms`.

DVSS writes silence as `xx 00 40` (mode 1, address 0), and unused messages as
`80 00 00`. After the final `E` instruction, the chip adds 81.92 ms of
silence and then raises INTR.

### Timing

The 4 MHz crystal is divided by 4 for the DAC clock, so one tick is 1 us. A
waveform contains 128 DAC values. Each value is held for `PITCH_VALS[p]` us,
which ranges from 97 down to 40 us. A period therefore lasts 12.4–5.1 ms,
corresponding to pitches from 80 to 195 Hz.

Digichiver recordings run about 1.8% slow because the board's PWM clock is
about 3.93 MHz.

## Modes

| Mode | Use | Data per instruction |
|---|---|---|
| 0 | Voiced, `_/\_` | `S * 9` bytes: header plus 32 two-bit codes |
| 1 | Unknown with data; only used for silence | - |
| 2 | Voiced, `/\/\` (two cycles per period) | `S * 9` bytes |
| 3 | Unvoiced | 1 header byte plus `S * 32` bytes (128 codes each) |

### Voiced header byte

The first waveform uses the `VVVPPPPP` header. It gives the volume row and
pitch index, and each repeat of this waveform uses pitch `P`.

Later waveforms use `VVVDCCCC`:

- Repeat `r` (counting from 0) plays at pitch `prev +/- min(C, r+1)`, clamped
  to 0–31.
- `prev` is the pitch of the previous waveform's last repeat.
- When `D` is set, the pitch decreases.

### Voiced data

The two-bit codes are packed lowest bit-pair first. The accumulator runs
`d_k = d_{k-1} + DELTA1[prev | cur<<2]`, starting from `d_0 = 0` with previous
code 2.

The change depends only on the current code and bit 1 of the previous code.
The 4-bit accumulator wraps, so encoders must keep it within -7 to +8. The
output level is `+/-PCM_LEVELS[V][|d|-1]`, which is roughly `d * base(V)`.

The 32 codes (`d_1..d_32`) expand into 128 values as follows:

```
mode 0:  0*32, d1..d32, d32, d31..d0, 0*31
mode 2:  [d1..d32, d32, d31..d1] * 2
```

### Mode 3 data

`DELTA2[prev | cur<<2]` is simply `cur - prev`. Each value is therefore the
code minus the previous code at the start of the block, making mode 3 a
four-level PCM format.

The previous-code state carries from block to block, and the set of `S`
blocks repeats `R` times. MAME resets the accumulator at every block. `dkrom`
pins each block's last code to 1, so its output is the same either way.

### Usage in practice

National's own data uses only modes 0 and 3. Mode 2 appears in only a handful
of examples across all known ROM dumps.

## DVSS archive (STDARC)

The `NSC_DIGITALKER_CPM` disks on bitsavers are 8-inch, single-sided,
single-density CP/M images. Each disk has:

- 77 tracks, 26 sectors per track, and 128 bytes per sector.
- 2 reserved tracks.
- A sector skew of 6.
- 1 KiB blocks.

Use `tools/cpm_extract.py` to read the disk images.

### `STDARC.IDX`

The index is made of 512-byte blocks. Each block has an 8-byte header followed
by 21 records of 24 bytes:

```
u16le offset_lo, char name[14], char qualifier[3], "ose", u16le offset_hi
```

The full name is `name.qualifier`, for example `-ing.fs2` or `the.r`. The
index file also contains stale duplicate blocks left over on the disk.

### `STDARC.DAT`

At each offset, the data file contains:

```
u16le length, u16 0
5-byte entries until one has the end bit: v1 (ERRRSSSS), v3 (mode bits),
u16be blob id, n
for each non-silence entry: the whole blob, n+1 waveforms
```

An entry may play only the first `S` waveforms. Blob IDs are global, which
lets IBUILD share speech data between words.

`tools/stdarc.py` rebuilds every word as a one-message ROM. Of the 649 words
in the DVSS ROMs, 644 decode identically.

### How National encoded the words

Use `tools/stdarc.py --stats` to see the encoding statistics. Voiced
instructions mostly use `R = 2–3` and `S = 1–7`, with pitch slides of +/-1–3.
Noise instructions mostly use pitch code 8, `S = 1–2`, and `R = 1–2`, or
`R = 8` for long hiss.
