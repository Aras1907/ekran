# Ekran Charter

## Scope

Ekran is a GNOME desktop GUI for controlling external monitor settings via DDC/CI.
It targets the ARZOPA-27 external monitor on Fedora Linux (GNOME, NVIDIA + Intel hybrid).

## Tech Stack

- Python 3.10+
- GTK4 + libadwaita (PyGObject / gi)
- Backend: in-process ctypes calls to bundled `libddcutil`
- Config: JSON at `~/.config/ekran/settings.json` (source) or `~/.var/app/io.github.ekran.Ekran/config/ekran/settings.json` (flatpak)
- Build: `make run` (source) or `make flatpak-build` + `make flatpak-run` (flatpak)
- Flatpak: bundles libddcutil 2.2.1 + jansson inside sandbox, runtime `org.gnome.Platform//50`; ddcutil CLI is not installed

## Verified Facts

- ARZOPA-27 is on I2C bus `/dev/i2c-10`, DRM connector `card0-HDMI-A-1`
- `ddcutil detect` confirms VCP version 2.2 (DDC/CI supported)
- Laptop panel (CMN) does not support DDC/CI — filtered out
- ddcutil 2.2.1 is installed on the target machine

## VCP Codes

| Code | Name | Type |
|------|------|------|
| 0x10 | Brightness | Continuous |
| 0x12 | Contrast | Continuous |
| 0x16 | Red Gain | Continuous |
| 0x18 | Green Gain | Continuous |
| 0x1A | Blue Gain | Continuous |
| 0x14 | Color Preset | Choice (hidden from UI) |
| 0x60 | Input Source | Choice |
| 0xD6 | Power Mode | Choice |
| 0x87 | Sharpness | Continuous |
| 0x62 | Audio Volume | Continuous |
| 0x13 | Backlight Control | Continuous |
| 0x72 | Gamma | Continuous |
| 0x64 | Audio Microphone Volume | Continuous |
| 0x63 | Speaker Select | Choice |
| 0x66 | Ambient Light Sensor | Choice |
| 0x86 | Display Scaling | Choice |
| 0xAA | Screen Orientation | Choice |
| + | 6-axis Sat/Hue, Black Level, Backlight Level, Position/Size, etc. | Continuous |
| + | Display Usage Time, Firmware Level, Monitor Status, etc. | Info |

All codes are capability-gated — only rendered when the monitor exposes them via DDC/CI. Full MCCS set (~66 registry entries) covers every standard VCP feature ddcutil knows.

## Design Decisions

- Slider debounce: 150ms to avoid spamming ddcutil
- All ddcutil calls serialized with threading.Lock
- Config path: stdlib `os.environ.get("XDG_CONFIG_HOME")`, no pyxdg dependency
- Non-continuous VCP codes (input source, power mode) detected but no slider (omitted, not faked)
- All supported continuous controls visible directly (no Show More/Less toggle)
- Refresh rate from Mutter D-Bus `GetCurrentState` → `is-current` mode → `refresh-rate`; shown as "60 Hz" in list; fallback: omitted
- Permission-first banner: EACCES from ddcutil → banner shows error + 4 fix commands
- Background scan: detect runs in a thread, UI shows instantly, results arrive ~1-2s later
- Parser: only `Display N` sections parsed; "Invalid display" blocks skipped entirely
- Ground truth format: `DRM_connector:` (underscore), `Mfg id:` (not "Manufacturer"), `Model:` (not "Monitor:")
- Color Preset uses monitor-specific libddcutil metadata; RGB gain writes switch to User/Custom mode first
