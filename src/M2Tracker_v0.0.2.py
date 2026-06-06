APP_NAME = "M2 Tracker"
APP_VERSION = "0.0.2"
APP_AUTHOR = "Spike Murphy Müller"

import rumps
from datetime import datetime, timedelta
import json
import os
# PyObjC for native date picker + colored menu items
from AppKit import (
    NSAlert, NSDatePicker, NSDatePickerStyleClockAndCalendar,
    NSDatePickerModeRange, NSDatePickerModeSingle,
    NSAlertFirstButtonReturn, NSTextField, NSView, NSMakeRect,
    NSDatePickerElementFlagYearMonthDay,
    NSAttributedString, NSForegroundColorAttributeName,
    NSFont, NSFontAttributeName, NSColor,
    NSApp, NSFloatingWindowLevel,
)
from Foundation import (
    NSDate, NSCalendar, NSCalendarUnitYear, NSCalendarUnitMonth, NSCalendarUnitDay,
    NSMutableDictionary,
)

CONFIG_FILE = os.path.expanduser("~/.m2_tracker_config.json")

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {"start_date": None, "offset_days": 0}

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)

def count_weekdays(start_date, end_date):
    """Count weekdays (Mon-Fri) between two dates, inclusive of start, exclusive of end."""
    if start_date > end_date:
        return 0
    days = 0
    current = start_date
    while current < end_date:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days

def count_all_days(start_date, end_date):
    return (end_date - start_date).days

def make_progress_bar(current, total, width=24):
    """Progress bar with day count embedded in the center.
    e.g. [════017/100─────────────]  (always 7 chars: '017/100')
    """
    label = f"{current:03d}/{total}"   # e.g. '017/100' — always 7 chars when total<=999
    filled = 0 if total == 0 else min(int((current / total) * width), width)

    center = width // 2 - len(label) // 2   # center the label
    bar = ["─"] * width
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
    """Center an NSAlert on screen and raise it above all other windows."""
    alert.window().center()
    alert.window().setLevel_(NSFloatingWindowLevel)
    NSApp.activateIgnoringOtherApps_(True)
    alert.window().makeKeyAndOrderFront_(None)

def colored_menu_item(text, color):
    """
    Return a rumps.MenuItem whose text is rendered in the given NSColor.
    We keep the item enabled (no set_callback(None)) so macOS doesn't
    grey it out and override the attributedTitle color. Instead we
    explicitly enable it and give it a no-op callback.
    """
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
        cal = NSCalendar.currentCalendar()
        comps = cal.components_fromDate_(
            NSCalendarUnitYear | NSCalendarUnitMonth | NSCalendarUnitDay,
            NSDate.date()
        )
        comps.setYear_(current_date.year)
        comps.setMonth_(current_date.month)
        comps.setDay_(current_date.day)
        ns_date = cal.dateFromComponents_(comps)
        picker.setDateValue_(ns_date)
    else:
        picker.setDateValue_(NSDate.date())

    container = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 220, 148))
    container.addSubview_(picker)
    alert.setAccessoryView_(container)

    present_alert(alert)

    response = alert.runModal()
    if response == NSAlertFirstButtonReturn:
        ns_date = picker.dateValue()
        cal = NSCalendar.currentCalendar()
        comps = cal.components_fromDate_(
            NSCalendarUnitYear | NSCalendarUnitMonth | NSCalendarUnitDay,
            ns_date
        )
        return datetime(comps.year(), comps.month(), comps.day()).date()
    return None


class M2TrackerApp(rumps.App):
    def __init__(self):
        self.config = load_config()
        self.total_days = 100

        super(M2TrackerApp, self).__init__("M2", quit_button=None)
        self._quit_item = rumps.MenuItem("Quit M2 Tracker", callback=rumps.quit_application)
        self.update_display()

    def get_start_date(self):
        if self.config.get("start_date"):
            return datetime.strptime(self.config["start_date"], "%Y-%m-%d").date()
        return None

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

        weekday_count = count_weekdays(start, today)
        calendar_count = count_all_days(start, today)
        offset = self.config.get("offset_days", 0)
        actual_day = weekday_count + offset

        if offset > 0:
            status_color = NSColor.systemGreenColor()
            status_symbol = "▲"
        elif offset < 0:
            status_color = NSColor.systemRedColor()
            status_symbol = "▼"
        else:
            status_color = NSColor.labelColor()
            status_symbol = ""

        self.title = f"D{actual_day}{status_symbol}"

        self.menu.clear()

        start_item = rumps.MenuItem(f"Start date:  {start.strftime('%Y-%m-%d')}")
        start_item.set_callback(None)

        weekday_bar = make_progress_bar(weekday_count, self.total_days)
        weekday_item = rumps.MenuItem(make_row("Weekdays", weekday_bar))
        weekday_item.set_callback(None)

        calendar_bar = make_progress_bar(calendar_count, self.total_days)
        calendar_item = rumps.MenuItem(make_row("Calendar        ", calendar_bar))
        calendar_item.set_callback(None)

        diff = actual_day - weekday_count
        if diff == 1:
            efficiency = "1 day ahead"
        elif diff == -1:
            efficiency = "1 day behind"
        elif diff > 1:
            efficiency = f"{diff} days ahead"
        elif diff < -1:
            efficiency = f"{abs(diff)} days behind"
        else:
            efficiency = "on track"

        actual_bar = make_progress_bar(actual_day, self.total_days)
        actual_text = make_row("Actual             ", actual_bar, f"({efficiency})")
        actual_item = colored_menu_item(actual_text, status_color)

        offset_sign = f"+{offset}" if offset >= 0 else str(offset)
        plus_item  = rumps.MenuItem(f"+ offset Day (current: {offset_sign})", callback=self.add_offset_day)
        minus_item = rumps.MenuItem(f"- offset Day (current: {offset_sign})", callback=self.remove_offset_day)

        self.menu = [
            start_item,
            None,
            weekday_item,
            calendar_item,
            actual_item,
            None,
            plus_item,
            minus_item,
            None,
            rumps.MenuItem("Set Start Date", callback=self.set_start_date),
            rumps.MenuItem("Reset offset Days", callback=self.reset_offset),
            None,
            rumps.MenuItem("About M2 Tracker", callback=self.show_about),
            self._quit_item,
        ]

    def set_start_date(self, _):
        current = self.get_start_date()
        chosen = pick_date_with_calendar(current)
        if chosen:
            self.config["start_date"] = chosen.strftime("%Y-%m-%d")
            self.config["offset_days"] = 0
            save_config(self.config)
            self.update_display()
            rumps.notification("M2 Tracker", "Start date set", f"Tracking from {chosen}")

    def add_offset_day(self, _):
        self.config["offset_days"] = self.config.get("offset_days", 0) + 1
        save_config(self.config)
        self.update_display()

    def remove_offset_day(self, _):
        self.config["offset_days"] = self.config.get("offset_days", 0) - 1
        save_config(self.config)
        self.update_display()

    def reset_offset(self, _):
        self.config["offset_days"] = 0
        save_config(self.config)
        self.update_display()

    def show_about(self, _):
        from Foundation import NSBundle
        bundle = NSBundle.mainBundle()
        name    = bundle.objectForInfoDictionaryKey_("CFBundleDisplayName") or \
                  bundle.objectForInfoDictionaryKey_("CFBundleName") or "M2 Tracker"
        version = bundle.objectForInfoDictionaryKey_("CFBundleShortVersionString") or "—"
        build   = bundle.objectForInfoDictionaryKey_("CFBundleVersion") or "—"
        info    = bundle.objectForInfoDictionaryKey_("CFBundleGetInfoString") or ""
        copy    = bundle.objectForInfoDictionaryKey_("NSHumanReadableCopyright") or ""

        alert = NSAlert.alloc().init()
        alert.setMessageText_(f"{name}  v{version}")
        alert.setInformativeText_(f"Build {build}\n\n{info}\n\n{copy}")
        alert.addButtonWithTitle_("OK")

        present_alert(alert)
        alert.runModal()

    @rumps.timer(60)
    def refresh(self, _):
        self.update_display()


if __name__ == "__main__":
    M2TrackerApp().run()