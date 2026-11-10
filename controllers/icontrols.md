# iCON iControls — RGV CC map

Program Layer 1 (inspector + stack/clock) and Layer 2 (audio / mod) in iCON iMap so they send **distinct CCs on MIDI channel 2** (APC uses channel 1). Absolute knobs/faders. Transport as CC, not MMC.

APC mini mk2 and iControls are **alternate** factory profiles — one surface at a time for a show. Channel separation stays so either can be plugged in without relearning.

Two number spaces:

| Space | Meaning |
| --- | --- |
| **iMap** | Value typed in iCON iMap (raw MIDI CC/note byte 0–127) |
| **DAT / JSON** | MIDI In DAT Index = iMap + 1. This is what `MidiExt` / `icontrols.json` store |

Live MIDI Console is the source of truth. `controllers/icontrols.icon` is a best-effort iMap dump and **does not reliably round-trip both layers** — program from this table, then verify Indices in the console.

Macros 5–8 are UI / MIDI Learn only (no Layer 1 fader).

## Layer 1 — inspector + stack / clock

| Control | Msg | iMap | DAT / JSON | Destination |
| --- | --- | --- | --- | --- |
| Knobs 1–8 | CC | 1–8 | 2–9 | Factory slots 0–7 of selected module |
| Knob 9 | CC | 9 | 10 | Mix (noop until module mix address exists) |
| Faders 1–4 | CC | 11–14 | 12–15 | Layer 1–4 opacity |
| Faders 5–8 | CC | 15–18 | 16–19 | Macros 1–4 |
| Fader 9 | CC | 19 | 20 | Global mod amount |
| Top buttons 1–9 | Note | 36–44 | 37–45 | Select module (gen / fx1–6 / master / palette) |
| Bottom 1–4 | Note | 52–55 | 53–56 | Select layer 1–4 |
| Bottom 5 | Note | 56 | 57 | Blackout |
| Bottom 6 | Note | 57 | 58 | Freeze |
| Bottom 7 | Note | 58 | 59 | FX kill |
| Bottom 8 | Note | 59 | 60 | Tap |
| Bottom 9 | Note | 60 | 61 | Resync |

## Layer 2 — audio / modulation

| Control | Msg | iMap | DAT / JSON | Destination |
| --- | --- | --- | --- | --- |
| Knobs 1–8 | CC | 21–28 | 22–29 | LFO 1–8 rate |
| Knob 9 | CC | 29 | 30 | Audio input gain |
| Faders 1–8 | CC | 31–38 | 32–39 | Low/mid/high gain, attack, release, gate, onset, auto-gain |

## Transport (both layers)

| Control | Msg | iMap | DAT / JSON | Destination |
| --- | --- | --- | --- | --- |
| Record | CC | 45 | 46 | MIDI Learn toggle |
| Prev / next gen | CC | 46–47 | 47–48 | Half / double feel |
| Prev / next scene | CC | 48–49 | 49–50 | noop until Presets |
| Play / stop | CC | 50 | 51 | noop until clock wiring |

## Setup

1. In iMap, assign the **iMap** column values on channel 2 for Layer 1 and Layer 2 (including bottom notes 52–60).
2. Save to the device. Confirm in TD MIDI Console: Layer 1 Fader 1 → Index **12**, Layer 2 Fader 1 → Index **32**, Bottom 1 → Index **53**.
3. Optionally export `controllers/icontrols.icon` for reference only — do not trust it over this table or the console.
