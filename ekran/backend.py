"""Backend layer — calls libddcutil via ctypes (no subprocess, no CLI)."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

from . import ddc

_lock = threading.Lock()


# ── VcpResult ───────────────────────────────────────────────────────

@dataclass
class VcpResult:
    current: int | None = None
    maximum: int | None = None
    choice_label: str | None = None
    choice_value: int | None = None
    info_text: str | None = None
    error: str = ""


# ── detect ──────────────────────────────────────────────────────────

def detect_displays() -> tuple[list[dict], str]:
    with _lock:
        return ddc.list_displays()


# ── capabilities ────────────────────────────────────────────────────

_CAP_FEATURE_RE = re.compile(r"Feature:\s*([0-9A-Fa-f]{2,4})\s*(?:\(([^)]*)\))?")


def get_capabilities(bus: str) -> dict[int, str]:
    with _lock:
        caps_raw = ddc.get_capabilities(bus)
    if not caps_raw:
        return {}
    caps = {}
    for m in _CAP_FEATURE_RE.finditer(caps_raw):
        code = int(m.group(1), 16)
        name = m.group(2) or f"Feature 0x{code:02X}"
        caps[code] = name
    return caps


# ── getvcp ──────────────────────────────────────────────────────────

def get_vcp(bus: str, code: int) -> VcpResult:
    with _lock:
        cur, maximum, _, _, _, err = ddc.get_vcp(bus, code)
    if err:
        return VcpResult(error=err)
    return VcpResult(current=cur, maximum=maximum)


# ── setvcp with pace + transient retry ──────────────────────────────

_TRANSIENT_MARKERS = (
    "busy", "locked", "retries", "null response", "all tries",
    "temporarily", "not ready", "ebusy", "flocked", "quiesced",
)


def _is_transient(err: str) -> bool:
    e = err.lower()
    return any(m in e for m in _TRANSIENT_MARKERS)


def set_vcp(bus: str, code: int, value: int) -> str:
    with _lock:
        err = ddc.set_vcp(bus, code, value)
        if err and _is_transient(err):
            time.sleep(0.5)
            err = ddc.set_vcp(bus, code, value)
        time.sleep(0.05)
        return err


def get_choices(bus: str, code: int) -> list[tuple[int, str]]:
    with _lock:
        return ddc.get_choice_values(bus, code)
