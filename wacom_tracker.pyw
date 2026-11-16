import atexit
import calendar
import ctypes
from ctypes import wintypes
import datetime
import io
import json
import math
import os
import signal
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any
from PIL import Image, ImageDraw
from pynput import mouse
import pystray

# Set an explicit AppUserModelID so Windows taskbar uses the custom window icon
# instead of grouping under the generic python.exe / pythonw.exe icon
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "WacomTracker.StylusDistanceMonitor.1"
    )
except Exception:
    pass

# Enable per-monitor DPI awareness so Windows reports true physical pixels
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

# =====================================================================
# DEFAULT FALLBACK VALUES (Overridden & persisted via Setup Dialogs)
# =====================================================================
DEFAULT_TABLET_MODEL = "Wacom Intuos Medium (CTL-6100)"
DEFAULT_TABLET_WIDTH_MM = 216.0
DEFAULT_TABLET_HEIGHT_MM = 135.0

TABLET_PRESETS: list[tuple[str, float | None, float | None]] = [
    ("Wacom Intuos Small (CTL-4100)", 152.0, 95.0),
    ("Wacom Intuos Medium (CTL-6100)", 216.0, 135.0),
    ("Wacom Intuos Pro Small (PTH-460)", 160.0, 100.0),
    ("Wacom Intuos Pro Medium (PTH-660)", 224.0, 148.0),
    ("Wacom Intuos Pro Large (PTH-860)", 311.0, 216.0),
    ("One by Wacom Small (CTL-472)", 152.0, 95.0),
    ("One by Wacom Medium (CTL-672)", 216.0, 135.0),
    ("Wacom One 13 (DTC-133)", 294.0, 166.0),
    ("Wacom Cintiq 16 (DTK-1660)", 344.16, 193.59),
    ("Wacom Cintiq Pro 16 (DTH-167)", 345.0, 194.0),
    ("Wacom Cintiq 16 (DTK-168)", 345.0, 215.0),
    ("Wacom Cintiq 22 (DTK-2260)", 476.0, 268.0),
    ("Wacom Cintiq 24 (DTK-246)", 527.0, 296.0),
    ("Custom Size...", None, None),
]

DEFAULT_DISPLAY_NAME = "Primary Display"
DEFAULT_DISPLAY_WIDTH_PX = 3840
DEFAULT_DISPLAY_HEIGHT_PX = 2160
DEFAULT_DISPLAY_OFFSET_X = 0
DEFAULT_DISPLAY_OFFSET_Y = 0

# Ignore single-frame teleports larger than this (in mm)
MAX_STEP_MM = 60.0

# Always store save data right next to this script regardless of launch folder
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_FILE = os.path.join(SCRIPT_DIR, "wacom_tracker_data.json")

UNITS = ("km", "m", "mm")
UNIT_DIVISORS = {
    "km": 1_000_000.0,
    "m": 1_000.0,
    "mm": 1.0,
}

# =====================================================================
# WIN32 RAW INPUT, HID & MONITOR DEFINITIONS
# =====================================================================
WM_INPUT = 0x00FF
RID_INPUT = 0x10000003
RIDEV_PAGEONLY = 0x00000020
RIDEV_INPUTSINK = 0x00000100
RIDI_PREPARSEDDATA = 0x20000005
RIDI_DEVICENAME = 0x20000007
RIDI_DEVICEINFO = 0x2000000B
RIM_TYPEMOUSE = 0
RIM_TYPEHID = 2
GWLP_WNDPROC = -4
GA_ROOT = 2
MONITORINFOF_PRIMARY = 0x00000001
MONITOR_DEFAULTTONEAREST = 0x00000002
SM_CXSIZEFRAME = 32
SM_CYSIZEFRAME = 33
SM_CYCAPTION = 4
SM_CXPADDEDBORDER = 92

WACOM_VID_STR = "VID_056A"
WACOM_VID_INT = 0x056A

HIDP_INPUT = 0
HIDP_STATUS_SUCCESS = 0x00110000

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)
PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HMONITOR,
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    wintypes.LPARAM,
)


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class RAWHID(ctypes.Structure):
    _fields_ = [
        ("dwSizeHid", wintypes.DWORD),
        ("dwCount", wintypes.DWORD),
    ]


class RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [
        ("hDevice", wintypes.HANDLE),
        ("dwType", wintypes.DWORD),
    ]


class RID_DEVICE_INFO_HID(ctypes.Structure):
    _fields_ = [
        ("dwVendorId", wintypes.DWORD),
        ("dwProductId", wintypes.DWORD),
        ("dwVersionNumber", wintypes.DWORD),
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
    ]


class RID_DEVICE_INFO_UNION(ctypes.Union):
    _fields_ = [
        ("hid", RID_DEVICE_INFO_HID),
        ("raw_bytes", ctypes.c_byte * 24),
    ]


class RID_DEVICE_INFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("dwType", wintypes.DWORD),
        ("u", RID_DEVICE_INFO_UNION),
    ]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


class HIDP_VALUE_CAPS_RANGE(ctypes.Structure):
    _fields_ = [
        ("UsageMin", wintypes.USHORT),
        ("UsageMax", wintypes.USHORT),
        ("StringMin", wintypes.USHORT),
        ("StringMax", wintypes.USHORT),
        ("DesignatorMin", wintypes.USHORT),
        ("DesignatorMax", wintypes.USHORT),
        ("DataIndexMin", wintypes.USHORT),
        ("DataIndexMax", wintypes.USHORT),
    ]


class HIDP_VALUE_CAPS_NOT_RANGE(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("Reserved1", wintypes.USHORT),
        ("StringIndex", wintypes.USHORT),
        ("Reserved2", wintypes.USHORT),
        ("DesignatorIndex", wintypes.USHORT),
        ("Reserved3", wintypes.USHORT),
        ("DataIndex", wintypes.USHORT),
        ("Reserved4", wintypes.USHORT),
    ]


class HIDP_VALUE_CAPS_UNION(ctypes.Union):
    _fields_ = [
        ("Range", HIDP_VALUE_CAPS_RANGE),
        ("NotRange", HIDP_VALUE_CAPS_NOT_RANGE),
    ]


class HIDP_VALUE_CAPS(ctypes.Structure):
    _fields_ = [
        ("UsagePage", wintypes.USHORT),
        ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte),
        ("BitField", wintypes.USHORT),
        ("LinkCollection", wintypes.USHORT),
        ("LinkUsage", wintypes.USHORT),
        ("LinkUsagePage", wintypes.USHORT),
        ("IsRange", ctypes.c_ubyte),
        ("IsStringRange", ctypes.c_ubyte),
        ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte),
        ("HasNull", ctypes.c_ubyte),
        ("Reserved", ctypes.c_ubyte),
        ("BitSize", wintypes.USHORT),
        ("ReportCount", wintypes.USHORT),
        ("Reserved2", wintypes.USHORT * 5),
        ("UnitsExp", wintypes.ULONG),
        ("Units", wintypes.ULONG),
        ("LogicalMin", wintypes.LONG),
        ("LogicalMax", wintypes.LONG),
        ("PhysicalMin", wintypes.LONG),
        ("PhysicalMax", wintypes.LONG),
        ("u", HIDP_VALUE_CAPS_UNION),
    ]


user32 = ctypes.windll.user32
hid = ctypes.windll.hid
kernel32 = ctypes.windll.kernel32

user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND

user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

user32.EnumDisplayMonitors.argtypes = [
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    MONITORENUMPROC,
    wintypes.LPARAM,
]
user32.EnumDisplayMonitors.restype = wintypes.BOOL

user32.GetMonitorInfoW.argtypes = [
    wintypes.HMONITOR,
    ctypes.POINTER(MONITORINFOEXW),
]
user32.GetMonitorInfoW.restype = wintypes.BOOL

user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HMONITOR

user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

user32.GetRawInputDeviceList.argtypes = [
    ctypes.POINTER(RAWINPUTDEVICELIST),
    ctypes.POINTER(wintypes.UINT),
    wintypes.UINT,
]
user32.GetRawInputDeviceList.restype = wintypes.UINT

user32.GetRawInputData.argtypes = [
    wintypes.HANDLE,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.UINT),
    wintypes.UINT,
]
user32.GetRawInputData.restype = wintypes.UINT

user32.GetRawInputDeviceInfoW.argtypes = [
    wintypes.HANDLE,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.UINT),
]
user32.GetRawInputDeviceInfoW.restype = wintypes.UINT

user32.RegisterRawInputDevices.argtypes = [
    ctypes.POINTER(RAWINPUTDEVICE),
    wintypes.UINT,
    wintypes.UINT,
]
user32.RegisterRawInputDevices.restype = wintypes.BOOL

user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, WNDPROC]
user32.SetWindowLongPtrW.restype = ctypes.c_void_p

user32.CallWindowProcW.argtypes = [
    ctypes.c_void_p,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallWindowProcW.restype = LRESULT

kernel32.SetConsoleCtrlHandler.argtypes = [PHANDLER_ROUTINE, wintypes.BOOL]
kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL

hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidP_GetCaps.restype = wintypes.LONG

hid.HidP_GetValueCaps.argtypes = [
    ctypes.c_int,
    ctypes.POINTER(HIDP_VALUE_CAPS),
    ctypes.POINTER(wintypes.USHORT),
    ctypes.c_void_p,
]
hid.HidP_GetValueCaps.restype = wintypes.LONG

hid.HidP_GetUsageValue.argtypes = [
    ctypes.c_int,
    wintypes.USHORT,
    wintypes.USHORT,
    wintypes.USHORT,
    ctypes.POINTER(wintypes.ULONG),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.ULONG,
]
hid.HidP_GetUsageValue.restype = wintypes.LONG

hid.HidP_GetUsages.argtypes = [
    ctypes.c_int,
    wintypes.USHORT,
    wintypes.USHORT,
    ctypes.POINTER(wintypes.USHORT),
    ctypes.POINTER(wintypes.ULONG),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.ULONG,
]
hid.HidP_GetUsages.restype = wintypes.LONG


def _create_app_icon_image() -> Image.Image:
    """Generates the 64x64 stylus/pen icon shared by the system tray and all windows."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded dark badge background
    draw.rounded_rectangle(
        (2, 2, 61, 61),
        radius=14,
        fill=(24, 24, 27, 255),
        outline=(56, 189, 248, 255),
        width=3,
    )

    # Diagonal stylus body
    draw.polygon(
        [(43, 13), (51, 21), (26, 46), (18, 38)],
        fill=(56, 189, 248, 255),
    )
    # Stylus nib
    draw.polygon(
        [(18, 38), (26, 46), (13, 51)],
        fill=(244, 244, 245, 255),
    )
    # Grip accent ring
    draw.line([(36, 20), (44, 28)], fill=(24, 24, 27, 255), width=3)

    return img


_WINDOW_ICON_PHOTOS: list[tk.PhotoImage] = []


def _apply_window_icon(win: tk.Wm, as_default: bool = False):
    """Applies the stylus icon across multiple resolutions to a Tk or Toplevel window."""
    if not _WINDOW_ICON_PHOTOS:
        base_img = _create_app_icon_image()
        for sz in (16, 32, 48, 64):
            resized = base_img.resize((sz, sz), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            resized.save(buf, format="PNG")
            _WINDOW_ICON_PHOTOS.append(tk.PhotoImage(data=buf.getvalue()))
    try:
        win.iconphoto(as_default, *_WINDOW_ICON_PHOTOS)
    except tk.TclError:
        pass


def _position_window_on_parent(
    win: tk.Toplevel,
    parent: tk.Misc,
    width: int | None = None,
    height: int | None = None,
):
    """Centers `win` over `parent` and clamps its full outer frame inside the monitor's work area."""
    parent.update_idletasks()
    win.update_idletasks()

    client_w = width if width is not None else win.winfo_reqwidth()
    client_h = height if height is not None else win.winfo_reqheight()

    # Calculate outer frame chrome (title bar + borders) so nothing clips off-screen
    border_x = user32.GetSystemMetrics(SM_CXSIZEFRAME) + user32.GetSystemMetrics(
        SM_CXPADDEDBORDER
    )
    border_y = user32.GetSystemMetrics(SM_CYSIZEFRAME) + user32.GetSystemMetrics(
        SM_CXPADDEDBORDER
    )
    caption_h = user32.GetSystemMetrics(SM_CYCAPTION)

    outer_w = client_w + (border_x * 2)
    outer_h = client_h + caption_h + (border_y * 2)

    # Query the work area (excluding taskbar) of the monitor hosting the parent window
    parent_hwnd = user32.GetAncestor(parent.winfo_id(), GA_ROOT) or parent.winfo_id()
    h_mon = user32.MonitorFromWindow(parent_hwnd, MONITOR_DEFAULTTONEAREST)
    mon_info = MONITORINFOEXW()
    mon_info.cbSize = ctypes.sizeof(MONITORINFOEXW)

    if h_mon and user32.GetMonitorInfoW(h_mon, ctypes.byref(mon_info)):
        work = mon_info.rcWork
        wx0, wy0, wx1, wy1 = (
            int(work.left),
            int(work.top),
            int(work.right),
            int(work.bottom),
        )
    else:
        wx0, wy0 = 0, 0
        wx1, wy1 = win.winfo_screenwidth(), win.winfo_screenheight()

    if parent.winfo_viewable():
        # Convert parent's client origin to outer window origin
        px = parent.winfo_rootx() - border_x
        py = parent.winfo_rooty() - caption_h - border_y
        pw = parent.winfo_width() + (border_x * 2)
        ph = parent.winfo_height() + caption_h + (border_y * 2)
        x = px + (pw - outer_w) // 2
        y = py + (ph - outer_h) // 2
    else:
        x = wx0 + ((wx1 - wx0) - outer_w) // 2
        y = wy0 + ((wy1 - wy0) - outer_h) // 2

    # Clamp full outer window rectangle inside the monitor's usable work area
    max_x = max(wx0, wx1 - outer_w)
    max_y = max(wy0, wy1 - outer_h)
    x = max(wx0, min(x, max_x))
    y = max(wy0, min(y, max_y))

    if width is not None and height is not None:
        win.geometry(f"{client_w}x{client_h}+{x}+{y}")
    else:
        win.geometry(f"+{x}+{y}")


def detect_connected_displays() -> list[dict]:
    """Queries Windows for all active monitors with true physical resolution & offsets."""
    raw_monitors: list[dict] = []

    def _enum_cb(h_mon, _hdc, _lprc, _lparam) -> bool:
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(h_mon, ctypes.byref(info)):
            r = info.rcMonitor
            w = int(r.right - r.left)
            h = int(r.bottom - r.top)
            ox = int(r.left)
            oy = int(r.top)
            is_primary = bool(info.dwFlags & MONITORINFOF_PRIMARY)
            raw_monitors.append(
                {
                    "device": info.szDevice,
                    "width": w,
                    "height": h,
                    "offset_x": ox,
                    "offset_y": oy,
                    "is_primary": is_primary,
                }
            )
        return True

    cb_ref = MONITORENUMPROC(_enum_cb)
    user32.EnumDisplayMonitors(None, None, cb_ref, 0)

    if not raw_monitors:
        return [
            {
                "label": f"{DEFAULT_DISPLAY_NAME} — {DEFAULT_DISPLAY_WIDTH_PX} × {DEFAULT_DISPLAY_HEIGHT_PX} @ (0, 0)",
                "short_name": DEFAULT_DISPLAY_NAME,
                "width": DEFAULT_DISPLAY_WIDTH_PX,
                "height": DEFAULT_DISPLAY_HEIGHT_PX,
                "offset_x": DEFAULT_DISPLAY_OFFSET_X,
                "offset_y": DEFAULT_DISPLAY_OFFSET_Y,
                "is_primary": True,
            }
        ]

    raw_monitors.sort(
        key=lambda m: (not m["is_primary"], m["offset_x"], m["offset_y"])
    )

    results: list[dict] = []
    for idx, m in enumerate(raw_monitors, start=1):
        tag = "Primary" if m["is_primary"] else m["device"].replace("\\\\.\\", "")
        short_name = f"Display {idx} ({tag})"
        label = (
            f"{short_name} — {m['width']} × {m['height']} "
            f"@ ({m['offset_x']}, {m['offset_y']})"
        )
        results.append(
            {
                "label": label,
                "short_name": short_name,
                "width": m["width"],
                "height": m["height"],
                "offset_x": m["offset_x"],
                "offset_y": m["offset_y"],
                "is_primary": m["is_primary"],
            }
        )

    if len(raw_monitors) > 1:
        min_x = min(m["offset_x"] for m in raw_monitors)
        min_y = min(m["offset_y"] for m in raw_monitors)
        max_x = max(m["offset_x"] + m["width"] for m in raw_monitors)
        max_y = max(m["offset_y"] + m["height"] for m in raw_monitors)
        span_w = max_x - min_x
        span_h = max_y - min_y
        results.append(
            {
                "label": f"All Displays (Spanning) — {span_w} × {span_h} @ ({min_x}, {min_y})",
                "short_name": "All Displays (Spanning)",
                "width": span_w,
                "height": span_h,
                "offset_x": min_x,
                "offset_y": min_y,
                "is_primary": False,
            }
        )

    return results


def _apply_dark_combobox_style(widget: tk.Misc):
    style = ttk.Style(widget)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "Dark.TCombobox",
        fieldbackground="#27272a",
        background="#3f3f46",
        foreground="#f4f4f5",
        arrowcolor="#f4f4f5",
        bordercolor="#3f3f46",
        lightcolor="#3f3f46",
        darkcolor="#27272a",
    )
    style.map(
        "Dark.TCombobox",
        fieldbackground=[("readonly", "#27272a")],
        foreground=[("readonly", "#f4f4f5")],
        selectbackground=[("readonly", "#27272a")],
        selectforeground=[("readonly", "#f4f4f5")],
    )


class TabletSetupDialog:
    """Modal dialog for selecting or customizing Wacom tablet active area dimensions."""

    def __init__(
        self,
        parent: tk.Misc,
        initial_model: str = DEFAULT_TABLET_MODEL,
        initial_w: float = DEFAULT_TABLET_WIDTH_MM,
        initial_h: float = DEFAULT_TABLET_HEIGHT_MM,
        required: bool = True,
    ):
        self.parent = parent
        self.required = required
        self.result: tuple[str, float, float] | None = None
        self._syncing_fields = False

        self.win = tk.Toplevel(parent)
        self.win.title("Wacom Tablet Setup")
        self.win.configure(bg="#18181b")
        self.win.resizable(False, False)
        _apply_window_icon(self.win)

        _apply_dark_combobox_style(self.win)

        self.preset_map = {name: (w, h) for name, w, h in TABLET_PRESETS}
        preset_names = [name for name, _, _ in TABLET_PRESETS]

        matched_preset = "Custom Size..."
        for name, pw, ph in TABLET_PRESETS:
            if (
                pw is not None
                and ph is not None
                and abs(pw - initial_w) < 0.05
                and abs(ph - initial_h) < 0.05
            ):
                if name == initial_model or matched_preset == "Custom Size...":
                    matched_preset = name

        self.preset_var = tk.StringVar(value=matched_preset)
        self.width_var = tk.StringVar(value=f"{initial_w:g}")
        self.height_var = tk.StringVar(value=f"{initial_h:g}")

        self._build_ui(preset_names)

        self.width_var.trace_add("write", self._on_dimension_typed)
        self.height_var.trace_add("write", self._on_dimension_typed)

        self.win.protocol("WM_DELETE_WINDOW", self._on_cancel)
        _position_window_on_parent(self.win, parent)

        self.win.grab_set()
        self.win.focus_force()

    def _build_ui(self, preset_names: list[str]):
        pad = tk.Frame(self.win, bg="#18181b")
        pad.pack(padx=20, pady=18)

        tk.Label(
            pad,
            text="SELECT YOUR WACOM TABLET",
            font=("Segoe UI", 10, "bold"),
            fg="#38bdf8",
            bg="#18181b",
            anchor="w",
        ).pack(fill="x", pady=(0, 4))

        subtitle = (
            "Choose your tablet model (or enter its active area in mm) so\n"
            "pen movement distances match real-world millimeters."
            if self.required
            else "Update your tablet model or active area dimensions (in mm)."
        )
        tk.Label(
            pad,
            text=subtitle,
            font=("Segoe UI", 8),
            fg="#a1a1aa",
            bg="#18181b",
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(0, 12))

        tk.Label(
            pad,
            text="Tablet Model Preset",
            font=("Segoe UI", 9),
            fg="#d4d4d8",
            bg="#18181b",
            anchor="w",
        ).pack(fill="x", pady=(0, 3))

        cb = ttk.Combobox(
            pad,
            textvariable=self.preset_var,
            values=preset_names,
            state="readonly",
            width=36,
            style="Dark.TCombobox",
        )
        cb.pack(fill="x", pady=(0, 12))
        cb.bind("<<ComboboxSelected>>", self._on_preset_selected)

        dims_row = tk.Frame(pad, bg="#18181b")
        dims_row.pack(fill="x", pady=(0, 14))

        w_col = tk.Frame(dims_row, bg="#18181b")
        w_col.pack(side="left", expand=True, fill="x", padx=(0, 8))
        tk.Label(
            w_col,
            text="Active Width (mm)",
            font=("Segoe UI", 9),
            fg="#d4d4d8",
            bg="#18181b",
            anchor="w",
        ).pack(fill="x", pady=(0, 3))
        self.ent_width = tk.Entry(
            w_col,
            textvariable=self.width_var,
            font=("Consolas", 10),
            bg="#27272a",
            fg="#f4f4f5",
            insertbackground="#f4f4f5",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#3f3f46",
            highlightcolor="#38bdf8",
        )
        self.ent_width.pack(fill="x", ipady=4)

        h_col = tk.Frame(dims_row, bg="#18181b")
        h_col.pack(side="left", expand=True, fill="x", padx=(8, 0))
        tk.Label(
            h_col,
            text="Active Height (mm)",
            font=("Segoe UI", 9),
            fg="#d4d4d8",
            bg="#18181b",
            anchor="w",
        ).pack(fill="x", pady=(0, 3))
        self.ent_height = tk.Entry(
            h_col,
            textvariable=self.height_var,
            font=("Consolas", 10),
            bg="#27272a",
            fg="#f4f4f5",
            insertbackground="#f4f4f5",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#3f3f46",
            highlightcolor="#38bdf8",
        )
        self.ent_height.pack(fill="x", ipady=4)

        btn_row = tk.Frame(pad, bg="#18181b")
        btn_row.pack(fill="x", pady=(2, 0))

        if not self.required:
            tk.Button(
                btn_row,
                text="Cancel",
                width=10,
                font=("Segoe UI", 9),
                command=self._on_cancel,
                bg="#27272a",
                fg="#a1a1aa",
                relief="flat",
                cursor="hand2",
            ).pack(side="right", padx=(8, 0))

        tk.Button(
            btn_row,
            text="Continue" if self.required else "Apply",
            width=14,
            font=("Segoe UI", 9, "bold"),
            command=self._on_confirm,
            bg="#2563eb",
            fg="#f4f4f5",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            relief="flat",
            cursor="hand2",
        ).pack(side="right")

        self.win.bind("<Return>", lambda _e: self._on_confirm())

    def _on_preset_selected(self, _event=None):
        chosen = self.preset_var.get()
        pw, ph = self.preset_map.get(chosen, (None, None))
        if pw is not None and ph is not None:
            self._syncing_fields = True
            self.width_var.set(f"{pw:g}")
            self.height_var.set(f"{ph:g}")
            self._syncing_fields = False
        else:
            self.ent_width.focus_set()
            self.ent_width.select_range(0, tk.END)

    def _on_dimension_typed(self, *_args):
        if self._syncing_fields:
            return
        try:
            w_val = float(self.width_var.get().strip())
            h_val = float(self.height_var.get().strip())
        except ValueError:
            self.preset_var.set("Custom Size...")
            return

        for name, pw, ph in TABLET_PRESETS:
            if (
                pw is not None
                and ph is not None
                and abs(pw - w_val) < 0.05
                and abs(ph - h_val) < 0.05
            ):
                self.preset_var.set(name)
                return
        self.preset_var.set("Custom Size...")

    def _on_confirm(self):
        try:
            w_mm = float(self.width_var.get().strip().replace(",", "."))
            h_mm = float(self.height_var.get().strip().replace(",", "."))
        except ValueError:
            messagebox.showerror(
                "Invalid Dimensions",
                "Please enter valid numeric values for Width and Height in millimeters.",
                parent=self.win,
            )
            return

        if not (10.0 <= w_mm <= 3000.0 and 10.0 <= h_mm <= 3000.0):
            messagebox.showerror(
                "Out of Range",
                "Active area dimensions must be between 10 mm and 3000 mm.",
                parent=self.win,
            )
            return

        chosen_preset = self.preset_var.get()
        model_label = (
            "Custom Size" if chosen_preset == "Custom Size..." else chosen_preset
        )
        self.result = (model_label, round(w_mm, 3), round(h_mm, 3))
        self.win.grab_release()
        self.win.destroy()

    def _on_cancel(self):
        self.result = None
        self.win.grab_release()
        self.win.destroy()


class DisplaySetupDialog:
    """Modal dialog with auto-detection for selecting mapped display resolution & offset."""

    CUSTOM_OPTION = "Custom Region..."

    def __init__(
        self,
        parent: tk.Misc,
        initial_name: str | None = None,
        initial_w: int | None = None,
        initial_h: int | None = None,
        initial_ox: int | None = None,
        initial_oy: int | None = None,
        required: bool = True,
    ):
        self.parent = parent
        self.required = required
        self.result: tuple[str, int, int, int, int] | None = None
        self._syncing_fields = False
        self._pen_detect_active = False
        self._pen_detect_after_id: str | None = None
        self._pen_start_pos: tuple[int, int] | None = None
        self._pen_detect_deadline = 0.0

        self.win = tk.Toplevel(parent)
        self.win.title("Display Region Setup")
        self.win.configure(bg="#18181b")
        self.win.resizable(False, False)
        _apply_window_icon(self.win)

        _apply_dark_combobox_style(self.win)

        self.displays = detect_connected_displays()
        self.display_map: dict[str, dict] = {
            d["label"]: d for d in self.displays
        }

        primary = self.displays[0]
        use_w = initial_w if initial_w is not None else primary["width"]
        use_h = initial_h if initial_h is not None else primary["height"]
        use_ox = initial_ox if initial_ox is not None else primary["offset_x"]
        use_oy = initial_oy if initial_oy is not None else primary["offset_y"]

        matched_label = self._match_display_label(
            use_w, use_h, use_ox, use_oy, initial_name
        )

        self.preset_var = tk.StringVar(value=matched_label)
        self.width_var = tk.StringVar(value=str(use_w))
        self.height_var = tk.StringVar(value=str(use_h))
        self.offset_x_var = tk.StringVar(value=str(use_ox))
        self.offset_y_var = tk.StringVar(value=str(use_oy))

        self._build_ui()

        for var in (
            self.width_var,
            self.height_var,
            self.offset_x_var,
            self.offset_y_var,
        ):
            var.trace_add("write", self._on_values_typed)

        self.win.protocol("WM_DELETE_WINDOW", self._on_cancel)
        _position_window_on_parent(self.win, parent)

        self.win.grab_set()
        self.win.focus_force()

    def _match_display_label(
        self,
        w: int,
        h: int,
        ox: int,
        oy: int,
        preferred_short_name: str | None = None,
    ) -> str:
        fallback = self.CUSTOM_OPTION
        for d in self.displays:
            if (
                d["width"] == w
                and d["height"] == h
                and d["offset_x"] == ox
                and d["offset_y"] == oy
            ):
                if preferred_short_name and d["short_name"] == preferred_short_name:
                    return d["label"]
                if fallback == self.CUSTOM_OPTION:
                    fallback = d["label"]
        return fallback

    def _build_ui(self):
        pad = tk.Frame(self.win, bg="#18181b")
        pad.pack(padx=20, pady=18)

        tk.Label(
            pad,
            text="SELECT MAPPED DISPLAY REGION",
            font=("Segoe UI", 10, "bold"),
            fg="#38bdf8",
            bg="#18181b",
            anchor="w",
        ).pack(fill="x", pady=(0, 4))

        num_monitors = len(
            [d for d in self.displays if "All Displays" not in d["short_name"]]
        )
        subtitle = (
            f"Auto-detected {num_monitors} connected display(s). Pick the monitor mapped to your\n"
            "Wacom tablet, or enter a custom resolution and pixel offset."
        )
        tk.Label(
            pad,
            text=subtitle,
            font=("Segoe UI", 8),
            fg="#a1a1aa",
            bg="#18181b",
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(0, 12))

        tk.Label(
            pad,
            text="Detected Display / Mapping Preset",
            font=("Segoe UI", 9),
            fg="#d4d4d8",
            bg="#18181b",
            anchor="w",
        ).pack(fill="x", pady=(0, 3))

        options = [d["label"] for d in self.displays] + [self.CUSTOM_OPTION]
        self.cb_display = ttk.Combobox(
            pad,
            textvariable=self.preset_var,
            values=options,
            state="readonly",
            width=44,
            style="Dark.TCombobox",
        )
        self.cb_display.pack(fill="x", pady=(0, 8))
        self.cb_display.bind("<<ComboboxSelected>>", self._on_display_selected)

        detect_row = tk.Frame(pad, bg="#18181b")
        detect_row.pack(fill="x", pady=(0, 12))

        self.btn_detect_pen = tk.Button(
            detect_row,
            text="Detect From Pen",
            font=("Segoe UI", 8, "bold"),
            padx=10,
            pady=3,
            command=self._start_pen_detect,
            bg="#27272a",
            fg="#38bdf8",
            activebackground="#3f3f46",
            activeforeground="#7dd3fc",
            relief="flat",
            cursor="hand2",
        )
        self.btn_detect_pen.pack(side="left")

        tk.Button(
            detect_row,
            text="Refresh Monitors",
            font=("Segoe UI", 8),
            padx=10,
            pady=3,
            command=self._refresh_monitors,
            bg="#27272a",
            fg="#a1a1aa",
            activebackground="#3f3f46",
            activeforeground="#f4f4f5",
            relief="flat",
            cursor="hand2",
        ).pack(side="left", padx=(6, 0))

        res_row = tk.Frame(pad, bg="#18181b")
        res_row.pack(fill="x", pady=(0, 10))

        w_col = tk.Frame(res_row, bg="#18181b")
        w_col.pack(side="left", expand=True, fill="x", padx=(0, 8))
        tk.Label(
            w_col,
            text="Region Width (px)",
            font=("Segoe UI", 9),
            fg="#d4d4d8",
            bg="#18181b",
            anchor="w",
        ).pack(fill="x", pady=(0, 3))
        self.ent_width = self._make_entry(w_col, self.width_var)

        h_col = tk.Frame(res_row, bg="#18181b")
        h_col.pack(side="left", expand=True, fill="x", padx=(8, 0))
        tk.Label(
            h_col,
            text="Region Height (px)",
            font=("Segoe UI", 9),
            fg="#d4d4d8",
            bg="#18181b",
            anchor="w",
        ).pack(fill="x", pady=(0, 3))
        self.ent_height = self._make_entry(h_col, self.height_var)

        off_row = tk.Frame(pad, bg="#18181b")
        off_row.pack(fill="x", pady=(0, 14))

        ox_col = tk.Frame(off_row, bg="#18181b")
        ox_col.pack(side="left", expand=True, fill="x", padx=(0, 8))
        tk.Label(
            ox_col,
            text="Offset X (px)",
            font=("Segoe UI", 9),
            fg="#d4d4d8",
            bg="#18181b",
            anchor="w",
        ).pack(fill="x", pady=(0, 3))
        self.ent_offset_x = self._make_entry(ox_col, self.offset_x_var)

        oy_col = tk.Frame(off_row, bg="#18181b")
        oy_col.pack(side="left", expand=True, fill="x", padx=(8, 0))
        tk.Label(
            oy_col,
            text="Offset Y (px)",
            font=("Segoe UI", 9),
            fg="#d4d4d8",
            bg="#18181b",
            anchor="w",
        ).pack(fill="x", pady=(0, 3))
        self.ent_offset_y = self._make_entry(oy_col, self.offset_y_var)

        btn_row = tk.Frame(pad, bg="#18181b")
        btn_row.pack(fill="x", pady=(2, 0))

        if not self.required:
            tk.Button(
                btn_row,
                text="Cancel",
                width=10,
                font=("Segoe UI", 9),
                command=self._on_cancel,
                bg="#27272a",
                fg="#a1a1aa",
                relief="flat",
                cursor="hand2",
            ).pack(side="right", padx=(8, 0))

        tk.Button(
            btn_row,
            text="Save & Start" if self.required else "Apply",
            width=14,
            font=("Segoe UI", 9, "bold"),
            command=self._on_confirm,
            bg="#2563eb",
            fg="#f4f4f5",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            relief="flat",
            cursor="hand2",
        ).pack(side="right")

        self.win.bind("<Return>", lambda _e: self._on_confirm())

    def _make_entry(self, parent: tk.Frame, var: tk.StringVar) -> tk.Entry:
        ent = tk.Entry(
            parent,
            textvariable=var,
            font=("Consolas", 10),
            bg="#27272a",
            fg="#f4f4f5",
            insertbackground="#f4f4f5",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#3f3f46",
            highlightcolor="#38bdf8",
        )
        ent.pack(fill="x", ipady=4)
        return ent

    def _apply_display_dict(self, d: dict):
        self._syncing_fields = True
        self.preset_var.set(d["label"])
        self.width_var.set(str(d["width"]))
        self.height_var.set(str(d["height"]))
        self.offset_x_var.set(str(d["offset_x"]))
        self.offset_y_var.set(str(d["offset_y"]))
        self._syncing_fields = False

    def _on_display_selected(self, _event=None):
        chosen = self.preset_var.get()
        d = self.display_map.get(chosen)
        if d is not None:
            self._apply_display_dict(d)
        else:
            self.ent_width.focus_set()
            self.ent_width.select_range(0, tk.END)

    def _on_values_typed(self, *_args):
        if self._syncing_fields:
            return
        try:
            w = int(self.width_var.get().strip())
            h = int(self.height_var.get().strip())
            ox = int(self.offset_x_var.get().strip())
            oy = int(self.offset_y_var.get().strip())
        except ValueError:
            self.preset_var.set(self.CUSTOM_OPTION)
            return

        self.preset_var.set(self._match_display_label(w, h, ox, oy))

    def _refresh_monitors(self):
        self.displays = detect_connected_displays()
        self.display_map = {d["label"]: d for d in self.displays}
        options = [d["label"] for d in self.displays] + [self.CUSTOM_OPTION]
        self.cb_display.config(values=options)
        self._on_values_typed()

    def _start_pen_detect(self):
        if self._pen_detect_active:
            self._stop_pen_detect()
            return
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        self._pen_start_pos = (pt.x, pt.y)
        self._pen_detect_active = True
        self._pen_detect_deadline = time.monotonic() + 6.0
        self.btn_detect_pen.config(
            text="Move or tap pen on screen...", fg="#4ade80"
        )
        self._pen_detect_after_id = self.win.after(250, self._poll_pen_detect)

    def _stop_pen_detect(self):
        self._pen_detect_active = False
        if self._pen_detect_after_id is not None:
            try:
                self.win.after_cancel(self._pen_detect_after_id)
            except Exception:
                pass
            self._pen_detect_after_id = None
        if self.win.winfo_exists():
            self.btn_detect_pen.config(text="Detect From Pen", fg="#38bdf8")

    def _poll_pen_detect(self):
        if not self._pen_detect_active or not self.win.winfo_exists():
            return
        if time.monotonic() > self._pen_detect_deadline:
            self._stop_pen_detect()
            return

        pt = wintypes.POINT()
        if user32.GetCursorPos(ctypes.byref(pt)):
            sx, sy = self._pen_start_pos or (pt.x, pt.y)
            moved = math.hypot(pt.x - sx, pt.y - sy) > 20
            clicked = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
            if moved or clicked:
                for d in self.displays:
                    if "All Displays" in d["short_name"]:
                        continue
                    x0, y0 = d["offset_x"], d["offset_y"]
                    x1, y1 = x0 + d["width"], y0 + d["height"]
                    if x0 <= pt.x < x1 and y0 <= pt.y < y1:
                        self._apply_display_dict(d)
                        self._stop_pen_detect()
                        return

        self._pen_detect_after_id = self.win.after(50, self._poll_pen_detect)

    def _on_confirm(self):
        self._stop_pen_detect()
        try:
            w_px = int(self.width_var.get().strip())
            h_px = int(self.height_var.get().strip())
            ox_px = int(self.offset_x_var.get().strip())
            oy_px = int(self.offset_y_var.get().strip())
        except ValueError:
            messagebox.showerror(
                "Invalid Display Settings",
                "Please enter whole numbers for Resolution (Width/Height) and Offset (X/Y).",
                parent=self.win,
            )
            return

        if not (100 <= w_px <= 32768 and 100 <= h_px <= 32768):
            messagebox.showerror(
                "Out of Range",
                "Display width and height must be between 100 and 32768 pixels.",
                parent=self.win,
            )
            return

        if not (-32768 <= ox_px <= 32768 and -32768 <= oy_px <= 32768):
            messagebox.showerror(
                "Out of Range",
                "Display offsets must be between -32768 and 32768 pixels.",
                parent=self.win,
            )
            return

        chosen = self.preset_var.get()
        matched_dict = self.display_map.get(chosen)
        short_name = (
            matched_dict["short_name"]
            if matched_dict is not None
            else "Custom Region"
        )
        self.result = (short_name, w_px, h_px, ox_px, oy_px)
        self.win.grab_release()
        self.win.destroy()

    def _on_cancel(self):
        self._stop_pen_detect()
        self.result = None
        self.win.grab_release()
        self.win.destroy()


class SettingsWindow:
    """Modal settings menu consolidating tray toggle, tablet/display setup, and stats reset."""

    def __init__(self, app: "WacomTrackerApp"):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("Wacom Tracker Settings")
        self.win.configure(bg="#18181b")
        self.win.resizable(False, False)
        _apply_window_icon(self.win)

        self._build_ui()
        self.sync_state()

        self.win.protocol("WM_DELETE_WINDOW", self.close)
        _position_window_on_parent(self.win, self.app.root)

        self.win.grab_set()
        self.win.lift()
        self.win.focus_force()

    def _build_ui(self):
        pad = tk.Frame(self.win, bg="#18181b")
        pad.pack(padx=20, pady=18)

        tk.Label(
            pad,
            text="SETTINGS",
            font=("Segoe UI", 10, "bold"),
            fg="#38bdf8",
            bg="#18181b",
            anchor="w",
        ).pack(fill="x", pady=(0, 12))

        # --- Section 1: Hardware & Display ---
        tk.Label(
            pad,
            text="HARDWARE & DISPLAY",
            font=("Segoe UI", 8, "bold"),
            fg="#71717a",
            bg="#18181b",
            anchor="w",
        ).pack(fill="x", pady=(0, 4))

        hw_card = tk.Frame(
            pad,
            bg="#1f1f23",
            highlightbackground="#3f3f46",
            highlightthickness=1,
        )
        hw_card.pack(fill="x", pady=(0, 14))

        # Tablet row
        tablet_row = tk.Frame(hw_card, bg="#1f1f23")
        tablet_row.pack(fill="x", padx=12, pady=(10, 8))

        tablet_info = tk.Frame(tablet_row, bg="#1f1f23")
        tablet_info.pack(side="left", fill="x", expand=True, padx=(0, 12))

        tk.Label(
            tablet_info,
            text="Wacom Tablet Active Area",
            font=("Segoe UI", 9, "bold"),
            fg="#f4f4f5",
            bg="#1f1f23",
            anchor="w",
        ).pack(fill="x")

        self.lbl_tablet_val = tk.Label(
            tablet_info,
            text="",
            font=("Segoe UI", 8),
            fg="#a1a1aa",
            bg="#1f1f23",
            anchor="w",
        )
        self.lbl_tablet_val.pack(fill="x")

        tk.Button(
            tablet_row,
            text="Configure...",
            width=11,
            font=("Segoe UI", 8, "bold"),
            command=self._configure_tablet,
            bg="#27272a",
            fg="#fbbf24",
            activebackground="#3f3f46",
            activeforeground="#fde68a",
            relief="flat",
            cursor="hand2",
        ).pack(side="right")

        tk.Frame(hw_card, bg="#2e2e34", height=1).pack(fill="x", padx=12)

        # Display row
        display_row = tk.Frame(hw_card, bg="#1f1f23")
        display_row.pack(fill="x", padx=12, pady=(8, 10))

        display_info = tk.Frame(display_row, bg="#1f1f23")
        display_info.pack(side="left", fill="x", expand=True, padx=(0, 12))

        tk.Label(
            display_info,
            text="Mapped Display Region",
            font=("Segoe UI", 9, "bold"),
            fg="#f4f4f5",
            bg="#1f1f23",
            anchor="w",
        ).pack(fill="x")

        self.lbl_display_val = tk.Label(
            display_info,
            text="",
            font=("Segoe UI", 8),
            fg="#a1a1aa",
            bg="#1f1f23",
            anchor="w",
        )
        self.lbl_display_val.pack(fill="x")

        tk.Button(
            display_row,
            text="Configure...",
            width=11,
            font=("Segoe UI", 8, "bold"),
            command=self._configure_display,
            bg="#27272a",
            fg="#c084fc",
            activebackground="#3f3f46",
            activeforeground="#e9d5ff",
            relief="flat",
            cursor="hand2",
        ).pack(side="right")

        # --- Section 2: Window & System Tray ---
        tk.Label(
            pad,
            text="WINDOW & TRAY BEHAVIOR",
            font=("Segoe UI", 8, "bold"),
            fg="#71717a",
            bg="#18181b",
            anchor="w",
        ).pack(fill="x", pady=(0, 4))

        tray_card = tk.Frame(
            pad,
            bg="#1f1f23",
            highlightbackground="#3f3f46",
            highlightthickness=1,
        )
        tray_card.pack(fill="x", pady=(0, 14))

        tray_row = tk.Frame(tray_card, bg="#1f1f23")
        tray_row.pack(fill="x", padx=12, pady=10)

        tray_info = tk.Frame(tray_row, bg="#1f1f23")
        tray_info.pack(side="left", fill="x", expand=True, padx=(0, 12))

        tk.Label(
            tray_info,
            text="Minimize to system tray on close",
            font=("Segoe UI", 9, "bold"),
            fg="#f4f4f5",
            bg="#1f1f23",
            anchor="w",
        ).pack(fill="x")

        tk.Label(
            tray_info,
            text="Keep tracking in background when clicking window X",
            font=("Segoe UI", 8),
            fg="#a1a1aa",
            bg="#1f1f23",
            anchor="w",
        ).pack(fill="x")

        self.btn_tray_toggle = tk.Button(
            tray_row,
            text="ON",
            width=8,
            font=("Segoe UI", 8, "bold"),
            command=self._toggle_tray_setting,
            relief="flat",
            cursor="hand2",
        )
        self.btn_tray_toggle.pack(side="right")

        # --- Section 3: Data & Statistics ---
        tk.Label(
            pad,
            text="DATA & STATISTICS",
            font=("Segoe UI", 8, "bold"),
            fg="#71717a",
            bg="#18181b",
            anchor="w",
        ).pack(fill="x", pady=(0, 4))

        data_card = tk.Frame(
            pad,
            bg="#1f1f23",
            highlightbackground="#3f3f46",
            highlightthickness=1,
        )
        data_card.pack(fill="x", pady=(0, 16))

        reset_row = tk.Frame(data_card, bg="#1f1f23")
        reset_row.pack(fill="x", padx=12, pady=10)

        reset_info = tk.Frame(reset_row, bg="#1f1f23")
        reset_info.pack(side="left", fill="x", expand=True, padx=(0, 12))

        tk.Label(
            reset_info,
            text="Reset All Statistics",
            font=("Segoe UI", 9, "bold"),
            fg="#f4f4f5",
            bg="#1f1f23",
            anchor="w",
        ).pack(fill="x")

        tk.Label(
            reset_info,
            text="Permanently clear lifetime distances and daily history",
            font=("Segoe UI", 8),
            fg="#a1a1aa",
            bg="#1f1f23",
            anchor="w",
        ).pack(fill="x")

        tk.Button(
            reset_row,
            text="Reset Stats...",
            width=11,
            font=("Segoe UI", 8, "bold"),
            command=self._reset_stats_with_confirmation,
            bg="#3f1d1d",
            fg="#f87171",
            activebackground="#5c2626",
            activeforeground="#fecaca",
            relief="flat",
            cursor="hand2",
        ).pack(side="right")

        # Bottom Close Button
        btn_row = tk.Frame(pad, bg="#18181b")
        btn_row.pack(fill="x")

        tk.Button(
            btn_row,
            text="Close",
            width=12,
            font=("Segoe UI", 9, "bold"),
            command=self.close,
            bg="#27272a",
            fg="#f4f4f5",
            activebackground="#3f3f46",
            activeforeground="#ffffff",
            relief="flat",
            cursor="hand2",
        ).pack(side="right")

        self.win.bind("<Escape>", lambda _e: self.close())

    def sync_state(self):
        if not self.win.winfo_exists():
            return
        with self.app.lock:
            t_model = self.app.tablet_model
            t_w = self.app.tablet_width_mm
            t_h = self.app.tablet_height_mm
            d_name = self.app.display_name
            d_w = self.app.display_width_px
            d_h = self.app.display_height_px
            d_ox = self.app.display_offset_x
            d_oy = self.app.display_offset_y
            min_tray = self.app.minimize_to_tray

        self.lbl_tablet_val.config(
            text=f"{t_model} ({t_w:g} × {t_h:g} mm)"
        )
        self.lbl_display_val.config(
            text=f"{d_name} ({d_w}×{d_h} @ {d_ox}, {d_oy})"
        )
        if min_tray:
            self.btn_tray_toggle.config(
                text="ON",
                bg="#14532d",
                fg="#4ade80",
                activebackground="#166534",
                activeforeground="#86efac",
            )
        else:
            self.btn_tray_toggle.config(
                text="OFF",
                bg="#27272a",
                fg="#a1a1aa",
                activebackground="#3f3f46",
                activeforeground="#f4f4f5",
            )

    def _configure_tablet(self):
        self.app._open_tablet_settings(parent=self.win)
        if self.win.winfo_exists():
            self.win.grab_set()
            self.win.focus_force()
            self.sync_state()

    def _configure_display(self):
        self.app._open_display_settings(parent=self.win)
        if self.win.winfo_exists():
            self.win.grab_set()
            self.win.focus_force()
            self.sync_state()

    def _toggle_tray_setting(self):
        self.app._toggle_minimize_to_tray()
        self.sync_state()

    def _reset_stats_with_confirmation(self):
        confirmed = messagebox.askyesno(
            "Confirm Statistics Reset",
            "Are you sure you want to reset all statistics?\n\n"
            "This will permanently erase all lifetime distances (Drawn, Hover, Total), "
            "current session counters, and daily graph/histogram history.\n\n"
            "This action cannot be undone.",
            icon="warning",
            parent=self.win,
        )
        if not confirmed:
            if self.win.winfo_exists():
                self.win.grab_set()
                self.win.focus_force()
            return

        self.app._execute_reset_counters()
        if self.win.winfo_exists():
            self.win.grab_set()
            self.win.focus_force()
            messagebox.showinfo(
                "Statistics Reset",
                "All distance statistics and history have been reset to 0.",
                parent=self.win,
            )
            if self.win.winfo_exists():
                self.win.grab_set()
                self.win.focus_force()

    def close(self):
        self.app.settings_window = None
        if self.win.winfo_exists():
            self.win.grab_release()
            self.win.destroy()


class WacomDigitizerSpec:
    """Stores HID descriptor ranges for direct hardware X/Y coordinate decoding."""

    def __init__(
        self,
        preparsed_buf,
        x_min: int,
        x_max: int,
        y_min: int,
        y_max: int,
        max_buttons: int,
    ):
        self.preparsed_buf = preparsed_buf
        self.x_min = x_min
        self.x_max = x_max
        self.y_min = y_min
        self.y_max = y_max
        self.max_buttons = max(max_buttons, 16)


def _nice_axis_ticks(max_val: float, target_ticks: int = 7) -> tuple[float, list[float]]:
    """Computes clean rounded Y-axis grid steps for any positive value."""
    if max_val <= 0:
        max_val = 1.0
    raw_step = max_val / target_ticks
    magnitude = 10 ** math.floor(math.log10(raw_step))
    residual = raw_step / magnitude
    if residual <= 1.0:
        nice_step = 1.0 * magnitude
    elif residual <= 2.0:
        nice_step = 2.0 * magnitude
    elif residual <= 2.5:
        nice_step = 2.5 * magnitude
    elif residual <= 5.0:
        nice_step = 5.0 * magnitude
    else:
        nice_step = 10.0 * magnitude

    num_steps = max(1, math.ceil(max_val / nice_step))
    top_val = num_steps * nice_step
    ticks = [i * nice_step for i in range(num_steps + 1)]
    return top_val, ticks


class WacomStatsWindow:
    """Gameplay-Time-Tracker-style distance statistics & histogram window."""

    PARAM_OPTIONS = (
        ("Drawn", 68),
        ("Hover", 68),
        ("Drawn + Hover", 112),
    )
    PARAM_ACCENTS = {
        "Drawn": "#38bdf8",
        "Hover": "#a78bfa",
        "Drawn + Hover": "#f4f4f5",
    }
    RANGE_OPTIONS = (
        ("Week", 72),
        ("Month", 76),
        ("Year", 72),
    )
    HIST_GROUP_OPTIONS = (
        ("Dates", 68),
        ("Weeks", 70),
        ("Months", 76),
        ("Years", 66),
    )
    HIST_COL_LABELS = {
        "Dates": "Date",
        "Weeks": "Week",
        "Months": "Month",
        "Years": "Year",
    }
    CURRENT_PERIOD_LABELS = {
        "Week": "This week",
        "Month": "This month",
        "Year": "This year",
    }

    def __init__(self, app: "WacomTrackerApp"):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("Wacom Tracker Statistics")
        self.win.geometry("840x530")
        self.win.minsize(760, 420)
        self.win.configure(bg="#18181b")
        _apply_window_icon(self.win)

        self.active_tab = "Graph"  # "Graph" or "Histogram"
        self.param_var = tk.StringVar(value="Drawn")
        self.range_var = tk.StringVar(value="Month")
        self.hist_group_var = tk.StringVar(value="Dates")
        self.anchor_date = datetime.date.today()

        self.param_buttons: dict[str, tk.Button] = {}
        self.range_buttons: dict[str, tk.Button] = {}
        self.hist_group_buttons: dict[str, tk.Button] = {}

        # Graph hover hitboxes
        self.bar_hitboxes: list[tuple[float, float, float, float, str, float]] = []
        self.hovered_index: int | None = None
        self.tooltip_pos: tuple[int, int] | None = None

        # Histogram table state (Gameplay Time Tracker style: Period | Value)
        self.hist_sort_col = "date"  # "date" or "value"
        self.hist_sort_desc = True
        self.hist_selected_key: str | None = None
        self.hist_row_height = 29
        self.hist_date_col_w = 270
        self.hist_rows_cache: list[tuple[str, str, float]] = []

        self._build_ui()
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.redraw()

        _position_window_on_parent(self.win, self.app.root, width=840, height=530)

        self.win.lift()
        self.win.focus_force()

    def _make_fixed_toggle(
        self,
        parent: tk.Misc,
        text: str,
        width_px: int,
        height_px: int,
        command,
    ) -> tk.Button:
        """Creates a toggle button with locked pixel width & height."""
        holder = tk.Frame(parent, width=width_px, height=height_px, bg="#18181b")
        holder.pack_propagate(False)
        holder.pack(side="left", padx=(0, 4))
        btn = tk.Button(
            holder,
            text=text,
            font=("Segoe UI", 8, "bold"),
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            command=command,
        )
        btn.pack(fill="both", expand=True)
        return btn

    def _build_ui(self):
        # 1. Top Header Bar
        header = tk.Frame(self.win, bg="#18181b")
        header.pack(side="top", fill="x", padx=16, pady=(12, 6))

        tk.Label(
            header,
            text="Distance statistics",
            font=("Segoe UI", 14, "bold"),
            fg="#38bdf8",
            bg="#18181b",
        ).pack(side="left")

        self.lbl_period_sum = tk.Label(
            header,
            text="",
            font=("Segoe UI", 9, "bold"),
            fg="#a1a1aa",
            bg="#18181b",
        )
        self.lbl_period_sum.pack(side="left", padx=18)

        tk.Button(
            header,
            text="Back to tracker",
            font=("Segoe UI", 9, "underline"),
            fg="#60a5fa",
            bg="#18181b",
            activebackground="#18181b",
            activeforeground="#93c5fd",
            relief="flat",
            cursor="hand2",
            command=self.close,
        ).pack(side="right")

        # 2. Tabs Row ("Graph" | "Histogram")
        tab_bar = tk.Frame(self.win, bg="#18181b")
        tab_bar.pack(side="top", fill="x", padx=16, pady=(2, 0))

        self.tab_buttons: dict[str, tk.Button] = {}
        for tab_name in ("Graph", "Histogram"):
            btn = tk.Button(
                tab_bar,
                text=tab_name,
                font=("Segoe UI", 9, "bold"),
                padx=12,
                pady=4,
                relief="flat",
                cursor="hand2",
                command=lambda t=tab_name: self._set_tab(t),
            )
            btn.pack(side="left", padx=(0, 2))
            self.tab_buttons[tab_name] = btn

        self._style_tabs()

        # 3. Bottom Controls Row (packed side="bottom" first so it NEVER shifts or clips)
        btn_h = 26
        self.controls_bar = tk.Frame(self.win, bg="#18181b", height=btn_h)
        self.controls_bar.pack_propagate(False)
        self.controls_bar.pack(side="bottom", fill="x", padx=16, pady=(0, 14))

        tk.Label(
            self.controls_bar,
            text="Displayed parameter:",
            font=("Segoe UI", 9),
            fg="#a1a1aa",
            bg="#18181b",
        ).pack(side="left", padx=(0, 8))

        param_btn_wrap = tk.Frame(self.controls_bar, bg="#18181b", height=btn_h)
        param_btn_wrap.pack(side="left", padx=(0, 20))

        for mode, w_px in self.PARAM_OPTIONS:
            self.param_buttons[mode] = self._make_fixed_toggle(
                param_btn_wrap,
                mode,
                w_px,
                btn_h,
                lambda m=mode: self._set_param(m),
            )

        # Fixed-width slot (365x26) shared by Range (Graph) and Group by (Histogram)
        self.mode_controls_slot = tk.Frame(
            self.controls_bar, bg="#18181b", width=365, height=btn_h
        )
        self.mode_controls_slot.pack_propagate(False)
        self.mode_controls_slot.pack(side="left")

        # Graph-only range segmented toggle (Week / Month / Year)
        self.range_controls_frame = tk.Frame(
            self.mode_controls_slot, bg="#18181b", height=btn_h
        )

        lbl_range = tk.Label(
            self.range_controls_frame,
            text="Range:",
            font=("Segoe UI", 9),
            fg="#a1a1aa",
            bg="#18181b",
            width=8,
            anchor="e",
        )
        lbl_range.pack(side="left", padx=(0, 8))

        for rng, w_px in self.RANGE_OPTIONS:
            self.range_buttons[rng] = self._make_fixed_toggle(
                self.range_controls_frame,
                rng,
                w_px,
                btn_h,
                lambda r=rng: self._set_range(r),
            )

        # Histogram-only grouping segmented toggle (Dates / Weeks / Months / Years)
        self.hist_group_frame = tk.Frame(
            self.mode_controls_slot, bg="#18181b", height=btn_h
        )

        lbl_group = tk.Label(
            self.hist_group_frame,
            text="Group by:",
            font=("Segoe UI", 9),
            fg="#a1a1aa",
            bg="#18181b",
            width=8,
            anchor="e",
        )
        lbl_group.pack(side="left", padx=(0, 8))

        for grp, w_px in self.HIST_GROUP_OPTIONS:
            self.hist_group_buttons[grp] = self._make_fixed_toggle(
                self.hist_group_frame,
                grp,
                w_px,
                btn_h,
                lambda g=grp: self._set_hist_group(g),
            )

        # 4. Reserved Navigation Slot (packed side="bottom" right above controls_bar)
        self.nav_slot = tk.Frame(self.win, bg="#18181b", height=32)
        self.nav_slot.pack_propagate(False)
        self.nav_slot.pack(side="bottom", fill="x", padx=16, pady=(0, 8))

        self.nav_bar = tk.Frame(self.nav_slot, bg="#18181b")

        tk.Button(
            self.nav_bar,
            text="◀",
            font=("Segoe UI", 10, "bold"),
            width=4,
            bg="#27272a",
            fg="#38bdf8",
            activebackground="#3f3f46",
            activeforeground="#7dd3fc",
            relief="flat",
            cursor="hand2",
            command=lambda: self._shift_period(-1),
        ).pack(side="left", fill="y")

        self.lbl_date_range = tk.Label(
            self.nav_bar,
            text="",
            font=("Segoe UI", 10),
            fg="#f4f4f5",
            bg="#18181b",
        )
        self.lbl_date_range.pack(side="left", expand=True)

        self.btn_current_period = tk.Button(
            self.nav_bar,
            text="This month",
            font=("Segoe UI", 8, "bold"),
            padx=10,
            bg="#27272a",
            fg="#a1a1aa",
            activebackground="#3f3f46",
            activeforeground="#f4f4f5",
            relief="flat",
            cursor="hand2",
            command=self._jump_today,
        )
        self.btn_current_period.pack(side="right", padx=(6, 0), fill="y")

        tk.Button(
            self.nav_bar,
            text="▶",
            font=("Segoe UI", 10, "bold"),
            width=4,
            bg="#27272a",
            fg="#38bdf8",
            activebackground="#3f3f46",
            activeforeground="#7dd3fc",
            relief="flat",
            cursor="hand2",
            command=lambda: self._shift_period(1),
        ).pack(side="right", fill="y")

        # 5. Main Content Container (fills remaining space between tabs and nav_slot)
        self.content_wrap = tk.Frame(
            self.win, bg="#27272a", highlightbackground="#3f3f46", highlightthickness=1
        )
        self.content_wrap.pack(side="top", fill="both", expand=True, padx=16, pady=(0, 8))

        # --- View 1: Time Graph Canvas ---
        self.graph_frame = tk.Frame(self.content_wrap, bg="#1f1f23")
        self.canvas = tk.Canvas(
            self.graph_frame, bg="#1f1f23", highlightthickness=0, cursor="crosshair"
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.canvas.bind("<Motion>", self._on_mouse_move)
        self.canvas.bind("<Leave>", self._on_mouse_leave)

        # --- View 2: Histogram Table (Period | Value with horizontal bars) ---
        self.hist_frame = tk.Frame(self.content_wrap, bg="#1f1f23")

        hist_header_row = tk.Frame(self.hist_frame, bg="#27272a", height=28)
        hist_header_row.pack(fill="x")
        hist_header_row.pack_propagate(False)

        self.btn_sort_date = tk.Button(
            hist_header_row,
            text="Date  ▼",
            font=("Segoe UI", 9, "bold"),
            fg="#38bdf8",
            bg="#263240",
            activebackground="#2e3d4f",
            activeforeground="#7dd3fc",
            relief="flat",
            anchor="w",
            padx=12,
            cursor="hand2",
            command=lambda: self._toggle_hist_sort("date"),
        )
        self.btn_sort_date.pack(side="left", fill="y")
        self.btn_sort_date.config(width=36)

        tk.Frame(hist_header_row, bg="#3f3f46", width=1).pack(side="left", fill="y")

        self.btn_sort_val = tk.Button(
            hist_header_row,
            text="Value",
            font=("Segoe UI", 9, "bold"),
            fg="#d4d4d8",
            bg="#27272a",
            activebackground="#323238",
            activeforeground="#f4f4f5",
            relief="flat",
            anchor="w",
            padx=12,
            cursor="hand2",
            command=lambda: self._toggle_hist_sort("value"),
        )
        self.btn_sort_val.pack(side="left", fill="both", expand=True)

        tk.Frame(self.hist_frame, bg="#3f3f46", height=1).pack(fill="x")

        hist_body = tk.Frame(self.hist_frame, bg="#1f1f23")
        hist_body.pack(fill="both", expand=True)

        self.hist_canvas = tk.Canvas(
            hist_body, bg="#1f1f23", highlightthickness=0
        )
        self.hist_scrollbar = ttk.Scrollbar(
            hist_body, orient="vertical", command=self.hist_canvas.yview
        )
        self.hist_canvas.configure(yscrollcommand=self.hist_scrollbar.set)

        self.hist_scrollbar.pack(side="right", fill="y")
        self.hist_canvas.pack(side="left", fill="both", expand=True)

        self.hist_canvas.bind("<Configure>", lambda _e: self.redraw())
        self.hist_canvas.bind("<Button-1>", self._on_hist_click)
        self.hist_canvas.bind("<MouseWheel>", self._on_hist_mousewheel)

        self._style_param_buttons()
        self._style_range_buttons()
        self._style_hist_group_buttons()
        self._sync_active_view()

    def _sync_active_view(self):
        if self.active_tab == "Graph":
            self.hist_frame.pack_forget()
            self.hist_group_frame.pack_forget()
            self.graph_frame.pack(fill="both", expand=True)
            self.nav_bar.pack(fill="both", expand=True)
            self.range_controls_frame.pack(fill="both", expand=True)
        else:
            self.graph_frame.pack_forget()
            self.nav_bar.pack_forget()
            self.range_controls_frame.pack_forget()
            self.hist_frame.pack(fill="both", expand=True)
            self.hist_group_frame.pack(fill="both", expand=True)

    def _style_tabs(self):
        for name, btn in self.tab_buttons.items():
            if name == self.active_tab:
                btn.config(bg="#27272a", fg="#f4f4f5")
            else:
                btn.config(bg="#18181b", fg="#71717a")

    def _style_param_buttons(self):
        current = self.param_var.get()
        for mode, btn in self.param_buttons.items():
            if mode == current:
                btn.config(bg="#3f3f46", fg=self.PARAM_ACCENTS[mode])
            else:
                btn.config(bg="#27272a", fg="#a1a1aa")

    def _style_range_buttons(self):
        current = self.range_var.get()
        for rng, btn in self.range_buttons.items():
            if rng == current:
                btn.config(bg="#3f3f46", fg="#38bdf8")
            else:
                btn.config(bg="#27272a", fg="#a1a1aa")

    def _style_hist_group_buttons(self):
        current = self.hist_group_var.get()
        for grp, btn in self.hist_group_buttons.items():
            if grp == current:
                btn.config(bg="#3f3f46", fg="#38bdf8")
            else:
                btn.config(bg="#27272a", fg="#a1a1aa")

    def _update_hist_header_buttons(self):
        col_title = self.HIST_COL_LABELS.get(self.hist_group_var.get(), "Date")
        arrow = "▼" if self.hist_sort_desc else "▲"
        if self.hist_sort_col == "date":
            self.btn_sort_date.config(
                text=f"{col_title}  {arrow}", bg="#263240", fg="#38bdf8"
            )
            self.btn_sort_val.config(
                text="Value", bg="#27272a", fg="#d4d4d8"
            )
        else:
            self.btn_sort_date.config(
                text=col_title, bg="#27272a", fg="#d4d4d8"
            )
            self.btn_sort_val.config(
                text=f"Value  {arrow}", bg="#263240", fg="#38bdf8"
            )

    def _set_hist_group(self, group_name: str):
        if group_name not in self.HIST_COL_LABELS:
            return
        self.hist_group_var.set(group_name)
        self.hist_selected_key = None
        self._style_hist_group_buttons()
        self._update_hist_header_buttons()
        self.hist_canvas.yview_moveto(0.0)
        self.redraw()

    def _set_tab(self, tab_name: str):
        if self.active_tab == tab_name:
            return
        self.active_tab = tab_name
        self.hovered_index = None
        self._style_tabs()
        self._sync_active_view()
        self.redraw()

    def _set_param(self, mode: str):
        if mode not in self.PARAM_ACCENTS:
            return
        self.param_var.set(mode)
        self.hovered_index = None
        self._style_param_buttons()
        self.redraw()

    def _set_range(self, rng: str):
        if rng not in self.CURRENT_PERIOD_LABELS:
            return
        self.range_var.set(rng)
        self._style_range_buttons()
        self.btn_current_period.config(
            text=self.CURRENT_PERIOD_LABELS.get(rng, "This month")
        )
        self.hovered_index = None
        self.redraw()

    def _toggle_hist_sort(self, col: str):
        if self.hist_sort_col == col:
            self.hist_sort_desc = not self.hist_sort_desc
        else:
            self.hist_sort_col = col
            self.hist_sort_desc = True

        self._update_hist_header_buttons()
        self.redraw()

    def _on_hist_click(self, event):
        y_canvas = self.hist_canvas.canvasy(event.y)
        row_idx = int(y_canvas // self.hist_row_height)
        if 0 <= row_idx < len(self.hist_rows_cache):
            self.hist_selected_key = self.hist_rows_cache[row_idx][0]
            self.redraw()

    def _on_hist_mousewheel(self, event):
        self.hist_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _jump_today(self):
        self.anchor_date = datetime.date.today()
        self.hovered_index = None
        self.redraw()

    def _shift_period(self, delta: int):
        rng = self.range_var.get()
        d = self.anchor_date
        if rng == "Week":
            self.anchor_date = d + datetime.timedelta(weeks=delta)
        elif rng == "Month":
            month_idx = (d.year * 12 + (d.month - 1)) + delta
            new_year = month_idx // 12
            new_month = (month_idx % 12) + 1
            max_day = calendar.monthrange(new_year, new_month)[1]
            self.anchor_date = datetime.date(new_year, new_month, min(d.day, max_day))
        else:  # Year
            new_year = max(1970, d.year + delta)
            max_day = calendar.monthrange(new_year, d.month)[1]
            self.anchor_date = datetime.date(new_year, d.month, min(d.day, max_day))
        self.hovered_index = None
        self.redraw()

    def _format_long_date(self, d: datetime.date) -> str:
        return f"{d.strftime('%A')}, {d.day} {d.strftime('%B')} {d.year}"

    def _get_metric_key_and_colors(self) -> tuple[str, str, str]:
        param = self.param_var.get()
        if param == "Drawn":
            return "draw_mm", "#1e3a5f", "#38bdf8"
        if param == "Hover":
            return "hover_mm", "#2e1065", "#a78bfa"
        return "total_mm", "#3f3f46", "#f4f4f5"

    def _collect_buckets(
        self, daily_snapshot: dict[str, dict[str, float]], metric_key: str
    ) -> tuple[str, list[tuple[str, str, float]]]:
        """Returns (range_header_text, [(x_label, tooltip_title, value_mm), ...])."""
        rng = self.range_var.get()
        d = self.anchor_date
        buckets: list[tuple[str, str, float]] = []

        if rng == "Week":
            start = d - datetime.timedelta(days=d.weekday())
            end = start + datetime.timedelta(days=6)
            header = f"{self._format_long_date(start)}  —  {self._format_long_date(end)}"
            for i in range(7):
                day = start + datetime.timedelta(days=i)
                val_mm = daily_snapshot.get(day.isoformat(), {}).get(metric_key, 0.0)
                buckets.append(
                    (f"{day.strftime('%a')} {day.day}", self._format_long_date(day), val_mm)
                )
            return header, buckets

        if rng == "Month":
            days_in_month = calendar.monthrange(d.year, d.month)[1]
            start = datetime.date(d.year, d.month, 1)
            end = datetime.date(d.year, d.month, days_in_month)
            header = f"{self._format_long_date(start)}  —  {self._format_long_date(end)}"
            for day_num in range(1, days_in_month + 1):
                day = datetime.date(d.year, d.month, day_num)
                val_mm = daily_snapshot.get(day.isoformat(), {}).get(metric_key, 0.0)
                buckets.append((str(day_num), self._format_long_date(day), val_mm))
            return header, buckets

        # Year
        start = datetime.date(d.year, 1, 1)
        end = datetime.date(d.year, 12, 31)
        header = f"{self._format_long_date(start)}  —  {self._format_long_date(end)}"
        for m in range(1, 13):
            prefix = f"{d.year:04d}-{m:02d}-"
            month_sum = sum(
                entry.get(metric_key, 0.0)
                for iso_key, entry in daily_snapshot.items()
                if iso_key.startswith(prefix)
            )
            month_label = datetime.date(d.year, m, 1).strftime("%b")
            full_label = datetime.date(d.year, m, 1).strftime("%B %Y")
            buckets.append((month_label, full_label, month_sum))
        return header, buckets

    def _collect_histogram_rows(
        self, daily_snapshot: dict[str, dict[str, float]], metric_key: str
    ) -> list[tuple[str, str, float]]:
        """Aggregates daily records by Dates, Weeks, Months, or Years."""
        mode = self.hist_group_var.get()
        grouped: dict[str, tuple[str, float]] = {}

        for iso_key, entry in daily_snapshot.items():
            val_mm = float(entry.get(metric_key, 0.0))
            if val_mm <= 0:
                continue

            try:
                dt = datetime.date.fromisoformat(iso_key)
            except ValueError:
                dt = None

            if dt is None:
                sort_key = iso_key
                label = iso_key
            elif mode == "Dates":
                sort_key = iso_key
                label = self._format_long_date(dt)
            elif mode == "Weeks":
                iso_year, iso_week, _ = dt.isocalendar()
                week_start = dt - datetime.timedelta(days=dt.weekday())
                week_end = week_start + datetime.timedelta(days=6)
                sort_key = f"{iso_year:04d}-W{iso_week:02d}"
                start_str = f"{week_start.day} {week_start.strftime('%b')}"
                end_str = f"{week_end.day} {week_end.strftime('%b')}"
                label = f"Week {iso_week}, {iso_year} ({start_str} – {end_str})"
            elif mode == "Months":
                sort_key = f"{dt.year:04d}-{dt.month:02d}"
                label = f"{dt.strftime('%B')} {dt.year}"
            else:  # Years
                sort_key = f"{dt.year:04d}"
                label = f"{dt.year}"

            prev = grouped.get(sort_key)
            if prev is None:
                grouped[sort_key] = (label, val_mm)
            else:
                grouped[sort_key] = (prev[0], prev[1] + val_mm)

        rows = [(k, lbl, total) for k, (lbl, total) in grouped.items()]
        if self.hist_sort_col == "date":
            rows.sort(key=lambda r: r[0], reverse=self.hist_sort_desc)
        else:
            rows.sort(key=lambda r: (r[2], r[0]), reverse=self.hist_sort_desc)
        return rows

    def _format_axis_tick(self, val_in_unit: float, unit: str, step: float) -> str:
        if val_in_unit == 0:
            return "0"
        if step >= 10:
            return f"{val_in_unit:,.0f}"
        if step >= 1:
            return f"{val_in_unit:,.1f}".rstrip("0").rstrip(".")
        if step >= 0.1:
            return f"{val_in_unit:,.2f}".rstrip("0").rstrip(".")
        return f"{val_in_unit:,.3f}".rstrip("0").rstrip(".")

    def _on_mouse_move(self, event):
        hit = None
        for idx, (x0, y0, x1, y1, _title, _val) in enumerate(self.bar_hitboxes):
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                hit = idx
                break
        if hit != self.hovered_index or hit is not None:
            self.hovered_index = hit
            self.tooltip_pos = (event.x, event.y) if hit is not None else None
            self.redraw()

    def _on_mouse_leave(self, _event):
        if self.hovered_index is not None:
            self.hovered_index = None
            self.tooltip_pos = None
            self.redraw()

    def redraw(self):
        if not self.win.winfo_exists():
            return

        with self.app.lock:
            daily_snapshot = {
                k: dict(v) for k, v in self.app.daily.items()
            }

        metric_key, bar_fill, bar_outline = self._get_metric_key_and_colors()

        if self.active_tab == "Histogram":
            all_total_mm = sum(
                entry.get(metric_key, 0.0) for entry in daily_snapshot.values()
            )
            self.lbl_period_sum.config(
                text=f"All-time total ({self.param_var.get()}): {self.app._format_lifetime(all_total_mm)}"
            )
            self._draw_histogram_table(daily_snapshot, metric_key, bar_fill, bar_outline)
            return

        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 100 or h < 100:
            return

        unit = self.app.unit
        divisor = UNIT_DIVISORS[unit]

        header_text, buckets = self._collect_buckets(daily_snapshot, metric_key)
        self.lbl_date_range.config(text=header_text)

        period_total_mm = sum(b[2] for b in buckets)
        self.lbl_period_sum.config(
            text=f"Period total ({self.param_var.get()}): {self.app._format_lifetime(period_total_mm)}"
        )

        self.canvas.delete("all")
        self.bar_hitboxes.clear()

        pad_left = 68
        pad_right = 18
        pad_top = 18
        pad_bottom = 28

        plot_w = max(10, w - pad_left - pad_right)
        plot_h = max(10, h - pad_top - pad_bottom)
        x_start = pad_left
        x_end = pad_left + plot_w
        y_top = pad_top
        y_bottom = pad_top + plot_h

        self._draw_time_graph(
            buckets,
            unit,
            divisor,
            bar_fill,
            bar_outline,
            x_start,
            x_end,
            y_top,
            y_bottom,
            plot_w,
            plot_h,
        )

        # Draw hover tooltip on top
        if (
            self.hovered_index is not None
            and 0 <= self.hovered_index < len(self.bar_hitboxes)
            and self.tooltip_pos is not None
        ):
            _x0, _y0, _x1, _y1, title, val_mm = self.bar_hitboxes[self.hovered_index]
            val_str = self.app._format_lifetime(val_mm)
            tip_text = f"{title}\n{self.param_var.get()}: {val_str}"
            tx, ty = self.tooltip_pos
            box_w = max(175, len(title) * 7 + 24)
            box_h = 44
            bx = min(max(tx + 12, x_start), x_end - box_w)
            by = max(y_top + 4, ty - box_h - 8)
            self.canvas.create_rectangle(
                bx,
                by,
                bx + box_w,
                by + box_h,
                fill="#18181b",
                outline=bar_outline,
                width=1.5,
            )
            self.canvas.create_text(
                bx + 10,
                by + box_h / 2,
                text=tip_text,
                anchor="w",
                fill="#f4f4f5",
                font=("Segoe UI", 8, "bold"),
            )

    def _draw_time_graph(
        self,
        buckets: list[tuple[str, str, float]],
        unit: str,
        divisor: float,
        bar_fill: str,
        bar_outline: str,
        x_start: float,
        x_end: float,
        y_top: float,
        y_bottom: float,
        plot_w: float,
        plot_h: float,
    ):
        max_val_unit = max((b[2] / divisor for b in buckets), default=0.0)
        top_val, ticks = _nice_axis_ticks(max_val_unit, target_ticks=7)
        step = ticks[1] - ticks[0] if len(ticks) > 1 else 1.0

        # Horizontal grid lines & Y-axis labels
        for tick_val in ticks:
            frac = tick_val / top_val if top_val > 0 else 0.0
            y = y_bottom - frac * plot_h
            self.canvas.create_line(x_start, y, x_end, y, fill="#2e2e34", width=1)
            if tick_val > 0:
                lbl = f"{self._format_axis_tick(tick_val, unit, step)} {unit}"
                self.canvas.create_text(
                    x_start - 8,
                    y,
                    text=lbl,
                    anchor="e",
                    fill="#a1a1aa",
                    font=("Segoe UI", 8),
                )

        n = len(buckets)
        col_w = plot_w / max(1, n)

        # Vertical grid lines, X-axis labels, and bars
        for i, (x_lbl, title, val_mm) in enumerate(buckets):
            cx0 = x_start + i * col_w
            cx1 = cx0 + col_w
            self.canvas.create_line(cx0, y_top, cx0, y_bottom, fill="#2e2e34", width=1)

            self.canvas.create_text(
                (cx0 + cx1) / 2,
                y_bottom + 12,
                text=x_lbl,
                fill="#a1a1aa",
                font=("Segoe UI", 8),
            )

            val_unit = val_mm / divisor
            bar_h = (val_unit / top_val) * plot_h if top_val > 0 else 0.0
            if val_mm > 0:
                bar_h = max(bar_h, 2.0)
                bx0 = cx0 + 1
                bx1 = max(bx0 + 2, cx1 - 1)
                by0 = y_bottom - bar_h
                is_hovered = self.hovered_index == i
                self.canvas.create_rectangle(
                    bx0,
                    by0,
                    bx1,
                    y_bottom,
                    fill=bar_outline if is_hovered else bar_fill,
                    outline=bar_outline,
                    width=1.5,
                )

            self.bar_hitboxes.append((cx0, y_top, cx1, y_bottom, title, val_mm))

        self.canvas.create_rectangle(
            x_start, y_top, x_end, y_bottom, outline="#52525b", width=1
        )

    def _draw_histogram_table(
        self,
        daily_snapshot: dict[str, dict[str, float]],
        metric_key: str,
        bar_fill: str,
        bar_outline: str,
    ):
        """Gameplay-Time-Tracker-style chronological Period | Value table with inline bars."""
        w = self.hist_canvas.winfo_width()
        h = self.hist_canvas.winfo_height()
        if w < 100 or h < 50:
            return

        rows = self._collect_histogram_rows(daily_snapshot, metric_key)
        self.hist_rows_cache = rows
        self.hist_canvas.delete("all")

        if not rows:
            self.hist_canvas.configure(scrollregion=(0, 0, w, h))
            self.hist_canvas.create_text(
                w / 2,
                h / 2,
                text="No Wacom activity recorded yet",
                fill="#71717a",
                font=("Segoe UI", 10),
            )
            return

        valid_keys = {r[0] for r in rows}
        if self.hist_selected_key not in valid_keys:
            self.hist_selected_key = rows[0][0]

        max_val_mm = max((r[2] for r in rows), default=1.0)
        row_h = self.hist_row_height
        split_x = self.btn_sort_date.winfo_width()
        if split_x < 120:
            split_x = min(self.hist_date_col_w, int(w * 0.38))

        total_h = len(rows) * row_h
        self.hist_canvas.configure(scrollregion=(0, 0, w, max(h, total_h)))

        val_col_x0 = split_x + 8
        val_text_reserve = 115
        max_bar_w = max(20.0, w - val_col_x0 - val_text_reserve)

        for idx, (sort_key, period_label, val_mm) in enumerate(rows):
            y0 = idx * row_h
            y1 = y0 + row_h
            is_selected = sort_key == self.hist_selected_key

            row_bg = "#0284c7" if is_selected else ("#1f1f23" if idx % 2 == 0 else "#232329")
            text_fg = "#ffffff" if is_selected else "#e4e4e7"
            grid_line = "#0369a1" if is_selected else "#2e2e34"

            self.hist_canvas.create_rectangle(
                0, y0, w, y1, fill=row_bg, outline=""
            )

            self.hist_canvas.create_text(
                split_x - 12,
                (y0 + y1) / 2,
                text=period_label,
                anchor="e",
                fill=text_fg,
                font=("Segoe UI", 9, "bold" if is_selected else "normal"),
            )

            self.hist_canvas.create_line(
                split_x, y0, split_x, y1, fill=grid_line, width=1
            )

            frac = val_mm / max_val_mm if max_val_mm > 0 else 0.0
            bar_w = max(2.0, frac * max_bar_w)
            bar_y0 = y0 + 5
            bar_y1 = y1 - 5
            self.hist_canvas.create_rectangle(
                val_col_x0,
                bar_y0,
                val_col_x0 + bar_w,
                bar_y1,
                fill="#0369a1" if is_selected else bar_fill,
                outline="#7dd3fc" if is_selected else bar_outline,
                width=1,
            )

            val_str = self.app._format_lifetime(val_mm)
            self.hist_canvas.create_text(
                w - 14,
                (y0 + y1) / 2,
                text=val_str,
                anchor="e",
                fill=text_fg,
                font=("Segoe UI", 9, "bold" if is_selected else "normal"),
            )

            self.hist_canvas.create_line(
                0, y1 - 1, w, y1 - 1, fill=grid_line, width=1
            )

    def close(self):
        self.app.stats_window = None
        if self.win.winfo_exists():
            self.win.destroy()


class WacomTrackerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Wacom Tracker")
        self.root.configure(bg="#18181b")
        self.root.resizable(False, False)
        _apply_window_icon(self.root, as_default=True)

        # Tablet active area properties (loaded from JSON or configured on startup)
        self.tablet_model = DEFAULT_TABLET_MODEL
        self.tablet_width_mm = DEFAULT_TABLET_WIDTH_MM
        self.tablet_height_mm = DEFAULT_TABLET_HEIGHT_MM
        self.needs_tablet_setup = True

        # Display region properties (loaded from JSON or auto-detected/configured on startup)
        detected = detect_connected_displays()
        primary = detected[0]
        self.display_name = primary["short_name"]
        self.display_width_px = primary["width"]
        self.display_height_px = primary["height"]
        self.display_offset_x = primary["offset_x"]
        self.display_offset_y = primary["offset_y"]
        self.needs_display_setup = True

        self.mm_per_px_x = self.tablet_width_mm / self.display_width_px
        self.mm_per_px_y = self.tablet_height_mm / self.display_height_px
        self.bounds = (
            self.display_offset_x,
            self.display_offset_y,
            self.display_offset_x + self.display_width_px,
            self.display_offset_y + self.display_height_px,
        )

        # State variables
        self.lock = threading.Lock()
        self._save_lock = threading.Lock()
        self.total_mm = 0.0
        self.draw_mm = 0.0
        self.hover_mm = 0.0
        self.session_total_mm = 0.0
        self.session_draw_mm = 0.0
        self.session_hover_mm = 0.0
        self.daily: dict[str, dict[str, float]] = {}
        self.unit = "km"
        self.minimize_to_tray = True

        # Direct HID tracking state (works in OneNote & all Windows Ink apps)
        self.last_hid_pos_mm: tuple[float, float] | None = None
        self.last_hid_packet_time = 0.0
        self.last_wacom_any_time = 0.0

        # Fallback cursor tracking state (used if a profile disables Windows Ink)
        self.last_cursor_pos: tuple[float, float] | None = None
        self.cursor_is_drawing = False
        self.physical_mouse_active = False

        self.paused = False
        self.ticks_since_save = 0
        self._save_btn_after_id: str | None = None
        self.unit_buttons: dict[str, tk.Button] = {}
        self.stats_window: WacomStatsWindow | None = None
        self.settings_window: SettingsWindow | None = None
        self.tray_icon: Any = None
        self._is_quitting = False

        # Device caches
        self.device_is_wacom: dict[int, bool] = {}
        self.digitizer_specs: dict[int, WacomDigitizerSpec | None] = {}

        self._load_data()

        if self.needs_tablet_setup or self.needs_display_setup:
            self.root.withdraw()

            if self.needs_tablet_setup:
                t_dlg = TabletSetupDialog(
                    self.root,
                    initial_model=self.tablet_model,
                    initial_w=self.tablet_width_mm,
                    initial_h=self.tablet_height_mm,
                    required=True,
                )
                self.root.wait_window(t_dlg.win)
                if t_dlg.result is None:
                    self.root.destroy()
                    sys.exit(0)
                model_name, w_mm, h_mm = t_dlg.result
                self._apply_tablet_properties(model_name, w_mm, h_mm)

            if self.needs_display_setup:
                d_dlg = DisplaySetupDialog(
                    self.root,
                    initial_name=self.display_name,
                    initial_w=self.display_width_px,
                    initial_h=self.display_height_px,
                    initial_ox=self.display_offset_x,
                    initial_oy=self.display_offset_y,
                    required=True,
                )
                self.root.wait_window(d_dlg.win)
                if d_dlg.result is None:
                    self.root.destroy()
                    sys.exit(0)
                d_name, d_w, d_h, d_ox, d_oy = d_dlg.result
                self._apply_display_properties(d_name, d_w, d_h, d_ox, d_oy)

            self._save_data()
            self.root.deiconify()
        else:
            self._apply_tablet_properties(
                self.tablet_model, self.tablet_width_mm, self.tablet_height_mm
            )
            self._apply_display_properties(
                self.display_name,
                self.display_width_px,
                self.display_height_px,
                self.display_offset_x,
                self.display_offset_y,
            )

        self._build_ui()
        self._setup_raw_input_filter()
        self._register_exit_hooks()
        self._start_tray_icon()

        # Fallback listener when a Wacom profile uses legacy mouse injection
        self.listener = mouse.Listener(
            on_move=self._on_cursor_move,
            on_click=self._on_cursor_click,
        )
        self.listener.start()

        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self._update_loop()

    def _start_tray_icon(self):
        """Creates and runs the background system tray icon."""
        menu = pystray.Menu(
            pystray.MenuItem(
                "Open Wacom Tracker",
                self._on_tray_open,
                default=True,
            ),
            pystray.MenuItem(
                lambda _item: "Resume Tracking" if self.paused else "Pause Tracking",
                self._on_tray_toggle_pause,
            ),
            pystray.MenuItem(
                "Minimize to Tray on Close",
                self._on_tray_toggle_minimize,
                checked=lambda _item: self.minimize_to_tray,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Quit",
                self._on_tray_quit,
            ),
        )
        icon = pystray.Icon(
            "wacom_tracker",
            _create_app_icon_image(),
            "Wacom Tracker",
            menu,
        )
        icon.run_detached()
        self.tray_icon = icon

    def _on_window_close(self):
        """Minimizes to the system tray or exits based on the tray toggle setting."""
        with self.lock:
            should_minimize = self.minimize_to_tray

        if should_minimize:
            self._minimize_to_tray()
        else:
            self._quit_app()

    def _minimize_to_tray(self):
        """Hides the main (and child) windows to the system tray while tracking continues."""
        self._save_data()
        if self.settings_window is not None and self.settings_window.win.winfo_exists():
            self.settings_window.close()
        if self.stats_window is not None and self.stats_window.win.winfo_exists():
            self.stats_window.close()
        self.root.withdraw()

    def _restore_from_tray(self):
        """Brings the main window back from the system tray."""
        if self._is_quitting:
            return
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._refresh_display()

    def _on_tray_open(self, _icon=None, _item=None):
        self.root.after(0, self._restore_from_tray)

    def _on_tray_toggle_pause(self, _icon=None, _item=None):
        self.root.after(0, self._toggle_pause)

    def _on_tray_toggle_minimize(self, _icon=None, _item=None):
        self.root.after(0, self._toggle_minimize_to_tray)

    def _toggle_minimize_to_tray(self):
        with self.lock:
            self.minimize_to_tray = not self.minimize_to_tray
            enabled = self.minimize_to_tray
        if self.tray_icon is not None:
            try:
                self.tray_icon.update_menu()
            except Exception:
                pass
        if self.settings_window is not None and self.settings_window.win.winfo_exists():
            self.settings_window.sync_state()
        self._save_data()
        # If the user disabled minimize-to-tray while the window is already hidden, restore it
        if not enabled and not self.root.winfo_viewable():
            self._restore_from_tray()

    def _on_tray_quit(self, _icon=None, _item=None):
        self.root.after(0, self._quit_app)

    def _apply_tablet_properties(self, model_name: str, width_mm: float, height_mm: float):
        with self.lock:
            self.tablet_model = model_name
            self.tablet_width_mm = width_mm
            self.tablet_height_mm = height_mm
            self.mm_per_px_x = width_mm / self.display_width_px
            self.mm_per_px_y = height_mm / self.display_height_px
            self.last_hid_pos_mm = None
            self.last_cursor_pos = None

        if hasattr(self, "lbl_tablet"):
            self.lbl_tablet.config(
                text=f"Tablet: {model_name} ({width_mm:g} × {height_mm:g} mm)"
            )

    def _apply_display_properties(
        self,
        display_name: str,
        width_px: int,
        height_px: int,
        offset_x: int,
        offset_y: int,
    ):
        with self.lock:
            self.display_name = display_name
            self.display_width_px = width_px
            self.display_height_px = height_px
            self.display_offset_x = offset_x
            self.display_offset_y = offset_y
            self.mm_per_px_x = self.tablet_width_mm / width_px
            self.mm_per_px_y = self.tablet_height_mm / height_px
            self.bounds = (
                offset_x,
                offset_y,
                offset_x + width_px,
                offset_y + height_px,
            )
            self.last_cursor_pos = None

        if hasattr(self, "lbl_display"):
            self.lbl_display.config(
                text=f"Display: {display_name} ({width_px}×{height_px} @ {offset_x}, {offset_y})"
            )

    def _open_tablet_settings(self, parent: tk.Misc | None = None):
        dlg = TabletSetupDialog(
            parent or self.root,
            initial_model=self.tablet_model,
            initial_w=self.tablet_width_mm,
            initial_h=self.tablet_height_mm,
            required=False,
        )
        self.root.wait_window(dlg.win)
        if dlg.result is not None:
            model_name, w_mm, h_mm = dlg.result
            self._apply_tablet_properties(model_name, w_mm, h_mm)
            self._save_data()

    def _open_display_settings(self, parent: tk.Misc | None = None):
        dlg = DisplaySetupDialog(
            parent or self.root,
            initial_name=self.display_name,
            initial_w=self.display_width_px,
            initial_h=self.display_height_px,
            initial_ox=self.display_offset_x,
            initial_oy=self.display_offset_y,
            required=False,
        )
        self.root.wait_window(dlg.win)
        if dlg.result is not None:
            d_name, d_w, d_h, d_ox, d_oy = dlg.result
            self._apply_display_properties(d_name, d_w, d_h, d_ox, d_oy)
            self._save_data()

    def _open_settings(self):
        if self.settings_window is not None and self.settings_window.win.winfo_exists():
            self.settings_window.win.deiconify()
            _position_window_on_parent(self.settings_window.win, self.root)
            self.settings_window.win.lift()
            self.settings_window.win.focus_force()
            self.settings_window.sync_state()
            return
        self.settings_window = SettingsWindow(self)

    def _record_distance_locked(self, dist_mm: float, is_pen_down: bool):
        """Accumulates lifetime, session, and daily distances (caller holds self.lock)."""
        self.total_mm += dist_mm
        self.session_total_mm += dist_mm

        today_key = datetime.date.today().isoformat()
        day_entry = self.daily.get(today_key)
        if day_entry is None:
            day_entry = {"total_mm": 0.0, "draw_mm": 0.0, "hover_mm": 0.0}
            self.daily[today_key] = day_entry

        day_entry["total_mm"] += dist_mm
        if is_pen_down:
            self.draw_mm += dist_mm
            self.session_draw_mm += dist_mm
            day_entry["draw_mm"] += dist_mm
        else:
            self.hover_mm += dist_mm
            self.session_hover_mm += dist_mm
            day_entry["hover_mm"] += dist_mm

    def _register_exit_hooks(self):
        """Ensures progress is flushed to disk even if closed via terminal, Ctrl+C, or shutdown."""
        atexit.register(self._save_data)

        def _signal_handler(signum, frame):
            self._save_data()
            raise SystemExit(0)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _signal_handler)
            except (ValueError, OSError):
                pass

        def _console_ctrl_handler(ctrl_type: int) -> bool:
            if ctrl_type in (0, 1, 2, 5, 6):
                self._save_data()
            return False

        self._console_handler_ref = PHANDLER_ROUTINE(_console_ctrl_handler)
        kernel32.SetConsoleCtrlHandler(self._console_handler_ref, True)

    def _inspect_hid_digitizer(self, h_device: int) -> WacomDigitizerSpec | None:
        """Builds a HID parser for a Wacom digitizer device if it reports X/Y axes."""
        if h_device in self.digitizer_specs:
            return self.digitizer_specs[h_device]

        prep_size = wintypes.UINT(0)
        user32.GetRawInputDeviceInfoW(
            h_device, RIDI_PREPARSEDDATA, None, ctypes.byref(prep_size)
        )
        if prep_size.value == 0:
            self.digitizer_specs[h_device] = None
            return None

        prep_buf = ctypes.create_string_buffer(prep_size.value)
        if (
            user32.GetRawInputDeviceInfoW(
                h_device, RIDI_PREPARSEDDATA, prep_buf, ctypes.byref(prep_size)
            )
            == 0xFFFFFFFF
        ):
            self.digitizer_specs[h_device] = None
            return None

        caps = HIDP_CAPS()
        if hid.HidP_GetCaps(prep_buf, ctypes.byref(caps)) != HIDP_STATUS_SUCCESS:
            self.digitizer_specs[h_device] = None
            return None

        num_val_caps = wintypes.USHORT(caps.NumberInputValueCaps)
        if num_val_caps.value == 0:
            self.digitizer_specs[h_device] = None
            return None

        val_caps = (HIDP_VALUE_CAPS * num_val_caps.value)()
        if (
            hid.HidP_GetValueCaps(
                HIDP_INPUT, val_caps, ctypes.byref(num_val_caps), prep_buf
            )
            != HIDP_STATUS_SUCCESS
        ):
            self.digitizer_specs[h_device] = None
            return None

        x_range = None
        y_range = None
        for i in range(num_val_caps.value):
            vc = val_caps[i]
            if vc.UsagePage == 0x01 and not vc.IsRange:
                usage = vc.u.NotRange.Usage
                if usage == 0x30 and vc.LogicalMax > vc.LogicalMin:
                    x_range = (vc.LogicalMin, vc.LogicalMax)
                elif usage == 0x31 and vc.LogicalMax > vc.LogicalMin:
                    y_range = (vc.LogicalMin, vc.LogicalMax)

        if x_range and y_range:
            spec = WacomDigitizerSpec(
                prep_buf,
                x_range[0],
                x_range[1],
                y_range[0],
                y_range[1],
                caps.NumberInputButtonCaps * 8,
            )
            self.digitizer_specs[h_device] = spec
            return spec

        self.digitizer_specs[h_device] = None
        return None

    def _discover_wacom_usages(self) -> set[tuple[int, int]]:
        """Finds all HID UsagePage/Usage pairs belonging to connected Wacom devices."""
        extra_usages: set[tuple[int, int]] = set()
        num_devices = wintypes.UINT(0)
        item_size = ctypes.sizeof(RAWINPUTDEVICELIST)

        if user32.GetRawInputDeviceList(None, ctypes.byref(num_devices), item_size) != 0:
            return extra_usages

        if num_devices.value == 0:
            return extra_usages

        dev_list = (RAWINPUTDEVICELIST * num_devices.value)()
        count = user32.GetRawInputDeviceList(
            dev_list, ctypes.byref(num_devices), item_size
        )
        if count == 0xFFFFFFFF:
            return extra_usages

        for i in range(count):
            dev = dev_list[i]
            if dev.dwType == RIM_TYPEHID and dev.hDevice:
                info = RID_DEVICE_INFO()
                info.cbSize = ctypes.sizeof(RID_DEVICE_INFO)
                info_size = wintypes.UINT(info.cbSize)
                res = user32.GetRawInputDeviceInfoW(
                    dev.hDevice,
                    RIDI_DEVICEINFO,
                    ctypes.byref(info),
                    ctypes.byref(info_size),
                )
                if res != 0xFFFFFFFF and info.u.hid.dwVendorId == WACOM_VID_INT:
                    h_dev = int(dev.hDevice)
                    self.device_is_wacom[h_dev] = True
                    self._inspect_hid_digitizer(h_dev)
                    page = info.u.hid.usUsagePage
                    usage = info.u.hid.usUsage
                    if page != 0x0D:
                        extra_usages.add((page, usage))

        return extra_usages

    def _setup_raw_input_filter(self):
        """Registers background Raw Input on the top-level window."""
        self.root.update_idletasks()
        child_hwnd = self.root.winfo_id()
        top_hwnd = user32.GetAncestor(child_hwnd, GA_ROOT) or child_hwnd

        registrations = [
            RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, top_hwnd),
            RAWINPUTDEVICE(0x0D, 0x00, RIDEV_PAGEONLY | RIDEV_INPUTSINK, top_hwnd),
        ]

        for page, usage in self._discover_wacom_usages():
            registrations.append(
                RAWINPUTDEVICE(page, usage, RIDEV_INPUTSINK, top_hwnd)
            )

        devices = (RAWINPUTDEVICE * len(registrations))(*registrations)
        user32.RegisterRawInputDevices(
            devices, len(registrations), ctypes.sizeof(RAWINPUTDEVICE)
        )

        self._wndproc_ref = WNDPROC(self._wndproc)
        self._old_wndproc = user32.SetWindowLongPtrW(
            top_hwnd, GWLP_WNDPROC, self._wndproc_ref
        )

    def _is_wacom_handle(self, h_device: int) -> bool:
        if h_device in self.device_is_wacom:
            return self.device_is_wacom[h_device]

        char_count = wintypes.UINT(0)
        user32.GetRawInputDeviceInfoW(
            h_device, RIDI_DEVICENAME, None, ctypes.byref(char_count)
        )
        is_wacom = False
        if char_count.value > 0:
            name_buf = ctypes.create_unicode_buffer(char_count.value)
            user32.GetRawInputDeviceInfoW(
                h_device, RIDI_DEVICENAME, name_buf, ctypes.byref(char_count)
            )
            is_wacom = WACOM_VID_STR in name_buf.value.upper()

        self.device_is_wacom[h_device] = is_wacom
        if is_wacom:
            self._inspect_hid_digitizer(h_device)
        return is_wacom

    def _process_wacom_hid_reports(self, h_device: int, raw_buf, total_size: int):
        """Extracts exact millimeter movement directly from Wacom HID reports."""
        now = time.monotonic()
        with self.lock:
            self.last_wacom_any_time = now
            self.physical_mouse_active = False

        spec = self._inspect_hid_digitizer(h_device)
        if spec is None:
            return

        header_size = ctypes.sizeof(RAWINPUTHEADER)
        rawhid_size = ctypes.sizeof(RAWHID)
        if total_size < header_size + rawhid_size:
            return

        rawhid = RAWHID.from_buffer_copy(raw_buf, header_size)
        report_len = rawhid.dwSizeHid
        report_count = rawhid.dwCount
        offset = header_size + rawhid_size

        raw_x = wintypes.ULONG(0)
        raw_y = wintypes.ULONG(0)

        for i in range(report_count):
            rep_start = offset + i * report_len
            if rep_start + report_len > total_size:
                break

            rep_ptr = ctypes.byref(raw_buf, rep_start)

            # Query active digitizer switches (0x32=In Range, 0x42=Tip Switch, 0x45=Eraser)
            usage_len = wintypes.ULONG(spec.max_buttons)
            usages = (wintypes.USHORT * spec.max_buttons)()
            in_range = True
            tip_down = False
            if (
                hid.HidP_GetUsages(
                    HIDP_INPUT,
                    0x0D,
                    0,
                    usages,
                    ctypes.byref(usage_len),
                    spec.preparsed_buf,
                    rep_ptr,
                    report_len,
                )
                == HIDP_STATUS_SUCCESS
            ):
                active_usages = {usages[j] for j in range(usage_len.value)}
                in_range = 0x32 in active_usages
                tip_down = (0x42 in active_usages) or (0x45 in active_usages)

            if not in_range:
                with self.lock:
                    self.last_hid_pos_mm = None
                continue

            status_x = hid.HidP_GetUsageValue(
                HIDP_INPUT,
                0x01,
                0,
                0x30,
                ctypes.byref(raw_x),
                spec.preparsed_buf,
                rep_ptr,
                report_len,
            )
            status_y = hid.HidP_GetUsageValue(
                HIDP_INPUT,
                0x01,
                0,
                0x31,
                ctypes.byref(raw_y),
                spec.preparsed_buf,
                rep_ptr,
                report_len,
            )

            if status_x == HIDP_STATUS_SUCCESS and status_y == HIDP_STATUS_SUCCESS:
                norm_x = (raw_x.value - spec.x_min) / (spec.x_max - spec.x_min)
                norm_y = (raw_y.value - spec.y_min) / (spec.y_max - spec.y_min)

                with self.lock:
                    if self.paused:
                        self.last_hid_pos_mm = None
                        self.last_hid_packet_time = now
                        continue

                    cur_x_mm = norm_x * self.tablet_width_mm
                    cur_y_mm = norm_y * self.tablet_height_mm

                    if (
                        self.last_hid_pos_mm is not None
                        and (now - self.last_hid_packet_time) <= 0.15
                    ):
                        dist_mm = math.hypot(
                            cur_x_mm - self.last_hid_pos_mm[0],
                            cur_y_mm - self.last_hid_pos_mm[1],
                        )
                        if dist_mm <= MAX_STEP_MM:
                            self._record_distance_locked(dist_mm, tip_down)

                    self.last_hid_pos_mm = (cur_x_mm, cur_y_mm)
                    self.last_hid_packet_time = now

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_INPUT:
            size = wintypes.UINT(0)
            header_size = ctypes.sizeof(RAWINPUTHEADER)
            user32.GetRawInputData(
                lparam, RID_INPUT, None, ctypes.byref(size), header_size
            )
            if size.value >= header_size:
                buf = ctypes.create_string_buffer(size.value)
                if (
                    user32.GetRawInputData(
                        lparam, RID_INPUT, buf, ctypes.byref(size), header_size
                    )
                    != 0xFFFFFFFF
                ):
                    header = ctypes.cast(buf, ctypes.POINTER(RAWINPUTHEADER)).contents
                    h_dev = int(header.hDevice) if header.hDevice else 0

                    if h_dev != 0:
                        if self._is_wacom_handle(h_dev):
                            if header.dwType == RIM_TYPEHID:
                                self._process_wacom_hid_reports(h_dev, buf, size.value)
                        elif header.dwType == RIM_TYPEMOUSE:
                            with self.lock:
                                self.physical_mouse_active = True
                                self.last_cursor_pos = None
                                self.cursor_is_drawing = False

        return user32.CallWindowProcW(self._old_wndproc, hwnd, msg, wparam, lparam)

    def _load_data(self):
        if not os.path.exists(SAVE_FILE):
            self.needs_tablet_setup = True
            self.needs_display_setup = True
            return

        try:
            with open(SAVE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    self.needs_tablet_setup = True
                    self.needs_display_setup = True
                    return

                self.total_mm = float(data.get("total_mm", 0.0))
                self.draw_mm = float(data.get("draw_mm", 0.0))
                if "hover_mm" in data:
                    self.hover_mm = float(data.get("hover_mm", 0.0))
                else:
                    self.hover_mm = max(0.0, self.total_mm - self.draw_mm)

                raw_daily = data.get("daily", {})
                if isinstance(raw_daily, dict):
                    for day_key, vals in raw_daily.items():
                        if isinstance(vals, dict):
                            t_v = float(vals.get("total_mm", 0.0))
                            d_v = float(vals.get("draw_mm", 0.0))
                            h_v = float(vals.get("hover_mm", max(0.0, t_v - d_v)))
                            self.daily[str(day_key)] = {
                                "total_mm": t_v,
                                "draw_mm": d_v,
                                "hover_mm": h_v,
                            }

                if not self.daily and self.total_mm > 0:
                    today_key = datetime.date.today().isoformat()
                    self.daily[today_key] = {
                        "total_mm": self.total_mm,
                        "draw_mm": self.draw_mm,
                        "hover_mm": self.hover_mm,
                    }

                saved_unit = data.get("unit", "km")
                if saved_unit in UNITS:
                    self.unit = saved_unit

                saved_min_tray = data.get("minimize_to_tray", True)
                if isinstance(saved_min_tray, bool):
                    self.minimize_to_tray = saved_min_tray

                # Validate tablet properties in save file
                raw_tw = data.get("tablet_width_mm")
                raw_th = data.get("tablet_height_mm")
                if (
                    isinstance(raw_tw, (int, float))
                    and not isinstance(raw_tw, bool)
                    and isinstance(raw_th, (int, float))
                    and not isinstance(raw_th, bool)
                    and float(raw_tw) > 0
                    and float(raw_th) > 0
                ):
                    self.tablet_width_mm = float(raw_tw)
                    self.tablet_height_mm = float(raw_th)
                    saved_model = data.get("tablet_model")
                    if isinstance(saved_model, str) and saved_model.strip():
                        self.tablet_model = saved_model.strip()
                    else:
                        self.tablet_model = "Custom Size"
                    self.needs_tablet_setup = False
                else:
                    self.needs_tablet_setup = True

                # Validate display region properties in save file
                raw_dw = data.get("display_width_px")
                raw_dh = data.get("display_height_px")
                raw_ox = data.get("display_offset_x")
                raw_oy = data.get("display_offset_y")
                if (
                    isinstance(raw_dw, (int, float))
                    and not isinstance(raw_dw, bool)
                    and isinstance(raw_dh, (int, float))
                    and not isinstance(raw_dh, bool)
                    and isinstance(raw_ox, (int, float))
                    and not isinstance(raw_ox, bool)
                    and isinstance(raw_oy, (int, float))
                    and not isinstance(raw_oy, bool)
                    and int(raw_dw) > 0
                    and int(raw_dh) > 0
                ):
                    self.display_width_px = int(raw_dw)
                    self.display_height_px = int(raw_dh)
                    self.display_offset_x = int(raw_ox)
                    self.display_offset_y = int(raw_oy)
                    saved_dname = data.get("display_name")
                    if isinstance(saved_dname, str) and saved_dname.strip():
                        self.display_name = saved_dname.strip()
                    else:
                        self.display_name = "Custom Region"
                    self.needs_display_setup = False
                else:
                    self.needs_display_setup = True
        except (OSError, ValueError, TypeError):
            self.needs_tablet_setup = True
            self.needs_display_setup = True

    def _save_data(self) -> bool:
        """Atomically writes tablet, display, distance totals, daily history, and settings to disk."""
        with self.lock:
            daily_out = {
                day_key: {
                    "total_mm": round(vals["total_mm"], 3),
                    "draw_mm": round(vals["draw_mm"], 3),
                    "hover_mm": round(vals["hover_mm"], 3),
                }
                for day_key, vals in sorted(self.daily.items())
            }
            payload = {
                "tablet_model": self.tablet_model,
                "tablet_width_mm": round(self.tablet_width_mm, 3),
                "tablet_height_mm": round(self.tablet_height_mm, 3),
                "display_name": self.display_name,
                "display_width_px": self.display_width_px,
                "display_height_px": self.display_height_px,
                "display_offset_x": self.display_offset_x,
                "display_offset_y": self.display_offset_y,
                "total_mm": round(self.total_mm, 3),
                "draw_mm": round(self.draw_mm, 3),
                "hover_mm": round(self.hover_mm, 3),
                "unit": self.unit,
                "minimize_to_tray": self.minimize_to_tray,
                "daily": daily_out,
            }
        tmp_file = f"{SAVE_FILE}.tmp"
        try:
            with self._save_lock:
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_file, SAVE_FILE)
            return True
        except OSError:
            return False

    def _in_mapped_monitor(self, x: float, y: float) -> bool:
        x0, y0, x1, y1 = self.bounds
        return x0 <= x <= x1 and y0 <= y <= y1

    def _is_fallback_cursor_allowed(self, now: float) -> bool:
        direct_hid_active = (now - self.last_hid_packet_time) < 0.15
        wacom_active_now = (now - self.last_wacom_any_time) < 0.15
        return (
            not direct_hid_active
            and wacom_active_now
            and not self.physical_mouse_active
        )

    def _on_cursor_click(self, x: float, y: float, button, pressed: bool):
        if button == mouse.Button.left:
            now = time.monotonic()
            with self.lock:
                if not self._is_fallback_cursor_allowed(now):
                    self.cursor_is_drawing = False
                    return
                self.cursor_is_drawing = pressed
                if pressed and self._in_mapped_monitor(x, y):
                    self.last_cursor_pos = (x, y)

    def _on_cursor_move(self, x: float, y: float):
        now = time.monotonic()
        with self.lock:
            if self.paused or not self._is_fallback_cursor_allowed(now):
                self.last_cursor_pos = None
                return

            if not self._in_mapped_monitor(x, y):
                self.last_cursor_pos = None
                return

            if self.last_cursor_pos is not None:
                dx_mm = (x - self.last_cursor_pos[0]) * self.mm_per_px_x
                dy_mm = (y - self.last_cursor_pos[1]) * self.mm_per_px_y
                dist_mm = math.hypot(dx_mm, dy_mm)

                if dist_mm <= MAX_STEP_MM:
                    self._record_distance_locked(dist_mm, self.cursor_is_drawing)

            self.last_cursor_pos = (x, y)

    def _format_lifetime(self, mm: float) -> str:
        if self.unit == "km":
            return f"{mm / 1_000_000.0:,.2f} km"
        if self.unit == "m":
            return f"{mm / 1_000.0:,.2f} m"
        return f"{mm:,.1f} mm"

    def _format_session_val(self, mm: float) -> str:
        if self.unit == "km":
            return f"{mm / 1_000_000.0:,.2f} km"
        if self.unit == "m":
            return f"{mm / 1_000.0:,.2f} m"
        return f"{mm:,.0f} mm"

    def _build_ui(self):
        tk.Label(
            self.root,
            text="WACOM TRACKER",
            font=("Segoe UI", 10, "bold"),
            fg="#a1a1aa",
            bg="#18181b",
        ).pack(pady=(14, 2))

        self.lbl_tablet = tk.Label(
            self.root,
            text=(
                f"Tablet: {self.tablet_model} "
                f"({self.tablet_width_mm:g} × {self.tablet_height_mm:g} mm)"
            ),
            font=("Segoe UI", 8),
            fg="#71717a",
            bg="#18181b",
        )
        self.lbl_tablet.pack(pady=(0, 1))

        self.lbl_display = tk.Label(
            self.root,
            text=(
                f"Display: {self.display_name} "
                f"({self.display_width_px}×{self.display_height_px} @ "
                f"{self.display_offset_x}, {self.display_offset_y})"
            ),
            font=("Segoe UI", 8),
            fg="#71717a",
            bg="#18181b",
        )
        self.lbl_display.pack(pady=(0, 2))

        self.lbl_source = tk.Label(
            self.root,
            text="Input: Waiting for pen...",
            font=("Segoe UI", 8, "bold"),
            fg="#71717a",
            bg="#18181b",
        )
        self.lbl_source.pack(pady=(0, 8))

        # Unit Selector Bar (km / m / mm)
        unit_frame = tk.Frame(self.root, bg="#18181b")
        unit_frame.pack(pady=(0, 10))

        for u in UNITS:
            btn = tk.Button(
                unit_frame,
                text=u,
                width=5,
                font=("Segoe UI", 8, "bold"),
                command=lambda chosen=u: self._set_unit(chosen),
                relief="flat",
                cursor="hand2",
            )
            btn.pack(side="left", padx=3)
            self.unit_buttons[u] = btn

        self._style_unit_buttons()

        # Lifetime Total
        tk.Label(
            self.root,
            text="Lifetime Total (Hover + Draw)",
            font=("Segoe UI", 9),
            fg="#71717a",
            bg="#18181b",
        ).pack()
        self.lbl_total = tk.Label(
            self.root,
            text="0.00 km",
            font=("Consolas", 15, "bold"),
            fg="#f4f4f5",
            bg="#18181b",
        )
        self.lbl_total.pack(padx=16, pady=5)

        # Lifetime Drawing Only
        tk.Label(
            self.root,
            text="Lifetime Pen-Down (Drawing Only)",
            font=("Segoe UI", 9),
            fg="#71717a",
            bg="#18181b",
        ).pack()
        self.lbl_draw = tk.Label(
            self.root,
            text="0.00 km",
            font=("Consolas", 15, "bold"),
            fg="#38bdf8",
            bg="#18181b",
        )
        self.lbl_draw.pack(padx=16, pady=5)

        # Lifetime Hover Only
        tk.Label(
            self.root,
            text="Lifetime Hover Only",
            font=("Segoe UI", 9),
            fg="#71717a",
            bg="#18181b",
        ).pack()
        self.lbl_hover = tk.Label(
            self.root,
            text="0.00 km",
            font=("Consolas", 15, "bold"),
            fg="#a78bfa",
            bg="#18181b",
        )
        self.lbl_hover.pack(padx=16, pady=5)

        # Current Session
        self.lbl_session = tk.Label(
            self.root,
            text="Session: 0.00 km drawn  |  0.00 km hover  |  0.00 km total",
            font=("Segoe UI", 9),
            fg="#a1a1aa",
            bg="#18181b",
        )
        self.lbl_session.pack(padx=14, pady=(6, 10))

        # Controls (2x2 symmetrical grid)
        btn_frame = tk.Frame(self.root, bg="#18181b")
        btn_frame.pack(padx=14, pady=(0, 14))

        tk.Button(
            btn_frame,
            text="Stats",
            width=12,
            command=self._open_stats,
            bg="#27272a",
            fg="#38bdf8",
            relief="flat",
            cursor="hand2",
        ).grid(row=0, column=0, padx=4, pady=3)

        tk.Button(
            btn_frame,
            text="Settings",
            width=12,
            command=self._open_settings,
            bg="#27272a",
            fg="#fbbf24",
            relief="flat",
            cursor="hand2",
        ).grid(row=0, column=1, padx=4, pady=3)

        self.btn_save = tk.Button(
            btn_frame,
            text="Save",
            width=12,
            command=self._force_save,
            bg="#27272a",
            fg="#4ade80",
            relief="flat",
            cursor="hand2",
        )
        self.btn_save.grid(row=1, column=0, padx=4, pady=3)

        self.btn_pause = tk.Button(
            btn_frame,
            text="Pause",
            width=12,
            command=self._toggle_pause,
            bg="#27272a",
            fg="#f4f4f5",
            relief="flat",
            cursor="hand2",
        )
        self.btn_pause.grid(row=1, column=1, padx=4, pady=3)

    def _open_stats(self):
        if self.stats_window is not None and self.stats_window.win.winfo_exists():
            self.stats_window.win.deiconify()
            _position_window_on_parent(
                self.stats_window.win, self.root, width=840, height=530
            )
            self.stats_window.win.lift()
            self.stats_window.win.focus_force()
            self.stats_window.redraw()
            return
        self.stats_window = WacomStatsWindow(self)

    def _style_unit_buttons(self):
        for u, btn in self.unit_buttons.items():
            if u == self.unit:
                btn.config(bg="#3f3f46", fg="#38bdf8")
            else:
                btn.config(bg="#27272a", fg="#a1a1aa")

    def _set_unit(self, chosen_unit: str):
        if chosen_unit not in UNITS:
            return
        with self.lock:
            self.unit = chosen_unit
        self._style_unit_buttons()
        self._refresh_display()
        if self.stats_window is not None and self.stats_window.win.winfo_exists():
            self.stats_window.redraw()
        self._save_data()

    def _force_save(self):
        ok = self._save_data()
        if self._save_btn_after_id is not None:
            self.root.after_cancel(self._save_btn_after_id)
        if ok:
            self.btn_save.config(text="Saved!", fg="#4ade80")
        else:
            self.btn_save.config(text="Error", fg="#f87171")
        self._save_btn_after_id = self.root.after(
            1200, lambda: self.btn_save.config(text="Save", fg="#4ade80")
        )

    def _toggle_pause(self):
        with self.lock:
            self.paused = not self.paused
            self.last_hid_pos_mm = None
            self.last_cursor_pos = None
            paused = self.paused
        self.btn_pause.config(text="Resume" if paused else "Pause")
        if self.tray_icon is not None:
            try:
                self.tray_icon.update_menu()
            except Exception:
                pass

    def _execute_reset_counters(self):
        """Clears all distance counters and daily history, refreshes views, and persists."""
        with self.lock:
            self.total_mm = 0.0
            self.draw_mm = 0.0
            self.hover_mm = 0.0
            self.session_total_mm = 0.0
            self.session_draw_mm = 0.0
            self.session_hover_mm = 0.0
            self.daily.clear()
        self._refresh_display()
        if self.stats_window is not None and self.stats_window.win.winfo_exists():
            self.stats_window.redraw()
        self._save_data()

    def _refresh_display(self):
        now = time.monotonic()
        with self.lock:
            t_mm = self.total_mm
            d_mm = self.draw_mm
            h_mm = self.hover_mm
            st_mm = self.session_total_mm
            sd_mm = self.session_draw_mm
            sh_mm = self.session_hover_mm
            hid_active = (now - self.last_hid_packet_time) < 0.25
            wacom_any = (
                now - self.last_wacom_any_time
            ) < 0.25 and not self.physical_mouse_active
            paused = self.paused

        if self.tray_icon is not None:
            state_tag = " (Paused)" if paused else ""
            self.tray_icon.title = (
                f"Wacom Tracker{state_tag}\n"
                f"Drawn: {self._format_lifetime(d_mm)} | Total: {self._format_lifetime(t_mm)}"
            )

        if not self.root.winfo_viewable():
            return

        if hid_active:
            self.lbl_source.config(text="Input: Wacom HID (Direct)", fg="#4ade80")
        elif wacom_any:
            self.lbl_source.config(text="Input: Wacom Pen (Cursor Mode)", fg="#4ade80")
        else:
            self.lbl_source.config(text="Input: Mouse / Idle (Ignored)", fg="#71717a")

        self.lbl_total.config(text=self._format_lifetime(t_mm))
        self.lbl_draw.config(text=self._format_lifetime(d_mm))
        self.lbl_hover.config(text=self._format_lifetime(h_mm))
        self.lbl_session.config(
            text=(
                f"Session: {self._format_session_val(sd_mm)} drawn  |  "
                f"{self._format_session_val(sh_mm)} hover  |  "
                f"{self._format_session_val(st_mm)} total"
            )
        )

    def _update_loop(self):
        if self._is_quitting:
            return

        self._refresh_display()
        if (
            self.stats_window is not None
            and self.stats_window.win.winfo_exists()
            and self.stats_window.win.winfo_viewable()
        ):
            self.stats_window.redraw()

        # Auto-save every ~5 seconds
        self.ticks_since_save += 1
        if self.ticks_since_save >= 25:
            self.ticks_since_save = 0
            self._save_data()

        self.root.after(200, self._update_loop)

    def _quit_app(self):
        """Stops background listeners and tray icon, saves data, and exits cleanly."""
        if self._is_quitting:
            return
        self._is_quitting = True
        self._save_data()
        try:
            self.listener.stop()
        except Exception:
            pass
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        self._save_data()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = WacomTrackerApp(root)
    root.mainloop()