import os
import sys
from pathlib import Path
from PyQt6.QtCore import QUrl


def get_resource_path(*parts: str) -> Path:
    """
    Get the absolute path to a resource file, handling PyInstaller bundles.
    
    In development: returns path relative to project root
    In PyInstaller bundle: returns path inside _MEIPASS/_internal
    
    Args:
        *parts: Path components relative to project/bundle root
                e.g. get_resource_path('resources', 'logos', 'patreon.svg')
    
    Returns:
        Absolute Path to the resource
    """
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller bundle - resources are in _MEIPASS (which points to _internal)
        base = Path(sys._MEIPASS)
    else:
        # Development - use project root (parent of src/)
        base = Path(__file__).parent.parent.parent
    
    return base.joinpath(*parts)


def standardize_url_path(url_or_path: str):
    """
    Converts a URL (http/https/file://) or a local path string into a standardized 
    format (str for network URLs, Path object for local files).
    """
    if not url_or_path:
        return ""

    # Handle standard HTTP/HTTPS URLs (return as a clean string)
    if url_or_path.startswith('http'):
        return url_or_path.strip()

    # Handle file:// URLs using QUrl for robust conversion to a local path string
    if url_or_path.startswith('file://'):
        local_file_path_str = QUrl(url_or_path).toLocalFile()
        return Path(local_file_path_str).resolve() # Return as Path object

    # Handle standard local paths passed as strings
    local_path = Path(url_or_path)
    if local_path.is_absolute():
        return local_path.resolve() # Return as Path object
    
    # Handle relative paths by resolving them to the current working directory
    return (Path(os.getcwd()) / local_path).resolve() # Return as Path object


def apply_windows_dark_mode(widget):
    """
    Apply Windows dark mode to a widget's title bar (Windows 10 1809+ / Windows 11).
    Should be called after the widget is created but before or after show().
    
    Args:
        widget: A QWidget with a window handle (QMainWindow, QDialog, etc.)
    """
    if sys.platform != "win32":
        return
    
    try:
        import ctypes
        hwnd = int(widget.winId())
        dwmapi = ctypes.windll.dwmapi
        
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Windows 10 20H1+)
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        value = ctypes.c_int(1)  # 1 = dark mode
        result = dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value),
            ctypes.sizeof(value)
        )
        if result != 0:
            # Try older attribute for Windows 10 1809-1909
            DWMWA_USE_IMMERSIVE_DARK_MODE = 19
            dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(value),
                ctypes.sizeof(value)
            )
    except Exception:
        pass  # Silently fail on non-Windows or older Windows
