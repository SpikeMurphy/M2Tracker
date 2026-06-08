APP_NAME = "M2 Tracker"
APP_VERSION = "0.1.1"
APP_AUTHOR = "Spike Murphy Müller"

import rumps
from datetime import datetime, timedelta
import json
import os
import subprocess
import sys
import plistlib
import time

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
    NSWorkspace,
)
from Foundation import (
    NSDate, NSCalendar, NSCalendarUnitYear, NSCalendarUnitMonth, NSCalendarUnitDay,
    NSMutableDictionary, NSObject, NSMakeRect as FNSMakeRect,
    NSBundle,
)
from Quartz import CATextLayer

# ── Paths ───────────────────────────────────────────────────────────────────────
LOCAL_CONFIG_FILE  = os.path.expanduser("~/.m2_tracker_config.json")

# Prefer the standard CloudStorage symlink, fall back to the raw Mobile Documents path.
_icloud_symlink = os.path.expanduser("~/Library/CloudStorage/iCloudDrive")
if not os.path.isdir(_icloud_symlink):
    # macOS sometimes uses a space in the display name but not in the path
    _icloud_symlink = os.path.expanduser("~/Library/CloudStorage/iCloud~Drive")
if not os.path.isdir(_icloud_symlink):
    _icloud_symlink = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs")
ICLOUD_DIR         = os.path.join(_icloud_symlink, "M2Tracker")
ICLOUD_CONFIG_FILE = os.path.join(ICLOUD_DIR, "m2_tracker_config.json")
PREFS_FILE         = os.path.expanduser("~/.m2_tracker_prefs.json")   # always local

# ── BUNDLE_ID & LaunchAgent path ────────────────────────────────────────────────
BUNDLE_ID = "com.spikemurphy.m2tracker"
LAUNCH_AGENT_PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{BUNDLE_ID}.plist")

# ── macOS Notification Center helper ───────────────────────────────────────────
def notify(title, message, subtitle=""):
    """Send a notification to the macOS Notification Center (top-right corner)."""
    try:
        rumps.notification(title, subtitle, message)
    except Exception:
        # Fallback: use osascript if rumps notification fails
        try:
            script = (
                f'display notification "{message}" '
                f'with title "{title}"'
                + (f' subtitle "{subtitle}"' if subtitle else "")
            )
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
        except Exception:
            pass  # Best effort — never crash because of a notification


# ── Prefs (local, separate from synced config) ──────────────────────────────────
def load_prefs():
    if os.path.exists(PREFS_FILE):
        try:
            with open(PREFS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            notify("M2 Tracker – Prefs Error",
                   f"Could not read preferences file: {e}. Using defaults.")
    return {}

def save_prefs(prefs):
    try:
        with open(PREFS_FILE, "w") as f:
            json.dump(prefs, f, indent=2)
    except Exception as e:
        notify("M2 Tracker – Save Error",
               f"Could not save preferences: {e}")

def get_config_file(prefs):
    """Return the active config file path based on current prefs."""
    if prefs.get("use_icloud"):
        return ICLOUD_CONFIG_FILE
    return LOCAL_CONFIG_FILE


# ── Brand colours ──────────────────────────────────────────────────────────────
AMBOSS_BLUE  = NSColor.colorWithRed_green_blue_alpha_(58/255, 176/255, 199/255, 1)
AMBOSS_GREEN = NSColor.colorWithRed_green_blue_alpha_(76/255, 184/255, 159/255, 1)
ANKI_BLUE    = NSColor.colorWithRed_green_blue_alpha_(20/255, 141/255, 223/255, 1)

TOTAL_DAYS = 100
COLS       = 5
ROWS       = TOTAL_DAYS // COLS   # 20

# Layout
DOT_SIZE     = 10
DOT_GAP      = 4
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


# ── iCloud helpers ──────────────────────────────────────────────────────────────
def ensure_icloud_downloaded(path):
    """
    Ask iCloud to materialise an evicted file, then wait up to 5 s.
    Safe to call even when the file is already local.
    """
    try:
        subprocess.run(["brctl", "download", path],
                       capture_output=True, timeout=5)
    except Exception:
        pass
    # Brief wait for iCloud to finish downloading
    for _ in range(10):
        if os.path.exists(path):
            return True
        time.sleep(0.5)
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



# ── Config I/O ──────────────────────────────────────────────────────────────────
_EMPTY_CONFIG = {"start_date": None, "offset_days": 0, "grid": {}, "use_grid": False}


def _normalise_config(data):
    """Ensure all required keys are present (forward-compatibility guard)."""
    data.setdefault("start_date", None)
    data.setdefault("offset_days", 0)
    data.setdefault("grid", {})
    data.setdefault("use_grid", False)
    return data


def _read_json_file(path):
    """
    Read and parse a JSON file.  Returns (data_dict, error_string).
    On success error_string is None.  Never raises.
    """
    try:
        with open(path, "r") as f:
            return json.load(f), None
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"
    except PermissionError as e:
        return None, f"Permission denied (Errno {e.errno}): {e.strerror}"
    except OSError as e:
        return None, f"OS error (Errno {e.errno}): {e.strerror}"
    except Exception as e:
        return None, str(e)


def _write_json_file_atomic(path, data):
    """
    Atomically write *data* to *path* via a .tmp side-file + os.replace().
    Returns (True, None) on success, (False, error_string) on failure.
    """
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        return True, None
    except Exception as e:
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False, str(e)


def load_config(prefs):
    """
    Load config from the path determined by prefs.

    iCloud mode:
      1. Try to materialise the file via brctl download.
      2. Read and return it.
      3. On permission error: notify and return empty config (timer will retry).
      4. On any other error: notify and return empty config.

    Always returns a valid config dict — never raises.
    """
    path = get_config_file(prefs)

    if prefs.get("use_icloud"):
        # Try to materialise the file (no-op if already local)
        ensure_icloud_downloaded(path)

        data, err = _read_json_file(path)
        if data is not None:
            return _normalise_config(data)

        if err is not None:
            if "Permission denied" in err or "Errno 1" in err:
                # iCloud Drive not yet accessible (e.g. right after boot).
                # The 60-second refresh timer will pick up the real file once ready.
                notify("M2 Tracker – iCloud",
                       "iCloud Drive not yet accessible. Will retry automatically.")
                return dict(_EMPTY_CONFIG)
            else:
                # Unexpected error (corrupt JSON, disk full, …)
                notify("M2 Tracker – Config Error", f"Could not read config: {err}")
                if "JSON parse error" in err:
                    try:
                        import shutil
                        shutil.copy2(path, path + ".bak")
                    except Exception:
                        pass
                return dict(_EMPTY_CONFIG)

        # File simply doesn't exist yet (first run)
        return dict(_EMPTY_CONFIG)

    else:
        # Local mode — straightforward
        if not os.path.exists(path):
            return dict(_EMPTY_CONFIG)
        data, err = _read_json_file(path)
        if data is not None:
            return _normalise_config(data)
        notify("M2 Tracker – Config Error", f"Could not read config: {err}")
        if err and "JSON parse error" in err:
            try:
                import shutil
                shutil.copy2(path, path + ".bak")
            except Exception:
                pass
        return dict(_EMPTY_CONFIG)


def save_config(config, prefs):
    """
    Atomically write config to the correct path.
    In iCloud mode also refreshes the local cache so the next boot has a fallback.
    """
    path = get_config_file(prefs)
    if prefs.get("use_icloud"):
        try:
            os.makedirs(ICLOUD_DIR, exist_ok=True)
        except OSError as e:
            notify("M2 Tracker – Save Error",
                   f"Could not create iCloud directory: {e}")
            return

    config["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok, err = _write_json_file_atomic(path, config)
    if not ok:
        notify("M2 Tracker – Save Error", f"Could not save config: {err}")
        return


def get_config_mtime(prefs):
    path = get_config_file(prefs)
    if os.path.exists(path):
        try:
            return os.path.getmtime(path)
        except OSError:
            pass
    return 0


def transfer_config(from_prefs, to_prefs):
    """Copy config data from one storage location to the other."""
    src = get_config_file(from_prefs)
    data, err = _read_json_file(src)
    if data is None:
        if err:
            notify("M2 Tracker – Transfer Error",
                   f"Could not read source config for transfer: {err}")
        data = dict(_EMPTY_CONFIG)

    if to_prefs.get("use_icloud"):
        try:
            os.makedirs(ICLOUD_DIR, exist_ok=True)
        except OSError as e:
            notify("M2 Tracker – Transfer Error",
                   f"Could not create iCloud directory during transfer: {e}")
            return

    dst = get_config_file(to_prefs)
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok, err = _write_json_file_atomic(dst, data)
    if not ok:
        notify("M2 Tracker – Transfer Error",
               f"Could not write config to destination: {err}")
        return


# ── Launch-at-Login ─────────────────────────────────────────────────────────────
#
# macOS 13+ requires SMAppService (ServiceManagement framework) to show the app
# in System Settings → General → Login Items.  LaunchAgent plists still work
# technically but are INVISIBLE in that panel and increasingly blocked unsigned.
#
# SMAppService.mainAppService().register() / .unregisterAndReturnError_() is the
# one-call modern API.  It requires the app to be a proper signed .app bundle,
# which PyInstaller produces.
#
# Fallback: plain LaunchAgent plist for macOS 12 and earlier.


def _get_sm_app_service():
    """
    Return the SMAppService.mainAppService() instance (macOS 13+), or None.
    Tries the PyObjC binding first, then manual objc.loadBundle().
    Add pyobjc-framework-ServiceManagement to your PyInstaller requirements.
    """
    try:
        from ServiceManagement import SMAppService
        return SMAppService.mainAppService()
    except ImportError:
        pass
    try:
        import objc as _objc
        _objc.loadBundle(
            "ServiceManagement",
            bundle_path="/System/Library/Frameworks/ServiceManagement.framework",
            module_globals=globals(),
        )
        svc_cls = globals().get("SMAppService")
        if svc_cls is not None:
            return svc_cls.mainAppService()
    except Exception:
        pass
    return None


def get_app_executable():
    """
    Return the binary path for the LaunchAgent plist (fallback path only).
    PyInstaller: sys.executable IS the compiled app binary.
    """
    if getattr(sys, "frozen", False):
        bundle = NSBundle.mainBundle()
        bp = bundle.bundlePath()
        if bp and bp.endswith(".app"):
            ep = bundle.executablePath()
            if ep and os.path.exists(ep):
                return ep
        return sys.executable
    bundle = NSBundle.mainBundle()
    bp = bundle.bundlePath()
    if bp and bp.endswith(".app"):
        ep = bundle.executablePath()
        if ep and os.path.exists(ep):
            return ep
    script_path = os.path.abspath(__file__)
    if os.path.exists(script_path):
        return sys.executable, script_path
    return sys.executable, None


def _build_program_arguments():
    result = get_app_executable()
    if isinstance(result, tuple):
        py_exe, script = result
        return [py_exe, script] if script else [py_exe]
    return [result]


# SMAppServiceStatusEnabled = 1  (from SMAppService.h)
_SM_ENABLED = 1


def is_launch_at_login_enabled():
    """
    Return True if launch-at-login is active.
    Uses SMAppService on macOS 13+, plist presence on older macOS.
    """
    svc = _get_sm_app_service()
    if svc is not None:
        try:
            return int(svc.status()) == _SM_ENABLED
        except Exception:
            pass
    return os.path.exists(LAUNCH_AGENT_PLIST)


def set_launch_at_login(enabled):
    """
    Enable or disable launch-at-login.

    Primary path — SMAppService (macOS 13+, shows in System Settings):
      register()   → adds to Login Items
      unregister() → removes from Login Items

    Fallback — LaunchAgent plist (macOS 12 and earlier):
      Just write/delete the file.  Do NOT call launchctl load — that triggers
      RunAtLoad immediately and spawns a duplicate instance right now.
    """
    svc = _get_sm_app_service()
    if svc is not None:
        try:
            if enabled:
                svc.registerAndReturnError_(None)
            else:
                svc.unregisterAndReturnError_(None)
            return   # SMAppService succeeded — no plist needed
        except Exception as e:
            notify("M2 Tracker – Launch at Login",
                   f"SMAppService error ({e}). Falling back to LaunchAgent.")

    # ── Fallback: LaunchAgent plist ──────────────────────────────────────────
    if enabled:
        prog_args = _build_program_arguments()
        plist_data = {
            "Label": BUNDLE_ID,
            "ProgramArguments": prog_args,
            "RunAtLoad": True,
            "KeepAlive": False,
        }
        try:
            os.makedirs(os.path.dirname(LAUNCH_AGENT_PLIST), exist_ok=True)
            with open(LAUNCH_AGENT_PLIST, "wb") as f:
                plistlib.dump(plist_data, f)
        except Exception as e:
            notify("M2 Tracker – Launch at Login",
                   f"Could not write LaunchAgent plist: {e}")
    else:
        if os.path.exists(LAUNCH_AGENT_PLIST):
            subprocess.call(["launchctl", "unload", LAUNCH_AGENT_PLIST],
                            stderr=subprocess.DEVNULL)
            try:
                os.remove(LAUNCH_AGENT_PLIST)
            except Exception as e:
                notify("M2 Tracker – Launch at Login",
                       f"Could not remove LaunchAgent plist: {e}")


# ── Day counting helpers ────────────────────────────────────────────────────────
def count_weekdays(start_date, end_date):
    if start_date > end_date:
        return 0
    days, current = 0, start_date
    while current <= end_date:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days

def count_all_days(start_date, end_date):
    return max((end_date - start_date).days, 0)

def count_grid_days(grid):
    return sum(1 for v in grid.values() if v.get("r") and v.get("q") and v.get("a"))


# ── UI helpers ─────────────────────────────────────────────────────────────────
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
    """Bring an alert window to front on the floating level."""
    try:
        alert.window().center()
        alert.window().setLevel_(NSFloatingWindowLevel)
        NSApp.activateIgnoringOtherApps_(True)
        alert.window().makeKeyAndOrderFront_(None)
    except Exception:
        pass

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
    cal = NSCalendar.currentCalendar()
    if current_date:
        comps = cal.components_fromDate_(
            NSCalendarUnitYear | NSCalendarUnitMonth | NSCalendarUnitDay,
            NSDate.date())
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

    def __init__(self, day, config, on_change, highlighted=False):
        self._day       = day
        self._config    = config
        self._on_change = on_change
        self._squares   = {}
        self._highlighted = highlighted

        self.view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, BOX_W, BOX_H))
        self.view.setWantsLayer_(True)
        self.view.layer().setCornerRadius_(4)

        if highlighted:
            from Quartz import CGColorCreateSRGB
            self.view.layer().setBorderWidth_(1.0)
            self.view.layer().setBorderColor_(
                CGColorCreateSRGB(0.20, 0.55, 1.00, 1.0)
            )

        self._set_bg(False)
        self._build()

    def _set_bg(self, all_done):
        from Quartz import CGColorCreateSRGB
        cg = CGColorCreateSRGB(0.12, 0.12, 0.12, 1.0) if all_done \
             else CGColorCreateSRGB(0.22, 0.22, 0.22, 1.0)
        self.view.layer().setBackgroundColor_(cg)

    def _build(self):
        key        = str(self._day)
        state      = self._config.get("grid", {}).get(key, {})
        total_sq_w = len(self.FIELDS) * DOT_SIZE + (len(self.FIELDS) - 1) * DOT_GAP
        x_start    = (BOX_W - total_sq_w) // 2
        y_sq       = 3

        from Quartz import CATextLayer, CGColorCreateSRGB
        num_layer = CATextLayer.layer()
        num_layer.setString_(str(self._day))
        num_layer.setFontSize_(7)
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
        self._panel.setLevel_(NSFloatingWindowLevel)
        self._panel.makeKeyAndOrderFront_(None)
        self._panel.orderFrontRegardless()

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
        self._toggle_handler = _ToggleHandler.alloc_init_with(
            self._toggle, self._toggle_changed)
        content.addSubview_(self._toggle)

        info_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(PAD * 2, y_toggle - 16, PANEL_W - PAD * 4, 14))
        info_label.setStringValue_("□ AMBOSS-Articles · □ AMBOSS-IMPP · □ Anki")
        info_label.setFont_(NSFont.systemFontOfSize_(10))
        info_label.setTextColor_(NSColor.secondaryLabelColor())
        info_label.setBezeled_(False)
        info_label.setDrawsBackground_(False)
        info_label.setEditable_(False)
        info_label.setSelectable_(False)
        content.addSubview_(info_label)

        # Remove the duplicated y_start assignment (was set twice in original)
        y_start = y_toggle - INFO_LABEL_H - INFO_LABEL_GAP - PAD * 2

        current_day = None
        if self._config.get("start_date"):
            try:
                start = datetime.strptime(
                    self._config["start_date"], "%Y-%m-%d").date()
                current_day = min(
                    max(count_weekdays(start, datetime.now().date()), 1),
                    TOTAL_DAYS)
            except (ValueError, TypeError):
                pass

        self._boxes = []
        for row in range(ROWS):
            week_lbl = NSTextField.alloc().initWithFrame_(
                NSMakeRect(PAD, y_start - row * (BOX_H + PAD) - BOX_H,
                           ROW_LABEL_W - 2, BOX_H))
            week_lbl.setStringValue_(f"W{row + 1}")
            week_lbl.setFont_(NSFont.monospacedSystemFontOfSize_weight_(9, 0.0))
            week_lbl.setTextColor_(NSColor.secondaryLabelColor())
            week_lbl.setBezeled_(False)
            week_lbl.setDrawsBackground_(False)
            week_lbl.setEditable_(False)
            week_lbl.setSelectable_(False)
            week_lbl.setAlignment_(1)
            content.addSubview_(week_lbl)

            for col in range(COLS):
                day = row * COLS + col + 1
                x   = PAD + ROW_LABEL_W + col * (BOX_W + PAD)
                y   = y_start - row * (BOX_H + PAD) - BOX_H
                box = DayBox(day, self._config, self._box_changed,
                             highlighted=(day == current_day))
                box.view.setFrame_(NSMakeRect(x, y, BOX_W, BOX_H))
                content.addSubview_(box.view)
                self._boxes.append(box)

        button_y = 8
        batch_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(PAD * 2, button_y, 110, 28))
        batch_btn.setTitle_("Batch Edit")
        batch_btn.setBezelStyle_(NSBezelStyleRounded)

        clear_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(PAD * 2 + 120, button_y, 110, 28))
        clear_btn.setTitle_("Clear All")
        clear_btn.setBezelStyle_(NSBezelStyleRounded)

        self._batch_handler = _ButtonHandler.alloc_init_with(self._batch_edit)
        self._clear_handler = _ButtonHandler.alloc_init_with(self._clear_all)

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
        """Close the panel so it will be rebuilt next time show() is called."""
        if self._panel:
            self._panel.close()
            self._panel = None

    def _clear_all(self, sender):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Clear all progress?")
        alert.setInformativeText_("This will reset every checkbox in the grid.")
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
        alert.setInformativeText_("How many days have already been completed?")
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 200, 24))
        field.setStringValue_("0")
        alert.setAccessoryView_(field)
        alert.addButtonWithTitle_("Apply")
        alert.addButtonWithTitle_("Cancel")
        present_alert(alert)
        if alert.runModal() != NSAlertFirstButtonReturn:
            return
        try:
            completed = int(field.stringValue())
        except (ValueError, TypeError):
            notify("M2 Tracker", "Batch Edit: please enter a valid number.")
            return
        completed = max(0, min(TOTAL_DAYS, completed))
        new_grid = {
            str(day): {"r": True, "q": True, "a": True}
            for day in range(1, completed + 1)
        }
        self._config["grid"] = new_grid
        save_config(self._config, self._prefs)
        self._reload_grid()
        self._on_change()


# ── Objective-C handler helpers ────────────────────────────────────────────────
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

        # Run first-launch prompts BEFORE syncing launch_at_login with reality.
        # If we synced first, the key would be written to prefs before
        # _ask_autostart() checks for its absence, and the prompt would never show.
        self._first_launch_setup()

        # Sync the in-memory launch_at_login pref with reality (plist presence).
        # Handles the case where the plist was removed externally or the app moved.
        actual_lal = is_launch_at_login_enabled()
        if self.prefs.get("launch_at_login", False) != actual_lal:
            self.prefs["launch_at_login"] = actual_lal
            save_prefs(self.prefs)

        # Load config — works whether the Mac just booted or iCloud is still syncing
        self.config = load_config(self.prefs)
        self.config.setdefault("grid", {})
        self.config.setdefault("use_grid", False)
        self.total_days = TOTAL_DAYS
        self._last_config_mtime = get_config_mtime(self.prefs)

        super(M2TrackerApp, self).__init__("M2", quit_button=None)
        self._quit_item  = rumps.MenuItem("Quit M2 Tracker",
                                          callback=rumps.quit_application)
        self._grid_panel = GridPanel(self.config, self.prefs, self._grid_changed)
        self.update_display()

    # ── First-launch prompts ────────────────────────────────────────────────────
    def _first_launch_setup(self):
        """
        On the very first run: ask where to store data and whether to auto-launch.
        On every subsequent iCloud launch: restore the saved iCloud folder path
        from prefs (no file picker needed, no security-scoped bookmark).
        """
        if "use_icloud" not in self.prefs:
            self._ask_storage()
        elif self.prefs.get("use_icloud"):
            # Restore the iCloud path saved in prefs on previous launch.
            saved_path = self.prefs.get("icloud_dir")
            if saved_path and os.path.isdir(saved_path):
                global ICLOUD_DIR, ICLOUD_CONFIG_FILE
                ICLOUD_DIR = saved_path
                ICLOUD_CONFIG_FILE = os.path.join(ICLOUD_DIR, "m2_tracker_config.json")
            else:
                # Path not saved or folder gone — try the default location
                if not os.path.isdir(ICLOUD_DIR):
                    notify("M2 Tracker – iCloud",
                           "iCloud folder not found. Please switch storage to iCloud again.")

        if "launch_at_login" not in self.prefs:
            self._ask_autostart()

    def _ask_storage(self):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Where should M2 Tracker save your data?")
        alert.setInformativeText_(
            "iCloud keeps your progress synced across all your Macs.\n"
            "Local stores the file only on this Mac.")
        alert.addButtonWithTitle_("iCloud")
        alert.addButtonWithTitle_("Local")
        NSApp.activateIgnoringOtherApps_(True)
        result = alert.runModal()
        use_icloud = (result == NSAlertFirstButtonReturn)
        self.prefs["use_icloud"] = use_icloud
        if use_icloud:
            try:
                os.makedirs(ICLOUD_DIR, exist_ok=True)
            except OSError:
                pass
            self.prefs["icloud_dir"] = ICLOUD_DIR
        save_prefs(self.prefs)

    def _ask_autostart(self):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Launch M2 Tracker at Login?")
        alert.setInformativeText_(
            "Should M2 Tracker start automatically every time you log in?")
        alert.addButtonWithTitle_("Yes")
        alert.addButtonWithTitle_("No")
        NSApp.activateIgnoringOtherApps_(True)
        result = alert.runModal()
        enabled = (result == NSAlertFirstButtonReturn)
        self.prefs["launch_at_login"] = enabled
        save_prefs(self.prefs)
        set_launch_at_login(enabled)      # Actually installs/removes the plist

    # ── Callbacks ───────────────────────────────────────────────────────────────
    def _grid_changed(self):
        save_config(self.config, self.prefs)
        self._last_config_mtime = get_config_mtime(self.prefs)
        self.update_display()

    def get_start_date(self):
        raw = self.config.get("start_date")
        if raw:
            try:
                return datetime.strptime(raw, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                notify("M2 Tracker – Config Warning",
                       f"start_date value '{raw}' is not a valid date (YYYY-MM-DD). "
                       "Please reset the start date.")
        return None

    def get_next_check_day(self):
        grid = self.config.get("grid", {})
        last_used_day = 0
        for day_str, state in grid.items():
            if any(state.get(k, False) for k in ("r", "q", "a")):
                try:
                    last_used_day = max(last_used_day, int(day_str))
                except (ValueError, TypeError):
                    pass
        return min(last_used_day + 1, TOTAL_DAYS)

    # ── Storage / autostart toggles ─────────────────────────────────────────────
    def toggle_storage(self, _):
        use_icloud     = self.prefs.get("use_icloud", False)
        new_use_icloud = not use_icloud
        dest = "iCloud" if new_use_icloud else "Local"

        alert = NSAlert.alloc().init()
        alert.setMessageText_(f"Switch storage to {dest}?")
        alert.setInformativeText_(
            f"Your current config will be copied to {dest} storage. "
            "The app will sync from there going forward.")
        alert.addButtonWithTitle_("Switch")
        alert.addButtonWithTitle_("Cancel")
        NSApp.activateIgnoringOtherApps_(True)
        if alert.runModal() != NSAlertFirstButtonReturn:
            return

        old_prefs = dict(self.prefs)
        self.prefs["use_icloud"] = new_use_icloud
        if new_use_icloud:
            try:
                os.makedirs(ICLOUD_DIR, exist_ok=True)
            except OSError:
                pass
            self.prefs["icloud_dir"] = ICLOUD_DIR

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
        set_launch_at_login(enabled)    # Install or remove the LaunchAgent plist
        self.update_display()

    # ── Display ─────────────────────────────────────────────────────────────────
    def update_display(self):
        start = self.get_start_date()
        today = datetime.now().date()

        # ── Storage / login items (used in both branches) ───────────────────────
        use_icloud    = self.prefs.get("use_icloud", False)
        updated_at    = self.config.get("updated_at", "—")
        try:
            updated_short = datetime.strptime(
                updated_at, "%Y-%m-%d %H:%M:%S").strftime("%H:%M")
        except Exception:
            updated_short = updated_at
        storage_label = f"{'iCloud' if use_icloud else 'Local'}  ·  updated {updated_short}"
        storage_item  = rumps.MenuItem(storage_label, callback=self.toggle_storage)
        login_enabled = self.prefs.get("launch_at_login", False)
        login_label   = "Launch at Login  ✓" if login_enabled else "Launch at Login"
        login_item    = rumps.MenuItem(login_label, callback=self.toggle_launch_at_login)

        if not start:
            # ── No start date yet: show minimal menu ────────────────────────────
            self.title = "M2: ?"
            self.menu.clear()
            no_date_note = rumps.MenuItem("No start date set")
            no_date_note.set_callback(None)
            self.menu = [
                no_date_note,
                None,
                rumps.MenuItem("Set Start Date", callback=self.set_start_date),
                None,
                storage_item,
                login_item,
                None,
                self._quit_item,
            ]
            return

        # ── Full display ────────────────────────────────────────────────────────
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

        weekday_item = rumps.MenuItem(
            make_row("Weekdays", make_progress_bar(weekday_count, self.total_days)))
        weekday_item.set_callback(None)
        calendar_item = rumps.MenuItem(
            make_row("Calendar        ",
                     make_progress_bar(calendar_count, self.total_days)))
        calendar_item.set_callback(None)

        if diff == 1:    efficiency = "1 day ahead"
        elif diff == -1: efficiency = "1 day behind"
        elif diff > 1:   efficiency = f"{diff} days ahead"
        elif diff < -1:  efficiency = f"{abs(diff)} days behind"
        else:            efficiency = "on track"

        actual_text = make_row("Actual             ",
                               make_progress_bar(actual_day, self.total_days),
                               f"({efficiency})")
        actual_item = colored_menu_item(actual_text, status_color)

        offset_sign = f"+{offset}" if offset >= 0 else str(offset)
        plus_item   = rumps.MenuItem(
            f"+ offset Day (current: {offset_sign})",
            callback=None if use_grid else self.add_offset_day)
        minus_item  = rumps.MenuItem(
            f"- offset Day (current: {offset_sign})",
            callback=None if use_grid else self.remove_offset_day)
        if use_grid:
            plus_item._menuitem.setEnabled_(False)
            minus_item._menuitem.setEnabled_(False)

        grid_label = "Day Grid  ✓" if use_grid else "Day Grid"

        next_day   = self.get_next_check_day()
        today_item = rumps.MenuItem(
            f"Check Day {next_day}",
            callback=self.mark_today_shortcut if use_grid else None)
        if not use_grid:
            today_item._menuitem.setEnabled_(False)

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

    # ── Action handlers ─────────────────────────────────────────────────────────
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
            notify("M2 Tracker", f"Start date set — tracking from {chosen}")

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
        bundle  = NSBundle.mainBundle()
        name    = bundle.objectForInfoDictionaryKey_("CFBundleDisplayName") or \
                  bundle.objectForInfoDictionaryKey_("CFBundleName") or "M2 Tracker"
        version = bundle.objectForInfoDictionaryKey_("CFBundleShortVersionString") or "—"
        build   = bundle.objectForInfoDictionaryKey_("CFBundleVersion") or "—"
        info    = bundle.objectForInfoDictionaryKey_("CFBundleGetInfoString") or ""
        copy_   = bundle.objectForInfoDictionaryKey_("NSHumanReadableCopyright") or ""
        alert   = NSAlert.alloc().init()
        alert.setMessageText_(f"{name}  v{version}")
        alert.setInformativeText_(f"Build {build}\n\n{info}\n\n{copy_}")
        alert.addButtonWithTitle_("OK")
        present_alert(alert)
        alert.runModal()

    # ── Periodic refresh ────────────────────────────────────────────────────────
    @rumps.timer(60)
    def refresh(self, _):
        """
        Every 60 s: check for external config changes (important for iCloud sync).
        In iCloud mode also triggers a download if the file was evicted.
        """
        try:
            if self.prefs.get("use_icloud"):
                ensure_icloud_downloaded(ICLOUD_CONFIG_FILE)

            mtime = get_config_mtime(self.prefs)
            if mtime and mtime != self._last_config_mtime:
                new_config = load_config(self.prefs)
                new_config.setdefault("grid", {})
                new_config.setdefault("use_grid", False)
                self.config = new_config
                self._last_config_mtime = mtime
                # Rebuild the grid panel with the new config reference
                self._grid_panel = GridPanel(self.config, self.prefs, self._grid_changed)

            self.update_display()
        except Exception as e:
            notify("M2 Tracker – Refresh Error",
                   f"Unexpected error during periodic refresh: {e}")


if __name__ == "__main__":
    # ── Single-instance guard ────────────────────────────────────────────────
    # Prevents a second process when SMAppService/LaunchAgent launches the app
    # at login while a previous session is still running (or launches it twice).
    # Uses a PID-lockfile: if the file exists and the recorded PID is still alive,
    # exit immediately; otherwise claim the lock and continue.
    import fcntl
    _LOCK_FILE_PATH = os.path.expanduser("~/.m2_tracker.lock")
    _lock_fh = open(_LOCK_FILE_PATH, "w")
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Another instance is running — exit silently
        sys.exit(0)
    # Write our PID so it can be inspected if needed
    _lock_fh.write(str(os.getpid()))
    _lock_fh.flush()
    # Keep _lock_fh open for the lifetime of the process; closing it releases the lock.

    app = M2TrackerApp()
    try:
        app.run()
    finally:
        # Release the security-scoped resource cleanly on every exit path
        stop_icloud_access()
        # Release the instance lock
        try:
            fcntl.flock(_lock_fh, fcntl.LOCK_UN)
            _lock_fh.close()
            os.remove(_LOCK_FILE_PATH)
        except Exception:
            pass