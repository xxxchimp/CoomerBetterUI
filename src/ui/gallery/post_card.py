"""
PostCard widget for displaying individual post previews.

Extracted from native_widgets.py for better maintainability.
Uses theme.py for dynamic styling and dark_theme_pro.qss for static widget styles.
"""
from PyQt6.QtWidgets import (QWidget, QLabel, QVBoxLayout, QHBoxLayout,
                             QPushButton, QFrame, QSizePolicy, QScrollArea)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QEvent, QRect, QRectF
from PyQt6.QtGui import QPixmap, QPainter, QColor, QIcon, QPainterPath, QCursor
import logging
import random
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from typing import Callable, List, Optional
import qtawesome as qta

from src.ui.widgets.rounded_effect import RoundedCornerGraphicsEffect
from src.ui.widgets.spinner_widget import SpinnerWidget
from src.ui.common.utils import strip_html, normalize_whitespace, truncate_text
from src.ui.images.image_utils import scale_and_crop_pixmap, scale_pixmap_to_fit
from src.ui.creators.creator_utils import resolve_creator_name
from src.ui.images.async_image_widgets import AsyncImageLabel, ImageLoadRequest
from src.ui.common.theme import Colors, Fonts, Spacing
from src.core.media_manager import MediaManager
from src.core.dto.post import PostDTO
from src.core.dto.file import FileDTO
from src.utils.file_utils import get_resource_path

logger = logging.getLogger(__name__)

# UI tuning constants
COLLAGE_GAP_RATIO = 0.02      # fraction of min(width,height) used as gap between collage images
COLLAGE_CORNER_RADIUS = float(Spacing.RADIUS_XXL)  # pixels for rounded corners on thumbnails
BADGE_PADDING_H = 3           # horizontal padding in px for badge
BADGE_PADDING_V = 2           # vertical padding in px for badge
BADGE_FONT_SIZE = Fonts.SIZE_XS  # point size for badge font
BADGE_POSITION = 'top-right'  # 'bottom-left' or 'top-right'
BROKEN_MEDIA_ICON_NAME = "fa5s.exclamation-triangle"
OVERSIZED_ICON_NAME = "fa5s.weight-hanging"
TIMEOUT_ICON_NAME = "fa5s.hourglass-end"
CONTENT_ONLY_ICON_NAME = "fa5s.newspaper"
BROKEN_MEDIA_ICON_COLOR = "#ffffff"  # White (opacity applied when drawing)
BROKEN_MEDIA_ICON_SIZE = Spacing.ICON_XL + 18  # 50px
POST_TITLE_MAX_CHARS = 45  # Max chars for post title display

# Placeholder background color palette (randomly selected to break visual monotony)
PLACEHOLDER_COLORS = [
    (247, 178, 183),  # cherry-blossom
    (247, 113, 125),  # light-coral
    (222, 99, 154),   # sweet-peony
    (127, 41, 130),   # dark-magenta
    (22, 0, 30),      # midnight-violet
]

# File extensions for preview detection
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mov', '.avi', '.mkv', '.m4v', '.flv'}


def _is_image_file(file_dto: FileDTO) -> bool:
    """Return True if file looks like an image."""
    if getattr(file_dto, "is_image", False):
        return True
    mime = (getattr(file_dto, "mime", None) or "").lower()
    if mime.startswith("image/"):
        return True
    path = getattr(file_dto, "path", "") or ""
    name = getattr(file_dto, "name", "") or ""
    ext = _last_suffix(path or name)
    return ext in IMAGE_EXTENSIONS


def _is_video_file(file_dto: FileDTO) -> bool:
    """Return True if file looks like a video."""
    if getattr(file_dto, "is_video", False):
        return True
    mime = (getattr(file_dto, "mime", None) or "").lower()
    if mime.startswith("video/"):
        return True
    path = getattr(file_dto, "path", "") or ""
    name = getattr(file_dto, "name", "") or ""
    ext = _last_suffix(path or name)
    return ext in VIDEO_EXTENSIONS

def _subprocess_kwargs() -> dict:
    kwargs = {}
    if sys.platform == 'win32':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return kwargs

def _probe_url_duration(url: str) -> Optional[float]:
    """Probe a remote video URL for its duration using ffprobe. Returns seconds or None."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        url,
    ]
    logger.debug(f"[duration-probe] probing: {url[:120]}")
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,
            timeout=15,
            **_subprocess_kwargs(),
        )
    except FileNotFoundError:
        logger.warning("[duration-probe] ffprobe not found on PATH")
        return None
    except subprocess.TimeoutExpired:
        logger.debug(f"[duration-probe] timeout for {url[:80]}")
        return None
    except subprocess.CalledProcessError as e:
        logger.debug(f"[duration-probe] ffprobe error: {e.stderr[:200] if e.stderr else ''}")
        return None
    raw = proc.stdout.strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.debug(f"[duration-probe] could not parse: '{raw}'")
        return None
    if value > 0:
        logger.debug(f"[duration-probe] got duration: {value:.1f}s")
        return value
    return None


class _DurationCache:
    """
    Thin wrapper around DatabaseManager for video duration lookups.

    Uses the existing ``media_url_map`` → ``media_content_cache`` tables so
    durations discovered by ffprobe (or by the thumbnail pipeline) are
    persisted across sessions.
    """
    _instance: Optional['_DurationCache'] = None

    def __init__(self):
        from src.core.database import DatabaseManager
        self._db = DatabaseManager()  # uses default app db path
        self._db.connect()

    @classmethod
    def instance(cls) -> '_DurationCache':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ---- public API (call from main thread only) ----

    def get(self, url: str) -> Optional[float]:
        """Return cached duration in seconds, or *None* on miss."""
        try:
            content_id = self._db.get_content_id_for_url(url)
            if not content_id:
                return None
            row = self._db.get_cached_content(content_id)
            if row and row.get('duration'):
                return float(row['duration'])
        except Exception as e:
            logger.debug(f"[duration-cache] lookup error: {e}")
        return None

    def store(self, url: str, duration: float) -> None:
        """Persist a probed duration.  Reuses existing content_id when one
        is already mapped for *url*, otherwise creates one from SHA-256."""
        import hashlib
        try:
            content_id = self._db.get_content_id_for_url(url)
            if content_id:
                # Update existing row — preserve other fields
                row = self._db.get_cached_content(content_id) or {}
                self._db.cache_media_content(
                    content_id,
                    media_type=row.get('media_type', 'video'),
                    content_hash=row.get('content_hash'),
                    etag=row.get('etag'),
                    last_modified=row.get('last_modified'),
                    content_length=row.get('content_length'),
                    duration=duration,
                    width=row.get('width'),
                    height=row.get('height'),
                    codec=row.get('codec'),
                    thumbnail_path=row.get('thumbnail_path'),
                )
            else:
                content_id = hashlib.sha256(url.encode('utf-8')).hexdigest()
                self._db.map_media_url(url, content_id)
                self._db.cache_media_content(
                    content_id,
                    media_type='video',
                    duration=duration,
                )
            logger.debug(f"[duration-cache] stored {duration:.1f}s for {url[:80]}")
        except Exception as e:
            logger.debug(f"[duration-cache] store error: {e}")


class _DurationProbeQueue:
    """
    Centralized queue that probes video durations one at a time.

    All Qt / DB operations stay on the main thread via a polling QTimer.
    A background thread runs the blocking ffprobe subprocess.

    Durations are cached in the app SQLite database so that subsequent
    sessions can skip the ffprobe entirely.
    """
    _instance: Optional['_DurationProbeQueue'] = None

    def __init__(self):
        from collections import deque
        from PyQt6.QtCore import QTimer
        self._queue: deque = deque()
        self._result = None       # (card_ref, duration, url) from last probe
        self._busy = False        # True while a probe thread is running
        self._cache = _DurationCache.instance()

        # Main-thread timer that polls for results and dispatches work
        self._timer = QTimer()
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._tick)

    @classmethod
    def instance(cls) -> '_DurationProbeQueue':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def enqueue(self, card_ref, url: str):
        """Add a probe request. card_ref is a weakref to a PostCard."""
        self._queue.append((card_ref, url))
        if not self._timer.isActive():
            self._tick()

    def _tick(self):
        """Called on main thread. Check result, start next probe."""
        # Collect result from finished probe
        if self._result is not None:
            card_ref, dur, url = self._result
            self._result = None
            self._busy = False
            # Persist to DB (main thread — thread-safe)
            if dur and dur > 0:
                self._cache.store(url, dur)
            card = card_ref() if card_ref else None
            logger.debug(f"[probe-queue] result arrived: dur={dur}, card_alive={card is not None}")
            if card is not None and dur and dur > 0:
                try:
                    card._on_duration_probed(dur)
                except Exception as e:
                    logger.warning(f"[probe-queue] callback error: {e}")

        # If a probe is running, wait for it
        if self._busy:
            return

        # Pick next live card from queue
        while self._queue:
            card_ref, url = self._queue.popleft()
            card = card_ref()
            if card is None:
                continue

            # Check DB cache first (main thread — no thread-safety issue)
            cached = self._cache.get(url)
            if cached is not None and cached > 0:
                logger.debug(f"[probe-queue] cache hit {cached:.1f}s for {url[:80]}")
                try:
                    card._on_duration_probed(cached)
                except Exception:
                    pass
                continue  # process next item immediately

            # Cache miss — start ffprobe in background thread
            self._busy = True
            logger.debug(f"[probe-queue] cache miss, probing {url[:80]}")
            import threading
            t = threading.Thread(
                target=self._run_probe, args=(card_ref, url), daemon=True
            )
            t.start()
            if not self._timer.isActive():
                self._timer.start()
            return

        # Nothing left to do
        self._timer.stop()

    def _run_probe(self, card_ref, url: str):
        """Runs in background thread — only calls ffprobe, no Qt / DB."""
        dur = _probe_url_duration(url)
        self._result = (card_ref, dur, url)


def _get_random_placeholder_color() -> QColor:
    """Get a random color from the placeholder palette."""
    r, g, b = random.choice(PLACEHOLDER_COLORS)
    return QColor(r, g, b)


def _get_icon_for_error(error: str) -> str:
    """Determine which icon to use based on error message."""
    error_lower = error.lower()
    if 'size limit' in error_lower or 'too large' in error_lower or 'oversized' in error_lower:
        return OVERSIZED_ICON_NAME
    elif 'timeout' in error_lower or 'timed out' in error_lower:
        return TIMEOUT_ICON_NAME
    else:
        return BROKEN_MEDIA_ICON_NAME


def _broken_media_icon_pixmap(icon_name: str = BROKEN_MEDIA_ICON_NAME) -> QPixmap:
    """Get the broken media icon pixmap."""
    return qta.icon(icon_name, color=BROKEN_MEDIA_ICON_COLOR).pixmap(
        BROKEN_MEDIA_ICON_SIZE, BROKEN_MEDIA_ICON_SIZE
    )


def _render_broken_media_placeholder(
    width: int,
    height: int,
    bg_color: Optional[str] = Colors.BG_TERTIARY,
    avatar_pixmap: Optional[QPixmap] = None,
    icon_name: str = BROKEN_MEDIA_ICON_NAME,
) -> QPixmap:
    """Render a placeholder pixmap with broken media icon."""
    w = max(1, int(width))
    h = max(1, int(height))
    placeholder = QPixmap(w, h)
    placeholder.fill(QColor("transparent"))
    
    painter = QPainter(placeholder)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    
    inner_margin = 0
    inner_rect = placeholder.rect().adjusted(inner_margin, inner_margin, -inner_margin, -inner_margin)
    
    # Draw avatar background if available
    if avatar_pixmap and not avatar_pixmap.isNull():
        scaled_avatar = avatar_pixmap.scaled(
            inner_rect.width(), inner_rect.height(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation
        )
        painter.setOpacity(0.5)
        painter.drawPixmap(inner_rect, scaled_avatar)
        painter.setOpacity(1.0)
    
    # Draw semi-transparent background with random color
    if bg_color:
        bg_qcolor = QColor(bg_color)
    else:
        bg_qcolor = _get_random_placeholder_color()
    
    if avatar_pixmap and not avatar_pixmap.isNull():
        bg_qcolor.setAlpha(100)  # 40% opacity to let avatar show through
    painter.setBrush(bg_qcolor)
    painter.setPen(QColor(Colors.BORDER_LIGHT))
    painter.drawRoundedRect(inner_rect, Spacing.RADIUS_LG, Spacing.RADIUS_LG)
    
    # Draw icon on top with opacity
    icon = _broken_media_icon_pixmap(icon_name)
    painter.setOpacity(0.7)  # 70% opacity for icon
    painter.drawPixmap(
        max(0, (w - icon.width()) // 2),
        max(0, (h - icon.height()) // 2),
        icon,
    )
    painter.end()
    return placeholder


def _build_media_url(platform: str, file_path: str) -> str:
    """Build full media URL from platform and file path."""
    return MediaManager.build_media_url(platform, file_path)


def _get_qta_icon(name: str) -> Optional[QIcon]:
    """Get QtAwesome icon by name, returning None on failure."""
    try:
        return qta.icon(name)
    except Exception:
        return None


def _last_suffix(value: str) -> str:
    """Extract file extension from path or URL."""
    if not value:
        return ""
    try:
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https", "file"}:
            value = parsed.path
    except Exception:
        pass
    return Path(value).suffix.lower()


def _build_post_title(post: PostDTO) -> str:
    """Return a display title, falling back to a content snippet when needed."""
    for candidate in (post.title, post.substring, post.content):
        if not candidate:
            continue
        cleaned = normalize_whitespace(strip_html(candidate))
        if cleaned:
            return truncate_text(cleaned, POST_TITLE_MAX_CHARS, "...")
    return ""


class PostCard(QFrame):
    """
    Individual post card widget displaying post preview.

    Shows thumbnail (single image or collage), title, creator info,
    and file type badges. Supports selection for batch operations.

    Signals:
        clicked(post_data): Emitted when card is clicked
        creator_clicked(creator_data): Emitted when creator label is clicked
        selected_changed(post_data, selected): Emitted when selection changes
    """

    clicked = pyqtSignal(object)  # Emits post data when clicked
    creator_clicked = pyqtSignal(object)  # Emits creator data dict when clicked
    selected_changed = pyqtSignal(object, bool)  # post_data, selected

    def __init__(
        self,
        post_data: PostDTO,
        platform: str = 'coomer',
        parent=None,
        *,
        creator_lookup: Optional[Callable[[str, str, str], Optional[str]]] = None,
        creators_manager = None,
    ):
        """
        Initialize post card.

        Args:
            post_data: PostDTO containing post information
            platform: Platform identifier (coomer/kemono)
            parent: Parent widget
            creator_lookup: Optional callback to resolve creator names
            creators_manager: Optional CreatorsManager for avatar loading
        """
        super().__init__(parent)
        self.platform = platform
        self._creator_lookup = creator_lookup
        self._creators_manager = creators_manager

        self.post_data = post_data
        self.thumbnail_pixmap = None
        self._qta = None
        self.movie = None
        self.selected = False
        self._collage_children = []
        self._preview_manager = None  # Video preview manager (set externally)

        self._creator_display_name = self._resolve_creator_display()
        self._setup_ui()
        self._load_thumbnail()

    def _setup_ui(self):
        """Setup card UI."""
        self.setObjectName("postCard")
        self.setFrameShape(QFrame.Shape.Box)
        self.setFixedSize(Spacing.CARD_WIDTH - Spacing.LG, Spacing.CARD_HEIGHT + Spacing.XXXL)  # 220, 320
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setGraphicsEffect(RoundedCornerGraphicsEffect(Spacing.RADIUS_XL, self))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Thumbnail area (async image loading)
        self.thumbnail_label = AsyncImageLabel(self)
        self.thumbnail_label.setObjectName("postThumbnail")
        self.card_width = Spacing.CARD_WIDTH
        # Subtract border width * 2 (left + right) to prevent thumbnail overflow
        thumb_width = self.card_width - (Spacing.CARD_BORDER * 2)
        self.thumbnail_label.setFixedSize(thumb_width, Spacing.THUMBNAIL_HEIGHT)
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setScaledContents(False)
        self.thumbnail_label.installEventFilter(self)
        self.thumbnail_label.setGraphicsEffect(RoundedCornerGraphicsEffect(Spacing.RADIUS_XL, self.thumbnail_label))
        self.thumbnail_spinner = SpinnerWidget(self.thumbnail_label, size=BROKEN_MEDIA_ICON_SIZE, color=Colors.SPINNER)
        self._position_thumbnail_spinner()

        # Overlay, badge, select button live on top of thumbnail
        self.overlay = QLabel(self.thumbnail_label)
        self.overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.overlay.setStyleSheet(f'background: transparent; color: {Colors.TEXT_WHITE};')
        self.overlay.setVisible(False)

        # Badge container
        self.badge_container = QPushButton(self.thumbnail_label)
        self.badge_container.setObjectName("badgeContainer")
        self.badge_container.setFlat(True)
        self.badge_container.setVisible(False)
        self.badge_container.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.badge_container.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.badge_layout = QHBoxLayout(self.badge_container)
        self.badge_layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.badge_layout.setContentsMargins(0, 0, 0, 0)
        self.badge_layout.setSpacing(4)

        # Duration label (bottom-left of thumbnail)
        self.duration_label = QLabel(self.thumbnail_label)
        self.duration_label.setObjectName("durationLabel")
        self.duration_label.setVisible(False)
        self.duration_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.duration_label.setStyleSheet(
            "background-color: rgba(0, 0, 0, 0.80);"
            "border-radius: 4px;"
            "padding: 2px 2px;"
            "color: #ffffff;"
            "font-size: 11px;"
            "font-weight: 600;"
        )

        self.select_btn = QPushButton(self.thumbnail_label)
        self.select_btn.setObjectName("selectButton")
        self.select_btn.setCheckable(True)
        self.select_btn.setFixedSize(Spacing.BTN_SM, Spacing.BTN_SM)
        self.select_btn.setText('')
        self.select_btn.toggled.connect(self._on_select_toggled)

        layout.addWidget(self.thumbnail_label)

        # Info widget below thumbnail
        info_widget = QWidget()
        info_widget.setObjectName("postInfoLayout")
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(Spacing.MD, 0, Spacing.MD, 0)
        info_layout.setAlignment(Qt.AlignmentFlag.AlignBottom)
        info_layout.setSpacing(0)

        title = _build_post_title(self.post_data)
        title_label = QLabel(title)
        title_label.setObjectName("postTitle")
        title_label.setWordWrap(True)
        title_label.setContentsMargins(0, Spacing.MD, 0, Spacing.XL)
        info_layout.addWidget(title_label)
        

        # Creator/service row with service badge
        creator_row = QWidget()
        creator_layout = QHBoxLayout(creator_row)
        creator_layout.setContentsMargins(0, 0, 0, 0)
        creator_layout.setSpacing(Spacing.XS)

        service_label = QLabel(
            f"{self._creator_display_name} • {self.post_data.service or ''}"
        )
        service_label.setObjectName("postMeta")
        service_label.setCursor(Qt.CursorShape.PointingHandCursor)
        creator_id = self.post_data.user_id or ""
        if creator_id:
            service_label.setToolTip(f"{creator_id} • {self.post_data.service or ''}")
        service_label.mousePressEvent = self._on_creator_label_clicked
        creator_layout.addWidget(service_label)
        self._creator_label = service_label

        creator_layout.addStretch()

        # Service icon badge
        service_icon_label = QLabel()
        service_icon_label.setFixedSize(Spacing.SERVICE_ICON, Spacing.SERVICE_ICON)
        service_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        service_icon = self._get_service_icon(self.post_data.service or '')
        if not service_icon.isNull():
            pixmap = service_icon.pixmap(Spacing.SERVICE_ICON, Spacing.SERVICE_ICON)
            scaled_pixmap = scale_pixmap_to_fit(pixmap, (Spacing.SERVICE_ICON, Spacing.SERVICE_ICON))
            service_icon_label.setPixmap(scaled_pixmap)
        creator_layout.addWidget(service_icon_label)

        info_layout.addWidget(creator_row)

        files_label = QLabel('')
        files_label.setStyleSheet(f'QLabel {{ color: {Colors.TEXT_PRIMARY}; font-size: {Fonts.SIZE_XS}px; }}')
        info_layout.addWidget(files_label)

        info_layout.addStretch()
        layout.addWidget(info_widget)

    def _get_icon(self, name: str, scale: int = 32) -> QIcon:
        """Return a QtAwesome icon when available, fallback to empty QIcon."""
        if self._qta is None:
            try:
                import qtawesome as qta
                self._qta = qta
            except Exception:
                self._qta = False

        if self._qta:
            try:
                return self._qta.icon(name)
            except Exception:
                return QIcon()
        return QIcon()

    def _get_service_icon(self, service: str) -> QIcon:
        """Load service logo from resources/logos folder."""
        # Map service names to logo files
        logo_files = {
            'all': None,  # Use qta icon for "all"
            'onlyfans': 'onlyfans.svg',
            'fansly': 'fansly.svg',
            'candfans': 'candfans.png',
            'patreon': None,  # Use qta icon (looks better on dark background)
            'fanbox': 'fanbox.svg',
            'fantia': 'fantia-square-logo.png',
            'boosty': 'boosty.svg',
            'gumroad': 'gumroad.svg',
            'subscribestar': 'subscribestar.png',
            'dlsite': 'DLsite.png'
        }
        
        logo_file = logo_files.get(service)
        if not logo_file:
            # Use qta icon for patreon or unknown services
            icon_map = {
                'patreon': 'fa5b.patreon'
            }
            if service in icon_map:
                return qta.icon(icon_map[service], color='#e0e0e0')
            return QIcon()  # Return empty icon for unknown services
        
        # Build path to logo file (handles PyInstaller bundles)
        logo_path = get_resource_path('resources', 'logos', logo_file)
        if not logo_path.exists():
            logger.debug(f"Logo not found: {logo_path}")
            return QIcon()  # Return empty icon if file not found
        
        # Load icon from file
        return QIcon(str(logo_path))

    def _resolve_creator_display(self) -> str:
        """Resolve creator display name using centralized utility."""
        creator_id = self.post_data.user_id or ""
        service = self.post_data.service or ""
        return resolve_creator_name(
            self._creator_lookup,
            self.platform,
            service,
            creator_id,
            fallback=creator_id or "Unknown"
        )

    def _emit_creator_clicked(self) -> None:
        """Emit creator_clicked signal with creator data."""
        creator_id = self.post_data.user_id or ""
        service = self.post_data.service or ""
        if not creator_id or not service:
            return
        name = self._creator_display_name or creator_id
        data = {
            "id": creator_id,
            "creator_id": creator_id,
            "service": service,
            "name": name,
        }
        self.creator_clicked.emit(data)

    def _on_creator_label_clicked(self, event) -> None:
        """Handle creator label click."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._emit_creator_clicked()
        event.accept()

    def _iter_files(self) -> List[FileDTO]:
        """Iterate over all files in the post."""
        files: List[FileDTO] = []
        if self.post_data.file:
            files.append(self.post_data.file)
        files.extend(self.post_data.attachments)
        return files

    def _get_first_video_url(self) -> Optional[str]:
        """Get URL of first video file for preview, if any."""
        for file_dto in self._iter_files():
            if _is_video_file(file_dto) and file_dto.path:
                return _build_media_url(self.platform, file_dto.path)
        return None

    def set_preview_manager(self, manager) -> None:
        """Set the shared video preview manager."""
        self._preview_manager = manager

    def _is_fully_visible(self) -> bool:
        """Check if the card is fully visible in its parent scroll area."""
        # Find parent scroll area
        parent = self.parent()
        while parent:
            if isinstance(parent, QScrollArea):
                viewport = parent.viewport()
                if viewport:
                    # Get card rect in viewport coordinates
                    card_rect = self.rect()
                    card_top_left = self.mapTo(viewport, card_rect.topLeft())
                    card_bottom_right = self.mapTo(viewport, card_rect.bottomRight())
                    card_in_viewport = QRect(card_top_left, card_bottom_right)

                    # Check if fully contained in viewport
                    viewport_rect = viewport.rect()
                    return viewport_rect.contains(card_in_viewport)
            parent = parent.parent()
        return True  # No scroll area found, assume visible

    def _has_valid_thumbnail(self) -> bool:
        """Check if the card has a successfully loaded thumbnail (not failed/placeholder)."""
        return self.thumbnail_pixmap is not None and not self.thumbnail_pixmap.isNull()

    def _set_placeholder(self):
        """Set placeholder image while loading."""
        w = max(self.card_width - (Spacing.CARD_BORDER * 2), self.thumbnail_label.width())
        h = max(Spacing.THUMBNAIL_HEIGHT, self.thumbnail_label.height())

        placeholder = QPixmap(w, h)
        placeholder.fill(QColor("transparent"))

        painter = QPainter(placeholder)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        inner_margin = 0
        inner_rect = placeholder.rect().adjusted(inner_margin, inner_margin, -inner_margin, -inner_margin)

        # Try to load creator avatar as background
        avatar_pixmap = self._load_creator_avatar()
        if avatar_pixmap and not avatar_pixmap.isNull():
            logger.debug(f"Avatar loaded for placeholder: post_id={self.post_data.id}, service={self.post_data.service}, user_id={self.post_data.user_id}, size={avatar_pixmap.width()}x{avatar_pixmap.height()}")
            # Scale avatar to fill background
            scaled_avatar = avatar_pixmap.scaled(
                inner_rect.width(), inner_rect.height(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )
            # Draw avatar as background at higher opacity
            painter.setOpacity(0.5)  # 50% opacity for visible effect
            painter.drawPixmap(inner_rect, scaled_avatar)
            painter.setOpacity(1.0)
        else:
            logger.info(f"No avatar available for placeholder: post_id={self.post_data.id}, service={self.post_data.service}, user_id={self.post_data.user_id}")
        
        # Draw semi-transparent rounded rect background over avatar with random color
        bg_color = _get_random_placeholder_color()
        if avatar_pixmap and not avatar_pixmap.isNull():
            # Make background much more transparent if avatar is present
            bg_color.setAlpha(100)  # 40% opacity to let avatar show through clearly
        painter.setBrush(bg_color)
        painter.setPen(QColor(Colors.BORDER_LIGHT))
        painter.drawRoundedRect(inner_rect, Spacing.RADIUS_LG, Spacing.RADIUS_LG)

        painter.end()
        self.thumbnail_label.setPixmap(placeholder)
        self._show_thumbnail_spinner()
        self.badge_container.raise_()
        self.duration_label.raise_()
        self.select_btn.raise_()
        self.badge_container.update()
        self.select_btn.update()

    def _load_creator_avatar(self) -> Optional[QPixmap]:
        """Load creator avatar for placeholder background."""
        if not self._creators_manager:
            logger.info(f"No creators_manager available for avatar load: post_id={self.post_data.id}")
            return None
        
        try:
            import hashlib
            from pathlib import Path
            from urllib.parse import urlparse
            from src.core.media_manager import MediaManager
            
            creator = self._creators_manager.get_creator(
                self.platform,
                self.post_data.service,
                self.post_data.user_id
            )
            
            if not creator:
                logger.info(f"Creator not found in manager: platform={self.platform}, service={self.post_data.service}, user_id={self.post_data.user_id}")
                return None
            
            # Build avatar URL
            icon_url = MediaManager.build_creator_icon_url(
                self.platform,
                self.post_data.service,
                self.post_data.user_id
            )
            logger.info(f"Looking for cached avatar: url={icon_url}")
            
            # Check if already cached (without downloading)
            # Compute cache path directly (matches MediaManager._download_media logic)
            cache_base = Path.home() / ".coomer-betterui" / "thumbnails" / "media" / "raw"
            parsed = urlparse(icon_url)
            suffix = Path(parsed.path).suffix or ".bin"
            digest = hashlib.sha256(icon_url.encode("utf-8")).hexdigest()
            cached_path = cache_base / f"{digest}{suffix}"
            
            logger.info(f"Checking cache path: {cached_path}, exists={cached_path.exists()}")
            
            if cached_path.exists():
                pixmap = QPixmap(str(cached_path))
                if not pixmap.isNull():
                    logger.info(f"Avatar loaded successfully from cache: {cached_path}")
                    return pixmap
                else:
                    logger.info(f"Cached avatar file is null/invalid: {cached_path}")
        except Exception as e:
            logger.info(f"Exception loading creator avatar: {e}", exc_info=True)
        
        return None

    def update_size(self, new_width: int):
        """Update card size when grid is reflowed. Rescales placeholder if needed."""
        old_width = self.card_width
        self.card_width = new_width
        
        # Update widget sizes - subtract border width * 2 for thumbnail
        thumb_width = new_width - (Spacing.CARD_BORDER * 2)
        self.thumbnail_label.setFixedSize(thumb_width, Spacing.THUMBNAIL_HEIGHT)
        self.setFixedSize(new_width, Spacing.CARD_HEIGHT + Spacing.XXXL)
        
        # If we have a loaded thumbnail pixmap, rescale it
        if self.thumbnail_pixmap and not self.thumbnail_pixmap.isNull():
            final_thumb = scale_and_crop_pixmap(self.thumbnail_pixmap, (thumb_width, Spacing.THUMBNAIL_HEIGHT))
            self.thumbnail_label.setPixmap(final_thumb)
        elif self.thumbnail_label.pixmap() and not self.thumbnail_label.pixmap().isNull():
            # Placeholder is showing - regenerate at new size if size changed
            if old_width != new_width:
                self._regenerate_placeholder()
        
        # Reposition overlay elements
        self._position_thumbnail_spinner()
        self._reposition_duration_label()
        self.badge_container.raise_()
        self.duration_label.raise_()
        self.select_btn.raise_()

    def _regenerate_placeholder(self):
        """Regenerate placeholder at current card_width."""
        w = self.card_width - (Spacing.CARD_BORDER * 2)  # Account for border
        h = Spacing.THUMBNAIL_HEIGHT
        
        # Check what kind of placeholder we need
        has_files = bool(self.post_data.file or self.post_data.attachments)
        has_content = bool(self.post_data.content or self.post_data.title or self.post_data.substring)
        
        if not has_files and has_content:
            # Content-only placeholder
            avatar_pixmap = self._load_creator_avatar()
            placeholder = _render_broken_media_placeholder(
                w, h, bg_color=None, avatar_pixmap=avatar_pixmap, icon_name=CONTENT_ONLY_ICON_NAME
            )
        else:
            # Standard loading placeholder
            placeholder = QPixmap(w, h)
            placeholder.fill(QColor("transparent"))
            
            painter = QPainter(placeholder)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            
            inner_rect = placeholder.rect()
            avatar_pixmap = self._load_creator_avatar()
            
            if avatar_pixmap and not avatar_pixmap.isNull():
                scaled_avatar = avatar_pixmap.scaled(
                    inner_rect.width(), inner_rect.height(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation
                )
                painter.setOpacity(0.5)
                painter.drawPixmap(inner_rect, scaled_avatar)
                painter.setOpacity(1.0)
            
            bg_color = _get_random_placeholder_color()
            if avatar_pixmap and not avatar_pixmap.isNull():
                bg_color.setAlpha(100)
            painter.setBrush(bg_color)
            painter.setPen(QColor(Colors.BORDER_LIGHT))
            painter.drawRoundedRect(inner_rect, Spacing.RADIUS_LG, Spacing.RADIUS_LG)
            painter.end()
        
        self.thumbnail_label.setPixmap(placeholder)

    def _position_thumbnail_spinner(self):
        """Position the thumbnail spinner in center."""
        if not hasattr(self, "thumbnail_spinner"):
            return
        w = max(1, self.thumbnail_label.width())
        h = max(1, self.thumbnail_label.height())
        self.thumbnail_spinner.move(
            max(0, (w - self.thumbnail_spinner.width()) // 2),
            max(0, (h - self.thumbnail_spinner.height()) // 2),
        )

    def _reposition_duration_label(self):
        """Reposition the duration label at bottom-left of thumbnail."""
        if not hasattr(self, 'duration_label') or not self.duration_label.isVisible():
            return
        h = max(1, self.thumbnail_label.height())
        lbl_h = self.duration_label.height()
        self.duration_label.move(8, h - lbl_h - 8)
        self.duration_label.raise_()

    def _show_thumbnail_spinner(self):
        """Show and start the thumbnail spinner."""
        if not hasattr(self, "thumbnail_spinner"):
            return
        self._position_thumbnail_spinner()
        self.thumbnail_spinner.raise_()
        self.thumbnail_spinner.start()

    def _hide_thumbnail_spinner(self):
        """Hide the thumbnail spinner."""
        if hasattr(self, "thumbnail_spinner"):
            self.thumbnail_spinner.stop()

    def _set_video_placeholder(self):
        """Set video icon placeholder for video files."""
        w = self.card_width - (Spacing.CARD_BORDER * 2)
        h = Spacing.THUMBNAIL_HEIGHT
        placeholder = QPixmap(w, h)
        placeholder.fill(QColor(Colors.BG_TERTIARY))

        video_icon = qta.icon('fa5s.film', color=Colors.ACCENT_PRIMARY)
        video_pixmap = video_icon.pixmap(Spacing.ICON_XL * 2, Spacing.ICON_XL * 2)  # 64x64

        painter = QPainter(placeholder)
        icon_size = Spacing.ICON_XL * 2
        x = (w - icon_size) // 2
        y = (h - icon_size) // 2
        painter.drawPixmap(x, y, video_pixmap)
        painter.end()

        self.thumbnail_label.setPixmap(placeholder)
        self._hide_thumbnail_spinner()
        self.badge_container.raise_()
        self.duration_label.raise_()
        self.select_btn.raise_()
        self.badge_container.update()
        self.select_btn.update()
        self._update_badge_counts()

    def eventFilter(self, obj, event):
        """Filter events for thumbnail label resizing."""
        if obj is self.thumbnail_label and event.type() == QEvent.Type.Resize:
            w = obj.width()
            h = obj.height()
            try:
                self.overlay.setGeometry(0, 0, w, h)
            except Exception:
                pass
            self._position_thumbnail_spinner()
            try:
                self.select_btn.move(8, 8)
            except Exception:
                pass
            try:
                if self.badge_container.isVisible():
                    self.badge_container.adjustSize()
                    b_w = self.badge_container.width()
                    b_h = self.badge_container.height()
                    if BADGE_POSITION == 'bottom-left':
                        bx = 8
                        by = max(4, h - b_h - 8)
                    else:
                        bx = max(4, w - b_w - 8)
                        by = 8
                    bx = max(4, min(bx, max(0, w - b_w - 4)))
                    by = max(4, min(by, max(0, h - b_h - 4)))
                    try:
                        self.badge_container.setGeometry(bx, by, b_w, b_h)
                    except Exception:
                        pass
            except Exception:
                pass
            # Reposition duration label on resize
            self._reposition_duration_label()
        return super().eventFilter(obj, event)

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        """Format seconds into human-readable duration (e.g. '3:24' or '1:02:15')."""
        sec = int(seconds)
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def _update_badge_counts(self):
        """Compute file type counts and update the badge label."""
        images = 0
        videos = 0
        others = 0
        max_duration = getattr(self, '_probed_duration', 0.0) or 0.0

        if self.post_data.file:
            if _is_image_file(self.post_data.file):
                images += 1
            elif _is_video_file(self.post_data.file):
                videos += 1
                if self.post_data.file.duration:
                    max_duration = max(max_duration, self.post_data.file.duration)
            else:
                others += 1

        for att in self.post_data.attachments:
            if _is_image_file(att):
                images += 1
            elif _is_video_file(att):
                videos += 1
                if att.duration:
                    max_duration = max(max_duration, att.duration)
            else:
                others += 1

        # Batch updates to prevent white flashes
        self.badge_container.setUpdatesEnabled(False)
        try:
            # Clear existing badge items
            while self.badge_layout.count():
                item = self.badge_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            has_items = False

            if images:
                img_icon_btn = QPushButton()
                img_icon_btn.setObjectName("badgeIconImage")
                img_icon_btn.setFlat(True)
                img_icon_btn.setIcon(qta.icon('fa5s.image', color='white'))
                img_icon_btn.setIconSize(QSize(Spacing.ICON_SM, Spacing.ICON_SM))
                img_icon_btn.setFixedSize(Spacing.BTN_SM, Spacing.BTN_SM)
                img_icon_btn.setToolTip(f"{images} image{'s' if images != 1 else ''}")
                img_icon_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                self.badge_layout.addWidget(img_icon_btn)
                has_items = True

            if videos:
                vid_icon_btn = QPushButton()
                vid_icon_btn.setObjectName("badgeIconVideo")
                vid_icon_btn.setFlat(True)
                vid_icon_btn.setIcon(qta.icon('fa5s.film', color='white'))
                vid_icon_btn.setIconSize(QSize(Spacing.ICON_SM, Spacing.ICON_SM))
                vid_icon_btn.setFixedSize(Spacing.BTN_SM, Spacing.BTN_SM)
                vid_icon_btn.setToolTip(f"{videos} video{'s' if videos != 1 else ''}")
                vid_icon_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                self.badge_layout.addWidget(vid_icon_btn)
                has_items = True

            if others:
                other_icon_btn = QPushButton()
                other_icon_btn.setObjectName("badgeIconFile")
                other_icon_btn.setFlat(True)
                other_icon_btn.setIcon(qta.icon('fa5s.file', color='white'))
                other_icon_btn.setIconSize(QSize(Spacing.ICON_SM, Spacing.ICON_SM))
                other_icon_btn.setFixedSize(Spacing.BTN_SM, Spacing.BTN_SM)
                other_icon_btn.setToolTip(f"{others} other file{'s' if others != 1 else ''}")
                other_icon_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                self.badge_layout.addWidget(other_icon_btn)
                has_items = True

            if max_duration > 0:
                text = self._fmt_duration(max_duration)
                self.duration_label.setText(text)
                # Manual size: font metrics + padding (2px top/bottom, 6px left/right)
                fm = self.duration_label.fontMetrics()
                pad_h, pad_v = 6, 2
                text_w = fm.horizontalAdvance(text)
                text_h = fm.height()
                lbl_w = text_w + pad_h * 2 + 2  # +2 safety
                lbl_h = text_h + pad_v * 2 + 2
                self.duration_label.setFixedSize(lbl_w, lbl_h)

                h = max(1, self.thumbnail_label.height())
                # Bottom-left corner
                self.duration_label.move(8, h - lbl_h - 8)
                self.duration_label.setVisible(True)
                self.duration_label.raise_()
                logger.debug(f"[duration-label] showing '{text}' size={lbl_w}x{lbl_h}")
            elif videos > 0 and not getattr(self, '_duration_probing', False):
                self._probe_video_durations()

            if has_items:
                try:
                    self.badge_container.adjustSize()
                    b_w = self.badge_container.width()
                    b_h = self.badge_container.height()
                    w = max(1, self.thumbnail_label.width())
                    h = max(1, self.thumbnail_label.height())
                    if BADGE_POSITION == 'bottom-left':
                        bx = 8
                        by = max(4, h - b_h - 8)
                    else:
                        bx = max(4, w - b_w - 8)
                        by = 8
                    bx = max(4, min(bx, max(0, w - b_w - 4)))
                    by = max(4, min(by, max(0, h - b_h - 4)))
                    try:
                        self.badge_container.setGeometry(bx, by, b_w, b_h)
                    except Exception:
                        pass
                except Exception:
                    pass
                self.badge_container.setVisible(True)
                self.badge_container.raise_()
                self.badge_container.update()
            else:
                self.badge_container.setVisible(False)
        finally:
            self.badge_container.setUpdatesEnabled(True)

    def _get_video_urls(self) -> List[str]:
        """Get media URLs for all video files in the post."""
        urls = []
        for file_dto in self._iter_files():
            if _is_video_file(file_dto) and file_dto.path:
                urls.append(_build_media_url(self.platform, file_dto.path))
        return urls

    def _probe_video_durations(self):
        """Enqueue ffprobe jobs for all video URLs in this post."""
        self._duration_probing = True
        urls = self._get_video_urls()
        if not urls:
            logger.debug(f"[duration-probe] no video URLs for post {self.post_data.id}")
            return
        import weakref
        card_ref = weakref.ref(self)
        queue = _DurationProbeQueue.instance()
        logger.debug(f"[duration-probe] enqueueing {len(urls)} video(s) for post={self.post_data.id}")
        for url in urls:
            queue.enqueue(card_ref, url)

    def _on_duration_probed(self, duration: float):
        """Called on main thread when ffprobe duration is available.

        Accumulates the maximum duration across all probed videos.
        """
        try:
            _ = self.objectName()
        except RuntimeError:
            return
        prev = getattr(self, '_probed_duration', 0.0) or 0.0
        self._probed_duration = max(prev, duration)
        logger.debug(
            f"[duration-probe] result for post={self.post_data.id}: "
            f"{duration:.1f}s (max={self._probed_duration:.1f}s)"
        )
        self._update_badge_counts()

    def _load_thumbnail(self):
        """Load thumbnail from post data."""
        self._thumbnail_url = None
        
        # Check if this is a content-only post (no attachments, no file, but has content/title)
        has_files = bool(self.post_data.file or self.post_data.attachments)
        has_content = bool(self.post_data.content or self.post_data.title or self.post_data.substring)
        
        if not has_files and has_content:
            # Render content-only placeholder with newspaper icon
            w = max(self.card_width - (Spacing.CARD_BORDER * 2), self.thumbnail_label.width())
            h = max(Spacing.THUMBNAIL_HEIGHT, self.thumbnail_label.height())
            avatar_pixmap = self._load_creator_avatar()
            placeholder = _render_broken_media_placeholder(
                w, h, 
                bg_color=None,  # Use random color from palette
                avatar_pixmap=avatar_pixmap, 
                icon_name=CONTENT_ONLY_ICON_NAME
            )
            self.thumbnail_label.setPixmap(placeholder)
            self._hide_thumbnail_spinner()
            self.badge_container.raise_()
            self.duration_label.raise_()
            self.select_btn.raise_()
            self.badge_container.update()
            self.select_btn.update()
            self._update_badge_counts()
            return
        
        thumb_url = getattr(self.post_data, "thumbnail_url", None)
        if thumb_url:
            self._set_placeholder()
            self._thumbnail_url = thumb_url
            self.thumbnail_label.load_image(
                url=thumb_url,
                target_size=(self.thumbnail_label.width(), self.thumbnail_label.height()),
                on_loaded=lambda url, pix: self._on_image_loaded(url, pix),
                on_failed=lambda url, err: self._on_image_failed(url, err)
            )
            self._update_badge_counts()
            return

        file_dto = self.post_data.file or (self.post_data.attachments[0] if self.post_data.attachments else None)
        file_path = file_dto.path if file_dto else None

        if file_path:
            self._set_placeholder()
            file_url = _build_media_url(self.platform, file_path)
            self._thumbnail_url = file_url

            # Gather attachments for potential collage (only images)
            collage_urls = []
            if file_dto and _is_image_file(file_dto):
                collage_urls.append(file_url)

            for att in self.post_data.attachments:
                if not att.path:
                    continue
                u = _build_media_url(self.platform, att.path)
                if _is_image_file(att):
                    collage_urls.append(u)

            if len(collage_urls) >= 2:
                file_url = collage_urls[0]
                needed = collage_urls[:3]
                pixmap_map = {}
                remaining = set(needed)
                failed = set()

                try:
                    target_w = self.card_width - (Spacing.CARD_BORDER * 2)
                    target_h = Spacing.THUMBNAIL_HEIGHT
                except Exception:
                    return

                import weakref
                ref_self = weakref.ref(self)

                self._collage_requests = []

                def _on_collage_loaded(url, pix):
                    self_obj = ref_self()
                    if not self_obj:
                        return

                    if url in remaining:
                        logger.debug(f"Collage image loaded: {url}")
                        if pix is not None:
                            pixmap_map[url] = pix
                        remaining.discard(url)

                    if not remaining:
                        try:
                            w = self_obj.card_width - (Spacing.CARD_BORDER * 2)  # Account for border
                            h = Spacing.THUMBNAIL_HEIGHT
                            gap = max(Spacing.XS, int(min(w, h) * COLLAGE_GAP_RATIO))
                            inner_margin = 0
                            inner_x = inner_margin
                            inner_y = inner_margin
                            inner_w = w
                            inner_h = h

                            if not pixmap_map:
                                avatar_pixmap = self_obj._load_creator_avatar()
                                placeholder = _render_broken_media_placeholder(w, h, bg_color=None, avatar_pixmap=avatar_pixmap, icon_name=BROKEN_MEDIA_ICON_NAME)
                                self_obj.thumbnail_label.setPixmap(placeholder)
                                self_obj.thumbnail_pixmap = None
                                self_obj.badge_container.raise_()
                                self_obj.duration_label.raise_()
                                self_obj.select_btn.raise_()
                                self_obj._hide_thumbnail_spinner()
                                return

                            comp = QPixmap(w, h)
                            comp.fill(QColor('transparent'))
                            painter = QPainter(comp)
                            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

                            radius = max(float(Spacing.RADIUS_LG), float(min(w, h) * 0.04))
                            path = QPainterPath()
                            path.addRoundedRect(QRectF(0.0, 0.0, float(w), float(h)), radius, radius)
                            painter.setClipPath(path)

                            inner_rect = QRect(inner_x, inner_y, inner_w, inner_h)

                            def draw_fitted(target_rect, source_pix):
                                if not source_pix or source_pix.isNull():
                                    return
                                cropped = scale_and_crop_pixmap(source_pix, target_rect.size())
                                painter.drawPixmap(target_rect, cropped)

                            if failed and pixmap_map:
                                first_pix = next(iter(pixmap_map.values()))
                                draw_fitted(inner_rect, first_pix)
                            else:
                                if len(needed) == 2:
                                    left = pixmap_map.get(needed[0])
                                    right = pixmap_map.get(needed[1])
                                    lw = int(inner_rect.width() * 0.6)
                                    rw = inner_rect.width() - lw - gap

                                    if left:
                                        draw_fitted(QRect(inner_rect.x(), inner_rect.y(), lw, inner_rect.height()), left)
                                    if right:
                                        draw_fitted(QRect(inner_rect.x() + lw + gap, inner_rect.y(), rw, inner_rect.height()), right)
                                else:
                                    left = pixmap_map.get(needed[0])
                                    r1 = pixmap_map.get(needed[1])
                                    r2 = pixmap_map.get(needed[2])
                                    lw = int(inner_rect.width() * 0.6)
                                    rw = inner_rect.width() - lw - gap
                                    half_h = int((inner_rect.height() - gap) / 2)

                                    if left:
                                        draw_fitted(QRect(inner_rect.x(), inner_rect.y(), lw, inner_rect.height()), left)
                                    if r1:
                                        draw_fitted(QRect(inner_rect.x() + lw + gap, inner_rect.y(), rw, half_h), r1)
                                    if r2:
                                        draw_fitted(QRect(inner_rect.x() + lw + gap, inner_rect.y() + half_h + gap, rw, half_h), r2)

                            painter.end()

                            try:
                                self_obj.thumbnail_pixmap = comp
                                self_obj.thumbnail_label.setPixmap(comp)
                                self_obj.badge_container.raise_()
                                self_obj.duration_label.raise_()
                                self_obj.select_btn.raise_()
                                self_obj._hide_thumbnail_spinner()
                                self_obj._update_badge_counts()
                                try:
                                    self_obj.overlay.setVisible(False)
                                except:
                                    pass
                            except Exception:
                                logger.exception('Failed setting composed collage pixmap')
                        except Exception:
                            logger.exception('Error composing collage')

                def _on_collage_failed(url, err):
                    self_obj = ref_self()
                    if not self_obj:
                        return
                    if url in remaining:
                        remaining.discard(url)
                        failed.add(url)
                    if not remaining:
                        _on_collage_loaded(url, None)

                for u in needed:
                    req = ImageLoadRequest.load(
                        url=u,
                        target_size=(target_w, target_h),
                        on_loaded=_on_collage_loaded,
                        on_failed=_on_collage_failed
                    )
                    self._collage_requests.append(req)

                self._update_badge_counts()
                return

            self.thumbnail_label.load_image(
                url=file_url,
                target_size=(self.thumbnail_label.width(), self.thumbnail_label.height()),
                on_loaded=lambda url, pix: self._on_image_loaded(url, pix),
                on_failed=lambda url, err: self._on_image_failed(url, err)
            )
            self._update_badge_counts()

    def _on_image_loaded(self, url: str, pixmap):
        """Handle loaded image."""
        expected_url = getattr(self, "_thumbnail_url", None)
        if expected_url:
            if url != expected_url:
                return
        else:
            file_dto = self.post_data.file or (self.post_data.attachments[0] if self.post_data.attachments else None)
            if not file_dto or not file_dto.path:
                return
            file_path = file_dto.path
            file_hash = file_path.split('/')[-1] if file_path else ''
            if not (file_hash and file_hash in url):
                return

        self._hide_thumbnail_spinner()
        self.thumbnail_pixmap = pixmap
        try:
            w = max(1, self.card_width - (Spacing.CARD_BORDER * 2))
            h = max(1, Spacing.THUMBNAIL_HEIGHT)
            final_thumb = scale_and_crop_pixmap(pixmap, (w, h))
            self.thumbnail_label.setPixmap(final_thumb)
            self.badge_container.raise_()
            self.duration_label.raise_()
            self.select_btn.raise_()
            try:
                self.overlay.setVisible(False)
            except Exception:
                pass
        except Exception as e:
            logger.exception(f"Failed drawing thumbnail: {e}")
            self.thumbnail_label.setPixmap(pixmap)

        self._update_badge_counts()

    def _on_image_failed(self, url: str, error: str):
        """Handle image load failure."""
        expected_url = getattr(self, "_thumbnail_url", None)
        if expected_url:
            if url != expected_url:
                return
        else:
            file_dto = self.post_data.file or (self.post_data.attachments[0] if self.post_data.attachments else None)
            if not file_dto or not file_dto.path:
                return
            file_path = file_dto.path
            file_hash = file_path.split('/')[-1] if file_path else ''
            if not (file_hash and file_hash in url):
                return

        self._hide_thumbnail_spinner()
        w = max(self.card_width - (Spacing.CARD_BORDER * 2), self.thumbnail_label.width())
        h = max(Spacing.THUMBNAIL_HEIGHT, self.thumbnail_label.height())
        avatar_pixmap = self._load_creator_avatar()
        icon_name = _get_icon_for_error(error)
        placeholder = _render_broken_media_placeholder(w, h, bg_color=None, avatar_pixmap=avatar_pixmap, icon_name=icon_name)

        try:
            self.thumbnail_label.setPixmap(placeholder)
            self.badge_container.raise_()
            self.duration_label.raise_()
            self.select_btn.raise_()
        except Exception:
            pass
        self._update_badge_counts()

    def mousePressEvent(self, event):
        """Handle mouse click."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.post_data)
        super().mousePressEvent(event)

    def enterEvent(self, event):
        """Show overlay on hover and request video preview."""
        file_count = len(self.post_data.attachments) + (1 if self.post_data.file else 0)

        if self.overlay.pixmap() is not None:
            pass
        else:
            if file_count > 1:
                self.overlay.setText(f'{file_count} files')
            else:
                self.overlay.setText('')

        self.overlay.setVisible(True)
        # Keep duration label above overlay
        if self.duration_label.isVisible():
            self.duration_label.raise_()

        # Request video preview if this post has video, card is fully visible, and thumbnail loaded
        if self._preview_manager and self._has_valid_thumbnail() and self._is_fully_visible():
            video_url = self._get_first_video_url()
            if video_url:
                self._preview_manager.request_preview(self, video_url)

        super().enterEvent(event)

    def leaveEvent(self, event):
        """Hide overlay and cancel video preview."""
        # Check if mouse actually left the card (not just occluded by preview window)
        global_pos = QCursor.pos()
        card_rect = self.rect()
        local_pos = self.mapFromGlobal(global_pos)
        mouse_really_left = not card_rect.contains(local_pos)

        if mouse_really_left:
            self.overlay.setVisible(False)
            if self._preview_manager:
                self._preview_manager.cancel_preview(self)

        super().leaveEvent(event)

    def showEvent(self, event):
        """Start GIF playback when shown."""
        super().showEvent(event)
        if self.movie:
            try:
                self.movie.start()
            except Exception:
                pass

    def hideEvent(self, event):
        """Stop GIF playback and cancel video preview when hidden."""
        if self.movie:
            try:
                self.movie.stop()
            except Exception:
                pass

        # Cancel video preview when scrolled out of view
        if self._preview_manager:
            self._preview_manager.cancel_preview(self)

        super().hideEvent(event)

    def _on_select_toggled(self, checked: bool):
        """Handle selection checkbox toggled."""
        self.selected = checked
        try:
            self.selected_changed.emit(self.post_data, checked)
        except Exception:
            pass
