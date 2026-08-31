# Ekran

![Vibe Coded](https://img.shields.io/badge/vibe--coded-yes-blue)

GNOME desktop app to control external monitor brightness, contrast, and color via DDC/CI. Shows current refresh rate (Hz) from Mutter. **Vibe-coded** — built with AI assistance.

## Requirements

- Fedora Linux with GNOME
- Python 3.10+
- GTK4 + libadwaita (PyGObject)
- libddcutil (bundled in the flatpak; no host ddcutil or CLI required)

## Install dependencies (for running from source)

```bash
sudo dnf install python3-gobject gtk4 libadwaita
```

## i2c permissions (required)

By default, /dev/i2c-* devices are owned by root. You must add your user to the `i2c` group:

```bash
sudo groupadd --system i2c
sudo usermod -aG i2c $USER
echo 'SUBSYSTEM=="i2c-dev", KERNEL=="i2c-[0-9]*", GROUP="i2c", MODE="0660"' \
  | sudo tee /etc/udev/rules.d/90-i2c.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
# Log out and back in for group changes to take effect.
```

After logging back in, verify: `ddcutil detect` should list your monitors without errors.

## Run from source

Source execution requires a system `libddcutil.so`; the self-contained flatpak is the recommended build.

```bash
cd ~/development/Ekran
make
# or
python3 -m ekran.main
```

## Build as Flatpak

The flatpak bundles libddcutil + jansson inside the sandbox. It does not install or use the ddcutil CLI on the host.

```bash
# 1. Install flatpak tooling (one time, needs sudo)
sudo dnf install flatpak flatpak-builder
flatpak remote-add --user --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo

# 2. Build and install (first run downloads GNOME runtime ~1.5 GB)
make flatpak-build

# 3. After rebuilding, always quit the old instance first (single-instance app)
flatpak kill io.github.ekran.Ekran
make flatpak-run
```

## Config

Settings are saved at `~/.config/ekran/settings.json` (from source) or `~/.var/app/io.github.ekran.Ekran/config/ekran/settings.json` (flatpak). Stores last brightness/contrast per display and the selected monitor.

## Known limitations

- Laptop built-in panels are not supported (they lack DDC/CI).
- Only DDC/CI-capable monitors can be controlled.
- Input Source (0x60) and Power Mode (0xD6) are exposed as selectors when supported.
- Color Preset (0x14) uses the monitor's own preset table; RGB gain changes automatically switch from fixed temperature presets to User mode.
- The registry covers all standard MCCS 2.2 features (~66 entries). Controls only render when the monitor exposes them via DDC/CI.
- Some monitors may not support all VCP codes; unsupported codes are automatically hidden.
- i2c permissions must be configured regardless of running from source or flatpak.
- Refresh rate is queried from Mutter D-Bus; if unavailable (non-GNOME desktop), Hz is omitted from the display.
