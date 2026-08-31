"""ctypes bindings to the bundled libddcutil — no subprocess, no text parsing."""

from __future__ import annotations

import ctypes
from ctypes import (
    c_int, c_uint8, c_uint16, c_char, c_void_p, c_bool,
    c_char_p, Structure, POINTER, byref, cast,
)
import threading

# ── Structs (pinned from ddcutil 2.2.1 public headers) ─────────────

class _IO_Path(Structure):
    _fields_ = [("path_type", c_int), ("i2c_busno", c_int)]

class _MCCS_Version(Structure):
    _fields_ = [("major", c_uint8), ("minor", c_uint8)]

class _Display_Info(Structure):
    _fields_ = [
        ("marker", c_char * 4),
        ("dispno", c_int),
        ("path", _IO_Path),
        ("usb_bus", c_int),
        ("usb_device", c_int),
        ("mfg_id", c_char * 4),
        ("model_name", c_char * 14),
        ("sn", c_char * 14),
        ("product_code", c_uint16),
        ("edid_bytes", c_uint8 * 128),
        ("vcp_version", _MCCS_Version),
        ("dref", c_void_p),
    ]

class _NonTableVcpValue(Structure):
    _fields_ = [
        ("mh", c_uint8), ("ml", c_uint8),
        ("sh", c_uint8), ("sl", c_uint8),
    ]

class _FeatureValueEntry(Structure):
    _fields_ = [
        ("value_code", c_uint8),
        ("value_name", c_char_p),
    ]

class _FeatureMetadata(Structure):
    _fields_ = [
        ("marker", c_char * 4),
        ("feature_code", c_uint8),
        ("vcp_version", _MCCS_Version),
        ("feature_flags", c_uint16),
        ("sl_values", POINTER(_FeatureValueEntry)),
        ("unused", c_void_p),
        ("feature_name", c_char_p),
        ("feature_desc", c_char_p),
    ]

# ── Library load ────────────────────────────────────────────────────

try:
    _LIB = ctypes.CDLL("/app/lib/libddcutil.so.5.3.0")
except OSError:
    import ctypes.util as _cu
    _path = _cu.find_library("ddcutil")
    if not _path:
        _path = _cu.find_library("ddcutil.so.5")
    if _path:
        _LIB = ctypes.CDLL(_path)
    else:
        raise RuntimeError(
            "libddcutil not found. In flatpak: bundled at /app/lib/. "
            "From source: install ddcutil package (provides libddcutil.so)."
        )

# ── argtypes / restype (prevents silent coercion) ──────────────────

_LIB.ddca_init.argtypes = [ctypes.c_char_p, c_int, c_int]
_LIB.ddca_init.restype = c_int
_LIB.ddca_enable_verify.argtypes = [c_bool]
_LIB.ddca_enable_verify.restype = c_bool
_LIB.ddca_get_display_refs.argtypes = [c_bool, POINTER(c_void_p)]
_LIB.ddca_get_display_refs.restype = c_int
_LIB.ddca_get_display_info.argtypes = [c_void_p, POINTER(c_void_p)]
_LIB.ddca_get_display_info.restype = c_int
_LIB.ddca_free_display_info.argtypes = [c_void_p]
_LIB.ddca_free_display_info.restype = None
_LIB.ddca_open_display2.argtypes = [c_void_p, c_bool, POINTER(c_void_p)]
_LIB.ddca_open_display2.restype = c_int
_LIB.ddca_close_display.argtypes = [c_void_p]
_LIB.ddca_close_display.restype = c_int
_LIB.ddca_get_non_table_vcp_value.argtypes = [c_void_p, c_uint8, POINTER(_NonTableVcpValue)]
_LIB.ddca_get_non_table_vcp_value.restype = c_int
_LIB.ddca_set_non_table_vcp_value.argtypes = [c_void_p, c_uint8, c_uint8, c_uint8]
_LIB.ddca_set_non_table_vcp_value.restype = c_int
_LIB.ddca_get_capabilities_string.argtypes = [c_void_p, POINTER(c_void_p)]
_LIB.ddca_get_capabilities_string.restype = c_int
_LIB.ddca_get_feature_metadata_by_dh.argtypes = [c_uint8, c_void_p, c_bool, POINTER(c_void_p)]
_LIB.ddca_get_feature_metadata_by_dh.restype = c_int
_LIB.ddca_free_feature_metadata.argtypes = [c_void_p]
_LIB.ddca_free_feature_metadata.restype = None
_LIB.ddca_rc_desc.argtypes = [c_int]
_LIB.ddca_rc_desc.restype = ctypes.c_char_p

# ── Init ────────────────────────────────────────────────────────────

_LIB.ddca_init(None, 0, 1)       # DDCA_INIT_OPTIONS_DISABLE_CONFIG_FILE
_LIB.ddca_enable_verify(False)

# ── Handle management: open-use-close per call ──────────────────────
# ddcutil does not allow two threads to hold the same display open
# simultaneously. Callers serialize with backend._lock, and each call
# opens, uses, then closes — so no handle ever lingers across threads.

def _open_display(bus: str) -> c_void_p:
    """Open a display handle for one operation. Caller must close it."""
    list_ptr = c_void_p()
    status = _LIB.ddca_get_display_refs(True, byref(list_ptr))
    if status != 0 or not list_ptr:
        raise RuntimeError(f"ddca_get_display_refs failed: {_rc_desc(status)}")
    arr = cast(list_ptr.value, POINTER(c_void_p))

    bus_int = int(bus.split("-")[-1]) if "-" in bus else int(bus)
    target_ref = None
    for i in range(128):
        ref = arr[i]
        if not ref:
            break
        info_ptr = c_void_p()
        st = _LIB.ddca_get_display_info(ref, byref(info_ptr))
        if st == 0 and info_ptr:
            info = cast(info_ptr.value, POINTER(_Display_Info)).contents
            if info.path.i2c_busno == bus_int:
                target_ref = info.dref
            _LIB.ddca_free_display_info(info_ptr)
        if target_ref:
            break

    if not target_ref:
        raise RuntimeError(f"No display on bus {bus}")

    handle = c_void_p()
    status = _LIB.ddca_open_display2(target_ref, False, byref(handle))
    if status != 0:
        raise RuntimeError(f"ddca_open_display2 failed: {_rc_desc(status)}")
    return handle


def _close_display(handle: c_void_p):
    try:
        _LIB.ddca_close_display(handle)
    except Exception:
        pass


def _rc_desc(status: int) -> str:
    try:
        ptr = _LIB.ddca_rc_desc(status)
        return ptr.decode("utf-8", errors="replace") if ptr else f"status={status}"
    except Exception:
        return f"status={status}"


# ── Public API ──────────────────────────────────────────────────────

LAPTOP_MFGS = {"CMN", "LGD", "SDC", "BOE"}


def list_displays() -> tuple[list[dict], str]:
    """Enumerate external monitors. Returns (list_of_display_dicts, error_string)."""
    list_ptr = c_void_p()
    status = _LIB.ddca_get_display_refs(True, byref(list_ptr))
    if status != 0 or not list_ptr:
        return [], _rc_desc(status)

    arr = cast(list_ptr.value, POINTER(c_void_p))
    displays = []

    for i in range(128):
        ref = arr[i]
        if not ref:
            break

        info_ptr = c_void_p()
        status = _LIB.ddca_get_display_info(ref, byref(info_ptr))
        if status != 0 or not info_ptr:
            continue

        info = cast(info_ptr.value, POINTER(_Display_Info)).contents

        mfg = info.mfg_id.decode("utf-8", errors="replace").rstrip("\x00").strip()
        model = info.model_name.decode("utf-8", errors="replace").rstrip("\x00").strip()
        sn = info.sn.decode("utf-8", errors="replace").rstrip("\x00").strip()
        busno = info.path.i2c_busno
        product_code = info.product_code
        vcp_version = f"{info.vcp_version.major}.{info.vcp_version.minor}"

        _LIB.ddca_free_display_info(info_ptr)

        if mfg.upper() in LAPTOP_MFGS:
            continue
        if busno <= 0:
            continue

        displays.append({
            "I2C bus": f"/dev/i2c-{busno}",
            "DRM connector": f"card?-{busno}",
            "Monitor": model or "Unknown",
            "Mfg id": mfg,
            "Product code": str(product_code),
            "Serial number": sn,
            "VCP version": vcp_version,
        })

    return displays, ""


def get_vcp(bus: str, code: int) -> tuple:
    """Get VCP value. Returns (current, max, choice_label, choice_value, info_text, error)."""
    try:
        handle = _open_display(bus)
    except Exception as e:
        return None, None, None, None, None, str(e)
    try:
        valrec = _NonTableVcpValue()
        status = _LIB.ddca_get_non_table_vcp_value(handle, c_uint8(code), byref(valrec))
        if status != 0:
            return None, None, None, None, None, _rc_desc(status)

        current = (valrec.sh << 8) | valrec.sl
        maximum = (valrec.mh << 8) | valrec.ml
        return current, maximum, None, None, None, ""
    finally:
        _close_display(handle)


def set_vcp(bus: str, code: int, value: int) -> str:
    try:
        handle = _open_display(bus)
    except Exception as e:
        return str(e)
    try:
        hi = (value >> 8) & 0xFF
        lo = value & 0xFF
        status = _LIB.ddca_set_non_table_vcp_value(handle, c_uint8(code), c_uint8(hi), c_uint8(lo))
        if status != 0:
            return _rc_desc(status)
        return ""
    finally:
        _close_display(handle)


def get_capabilities(bus: str) -> str:
    """Get raw MCCS capabilities string."""
    try:
        handle = _open_display(bus)
    except Exception:
        return ""
    try:
        caps_ptr = c_void_p()
        status = _LIB.ddca_get_capabilities_string(handle, byref(caps_ptr))
        if status != 0 or not caps_ptr:
            return ""
        try:
            caps = ctypes.string_at(caps_ptr).decode("utf-8", errors="replace")
        except Exception:
            caps = ""
        try:
            ctypes.CDLL("libc.so.6").free(caps_ptr)
        except Exception:
            pass
        return caps
    finally:
        _close_display(handle)


def get_choice_values(bus: str, code: int) -> list[tuple[int, str]]:
    """Return the monitor-specific simple non-continuous value table."""
    try:
        handle = _open_display(bus)
    except Exception:
        return []
    try:
        metadata_ptr = c_void_p()
        status = _LIB.ddca_get_feature_metadata_by_dh(
            c_uint8(code), handle, True, byref(metadata_ptr)
        )
        if status != 0 or not metadata_ptr:
            return []
        try:
            metadata = cast(metadata_ptr.value, POINTER(_FeatureMetadata)).contents
            if not metadata.sl_values:
                return []
            values = []
            for i in range(256):
                entry = metadata.sl_values[i]
                if entry.value_code == 0 and not entry.value_name:
                    break
                if entry.value_name:
                    label = entry.value_name.decode("utf-8", errors="replace").strip()
                else:
                    label = f"Value 0x{entry.value_code:02X}"
                values.append((int(entry.value_code), label))
            return values
        finally:
            _LIB.ddca_free_feature_metadata(metadata_ptr)
    finally:
        _close_display(handle)


def _selftest():
    displays, error = list_displays()
    assert not error, error
    assert displays, "No external displays found"
    result = get_vcp(displays[0]["I2C bus"], 0x10)
    assert not result[-1], result[-1]
    assert result[0] is not None and result[1] is not None
    choices = get_choice_values(displays[0]["I2C bus"], 0x14)
    assert choices, "No Color Preset values found"
    print("ddc self-test PASSED")


if __name__ == "__main__":
    _selftest()
