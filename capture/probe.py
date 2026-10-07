import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass
import psutil

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
kernel32.GetTickCount.restype = wt.DWORD


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


@dataclass
class WindowInfo:
    app: str | None
    title: str | None
class WindowsProbe:
    # def get_active_windows(self) -> list[WindowInfo]:
    #     """Return 1 or 2 active windows.

    #     v1: returns just the foreground window (length 1).
    #     v2: when the foreground window's bounds look roughly half-screen
    #     width, run EnumWindows and return the other matching allowlisted
    #     window too. That check is deliberately not implemented yet.
    #     """
    #     app, title = self._get_foreground()
    #     if app is None and title is None:
    #         return []
    #     return [WindowInfo(app=app, title=title)]
    
    def get_foreground(self) -> tuple[str | None, str | None]:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None, None

        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)

        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            app = psutil.Process(pid.value).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            app = None

        return app, buf.value or None

    def get_idle_seconds(self) -> float:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0

        # GetTickCount wraps every ~49 days, so mask the difference
        return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0

