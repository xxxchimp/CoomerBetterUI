# Coomer BetterUI

Coomer BetterUI is a desktop application built with PyQt6. It provides a modern, native UI for browsing Coomer/Kemono content with fast media previews, downloads, and local caching.

**Features**
- Browse creators and posts in a responsive grid view with a detail view.
- Search and filter by service, tags, content type (images/videos), and video duration.
- Built-in image viewer and MPV-backed video playback/preview when `libmpv-2.dll` is available.
- Download queue with per-file progress, pause/resume, retry, and batch downloads.
- Local caching for thumbnails, media, HTTP responses, and a range-proxy cache for smoother streaming.
- Optional proxy support with cookie persistence.

**Requirements**
- Python 3 and pip.
- Windows 10/11 is the primary target. Other platforms may work but are not tested.
- Optional: `ffmpeg` and `ffprobe` in PATH for video thumbnails and metadata extraction.
- Optional: `libmpv-2.dll` for video playback (bundled in `mpv/` or provided by your environment).

**Quick Start (Dev)**
1. `python -m venv venv`
2. `venv\Scripts\activate`
3. `pip install -r requirements.txt`
4. `python build.py --dev`

**Build**
1. `python build.py`
2. Output: `dist/CoomerBetterUI/CoomerBetterUI.exe`

**Data Locations**
- SQLite database and logs: `%LOCALAPPDATA%\CoomerBetterUI\`
- Caches: `~\.coomer-betterui\` (media, thumbnails, HTTP, range cache)

**Notes**
- The app talks to Coomer/Kemono APIs. Use responsibly and respect site terms.
