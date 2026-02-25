"""
UI Utility Functions

Centralized utility functions used across the UI layer.
Eliminates code duplication for common string, HTML, and path operations.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Optional


def strip_html(text: str) -> str:
    """
    Remove HTML tags and unescape HTML entities from text.

    Args:
        text: Input text that may contain HTML

    Returns:
        Plain text with HTML tags removed and entities unescaped

    Examples:
        >>> strip_html("Hello &lt;b&gt;world&lt;/b&gt;!")
        "Hello world!"
        >>> strip_html("<p>Test</p>")
        "Test"
    """
    if not text:
        return ""
    # First unescape HTML entities, then remove tags
    unescaped = html.unescape(text)
    return re.sub(r"<[^>]+>", "", unescaped)


def sanitize_path_segment(value: str, default: str) -> str:
    """
    Sanitize a string to be filesystem-safe for use as directory/file name.

    Removes filesystem-invalid characters and normalizes whitespace.
    Falls back to default if input is empty after cleaning.

    Args:
        value: Raw string to sanitize
        default: Fallback value if sanitization results in empty string

    Returns:
        Filesystem-safe string suitable for directory/file names

    Examples:
        >>> sanitize_path_segment("User: Name<Test>", "default")
        "User_ Name_Test_"
        >>> sanitize_path_segment("  ", "fallback")
        "fallback"
    """
    cleaned = (value or "").strip()
    if not cleaned:
        return default

    # Normalize whitespace
    cleaned = re.sub(r"\s+", " ", cleaned)

    # Replace filesystem-invalid characters with underscore
    # Invalid: < > : " / \ | ? *
    #cleaned = re.sub(r'[<>:"/\\|?*.]', "_", cleaned)

    # Replaces everything EXCEPT letters, numbers, underscores, and dashes
    cleaned = re.sub(r'[^\w\s-]', '_', cleaned)

    # Convert to ASCII-safe (removes non-ASCII characters)
    cleaned = cleaned.encode("ascii", "ignore").decode()

    return cleaned if cleaned else default


# Windows reserved filenames (case-insensitive)
_WINDOWS_RESERVED_NAMES = frozenset([
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
])


def sanitize_filename(name: str, default: str) -> str:
    """
    Sanitize a filename while preserving the file extension.

    Only blocks characters that are invalid on Windows filesystems:
    < > : " / \\ | ? *

    Also handles:
    - Windows reserved filenames (CON, PRN, AUX, NUL, COM1-9, LPT1-9)
    - Trailing periods and spaces (not allowed on Windows)

    Args:
        name: Original filename (may include path)
        default: Fallback filename if sanitization fails

    Returns:
        Sanitized filename safe for Windows filesystems

    Examples:
        >>> sanitize_filename("test<file>.jpg", "default.jpg")
        "test_file_.jpg"
        >>> sanitize_filename("file (1) [2024].png", "default.png")
        "file (1) [2024].png"
        >>> sanitize_filename("CON.txt", "default.txt")
        "_CON.txt"
    """
    # Extract filename from path
    raw = Path(name).name if name else ""
    if not raw:
        return default

    # Split extension
    suffix = Path(raw).suffix
    stem = raw[: -len(suffix)] if suffix else raw

    if not stem.strip():
        stem = Path(default).stem

    # Replace only Windows-invalid characters: < > : " / \ | ? *
    clean_stem = re.sub(r'[<>:"/\\|?*]', '_', stem)

    # Remove trailing periods and spaces (Windows doesn't allow them)
    clean_stem = clean_stem.rstrip('. ')

    # Handle empty stem after cleaning
    if not clean_stem:
        clean_stem = Path(default).stem

    # Check for Windows reserved names (case-insensitive)
    if clean_stem.upper() in _WINDOWS_RESERVED_NAMES:
        clean_stem = f"_{clean_stem}"

    # Preserve original extension or use default's extension
    final_suffix = suffix if suffix else Path(default).suffix

    # Also sanitize extension (remove invalid chars)
    if final_suffix:
        final_suffix = re.sub(r'[<>:"/\\|?*]', '_', final_suffix)

    return f"{clean_stem}{final_suffix}"


def truncate_text(text: str, max_length: int, ellipsis: str = "...") -> str:
    """
    Truncate text to maximum length with ellipsis.

    Args:
        text: Text to truncate
        max_length: Maximum length including ellipsis
        ellipsis: String to append when truncated (default: "...")

    Returns:
        Truncated text with ellipsis if needed

    Examples:
        >>> truncate_text("Hello world", 8)
        "Hello..."
        >>> truncate_text("Short", 10)
        "Short"
    """
    if not text:
        return ""

    if len(text) <= max_length:
        return text

    # Reserve space for ellipsis
    return text[: max_length - len(ellipsis)] + ellipsis


def format_file_size(size_bytes: int) -> str:
    """
    Format byte size as human-readable string.

    Args:
        size_bytes: File size in bytes

    Returns:
        Human-readable size string (e.g., "1.5 MB")

    Examples:
        >>> format_file_size(1024)
        "1.0 KB"
        >>> format_file_size(1536000)
        "1.5 MB"
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024**2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024**3:
        return f"{size_bytes / 1024**2:.1f} MB"
    else:
        return f"{size_bytes / 1024**3:.1f} GB"


def normalize_whitespace(text: str) -> str:
    """
    Normalize all whitespace sequences to single spaces.

    Args:
        text: Text with potentially irregular whitespace

    Returns:
        Text with normalized whitespace

    Examples:
        >>> normalize_whitespace("Hello    world\\n\\ntest")
        "Hello world test"
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def get_file_extension(path_or_url: str) -> Optional[str]:
    """
    Extract file extension from path or URL.

    Args:
        path_or_url: File path or URL string

    Returns:
        Lowercase extension including dot (e.g., ".jpg") or None

    Examples:
        >>> get_file_extension("image.JPG")
        ".jpg"
        >>> get_file_extension("https://example.com/file.png?query=1")
        ".png"
    """
    if not path_or_url:
        return None

    # Handle URLs with query parameters
    path_part = path_or_url.split("?")[0]
    ext = Path(path_part).suffix.lower()

    return ext if ext else None
