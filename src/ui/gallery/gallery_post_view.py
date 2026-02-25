"""
Gallery-focused post detail view
Top half: Large media preview
Bottom half: Tight responsive thumbnail grid
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QFrame, QGridLayout, QScrollArea,
                             QSizePolicy, QStackedWidget, QSplitter,
                             QSplitterHandle, QToolButton, QComboBox,
                             QGraphicsOpacityEffect)
from PyQt6.QtCore import (
    Qt,
    pyqtSignal,
    QTimer,
    QRect,
    QSize,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QEasingCurve,
)
from PyQt6.QtGui import QPixmap, QCursor, QPainter, QMovie
from typing import List
import html as html_lib
import re
import sqlite3
import logging
import hashlib
from pathlib import Path
import weakref
from urllib.parse import urlparse
import qtawesome as qta
from src.ui.images.image_utils import (
    scale_and_crop_pixmap,
    scale_pixmap_to_fill,
    scale_pixmap_to_fit,
    create_circular_pixmap,
)
from src.ui.creators.creator_utils import resolve_creator_name
from src.ui.images.async_image_widgets import AsyncImageLabel, ImageLoadRequest
from src.ui.images.zoomable_image import ZoomableImageWidget
from src.ui.video.html_viewer import HtmlViewer, HtmlPopupWindow
from src.ui.widgets.spinner_widget import SpinnerWidget
from src.ui.gallery.file_browser_sidebar import FileBrowserSidebar
from src.core.media_manager import MediaManager
from src.core.jdownloader_export import JDownloaderExporter
from src.core.dto.post import PostDTO
from src.core.dto.file import FileDTO
from src.ui.common.view_models import MediaItem
from src.ui.gallery.post_card import _DurationCache, _DurationProbeQueue
from src.ui.common.theme import Colors, Fonts, Spacing, FileSidebar

logger = logging.getLogger(__name__)


class _GifPreviewLabel(QLabel):
    def sizeHint(self) -> QSize:
        return QSize(0, 0)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)


class MediaPreviewWidget(QWidget):
    """Large media preview (top half)"""

    prev_requested = pyqtSignal()
    next_requested = pyqtSignal()
    content_popup_closed = pyqtSignal()

    def __init__(self, parent=None, *, db_manager=None, core_context=None):
        super().__init__(parent)
        self.current_url = None
        self.image_loader = None  # Legacy: kept for GIF loading
        self._image_load_request = None  # New: ImageLoadRequest for preview images
        self._db = db_manager
        self._core_context = core_context
        self._post_content = ""
        self._gif_movie = None
        self._gif_frame_size = None
        self._gif_scaled_size = None
        self._gif_has_frame = False
        self._fade_duration_ms = 260
        self._fade_next_image = False
        self._fade_pending = False
        self._fade_overlay = None
        self._fade_effect = None
        self._fade_anim = None
        self._zoom_fade_effect = None
        self._zoom_fade_anim = None
        self._content_popup = None
        self._allow_external_media = False
        self._content_title = None
        self._content_post_title = ""
        self._content_creator_name = None
        self._content_creator_id = None
        self._content_service = None
        self._content_banner_url = None
        self._content_avatar_url = None
        self._content_display_html = ""
        self._preload_request = None
        self._preloaded_url = None
        self._preloaded_pixmap = None
        self._sidebar_toggle_overlay_handler = None
        self._setup_ui()

    def set_sidebar_toggle_overlay_handler(self, handler) -> None:
        self._sidebar_toggle_overlay_handler = handler
        if hasattr(self, "media_preview") and self.media_preview:
            try:
                self.media_preview.set_sidebar_toggle_overlay_handler(handler)
            except Exception:
                pass

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.setMouseTracking(True)

        self.preview_stack = QStackedWidget()
        self.preview_stack.setMouseTracking(True)
        self.preview_stack.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, False
        )
        self.preview_stack.installEventFilter(self)

        self.image_container = QWidget()
        self.image_container.setMouseTracking(True)
        self.image_container.installEventFilter(self)

        image_layout = QVBoxLayout(self.image_container)
        image_layout.setContentsMargins(0, 0, 0, 0)

        self.image_stack = QStackedWidget(self.image_container)
        self.image_stack.setObjectName("previewImageStack")
        self.image_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.image_stack.installEventFilter(self)

        self.zoom_widget = ZoomableImageWidget()
        self.zoom_widget.setMouseTracking(True)
        self.zoom_widget.installEventFilter(self)
        try:
            viewport = self.zoom_widget.viewport()
            if viewport is not None:
                viewport.setMouseTracking(True)
                viewport.installEventFilter(self)
        except Exception:
            pass
        self._zoom_fade_effect = QGraphicsOpacityEffect(self.zoom_widget)
        self._zoom_fade_effect.setOpacity(1.0)
        self.zoom_widget.setGraphicsEffect(self._zoom_fade_effect)
        self._zoom_fade_anim = QPropertyAnimation(self._zoom_fade_effect, b"opacity", self)
        self._zoom_fade_anim.setDuration(self._fade_duration_ms)
        self._zoom_fade_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.image_stack.addWidget(self.zoom_widget)

        self.gif_label = _GifPreviewLabel()
        self.gif_label.setObjectName("previewGifLabel")
        self.gif_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gif_label.setScaledContents(False)
        self.gif_label.setMouseTracking(True)
        self.gif_label.installEventFilter(self)
        self.gif_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.gif_label.setMinimumSize(0, 0)
        self.image_stack.addWidget(self.gif_label)

        image_layout.addWidget(self.image_stack)

        self._fade_overlay = QLabel(self.image_container)
        self._fade_overlay.setObjectName("previewFadeOverlay")
        self._fade_overlay.setScaledContents(True)
        self._fade_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._fade_overlay.setVisible(False)
        self._fade_effect = QGraphicsOpacityEffect(self._fade_overlay)
        self._fade_overlay.setGraphicsEffect(self._fade_effect)
        self._fade_anim = QPropertyAnimation(self._fade_effect, b"opacity", self)
        self._fade_anim.setDuration(self._fade_duration_ms)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fade_anim.finished.connect(self._hide_fade_overlay)

        self.preview_spinner = SpinnerWidget(self.image_container, size=50, color="#909090")

        self.zoom_controls = self._create_zoom_controls()
        self.preview_stack.addWidget(self.image_container)

        self.video_container = QWidget()
        self.video_container.setMouseTracking(True)
        self.video_container.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, False
        )
        self.video_container.installEventFilter(self)

        video_layout = QVBoxLayout(self.video_container)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.setSpacing(0)

        self.preview_stack.addWidget(self.video_container)

        self.content_container = QWidget()
        self.content_container.setMouseTracking(True)
        self.content_container.installEventFilter(self)
        content_layout = QVBoxLayout(self.content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_view = HtmlViewer()
        self.content_view.setMouseTracking(True)
        content_layout.addWidget(self.content_view)
        self.preview_stack.addWidget(self.content_container)
        layout.addWidget(self.preview_stack)

        # Preview navigation buttons (shown on hover, anchored to image container)
        self._nav_prev_btn = QPushButton(self.image_container)
        self._nav_prev_btn.setObjectName("prevMediaButton")
        self._nav_prev_btn.setIcon(qta.icon('fa5s.chevron-left'))
        self._nav_prev_btn.setFixedSize(32, 32)
        self._nav_prev_btn.clicked.connect(self.prev_requested.emit)
        self._nav_prev_btn.setVisible(False)

        self._nav_next_btn = QPushButton(self.image_container)
        self._nav_next_btn.setObjectName("nextMediaButton")
        self._nav_next_btn.setIcon(qta.icon('fa5s.chevron-right'))
        self._nav_next_btn.setFixedSize(32, 32)
        self._nav_next_btn.clicked.connect(self.next_requested.emit)
        self._nav_next_btn.setVisible(False)

        self._position_nav_buttons()
        self._nav_prev_btn.raise_()
        self._nav_next_btn.raise_()
        self._nav_allowed = True

    def _ensure_content_popup(self) -> HtmlPopupWindow:
        if self._content_popup is None:
            self._content_popup = HtmlPopupWindow(parent=None)
            self._content_popup.closed.connect(self._on_content_popup_closed)
        return self._content_popup

    def _on_content_popup_closed(self) -> None:
        self.content_popup_closed.emit()

    def _position_content_popup(self, popup: HtmlPopupWindow) -> None:
        parent_window = self.window() if self.window() else None
        if parent_window is None:
            return
        geo = parent_window.frameGeometry()
        target_w = max(520, int(geo.width() * 0.6))
        target_h = max(360, int(geo.height() * 0.6))
        popup.resize(target_w, target_h)
        center = geo.center()
        popup.move(center.x() - target_w // 2, center.y() - target_h // 2)

    def _set_content_profile(
        self,
        creator_name: str | None,
        service: str | None,
        creator_id: str | None,
        title: str | None,
    ) -> None:
        self._content_creator_name = creator_name or "Creator"
        self._content_creator_id = creator_id or ""
        self._content_service = service or ""
        self._content_post_title = title or ""
        self._content_title = title or "Post Content"
        if self._content_popup is not None:
            self._content_popup.set_title(self._content_title)
            self._content_popup.set_profile(
                self._content_creator_name,
                self._content_service,
                self._content_creator_id,
                None,
            )
            self._content_popup.set_profile_images(
                self._content_banner_url,
                self._content_avatar_url,
            )

    def _should_inject_title(self, html: str, title: str) -> bool:
        if not html or not title:
            return bool(title)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip().lower()
        title_norm = re.sub(r"\s+", " ", title).strip().lower()
        if not text or not title_norm:
            return bool(title_norm)
        return not text.startswith(title_norm)

    def _build_content_display_html(self) -> str:
        html = self._post_content or ""
        title = (self._content_post_title or "").strip()
        if not title:
            return html
        if not self._should_inject_title(html, title):
            return html
        safe_title = html_lib.escape(title)
        return f"<h2 class=\"post-title\">{safe_title}</h2>\n{html}"

    def get_content_display_html(self) -> str:
        return self._content_display_html or self._post_content or ""

    def _create_zoom_controls(self) -> QWidget:
        """Create zoom control buttons"""
        controls = QWidget(self.image_container)
        controls.setObjectName("zoomControls")
        controls.setFixedSize(120, 40)

        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(8, 8, 8, 8)
        controls_layout.setSpacing(4)

        # Zoom out button
        zoom_out_btn = QPushButton()
        zoom_out_btn.setIcon(qta.icon('fa5s.search-minus', color='#e0e0e0'))
        zoom_out_btn.setFixedSize(30, 30)
        zoom_out_btn.setToolTip("Zoom Out (Mouse Wheel)")
        zoom_out_btn.clicked.connect(self.zoom_widget.zoom_out)
        zoom_out_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(42, 42, 42, 0.8);
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_SM}px;
            }}
            QPushButton:hover {{
                background-color: rgba(53, 53, 53, 0.9);
                border-color: {Colors.ACCENT_PRIMARY};
            }}
        """)
        controls_layout.addWidget(zoom_out_btn)

        # Reset zoom button
        reset_btn = QPushButton()
        reset_btn.setIcon(qta.icon('fa5s.compress-arrows-alt', color='#e0e0e0'))
        reset_btn.setFixedSize(30, 30)
        reset_btn.setToolTip("Reset Zoom (Fit to View)")
        reset_btn.clicked.connect(self.zoom_widget.reset_zoom)
        reset_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(42, 42, 42, 0.8);
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_SM}px;
            }}
            QPushButton:hover {{
                background-color: rgba(53, 53, 53, 0.9);
                border-color: {Colors.ACCENT_PRIMARY};
            }}
        """)
        controls_layout.addWidget(reset_btn)

        # Zoom in button
        zoom_in_btn = QPushButton()
        zoom_in_btn.setIcon(qta.icon('fa5s.search-plus', color='#e0e0e0'))
        zoom_in_btn.setFixedSize(30, 30)
        zoom_in_btn.setToolTip("Zoom In (Mouse Wheel)")
        zoom_in_btn.clicked.connect(self.zoom_widget.zoom_in)
        zoom_in_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(42, 42, 42, 0.8);
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_SM}px;
            }}
            QPushButton:hover {{
                background-color: rgba(53, 53, 53, 0.9);
                border-color: {Colors.ACCENT_PRIMARY};
            }}
        """)
        controls_layout.addWidget(zoom_in_btn)

        # Position controls at top-right
        controls.move(self.image_container.width() - 140, 20)
        controls.raise_()

        return controls

    def resizeEvent(self, event):
        """Handle resize to reposition zoom controls"""
        super().resizeEvent(event)
        self._position_preview_spinner()
        self._refresh_image_positions()
        self.refresh_nav_visibility()
        self._update_gif_scaled_size()

    def _position_preview_spinner(self):
        if not hasattr(self, "preview_spinner"):
            return
        w = max(1, self.image_container.width())
        h = max(1, self.image_container.height())
        self.preview_spinner.move(
            max(0, (w - self.preview_spinner.width()) // 2),
            max(0, (h - self.preview_spinner.height()) // 2),
        )

    def _position_nav_buttons(self) -> None:
        if not hasattr(self, "image_container"):
            return
        if not hasattr(self, "_nav_prev_btn") or not hasattr(self, "_nav_next_btn"):
            return
        base = getattr(self, "preview_stack", None) or self.image_container
        w = base.width()
        h = base.height()
        btn_w = self._nav_prev_btn.width()
        btn_h = self._nav_prev_btn.height()
        y = max(0, (h - btn_h) // 2)
        margin = 12
        left_pos = QPoint(margin, y)
        right_pos = QPoint(max(margin, w - margin - btn_w), y)
        if base is not self.image_container:
            left_pos = self.image_container.mapFrom(base, left_pos)
            right_pos = self.image_container.mapFrom(base, right_pos)
        self._nav_prev_btn.move(left_pos)
        self._nav_next_btn.move(right_pos)

    def _refresh_image_positions(self) -> None:
        if hasattr(self, 'zoom_controls'):
            self.zoom_controls.move(self.image_container.width() - 140, 20)
            self.zoom_controls.raise_()
        self._position_nav_buttons()
        self._sync_fade_overlay_geometry()
        self._raise_overlay_controls()

    def set_nav_enabled(self, prev_enabled: bool, next_enabled: bool) -> None:
        if hasattr(self, "_nav_prev_btn"):
            self._nav_prev_btn.setEnabled(bool(prev_enabled))
        if hasattr(self, "_nav_next_btn"):
            self._nav_next_btn.setEnabled(bool(next_enabled))

    def set_nav_allowed(self, allowed: bool) -> None:
        self._nav_allowed = bool(allowed)
        if not self._nav_allowed:
            self._set_nav_visible(False)
            return
        self._set_nav_visible(self._cursor_over_preview())

    def _set_nav_visible(self, visible: bool) -> None:
        if not getattr(self, "_nav_allowed", True):
            visible = False
        if hasattr(self, "_nav_prev_btn"):
            self._nav_prev_btn.setVisible(visible)
            if visible:
                self._nav_prev_btn.raise_()
        if hasattr(self, "_nav_next_btn"):
            self._nav_next_btn.setVisible(visible)
            if visible:
                self._nav_next_btn.raise_()

    def enterEvent(self, event):
        if getattr(self.preview_stack, "currentWidget", None):
            if self.preview_stack.currentWidget() is getattr(self, "video_container", None):
                player = getattr(self, "current_video_player", None)
                if player is not None and hasattr(player, "set_nav_parent_hover"):
                    player.set_nav_parent_hover(True)
        self._set_nav_visible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        player = getattr(self, "current_video_player", None)
        if player is not None and hasattr(player, "set_nav_parent_hover"):
            player.set_nav_parent_hover(False)
        self._set_nav_visible(False)
        super().leaveEvent(event)

    def refresh_nav_visibility(self) -> None:
        self._set_nav_visible(self._nav_allowed and self._cursor_over_preview())

    def _cursor_over_preview(self) -> bool:
        try:
            if not self.isVisible() or not self.preview_stack:
                return False
            pos = QCursor.pos()
            local = self.preview_stack.mapFromGlobal(pos)
            return self.preview_stack.rect().contains(local)
        except Exception:
            return False

    def eventFilter(self, obj, event):
        if obj in (
            getattr(self, "preview_stack", None),
            getattr(self, "image_container", None),
            getattr(self, "image_stack", None),
            getattr(self, "zoom_widget", None),
            getattr(getattr(self, "zoom_widget", None), "viewport", lambda: None)(),
            getattr(self, "gif_label", None),
            getattr(self, "video_container", None),
            getattr(self, "content_container", None),
        ):
            if event.type() in (QEvent.Type.Enter, QEvent.Type.HoverEnter, QEvent.Type.MouseMove):
                self._set_nav_visible(self._nav_allowed)
            elif event.type() in (QEvent.Type.Leave, QEvent.Type.HoverLeave):
                QTimer.singleShot(0, self.refresh_nav_visibility)
        return super().eventFilter(obj, event)

    def _show_preview_spinner(self):
        if hasattr(self, "preview_spinner"):
            self._position_preview_spinner()
            self.preview_spinner.raise_()
            self.preview_spinner.start()

    def _hide_preview_spinner(self):
        if hasattr(self, "preview_spinner"):
            self.preview_spinner.stop()

    def _can_fade_from_current(self) -> bool:
        if not hasattr(self, "zoom_widget"):
            return False
        if not hasattr(self.zoom_widget, "current_pixmap"):
            return False
        if not self.zoom_widget.current_pixmap:
            return False
        if hasattr(self, "image_stack") and self.image_stack.currentWidget() is not self.zoom_widget:
            return False
        if not self.zoom_widget.isVisible():
            return False
        return True

    def set_fade_duration_ms(self, duration_ms: int) -> None:
        try:
            duration_ms = int(duration_ms)
        except (TypeError, ValueError):
            return
        duration_ms = max(0, duration_ms)
        self._fade_duration_ms = duration_ms
        if self._fade_anim:
            self._fade_anim.setDuration(self._fade_duration_ms)
        if self._zoom_fade_anim:
            self._zoom_fade_anim.setDuration(self._fade_duration_ms)

    def request_fade_next(self) -> None:
        self._fade_next_image = True

    def _consume_fade_request(self) -> bool:
        if self._fade_next_image:
            self._fade_next_image = False
            return True
        return False

    def _capture_fade_overlay(self) -> bool:
        if not self._fade_overlay or not self._fade_effect:
            return False
        if not hasattr(self, "zoom_widget") or not self.zoom_widget.isVisible():
            return False
        viewport = self.zoom_widget.viewport() if hasattr(self.zoom_widget, "viewport") else None
        if viewport is None or not viewport.isVisible():
            return False
        pix = viewport.grab()
        if pix.isNull():
            return False
        pos = viewport.mapTo(self.image_container, QPoint(0, 0))
        self._fade_overlay.setPixmap(pix)
        self._fade_overlay.setGeometry(QRect(pos, viewport.size()))
        self._fade_effect.setOpacity(1.0)
        self._fade_overlay.setVisible(True)
        self._fade_overlay.raise_()
        self._raise_overlay_controls()
        return True

    def _start_fade_out(self) -> None:
        if not self._fade_overlay or not self._fade_anim or not self._fade_effect:
            return
        if not self._fade_overlay.isVisible():
            return
        if self._fade_duration_ms <= 0:
            self._hide_fade_overlay()
            return
        self._fade_anim.stop()
        self._fade_effect.setOpacity(1.0)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()

    def _start_fade_in(self) -> None:
        if not self._zoom_fade_effect or not self._zoom_fade_anim:
            return
        if self._fade_duration_ms <= 0:
            self._zoom_fade_effect.setOpacity(1.0)
            return
        self._zoom_fade_anim.stop()
        self._zoom_fade_effect.setOpacity(0.0)
        self._zoom_fade_anim.setStartValue(0.0)
        self._zoom_fade_anim.setEndValue(1.0)
        self._zoom_fade_anim.start()

    def _hide_fade_overlay(self) -> None:
        if not self._fade_overlay:
            return
        self._fade_overlay.setVisible(False)
        self._fade_overlay.clear()

    def _sync_fade_overlay_geometry(self) -> None:
        if not self._fade_overlay or not self._fade_overlay.isVisible():
            return
        viewport = self.zoom_widget.viewport() if hasattr(self, "zoom_widget") else None
        if viewport is None:
            return
        pos = viewport.mapTo(self.image_container, QPoint(0, 0))
        self._fade_overlay.setGeometry(QRect(pos, viewport.size()))

    def _raise_overlay_controls(self) -> None:
        if hasattr(self, "zoom_controls"):
            self.zoom_controls.raise_()
        if hasattr(self, "_nav_prev_btn"):
            self._nav_prev_btn.raise_()
        if hasattr(self, "_nav_next_btn"):
            self._nav_next_btn.raise_()

    def _apply_loaded_pixmap(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        self.zoom_widget.set_pixmap(pixmap)
        self.zoom_controls.setVisible(True)
        self.zoom_controls.raise_()
        if self._fade_pending:
            self._start_fade_in()
            self._start_fade_out()
            self._fade_pending = False
        else:
            if self._zoom_fade_effect:
                self._zoom_fade_effect.setOpacity(1.0)
            self._hide_fade_overlay()

    def preload_image(self, url: str) -> None:
        if not url:
            return
        if self._is_gif(url):
            return
        if url == self.current_url:
            return
        if url == self._preloaded_url and self._preloaded_pixmap is not None:
            return
        if url == self._preloaded_url and self._preload_request is not None:
            return
        self._cancel_preload()
        self._preloaded_url = url
        self._preload_request = ImageLoadRequest.load(
            url=url,
            target_size=(0, 0),
            on_loaded=self._on_preload_loaded,
            on_failed=self._on_preload_failed,
            use_preview_loader=True,
        )

    def _cancel_preload(self) -> None:
        if self._preload_request:
            try:
                self._preload_request.cancel()
            except Exception:
                pass
        self._preload_request = None
        self._preloaded_url = None
        self._preloaded_pixmap = None

    def _on_preload_loaded(self, loaded_url: str, pixmap: QPixmap) -> None:
        if loaded_url != self._preloaded_url:
            return
        if pixmap is None or pixmap.isNull():
            self._preloaded_pixmap = None
        else:
            self._preloaded_pixmap = pixmap
        self._preload_request = None

    def _on_preload_failed(self, loaded_url: str, _error: str) -> None:
        if loaded_url != self._preloaded_url:
            return
        self._preloaded_pixmap = None
        self._preloaded_url = None
        self._preload_request = None

    def _take_preloaded_pixmap(self, url: str) -> QPixmap | None:
        if not url:
            return None
        if url != self._preloaded_url:
            return None
        if not self._preloaded_pixmap or self._preloaded_pixmap.isNull():
            return None
        pixmap = self._preloaded_pixmap
        self._preloaded_pixmap = None
        self._preloaded_url = None
        if self._preload_request:
            try:
                self._preload_request.cancel()
            except Exception:
                pass
        self._preload_request = None
        return pixmap

    def _disconnect_image_loader(self):
        # Cancel ImageLoadRequest if active
        if self._image_load_request:
            self._image_load_request.cancel()
            self._image_load_request = None

        # Legacy cleanup for GIF loading
        if self.image_loader:
            try:
                self.image_loader.image_loaded.disconnect(self._on_image_loaded)
            except:
                pass
            try:
                self.image_loader.load_failed.disconnect(self._on_image_failed)
            except:
                pass
            try:
                self.image_loader.image_loaded.disconnect(self._on_gif_loaded)
            except:
                pass
            try:
                self.image_loader.load_failed.disconnect(self._on_gif_failed)
            except:
                pass

    def reset_preview(self):
        self._disconnect_image_loader()
        self.current_url = None
        self.zoom_controls.setVisible(False)
        self._hide_preview_spinner()
        self._hide_fade_overlay()
        if self._fade_anim:
            self._fade_anim.stop()
        if self._zoom_fade_anim:
            self._zoom_fade_anim.stop()
        if self._zoom_fade_effect:
            self._zoom_fade_effect.setOpacity(1.0)
        if hasattr(self, "current_video_player") and self.current_video_player:
            if hasattr(self.current_video_player, "set_nav_parent_hover"):
                self.current_video_player.set_nav_parent_hover(False)
        if hasattr(self.zoom_widget, "clear_pixmap"):
            self.zoom_widget.clear_pixmap()
        self._clear_gif()
        self._cleanup_video()
        self.preview_stack.setCurrentWidget(self.image_container)
        if hasattr(self, "image_stack"):
            self.image_stack.setCurrentWidget(self.zoom_widget)

    def set_post_content(
        self,
        html: str,
        allow_external_media: bool,
        title: str | None = None,
        creator_name: str | None = None,
        creator_id: str | None = None,
        service: str | None = None,
        banner_url: str | None = None,
        avatar_url: str | None = None,
    ) -> None:
        self._post_content = html or ""
        self._allow_external_media = bool(allow_external_media)
        self._content_banner_url = banner_url
        self._content_avatar_url = avatar_url
        self._set_content_profile(creator_name, service, creator_id, title)
        self._content_display_html = self._build_content_display_html()
        self.content_view.set_post_html(self._content_display_html, self._allow_external_media)
        if self._content_popup is not None:
            self._content_popup.set_title(self._content_title)
            self._content_popup.set_profile(
                self._content_creator_name,
                self._content_service,
                self._content_creator_id,
                None,
            )
            self._content_popup.set_profile_images(
                self._content_banner_url,
                self._content_avatar_url,
            )
            self._content_popup.set_html(self._content_display_html, self._allow_external_media)

    def show_content(self):
        if not self._post_content.strip():
            return
        popup = self._ensure_content_popup()
        popup.set_title(self._content_title)
        popup.set_profile(
            self._content_creator_name,
            self._content_service,
            self._content_creator_id,
            None,
        )
        popup.set_profile_images(
            self._content_banner_url,
            self._content_avatar_url,
        )
        popup.set_html(self._content_display_html, self._allow_external_media)
        if popup.isMinimized():
            popup.showNormal()
        if not popup.isVisible():
            self._position_content_popup(popup)
        popup.show()
        popup.raise_()
        popup.activateWindow()

    def hide_content(self) -> bool:
        if self._content_popup is not None and self._content_popup.isVisible():
            self._content_popup.close()
            return True
        return False

    def _show_broken_preview(self):
        icon = qta.icon("fa5s.exclamation-triangle", color="#909090").pixmap(50, 50)
        if hasattr(self.zoom_widget, "set_centered_pixmap"):
            self.zoom_widget.set_centered_pixmap(icon)
        else:
            self.zoom_widget.set_pixmap(icon)
        if hasattr(self, "image_stack"):
            self.image_stack.setCurrentWidget(self.zoom_widget)
        self._hide_preview_spinner()
        QTimer.singleShot(0, self._refresh_image_positions)

    def show_media(self, media_item: MediaItem):
        """Show media item in preview"""
        url = media_item.url
        media_type = media_item.media_type

        if not url:
            return

        if url != self.current_url:
            if media_type != 'video' and self._fade_next_image and self._can_fade_from_current():
                self._disconnect_image_loader()
                self._hide_preview_spinner()
                self._cleanup_video()
            else:
                self.reset_preview()

        self.current_url = url

        if media_type == 'video':
            self._fade_next_image = False
            self._show_video(url)
        else:
            self._show_image(url)

    def _is_gif(self, url: str) -> bool:
        if not url:
            return False
        suffix = Path(urlparse(url).path).suffix.lower()
        return suffix == ".gif"

    def _clear_gif(self):
        if self._gif_movie:
            try:
                self._gif_movie.stop()
            except Exception:
                pass
            try:
                self._gif_movie.frameChanged.disconnect(self._on_gif_frame)
            except Exception:
                pass
        self._gif_movie = None
        self._gif_frame_size = None
        self._gif_scaled_size = None
        self._gif_has_frame = False
        if hasattr(self, "gif_label"):
            self.gif_label.clear()
            self.gif_label.setMovie(None)
            self.gif_label.setVisible(False)

    def _resolve_gif_path(self, url: str) -> Path | None:
        if not url:
            return None
        if url.startswith("file://"):
            return Path(url[7:])
        p = Path(url)
        if p.exists():
            return p
        parsed = urlparse(url)
        suffix = Path(parsed.path).suffix or ".bin"
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        cache_dir = Path.home() / ".coomer-betterui" / "thumbnails" / "media" / "raw"
        return cache_dir / f"{digest}{suffix}"

    def _update_gif_scaled_size(self) -> None:
        if not self._gif_movie:
            return
        frame_size = self._gif_frame_size
        if frame_size is None or frame_size.isEmpty():
            pix = self._gif_movie.currentPixmap()
            if not pix.isNull():
                dpr = pix.devicePixelRatio() or 1.0
                frame_size = QSize(int(pix.width() / dpr), int(pix.height() / dpr))
        if frame_size is None or frame_size.isEmpty():
            return

        if hasattr(self, "image_stack") and self.image_stack.isVisible():
            container = self.image_stack.size()
        else:
            container = self.image_container.size()
        target_w = max(1, container.width())
        target_h = max(1, container.height())
        scale = min(target_w / frame_size.width(), target_h / frame_size.height(), 1.0)
        scaled_w = max(1, int(frame_size.width() * scale))
        scaled_h = max(1, int(frame_size.height() * scale))
        scaled_size = QSize(scaled_w, scaled_h)
        if self._gif_scaled_size == scaled_size:
            return
        self._gif_scaled_size = scaled_size

    def _on_gif_frame(self, _frame: int) -> None:
        if not self._gif_movie:
            return
        pix = self._gif_movie.currentPixmap()
        if pix.isNull():
            return
        dpr = pix.devicePixelRatio() or 1.0
        self._gif_frame_size = QSize(
            int(pix.width() / dpr),
            int(pix.height() / dpr),
        )
        self._update_gif_scaled_size()
        if self._gif_scaled_size:
            scaled = scale_pixmap_to_fit(pix, self._gif_scaled_size)
        else:
            scaled = pix
        self.gif_label.setPixmap(scaled)
        if not self._gif_has_frame:
            self._gif_has_frame = True
            self.gif_label.setVisible(True)
            self._hide_preview_spinner()

    def _show_image(self, url: str):
        """Show image"""
        if self._is_gif(url):
            self._fade_next_image = False
            self._show_gif(url)
            return
        self._fade_pending = False
        if self._consume_fade_request():
            self._fade_pending = self._capture_fade_overlay()
        self._clear_gif()
        if hasattr(self, "image_stack"):
            self.image_stack.setCurrentWidget(self.zoom_widget)
        self.preview_stack.setCurrentWidget(self.image_container)
        QTimer.singleShot(0, self._refresh_image_positions)

        preloaded = self._take_preloaded_pixmap(url)
        if preloaded is not None:
            self._apply_loaded_pixmap(preloaded)
            self._hide_preview_spinner()
            return

        self._show_preview_spinner()

        # Load async using ImageLoadRequest
        self._disconnect_image_loader()
        self._image_load_request = ImageLoadRequest.load(
            url=url,
            target_size=(0, 0),
            on_loaded=self._on_image_loaded,
            on_failed=self._on_image_failed,
            use_preview_loader=True
        )

    def _show_gif(self, url: str):
        self._fade_pending = False
        self._hide_fade_overlay()
        self.preview_stack.setCurrentWidget(self.image_container)
        if hasattr(self, "image_stack"):
            self.image_stack.setCurrentWidget(self.gif_label)
        if hasattr(self, "gif_label"):
            self.gif_label.setVisible(False)
        self.zoom_controls.setVisible(False)
        self._show_preview_spinner()
        QTimer.singleShot(0, self._refresh_image_positions)
        self._clear_gif()

        from src.ui.images.image_loader_manager import get_image_loader_manager
        self._disconnect_image_loader()
        self.image_loader = get_image_loader_manager().preview_loader()
        self.image_loader.image_loaded.connect(self._on_gif_loaded)
        self.image_loader.load_failed.connect(self._on_gif_failed)
        self.image_loader.load_image(url, target_size=(0, 0))

    def _on_image_loaded(self, loaded_url: str, pixmap: QPixmap):
        """Handle image loaded"""
        if loaded_url == self.current_url:
            try:
                if not pixmap.isNull():
                    self._apply_loaded_pixmap(pixmap)
                if hasattr(self, "image_stack"):
                    self.image_stack.setCurrentWidget(self.zoom_widget)
                self._hide_preview_spinner()
                QTimer.singleShot(0, self._refresh_image_positions)
            except RuntimeError:
                pass

    def _on_image_failed(self, loaded_url: str, error: str):
        """Handle image load failure"""
        if loaded_url == self.current_url:
            try:
                # Hide zoom controls on error
                self.zoom_controls.setVisible(False)
                self._show_broken_preview()
                self._hide_preview_spinner()
                self._fade_pending = False
                self._hide_fade_overlay()
                logger.error(f"Failed to load image: {error}")
            except RuntimeError:
                pass

    def _on_gif_loaded(self, loaded_url: str, pixmap: QPixmap):
        if loaded_url != self.current_url:
            return
        try:
            gif_path = self._resolve_gif_path(loaded_url)
            if gif_path and gif_path.exists():
                self._gif_movie = QMovie(str(gif_path))
                self._gif_movie.setCacheMode(QMovie.CacheMode.CacheAll)
                self._gif_frame_size = None
                self._gif_has_frame = False
                self._gif_movie.frameChanged.connect(self._on_gif_frame)
                self._gif_movie.start()
                if hasattr(self, "image_stack"):
                    self.image_stack.setCurrentWidget(self.gif_label)
                QTimer.singleShot(0, self._refresh_image_positions)
                return

            if not pixmap.isNull():
                self._clear_gif()
                self._apply_loaded_pixmap(pixmap)
                if hasattr(self, "image_stack"):
                    self.image_stack.setCurrentWidget(self.zoom_widget)
            self._hide_preview_spinner()
            QTimer.singleShot(0, self._refresh_image_positions)
        except RuntimeError:
            pass

    def _on_gif_failed(self, loaded_url: str, error: str):
        if loaded_url != self.current_url:
            return
        try:
            self._clear_gif()
            self.zoom_controls.setVisible(False)
            self._show_broken_preview()
            self._hide_preview_spinner()
            self._fade_pending = False
            self._hide_fade_overlay()
            logger.error(f"Failed to load GIF: {error}")
        except RuntimeError:
            pass

    def _show_video(self, url: str):
        self._fade_pending = False
        self._hide_fade_overlay()
        self._clear_gif()
        self.preview_stack.setCurrentWidget(self.video_container)
        self.zoom_controls.setVisible(False)
        self._hide_preview_spinner()

        self._cleanup_video()

        if self._db and self._core_context:
            try:
                enabled = self._db.get_config("enable_range_proxy", "false") == "true"
            except Exception:
                enabled = False
            if enabled:
                try:
                    logger.debug(f"_show_video original URL: {url} (len={len(url)})")
                    url = self._core_context.range_proxy.proxy_url(url)
                except Exception:
                    pass

        from src.ui.video.video_player import VideoPlayerWidget
        video_player = VideoPlayerWidget(url, parent=self.video_container, core_context=self._core_context)

        video_player.setMouseTracking(True)
        self.video_container.setMouseTracking(True)
        self.preview_stack.setMouseTracking(True)

        self.video_container.layout().addWidget(video_player)
        self.current_video_player = video_player
        if self.underMouse() and hasattr(video_player, "set_nav_parent_hover"):
            video_player.set_nav_parent_hover(True)
        if self._sidebar_toggle_overlay_handler:
            try:
                overlay_root = video_player.get_overlay_root() if hasattr(video_player, "get_overlay_root") else None
                if overlay_root is not None:
                    self._sidebar_toggle_overlay_handler(overlay_root)
                else:
                    player_ref = weakref.ref(video_player)
                    def _retry():
                        player = player_ref()
                        if not player:
                            return
                        root = player.get_overlay_root() if hasattr(player, "get_overlay_root") else None
                        self._sidebar_toggle_overlay_handler(root)
                    QTimer.singleShot(0, _retry)
            except Exception:
                pass

    def _cleanup_video(self):
        """Cleanup video player resources"""
        if self._sidebar_toggle_overlay_handler:
            try:
                self._sidebar_toggle_overlay_handler(None)
            except Exception:
                pass
        while self.video_container.layout().count():
            item = self.video_container.layout().takeAt(0)
            if item.widget():
                widget = item.widget()
                # Call cleanup if available
                if hasattr(widget, 'cleanup'):
                    widget.cleanup()
                widget.deleteLater()

    def cleanup(self):
        """Cleanup all media resources"""
        # Disconnect image loader signals
        self._disconnect_image_loader()

        # Cleanup video
        self._cleanup_video()

        if self._content_popup is not None:
            try:
                self._content_popup.close()
            except Exception:
                pass

        logger.debug("MediaPreviewWidget cleaned up")


class MediaThumbnailGrid(QWidget):
    """Tight responsive thumbnail grid (bottom half)"""

    thumbnail_clicked = pyqtSignal(int, object)  # index, media_item

    def __init__(self, parent=None):
        super().__init__(parent)
        self.media_items = []
        self.selected_index = 0
        self.thumbnail_widgets = []
        self._thumb_size = 112
        self._thumb_spacing = 4
        self._thumb_margin = 4
        self._sort_mode = "default"
        self._sort_ascending = True
        self._duration_overrides = {}
        self._display_map = []
        self._media_signature = ()
        
        # Debounce timer for resize events to reduce layout thrashing
        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(100)  # 100ms debounce
        self._resize_timer.timeout.connect(self._do_resize_layout)
        
        self._setup_ui()

    def _setup_ui(self):
        """Setup UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Scrollable grid
        scroll = QScrollArea()
        scroll.setObjectName("thumbnailScrollArea")
        scroll.setStyleSheet(
            f"""
            QScrollArea#thumbnailScrollArea {{
                background-color: {Colors.BG_DARKEST};
                border: none;
            }}
            QScrollArea#thumbnailScrollArea QWidget {{
                background-color: {Colors.BG_DARKEST};
            }}
            """
        )
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setContentsMargins(0, 0, 0, 0)
        scroll.setViewportMargins(0, 0, 0, 0)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        grid_container = QWidget()
        grid_container.setStyleSheet(f"background-color: {Colors.BG_DARKEST};")
        self.grid_layout = QGridLayout(grid_container)
        self.grid_layout.setSpacing(self._thumb_spacing)
        self.grid_layout.setContentsMargins(
            self._thumb_margin,
            self._thumb_margin,
            self._thumb_margin,
            self._thumb_margin,
        )
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        scroll.setWidget(grid_container)
        layout.addWidget(scroll)

    def set_media_items(self, media_items: List[MediaItem], selected_index: int = 0):
        """Set media items and create thumbnails"""
        signature = tuple(item.url for item in media_items)
        if signature != self._media_signature:
            self._duration_overrides = {}
            self._media_signature = signature
        self.media_items = media_items
        if media_items and not (0 <= selected_index < len(media_items)):
            selected_index = 0
        self.selected_index = selected_index
        self._display_map = self._build_display_map()

        # Batch updates to prevent white flashes
        self.setUpdatesEnabled(False)
        try:
            # Clear existing
            for widget in self.thumbnail_widgets:
                widget.setParent(None)
                widget.deleteLater()
            self.thumbnail_widgets.clear()

            # Calculate responsive columns based on width
            # Each thumbnail is size + spacing
            available_width = self.width() - (self._thumb_margin * 2)
            cell_size = self._thumb_size + self._thumb_spacing
            max_cols = max(1, (available_width + self._thumb_spacing) // cell_size)

            # Create thumbnails
            row = 0
            col = 0

            for media_index, media_item in self._display_map:
                thumb = self._create_thumbnail(media_item, media_index)
                self.grid_layout.addWidget(thumb, row, col)
                self.thumbnail_widgets.append(thumb)

                col += 1
                if col >= max_cols:
                    col = 0
                    row += 1
        finally:
            self.setUpdatesEnabled(True)

    def get_display_order(self) -> List[int]:
        if not self.media_items:
            return []
        if not self._display_map:
            self._display_map = self._build_display_map()
        return [index for index, _ in self._display_map]

    def set_sort_mode(self, mode: str, ascending: bool = True) -> None:
        mode = mode or "default"
        if mode not in ("default", "type", "duration"):
            mode = "default"
        self._sort_mode = mode
        self._sort_ascending = bool(ascending)
        self.set_media_items(self.media_items, self.selected_index)

    def reset_sort(self) -> None:
        self._sort_mode = "default"
        self._sort_ascending = True
        self.set_media_items(self.media_items, self.selected_index)

    def _build_display_map(self) -> List[tuple[int, MediaItem]]:
        items = list(enumerate(self.media_items))
        if not items:
            return []

        if self._sort_mode == "type":
            order_list = ["image", "video", "file", "text"]
            if not self._sort_ascending:
                order_list = list(reversed(order_list))
            order = {name: i for i, name in enumerate(order_list)}
            items.sort(key=lambda t: (order.get(t[1].media_type, 99), t[0]))
            return items

        if self._sort_mode == "duration":
            def _duration_value(index: int, item: MediaItem) -> float | None:
                value = item.duration
                if value is None or value <= 0:
                    value = self._duration_overrides.get(index)
                return value

            def _key(t):
                index, item = t
                duration = _duration_value(index, item)
                missing = duration is None or duration <= 0
                if self._sort_ascending:
                    return (missing, duration or 0.0, index)
                return (missing, -(duration or 0.0), index)

            items.sort(key=_key)
            return items

        items.sort(key=lambda t: t[0])
        return items

    def _create_thumbnail(self, media_item: MediaItem, index: int) -> QWidget:
        """Create thumbnail widget"""
        thumb = QFrame()
        thumb.setFixedSize(self._thumb_size, self._thumb_size)
        thumb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        thumb.setObjectName("thumbnailCard")
        is_selected = index == self.selected_index
        layout = QVBoxLayout(thumb)
        layout.setContentsMargins(0, 0, 0, 0) #
        # Thumbnail image
        thumb_label = QLabel()
        thumb_label.setObjectName("thumbnailImage") #
        thumb_label.setFixedSize(self._thumb_size, self._thumb_size) #
        thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter) #
        thumb_label.setScaledContents(False) #
        thumb_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        overlay = QLabel(thumb_label)
        overlay.setObjectName("thumbnailOverlay")
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        overlay.setGeometry(0, 0, self._thumb_size, self._thumb_size)
        overlay.lower()
        type_badge = QLabel(thumb_label)
        type_badge.setObjectName("thumbnailTypeBadge")
        type_badge.setFixedSize(22, 22)
        type_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        type_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        type_badge.setStyleSheet(
            "QLabel#thumbnailTypeBadge { "
            "border-radius: 11px; "
            "}"
        )
        # Load thumbnail
        media_type = media_item.media_type  #
        url = media_item.url  #
        badge_color = "rgba(155, 89, 182, 1)"
        overlay_color = "rgba(155, 89, 182, 0.22)"
        overlay_color_selected = "rgba(155, 89, 182, 0.36)"
        video_icon_pixmap = None
        duration_label = None

        if media_type == 'video':
            type_badge.setPixmap(qta.icon('fa5s.film', color='#ffffff').pixmap(12, 12))
            badge_color = "rgba(255, 107, 53, 1)"
            overlay_color = "rgba(255, 107, 53, 0.24)"
            overlay_color_selected = "rgba(255, 107, 53, 0.40)"
            video_icon_pixmap = qta.icon('fa5s.film', color='#ff6b35').pixmap(48, 48)
            thumb_label.setPixmap(video_icon_pixmap)
            duration_label = QLabel(thumb_label)
            duration_label.setObjectName("thumbnailDurationBadge")
            duration_label.setVisible(False)
            duration_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            duration_label.setStyleSheet(
                "background-color: rgba(0, 0, 0, 0.80);"
                "border-radius: 4px;"
                "padding: 2px 6px;"
                "color: #ffffff;"
                "font-size: 11px;"
                "font-weight: 600;"
            )
        elif media_type == 'image':
            type_badge.setPixmap(qta.icon('fa5s.image', color='#ffffff').pixmap(12, 12))
            badge_color = "rgba(74, 158, 255, 1)"
            overlay_color = "rgba(74, 158, 255, 0.20)"
            overlay_color_selected = "rgba(74, 158, 255, 0.34)"
        else:
            type_badge.setPixmap(qta.icon('fa5s.file', color='#ffffff').pixmap(12, 12))

        self._apply_thumbnail_style(thumb, is_selected)

        thumb._thumb_label = thumb_label
        thumb._thumb_loaded = False
        thumb._thumb_loading = False
        thumb._thumb_spinner = None
        thumb._thumb_overlay = overlay
        thumb._duration_label = duration_label
        thumb._media_index = index
        thumb._overlay_hover_color = overlay_color
        thumb._overlay_selected_color = overlay_color_selected
        thumb._thumb_hovered = False

        def _make_thumb_pixmap(pixmap: QPixmap) -> QPixmap:
            w, h = self._thumb_size, self._thumb_size
            final_thumb = QPixmap(w, h)
            final_thumb.fill(Qt.GlobalColor.transparent)
            final_thumb = scale_and_crop_pixmap(pixmap, (w, h))
            return final_thumb

        def _apply_thumb_pixmap(pixmap: QPixmap):
            if pixmap is None or pixmap.isNull():
                return None
            final_thumb = _make_thumb_pixmap(pixmap)
            thumb_label.setPixmap(final_thumb)
            thumb._thumb_loaded = True
            thumb._thumb_loading = False
            if thumb._thumb_spinner:
                try:
                    thumb._thumb_spinner.stop()
                except RuntimeError:
                    pass
            overlay.lower()
            type_badge.raise_()
            if duration_label:
                duration_label.raise_()
            return final_thumb

        thumb._thumb_apply_pixmap = _apply_thumb_pixmap

        thumb_spinner = None

        def _ensure_spinner():
            nonlocal thumb_spinner
            if thumb_spinner is None:
                thumb_spinner = SpinnerWidget(thumb_label, size=50, color="#909090")
                thumb_spinner.move(
                    max(0, (thumb_label.width() - thumb_spinner.width()) // 2),
                    max(0, (thumb_label.height() - thumb_spinner.height()) // 2),
                )
                thumb_spinner.raise_()
            thumb._thumb_spinner = thumb_spinner
            return thumb_spinner

        def _cached_video_thumb_path() -> Path | None:
            if media_type != "video" or not url:
                return None
            digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
            cache_dir = Path.home() / ".coomer-betterui" / "thumbnails"
            return cache_dir / f"{digest}_{self._thumb_size}x{self._thumb_size}.png"

        def _try_load_cached_video_thumb() -> bool:
            cache_path = _cached_video_thumb_path()
            if not cache_path or not cache_path.exists():
                return False
            pixmap = QPixmap(str(cache_path))
            if pixmap.isNull():
                return False
            try:
                _apply_thumb_pixmap(pixmap)
            except RuntimeError:
                return False
            return True

        def _start_thumbnail_load():
            if media_type not in ("image", "video") or not url:
                return
            if media_type == "video":
                if _try_load_cached_video_thumb():
                    return
            if thumb._thumb_loaded or thumb._thumb_loading:
                return
            thumb._thumb_loading = True
            spinner = _ensure_spinner()
            spinner.start()
            type_badge.raise_()

            def _cleanup():
                if spinner:
                    try:
                        spinner.stop()
                    except RuntimeError:
                        pass
                thumb._thumb_loading = False

            def on_thumb_loaded(_, pixmap):
                if not pixmap.isNull():
                    try:
                        _apply_thumb_pixmap(pixmap)
                    except RuntimeError:
                        # Handle case where thumb_label might be deleted
                        pass
                _cleanup()

            def on_thumb_failed(*_):
                try:
                    if media_type == "video" and video_icon_pixmap:
                        thumb_label.setPixmap(video_icon_pixmap)
                    else:
                        thumb_label.setPixmap(
                            qta.icon("fa5s.exclamation-triangle", color="#909090").pixmap(50, 50)
                        )
                except RuntimeError:
                    pass
                _cleanup()

            # Load using ImageLoadRequest (auto-cleanup)
            thumb._load_request = ImageLoadRequest.load(
                url=url,
                target_size=(self._thumb_size, self._thumb_size),
                on_loaded=on_thumb_loaded,
                on_failed=on_thumb_failed,
                use_preview_loader=False  # Use grid loader
            )

        thumb._thumb_start_load = _start_thumbnail_load
        if media_type in ("image", "video"):
            _start_thumbnail_load()
        if not type_badge.pixmap():
            type_badge.setPixmap(qta.icon('fa5s.file', color='#ffffff').pixmap(12, 12))
            badge_color = "rgba(155, 89, 182, 1)"
        type_badge.setStyleSheet(
            f"QLabel#thumbnailTypeBadge {{ background-color: {badge_color}; border-radius: 11px; }}"
        )
        type_badge.move(thumb_label.width() - type_badge.width() - 6, 6)
        type_badge.raise_()
        if duration_label:
            duration_value = getattr(media_item, "duration", None)
            if duration_value is None or duration_value <= 0:
                duration_value = self._duration_overrides.get(index)
            self._set_duration_label(duration_label, duration_value)

        layout.addWidget(thumb_label) #
        self._update_overlay_state(thumb)

        def _on_enter(_event):
            thumb._thumb_hovered = True
            self._update_overlay_state(thumb)

        def _on_leave(_event):
            thumb._thumb_hovered = False
            self._update_overlay_state(thumb)

        thumb.enterEvent = _on_enter
        thumb.leaveEvent = _on_leave
        # Click handler
        thumb.mousePressEvent = lambda e: self._on_thumbnail_clicked(index, media_item, thumb)
        return thumb

    def _on_thumbnail_clicked(self, index: int, media_item: MediaItem, thumb_widget: QWidget):
        """Handle thumbnail click"""
        # Update selection
        old_index = self.selected_index
        self.selected_index = index

        # Update styling
        if old_index != index:
            old_widget = self._find_thumb_by_media_index(old_index)
            if old_widget is not None:
                self._set_thumbnail_selected(old_widget, False)
        self._set_thumbnail_selected(thumb_widget, True)

        if media_item.media_type != "video" and hasattr(thumb_widget, "_thumb_start_load"):
            try:
                thumb_widget._thumb_start_load()
            except Exception:
                pass

        # Emit signal
        self.thumbnail_clicked.emit(index, media_item)

    def resizeEvent(self, event):
        """Handle window resize to update responsive grid (debounced)"""
        super().resizeEvent(event)
        if hasattr(self, 'media_items') and self.media_items:
            # Restart timer on each resize event - only recalculate after 100ms of no resizing
            self._resize_timer.start()
    
    def _do_resize_layout(self):
        """Perform actual grid recalculation after debounce period."""
        if hasattr(self, 'media_items') and self.media_items:
            # Recalculate grid layout
            self.set_media_items(self.media_items, self.selected_index)

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        """Format seconds into human-readable duration (e.g. '3:24' or '1:02:15')."""
        sec = int(seconds)
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def _set_duration_label(self, label: QLabel | None, seconds: float | None) -> None:
        if label is None:
            return
        if seconds is None or seconds <= 0:
            label.setVisible(False)
            return
        text = self._fmt_duration(seconds)
        label.setText(text)
        fm = label.fontMetrics()
        label.setFixedSize(fm.horizontalAdvance(text) + 12, fm.height() + 4)
        label.setVisible(True)
        label.move(6, self._thumb_size - label.height() - 6)
        label.raise_()

    def update_video_duration(self, index: int, duration: float) -> None:
        if not (0 <= index < len(self.media_items)):
            return
        self._duration_overrides[index] = duration
        thumb = self._find_thumb_by_media_index(index)
        if thumb is None:
            return
        label = getattr(thumb, "_duration_label", None)
        if label is None:
            return
        self._set_duration_label(label, duration)

    def _apply_thumbnail_style(self, thumb: QFrame, selected: bool) -> None:
        thumb.setProperty("selected", selected)
        thumb.setStyleSheet(
            f"""
            QFrame {{
                background-color: {Colors.BG_HOVER};
                border: 0px solid transparent;
                border-radius: 0px;
            }}
            QLabel#thumbnailImage {{
                background-color: {Colors.BG_HOVER};
                border-radius: 0px;
            }}
            """
        )
        thumb.style().unpolish(thumb)
        thumb.style().polish(thumb)

    def _set_thumbnail_selected(self, thumb: QWidget, selected: bool) -> None:
        if thumb is None:
            return
        thumb.setProperty("selected", selected)
        thumb.style().unpolish(thumb)
        thumb.style().polish(thumb)
        self._update_overlay_state(thumb)

    def _update_overlay_state(self, thumb: QWidget) -> None:
        overlay = getattr(thumb, "_thumb_overlay", None)
        if overlay is None:
            return
        is_hovered = bool(getattr(thumb, "_thumb_hovered", False))
        is_selected = bool(thumb.property("selected"))
        if is_hovered:
            color = getattr(thumb, "_overlay_hover_color", "rgba(0, 0, 0, 0)")
        elif is_selected:
            color = getattr(thumb, "_overlay_selected_color", "rgba(0, 0, 0, 0)")
        else:
            color = "rgba(0, 0, 0, 0)"
        overlay.setStyleSheet(f"background-color: {color};")

    def _find_thumb_by_media_index(self, media_index: int) -> QWidget | None:
        for thumb in self.thumbnail_widgets:
            if getattr(thumb, "_media_index", None) == media_index:
                return thumb
        return None

    def get_widget_for_media_index(self, media_index: int) -> QWidget | None:
        return self._find_thumb_by_media_index(media_index)


class _ThumbnailSplitterHandle(QSplitterHandle):
    def __init__(self, orientation: Qt.Orientation, parent=None):
        super().__init__(orientation, parent)
        self._toggle_btn = QToolButton(self)
        self._toggle_btn.setObjectName("thumbnailToggleButton")
        self._toggle_btn.setAutoRaise(True)
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setFixedSize(18, 18)
        self._toggle_btn.setStyleSheet(
            f"QToolButton {{ border: none; }}"
            f"QToolButton:hover {{ background-color: {Colors.BG_HOVER}; border-radius: 9px; }}"
        )
        self._toggle_btn.clicked.connect(self._on_toggle_clicked)
        self._update_icon(collapsed=False)

        if orientation == Qt.Orientation.Vertical:
            layout = QHBoxLayout(self)
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            layout = QVBoxLayout(self)
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addStretch()
        layout.addWidget(self._toggle_btn)
        layout.addStretch()

    def _on_toggle_clicked(self) -> None:
        splitter = self.splitter()
        if hasattr(splitter, "toggle_thumbnails"):
            splitter.toggle_thumbnails()

    def set_collapsed(self, collapsed: bool) -> None:
        self._update_icon(collapsed)

    def _update_icon(self, collapsed: bool) -> None:
        if self.orientation() == Qt.Orientation.Vertical:
            icon_name = "fa5s.chevron-up" if collapsed else "fa5s.chevron-down"
        else:
            icon_name = "fa5s.chevron-left" if collapsed else "fa5s.chevron-right"
        self._toggle_btn.setIcon(qta.icon(icon_name, color="#909090"))


class _ThumbnailSplitter(QSplitter):
    def __init__(self, orientation: Qt.Orientation, parent=None):
        super().__init__(orientation, parent)
        self._handle = None
        self._toggle_callback = None

    def createHandle(self) -> QSplitterHandle:
        self._handle = _ThumbnailSplitterHandle(self.orientation(), self)
        return self._handle

    def set_toggle_callback(self, callback) -> None:
        self._toggle_callback = callback

    def toggle_thumbnails(self) -> None:
        if self._toggle_callback:
            self._toggle_callback()

    def set_collapsed_state(self, collapsed: bool) -> None:
        if self._handle:
            self._handle.set_collapsed(collapsed)


class _GalleryDurationTarget:
    def __init__(self, owner, media_index: int, url: str):
        self._owner_ref = weakref.ref(owner)
        self._media_index = media_index
        self._url = url

    def _on_duration_probed(self, duration: float) -> None:
        owner = self._owner_ref()
        if owner is None:
            return
        owner._on_gallery_duration_probed(self._media_index, self._url, duration)


class _GallerySidePanel(QWidget):
    closed = pyqtSignal()
    tab_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_width = FileSidebar.WIDTH_DEFAULT
        self._resizing = False
        self._resize_start_x = 0
        self._resize_start_width = 0
        self._last_resize_width = 0
        self._tab_widgets = {}
        self._active_tab = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setObjectName("gallerySidePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(0)
        self.setMaximumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("gallerySideTabsHeader")
        header.setStyleSheet(
            f"background-color: {Colors.BG_HOVER}; "
            f"border-bottom: 1px solid {Colors.BG_PRIMARY};"
        )
        header.setFixedHeight(41)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)

        self.files_tab_btn = QPushButton("Files")
        self.files_tab_btn.setObjectName("galleryTabButton")
        self.files_tab_btn.setFixedHeight(40)
        self.files_tab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.files_tab_btn.clicked.connect(lambda: self.set_active_tab("Files"))
        header_layout.addWidget(self.files_tab_btn, 1)

        self.gallery_tab_btn = QPushButton("Gallery")
        self.gallery_tab_btn.setObjectName("galleryTabButton")
        self.gallery_tab_btn.setFixedHeight(40)
        self.gallery_tab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gallery_tab_btn.clicked.connect(lambda: self.set_active_tab("Gallery"))
        header_layout.addWidget(self.gallery_tab_btn, 1)

        layout.addWidget(header)

        self.stack = QStackedWidget()
        self.stack.setObjectName("gallerySideStack")
        layout.addWidget(self.stack, 1)

        self.setMouseTracking(True)
        self.setStyleSheet(
            f"QWidget#gallerySidePanel {{ "
            f"background-color: {Colors.BG_SECONDARY}; "
            f"border-left: 1px solid {Colors.BORDER_LIGHT}; "
            f"}}"
        )
        self._update_tab_styles()

    def _update_tab_styles(self) -> None:
        active_style = f"""
            QPushButton#galleryTabButton {{
                background-color: {Colors.BG_SECONDARY};
                border: none;
                border-radius: 0px;
                color: {Colors.ACCENT_PRIMARY};
                font-weight: {Fonts.WEIGHT_SEMIBOLD};
                font-size: {Fonts.SIZE_SM}px;
            }}
        """
        inactive_style = f"""
            QPushButton#galleryTabButton {{
                background-color: {Colors.BG_HOVER};
                border: none;
                border-radius: 0px;
                color: {Colors.TEXT_MUTED};
                font-weight: {Fonts.WEIGHT_MEDIUM};
                font-size: {Fonts.SIZE_SM}px;
            }}
            QPushButton#galleryTabButton:hover {{
                background-color: {Colors.BG_TERTIARY};
                color: {Colors.TEXT_SECONDARY};
            }}
        """

        if self._active_tab == "Gallery":
            self.gallery_tab_btn.setStyleSheet(active_style)
            self.files_tab_btn.setStyleSheet(inactive_style)
        else:
            self.files_tab_btn.setStyleSheet(active_style)
            self.gallery_tab_btn.setStyleSheet(inactive_style)

    def add_tab(self, widget: QWidget, title: str) -> None:
        if title in self._tab_widgets:
            return
        self._tab_widgets[title] = widget
        self.stack.addWidget(widget)
        if self._active_tab is None:
            self.set_active_tab(title)

    def set_active_tab(self, title: str) -> None:
        widget = self._tab_widgets.get(title)
        if widget is None:
            return
        self._active_tab = title
        self.stack.setCurrentWidget(widget)
        self._update_tab_styles()
        self.tab_changed.emit(title)

    def active_tab(self) -> str | None:
        return self._active_tab

    def show_panel(self) -> None:
        self.setVisible(True)
        self.setFixedWidth(self._current_width)

    def hide_panel(self) -> None:
        width = self.width()
        if width > 0:
            self._current_width = max(FileSidebar.WIDTH_MIN, min(FileSidebar.WIDTH_MAX, width))
        self.setVisible(False)
        self.closed.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if event.pos().x() <= FileSidebar.RESIZE_HANDLE:
                self._resizing = True
                self._resize_start_x = event.globalPosition().x()
                self._resize_start_width = self.width()
                self._last_resize_width = self.width()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing:
            delta = self._resize_start_x - event.globalPosition().x()
            new_width = self._resize_start_width + delta
            new_width = int(max(FileSidebar.WIDTH_MIN, min(FileSidebar.WIDTH_MAX, new_width)))
            if abs(new_width - self._last_resize_width) >= 1:
                self.setFixedWidth(new_width)
                self._last_resize_width = new_width
            event.accept()
        elif event.pos().x() <= FileSidebar.RESIZE_HANDLE:
            self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._resizing:
            self._resizing = False
            self._current_width = self.width()
            self.setFixedWidth(self._current_width)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _SidePanelResizeHandle(QFrame):
    def __init__(self, side_panel: _GallerySidePanel, parent=None):
        super().__init__(parent)
        self._side_panel = side_panel
        self._resizing = False
        self._resize_start_x = 0
        self._resize_start_width = 0
        self.setFixedWidth(FileSidebar.RESIZE_HANDLE)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setMouseTracking(True)
        self.setObjectName("gallerySideSeparator")
        self.setStyleSheet(
            f"""
            QFrame#gallerySideSeparator {{
                background-color: transparent;
                border-right: 1px solid {Colors.BORDER_LIGHT};
            }}
            """
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._resizing = True
            self._resize_start_x = event.globalPosition().x()
            self._resize_start_width = self._side_panel.width()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing:
            delta = self._resize_start_x - event.globalPosition().x()
            new_width = self._resize_start_width + delta
            new_width = int(max(FileSidebar.WIDTH_MIN, min(FileSidebar.WIDTH_MAX, new_width)))
            self._side_panel.setFixedWidth(new_width)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._resizing:
            self._resizing = False
            if hasattr(self._side_panel, "_current_width"):
                self._side_panel._current_width = self._side_panel.width()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class GalleryPostView(QWidget):
    """
    Gallery-focused post detail view
    Top: Large media preview (60-70%)
    Bottom: Tight thumbnail grid (30-40%)
    """

    back_clicked = pyqtSignal()
    download_clicked = pyqtSignal(list)  # URLs to download
    creator_requested = pyqtSignal(object)  # creator data dict

    def __init__(self, parent=None, *, db_manager=None, cache_dir: Path = None, core_context=None):
        super().__init__(parent)
        self.post_data = None
        self.media_items = []
        self.attachment_files = []
        self.current_media_index = 0
        self._nav_order = []
        self._nav_index_map = {}
        self._showing_content = False
        self._post_content = ""
        self._content_only_mode = False
        self._content_only_width = 760
        self._content_only_banner_height = 140
        self._content_only_avatar_size = 48
        self._autoplay_enabled = False
        self._autoplay_timer = QTimer(self)
        self._autoplay_timer.setSingleShot(True)
        self._autoplay_timer.timeout.connect(self._autoplay_advance)
        self._autoplay_image_delay_ms = 10000
        self._autoplay_video_player = None
        self._thumbnails_collapsed = False
        self._thumbnail_splitter_sizes = []
        self._splitter_update_in_progress = False
        self._gallery_sidebar_mode = False
        self._saved_thumbnail_collapsed = False
        self._video_thumb_player = None
        self._video_thumb_target_seconds = None
        self._video_thumb_capture_in_progress = False
        self._nav_video_player = None
        self._duration_targets = []
        self._sidebar_toggle_overlay_handler = None
        self.db = db_manager
        self._core_context = core_context
        if cache_dir is None:
            cache_dir = Path.home() / ".coomer-betterui" / "thumbnails"
        self.cache_dir = cache_dir
        self._creator_lookup = None
        self._setup_ui()

    def _setup_ui(self):
        """Setup UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header with title and controls
        header = self._create_header()
        layout.addWidget(header)

        # Splitter for resizable preview/thumbnails
        self.splitter = _ThumbnailSplitter(Qt.Orientation.Vertical)
        self.splitter.setMouseTracking(True)
        self.splitter.setChildrenCollapsible(True)
        self.splitter.setHandleWidth(18)
        self.splitter.setStyleSheet(f"""
            QSplitter::handle {{
                background-color: {Colors.BORDER_DEFAULT};
            }}
            QSplitter::handle:hover {{
                background-color: {Colors.ACCENT_PRIMARY};
            }}
        """)
        self.splitter.set_toggle_callback(self._toggle_thumbnail_grid)

        # Top area: Media preview + info bar
        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)

        # Media preview
        self.media_preview = MediaPreviewWidget(db_manager=self.db, core_context=self._core_context)
        self.media_preview.prev_requested.connect(self._show_previous)
        self.media_preview.next_requested.connect(self._show_next)
        self.media_preview.content_popup_closed.connect(self._on_content_popup_closed)
        top_layout.addWidget(self.media_preview)

        # Info bar
        info_bar = self._create_info_bar()
        top_layout.addWidget(info_bar)

        self.splitter.addWidget(top_widget)

        # Gallery panel (sort controls + thumbnail grid)
        self.gallery_panel = self._create_gallery_panel()

        # Bottom area: Gallery panel
        self._bottom_widget = QWidget()
        self._bottom_gallery_layout = QVBoxLayout(self._bottom_widget)
        self._bottom_gallery_layout.setContentsMargins(0, 0, 0, 0)
        self._bottom_gallery_layout.setSpacing(0)
        self._bottom_gallery_layout.addWidget(self.gallery_panel, 1)

        self.splitter.addWidget(self._bottom_widget)

        # Set initial splitter sizes (preview: 650px, thumbnails: 250px)
        self.splitter.setSizes([650, 250])
        self._thumbnail_splitter_sizes = self.splitter.sizes()
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        self.splitter.set_collapsed_state(self._thumbnails_collapsed)

        # Create file browser (embedded in right sidebar tabs)
        self.file_browser = FileBrowserSidebar(db_manager=self.db, embedded=True)
        self.file_browser.download_requested.connect(self._on_files_download)
        self.file_browser.jdownloader_requested.connect(self._on_files_jdownloader)
        
        # Create right sidebar (tabs: Files / Gallery)
        self.side_panel = _GallerySidePanel()
        self.side_panel.add_tab(self.file_browser, "Files")
        self._gallery_tab_container = QWidget()
        self._gallery_tab_container.setObjectName("galleryTabContainer")
        self._gallery_tab_container.setStyleSheet(
            f"""
            QWidget#galleryTabContainer {{
                border-left: 1px solid {Colors.BORDER_LIGHT};
                background-color: {Colors.BG_SECONDARY};
            }}
            """
        )
        self._gallery_tab_layout = QVBoxLayout(self._gallery_tab_container)
        self._gallery_tab_layout.setContentsMargins(0, 0, 0, 0)
        self._gallery_tab_layout.setSpacing(0)
        self._gallery_tab_placeholder = QLabel("Enable 'Grid Right' to show thumbnails here.")
        self._gallery_tab_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._gallery_tab_placeholder.setStyleSheet(f"color: {Colors.TEXT_MUTED};")
        self._gallery_tab_layout.addWidget(self._gallery_tab_placeholder, 1)
        self.side_panel.add_tab(self._gallery_tab_container, "Gallery")
        self.side_panel.hide_panel()
        self.side_panel.closed.connect(self._on_sidebar_closed)
        self.side_panel.tab_changed.connect(self._on_side_panel_tab_changed)

        self._side_panel_separator = _SidePanelResizeHandle(self.side_panel)
        self._side_panel_separator.setVisible(False)
        
        # Layout for splitter + sidebar (normal mode)
        self.body_stack = QStackedWidget()
        self.body_stack.setObjectName("detailBodyStack")

        self._normal_body = QWidget()
        content_layout = QHBoxLayout(self._normal_body)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.splitter, 1)
        content_layout.addWidget(self._side_panel_separator)
        content_layout.addWidget(self.side_panel)
        self.body_stack.addWidget(self._normal_body)

        # Content-only mode (centered fixed width)
        self._content_only_body = QWidget()
        content_only_layout = QVBoxLayout(self._content_only_body)
        content_only_layout.setContentsMargins(0, 0, 0, 0)
        content_only_layout.setSpacing(0)

        self.content_only_scroll = QScrollArea()
        self.content_only_scroll.setObjectName("contentOnlyScroll")
        self.content_only_scroll.setWidgetResizable(True)
        self.content_only_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content_only_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_only_scroll.setStyleSheet(
            f"""
            QScrollArea#contentOnlyScroll {{
                background-color: {Colors.BG_PRIMARY};
                border: none;
            }}
            QScrollArea#contentOnlyScroll QWidget {{
                background-color: {Colors.BG_PRIMARY};
            }}
            """
        )

        content_only_holder = QWidget()
        holder_layout = QVBoxLayout(content_only_holder)
        holder_layout.setContentsMargins(0, Spacing.XL, 0, Spacing.XL)
        holder_layout.setSpacing(Spacing.MD)

        profile_row = QWidget()
        profile_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        profile_row_layout = QHBoxLayout(profile_row)
        profile_row_layout.setContentsMargins(Spacing.XXL, 0, Spacing.XXL, 0)
        profile_row_layout.setSpacing(0)
        profile_row_layout.addStretch()

        self.content_only_profile = QFrame()
        self.content_only_profile.setObjectName("contentOnlyProfile")
        self.content_only_profile.setFixedWidth(self._content_only_width)
        self.content_only_profile.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.content_only_profile.setStyleSheet(
            f"""
            QFrame#contentOnlyProfile {{
                background-color: {Colors.BG_TERTIARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_XL}px;
            }}
            QLabel#contentOnlyProfileBanner {{
                border-bottom: 1px solid {Colors.BORDER_DEFAULT};
            }}
            QLabel#contentOnlyProfileName {{
                color: {Colors.TEXT_PRIMARY};
                font-size: {Fonts.SIZE_LG}px;
                font-weight: {Fonts.WEIGHT_SEMIBOLD};
            }}
            QLabel#contentOnlyProfileMeta {{
                color: {Colors.TEXT_SECONDARY};
                font-size: {Fonts.SIZE_SM}px;
            }}
            QLabel#contentOnlyProfileTitle {{
                color: {Colors.TEXT_PRIMARY};
                font-size: {Fonts.SIZE_MD}px;
            }}
            """
        )
        profile_layout = QVBoxLayout(self.content_only_profile)
        profile_layout.setContentsMargins(0, 0, 0, 0)
        profile_layout.setSpacing(0)

        self.content_only_profile_banner = AsyncImageLabel(self.content_only_profile)
        self.content_only_profile_banner.setObjectName("contentOnlyProfileBanner")
        self.content_only_profile_banner.setFixedHeight(self._content_only_banner_height)
        self.content_only_profile_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content_only_profile_banner.setStyleSheet(f"background-color: {Colors.BG_TERTIARY};")
        profile_layout.addWidget(self.content_only_profile_banner)

        profile_content = QWidget(self.content_only_profile)
        profile_content_layout = QVBoxLayout(profile_content)
        profile_content_layout.setContentsMargins(Spacing.LG, Spacing.MD, Spacing.LG, Spacing.MD)
        profile_content_layout.setSpacing(4)

        header_row = QWidget(profile_content)
        header_layout = QHBoxLayout(header_row)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(Spacing.MD)

        self.content_only_profile_avatar = AsyncImageLabel(header_row)
        self.content_only_profile_avatar.setObjectName("contentOnlyProfileAvatar")
        self.content_only_profile_avatar.setFixedSize(
            self._content_only_avatar_size, self._content_only_avatar_size
        )
        self.content_only_profile_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content_only_profile_avatar.setStyleSheet(
            f"""
            QLabel#contentOnlyProfileAvatar {{
                background-color: {Colors.BG_TERTIARY};
                border-radius: {self._content_only_avatar_size // 2}px;
            }}
            """
        )
        header_layout.addWidget(self.content_only_profile_avatar)

        text_col = QWidget(header_row)
        text_layout = QVBoxLayout(text_col)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self.content_only_profile_name = QLabel("Creator")
        self.content_only_profile_name.setObjectName("contentOnlyProfileName")
        self.content_only_profile_name.setWordWrap(False)
        text_layout.addWidget(self.content_only_profile_name)

        self.content_only_profile_meta = QLabel("")
        self.content_only_profile_meta.setObjectName("contentOnlyProfileMeta")
        self.content_only_profile_meta.setWordWrap(False)
        text_layout.addWidget(self.content_only_profile_meta)

        header_layout.addWidget(text_col, 1)
        profile_content_layout.addWidget(header_row)

        self.content_only_profile_title = QLabel("")
        self.content_only_profile_title.setObjectName("contentOnlyProfileTitle")
        self.content_only_profile_title.setWordWrap(True)
        profile_content_layout.addWidget(self.content_only_profile_title)

        profile_layout.addWidget(profile_content)

        profile_row_layout.addWidget(self.content_only_profile)
        profile_row_layout.addStretch()
        holder_layout.addWidget(profile_row)

        content_row = QWidget()
        content_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        row_layout = QHBoxLayout(content_row)
        row_layout.setContentsMargins(Spacing.XXL, 0, Spacing.XXL, 0)
        row_layout.setSpacing(0)
        row_layout.addStretch()

        self.content_only_view = HtmlViewer()
        self.content_only_view.setObjectName("contentOnlyView")
        self.content_only_view.setFixedWidth(self._content_only_width)
        self.content_only_view.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        row_layout.addWidget(self.content_only_view)
        row_layout.addStretch()

        holder_layout.addWidget(content_row, 1)
        self.content_only_scroll.setWidget(content_only_holder)
        content_only_layout.addWidget(self.content_only_scroll, 1)
        self.body_stack.addWidget(self._content_only_body)

        self.body_stack.setCurrentWidget(self._normal_body)
        layout.addWidget(self.body_stack, 1)

    def _set_content_only_mode(self, enabled: bool) -> None:
        self._content_only_mode = bool(enabled)
        if not hasattr(self, "body_stack"):
            return
        if self._content_only_mode:
            self.body_stack.setCurrentWidget(self._content_only_body)
        else:
            self.body_stack.setCurrentWidget(self._normal_body)

    def _update_content_profile(
        self,
        creator_name: str | None,
        service: str | None,
        creator_id: str | None,
        title: str | None,
    ) -> None:
        if not hasattr(self, "content_only_profile_name"):
            return
        name = creator_name or "Creator"
        service = (service or "").strip()
        creator_id = (creator_id or "").strip()
        meta_parts = []
        if service:
            meta_parts.append(service)
        if creator_id and creator_id != name:
            meta_parts.append(creator_id)
        meta = " • ".join(meta_parts)
        self.content_only_profile_name.setText(name)
        self.content_only_profile_meta.setText(meta)
        self.content_only_profile_title.setText("")
        self.content_only_profile_title.setVisible(False)

    def _set_content_profile_images(
        self, banner_url: str | None, avatar_url: str | None
    ) -> None:
        if not hasattr(self, "content_only_profile_banner"):
            return
        if banner_url:
            def _on_banner_loaded(_, pixmap: QPixmap) -> None:
                if pixmap.isNull():
                    return
                target = (self._content_only_width, self._content_only_banner_height)
                self.content_only_profile_banner.setPixmap(
                    scale_and_crop_pixmap(pixmap, target)
                )

            self.content_only_profile_banner.load_image(
                url=banner_url,
                target_size=(self._content_only_width, self._content_only_banner_height),
                on_loaded=_on_banner_loaded,
            )
        else:
            self.content_only_profile_banner.clear()

        if avatar_url:
            def _on_avatar_loaded(_, pixmap: QPixmap) -> None:
                if pixmap.isNull():
                    return
                scaled = scale_and_crop_pixmap(
                    pixmap, (self._content_only_avatar_size, self._content_only_avatar_size)
                )
                self.content_only_profile_avatar.setPixmap(
                    create_circular_pixmap(scaled, self._content_only_avatar_size)
                )

            self.content_only_profile_avatar.load_image(
                url=avatar_url,
                target_size=(self._content_only_avatar_size, self._content_only_avatar_size),
                on_loaded=_on_avatar_loaded,
            )
        else:
            self.content_only_profile_avatar.clear()

    def _create_header(self) -> QWidget:
        """Create header with back button and title"""
        header = QWidget()
        header.setObjectName("detailHeader")
        header.setFixedHeight(60)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 12, 16, 12)

        # Back button
        back_btn = QPushButton("Back to Posts")
        back_btn.setObjectName("backButton")
        back_btn.setIcon(qta.icon('fa5s.arrow-left'))
        back_btn.setFixedHeight(36)
        back_btn.clicked.connect(self.back_clicked.emit)
        layout.addWidget(back_btn)

        layout.addStretch()

        # Title
        self.title_label = QLabel()
        self.title_label.setObjectName("detailTitleLabel")
        layout.addWidget(self.title_label)

        layout.addStretch()

        # Sidebar button (Files + Gallery)
        self.sidebar_btn = QPushButton("Files/Gallery")
        self.sidebar_btn.setObjectName("sidebarButton")
        self.sidebar_btn.setIcon(qta.icon("fa5s.columns"))
        self.sidebar_btn.setFixedHeight(36)
        self.sidebar_btn.setCheckable(True)
        self.sidebar_btn.clicked.connect(self._toggle_sidebar)
        layout.addWidget(self.sidebar_btn)

        return header

    def _create_info_bar(self) -> QWidget:
        """Create info bar between preview and grid"""
        info_bar = QWidget()
        info_bar.setObjectName("infoBar")
        info_bar.setFixedHeight(48)

        layout = QHBoxLayout(info_bar)
        layout.setContentsMargins(16, 8, 16, 8)

        # Media counter
        self.media_counter_label = QLabel("1 / 1")
        layout.addWidget(self.media_counter_label)

        # Navigation arrows
        self.prev_btn = QPushButton()
        self.prev_btn.setObjectName("prevMediaButton")
        self.prev_btn.setIcon(qta.icon('fa5s.chevron-left'))
        self.prev_btn.setFixedSize(32, 32)
        self.prev_btn.clicked.connect(self._show_previous)
        layout.addWidget(self.prev_btn)

        self.next_btn = QPushButton()
        self.next_btn.setObjectName("nextMediaButton")
        self.next_btn.setIcon(qta.icon('fa5s.chevron-right'))
        self.next_btn.setFixedSize(32, 32)
        self.next_btn.clicked.connect(self._show_next)
        layout.addWidget(self.next_btn)

        self.content_btn = QPushButton("Content")
        self.content_btn.setObjectName("contentButton")
        self.content_btn.setIcon(qta.icon("fa5s.comment-alt"))
        self.content_btn.setFixedSize(110, 32)
        self.content_btn.setCheckable(True)
        self.content_btn.setVisible(False)
        self.content_btn.clicked.connect(self._show_post_content)
        layout.addWidget(self.content_btn)

        self.autoplay_btn = QPushButton("Autoplay")
        self.autoplay_btn.setObjectName("autoplayButton")
        self.autoplay_btn.setIcon(qta.icon("fa5s.play-circle"))
        self.autoplay_btn.setFixedSize(110, 32)
        self.autoplay_btn.setCheckable(True)
        self.autoplay_btn.clicked.connect(self._toggle_autoplay)
        layout.addWidget(self.autoplay_btn)

        layout.addStretch()

        # Post info
        self.post_info_label = QLabel()
        self.post_info_label.setObjectName("postInfoLabel")
        self.post_info_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.post_info_label.mousePressEvent = self._on_creator_label_clicked
        layout.addWidget(self.post_info_label)

        return info_bar

    def _create_gallery_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)

        sort_bar = QWidget()
        sort_layout = QHBoxLayout(sort_bar)
        sort_layout.setContentsMargins(8, 0, 8, 0)
        sort_layout.setSpacing(8)

        self.gallery_sort_combo = QComboBox()
        self.gallery_sort_combo.setObjectName("gallerySortCombo")
        self.gallery_sort_combo.setStyleSheet(
            f"""
            QComboBox#gallerySortCombo {{
                background-color: transparent;
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: 6px;
                color: {Colors.TEXT_SECONDARY};
                font-size: 12px;
                padding: 4px 10px;
            }}
            QComboBox#gallerySortCombo:hover {{
                border-color: {Colors.ACCENT_PRIMARY};
                color: {Colors.TEXT_PRIMARY};
            }}
            QComboBox#gallerySortCombo::drop-down {{
                border: none;
                width: 100px;
            }}
            QComboBox#gallerySortCombo::down-arrow {{
                image: none;
            }}
            QComboBox QAbstractItemView {{
                background-color: {Colors.BG_SECONDARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                color: {Colors.TEXT_PRIMARY};
                selection-background-color: {Colors.ACCENT_PRIMARY};
                selection-color: {Colors.TEXT_WHITE};
                outline: none;
            }}
            """
        )
        self.gallery_sort_combo.addItem("Default order", ("default", True))
        self.gallery_sort_combo.addItem("Type: Images first", ("type", True))
        self.gallery_sort_combo.addItem("Type: Videos first", ("type", False))
        self.gallery_sort_combo.addItem("Duration: Short to Long", ("duration", True))
        self.gallery_sort_combo.addItem("Duration: Long to Short", ("duration", False))
        self.gallery_sort_combo.currentIndexChanged.connect(self._on_gallery_sort_changed)
        sort_layout.addWidget(self.gallery_sort_combo)
        sort_layout.addStretch()

        layout.addWidget(sort_bar)

        self.thumbnail_grid = MediaThumbnailGrid()
        self.thumbnail_grid.setMinimumHeight(0)
        self.thumbnail_grid.thumbnail_clicked.connect(self._on_thumbnail_clicked)
        layout.addWidget(self.thumbnail_grid, 1)

        return panel

    def _on_gallery_sort_changed(self):
        data = self.gallery_sort_combo.currentData()
        if isinstance(data, tuple) and len(data) == 2:
            mode, ascending = data
        else:
            mode, ascending = "default", True
        if hasattr(self, "thumbnail_grid"):
            self.thumbnail_grid.set_sort_mode(mode, ascending)
        self._update_nav_order()
        self._update_nav_buttons()

    def _reset_gallery_sort(self) -> None:
        if not hasattr(self, "gallery_sort_combo"):
            return
        self.gallery_sort_combo.blockSignals(True)
        self.gallery_sort_combo.setCurrentIndex(0)
        self.gallery_sort_combo.blockSignals(False)
        if hasattr(self, "thumbnail_grid"):
            self.thumbnail_grid.reset_sort()
        self._update_nav_order()

    def set_post(self, post_data: PostDTO, current_platform: str):
        """Display post in gallery view"""
        self.post_data = post_data
        self.current_platform = current_platform
        self.media_preview.reset_preview()
        self._detach_video_thumb_capture()
        self._showing_content = False
        self.media_preview.hide_content()

        # Extract title
        title = post_data.title or "Untitled"
        self.title_label.setText(title if len(title) < 60 else title[:60] + "...")

        # Extract post info
        creator = self._resolve_creator_display(post_data)
        service = post_data.service or "unknown"
        creator_id = post_data.user_id or ""
        self.post_info_label.setText(f"{creator} • {service}")
        if creator_id:
            self.post_info_label.setToolTip(f"{creator_id} • {service}")

        # Extract media items
        self.media_items = self._get_post_media(post_data)
        post_content = post_data.content or ""
        self._post_content = post_content
        allow_external_media = self._get_allow_external_media()
        has_content = bool(post_content.strip())
        content_only = has_content and not self.media_items
        banner_url = None
        avatar_url = None
        if service and creator_id:
            base = "https://img.kemono.cr" if self.current_platform == "kemono" else "https://img.coomer.st"
            banner_url = f"{base}/banners/{service}/{creator_id}"
            avatar_url = MediaManager.build_creator_icon_url(self.current_platform, service, creator_id)
        self._set_content_only_mode(content_only)
        self.media_preview.set_post_content(
            post_content,
            allow_external_media,
            post_data.title,
            creator,
            creator_id,
            service,
            banner_url,
            avatar_url,
        )
        if content_only and hasattr(self, "content_only_view"):
            self.content_only_view.set_post_html(
                self.media_preview.get_content_display_html(),
                allow_external_media,
            )
            self.media_preview.hide_content()
            self._update_content_profile(creator, service, creator_id, post_data.title)
            self._set_content_profile_images(banner_url, avatar_url)
        if hasattr(self, "content_btn"):
            self.content_btn.setVisible(has_content and bool(self.media_items))
            self.content_btn.setChecked(False)
        
        # Update file browser sidebar with ALL files (media + attachments)
        self.file_browser.set_files(self.media_items, self.attachment_files, self.current_platform)

        self._reset_gallery_sort()
        if self.media_items:
            # Setup thumbnail grid
            self.thumbnail_grid.set_media_items(self.media_items, 0)
            self._update_nav_order()
            self._prefetch_video_durations()

            # Show first media item
            self.current_media_index = 0
            self._show_current_media()
        else:
            self.thumbnail_grid.set_media_items([], 0)
            self._update_nav_order()
        if hasattr(self, "autoplay_btn"):
            self.autoplay_btn.setEnabled(bool(self.media_items))
            if not content_only:
                if post_content.strip():
                    if hasattr(self, "content_btn"):
                        self.content_btn.setChecked(True)
                    if hasattr(self, "media_preview"):
                        self.media_preview.show_content()
                    if not self.media_items:
                        self.media_counter_label.setText("Content")
                else:
                    if not self.media_items:
                        self.media_counter_label.setText("No media")
                self._update_nav_buttons()

    def update_post_content(self, post_data: PostDTO) -> None:
        if not post_data:
            return
        if self.post_data and post_data.id != self.post_data.id:
            return
        self.post_data = post_data
        post_content = post_data.content or ""
        self._post_content = post_content

        allow_external_media = self._get_allow_external_media()
        title = post_data.title or None
        if not title and hasattr(self, "title_label"):
            try:
                title = self.title_label.text()
            except Exception:
                title = None
        creator = self._resolve_creator_display(post_data)
        service = post_data.service or "unknown"
        creator_id = post_data.user_id or ""
        has_content = bool(post_content.strip())
        content_only = has_content and not self.media_items
        banner_url = None
        avatar_url = None
        if service and creator_id:
            base = "https://img.kemono.cr" if self.current_platform == "kemono" else "https://img.coomer.st"
            banner_url = f"{base}/banners/{service}/{creator_id}"
            avatar_url = MediaManager.build_creator_icon_url(self.current_platform, service, creator_id)
        self._set_content_only_mode(content_only)
        self.media_preview.set_post_content(
            post_content,
            allow_external_media,
            title,
            creator,
            creator_id,
            service,
            banner_url,
            avatar_url,
        )
        if content_only and hasattr(self, "content_only_view"):
            self.content_only_view.set_post_html(
                self.media_preview.get_content_display_html(),
                allow_external_media,
            )
            self.media_preview.hide_content()
            self._update_content_profile(creator, service, creator_id, title)
            self._set_content_profile_images(banner_url, avatar_url)
        if hasattr(self, "content_btn"):
            self.content_btn.setVisible(has_content and bool(self.media_items))
            if self._showing_content:
                self.content_btn.setChecked(True)

        if not self.media_items and not content_only:
            if post_content.strip():
                if hasattr(self, "content_btn"):
                    self.content_btn.setChecked(True)
                self.media_counter_label.setText("Content")
                if hasattr(self, "media_preview"):
                    self.media_preview.show_content()
            else:
                self.media_counter_label.setText("No media")

        self._update_nav_buttons()

    def _has_post_content(self) -> bool:
        return bool(self._post_content and self._post_content.strip())

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        if self._splitter_update_in_progress:
            return
        sizes = self.splitter.sizes()
        if len(sizes) < 2:
            return
        if sizes[1] == 0:
            if not self._thumbnails_collapsed:
                self._thumbnails_collapsed = True
                self.splitter.set_collapsed_state(True)
            return
        if self._thumbnails_collapsed:
            self._thumbnails_collapsed = False
            self.splitter.set_collapsed_state(False)
        self._thumbnail_splitter_sizes = sizes

    def _apply_splitter_sizes(self, sizes: List[int]) -> None:
        self._splitter_update_in_progress = True
        try:
            self.splitter.setSizes(sizes)
        finally:
            self._splitter_update_in_progress = False

    def _toggle_thumbnail_grid(self) -> None:
        if self._gallery_sidebar_mode:
            return
        if self._thumbnails_collapsed:
            self._expand_thumbnail_grid()
        else:
            self._collapse_thumbnail_grid()

    def _collapse_thumbnail_grid(self) -> None:
        if self._thumbnails_collapsed:
            return
        sizes = self.splitter.sizes()
        if len(sizes) >= 2:
            self._thumbnail_splitter_sizes = sizes
            total = sum(sizes)
        else:
            total = 0
        if total <= 0:
            return
        self._apply_splitter_sizes([total, 0])
        self._thumbnails_collapsed = True
        self.splitter.set_collapsed_state(True)

    def _expand_thumbnail_grid(self) -> None:
        if not self._thumbnails_collapsed:
            return
        current_total = sum(self.splitter.sizes())
        sizes = self._thumbnail_splitter_sizes or [650, 250]
        saved_total = sum(sizes)
        if current_total > 0 and saved_total > 0:
            ratio = sizes[0] / saved_total
            top = max(1, int(current_total * ratio))
            bottom = max(0, current_total - top)
            sizes = [top, bottom]
        self._apply_splitter_sizes(sizes)
        self._thumbnails_collapsed = False
        self.splitter.set_collapsed_state(False)

    def set_creator_lookup(self, resolver) -> None:
        self._creator_lookup = resolver

    def set_sidebar_toggle_overlay_handler(self, handler) -> None:
        self._sidebar_toggle_overlay_handler = handler
        if hasattr(self, "media_preview") and self.media_preview:
            try:
                self.media_preview.set_sidebar_toggle_overlay_handler(handler)
            except Exception:
                pass

    def _resolve_creator_display(self, post_data: PostDTO) -> str:
        """Resolve creator display name using centralized utility"""
        creator_id = post_data.user_id or ""
        service = post_data.service or ""
        return resolve_creator_name(
            self._creator_lookup,
            self.current_platform,
            service,
            creator_id,
            fallback=creator_id or "Unknown"
        )

    def _emit_creator_requested(self) -> None:
        if not self.post_data:
            return
        creator_id = self.post_data.user_id or ""
        service = self.post_data.service or ""
        if not creator_id or not service:
            return
        name = self._resolve_creator_display(self.post_data)
        data = {
            "id": creator_id,
            "creator_id": creator_id,
            "service": service,
            "name": name or creator_id,
        }
        self.creator_requested.emit(data)

    def _on_creator_label_clicked(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._emit_creator_requested()
        event.accept()

    def _get_post_media(self, post_data: PostDTO) -> List[MediaItem]:
        """Extract media items from post and separate attachments"""
        media_items: List[MediaItem] = []
        attachment_files: List[FileDTO] = []

        def build_media_item(file_dto: FileDTO) -> MediaItem | None:
            if not file_dto or not file_dto.path:
                return None
            logger.debug(f"build_media_item: file_dto.path={file_dto.path} (len={len(file_dto.path)})")
            file_url = MediaManager.build_media_url(self.current_platform, file_dto.path)
            logger.debug(f"build_media_item: built URL={file_url} (len={len(file_url)})")
            name = file_dto.name or Path(file_dto.path).name
            if file_dto.is_video:
                media_type = "video"
            elif file_dto.is_image:
                media_type = "image"
            else:
                # Non-media files go to attachments list
                return None
            return MediaItem(
                name=name,
                url=file_url,
                media_type=media_type,
                is_downloadable=True,
                duration=file_dto.duration if file_dto.is_video else None,
            )

        if post_data.file:
            item = build_media_item(post_data.file)
            if item:
                media_items.append(item)
            elif post_data.file and post_data.file.path:
                # It's a non-media file
                attachment_files.append(post_data.file)

        for attachment in post_data.attachments:
            item = build_media_item(attachment)
            if item:
                media_items.append(item)
            elif attachment and attachment.path:
                # It's a non-media file
                attachment_files.append(attachment)
        
        # Store attachment files for the attachments list
        self.attachment_files = attachment_files

        return media_items

    def _show_current_media(self):
        """Show current media in preview"""
        if 0 <= self.current_media_index < len(self.media_items):
            media_item = self.media_items[self.current_media_index]
            self.media_preview.show_media(media_item)
            if media_item.media_type == "video":
                if hasattr(self.media_preview, "set_nav_allowed"):
                    self.media_preview.set_nav_allowed(False)
                self._attach_video_thumb_capture()
                self._attach_video_nav_controls()
            else:
                if hasattr(self.media_preview, "set_nav_allowed"):
                    self.media_preview.set_nav_allowed(True)
                self._detach_video_thumb_capture()

            # Update counter
            self._update_nav_order()
            nav_pos = self._get_nav_position()
            self.media_counter_label.setText(
                f"{nav_pos + 1} / {len(self.media_items)}"
            )

            self._update_nav_buttons()
            if hasattr(self.media_preview, "refresh_nav_visibility"):
                self.media_preview.refresh_nav_visibility()
            self._maybe_restart_autoplay()
            self._preload_autoplay_next_image()

    def _update_nav_buttons(self) -> None:
        if self._showing_content:
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(bool(self.media_items))
            self._update_overlay_nav_buttons(False, bool(self.media_items))
            return
        if not self.media_items:
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            self._update_overlay_nav_buttons(False, False)
            return
        has_content = self._has_post_content()
        self._update_nav_order()
        nav_pos = self._get_nav_position()
        order_len = len(self._nav_order) if self._nav_order else len(self.media_items)
        prev_enabled = nav_pos > 0 or has_content
        next_enabled = nav_pos < max(0, order_len - 1)
        self.prev_btn.setEnabled(prev_enabled)
        self.next_btn.setEnabled(next_enabled)
        if hasattr(self, "media_counter_label") and self.media_items:
            self.media_counter_label.setText(f"{nav_pos + 1} / {len(self.media_items)}")
        self._update_overlay_nav_buttons(prev_enabled, next_enabled)

    def _update_nav_order(self) -> None:
        if not self.media_items:
            self._nav_order = []
            self._nav_index_map = {}
            return
        order = []
        if hasattr(self, "thumbnail_grid"):
            try:
                order = self.thumbnail_grid.get_display_order()
            except Exception:
                order = []
        if not order or len(order) != len(self.media_items):
            order = list(range(len(self.media_items)))
        self._nav_order = order
        self._nav_index_map = {idx: pos for pos, idx in enumerate(order)}

    def _get_allow_external_media(self) -> bool:
        if not self.db:
            return False
        try:
            return self.db.get_config('allow_post_content_media', 'false') == 'true'
        except sqlite3.Error as exc:
            logger.warning(f"Failed to read allow_post_content_media: {exc}")
            return False

    def _get_nav_position(self) -> int:
        if not self.media_items:
            return 0
        if self._nav_index_map:
            return int(self._nav_index_map.get(self.current_media_index, 0))
        return max(0, min(self.current_media_index, len(self.media_items) - 1))

    def _update_overlay_nav_buttons(self, prev_enabled: bool, next_enabled: bool) -> None:
        if hasattr(self, "media_preview"):
            self.media_preview.set_nav_enabled(prev_enabled, next_enabled)
        player = getattr(self.media_preview, "current_video_player", None)
        if player is not None and hasattr(player, "set_nav_enabled"):
            player.set_nav_enabled(prev_enabled, next_enabled)

    def _attach_video_nav_controls(self) -> None:
        player = getattr(self.media_preview, "current_video_player", None)
        if not player:
            return
        if player is self._nav_video_player:
            return
        if self._nav_video_player is not None:
            try:
                self._nav_video_player.prev_requested.disconnect(self._show_previous)
            except Exception:
                pass
            try:
                self._nav_video_player.next_requested.disconnect(self._show_next)
            except Exception:
                pass
        self._nav_video_player = player
        if hasattr(player, "prev_requested"):
            player.prev_requested.connect(self._show_previous)
        if hasattr(player, "next_requested"):
            player.next_requested.connect(self._show_next)
        self._update_nav_buttons()

    def _show_post_content(self):
        if getattr(self, "_content_only_mode", False):
            return
        if not hasattr(self, "content_btn"):
            return
        if not self.content_btn.isChecked():
            if hasattr(self, "media_preview"):
                self.media_preview.hide_content()
            if not self.media_items:
                self.media_counter_label.setText("No media")
            return
        if hasattr(self, "media_preview"):
            self.media_preview.show_content()
        if hasattr(self, "media_counter_label") and not self.media_items:
            self.media_counter_label.setText("Content")

    def _on_content_popup_closed(self) -> None:
        if hasattr(self, "content_btn"):
            self.content_btn.setChecked(False)
        if not self.media_items:
            self.media_counter_label.setText("No media")
            self._update_nav_buttons()

    def _toggle_sidebar(self):
        """Toggle right sidebar visibility"""
        if self.sidebar_btn.isChecked():
            self._show_side_panel()
        else:
            self._set_gallery_sidebar_mode(False)
            self.side_panel.hide_panel()
            if hasattr(self, "_side_panel_separator"):
                self._side_panel_separator.setVisible(False)

    def _on_sidebar_closed(self) -> None:
        if hasattr(self, "sidebar_btn"):
            self.sidebar_btn.setChecked(False)
        self._set_gallery_sidebar_mode(False)
        if hasattr(self, "_side_panel_separator"):
            self._side_panel_separator.setVisible(False)

    def _show_side_panel(self, tab: str | None = None) -> None:
        if not hasattr(self, "side_panel"):
            return
        if tab:
            self.side_panel.set_active_tab(tab)
        self.side_panel.show_panel()
        if hasattr(self, "_side_panel_separator"):
            self._side_panel_separator.setVisible(True)
        active_tab = self.side_panel.active_tab()
        if active_tab:
            self._set_gallery_sidebar_mode(active_tab == "Gallery")
        if hasattr(self, "media_preview") and hasattr(self.media_preview, "refresh_nav_visibility"):
            self.media_preview.refresh_nav_visibility()

    def _on_side_panel_tab_changed(self, tab: str) -> None:
        self._set_gallery_sidebar_mode(tab == "Gallery")

    def _ensure_gallery_placeholder(self) -> None:
        if not hasattr(self, "_gallery_tab_placeholder"):
            return
        if self._gallery_tab_placeholder.parent() is None:
            self._gallery_tab_layout.addWidget(self._gallery_tab_placeholder, 1)

    def _move_gallery_panel(self, target_layout: QVBoxLayout) -> None:
        if not hasattr(self, "gallery_panel"):
            return
        self.gallery_panel.setParent(None)
        target_layout.addWidget(self.gallery_panel, 1)

    def _set_gallery_sidebar_mode(self, enabled: bool) -> None:
        if self._gallery_sidebar_mode == enabled:
            return
        self._gallery_sidebar_mode = enabled

        handle = self.splitter.handle(1) if hasattr(self, "splitter") else None

        if enabled:
            self._saved_thumbnail_collapsed = self._thumbnails_collapsed
            self._collapse_thumbnail_grid()
            if hasattr(self, "_bottom_widget"):
                self._bottom_widget.setVisible(False)
            if hasattr(self, "_gallery_tab_placeholder"):
                self._gallery_tab_placeholder.setParent(None)
            if hasattr(self, "_gallery_tab_layout"):
                self._move_gallery_panel(self._gallery_tab_layout)
            if hasattr(self, "side_panel"):
                if self.side_panel.active_tab() != "Gallery":
                    self.side_panel.set_active_tab("Gallery")
                if not self.side_panel.isVisible():
                    self.side_panel.show_panel()
            if handle:
                handle.setVisible(False)
            if hasattr(self, "splitter"):
                self.splitter.setHandleWidth(0)
        else:
            if hasattr(self, "_bottom_gallery_layout"):
                self._move_gallery_panel(self._bottom_gallery_layout)
            if hasattr(self, "_bottom_widget"):
                self._bottom_widget.setVisible(True)
            self._ensure_gallery_placeholder()
            if handle:
                handle.setVisible(True)
            if hasattr(self, "splitter"):
                self.splitter.setHandleWidth(18)
        if hasattr(self, "media_preview") and hasattr(self.media_preview, "refresh_nav_visibility"):
            self.media_preview.refresh_nav_visibility()
            if hasattr(self, "side_panel") and self.side_panel.isVisible():
                self._collapse_thumbnail_grid()
            elif self._saved_thumbnail_collapsed:
                self._collapse_thumbnail_grid()
            else:
                self._expand_thumbnail_grid()

    def _prefetch_video_durations(self) -> None:
        self._duration_targets = []
        if not self.media_items:
            return
        cache = _DurationCache.instance()
        queue = _DurationProbeQueue.instance()
        for index, media_item in enumerate(self.media_items):
            if media_item.media_type != "video" or not media_item.url:
                continue
            duration = media_item.duration or cache.get(media_item.url)
            if duration:
                self.thumbnail_grid.update_video_duration(index, duration)
                continue
            target = _GalleryDurationTarget(self, index, media_item.url)
            self._duration_targets.append(target)
            queue.enqueue(weakref.ref(target), media_item.url)

    def _on_gallery_duration_probed(self, media_index: int, url: str, duration: float) -> None:
        if not (0 <= media_index < len(self.media_items)):
            return
        if self.media_items[media_index].url != url:
            return
        if duration and duration > 0:
            self.thumbnail_grid.update_video_duration(media_index, duration)

    def _on_files_download(self, urls: List[str]):
        """Handle bulk download from file browser"""
        if urls:
            self.download_clicked.emit(urls)

    def _on_files_jdownloader(self, urls: List[str]):
        """Handle JDownloader export from file browser"""
        if not urls:
            return
        
        # Get watch directory from settings
        watch_dir = None
        if self.db:
            jd_enabled = self.db.get_setting('jdownloader_enabled', False)
            if not jd_enabled:
                return
            
            configured_path = self.db.get_setting('jdownloader_watch_dir', '')
            if configured_path:
                watch_dir = Path(configured_path)
                if not watch_dir.exists():
                    watch_dir.mkdir(parents=True, exist_ok=True)
        
        if not watch_dir:
            # Try auto-detect
            watch_dir = JDownloaderExporter.find_default_watch_folder()
        
        if not watch_dir:
            logger.warning("JDownloader watch folder not configured")
            return
        
        try:
            exporter = JDownloaderExporter()
            
            # Get package name from post data
            package_name = None
            if self.post_data:
                post_title = getattr(self.post_data, 'title', None) or 'Unknown Post'
                package_name = post_title[:100]  # Limit package name length
            
            # Add entries for each URL
            for url in urls:
                exporter.add_entry(
                    url=url,
                    package_name=package_name,
                    enabled=True,
                    auto_start=True,
                    auto_confirm="TRUE",
                )
            
            # Export to crawljob file
            crawljob_path = exporter.export_to_file(watch_dir)
            logger.info(f"Exported {len(urls)} URLs to JDownloader: {crawljob_path}")
            
        except Exception as e:
            logger.error(f"Failed to export to JDownloader: {e}")

    def _show_previous(self):
        """Show previous media"""
        if self._showing_content:
            return
        self._update_nav_order()
        nav_pos = self._get_nav_position()
        if nav_pos == 0 and self._has_post_content():
            if hasattr(self, "content_btn"):
                self.content_btn.setChecked(True)
            self._show_post_content()
            return
        if nav_pos > 0:
            if self._nav_order:
                self.current_media_index = self._nav_order[nav_pos - 1]
            else:
                self.current_media_index = max(0, self.current_media_index - 1)
            self._request_fade_for_current()
            self.thumbnail_grid.selected_index = self.current_media_index
            self.thumbnail_grid.set_media_items(self.media_items, self.current_media_index)
            self._show_current_media()

    def _show_next(self):
        """Show next media"""
        if self._showing_content:
            if self.media_items:
                self.thumbnail_grid.selected_index = self.current_media_index
                self.thumbnail_grid.set_media_items(self.media_items, self.current_media_index)
                self._show_current_media()
            return
        self._update_nav_order()
        nav_pos = self._get_nav_position()
        order_len = len(self._nav_order) if self._nav_order else len(self.media_items)
        if nav_pos < order_len - 1:
            if self._nav_order:
                self.current_media_index = self._nav_order[nav_pos + 1]
            else:
                self.current_media_index = min(len(self.media_items) - 1, self.current_media_index + 1)
            self._request_fade_for_current()
            self.thumbnail_grid.selected_index = self.current_media_index
            self.thumbnail_grid.set_media_items(self.media_items, self.current_media_index)
            self._show_current_media()

    def _on_thumbnail_clicked(self, index: int, media_item: MediaItem):
        """Handle thumbnail click"""
        self.current_media_index = index
        self._request_fade_for_current()
        self._show_current_media()

    def _toggle_autoplay(self):
        if not hasattr(self, "autoplay_btn"):
            return
        self._set_autoplay_enabled(self.autoplay_btn.isChecked())

    def _set_autoplay_enabled(self, enabled: bool) -> None:
        self._autoplay_enabled = bool(enabled)
        if hasattr(self, "autoplay_btn"):
            self.autoplay_btn.blockSignals(True)
            self.autoplay_btn.setChecked(self._autoplay_enabled)
            icon_name = "fa5s.pause-circle" if self._autoplay_enabled else "fa5s.play-circle"
            self.autoplay_btn.setIcon(qta.icon(icon_name))
            self.autoplay_btn.blockSignals(False)
        if not self._autoplay_enabled:
            self._stop_autoplay()
            return
        self._maybe_restart_autoplay()

    def _stop_autoplay(self) -> None:
        if hasattr(self, "_autoplay_timer"):
            self._autoplay_timer.stop()
        self._detach_autoplay_video_player()

    def _detach_autoplay_video_player(self) -> None:
        if self._autoplay_video_player is None:
            return
        try:
            if hasattr(self._autoplay_video_player, "signals"):
                self._autoplay_video_player.signals.eof.disconnect(self._on_autoplay_video_finished)
        except Exception:
            pass
        self._autoplay_video_player = None

    def _attach_autoplay_video_player(self, player) -> None:
        if player is self._autoplay_video_player:
            return
        self._detach_autoplay_video_player()
        if player is None or not hasattr(player, "signals"):
            return
        try:
            player.signals.eof.connect(self._on_autoplay_video_finished)
        except Exception:
            return
        self._autoplay_video_player = player

    def _maybe_restart_autoplay(self) -> None:
        if not self._autoplay_enabled:
            return
        if hasattr(self, "_autoplay_timer"):
            self._autoplay_timer.stop()
        self._detach_autoplay_video_player()
        if self._showing_content or not self.media_items:
            return
        media_item = self.media_items[self.current_media_index]
        if media_item.media_type == "video":
            player = getattr(self.media_preview, "current_video_player", None)
            if player is not None:
                self._attach_autoplay_video_player(player)
                try:
                    if getattr(player, "_paused", False):
                        player.toggle_play()
                except Exception:
                    try:
                        player.player.pause = False  # type: ignore[attr-defined]
                    except Exception:
                        pass
                return
        if hasattr(self, "_autoplay_timer"):
            self._autoplay_timer.start(self._autoplay_image_delay_ms)

    def _preload_autoplay_next_image(self) -> None:
        if not self._autoplay_enabled:
            return
        if self._showing_content or not self.media_items:
            return
        self._update_nav_order()
        nav_pos = self._get_nav_position()
        order = self._nav_order if self._nav_order else list(range(len(self.media_items)))
        if nav_pos >= len(order) - 1:
            return
        next_index = order[nav_pos + 1]
        if not (0 <= next_index < len(self.media_items)):
            return
        media_item = self.media_items[next_index]
        if media_item.media_type != "image" or not media_item.url:
            return
        if hasattr(self, "media_preview") and hasattr(self.media_preview, "preload_image"):
            self.media_preview.preload_image(media_item.url)

    def _request_fade_for_current(self) -> None:
        if not self.media_items:
            return
        if not (0 <= self.current_media_index < len(self.media_items)):
            return
        media_item = self.media_items[self.current_media_index]
        if media_item.media_type != "image" or not media_item.url:
            return
        if hasattr(self, "media_preview") and hasattr(self.media_preview, "request_fade_next"):
            self.media_preview.request_fade_next()

    def _autoplay_advance(self) -> None:
        if not self._autoplay_enabled:
            return
        if self._showing_content:
            return
        self._show_next()

    def _on_autoplay_video_finished(self) -> None:
        if not self._autoplay_enabled:
            return
        self._show_next()

    def _attach_video_thumb_capture(self) -> None:
        player = getattr(self.media_preview, "current_video_player", None)
        if not player:
            return
        if player is self._video_thumb_player:
            return
        self._detach_video_thumb_capture()
        self._video_thumb_player = player
        try:
            player.signals.position.connect(self._on_video_thumb_ready)
        except Exception:
            pass
        try:
            player.signals.buffer.connect(self._on_video_thumb_ready)
        except Exception:
            pass
        try:
            player.signals.duration.connect(self._on_video_thumb_duration)
        except Exception:
            pass

    def _detach_video_thumb_capture(self) -> None:
        player = self._video_thumb_player
        if not player:
            return
        try:
            player.signals.position.disconnect(self._on_video_thumb_ready)
        except Exception:
            pass
        try:
            player.signals.buffer.disconnect(self._on_video_thumb_ready)
        except Exception:
            pass
        try:
            player.signals.duration.disconnect(self._on_video_thumb_duration)
        except Exception:
            pass
        self._video_thumb_player = None
        self._video_thumb_target_seconds = None
        self._video_thumb_capture_in_progress = False

    def _on_video_thumb_duration(self, duration: float) -> None:
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            return
        if duration <= 0:
            return
        if hasattr(self, "thumbnail_grid"):
            try:
                self.thumbnail_grid.update_video_duration(self.current_media_index, duration)
            except Exception:
                pass
        self._video_thumb_target_seconds = min(duration * 0.05, 2.0)
        self._request_video_thumbnail_for_current()

    def _on_video_thumb_ready(self, ratio: float) -> None:
        if ratio is None or ratio <= 0:
            return
        self._request_video_thumbnail_for_current()

    def _request_video_thumbnail_for_current(self) -> bool:
        if self._showing_content or not self.media_items:
            return False
        if not (0 <= self.current_media_index < len(self.media_items)):
            return False
        media_item = self.media_items[self.current_media_index]
        if media_item.media_type != "video":
            return False
        if self._video_thumb_capture_in_progress:
            return False
        target_seconds = self._video_thumb_target_seconds
        if target_seconds is None:
            return False
        if not hasattr(self.thumbnail_grid, "thumbnail_widgets"):
            return False
        thumb_widget = self.thumbnail_grid.get_widget_for_media_index(self.current_media_index)
        if thumb_widget is None:
            return False
        if getattr(thumb_widget, "_thumb_loaded", False):
            return True
        player = getattr(self.media_preview, "current_video_player", None)
        if not player or not hasattr(player, "request_thumbnail_capture"):
            return False
        self._video_thumb_capture_in_progress = True

        def _on_capture(image):
            self._video_thumb_capture_in_progress = False
            if image is None or image.isNull():
                return
            pixmap = QPixmap.fromImage(image)
            final_thumb = None
            if hasattr(thumb_widget, "_thumb_apply_pixmap"):
                try:
                    final_thumb = thumb_widget._thumb_apply_pixmap(pixmap)
                except Exception:
                    return
            else:
                thumb_label = getattr(thumb_widget, "_thumb_label", None)
                if thumb_label:
                    thumb_label.setPixmap(pixmap)
                    thumb_widget._thumb_loaded = True
                    thumb_widget._thumb_loading = False
                    spinner = getattr(thumb_widget, "_thumb_spinner", None)
                    if spinner:
                        try:
                            spinner.stop()
                        except RuntimeError:
                            pass
            if final_thumb is None:
                final_thumb = pixmap
            self._cache_video_thumbnail(media_item.url, final_thumb)
            self._detach_video_thumb_capture()

        if not player.request_thumbnail_capture(target_seconds, _on_capture):
            self._video_thumb_capture_in_progress = False
            return False
        return True

    def _cache_video_thumbnail(self, url: str, pixmap: QPixmap) -> None:
        if not url or pixmap is None or pixmap.isNull():
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        size = getattr(self.thumbnail_grid, "_thumb_size", 112)
        cache_path = self.cache_dir / f"{digest}_{size}x{size}.png"
        image = pixmap.toImage()
        if image.isNull():
            return
        try:
            image.save(str(cache_path), "PNG")
        except Exception:
            pass
        if self.db:
            try:
                content_id = self.db.get_content_id_for_url(url)
            except Exception:
                content_id = None
            if content_id:
                try:
                    self.db.cache_thumbnail_for_content(
                        content_id,
                        pixmap.width(),
                        pixmap.height(),
                        str(cache_path),
                    )
                except Exception:
                    pass

    def cleanup(self):
        """Cleanup all resources when navigating away"""
        # Cleanup media preview (stops video, cancels image loading)
        if hasattr(self, 'media_preview'):
            self._showing_content = False
            self.media_preview.cleanup()
        self._detach_video_thumb_capture()
        self._duration_targets = []
        self._stop_autoplay()

        logger.info("GalleryPostView cleaned up")
