"""Main application window — pages, presets, refresh, auto-OSD."""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib, Gio

from . import backend, config
from .monitors import (
    Monitor, Control, discover_monitors, fetch_rates_and_hz,
    apply_refresh_rate, scan_all_features, probe_bus,
)
from .controls import (
    INITIAL_CODES, DENY_CODE, PRESETS, LANG_TO_OSD,
    apply_preset, capture_baseline, group_controls, _B_CODE, _C_CODE,
)

_PERMISSION_HINT = (
    "To fix i2c permissions, run these commands\n"
    "in a terminal, then log out and back in:\n"
    "  sudo groupadd --system i2c\n"
    "  sudo usermod -aG i2c $USER\n"
    '  echo \'SUBSYSTEM=="i2c-dev", KERNEL=="i2c-[0-9]*", GROUP="i2c", MODE="0660"\' | '
    "sudo tee /etc/udev/rules.d/90-i2c.rules\n"
    "  sudo udevadm control --reload-rules && sudo udevadm trigger"
)


class Window(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="Ekran",
                         default_width=900, default_height=650)

        self._monitors: list[Monitor] = []
        self._scan_lock = threading.Lock()
        self._scanning = False
        self._osd_synced = False
        self._rates_data: dict[str, dict] = {}
        self._baselines: dict[str, dict[int, int]] = config.load_config().get("baselines", {})

        # ── Toast overlay ───────────────────────────────────────────
        self._toast_overlay = Adw.ToastOverlay()
        self.set_content(self._toast_overlay)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._toast_overlay.set_child(root)

        # ── Header ──────────────────────────────────────────────────
        self._header = Adw.HeaderBar()
        refresh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._spinner = Gtk.Spinner()
        self._spinner.set_size_request(16, 16)
        self._spinner.set_visible(False)
        self._refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        self._refresh_btn.set_tooltip_text("Refresh (F5)")
        self._refresh_btn.connect("clicked", lambda _: self._start_scan())
        refresh_box.append(self._spinner)
        refresh_box.append(self._refresh_btn)
        self._header.pack_start(refresh_box)

        self._header.set_title_widget(Gtk.Label(label="Ekran", css_classes=["title"]))

        menu = Gio.Menu()
        menu.append("Load Config", "win.load-config")
        menu.append("Save Config", "win.save-config")
        menu.append("Scan All Features", "win.scan-all")
        menu.append("About", "win.about")
        self._menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self._menu_btn.set_menu_model(menu)
        self._header.pack_end(self._menu_btn)
        root.append(self._header)

        # ── Main area ───────────────────────────────────────────────
        self._outer_stack = Gtk.Stack()
        self._outer_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._outer_stack.set_vexpand(True)

        self._status_page = Adw.StatusPage()
        self._status_page.set_icon_name("video-display-symbolic")
        self._status_page.set_title("No External Monitors")
        self._status_page.set_description("Connect a DDC/CI monitor or fix i2c permissions.")
        self._outer_stack.add_named(self._status_page, "empty")

        page_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._view_stack = Adw.ViewStack()
        self._view_stack.set_vexpand(True)
        page_box.append(self._view_stack)
        self._switcher = Adw.ViewSwitcherBar()
        self._switcher.set_stack(self._view_stack)
        page_box.append(self._switcher)
        self._outer_stack.add_named(page_box, "pages")

        root.append(self._outer_stack)

        # ── Actions ─────────────────────────────────────────────────
        for name, fn in [("refresh", self._start_scan), ("load-config", self._load_config),
                         ("save-config", self._save_config), ("scan-all", self._scan_all),
                         ("about", self._show_about)]:
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda _action, _param, fn=fn: fn())
            self.add_action(act)
        app.set_accels_for_action("win.refresh", ["F5", "<Ctrl>r"])

        self._controls_stack_state("empty")
        self._start_theme_watcher()
        self._fast_path_then_scan()

    # ── Fast path + full scan ───────────────────────────────────────

    def _fast_path_then_scan(self):
        cfg = config.load_config()
        cached_bus = cfg.get("selected_bus")
        if cached_bus:
            thread = threading.Thread(target=self._run_fast_probe, args=(cached_bus,), daemon=True)
            thread.start()
        else:
            self._start_scan()

    def _run_fast_probe(self, bus: str):
        try:
            caps, controls = probe_bus(bus)
            if controls:
                mon = Monitor(bus=bus, connector="", name="Loading…",
                              manufacturer="", model="", product_code="",
                              serial="", vcp_version="", capabilities=caps,
                              controls=controls)
                GLib.idle_add(self._apply_fast_result, mon)
        except Exception:
            pass
        GLib.idle_add(self._start_scan)

    def _apply_fast_result(self, mon: Monitor):
        self._monitors = [mon]
        rates = fetch_rates_and_hz(self._monitors)
        if mon.bus not in self._baselines:
            self._baselines[mon.bus] = capture_baseline(mon.controls)
            self._save_baselines()
        self._rebuild_pages(rates)
        self._outer_stack.set_visible_child_name("pages")
        # Apply theme if follow enabled
        cfg = config.load_config()
        if self._baselines.get(mon.bus) and cfg.get("theme", {}).get(mon.bus, {}).get("follow", False):
            self._apply_theme(mon)

    # ── Pages ───────────────────────────────────────────────────────

    def _rebuild_pages(self, rates_data: dict | None = None):
        """Rebuild all monitor pages. rates_data = precomputed from fetch_rates_and_hz.
        No D-Bus calls allowed in this method."""
        if rates_data is not None:
            self._rates_data = rates_data

        pages = self._view_stack.get_pages()
        children = []
        for i in range(pages.get_n_items()):
            page_obj = pages.get_item(i)
            child = self._view_stack.get_child_by_name(page_obj.get_name())
            if child:
                children.append(child)
        for child in children:
            self._view_stack.remove(child)

        for mon in self._monitors:
            if mon.bus not in self._baselines:
                self._baselines[mon.bus] = capture_baseline(mon.controls)
            page_content = self._build_page(mon)
            page = self._view_stack.add_titled(page_content, str(mon.bus), str(mon.name))
            page.set_icon_name("video-display-symbolic")
            page._mon = mon

        self._switcher.set_visible(self._view_stack.get_pages().get_n_items() > 1)

        if self._monitors:
            self._outer_stack.set_visible_child_name("pages")
            self._view_stack.set_visible_child_name(str(self._monitors[0].bus))
        else:
            self._outer_stack.set_visible_child_name("empty")

    def _build_page(self, mon: Monitor) -> Gtk.Widget:
        prefs = Adw.PreferencesPage()
        baseline = self._baselines.get(mon.bus, {})
        groups = group_controls(mon.controls)

        # ── Image ───────────────────────────────────────────────────
        ctrls = groups.get("Image", [])
        if ctrls:
            grp = Adw.PreferencesGroup(title="Image")
            for ctrl in ctrls:
                if ctrl.kind == "continuous":
                    grp.add(self._make_slider_row(ctrl, mon))
            if grp.get_first_child():
                prefs.add(grp)

        # ── Display: refresh rate ────────────────────────────────────
        rd = self._rates_data.get(mon.name, {})
        rates = rd.get("rates", [])
        if len(rates) > 1:
            grp = Adw.PreferencesGroup(title="Display")
            rate_combo = Adw.ComboRow(
                title="Refresh Rate",
                model=Gtk.StringList.new([f"{r:.0f} Hz" for r in rates]),
            )
            cur_idx = next((i for i, r in enumerate(rates) if mon.hz and abs(r - mon.hz) < 0.5), 0)
            rate_combo.set_selected(cur_idx)
            rate_combo.connect("notify::selected", self._on_rate_changed, mon, rates)
            grp.add(rate_combo)
            prefs.add(grp)

        # ── Color ───────────────────────────────────────────────────
        ctrls = groups.get("Color", [])
        if ctrls:
            grp = Adw.PreferencesGroup(title="Color")
            for ctrl in ctrls:
                if ctrl.code == 0x14:
                    continue  # Hide Color Preset dropdown; auto-switch still works
                if ctrl.kind == "continuous":
                    grp.add(self._make_slider_row(ctrl, mon))
                elif ctrl.kind == "choice":
                    grp.add(self._make_combo_row(ctrl))
                elif ctrl.kind == "info":
                    grp.add(self._make_info_row(ctrl))
            if grp.get_first_child():
                prefs.add(grp)

        # ── Advanced ────────────────────────────────────────────────
        ctrls = groups.get("Advanced", [])
        if ctrls:
            grp = Adw.PreferencesGroup(title="Advanced")
            for ctrl in ctrls:
                if ctrl.kind == "continuous":
                    grp.add(self._make_slider_row(ctrl, mon))
                elif ctrl.kind == "choice":
                    grp.add(self._make_combo_row(ctrl))
            if grp.get_first_child():
                prefs.add(grp)

        # ── Quick Modes ─────────────────────────────────────────────
        mode_grp = Adw.PreferencesGroup(title="Quick Modes")
        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mode_box.set_halign(Gtk.Align.CENTER)
        for name in ["Gaming", "Movie", "Work", "Reset"]:
            btn = Gtk.Button(label=name, css_classes=["suggested-action"])
            btn.set_size_request(100, -1)
            btn.connect("clicked", self._on_preset_clicked, mon, name)
            mode_box.append(btn)
        mode_grp.add(mode_box)
        prefs.add(mode_grp)

        # ── Theme (dark/light mode) ──────────────────────────────────
        if baseline:
            theme_cfg = config.load_config().get("theme", {}).get(mon.bus, {})
            follow = theme_cfg.get("follow", False)
            detected_dark = self._is_dark_theme()
            mode_label = "Dark" if detected_dark else "Light"

            theme_grp = Adw.PreferencesGroup(title="Theme")
            follow_row = Adw.SwitchRow(title="Follow system theme")
            follow_row.set_active(follow)
            follow_row.set_subtitle(f"Currently: {mode_label}")
            follow_row.connect("notify::active", self._on_theme_switch, mon)
            theme_grp.add(follow_row)

            if theme_grp.get_first_child():
                prefs.add(theme_grp)

        # ── Info ────────────────────────────────────────────────────
        ctrls = groups.get("Info", [])
        if ctrls:
            grp = Adw.PreferencesGroup(title="Info")
            for ctrl in ctrls:
                subtitle = ctrl.info_text or ctrl.choice_label or str(ctrl.current or "")
                row = Adw.ActionRow(title=ctrl.name, subtitle=subtitle)
                row.set_icon_name("dialog-information-symbolic")
                row.set_activatable(False)
                row.add_css_class("property")
                grp.add(row)
            if grp.get_first_child():
                prefs.add(grp)

        return prefs

    # ── Row builders ────────────────────────────────────────────────

    def _make_slider_row(self, ctrl: Control, mon: Monitor) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        outer.set_margin_top(4)
        outer.set_margin_bottom(4)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        lbl = Gtk.Label(label=ctrl.name, xalign=0, hexpand=True, css_classes=["caption"])
        val_lbl = Gtk.Label(
            label=str(ctrl.current) if ctrl.current is not None else "—",
            xalign=1, css_classes=["caption", "dim-label"],
        )
        header.append(lbl)
        header.append(val_lbl)
        outer.append(header)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, ctrl.maximum or 100, 1)
        scale.set_value(ctrl.current or 0)
        scale.set_hexpand(True)
        scale.set_draw_value(False)
        scale.connect("value-changed", self._on_slider_changed, ctrl, val_lbl, mon)
        outer.append(scale)
        row = Adw.PreferencesRow()
        row.set_child(outer)
        ctrl._scale = scale
        return row

    def _on_slider_changed(self, scale, ctrl, val_lbl, mon):
        val = int(scale.get_value())
        val_lbl.set_label(str(val))
        tid_name = f"_tid_{ctrl.code}"
        old = getattr(self, tid_name, None)
        if old:
            GLib.source_remove(old)
        tid = GLib.timeout_add(150, self._apply_slider, ctrl.code, val, ctrl.bus, mon)
        setattr(self, tid_name, tid)

    def _apply_slider(self, code, value, bus, mon: Monitor) -> bool:
        setattr(self, f"_tid_{code}", None)
        if code in {0x16, 0x18, 0x1A, 0x87} and not self._ensure_user_color_mode(mon):
            return False
        err = backend.set_vcp(bus, code, value)
        if err:
            self._toast(f"setvcp 0x{code:02X}: {err}")
            self._schedule_recover_scan()
        else:
            # Theme-aware: store into active profile if follow enabled
            if code in {_B_CODE, _C_CODE}:
                theme_cfg = config.load_config().get("theme", {}).get(bus, {})
                if theme_cfg.get("follow", False):
                    active = "dark" if self._is_dark_theme() else "light"
                    profile = theme_cfg.setdefault(active, {})
                    key = "brightness" if code == _B_CODE else "contrast"
                    profile[key] = value
                    self._save_theme_cfg(bus, theme_cfg)
        return False

    def _ensure_user_color_mode(self, mon: Monitor) -> bool:
        """Switch from a fixed color preset before writing RGB gains."""
        preset = next((c for c in mon.controls if c.code == 0x14), None)
        if preset is None:
            self._toast("Select a User color preset to adjust RGB gains")
            return False

        current_label = (preset.choices or {}).get(
            preset.choice_value, preset.choice_label or ""
        ).lower()
        if "user" in current_label or "custom" in current_label:
            return True

        choices = preset.choices or {}
        user = next(
            ((value, label) for value, label in choices.items()
             if "user" in label.lower() or "custom" in label.lower()),
            None,
        )
        if user is None:
            self._toast("Select a User color preset to adjust RGB gains")
            return False

        value, label = user
        err = backend.set_vcp(mon.bus, 0x14, value)
        if err:
            if not getattr(preset, "_auto_switched", False):
                self._toast(f"Color preset: {err}")
            self._schedule_recover_scan()
            return False
        preset.choice_value = value
        preset.choice_label = label
        preset._auto_switched = True
        combo = getattr(preset, "_combo", None)
        if combo is not None:
            raw_values = getattr(combo, "_raw_values", [])
            if value in raw_values:
                combo._suppress_change = True
                try:
                    combo.set_selected(raw_values.index(value))
                finally:
                    combo._suppress_change = False
        if not getattr(preset, "_toast_shown", False):
            preset._toast_shown = True
            self._toast(f"Switched to {label} color mode")
        return True

    def _make_info_row(self, ctrl: Control) -> Gtk.Widget:
        subtitle = ctrl.info_text or ctrl.choice_label or str(ctrl.current or "")
        row = Adw.ActionRow(title=ctrl.name, subtitle=subtitle)
        row.set_icon_name("dialog-information-symbolic")
        row.set_activatable(False)
        row.add_css_class("property")
        return row

    def _make_combo_row(self, ctrl: Control) -> Gtk.Widget:
        model = Gtk.StringList.new()
        options = ctrl.choices or {}
        raw_values = sorted(options.keys())
        if ctrl.choice_value is not None and ctrl.choice_value not in options:
            raw_values.append(ctrl.choice_value)
        value_to_idx: dict[int, int] = {}
        for i, rv in enumerate(raw_values):
            model.append(options.get(rv, ctrl.choice_label or f"Value 0x{rv:02X}"))
            value_to_idx[rv] = i
        combo = Adw.ComboRow(title=ctrl.name, model=model)
        if ctrl.choice_value is not None and ctrl.choice_value in value_to_idx:
            combo.set_selected(value_to_idx[ctrl.choice_value])
        combo.connect("notify::selected", self._on_combo_changed, ctrl, raw_values)
        combo._raw_values = raw_values
        ctrl._combo = combo
        return combo

    def _on_combo_changed(self, combo, _pspec, ctrl, raw_values):
        if getattr(combo, "_suppress_change", False):
            return
        idx = combo.get_selected()
        if idx < 0 or idx >= len(raw_values):
            return
        new_val = raw_values[idx]
        err = backend.set_vcp(ctrl.bus, ctrl.code, new_val)
        if err:
            self._toast(f"setvcp 0x{ctrl.code:02X}: {err}")
            self._schedule_recover_scan()
        else:
            ctrl.choice_value = new_val
            ctrl.choice_label = (ctrl.choices or {}).get(new_val, ctrl.choice_label or f"Value 0x{new_val:02X}")

    # ── Refresh rate ────────────────────────────────────────────────

    def _on_rate_changed(self, combo, _pspec, mon, rates):
        idx = combo.get_selected()
        if idx < 0 or idx >= len(rates):
            return
        target = rates[idx]
        err = apply_refresh_rate(mon.name, target)
        if err:
            self._toast(f"Refresh rate: {err}")
        else:
            mon.hz = target
            self._toast(f"Refresh rate set to {target:.0f} Hz")

    # ── Presets + Reset ─────────────────────────────────────────────

    def _on_preset_clicked(self, btn, mon, preset_name):
        baseline = self._baselines.get(mon.bus, {})
        rates = self._rates_data.get(mon.name, {}).get("rates", [])
        errors = apply_preset(mon.bus, preset_name, baseline, mon.controls)

        preset = PRESETS.get(preset_name)
        if preset is not None and preset.get("refresh") is not None and rates:
            target_refresh = preset["refresh"]
            target = max(rates) if target_refresh == "highest" else min(rates, key=lambda r: abs(r - target_refresh))
            err = apply_refresh_rate(mon.name, target)
            if err:
                errors.append(f"Refresh: {err}")
            else:
                mon.hz = target

        self._toast(f"Preset '{preset_name}' applied" if not errors else f"Preset '{preset_name}': {'; '.join(errors)}")
        self._rebuild_pages()

    # ── Baseline ────────────────────────────────────────────────────

    def _save_baselines(self):
        cfg = config.load_config()
        cfg["baselines"] = {k: {str(c): v for c, v in bl.items()}
                             for k, bl in self._baselines.items()}
        config.save_config(cfg)

    # ── Scan ────────────────────────────────────────────────────────

    def _start_scan(self):
        with self._scan_lock:
            if self._scanning:
                return
            self._scanning = True
        self._spinner.set_visible(True)
        self._spinner.start()
        threading.Thread(target=self._run_scan, daemon=True).start()

    def _run_scan(self):
        try:
            monitors, err = discover_monitors()
        except Exception as e:
            monitors, err = [], str(e)
        GLib.idle_add(self._apply_results, monitors, err)

    def _apply_results(self, monitors: list[Monitor], err: str):
        self._scanning = False
        self._spinner.stop()
        self._spinner.set_visible(False)
        self._monitors = monitors

        rates = fetch_rates_and_hz(self._monitors) if self._monitors else {}
        self._rebuild_pages(rates)

        if not self._osd_synced and self._monitors:
            self._osd_synced = True
            threading.Thread(target=self._sync_osd_language_bg, daemon=True).start()

        # Apply dark/light theme for any monitor with follow enabled
        if self._monitors:
            cfg = config.load_config()
            for m in self._monitors:
                if self._baselines.get(m.bus) and cfg.get("theme", {}).get(m.bus, {}).get("follow", False):
                    self._apply_theme(m)

        if not self._monitors:
            self._outer_stack.set_visible_child_name("empty")
            if err:
                prefix = "DDC/CI Access Blocked" if ("EACCES" in err or "Permission denied" in err) else "Detection Failed"
                suffix = f"\n\n{_PERMISSION_HINT}" if prefix == "DDC/CI Access Blocked" else ""
                self._status_page.set_title(prefix)
                self._status_page.set_description(err + suffix)
            else:
                self._status_page.set_title("No External Monitors")
                self._status_page.set_description(
                    "Connect a DDC/CI monitor or fix i2c permissions.")

    def _sync_osd_language_bg(self):
        try:
            from gi.repository import GLocale
            lang = GLocale.get_current().split(".")[0].split("_")[0]
            val = LANG_TO_OSD.get(lang)
            if val is not None:
                for mon in self._monitors:
                    backend.set_vcp(mon.bus, 0xCC, val)
        except Exception:
            pass

    # ── Scan all ────────────────────────────────────────────────────

    def _scan_all(self):
        with self._scan_lock:
            if self._scanning:
                return
            self._scanning = True
        self._spinner.set_visible(True)
        self._spinner.start()
        threading.Thread(target=self._run_scan_all, daemon=True).start()

    def _run_scan_all(self):
        try:
            for mon in self._monitors:
                scan_all_features(mon)
        except Exception:
            pass
        GLib.idle_add(self._scan_all_done)

    def _scan_all_done(self):
        self._scanning = False
        self._spinner.stop()
        self._spinner.set_visible(False)
        self._rebuild_pages()

    # ── Theme (dark/light mode) ──────────────────────────────────────

    _DARK_B_FACTOR = 0.70
    _DARK_C_FACTOR = 0.90

    @staticmethod
    def _is_dark_theme() -> bool:
        """Detect GNOME dark mode via GSettings."""
        try:
            settings = Gio.Settings.new("org.gnome.desktop.interface")
            scheme = settings.get_string("color-scheme")
            if "dark" in scheme:
                return True
            theme = settings.get_string("gtk-theme")
            return "dark" in theme.lower()
        except Exception:
            return False

    def _on_theme_switch(self, switch, _pspec, mon):
        active = switch.get_active()
        theme_cfg = config.load_config().get("theme", {}).get(mon.bus, {})
        theme_cfg["follow"] = active
        if active and ("light" not in theme_cfg or "dark" not in theme_cfg):
            # Init both profiles from current physical values
            bv, cv = 50, 50
            r = backend.get_vcp(mon.bus, _B_CODE)
            if r.maximum is not None:
                bv = r.current or bv
            r2 = backend.get_vcp(mon.bus, _C_CODE)
            if r2.maximum is not None:
                cv = r2.current or cv
            theme_cfg["light"] = {"brightness": bv, "contrast": cv}
            theme_cfg["dark"] = {"brightness": bv, "contrast": cv}
        self._save_theme_cfg(mon.bus, theme_cfg)
        if active:
            self._apply_theme(mon)
        # If OFF: leave current physical values as-is (user owns the sliders)

    def _apply_theme(self, mon):
        """Apply the active dark/light profile values to brightness/contrast."""
        theme_cfg = config.load_config().get("theme", {}).get(mon.bus, {})
        active = "dark" if self._is_dark_theme() else "light"
        profile = theme_cfg.get(active, {})
        bv = profile.get("brightness")
        cv = profile.get("contrast")
        if bv is None or cv is None:
            return
        err_b = backend.set_vcp(mon.bus, _B_CODE, max(0, min(100, bv)))
        err_c = backend.set_vcp(mon.bus, _C_CODE, max(0, min(100, cv)))
        # Update slider widgets + ctrl.current
        for ctrl in mon.controls:
            if ctrl.code == _B_CODE and not err_b:
                ctrl.current = bv
                scale = getattr(ctrl, "_scale", None)
                if scale:
                    scale.set_value(bv)
            elif ctrl.code == _C_CODE and not err_c:
                ctrl.current = cv
                scale = getattr(ctrl, "_scale", None)
                if scale:
                    scale.set_value(cv)

    def _save_theme_cfg(self, bus: str, theme_cfg: dict):
        cfg = config.load_config()
        cfg.setdefault("theme", {})[bus] = theme_cfg
        config.save_config(cfg)

    def _start_theme_watcher(self):
        """Watch GNOME color-scheme changes; apply theme when monitored."""
        try:
            settings = Gio.Settings.new("org.gnome.desktop.interface")
            settings.connect("changed::color-scheme", self._on_system_theme_changed)
        except Exception:
            pass

    def _on_system_theme_changed(self, settings, key):
        for mon in self._monitors:
            theme_cfg = config.load_config().get("theme", {}).get(mon.bus, {})
            if theme_cfg.get("follow", False):
                self._apply_theme(mon)

    # ── Recovery: re-scan after a transient DDC failure ───────────────

    def _schedule_recover_scan(self):
        """After a transient set failure, re-scan values once the monitor recovers."""
        if self._scanning:
            return
        GLib.timeout_add(3000, lambda: (self._start_scan(), False)[1])

    # ── Config ──────────────────────────────────────────────────────

    def _current_mon(self) -> Monitor | None:
        page = self._view_stack.get_visible_child()
        if page and hasattr(page, "_mon"):
            return page._mon
        return self._monitors[0] if self._monitors else None

    def _load_config(self):
        cfg = config.load_config()
        if not cfg:
            self._toast("No saved config found.")
            return
        if "baselines" in cfg:
            self._baselines = {k: {int(c): v for c, v in bl.items()}
                                for k, bl in cfg["baselines"].items()}
        sel_bus = cfg.get("selected_bus")
        for mon in self._monitors:
            if mon.bus == sel_bus:
                self._view_stack.set_visible_child_name(str(mon.bus))
                break
        mon = self._current_mon()
        if mon:
            for ctrl in mon.controls:
                if ctrl.kind == "continuous":
                    val = cfg.get("values", {}).get(str(ctrl.code))
                    if val is not None:
                        ctrl.current = val
                elif ctrl.kind == "choice":
                    val = cfg.get("choices", {}).get(str(ctrl.code))
                    if val is not None:
                        ctrl.choice_value = val
            self._rebuild_pages()

    def _save_config(self):
        mon = self._current_mon()
        if not mon:
            self._toast("No monitor selected.")
            return
        cfg: dict = {"selected_bus": mon.bus, "values": {}, "choices": {}}
        for ctrl in mon.controls:
            if ctrl.kind == "continuous":
                cfg["values"][str(ctrl.code)] = ctrl.current or 0
            elif ctrl.kind == "choice":
                cfg["choices"][str(ctrl.code)] = ctrl.choice_value or 0
        cfg["baselines"] = {k: {str(c): v for c, v in bl.items()}
                            for k, bl in self._baselines.items()}
        err = config.save_config(cfg)
        self._toast("Config saved." if not err else f"Save failed: {err}")

    # ── About ───────────────────────────────────────────────────────

    def _show_about(self):
        about = Adw.AboutWindow(
            transient_for=self, application_name="Ekran", version="0.4",
            developer_name="Ekran", developers=["Ekran contributors"],
            copyright="© 2026 Ekran", license_type=Gtk.License.GPL_2_0,
        )
        about.present()

    def _toast(self, msg: str):
        self._toast_overlay.add_toast(Adw.Toast(title=msg))

    def _controls_stack_state(self, state: str):
        self._outer_stack.set_visible_child_name(state)
