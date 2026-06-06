# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

# Hidden imports for PyObjC and rumps that might be missed
hiddenimports = [
    'rumps',
    'AppKit',
    'Foundation',
    # Specific imports from AppKit and Foundation used in the script
    'AppKit.NSAlert',
    'AppKit.NSDatePicker',
    'AppKit.NSDatePickerStyleClockAndCalendar',
    'AppKit.NSDatePickerModeRange',
    'AppKit.NSDatePickerModeSingle',
    'AppKit.NSAlertFirstButtonReturn',
    'AppKit.NSTextField',
    'AppKit.NSView',
    'AppKit.NSMakeRect',
    'AppKit.NSDatePickerElementFlagYearMonthDay',
    'AppKit.NSAttributedString',
    'AppKit.NSForegroundColorAttributeName',
    'AppKit.NSFont',
    'AppKit.NSFontAttributeName',
    'AppKit.NSColor',
    'Foundation.NSDate',
    'Foundation.NSCalendar',
    'Foundation.NSCalendarUnitYear',
    'Foundation.NSCalendarUnitMonth',
    'Foundation.NSCalendarUnitDay',
    'Foundation.NSMutableDictionary',
]

a = Analysis(
    ['M2Tracker.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='M2Tracker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['M2TrackerIcon.icns'],
    onefile=True,
)

app = BUNDLE(
    exe,
    name='M2Tracker.app',
    icon='M2TrackerIcon.icns',
    bundle_identifier='com.example.m2tracker',
    version='1.0.0',
    info_plist={
        'LSUIElement': True,
        'CFBundleName': 'M2Tracker',
        'CFBundleDisplayName': 'M2Tracker',
        'CFBundleShortVersionString': '0.0.2',
        'CFBundleVersion': '20260606',
        'CFBundleExecutable': 'M2Tracker',
        'CFBundleIconFile': 'M2TrackerIcon',
        'NSHumanReadableCopyright': '© 2026 Spike Murphy Müller · MIT License',
        'CFBundleGetInfoString': 'M2 Tracker v0.0.2 – Developed by Spike Murphy Müller',
    }
)