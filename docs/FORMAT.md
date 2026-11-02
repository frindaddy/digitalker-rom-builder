# MM54104 Digitalker ROM and DVSS archive format

Notes behind `dkrom`. The codec description follows MAME's
`src/devices/sound/digitalk.cpp` (Olivier Galibert, from chip analysis by Kevin
Horton and Lord Nightmare). It has been checked here against the DT1052 and
DVSS ROMs and against real-chip recordings from
[digitalker-audio-archive](https://github.com/frindaddy/digitalker-audio-archive).

## ROM layout

The chip addresses 16 KiB (A0–A13). The SW0–7 byte selects message 0–255.

| Region | Contents |
|---|---|
| `0 .. 2N-1` | Pointer table: big-endian 16-bit, message *n* at bytes `2n`, `2n+1`, low 14 bits used |
| next | Instruction lists, 3 bytes per instruction |
| next | Speech data |
| rest | `0xFF` fill (DVSS) |

Instruction bytes:

```
byte0     byte1     byte2
ERRRSSSS  AAAAAAAA  MMAAAAAA
```

- `E`: last instruction of the message.
- `R+1`: repeats, 1–8.
- `S+1`: waveforms, 1–16.
- `A`: 14-bit data address.
- `M`: mode.

**Silence**
- `A = 0` means silence lasting `(S+1)(R+1) × 5.12 ms`.
- DVSS writes silence as `xx 00 40` (mode 1, address 0), and writes `80 00 00` for unused messages.
- After the `E` instruction the chip adds 81.92 ms of silence, then raises INTR.

**Timing**
- The DAC clock is the 4 MHz crystal divided by 4, so one tick is 1 µs.
- Each waveform is 128 DAC values. Each value is held `PITCH_VALS[p]` µs, which ranges from 97 down to 40 µs.
- A period therefore lasts 12.4–5.1 ms (80–195 Hz).
- The digichiver recordings run about 1.8% slow, because their PWM clock is about 3.93 MHz.

## Modes

| Mode | Use | Data per instruction |
|---|---|---|
| 0 | voiced, `_/\_` | `S × 9` bytes: header + 32 two-bit codes |
| 1 | unknown with data; only used for silence | — |
| 2 | voiced, `/\/\` (two cycles per period) | `S × 9` bytes |
| 3 | unvoiced | 1 header byte + `S × 32` bytes (128 codes each) |

**Voiced header byte**
- First waveform: `VVVPPPPP`, giving the volume row and pitch index. Every repeat of this waveform uses pitch `P`.
- Later waveforms: `VVVDCCCC`.
  - Repeat *r* (counting from 0) plays at pitch `prev ± min(C, r+1)`, clamped to 0–31.
  - `prev` is the pitch of the previous waveform's last repeat.
  - `D` set means the pitch decreases.

**Voiced data**
- The 2-bit codes are packed lowest bit-pair first.
- The accumulator runs `d_k = d_{k-1} + DELTA1[prev | cur<<2]`, starting from `d_0 = 0` with previous code 2.
- The change depends only on the current code and bit 1 of the previous code.
- The 4-bit accumulator wraps, so encoders must keep it within -7..+8.
- Output level is `±PCM_LEVELS[V][|d|-1]`, which is roughly `d × base(V)`.

How the 32 codes (`d_1..d_32`) expand into 128 values:

```
mode 0:  0×32, d1..d32, d32, d31..d0, 0×31
mode 2:  [d1..d32, d32, d31..d1] × 2
```

**Mode 3 data**
- `DELTA2[prev | cur<<2]` is simply `cur - prev`, so each value is `code - (previous code at the start of the block)`. This makes mode 3 four-level PCM.
- The previous-code state carries from block to block, and the set of `S` blocks repeats `R` times.
- MAME resets the accumulator at every block. `dkrom` pins each block's last code to 1, so its output is the same whether or not the real chip does this.

**Usage in practice**
- National's own data uses only modes 0 and 3.
- Mode 2 shows up only a handful of times across all known ROM dumps.

## DVSS archive (STDARC)

The `NSC_DIGITALKER_CPM` disks on bitsavers are 8" SSSD CP/M images:
- 77 tracks × 26 sectors × 128 bytes.
- 2 reserved tracks.
- Skew 6.
- 1 KiB blocks.

`tools/cpm_extract.py` reads them.

**`STDARC.IDX`**
- 512-byte blocks, each an 8-byte header followed by 21 records of 24 bytes:
  ```
  u16le offset_lo, char name[14], char qualifier[3], "ose", u16le offset_hi
  ```
- The full name is `name.qualifier`, for example `-ing.fs2` or `the.r`.
- The file also contains stale duplicate blocks left over on the disk.

**`STDARC.DAT`** at each offset:

```
u16le length, u16 0
5-byte entries until one has the end bit:  v1 (ERRRSSSS), v3 (mode bits), u16be blob id, n
for each non-silence entry: the whole blob, n+1 waveforms (an entry may play only the first S)
```

Blob ids are global, which is what lets IBUILD share speech data between words.

`tools/stdarc.py` rebuilds every word as a one-message ROM. Of the 649 words in the DVSS ROMs, 644 decode identically.

**How National encoded the words** (`tools/stdarc.py --stats`)
- Voiced instructions mostly use R = 2–3 and S = 1–7, with pitch slides of ±1–3.
- Noise instructions mostly use pitch code 8, S = 1–2 and R = 1–2, or R = 8 for long hiss.
