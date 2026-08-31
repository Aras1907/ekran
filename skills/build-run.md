# Build & Run

## Install dependencies (Fedora, for running from source)

```bash
sudo dnf install python3-gobject gtk4 libadwaita
```

The flatpak bundles `libddcutil`; source execution additionally needs a system `libddcutil.so`.

## Fix i2c permissions

```bash
sudo groupadd --system i2c
sudo usermod -aG i2c $USER
echo 'SUBSYSTEM=="i2c-dev", KERNEL=="i2c-[0-9]*", GROUP="i2c", MODE="0660"' \
  | sudo tee /etc/udev/rules.d/90-i2c.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
# Log out and back in
```

## Run from source

```bash
cd ~/development/Ekran
make
# or
python3 -m ekran.main
```

## Build as Flatpak

```bash
# Install tooling (one time)
sudo dnf install flatpak flatpak-builder
flatpak remote-add --user --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo

# Build (first run downloads GNOME 48 runtime ~1.5 GB)
make flatpak-build

# After every rebuild, quit the old instance first (single-instance app!)
flatpak kill io.github.ekran.Ekran
# Run
make flatpak-run

# Clean build artifacts
make flatpak-clean
```

## Verify

```bash
# Before running app, check brightness:
ddcutil getvcp 10 -b 10

# Move brightness slider in app, then verify:
ddcutil getvcp 10 -b 10

# Check config was saved:
cat ~/.config/ekran/settings.json
```

## Lint

No external linter configured. Run manually:
```bash
python3 -m py_compile ekran/main.py
python3 -m py_compile ekran/window.py
python3 -m py_compile ekran/backend.py
python3 -m py_compile ekran/monitors.py
python3 -m py_compile ekran/config.py
```
