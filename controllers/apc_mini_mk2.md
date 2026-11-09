# APC mini mk2 — RGV generic MIDI layout

Use the **generic MIDI** port. Disable Ableton’s APC mini mk2 control-surface script so RGV owns LED state.

Indices below are **MIDI In DAT 1-based** (1–128), matching `midi_map`.

## Notes

| Control | Note | Destination |
| --- | --- | --- |
| Hardware Shift | 99 | Shift latch |
| Clip-stop 1–4 | 65–68 | Select layer 1–4 |
| Scene 7 | 119 | MIDI Learn toggle |
| Scene 8 | 120 | Blackout |
| Pad col5–8 row7–8 | 57–64 | Tap, Resync, Half, Double, Freeze, FX kill*, Mutate*, Blackout |

\* noop until later phases.

Generator bank / scene / palette pads are stubbed as `noop` in the JSON profile.

## CCs (faders)

| Fader | CC | Destination |
| --- | --- | --- |
| 1–4 | 49–52 | Layer opacity (noop until Stack) |
| 5–8 | 53–56 | Macros 1–4 |
| 9 | 57 | Global mod amount |
