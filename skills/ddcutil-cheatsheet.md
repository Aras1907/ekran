# DDC/CI Cheatsheet (libddcutil)

Ekran calls the bundled `libddcutil` in-process. The CLI examples below are diagnostic commands only; the flatpak does not require or install the CLI.

## Common commands

```bash
ddcutil detect                              # list monitors
ddcutil environment                         # probe system setup
ddcutil capabilities -b <bus>               # monitor capabilities
ddcutil getvcp <code> -b <bus>              # read VCP value
ddcutil setvcp <code> <value> -b <bus>      # set VCP value
ddcutil scs -b <bus>                        # save current settings to monitor NVRAM
```

## Bus number

The `-b` flag takes the bus number only (e.g. `10`), not the full path (`/dev/i2c-10`).

## VCP codes

| Code | Name | Notes |
|------|------|-------|
| 0x10 | Brightness | 0–100 typically |
| 0x12 | Contrast | 0–100 typically |
| 0x16 | Red Gain | 0–255 typically |
| 0x18 | Green Gain | 0–255 typically |
| 0x1A | Blue Gain | 0–255 typically |
| 0x60 | Input Source | Non-continuous (1=VGA, 3=DP, 4=HDMI...) |
| 0xD6 | Power Mode | Non-continuous (1=on, 4=standby, 5=off) |

## Notes

- All calls serialized: never run two ddcutil commands on the same bus concurrently
- Ekran opens, uses, and closes one libddcutil display handle per operation
- Ekran serializes all DDC calls with one lock
- Non-continuous features have no `max value` in getvcp output
- Some monitors require `ddcutil scs` after setvcp for changes to persist
