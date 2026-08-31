# Monitor Scan

How to run ddcutil detect/capabilities and parse output.

## Detect all monitors

```bash
ddcutil detect
```

Ground truth output format (ddcutil 2.2.x):

```
Invalid display
I2C bus: /dev/i2c-6
DRM_connector: card1-eDP-1
EDID synopsis:
Mfg id: CMN - Chimei Innolux Corporation
Model:
Product code: 5607 (0x15e7)
Serial number:
Binary serial number: 0 (0x00000000)
Manufacture year: 2016, Week: 33
This is a laptop display. Laptop displays do not support DDC/CI.

Display 1
I2C bus: /dev/i2c-10
DRM_connector: card0-HDMI-A-1
EDID synopsis:
Mfg id: ARZ - UNK
Model: ARZOPA-27
Product code: 9985 (0x2701)
Serial number:
Binary serial number: 0 (0x00000000)
Manufacture year: 2024, Week: 42
VCP version: 2.2
```

Key fields:
- `I2C bus:` → bus path
- `DRM_connector:` (underscore) → DRM connector name
- `Mfg id:` → manufacturer code + full name (first token = 3-letter code)
- `Model:` → monitor name (some displays use "Monitor:" instead)
- `Product code:` → product code number
- `VCP version:` → DDC/CI version

Sections without "Display N" heading (e.g. "Invalid display") are skipped.

## Filter laptop panels

Exclude displays where:
- DRM connector contains "eDP" (laptop internal displays)
- Manufacturer code (first token of "Mfg id") is in known laptop panel OEM list (CMN, LGD, SDC, BOE)

## Get capabilities

```bash
ddcutil capabilities -b <bus_number>
```

Output contains `Feature: XX (Name)` lines listing supported VCP codes.

## Get/set VCP values

```bash
ddcutil getvcp 10 -b <bus_number>      # current + max value
ddcutil setvcp 10 75 -b <bus_number>   # set brightness to 75
```

## Refresh rate

Refresh rate is NOT from ddcutil. Query via Mutter D-Bus:
`org.gnome.Mutter.DisplayConfig.GetCurrentState` → modes with `is-current` → `refresh-rate` (d).
