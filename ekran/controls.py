"""VCP control registry, curated tables, presets, baseline restore."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ControlKind(Enum):
    CONTINUOUS = "continuous"
    CHOICE = "choice"
    INFO = "info"


@dataclass
class VCPDef:
    code: int
    name: str
    kind: ControlKind
    group: str
    choices: dict[int, str] | None = None


# ── Curated MCCS value tables ──────────────────────────────────────

_INPUT_SOURCE = {
    0x01: "Video 1", 0x02: "Video 2", 0x03: "Video 3",
    0x04: "Video 4", 0x05: "Video 5", 0x06: "Video 6",
    0x07: "Video 7", 0x08: "Video 8", 0x09: "Video 9",
    0x0A: "Video 10", 0x0B: "Video 11", 0x0C: "Video 12",
    0x0D: "Video 13", 0x0E: "Video 14", 0x0F: "Video 15",
    0x10: "DisplayPort 1", 0x11: "HDMI-1", 0x12: "HDMI-2",
    0x13: "DisplayPort 2", 0x14: "DisplayPort 3", 0x15: "DisplayPort 4",
    0x1B: "VGA",
}

_POWER_MODE = {
    0x01: "On", 0x02: "Standby", 0x03: "Suspend",
    0x04: "Off", 0x05: "Hard Off",
}

# ── Registry (OSD rows removed — auto-synced at startup) ───────────

VCP_REGISTRY: list[VCPDef] = [
    # ── Image ───────────────────────────────────────────────────────
    VCPDef(0x10, "Brightness", ControlKind.CONTINUOUS, "Image"),
    VCPDef(0x12, "Contrast", ControlKind.CONTINUOUS, "Image"),
    # ── Color ───────────────────────────────────────────────────────
    VCPDef(0x16, "Red Gain", ControlKind.CONTINUOUS, "Color"),
    VCPDef(0x18, "Green Gain", ControlKind.CONTINUOUS, "Color"),
    VCPDef(0x1A, "Blue Gain", ControlKind.CONTINUOUS, "Color"),
    VCPDef(0x14, "Color Preset", ControlKind.CHOICE, "Color"),
    # ── Advanced ────────────────────────────────────────────────────
    VCPDef(0x87, "Sharpness", ControlKind.CONTINUOUS, "Advanced"),
    VCPDef(0x62, "Audio Volume", ControlKind.CONTINUOUS, "Advanced"),
    VCPDef(0x60, "Input Source", ControlKind.CHOICE, "Advanced", _INPUT_SOURCE),
    VCPDef(0xD6, "Power Mode", ControlKind.CHOICE, "Advanced", _POWER_MODE),
    # ── Info ────────────────────────────────────────────────────────
    VCPDef(0xAC, "Horizontal Frequency", ControlKind.INFO, "Info"),
    VCPDef(0xAE, "Vertical Frequency", ControlKind.INFO, "Info"),
    VCPDef(0xDF, "VCP Version", ControlKind.INFO, "Info"),
    VCPDef(0xB2, "Sub-pixel Layout", ControlKind.INFO, "Info"),
    VCPDef(0xB6, "Technology Type", ControlKind.INFO, "Info"),
]

REGISTRY_BY_CODE: dict[int, VCPDef] = {d.code: d for d in VCP_REGISTRY}
DENY_CODE = {0x02, 0x04, 0x05, 0x06, 0x08, 0x0B, 0x0C, 0x52,
             0xC6, 0xC8, 0xCA, 0xCC, 0xFD, 0xFF}
INITIAL_CODES = {d.code for d in VCP_REGISTRY} - DENY_CODE

# ── OSD language auto-sync ─────────────────────────────────────────

LANG_TO_OSD = {
    "en": 0x02, "fr": 0x03, "de": 0x04, "es": 0x05, "it": 0x06,
    "pt": 0x07, "nl": 0x08, "sv": 0x0A, "fi": 0x0B, "da": 0x0C,
    "ru": 0x0D, "pl": 0x0E, "cs": 0x10, "hu": 0x11, "tr": 0x1F,
    "ja": 0x42, "ko": 0x43, "zh": 0x44,
}

# ── Presets: relative factors from baseline (brightness/contrast only) ──

PRESETS: dict[str, dict] = {
    "Gaming":  {"b_factor": 1.15, "c_factor": 1.10, "sharp": None, "refresh": "highest"},
    "Movie":   {"b_factor": 1.05, "c_factor": 1.00, "sharp": 3,    "refresh": 60},
    "Work":    {"b_factor": 0.90, "c_factor": 0.95, "sharp": 2,    "refresh": 60},
    "Reset":   None,
}

_B_CODE, _C_CODE, _S_CODE = 0x10, 0x12, 0x87


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def apply_preset(bus: str, preset_name: str, baseline: dict[int, int],
                 controls: list, rates: list[float] | None = None) -> list[str]:
    from . import backend
    errors = []
    preset = PRESETS.get(preset_name)
    if preset is None:
        for code, val in baseline.items():
            err = backend.set_vcp(bus, code, val)
            if err:
                errors.append(f"0x{code:02X}: {err}")
        return errors

    bv = baseline.get(_B_CODE, 50)
    err = backend.set_vcp(bus, _B_CODE, _clamp(round(bv * preset["b_factor"]), 0, 100))
    if err:
        errors.append(f"Brightness: {err}")

    cv = baseline.get(_C_CODE, 50)
    err = backend.set_vcp(bus, _C_CODE, _clamp(round(cv * preset["c_factor"]), 0, 100))
    if err:
        errors.append(f"Contrast: {err}")

    if preset["sharp"] is not None:
        if any(c.code == _S_CODE and c.kind == "continuous" for c in controls):
            err = backend.set_vcp(bus, _S_CODE, preset["sharp"])
            if err:
                errors.append(f"Sharpness: {err}")
    return errors


def capture_baseline(controls: list) -> dict[int, int]:
    return {c.code: c.current for c in controls
            if c.kind == "continuous" and c.current is not None}


def group_controls(controls: list) -> dict[str, list]:
    out: dict[str, list] = {}
    for c in controls:
        out.setdefault(c.group, []).append(c)
    return out


# ── Self-tests ──────────────────────────────────────────────────────

def _preset_selftest():
    rates = [60.0, 120.0, 144.0]
    g = PRESETS["Gaming"]
    assert (max(rates) if g["refresh"] == "highest" else min(rates, key=lambda r: abs(r - g["refresh"]))) == 144.0
    m = PRESETS["Movie"]
    assert (max(rates) if m["refresh"] == "highest" else min(rates, key=lambda r: abs(r - m["refresh"]))) == 60.0
    assert PRESETS["Reset"] is None

    @dataclass
    class _MC:
        code: int; kind: str; current: int | None = None
    bl = capture_baseline([_MC(0x10, "continuous", 55), _MC(0x12, "continuous", 50)])
    assert bl == {0x10: 55, 0x12: 50}
    print("Preset self-test PASSED")


def _group_selftest():
    @dataclass
    class _MC:
        code: int; name: str; kind: str; group: str
    controls = [
        _MC(0x10, "Brightness", "continuous", "Image"),
        _MC(0x12, "Contrast", "continuous", "Image"),
        _MC(0x16, "Red Gain", "continuous", "Color"),
        _MC(0x14, "Color Preset", "choice", "Color"),
        _MC(0x87, "Sharpness", "continuous", "Advanced"),
        _MC(0xDF, "VCP Version", "info", "Info"),
    ]
    g = group_controls(controls)
    assert set(g.keys()) == {"Image", "Color", "Advanced", "Info"}
    assert len(g["Image"]) == 2
    assert len(g["Color"]) == 2  # RGB gain + Color Preset
    assert len(g["Advanced"]) == 1
    assert len(g["Info"]) == 1
    print("Group self-test PASSED")


if __name__ == "__main__":
    _preset_selftest()
    _group_selftest()
