APP_NAME = "M2 Tracker"
APP_VERSION = "0.0.6"
APP_AUTHOR = "Spike Murphy Müller"

import rumps
from datetime import datetime, timedelta
import json
import os
import subprocess
import plistlib
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
)

# ── Paths ───────────────────────────────────────────────────────────────────────
LOCAL_CONFIG_FILE  = os.path.expanduser("~/.m2_tracker_config.json")
# Use the user-visible iCloud Drive folder (~/iCloud Drive → symlink macOS creates)
# Prefer the symlink path first; fall back to the raw Mobile Documents path.
_icloud_symlink = os.path.expanduser("~/Library/CloudStorage/iCloud Drive")
if not os.path.exists(_icloud_symlink):
    _icloud_symlink = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs")
ICLOUD_DIR         = os.path.join(_icloud_symlink, "M2Tracker")
ICLOUD_CONFIG_FILE = os.path.join(ICLOUD_DIR, "m2_tracker_config.json")
PREFS_FILE         = os.path.expanduser("~/.m2_tracker_prefs.json")   # always local

BUNDLE_ID = "com.spikemurphy.m2tracker"
LAUNCH_AGENT_PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{BUNDLE_ID}.plist")

# ── Prefs (local, separate from synced config) ──────────────────────────────────
def load_prefs():
    if os.path.exists(PREFS_FILE):
        with open(PREFS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_prefs(prefs):
    with open(PREFS_FILE, "w") as f:
        json.dump(prefs, f, indent=2)

def get_config_file(prefs):
    if prefs.get("use_icloud"):
        return ICLOUD_CONFIG_FILE   # module-level global, may be updated after picker
    return LOCAL_CONFIG_FILE

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

# ── Config ─────────────────────────────────────────────────────────────────────
def _icloud_is_downloaded(path):
    """
    Return True only if the file is fully present on disk (not an iCloud placeholder/evicted stub).
    Uses the com.apple.icloud.itemName xattr presence combined with the
    com.apple.ubiquity.inode-id attribute, but the most reliable signal is
    reading the com.apple.cloudDocs.download-state xattr via `xattr -p`.
    Simpler: check for the UF_COMPRESSED flag or just look for the shadow placeholder.
    Most reliable cross-version approach: try to read the file size via a real open().
    """
    if not os.path.exists(path):
        # Check for eviction placeholder: .filename.icloud
        dirname, basename = os.path.dirname(path), os.path.basename(path)
        placeholder = os.path.join(dirname, f".{basename}.icloud")
        return False  # definitely not downloaded

    # Even if os.path.exists() is True, the file may be an evicted stub (0 bytes
    # or iCloud placeholder that the OS presents as a regular file).
    # The safest check: read the com.apple.cloud.itemExists xattr, or simply
    # try to read the first byte — iCloud will either return real data or fail.
    try:
        # xattr check: com.apple.cloudDocs.download-state
        # Value 3 = downloaded, anything else = not yet local
        result = subprocess.run(
            ["xattr", "-p", "com.apple.cloudDocs.download-state", path],
            capture_output=True, timeout=3
        )
        if result.returncode == 0:
            # Value is a single byte: 3 means fully downloaded
            raw = result.stdout.strip()
            # Could be hex like "03" or binary byte 0x03
            try:
                val = int(raw, 16) if raw else 0
            except ValueError:
                val = raw[0] if raw else 0
            if val != 3:
                return False
    except Exception:
        pass  # xattr not available or not an iCloud file — assume downloaded

    return True

def ensure_icloud_downloaded(path):
    """
    Force iCloud to download the file if it is evicted or not yet local.
    Always calls brctl download — it is a no-op if the file is already local.
    Polls up to 15 s for the file to appear after requesting download.
    """
    import time

    # Always issue brctl download — safe even if already downloaded
    try:
        subprocess.run(["brctl", "download", path],
                       capture_output=True, timeout=5)
    except Exception:
        pass

    if _icloud_is_downloaded(path):
        return True

    # Not downloaded yet — poll for up to 15 s
    for _ in range(30):
        time.sleep(0.5)
        if _icloud_is_downloaded(path):
            return True

    # Last resort: just try opening it (triggers materialisation on some macOS versions)
    try:
        open(path, "rb").close()
    except Exception:
        pass

    return os.path.exists(path)

def _can_access_icloud():
    """Return True if we can read/write the iCloud config directory right now."""
    try:
        os.makedirs(ICLOUD_DIR, exist_ok=True)
        test = os.path.join(ICLOUD_DIR, ".m2_access_test")
        with open(test, "w") as f:
            f.write("ok")
        os.remove(test)
        return True
    except OSError:
        return False

def load_config(prefs):
    path = get_config_file(prefs)
    if prefs.get("use_icloud"):
        ensure_icloud_downloaded(path)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"start_date": None, "offset_days": 0, "grid": {}, "use_grid": False}

def save_config(config, prefs):
    path = get_config_file(prefs)
    if prefs.get("use_icloud"):
        os.makedirs(ICLOUD_DIR, exist_ok=True)
    config["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w") as f:
        json.dump(config, f, indent=2)

def get_config_mtime(prefs):
    path = get_config_file(prefs)
    if os.path.exists(path):
        return os.path.getmtime(path)
    return 0

def transfer_config(from_prefs, to_prefs):
    """Copy config data from one storage location to the other."""
    src = get_config_file(from_prefs)
    if os.path.exists(src):
        with open(src, "r") as f:
            data = json.load(f)
    else:
        data = {"start_date": None, "offset_days": 0, "grid": {}, "use_grid": False}
    if to_prefs.get("use_icloud"):
        os.makedirs(ICLOUD_DIR, exist_ok=True)
    dst = get_config_file(to_prefs)
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(dst, "w") as f:
        json.dump(data, f, indent=2)

# ── Launch-at-Login (LaunchAgent) ───────────────────────────────────────────────
def get_app_executable():
    """Return the path to the running executable (works for .app bundles too)."""
    import sys
    return sys.executable

def is_launch_at_login_enabled():
    return os.path.exists(LAUNCH_AGENT_PLIST)

def set_launch_at_login(enabled):
    if enabled:
        exe = get_app_executable()
        plist = {
            "Label": BUNDLE_ID,
            "ProgramArguments": [exe],
            "RunAtLoad": True,
            "KeepAlive": False,
        }
        os.makedirs(os.path.dirname(LAUNCH_AGENT_PLIST), exist_ok=True)
        with open(LAUNCH_AGENT_PLIST, "wb") as f:
            plistlib.dump(plist, f)
        subprocess.call(["launchctl", "load", LAUNCH_AGENT_PLIST])
    else:
        if os.path.exists(LAUNCH_AGENT_PLIST):
            subprocess.call(["launchctl", "unload", LAUNCH_AGENT_PLIST])
            os.remove(LAUNCH_AGENT_PLIST)

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


# ── Coloured square NSView ─────────────────────────────────────────────────────
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

        # Convert grayscale / other color spaces to RGB first
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


# ── Day box ────────────────────────────────────────────────────────────────────
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


# ── Grid panel ─────────────────────────────────────────────────────────────────
class GridPanel:
    def __init__(self, config, prefs, on_change):
        self._config    = config
        self._prefs     = prefs
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
        # Use a simple approach: poll state via timer-free method
        self._toggle.setAction_("_noop:")   # will use subview observation instead
        # Wire via a wrapper NSObject
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
        save_config(self._config, self._prefs)
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

        save_config(self._config, self._prefs)
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

        save_config(self._config, self._prefs)
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
        self.prefs = load_prefs()
        self._first_launch_setup()

        self.config = load_config(self.prefs)
        self.config.setdefault("grid", {})
        self.config.setdefault("use_grid", False)
        self.total_days = TOTAL_DAYS
        self._last_config_mtime = get_config_mtime(self.prefs)

        super(M2TrackerApp, self).__init__("M2", quit_button=None)
        self._quit_item  = rumps.MenuItem("Quit M2 Tracker", callback=rumps.quit_application)
        self._grid_panel = GridPanel(self.config, self.prefs, self._grid_changed)
        self.update_display()

    # ── First-launch prompts ────────────────────────────────────────────────────
    def _first_launch_setup(self):
        """Ask storage/autostart on first run; ensure iCloud permission on every launch."""
        if "use_icloud" not in self.prefs:
            self._ask_storage()
        elif self.prefs.get("use_icloud") and not _can_access_icloud():
            # Already chose iCloud but permission is missing (e.g. second Mac)
            self._grant_icloud_permission()
        if "launch_at_login" not in self.prefs:
            self._ask_autostart()

    def _ask_storage(self):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Where should M2 Tracker save your data?")
        alert.setInformativeText_(
            "iCloud keeps your progress synced across all your Macs.\n"
            "Local stores the file only on this Mac."
        )
        alert.addButtonWithTitle_("iCloud")
        alert.addButtonWithTitle_("Local")
        NSApp.activateIgnoringOtherApps_(True)
        result = alert.runModal()
        use_icloud = (result == NSAlertFirstButtonReturn)
        self.prefs["use_icloud"] = use_icloud
        if use_icloud:
            os.makedirs(ICLOUD_DIR, exist_ok=True)
            self._grant_icloud_permission()
        save_prefs(self.prefs)

    def _grant_icloud_permission(self):
        """
        Show a native folder picker pre-pointed at the M2Tracker iCloud folder.
        The user clicks Allow Access — macOS then grants this process permission
        to read/write that directory. Updates the global path to the resolved location.
        """
        global ICLOUD_DIR, ICLOUD_CONFIG_FILE
        from AppKit import NSOpenPanel, NSModalResponseOK
        from Foundation import NSURL
        os.makedirs(ICLOUD_DIR, exist_ok=True)
        panel = NSOpenPanel.openPanel()
        panel.setMessage_("Allow M2 Tracker to access its iCloud folder")
        panel.setPrompt_("Allow Access")
        panel.setCanChooseFiles_(False)
        panel.setCanChooseDirectories_(True)
        panel.setCanCreateDirectories_(True)
        panel.setAllowsMultipleSelection_(False)
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(ICLOUD_DIR))
        NSApp.activateIgnoringOtherApps_(True)
        response = panel.runModal()
        if response == NSModalResponseOK:
            chosen = panel.URL().path()
            ICLOUD_DIR = chosen
            ICLOUD_CONFIG_FILE = os.path.join(ICLOUD_DIR, "m2_tracker_config.json")

    def _ask_autostart(self):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Launch M2 Tracker at Login?")
        alert.setInformativeText_("Should M2 Tracker start automatically every time you log in?")
        alert.addButtonWithTitle_("Yes")
        alert.addButtonWithTitle_("No")
        NSApp.activateIgnoringOtherApps_(True)
        result = alert.runModal()
        enabled = (result == NSAlertFirstButtonReturn)
        self.prefs["launch_at_login"] = enabled
        save_prefs(self.prefs)
        set_launch_at_login(enabled)

    # ── Callbacks ───────────────────────────────────────────────────────────────
    def _grid_changed(self):
        save_config(self.config, self.prefs)
        self._last_config_mtime = get_config_mtime(self.prefs)
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

    # ── Storage / autostart toggles ─────────────────────────────────────────────
    def toggle_storage(self, _):
        use_icloud = self.prefs.get("use_icloud", False)
        new_use_icloud = not use_icloud
        dest = "iCloud" if new_use_icloud else "Local"

        alert = NSAlert.alloc().init()
        alert.setMessageText_(f"Switch storage to {dest}?")
        alert.setInformativeText_(
            f"Your current config will be copied to {dest} storage. "
            "The app will sync from there going forward."
        )
        alert.addButtonWithTitle_("Switch")
        alert.addButtonWithTitle_("Cancel")
        NSApp.activateIgnoringOtherApps_(True)
        if alert.runModal() != NSAlertFirstButtonReturn:
            return

        old_prefs = dict(self.prefs)
        self.prefs["use_icloud"] = new_use_icloud
        if new_use_icloud:
            os.makedirs(ICLOUD_DIR, exist_ok=True)
            self._grant_icloud_permission()

        transfer_config(old_prefs, self.prefs)
        save_prefs(self.prefs)

        self.config = load_config(self.prefs)
        self.config.setdefault("grid", {})
        self.config.setdefault("use_grid", False)
        self._last_config_mtime = get_config_mtime(self.prefs)
        self._grid_panel = GridPanel(self.config, self.prefs, self._grid_changed)
        self.update_display()

    def toggle_launch_at_login(self, _):
        enabled = not self.prefs.get("launch_at_login", False)
        self.prefs["launch_at_login"] = enabled
        save_prefs(self.prefs)
        set_launch_at_login(enabled)
        self.update_display()

    # ── Display ─────────────────────────────────────────────────────────────────
    def update_display(self):
        start = self.get_start_date()
        today = datetime.now().date()

        if not start:
            self.title = "M2: ?"
            self.menu.clear()
            use_icloud   = self.prefs.get("use_icloud", False)
            updated_at   = self.config.get("updated_at", "—")
            try:
                updated_short = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S").strftime("%H:%M")
            except Exception:
                updated_short = updated_at
            storage_label = f"{'iCloud' if use_icloud else 'Local'}  ·  updated {updated_short}"
            storage_item  = rumps.MenuItem(storage_label, callback=self.toggle_storage)
            login_enabled = self.prefs.get("launch_at_login", False)
            login_label   = "Launch at Login  ✓" if login_enabled else "Launch at Login"
            login_item    = rumps.MenuItem(login_label, callback=self.toggle_launch_at_login)
            syncing_note  = rumps.MenuItem("⏳ Waiting for iCloud sync…" if use_icloud else "No start date set")
            syncing_note.set_callback(None)
            self.menu = [
                syncing_note,
                None,
                rumps.MenuItem("Set Start Date", callback=self.set_start_date),
                None,
                storage_item,
                login_item,
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
            status_color  = NSColor.systemGreenColor()
            status_symbol = "▲"
        elif diff < 0:
            status_color  = NSColor.systemRedColor()
            status_symbol = "▼"
        else:
            status_color  = NSColor.labelColor()
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

        next_day   = self.get_next_check_day()
        today_item = rumps.MenuItem(
            f"Check Day {next_day}",
            callback=self.mark_today_shortcut if use_grid else None
        )
        if not use_grid:
            today_item._menuitem.setEnabled_(False)

        # ── Storage / autostart items ───────────────────────────────────────────
        use_icloud   = self.prefs.get("use_icloud", False)
        updated_at   = self.config.get("updated_at", "—")
        try:
            updated_short = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S").strftime("%H:%M")
        except Exception:
            updated_short = updated_at

        storage_label = f"{'iCloud' if use_icloud else 'Local'}  ·  updated {updated_short}"
        storage_item  = rumps.MenuItem(storage_label, callback=self.toggle_storage)

        login_enabled = self.prefs.get("launch_at_login", False)
        login_label   = "Launch at Login  ✓" if login_enabled else "Launch at Login"
        login_item    = rumps.MenuItem(login_label, callback=self.toggle_launch_at_login)

        self.menu = [
            start_item, None,
            weekday_item, calendar_item, actual_item, None,
            plus_item, minus_item, None,
            rumps.MenuItem(grid_label, callback=self.open_grid), today_item, None,
            rumps.MenuItem("Set Start Date", callback=self.set_start_date),
            rumps.MenuItem("Reset offset Days", callback=self.reset_offset), None,
            storage_item,
            login_item, None,
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
        grid[str(target_day)] = {"r": True, "q": True, "a": True}
        save_config(self.config, self.prefs)
        self._last_config_mtime = get_config_mtime(self.prefs)
        self._grid_panel._reload_grid()
        self.update_display()

    def set_start_date(self, _):
        chosen = pick_date_with_calendar(self.get_start_date())
        if chosen:
            self.config["start_date"] = chosen.strftime("%Y-%m-%d")
            self.config["offset_days"] = 0
            save_config(self.config, self.prefs)
            self._last_config_mtime = get_config_mtime(self.prefs)
            self.update_display()
            rumps.notification("M2 Tracker", "Start date set", f"Tracking from {chosen}")

    def add_offset_day(self, _):
        self.config["offset_days"] = self.config.get("offset_days", 0) + 1
        save_config(self.config, self.prefs)
        self._last_config_mtime = get_config_mtime(self.prefs)
        self.update_display()

    def remove_offset_day(self, _):
        self.config["offset_days"] = self.config.get("offset_days", 0) - 1
        save_config(self.config, self.prefs)
        self._last_config_mtime = get_config_mtime(self.prefs)
        self.update_display()

    def reset_offset(self, _):
        self.config["offset_days"] = 0
        save_config(self.config, self.prefs)
        self._last_config_mtime = get_config_mtime(self.prefs)
        self.update_display()

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
        alert.setInformativeText_(f"Build {build}\n\n{info}\n\n{copy}")
        alert.addButtonWithTitle_("OK")
        present_alert(alert)
        alert.runModal()

    @rumps.timer(60)
    def refresh(self, _):
        """Every 60 s: if iCloud mode, check for external config changes and sync."""
        if self.prefs.get("use_icloud"):
            # Trigger download if the file is still a placeholder
            ensure_icloud_downloaded(ICLOUD_CONFIG_FILE)
            mtime = get_config_mtime(self.prefs)
            if mtime and mtime != self._last_config_mtime:
                self.config = load_config(self.prefs)
                self.config.setdefault("grid", {})
                self.config.setdefault("use_grid", False)
                self._last_config_mtime = mtime
                self._grid_panel = GridPanel(self.config, self.prefs, self._grid_changed)
        self.update_display()


if __name__ == "__main__":
    M2TrackerApp().run()