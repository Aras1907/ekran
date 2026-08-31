"""Monitor discovery, filtering, Hz lookup, rates, refresh apply."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import backend
from .backend import VcpResult
from .controls import REGISTRY_BY_CODE, DENY_CODE, INITIAL_CODES, ControlKind, VCPDef

LAPTOP_MFGS = {"CMN", "LGD", "SDC", "BOE"}


@dataclass
class Control:
    code: int
    name: str
    kind: str
    group: str
    current: int | None = None
    maximum: int | None = None
    choice_label: str | None = None
    choice_value: int | None = None
    choices: dict[int, str] | None = None
    info_text: str | None = None
    bus: str = ""


@dataclass
class Monitor:
    bus: str
    connector: str
    name: str
    manufacturer: str
    model: str
    product_code: str
    serial: str
    vcp_version: str
    capabilities: dict[int, str]
    controls: list[Control] = field(default_factory=list)
    hz: float | None = None

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.connector:
            parts.append(self.connector)
        if self.hz is not None:
            parts.append(f"{self.hz:.0f} Hz")
        return " — ".join(parts)


def _is_external(display: dict) -> bool:
    conn = display.get("DRM connector", "")
    if "eDP" in conn:
        return False
    mfg_raw = display.get("Mfg id", "")
    mfg_code = mfg_raw.split()[0].upper() if mfg_raw else ""
    if mfg_code in LAPTOP_MFGS:
        return False
    return bool(display.get("I2C bus"))


def _classify(r: VcpResult, code: int, reg: VCPDef | None, caps_name: str) -> tuple[str, dict[int, str] | None]:
    if reg and reg.kind == ControlKind.INFO:
        kind = "info"
        if not r.info_text:
            r.info_text = r.choice_label or str(r.current or "")
        return kind, None
    if reg and reg.kind == ControlKind.CHOICE:
        return "choice", reg.choices
    if r.choice_value is not None:
        return "choice", reg.choices if reg else None
    if r.maximum is not None and r.maximum > 0 and r.maximum < 10000:
        return "continuous", None
    if r.info_text:
        return "info", None
    return "info", None


def _build_controls(bus: str, caps: dict[int, str], codes: set[int]) -> list[Control]:
    code_list = sorted(c for c in codes if c not in DENY_CODE and (c in caps or c in REGISTRY_BY_CODE))
    if not code_list:
        return []
    controls = []
    for code in code_list:
        r = backend.get_vcp(bus, code)
        if r.error:
            continue
        reg = REGISTRY_BY_CODE.get(code)
        caps_name = caps.get(code, f"Feature 0x{code:02X}")
        if reg and reg.kind == ControlKind.CHOICE:
            kind = "choice"
            choices = reg.choices or dict(backend.get_choices(bus, code))
        else:
            kind, choices = _classify(r, code, reg, caps_name)
        choice_value = r.choice_value
        choice_label = r.choice_label
        if kind == "choice" and choice_value is None:
            choice_value = r.current
        if kind == "choice" and choice_label is None and choices and choice_value is not None:
            choice_label = choices.get(choice_value, f"Value 0x{choice_value:02X}")
        controls.append(Control(
            code=code, name=reg.name if reg else caps_name,
            kind=kind, group=reg.group if reg else "Advanced",
            current=r.current, maximum=r.maximum,
            choice_label=choice_label, choice_value=choice_value,
            choices=choices, info_text=r.info_text, bus=bus,
        ))
    return controls


def probe_bus(bus: str) -> tuple[dict[int, str], list[Control]]:
    """Probe one known bus without running a full display scan."""
    caps = backend.get_capabilities(bus)
    codes = INITIAL_CODES & (set(caps.keys()) | REGISTRY_BY_CODE.keys())
    return caps, _build_controls(bus, caps, codes)


def discover_monitors() -> tuple[list[Monitor], str]:
    raw, err = backend.detect_displays()
    if err:
        return [], err
    external = [d for d in raw if _is_external(d)]
    monitors = []
    for d in external:
        bus = d.get("I2C bus", "")
        caps = backend.get_capabilities(bus)
        codes = INITIAL_CODES & (set(caps.keys()) | REGISTRY_BY_CODE.keys())
        controls = _build_controls(bus, caps, codes)
        monitors.append(Monitor(
            bus=bus, connector=d.get("DRM connector", ""),
            name=d.get("Monitor", "Unknown"),
            manufacturer=d.get("Mfg id", ""),
            model=d.get("Model", ""),
            product_code=d.get("Product code", ""),
            serial=d.get("Serial number", ""),
            vcp_version=d.get("VCP version", ""),
            capabilities=caps, controls=controls,
        ))
    return monitors, ""


def scan_all_features(mon: Monitor) -> None:
    codes = set(mon.capabilities.keys()) | REGISTRY_BY_CODE.keys()
    mon.controls = _build_controls(mon.bus, mon.capabilities, codes)


# ── Hz + rates + refresh apply (main-thread Gio only) ──────────────

def fetch_rates_and_hz(monitors: list[Monitor]) -> dict[str, dict]:
    """ONE GetCurrentState round-trip. Sets mon.hz for each monitor.
    Returns {model_name: {"rates": [float], "current_w": int, "current_h": int}}.
    Main-thread only. On exception: return {} (Hz/rates omitted gracefully)."""
    try:
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        proxy = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None,
            "org.gnome.Mutter.DisplayConfig",
            "/org/gnome/Mutter/DisplayConfig",
            "org.gnome.Mutter.DisplayConfig", None,
        )
        result = proxy.call_sync("GetCurrentState", None, Gio.DBusCallFlags.NONE, 3000, None)
        _serial, monitors_list, _logical, _props = result

        hz_map: dict[str, float] = {}
        out: dict[str, dict] = {}
        for mon in monitors_list:
            conn_info, modes, _mon_props = mon
            model_name = conn_info[2]
            cur_w = cur_h = 0
            cur_rate = 0.0
            for mode in modes:
                _mid, w, h, rate = mode[0], int(mode[1]), int(mode[2]), mode[3]
                mode_props = mode[6]
                if isinstance(mode_props, dict) and mode_props.get("is-current", False):
                    cur_w, cur_h = w, h
                    cur_rate = round(rate, 2)
                    break
            if cur_w == 0:
                continue
            rates = sorted(set(
                round(mode[3], 2)
                for mode in modes
                if int(mode[1]) == cur_w and int(mode[2]) == cur_h
            ))
            out[model_name] = {"rates": rates, "current_w": cur_w, "current_h": cur_h}
            hz_map[model_name] = cur_rate

        # Set Hz on monitor objects
        for m in monitors:
            if m.name in hz_map:
                m.hz = hz_map[m.name]

        return out
    except Exception:
        return {}


def apply_refresh_rate(model_name: str, target_rate: float) -> str:
    """Apply refresh rate via Mutter ApplyMonitorsConfig. Main-thread only.
    Returns "" on success or error string."""
    try:
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        proxy = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None,
            "org.gnome.Mutter.DisplayConfig",
            "/org/gnome/Mutter/DisplayConfig",
            "org.gnome.Mutter.DisplayConfig", None,
        )

        # 1. Get fresh state
        result = proxy.call_sync("GetCurrentState", None, Gio.DBusCallFlags.NONE, 3000, None)
        serial, monitors_list, logical_list, props = result

        # 2. Find target mode_id for the requested model
        target_mode_id = None
        for mon in monitors_list:
            conn_info, modes, _mon_props = mon
            if conn_info[2] != model_name:
                continue
            # Find current w/h
            cur_w = cur_h = 0
            cur_mode_id = None
            for mode in modes:
                _mid, w, h, _rate, _scale, _scales, mode_props = mode
                if isinstance(mode_props, dict) and mode_props.get("is-current", False):
                    cur_w, cur_h = int(w), int(h)
                    cur_mode_id = mode[0]
                    break
            if cur_w == 0:
                continue
            # Find mode with target rate at same resolution
            for mode in modes:
                mid, w, h, rate = mode[0], int(mode[1]), int(mode[2]), mode[3]
                if w == cur_w and h == cur_h and abs(rate - target_rate) < 0.5:
                    target_mode_id = mid
                    break
            break

        if target_mode_id is None:
            return f"Mode {target_rate}Hz not found for {model_name}"

        # 3. Build ApplyMonitorsConfig payload
        # logical_monitors: a(iiduba(ssa{sv})) — mirror current, swap mode_id
        # No need for GetResources — we only need connector + mode_id per monitor

        # Build logical_monitors variant for Apply
        logical_builder = GLib.VariantBuilder.new(GLib.VariantType.new("a(iiduba(ssa{sv}))"))
        for lm in logical_list:
            x, y, scale, _transform, primary, lm_monitors, lm_props = lm
            # Build monitors array: a(ssa{sv})
            mon_builder = GLib.VariantBuilder.new(GLib.VariantType.new("a(ssa{sv})"))
            for lm_mon in lm_monitors:
                # lm_mon is (ssss) from GetCurrentState: (connector, vendor, model, serial)
                conn = lm_mon[0]
                empty_props = GLib.VariantBuilder(GLib.VariantType("a{sv}")).end()
                # If this is the target monitor, use target mode_id
                if conn == _find_connector_for_model(monitors_list, model_name):
                    # Actually mode goes into the second 's' of (ssa{sv})
                    mon_builder.add_value(GLib.Variant.new_tuple(
                        GLib.Variant.new_string(conn),
                        GLib.Variant.new_string(target_mode_id),
                        empty_props,
                    ))
                else:
                    # Keep current mode
                    cur_mode = _current_mode_id(monitors_list, conn)
                    mon_builder.add_value(GLib.Variant.new_tuple(
                        GLib.Variant.new_string(conn),
                        GLib.Variant.new_string(cur_mode or ""),
                        empty_props,
                    ))
            # lm: (i, i, d, u, b, a(ssa{sv}))
            logical_builder.add_value(GLib.Variant.new_tuple(
                GLib.Variant.new_int32(x),
                GLib.Variant.new_int32(y),
                GLib.Variant.new_double(scale),
                GLib.Variant.new_uint32(0),  # transform
                GLib.Variant.new_boolean(primary),
                mon_builder.end(),
            ))

        # 4. Apply
        method = 0  # temporary
        empty_props = GLib.VariantBuilder(GLib.VariantType("a{sv}")).end()
        proxy.call_sync(
            "ApplyMonitorsConfig",
            GLib.Variant.new_tuple(
                GLib.Variant.new_uint32(serial),
                GLib.Variant.new_uint32(method),
                logical_builder.end(),
                empty_props,
            ),
            Gio.DBusCallFlags.NONE, 5000, None,
        )
        return ""
    except Exception as e:
        return str(e)


def _find_connector_for_model(monitors_list, model_name: str) -> str:
    for mon in monitors_list:
        if mon[0][2] == model_name:
            return mon[0][0]
    return ""


def _current_mode_id(monitors_list, connector: str) -> str | None:
    for mon in monitors_list:
        if mon[0][0] == connector:
            for mode in mon[1]:
                mode_props = mode[6]
                if isinstance(mode_props, dict) and mode_props.get("is-current", False):
                    return mode[0]
    return None
