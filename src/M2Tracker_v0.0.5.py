APP_NAME    = "M2 Tracker"
APP_VERSION = "0.0.5"
APP_AUTHOR  = "Spike Murphy Müller"

import rumps
import glob
import tempfile
import time
from datetime import datetime, timedelta
import json
import os
from Quartz import CATextLayer

from AppKit import (
    NSAlert, NSDatePicker, NSDatePickerStyleClockAndCalendar,
    NSDatePickerModeRange, NSDatePickerModeSingle,
    NSAlertFirstButtonReturn, NSTextField, NSView, NSMakeRect,
    NSDatePickerElementFlagYearMonthDay,
    NSAttributedString, NSForegroundColorAttributeName,
    NSFont, NSFontAttributeName, NSColor,
    NSApp, NSFloatingWindowLevel,
    NSPanel, NSButton, NSButtonTypeToggle, NSButtonTypeMomentaryLight,
    NSSwitchButton, NSBezelStyleRounded, NSBezelStyleSmallSquare,
    NSBackingStoreBuffered,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSBezierPath, NSRectFill, NSGraphicsContext,
)
from Foundation import (
    NSDate, NSCalendar, NSCalendarUnitYear, NSCalendarUnitMonth, NSCalendarUnitDay,
    NSMutableDictionary, NSObject, NSMakeRect as FNSMakeRect,
    NSUserDefaults,
)

# ── Paths ──────────────────────────────────────────────────────────────────────

LEGACY_CONFIG = os.path.expanduser("~/.m2_tracker_config.json")

def _get_config_path():
    """Use iCloud Drive if available, otherwise fall back to legacy path."""
    icloud = os.path.expanduser(
        "~/Library/Mobile Documents/com~apple~CloudDocs"
    )
    if os.path.isdir(icloud):
        d = os.path.join(icloud, "M2Tracker")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "config.json")
    return LEGACY_CONFIG

CONFIG_FILE = _get_config_path()

def _maybe_migrate_legacy():
    """
    On first launch with iCloud, copy the old ~/.m2_tracker_config.json
    into the iCloud folder if the destination doesn't exist yet.
    The original is left in place as a local backup.
    """
    if CONFIG_FILE == LEGACY_CONFIG:
        return  # not using iCloud, nothing to migrate
    if os.path.exists(CONFIG_FILE):
        return  # already migrated / fresh iCloud install
    if not os.path.exists(LEGACY_CONFIG):
        return  # truly fresh install, nothing to copy
    import shutil
    shutil.copy2(LEGACY_CONFIG, CONFIG_FILE)

_ASKED_LOGIN_KEY = "M2TrackerAskedLoginItem"

# ── Brand colours ──────────────────────────────────────────────────────────────
AMBOSS_BLUE  = NSColor.colorWithRed_green_blue_alpha_(58/255, 176/255, 199/255, 1)
AMBOSS_GREEN = NSColor.colorWithRed_green_blue_alpha_(76/255, 184/255, 159/255, 1)
ANKI_BLUE    = NSColor.colorWithRed_green_blue_alpha_(20/255, 141/255, 223/255, 1)

TOTAL_DAYS = 100
COLS       = 5
ROWS       = TOTAL_DAYS // COLS   # 20

# Layout
DOT_SIZE     = 10   # coloured square size
DOT_GAP      = 4    # gap between squares
BOX_W        = 3 * DOT_SIZE + 2 * DOT_GAP + 12
BOX_H        = DOT_SIZE + 14
ROW_LABEL_W  = 28
PAD          = 4
TOGGLE_H     = 28
HEADER_H     = 10
GRID_W       = ROW_LABEL_W + COLS * (BOX_W + PAD) + PAD
GRID_H       = ROWS * (BOX_H + PAD) + PAD
PANEL_W      = GRID_W + PAD * 2
BUTTON_BAR_H = 38
PANEL_H      = HEADER_H + TOGGLE_H + PAD + GRID_H + BUTTON_BAR_H + 24
INFO_LABEL_H = 14
INFO_LABEL_GAP = 6


# ── Launch-at-login (SMAppService, macOS 13+) ──────────────────────────────────

def _smas():
    try:
        from ServiceManagement import SMAppService
        return SMAppService.mainAppService()
    except Exception:
        return None

def is_launch_at_login_enabled():
    svc = _smas()
    if svc is None:
        return False
    try:
        return int(svc.status()) == 1
    except Exception:
        return False

def set_launch_at_login(enabled: bool):
    svc = _smas()
    if svc is None:
        rumps.notification("M2 Tracker", "Launch at Login",
                           "Requires macOS 13 or later.")
        return
    if enabled:
        svc.registerAndReturnError_(None)
    else:
        svc.unregisterAndReturnError_(None)

def maybe_ask_launch_at_login():
    """Ask once on first ever launch."""
    defaults = NSUserDefaults.standardUserDefaults()
    if defaults.boolForKey_(_ASKED_LOGIN_KEY):
        return
    defaults.setBool_forKey_(True, _ASKED_LOGIN_KEY)
    defaults.synchronize()

    alert = NSAlert.alloc().init()
    alert.setMessageText_("Launch M2 Tracker at login?")
    alert.setInformativeText_(
        "Should M2 Tracker start automatically every time you log in?"
    )
    alert.addButtonWithTitle_("Yes, launch at login")
    alert.addButtonWithTitle_("No thanks")
    present_alert(alert)
    if alert.runModal() == NSAlertFirstButtonReturn:
        set_launch_at_login(True)


# ── Config I/O ─────────────────────────────────────────────────────────────────

def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def _find_conflict_files():
    dir_  = os.path.dirname(CONFIG_FILE)
    stem  = os.path.splitext(os.path.basename(CONFIG_FILE))[0]
    return [p for p in glob.glob(os.path.join(dir_, f"{stem}*"))
            if p != CONFIG_FILE]

def _merge_configs(primary, secondary):
    """Union-merge: True wins per grid field, higher offset wins."""
    merged   = dict(primary)
    pri_grid = merged.setdefault("grid", {})
    for day, state in secondary.get("grid", {}).items():
        if day not in pri_grid:
            pri_grid[day] = dict(state)
        else:
            for field in ("r", "q", "a"):
                pri_grid[day][field] = (
                    pri_grid[day].get(field) or state.get(field, False)
                )
    merged["offset_days"] = max(
        primary.get("offset_days", 0),
        secondary.get("offset_days", 0),
    )
    return merged

def load_config():
    default = {"start_date": None, "offset_days": 0, "grid": {}, "use_grid": False}
    config  = _read_json(CONFIG_FILE) or default

    conflicts = _find_conflict_files()
    if conflicts:
        for p in conflicts:
            data = _read_json(p)
            if data:
                config = _merge_configs(config, data)
            try:
                os.remove(p)
            except OSError:
                pass
        save_config(config)
        rumps.notification("M2 Tracker", "Sync conflict resolved",
                           "Two versions were merged automatically.")
    return config

def save_config(config):
    """Atomic write — iCloud never reads a half-written file."""
    config["_last_saved"] = time.time()
    dir_ = os.path.dirname(CONFIG_FILE)
    try:
        with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False, suffix=".tmp") as f:
            json.dump(config, f, indent=2)
            tmp = f.name
        os.replace(tmp, CONFIG_FILE)
    except Exception as e:
        rumps.notification("M2 Tracker", "Save failed", str(e))


# ── Helpers (unchanged from v0.0.4) ───────────────────────────────────────────

def count_weekdays(start_date, end_date):
    if start_date > end_date:
        return 0
    days, current = 0, start_date
    while current < end_date:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days

def count_all_days(start_date, end_date):
    return (end_date - start_date).days

def count_grid_days(grid):
    return sum(1 for v in grid.values() if v.get("r") and v.get("q") and v.get("a"))

def make_progress_bar(current, total, width=24):
    label  = f"{current:03d}/{total}"
    filled = 0 if total == 0 else min(int((current / total) * width), width)
    center = width // 2 - len(label) // 2
    bar    = ["─"] * width
    for i in range(filled):
        bar[i] = "═"
    for i, ch in enumerate(label):
        pos = center + i
        if 0 <= pos < width:
            bar[pos] = ch
    return "[" + "".join(bar) + "]"

def make_row(label, bar, extra=""):
    return f"{label:<14}  {bar}  {extra}".rstrip()

def present_alert(alert):
    alert.window().center()
    alert.window().setLevel_(NSFloatingWindowLevel)
    NSApp.activateIgnoringOtherApps_(True)
    alert.window().makeKeyAndOrderFront_(None)

def colored_menu_item(text, color):
    item = rumps.MenuItem(text, callback=lambda _: None)
    attrs = NSMutableDictionary.dictionary()
    attrs[NSForegroundColorAttributeName] = color
    attrs[NSFontAttributeName] = NSFont.menuFontOfSize_(13)
    attr_str = NSAttributedString.alloc().initWithString_attributes_(text, attrs)
    item._menuitem.setAttributedTitle_(attr_str)
    item._menuitem.setEnabled_(True)
    return item

def pick_date_with_calendar(current_date=None):
    alert = NSAlert.alloc().init()
    alert.setMessageText_("Select Start Date")
    alert.setInformativeText_("Choose the day you want to start tracking from.")
    alert.addButtonWithTitle_("Set")
    alert.addButtonWithTitle_("Cancel")
    picker = NSDatePicker.alloc().initWithFrame_(NSMakeRect(0, 0, 220, 148))
    picker.setDatePickerStyle_(NSDatePickerStyleClockAndCalendar)
    picker.setDatePickerMode_(NSDatePickerModeSingle)
    picker.setDatePickerElements_(NSDatePickerElementFlagYearMonthDay)
    if current_date:
        cal   = NSCalendar.currentCalendar()
        comps = cal.components_fromDate_(
            NSCalendarUnitYear | NSCalendarUnitMonth | NSCalendarUnitDay, NSDate.date())
        comps.setYear_(current_date.year)
        comps.setMonth_(current_date.month)
        comps.setDay_(current_date.day)
        picker.setDateValue_(cal.dateFromComponents_(comps))
    else:
        picker.setDateValue_(NSDate.date())
    container = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 220, 148))
    container.addSubview_(picker)
    alert.setAccessoryView_(container)
    present_alert(alert)
    if alert.runModal() == NSAlertFirstButtonReturn:
        ns_date = picker.dateValue()
        cal     = NSCalendar.currentCalendar()
        comps   = cal.components_fromDate_(
            NSCalendarUnitYear | NSCalendarUnitMonth | NSCalendarUnitDay, ns_date)
        return datetime(comps.year(), comps.month(), comps.day()).date()
    return None


# ── Coloured square NSView (unchanged) ────────────────────────────────────────
class ColorSquare(NSView):
    """A clickable rounded-square that toggles between grey and a brand colour."""

    def mouseDown_(self, event):
        self._state = not self._state
        self._apply()
        self._on_toggle(self._state)

    def acceptsFirstResponder(self):
        return True

    def _apply(self):
        c = self._color_on if self._state else self._color_off
        rgb = c.colorUsingColorSpaceName_("NSCalibratedRGBColorSpace")
        if rgb is None:
            rgb = c
        r = rgb.redComponent()
        g = rgb.greenComponent()
        b = rgb.blueComponent()
        a = rgb.alphaComponent()
        from Quartz import CGColorCreateSRGB
        self.layer().setBackgroundColor_(CGColorCreateSRGB(r, g, b, a))

def make_color_square(color_on, state, on_toggle):
    sq = ColorSquare.alloc().initWithFrame_(NSMakeRect(0, 0, DOT_SIZE, DOT_SIZE))
    sq._color_on  = color_on
    sq._color_off = NSColor.colorWithWhite_alpha_(0.35, 1.0)
    sq._state     = state
    sq._on_toggle = on_toggle
    sq.setWantsLayer_(True)
    sq.layer().setCornerRadius_(3)
    sq._apply()
    return sq


# ── Day box (unchanged) ────────────────────────────────────────────────────────
class DayBox:
    """One day cell: three ColorSquares (R=read, Q=questions, A=anki)."""

    FIELDS = [
        ("r", AMBOSS_BLUE),
        ("q", AMBOSS_GREEN),
        ("a", ANKI_BLUE),
    ]

    def __init__(self, day, config, on_change):
        self._day      = day
        self._config   = config
        self._on_change = on_change
        self._squares  = {}

        self.view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, BOX_W, BOX_H))
        self.view.setWantsLayer_(True)
        self.view.layer().setCornerRadius_(4)
        self._set_bg(False)
        self._build()

    def _set_bg(self, all_done):
        from Quartz import CGColorCreateSRGB
        if all_done:
            cg = CGColorCreateSRGB(0.12, 0.12, 0.12, 1.0)
        else:
            cg = CGColorCreateSRGB(0.22, 0.22, 0.22, 1.0)
        self.view.layer().setBackgroundColor_(cg)

    def _build(self):
        key        = str(self._day)
        state      = self._config.get("grid", {}).get(key, {})
        total_sq_w = len(self.FIELDS) * DOT_SIZE + (len(self.FIELDS)-1) * DOT_GAP
        x_start    = (BOX_W - total_sq_w) // 2
        y_sq       = 3

        # Day number as CATextLayer — no hit-test footprint
        from Quartz import CATextLayer
        num_layer = CATextLayer.layer()
        num_layer.setString_(str(self._day))
        num_layer.setFontSize_(7)
        from Quartz import CGColorCreateSRGB
        num_layer.setForegroundColor_(CGColorCreateSRGB(0.55, 0.55, 0.55, 1.0))
        num_layer.setFrame_(((2, y_sq + DOT_SIZE + 1), (BOX_W - 4, 9)))
        num_layer.setContentsScale_(2.0)
        self.view.layer().addSublayer_(num_layer)

        for i, (field, color) in enumerate(self.FIELDS):
            x  = x_start + i * (DOT_SIZE + DOT_GAP)
            sq = make_color_square(color, bool(state.get(field, False)),
                                   self._make_toggle_cb(field))
            sq.setFrame_(NSMakeRect(x, y_sq, DOT_SIZE, DOT_SIZE))
            self.view.addSubview_(sq)
            self._squares[field] = sq

        self._refresh_bg()

    def refresh_state(self):
        """Re-read config and update square colours + background without rebuilding."""
        key   = str(self._day)
        state = self._config.get("grid", {}).get(key, {})
        for field, sq in self._squares.items():
            new_state = bool(state.get(field, False))
            if sq._state != new_state:
                sq._state = new_state
                sq._apply()
        self._refresh_bg()

    def _make_toggle_cb(self, field):
        def cb(new_state):
            key  = str(self._day)
            grid = self._config.setdefault("grid", {})
            grid.setdefault(key, {})[field] = new_state
            self._refresh_bg()
            self._on_change()
        return cb

    def _refresh_bg(self):
        key      = str(self._day)
        state    = self._config.get("grid", {}).get(key, {})
        all_done = all(state.get(f) for f, _ in self.FIELDS)
        self._set_bg(all_done)


# ── Grid panel (unchanged from v0.0.4) ────────────────────────────────────────
class GridPanel:
    def __init__(self, config, on_change):
        self._config    = config
        self._on_change = on_change
        self._panel     = None
        self._boxes     = []
        self._toggle    = None

    def show(self):
        if self._panel is None:
            self._build()
        NSApp.activateIgnoringOtherApps_(True)
        self._panel.center()
        self._panel.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)
        self._panel.center()
        self._panel.setLevel_(NSFloatingWindowLevel)
        self._panel.orderFrontRegardless()
        self._panel.makeKeyAndOrderFront_(None)

    def _build(self):
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        self._panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, PANEL_W, PANEL_H), style, NSBackingStoreBuffered, False)
        self._panel.setTitle_("M2 · 100-Day Tracker")
        self._panel.setFloatingPanel_(True)
        content = self._panel.contentView()

        # Toggle switch
        y_toggle = PANEL_H - HEADER_H - TOGGLE_H
        self._toggle = NSButton.alloc().initWithFrame_(
            NSMakeRect(PAD * 2, y_toggle, PANEL_W - PAD * 4, TOGGLE_H - 4))
        self._toggle.setTitle_("Use grid to calculate actual day")
        self._toggle.setButtonType_(NSSwitchButton)
        self._toggle.setState_(1 if self._config.get("use_grid") else 0)
        self._toggle.setTarget_(self._toggle)
        self._toggle.setAction_("_noop:")
        self._toggle_handler = _ToggleHandler.alloc_init_with(self._toggle, self._toggle_changed)
        content.addSubview_(self._toggle)

        info_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(PAD * 2, y_toggle - 16, PANEL_W - PAD * 4, 14)
        )
        info_label.setStringValue_("□ AMBOSS-Articles · □ AMBOSS-IMPP · □ Anki")
        info_label.setFont_(NSFont.systemFontOfSize_(10))
        info_label.setTextColor_(NSColor.secondaryLabelColor())
        info_label.setBezeled_(False)
        info_label.setDrawsBackground_(False)
        info_label.setEditable_(False)
        info_label.setSelectable_(False)

        content.addSubview_(info_label)

        # Grid
        y_start = y_toggle - 24

        # Grid
        y_start = y_toggle - INFO_LABEL_H - INFO_LABEL_GAP - PAD * 2

        self._boxes = []

        for row in range(ROWS):
            # Week label on left
            week_lbl = NSTextField.alloc().initWithFrame_(
                NSMakeRect(PAD, y_start - row * (BOX_H + PAD) - BOX_H, ROW_LABEL_W - 2, BOX_H))
            week_lbl.setStringValue_(f"W{row+1}")
            week_lbl.setFont_(NSFont.monospacedSystemFontOfSize_weight_(9, 0.0))
            week_lbl.setTextColor_(NSColor.secondaryLabelColor())
            week_lbl.setBezeled_(False)
            week_lbl.setDrawsBackground_(False)
            week_lbl.setEditable_(False)
            week_lbl.setSelectable_(False)
            week_lbl.setAlignment_(1)  # right-align
            content.addSubview_(week_lbl)

            for col in range(COLS):
                day = row * COLS + col + 1
                x   = PAD + ROW_LABEL_W + col * (BOX_W + PAD)
                y   = y_start - row * (BOX_H + PAD) - BOX_H
                box = DayBox(day, self._config, self._box_changed)
                box.view.setFrame_(NSMakeRect(x, y, BOX_W, BOX_H))
                content.addSubview_(box.view)
                self._boxes.append(box)

        button_y = 8

        batch_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(PAD * 2, button_y, 110, 28)
        )
        batch_btn.setTitle_("Batch Edit")
        batch_btn.setBezelStyle_(NSBezelStyleRounded)

        clear_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(PAD * 2 + 120, button_y, 110, 28)
        )
        clear_btn.setTitle_("Clear All")
        clear_btn.setBezelStyle_(NSBezelStyleRounded)

        self._batch_handler = _ButtonHandler.alloc_init_with(
            self._batch_edit
        )
        self._clear_handler = _ButtonHandler.alloc_init_with(
            self._clear_all
        )

        batch_btn.setTarget_(self._batch_handler)
        batch_btn.setAction_("clicked:")

        clear_btn.setTarget_(self._clear_handler)
        clear_btn.setAction_("clicked:")

        content.addSubview_(batch_btn)
        content.addSubview_(clear_btn)

    def _toggle_changed(self, state):
        self._config["use_grid"] = state
        self._on_change()

    def _box_changed(self):
        save_config(self._config)
        self._on_change()

    def _reload_grid(self):
        if self._panel:
            self._panel.close()
            self._panel = None
            self.show()

    def _clear_all(self, sender):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Clear all progress?")
        alert.setInformativeText_(
            "This will reset every checkbox in the grid."
        )

        alert.addButtonWithTitle_("Clear All")
        alert.addButtonWithTitle_("Cancel")

        present_alert(alert)

        if alert.runModal() != NSAlertFirstButtonReturn:
            return

        self._config["grid"] = {}

        save_config(self._config)
        self._reload_grid()
        self._on_change()

    def _batch_edit(self, sender):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Batch Edit")
        alert.setInformativeText_(
            "How many days have already been completed?"
        )

        field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(0, 0, 200, 24)
        )
        field.setStringValue_("0")

        alert.setAccessoryView_(field)

        alert.addButtonWithTitle_("Apply")
        alert.addButtonWithTitle_("Cancel")

        present_alert(alert)

        if alert.runModal() != NSAlertFirstButtonReturn:
            return

        try:
            completed = int(field.stringValue())
        except Exception:
            return

        completed = max(0, min(TOTAL_DAYS, completed))

        new_grid = {}

        for day in range(1, completed + 1):
            new_grid[str(day)] = {
                "r": True,
                "q": True,
                "a": True,
            }

        self._config["grid"] = new_grid

        save_config(self._config)
        self._reload_grid()
        self._on_change()


class _ToggleHandler(NSObject):
    @classmethod
    def alloc_init_with(cls, button, callback):
        obj = cls.alloc().init()
        obj._callback = callback
        button.setTarget_(obj)
        button.setAction_("toggled:")
        return obj

    def toggled_(self, sender):
        self._callback(bool(sender.state()))

class _ButtonHandler(NSObject):
    @classmethod
    def alloc_init_with(cls, callback):
        obj = cls.alloc().init()
        obj._callback = callback
        return obj

    def clicked_(self, sender):
        self._callback(sender)


# ── Main app ───────────────────────────────────────────────────────────────────
class M2TrackerApp(rumps.App):
    def __init__(self):
        _maybe_migrate_legacy()
        self.config = load_config()
        self.config.setdefault("grid", {})
        self.config.setdefault("use_grid", False)
        self.total_days = TOTAL_DAYS

        super(M2TrackerApp, self).__init__("M2", quit_button=None)
        self._quit_item  = rumps.MenuItem("Quit M2 Tracker", callback=rumps.quit_application)
        self._grid_panel = GridPanel(self.config, self._grid_changed)
        self.update_display()

        # Ask about login item once after app is up
        maybe_ask_launch_at_login()

    def _grid_changed(self):
        save_config(self.config)
        self.update_display()

    def get_start_date(self):
        if self.config.get("start_date"):
            return datetime.strptime(self.config["start_date"], "%Y-%m-%d").date()
        return None

    def get_next_check_day(self):
        grid = self.config.get("grid", {})
        last_used_day = 0
        for day_str, state in grid.items():
            if any(state.get(k, False) for k in ("r", "q", "a")):
                try:
                    last_used_day = max(last_used_day, int(day_str))
                except ValueError:
                    pass
        return min(last_used_day + 1, TOTAL_DAYS)

    def toggle_launch_at_login(self, _):
        set_launch_at_login(not is_launch_at_login_enabled())
        self.update_display()

    def open_config_folder(self, _):
        import subprocess
        subprocess.Popen(["open", os.path.dirname(CONFIG_FILE)])

    def update_display(self):
        start = self.get_start_date()
        today = datetime.now().date()

        if not start:
            self.title = "M2: ?"
            self.menu.clear()
            self.menu = [
                rumps.MenuItem("No start date set"),
                None,
                rumps.MenuItem("Set Start Date", callback=self.set_start_date),
                None,
                self._quit_item,
            ]
            return

        weekday_count  = count_weekdays(start, today)
        calendar_count = count_all_days(start, today)
        offset         = self.config.get("offset_days", 0)
        use_grid       = self.config.get("use_grid", False)
        actual_day     = count_grid_days(self.config.get("grid", {})) if use_grid \
                         else weekday_count + offset

        diff = actual_day - weekday_count

        if diff > 0:
            status_color = NSColor.systemGreenColor()
            status_symbol = "▲"
        elif diff < 0:
            status_color = NSColor.systemRedColor()
            status_symbol = "▼"
        else:
            status_color = NSColor.labelColor()
            status_symbol = ""

        self.title = f"D{actual_day}{status_symbol}"
        self.menu.clear()

        start_item = rumps.MenuItem(f"Start date:  {start.strftime('%Y-%m-%d')}")
        start_item.set_callback(None)

        weekday_item = rumps.MenuItem(make_row("Weekdays", make_progress_bar(weekday_count, self.total_days)))
        weekday_item.set_callback(None)
        calendar_item = rumps.MenuItem(make_row("Calendar        ", make_progress_bar(calendar_count, self.total_days)))
        calendar_item.set_callback(None)

        if diff == 1:    efficiency = "1 day ahead"
        elif diff == -1: efficiency = "1 day behind"
        elif diff > 1:   efficiency = f"{diff} days ahead"
        elif diff < -1:  efficiency = f"{abs(diff)} days behind"
        else:            efficiency = "on track"

        actual_text = make_row("Actual             ", make_progress_bar(actual_day, self.total_days), f"({efficiency})")
        actual_item = colored_menu_item(actual_text, status_color)

        offset_sign = f"+{offset}" if offset >= 0 else str(offset)
        plus_item   = rumps.MenuItem(f"+ offset Day (current: {offset_sign})",
                                     callback=None if use_grid else self.add_offset_day)
        minus_item  = rumps.MenuItem(f"- offset Day (current: {offset_sign})",
                                     callback=None if use_grid else self.remove_offset_day)
        if use_grid:
            plus_item._menuitem.setEnabled_(False)
            minus_item._menuitem.setEnabled_(False)

        grid_label = "Day Grid  ✓" if use_grid else "Day Grid"

        next_day = self.get_next_check_day()

        today_item = rumps.MenuItem(
            f"Check Day {next_day}",
            callback=self.mark_today_shortcut if use_grid else None
        )

        if not use_grid:
            today_item._menuitem.setEnabled_(False)

        # ── New: launch-at-login toggle ────────────────────────────────────────
        lal_label = "Launch at Login  ✓" if is_launch_at_login_enabled() else "Launch at Login"
        lal_item  = rumps.MenuItem(lal_label, callback=self.toggle_launch_at_login)

        # ── New: iCloud / local — click to reveal config folder in Finder ────────
        using_icloud = "com~apple~CloudDocs" in CONFIG_FILE
        icloud_label = "iCloud ☁︎" if using_icloud else "Local Storage"
        icloud_item  = rumps.MenuItem(icloud_label, callback=self.open_config_folder)

        self.menu = [
            start_item, None,
            weekday_item, calendar_item, actual_item, None,
            plus_item, minus_item, None,
            rumps.MenuItem(grid_label, callback=self.open_grid), today_item, None,
            rumps.MenuItem("Set Start Date", callback=self.set_start_date),
            rumps.MenuItem("Reset offset Days", callback=self.reset_offset), None,
            lal_item, icloud_item, None,
            rumps.MenuItem("About M2 Tracker", callback=self.show_about),
            self._quit_item,
        ]

    def open_grid(self, _):
        self._grid_panel.show()

    def mark_today_shortcut(self, _):
        grid = self.config.setdefault("grid", {})
        target_day = self.get_next_check_day()

        if target_day > TOTAL_DAYS:
            return

        grid[str(target_day)] = {
            "r": True,
            "q": True,
            "a": True,
        }

        save_config(self.config)
        self._grid_panel._reload_grid()
        self.update_display()

    def set_start_date(self, _):
        chosen = pick_date_with_calendar(self.get_start_date())
        if chosen:
            self.config["start_date"] = chosen.strftime("%Y-%m-%d")
            self.config["offset_days"] = 0
            save_config(self.config)
            self.update_display()
            rumps.notification("M2 Tracker", "Start date set", f"Tracking from {chosen}")

    def add_offset_day(self, _):
        self.config["offset_days"] = self.config.get("offset_days", 0) + 1
        save_config(self.config); self.update_display()

    def remove_offset_day(self, _):
        self.config["offset_days"] = self.config.get("offset_days", 0) - 1
        save_config(self.config); self.update_display()

    def reset_offset(self, _):
        self.config["offset_days"] = 0
        save_config(self.config); self.update_display()

    def show_about(self, _):
        from Foundation import NSBundle
        bundle  = NSBundle.mainBundle()
        name    = bundle.objectForInfoDictionaryKey_("CFBundleDisplayName") or \
                  bundle.objectForInfoDictionaryKey_("CFBundleName") or "M2 Tracker"
        version = bundle.objectForInfoDictionaryKey_("CFBundleShortVersionString") or "—"
        build   = bundle.objectForInfoDictionaryKey_("CFBundleVersion") or "—"
        info    = bundle.objectForInfoDictionaryKey_("CFBundleGetInfoString") or ""
        copy    = bundle.objectForInfoDictionaryKey_("NSHumanReadableCopyright") or ""
        alert   = NSAlert.alloc().init()
        alert.setMessageText_(f"{name}  v{version}")
        alert.setInformativeText_(
            f"Build {build}\n\n{info}\n\n{copy}\n\nConfig: {CONFIG_FILE}"
        )
        alert.addButtonWithTitle_("OK")
        present_alert(alert)
        alert.runModal()

    @rumps.timer(60)
    def refresh(self, _):
        # Re-check for iCloud conflicts on each tick
        if _find_conflict_files():
            self.config = load_config()
            self._grid_panel._config = self.config
            self._grid_panel._reload_grid()
        self.update_display()


if __name__ == "__main__":
    M2TrackerApp().run()