"""
Video preview on hover for PostCard widgets.

Provides muted, looping video preview when hovering over video posts.
Uses a singleton manager to reuse a single MPV instance across all cards.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from typing import TYPE_CHECKING, Optional
from weakref import ref

from PyQt6.QtCore import Qt, QTimer, QObject, QPoint, QRectF
from PyQt6.QtGui import QCursor, QWheelEvent, QPixmap, QPainterPath, QRegion
from PyQt6.QtWidgets import QGraphicsEffect
from PyQt6.QtWidgets import QWidget, QApplication, QScrollArea, QStyle
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
import ctypes
from src.ui.common.theme import Spacing
from src.ui.widgets.rounded_effect import RoundedCornerGraphicsEffect

if TYPE_CHECKING:
    from src.core.context import CoreContext

logger = logging.getLogger(__name__)

try:
    from PyQt6 import sip as _sip
except Exception:  # pragma: no cover - fallback for alternate sip installs
    try:
        import sip as _sip  # type: ignore
    except Exception:
        _sip = None


def _is_qobject_deleted(obj) -> bool:
    if obj is None:
        return True
    try:
        if _sip is not None and _sip.isdeleted(obj):
            return True
    except Exception:
        pass
    return False

# --------------------------------------------------
# MPV bootstrap (reuse from video_player.py pattern)
# --------------------------------------------------

def _bundled_mpv_dir() -> str:
    """Get the directory containing libmpv-2.dll."""
    if hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
        mpv_path = os.path.join(base, "mpv")
        if os.path.exists(os.path.join(mpv_path, "libmpv-2.dll")):
            return mpv_path
        if os.path.exists(os.path.join(base, "libmpv-2.dll")):
            return base
        return mpv_path
    else:
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        candidates = [
            os.path.join(project_root, "mpv"),
            os.path.join(project_root, "src", "ui", "mpv"),
            os.path.join(project_root, "venv", "Scripts"),
        ]
        for candidate in candidates:
            if os.path.exists(os.path.join(candidate, "libmpv-2.dll")):
                return candidate
        return os.path.join(project_root, "mpv")


def _setup_mpv_path() -> str:
    """Ensure mpv DLL is on PATH."""
    mpv_dir = _bundled_mpv_dir()
    current_path = os.environ.get("PATH", "")
    if mpv_dir not in current_path:
        os.environ["PATH"] = mpv_dir + os.pathsep + current_path
    return mpv_dir


_setup_mpv_path()

try:
    import mpv
except ImportError:
    mpv = None  # type: ignore
    logger.warning("mpv module not available - video preview disabled")


# --------------------------------------------------
# VideoPreviewWidget
# --------------------------------------------------

class _MPVOpenGLPreview(QOpenGLWidget):
    """OpenGL-backed MPV preview using libmpv render API."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._mpv: Optional["mpv.MPV"] = None
        self._render_ctx: Optional["mpv.MpvRenderContext"] = None
        self._pending_url: Optional[str] = None
        self._update_pending = False
        self._get_proc_address_fn = None
        self._fbo: Optional[QOpenGLFramebufferObject] = None
        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(33)  # ~30fps
        self._frame_timer.timeout.connect(self._tick_frame)
        self._debug_timer = QTimer(self)
        self._debug_timer.setInterval(1000)
        self._debug_timer.timeout.connect(self._log_debug_state)
        self._last_update_flag = None
        self._last_render_ok = None
        self._render_to_default = True
        self._render_fail_count = 0
        self._render_fail_limit = 5
        try:
            fmt = self.format()
            # Keep an alpha channel to avoid Qt compositing this widget as fully transparent.
            fmt.setAlphaBufferSize(8)
            self.setFormat(fmt)
        except Exception:
            pass
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: black; border: none;")
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)
        self._update_round_mask()

    @property
    def player(self):
        return self._mpv

    def ensure_player(self) -> bool:
        """Return True if MPV instance is available (created in initializeGL)."""
        return self._mpv is not None

    def stop(self) -> None:
        if not _is_qobject_deleted(self._frame_timer):
            try:
                if self._frame_timer.isActive():
                    self._frame_timer.stop()
            except RuntimeError:
                pass
        if not _is_qobject_deleted(self._debug_timer):
            try:
                if self._debug_timer.isActive():
                    self._debug_timer.stop()
            except RuntimeError:
                pass
        if self._mpv:
            try:
                self._mpv.command("stop")
            except Exception:
                pass

    def cleanup(self) -> None:
        if not _is_qobject_deleted(self._frame_timer):
            try:
                if self._frame_timer.isActive():
                    self._frame_timer.stop()
            except RuntimeError:
                pass
        if not _is_qobject_deleted(self._debug_timer):
            try:
                if self._debug_timer.isActive():
                    self._debug_timer.stop()
            except RuntimeError:
                pass
        if self._fbo is not None:
            self._fbo = None
        if self._render_ctx:
            try:
                self._render_ctx.free()
            except Exception:
                pass
            self._render_ctx = None
        if self._mpv:
            try:
                self._mpv.terminate()
            except Exception:
                pass
            self._mpv = None

    def initializeGL(self):
        if mpv is None:
            return

        # mpv OpenGL render API
        def _get_proc_address(_ctx, name):
            try:
                if not self.context():
                    return None
                # Try bytes first (Qt expects QByteArray), then str fallback
                proc = self.context().getProcAddress(name)
                if not proc:
                    proc = self.context().getProcAddress(name.decode("utf-8"))
                return int(proc) if proc else None
            except Exception:
                return None

        try:
            opengl = self.context()
            if opengl:
                fmt = opengl.format()
                logger.debug(
                    f"Preview GL context: version={fmt.majorVersion()}.{fmt.minorVersion()} "
                    f"profile={fmt.profile()} depth={fmt.depthBufferSize()} "
                    f"samples={fmt.samples()}"
                )
        except Exception:
            pass

        try:
            lib = ctypes.windll.opengl32 if sys.platform == "win32" else None
            if lib:
                lib.glGetString.restype = ctypes.c_char_p
                GL_VENDOR = 0x1F00
                GL_RENDERER = 0x1F01
                GL_VERSION = 0x1F02
                vendor = lib.glGetString(GL_VENDOR)
                renderer = lib.glGetString(GL_RENDERER)
                version = lib.glGetString(GL_VERSION)
                logger.debug(
                    "Preview GL info: vendor=%s renderer=%s version=%s",
                    vendor.decode("utf-8", "ignore") if vendor else "unknown",
                    renderer.decode("utf-8", "ignore") if renderer else "unknown",
                    version.decode("utf-8", "ignore") if version else "unknown",
                )
        except Exception:
            pass

        try:
            self._get_proc_address_fn = mpv.MpvGlGetProcAddressFn(_get_proc_address)
            self._mpv = mpv.MPV(
                osc="no",
                input_default_bindings="no",
                keep_open="yes",
                vo="libmpv",
                gpu_api="opengl",
                gpu_context="win" if sys.platform == "win32" else "auto",
                hwdec="no",
                msg_level="all=no",
                volume=0,
                cache="no",
                demuxer_max_bytes=4 * 1024 * 1024,
                demuxer_readahead_secs=10,
                keepaspect="yes",
            )
            self._render_ctx = mpv.MpvRenderContext(
                self._mpv,
                "opengl",
                opengl_init_params={"get_proc_address": self._get_proc_address_fn},
            )
            self._render_ctx.update_cb = self._on_mpv_update
        except Exception as e:
            logger.error(f"Failed to init MPV OpenGL preview: {e}")
            self._mpv = None
            self._render_ctx = None
            try:
                parent = self.parent()
                if parent and hasattr(parent, "_on_gl_init_failed"):
                    parent._on_gl_init_failed(str(e))
            except Exception:
                pass
            return

        if self._pending_url:
            try:
                self._mpv.play(self._pending_url)
                self._mpv.pause = False
                if not self._frame_timer.isActive():
                    self._frame_timer.start()
                if logger.isEnabledFor(logging.DEBUG) and not self._debug_timer.isActive():
                    self._debug_timer.start()
            except Exception:
                pass
        try:
            self.update()
        except Exception:
            pass

        try:
            parent = self.parent()
            if parent and hasattr(parent, "_on_gl_player_ready"):
                parent._on_gl_player_ready(self._mpv)
        except Exception:
            pass

    def paintGL(self):
        if not self._render_ctx:
            return
        if not self.isVisible():
            return
        w = max(1, self.width())
        h = max(1, self.height())
        fbo_id = None
        render_fbo = {"w": w, "h": h, "fbo": 0}
        if self._render_to_default:
            fbo_id = int(self.defaultFramebufferObject())
            render_fbo["fbo"] = fbo_id
        else:
            if self._fbo is None or self._fbo.width() != w or self._fbo.height() != h:
                self._create_fbo(w, h)
            if self._fbo is None:
                return
            fbo_id = int(self._fbo.handle())
            render_fbo["fbo"] = fbo_id
            render_fbo["internal_format"] = 0x8058

        logger.debug(f"[gl-preview] paintGL w={w} h={h} fbo={fbo_id}")
        try:
            start = time.perf_counter()
            try:
                ctx = self.context()
                if ctx:
                    funcs = ctx.extraFunctions()
                    GL_COLOR_BUFFER_BIT = 0x00004000
                    funcs.glClearColor(0.0, 0.0, 0.0, 1.0)
                    funcs.glClear(GL_COLOR_BUFFER_BIT)
            except Exception:
                pass
            self._render_ctx.render(
                opengl_fbo=render_fbo,
                flip_y=True,
                block_for_target_time=False,
            )
            self._render_ctx.report_swap()
            if not self._render_to_default:
                self._blit_fbo(w, h)
            self._last_render_ok = True
            self._render_fail_count = 0
            if (time.perf_counter() - start) > 0.25:
                logger.debug("[gl-preview] slow render detected")
        except Exception:
            logger.exception("[gl-preview] render failed")
            self._last_render_ok = False
            self._render_fail_count += 1
            if self._render_fail_count >= self._render_fail_limit:
                try:
                    logger.warning("[gl-preview] disabling preview after repeated render failures")
                except Exception:
                    pass
                try:
                    if self._frame_timer.isActive():
                        self._frame_timer.stop()
                except Exception:
                    pass
                try:
                    if self._debug_timer.isActive():
                        self._debug_timer.stop()
                except Exception:
                    pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_round_mask()

    def _update_round_mask(self) -> None:
        radius = float(getattr(Spacing, "RADIUS_XL", 12))
        w = max(1, self.width())
        h = max(1, self.height())
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def resizeGL(self, w: int, h: int):
        if not self._render_to_default:
            self._create_fbo(w, h)
        if self._render_ctx:
            self.update()

    def _on_mpv_update(self):
        if self._update_pending:
            return
        self._update_pending = True
        QTimer.singleShot(0, self._do_update)

    def _do_update(self):
        self._update_pending = False
        if self._render_ctx and self._render_ctx.update():
            self.update()

    def _tick_frame(self):
        if not self.isVisible():
            return
        if self._render_ctx:
            try:
                self._last_update_flag = bool(self._render_ctx.update())
            except Exception:
                pass
        self.update()

    def _log_debug_state(self):
        if not self._mpv:
            return
        try:
            time_pos = getattr(self._mpv, "time_pos", None)
            duration = getattr(self._mpv, "duration", None)
            core_idle = getattr(self._mpv, "core_idle", None)
            video_params = getattr(self._mpv, "video_params", None)
            logger.debug(
                "[gl-preview] mpv time_pos=%s duration=%s core_idle=%s "
                "video_params=%s update=%s render_ok=%s",
                time_pos,
                duration,
                core_idle,
                video_params,
                self._last_update_flag,
                self._last_render_ok,
            )
        except Exception:
            pass

    def play(self, url: str) -> bool:
        if not url:
            return False
        self._pending_url = url
        logger.debug(f"[gl-preview] play url={url[:120]}")
        if self._mpv:
            try:
                self._mpv.play(url)
                self._mpv.pause = False
                if not self._frame_timer.isActive():
                    self._frame_timer.start()
                if logger.isEnabledFor(logging.DEBUG) and not self._debug_timer.isActive():
                    self._debug_timer.start()
                self.update()
                return True
            except Exception:
                logger.exception("[gl-preview] play failed")
                return False
        # Will auto-start once initializeGL runs
        return True

    def _create_fbo(self, w: int, h: int) -> None:
        if w <= 0 or h <= 0:
            return
        try:
            fmt = QOpenGLFramebufferObjectFormat()
            # Use RGBA to avoid alpha=0 when Qt composites the OpenGL widget.
            GL_RGBA8 = 0x8058
            fmt.setInternalTextureFormat(GL_RGBA8)
            fmt.setAttachment(QOpenGLFramebufferObject.Attachment.NoAttachment)
            self._fbo = QOpenGLFramebufferObject(w, h, fmt)
            logger.debug(
                "[gl-preview] fbo created w=%s h=%s valid=%s tex=%s",
                w,
                h,
                self._fbo.isValid() if self._fbo else None,
                self._fbo.texture() if self._fbo else None,
            )
        except Exception:
            self._fbo = None

    def _blit_fbo(self, w: int, h: int) -> None:
        ctx = self.context()
        if not ctx or self._fbo is None:
            return
        try:
            funcs = ctx.extraFunctions()
            GL_READ_FRAMEBUFFER = 0x8CA8
            GL_DRAW_FRAMEBUFFER = 0x8CA9
            GL_COLOR_BUFFER_BIT = 0x00004000
            GL_LINEAR = 0x2601
            funcs.glBindFramebuffer(GL_READ_FRAMEBUFFER, int(self._fbo.handle()))
            funcs.glBindFramebuffer(GL_DRAW_FRAMEBUFFER, int(self.defaultFramebufferObject()))
            funcs.glBlitFramebuffer(0, 0, w, h, 0, 0, w, h, GL_COLOR_BUFFER_BIT, GL_LINEAR)
            funcs.glBindFramebuffer(GL_READ_FRAMEBUFFER, 0)
            funcs.glBindFramebuffer(GL_DRAW_FRAMEBUFFER, 0)
        except Exception:
            pass


class VideoPreviewWidget(QWidget):
    """
    Lightweight MPV-based video preview widget.

    Displays muted, looping video preview over a target widget.
    """

    # Loop duration in seconds (first N seconds of video)
    LOOP_DURATION = 8.0

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._player: Optional["mpv.MPV"] = None
        self._video_container: Optional[QWidget] = None
        self._gl_preview: Optional[_MPVOpenGLPreview] = None
        self._current_url: Optional[str] = None
        self._duration: float = 0.0
        self._loop_set: bool = False
        self._cleanup_started: bool = False
        self._duration_observer_set = False
        self._use_opengl = bool(mpv is not None and hasattr(mpv, "MpvRenderContext"))
        self._hidden_thumb_target: Optional[QWidget] = None
        self._hidden_thumb_pixmap: Optional[QPixmap] = None
        self._hidden_effects: list[tuple[QWidget, QGraphicsEffect]] = []
        self._effects_target: Optional[QWidget] = None

        # Scroll handling - hide during scroll, restore after
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(150)  # Restore after 150ms of no scrolling
        self._scroll_timer.timeout.connect(self._on_scroll_end)
        self._scroll_target: Optional[QWidget] = None
        self._scroll_area: Optional[QScrollArea] = None
        self._scroll_start_pos: int = 0
        self._hidden_for_scroll: bool = False

        if self._use_opengl:
            self.setWindowFlags(Qt.WindowType.Widget)
        else:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent; border: none;")

        self._setup_ui()

    def _setup_ui(self):
        """Create the native video container."""
        if self._use_opengl:
            self._gl_preview = _MPVOpenGLPreview(self)
            self._gl_preview.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._video_container = self._gl_preview
        else:
            self._create_native_container()

    def _create_native_container(self) -> None:
        """Create a native child window for mpv (non-OpenGL path)."""
        container = QWidget(self)
        container.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        container.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        container.setStyleSheet("background-color: black; border: none;")
        self._video_container = container

    def _ensure_player(self) -> bool:
        """Create MPV player if not already created. Returns True if ready."""
        if mpv is None:
            return False

        if self._use_opengl:
            if self._gl_preview is None or _is_qobject_deleted(self._gl_preview):
                self._gl_preview = None
                return False
            # MPV is created in initializeGL; may not be ready yet
            if self._gl_preview.player is not None:
                if self._player is None:
                    self._player = self._gl_preview.player
                if self._player and not self._duration_observer_set:
                    try:
                        self._player.observe_property("duration", self._on_duration)
                        self._duration_observer_set = True
                    except Exception:
                        pass
            return True

        if self._player is not None:
            return True

        if self._video_container is None:
            return False

        try:
            self._player = mpv.MPV(
                wid=int(self._video_container.winId()),
                osc="no",
                input_default_bindings="no",
                keep_open="yes",
                vo="gpu",
                gpu_context="d3d11",
                hwdec="auto-safe",
                msg_level="all=no",
                volume=0,  # Muted
                cache="no",  # Let range proxy handle caching
                demuxer_max_bytes=4 * 1024 * 1024,  # 4MB minimal buffer
                demuxer_readahead_secs=10,
                keepaspect="yes",
            )

            # Observe duration for A/B loop setup
            self._player.observe_property("duration", self._on_duration)

            logger.debug("Video preview MPV player initialized")
            return True

        except Exception as e:
            logger.error(f"Failed to create MPV player for preview: {e}")
            self._player = None
            return False

    def _on_duration(self, name: str, value):
        """Called when video duration becomes available."""
        if value is None or value <= 0:
            return

        self._duration = float(value)

        # Set up A/B loop for first N seconds (or full video if shorter)
        if not self._loop_set and self._player:
            loop_end = min(self._duration, self.LOOP_DURATION)
            try:
                self._player["ab-loop-a"] = 0
                self._player["ab-loop-b"] = loop_end
                self._loop_set = True
                logger.debug(f"Preview loop set: 0 to {loop_end:.1f}s")
            except Exception as e:
                logger.debug(f"Failed to set A/B loop: {e}")

    def _on_gl_player_ready(self, player: "mpv.MPV") -> None:
        """Attach duration observer once GL-backed player is created."""
        if not player:
            return
        if self._player is None:
            self._player = player
        if not self._duration_observer_set:
            try:
                self._player.observe_property("duration", self._on_duration)
                self._duration_observer_set = True
            except Exception:
                pass

    def _on_gl_init_failed(self, reason: str) -> None:
        """Fallback to native mpv when OpenGL render init fails."""
        if not self._use_opengl:
            return
        logger.warning(f"OpenGL preview init failed, falling back to native: {reason}")
        self._use_opengl = False

        if self._gl_preview:
            try:
                self._gl_preview.cleanup()
            except Exception:
                pass
            self._gl_preview.setParent(None)
            self._gl_preview.deleteLater()
            self._gl_preview = None

        self._player = None
        self._duration_observer_set = False

        self._create_native_container()
        if self._video_container:
            self._video_container.setFixedSize(self.size())
            self._video_container.move(0, 0)

        if self._current_url:
            if self._ensure_player():
                try:
                    self._player.play(self._current_url)
                    self._player.pause = False
                except Exception:
                    pass

    def play(self, url: str) -> bool:
        """
        Start playing a video URL.

        Returns True if playback started successfully.
        """
        if not url:
            return False

        if not self._ensure_player():
            return False

        self._current_url = url
        self._duration = 0.0
        self._loop_set = False

        if self._use_opengl and self._gl_preview is not None:
            ok = self._gl_preview.play(url)
            # Ensure duration observer is attached once player exists
            self._ensure_player()
            logger.debug(f"Preview playing (GL): {url[:80]}...")
            return ok

        try:
            self._player.play(url)
            self._player.pause = False
            logger.debug(f"Preview playing: {url[:80]}...")
            return True
        except Exception as e:
            logger.error(f"Failed to play preview: {e}")
            return False

    def stop(self):
        """Stop playback."""
        if self._use_opengl and self._gl_preview is not None:
            if not _is_qobject_deleted(self._gl_preview):
                self._gl_preview.stop()
        elif self._player:
            try:
                self._player.command("stop")
            except Exception:
                pass
        self._current_url = None
        self._loop_set = False

    def show_over(self, target: QWidget):
        """Position and show preview over target widget."""
        if target is None:
            return

        target_size = target.size()

        if self._use_opengl:
            # Embed directly into the thumbnail so stacking works with overlays.
            self.setParent(target)
            self.setWindowFlags(Qt.WindowType.Widget)
            self.setFixedSize(target_size)
            self.move(QPoint(0, 0))
            self._hide_thumbnail_pixmap(target)
            self._disable_widget_effects(target)
        else:
            # Get global position of target
            global_pos = target.mapToGlobal(QPoint(0, 0))
            # Position as top-level window at target location
            self.setParent(None)
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.Tool
                | Qt.WindowType.WindowStaysOnTopHint
            )
            self.setFixedSize(target_size)
            self.move(global_pos)

        if self._video_container:
            self._video_container.setFixedSize(target_size)
            self._video_container.move(0, 0)

        # Ensure thumbnail is below preview
        try:
            target.lower()
        except Exception:
            pass

        self.show()
        if self._use_opengl:
            # Keep preview above the thumbnail image but below overlays.
            try:
                parent = target.parent()
                overlays = []
                for attr in ("badge_container", "duration_label", "select_btn", "overlay"):
                    w = getattr(parent, attr, None) if parent is not None else None
                    if w:
                        overlays.append(w)
                if overlays:
                    # Ensure at least one overlay is above the preview.
                    self.stackUnder(overlays[0])
                    for w in overlays:
                        w.raise_()
                else:
                    self.raise_()
            except Exception:
                pass
        else:
            self.raise_()

    def hide_preview(self):
        """Hide and stop the preview."""
        if _is_qobject_deleted(self):
            return
        self.stop()
        self._restore_widget_effects()
        self._restore_thumbnail_pixmap()
        self.hide()

    def cleanup(self):
        """Clean up MPV resources."""
        if self._cleanup_started:
            return
        self._cleanup_started = True

        self.stop()
        self._restore_widget_effects()
        self._restore_thumbnail_pixmap()

        if self._use_opengl and self._gl_preview is not None:
            if not _is_qobject_deleted(self._gl_preview):
                try:
                    self._gl_preview.cleanup()
                except Exception:
                    pass
            self._gl_preview = None
            self._player = None
        elif self._player:
            try:
                self._player.terminate()
            except Exception:
                pass
            self._player = None

        logger.debug("Video preview widget cleaned up")

    def resizeEvent(self, event):
        """Resize video container with widget."""
        super().resizeEvent(event)
        if self._video_container:
            self._video_container.setFixedSize(self.size())

    def _find_scroll_area(self, widget: Optional[QWidget]) -> Optional[QScrollArea]:
        """Find the parent QScrollArea of a widget."""
        while widget:
            if isinstance(widget, QScrollArea):
                return widget
            widget = widget.parent()
        return None

    def _get_scroll_pos(self) -> int:
        """Get current vertical scroll position."""
        if self._scroll_area:
            scrollbar = self._scroll_area.verticalScrollBar()
            if scrollbar:
                return scrollbar.value()
        return 0

    def wheelEvent(self, event: QWheelEvent):
        """Forward wheel events to the widget underneath for scrolling."""
        global_pos = event.globalPosition().toPoint()

        # On first scroll event, find target and hide preview
        if not self._hidden_for_scroll:
            self.hide()
            self._scroll_target = QApplication.widgetAt(global_pos)
            self._scroll_area = self._find_scroll_area(self._scroll_target)
            self._scroll_start_pos = self._get_scroll_pos()
            self._hidden_for_scroll = True

        # Forward to the cached target widget
        if self._scroll_target:
            local_pos = self._scroll_target.mapFromGlobal(global_pos)
            forwarded = QWheelEvent(
                local_pos.toPointF(),
                global_pos.toPointF(),
                event.pixelDelta(),
                event.angleDelta(),
                event.buttons(),
                event.modifiers(),
                event.phase(),
                event.inverted(),
            )
            QApplication.sendEvent(self._scroll_target, forwarded)

        # Reset timer - will restore preview after scrolling stops
        self._scroll_timer.start()
        event.accept()

    def _on_scroll_end(self):
        """Called when scrolling stops - only restore if scroll position unchanged."""
        scroll_changed = abs(self._get_scroll_pos() - self._scroll_start_pos) > 5

        self._hidden_for_scroll = False
        self._scroll_target = None
        self._scroll_area = None

        # Only restore preview if user didn't actually scroll
        if not scroll_changed:
            self.show()

    def _hide_thumbnail_pixmap(self, target: QWidget) -> None:
        """Temporarily clear the thumbnail pixmap so the GL preview isn't occluded."""
        try:
            if not hasattr(target, "pixmap") or not hasattr(target, "setPixmap"):
                return
            pix = target.pixmap()
            if pix is None or pix.isNull():
                return
            self._hidden_thumb_target = target
            self._hidden_thumb_pixmap = pix
            target.setPixmap(QPixmap())
            target.update()
        except Exception:
            self._hidden_thumb_target = None
            self._hidden_thumb_pixmap = None

    def _restore_thumbnail_pixmap(self) -> None:
        """Restore any thumbnail pixmap cleared for preview."""
        if self._hidden_thumb_target is None or self._hidden_thumb_pixmap is None:
            return
        try:
            self._hidden_thumb_target.setPixmap(self._hidden_thumb_pixmap)
            self._hidden_thumb_target.update()
            self._ensure_thumbnail_rounding(self._hidden_thumb_target)
        except Exception:
            pass
        finally:
            self._hidden_thumb_target = None
            self._hidden_thumb_pixmap = None

    def _ensure_thumbnail_rounding(self, target: QWidget) -> None:
        """Reapply rounded-corner effect to the thumbnail label if missing."""
        try:
            if target.objectName() != "postThumbnail":
                return
            if target.graphicsEffect() is not None:
                return
            radius = float(getattr(Spacing, "RADIUS_XL", 12))
            target.setGraphicsEffect(RoundedCornerGraphicsEffect(radius, target))
            target.update()
        except Exception:
            pass

    def _disable_widget_effects(self, target: QWidget) -> None:
        """Disable graphics effects that can occlude OpenGL children."""
        if self._hidden_effects and self._effects_target is target:
            return
        if self._hidden_effects:
            self._restore_widget_effects()
        try:
            widget: Optional[QWidget] = target
            # Disable effects on thumbnail and immediate ancestors (e.g., card)
            for _ in range(3):
                if widget is None:
                    break
                effect = widget.graphicsEffect()
                if effect is not None:
                    self._hidden_effects.append((widget, effect))
                    widget.setGraphicsEffect(None)
                    widget.update()
                widget = widget.parent() if isinstance(widget.parent(), QWidget) else None
            self._effects_target = target
        except Exception:
            self._hidden_effects.clear()
            self._effects_target = None

    def _restore_widget_effects(self) -> None:
        """Restore any graphics effects removed for preview."""
        if not self._hidden_effects:
            return
        for widget, effect in self._hidden_effects:
            try:
                widget.setGraphicsEffect(effect)
                widget.update()
            except Exception:
                pass
        self._hidden_effects.clear()
        self._effects_target = None


# --------------------------------------------------
# VideoPreviewManager
# --------------------------------------------------

class VideoPreviewManager(QObject):
    """
    Singleton manager for video preview on hover.

    Handles hover timing, preview lifecycle, and resource management.
    Only one preview plays at a time.
    """

    _instance: Optional["VideoPreviewManager"] = None

    # Hover delay before showing preview (ms)
    HOVER_DELAY_MS = 600
    TOOLTIP_GRACE_MS = 200

    @classmethod
    def instance(cls, core_context: Optional["CoreContext"] = None) -> "VideoPreviewManager":
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls(core_context)
        return cls._instance

    @classmethod
    def cleanup_global(cls):
        """Clean up the global instance."""
        if cls._instance is not None:
            cls._instance.cleanup()
            cls._instance = None

    def __init__(self, core_context: Optional["CoreContext"] = None):
        super().__init__()
        self._ctx = core_context
        self._preview_widget: Optional[VideoPreviewWidget] = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._on_hover_timeout)

        # Use weak references to avoid preventing card cleanup
        self._pending_card_ref: Optional[ref] = None
        self._pending_url: Optional[str] = None
        self._active_card_ref: Optional[ref] = None

        self._enabled = True

    def set_enabled(self, enabled: bool):
        """Enable or disable video preview."""
        self._enabled = enabled
        if not enabled:
            self._stop_preview()

    def request_preview(self, card: QWidget, video_url: str):
        """
        Request a video preview for a card.

        Preview will start after HOVER_DELAY_MS if mouse stays on card.
        """
        if not self._enabled or not video_url:
            return

        # Cancel any pending request
        if self._hover_timer.isActive():
            self._hover_timer.stop()

        # Always enforce hover delay (even if preview is already showing)
        if self._active_card_ref:
            self._stop_preview()

        # Store pending request and start timer
        self._pending_card_ref = ref(card)
        self._pending_url = video_url
        self._hover_timer.start(self._effective_hover_delay_ms())

    def cancel_preview(self, card: QWidget):
        """Cancel preview request for a card."""
        # Cancel pending request if it's for this card
        if self._pending_card_ref:
            pending_card = self._pending_card_ref()
            if pending_card is card:
                self._hover_timer.stop()
                self._pending_card_ref = None
                self._pending_url = None

        # Stop active preview if it's on this card
        if self._active_card_ref:
            active_card = self._active_card_ref()
            if active_card is card:
                self._stop_preview()

    def _on_hover_timeout(self):
        """Called after hover delay - start the preview."""
        card_ref = self._pending_card_ref
        url = self._pending_url

        self._pending_card_ref = None
        self._pending_url = None

        if card_ref is None or url is None:
            return

        card = card_ref()
        if card is None:
            return

        if self._is_badge_hovered(card):
            # Keep delaying while the user is hovering badge/tooltips
            self._pending_card_ref = card_ref
            self._pending_url = url
            self._hover_timer.start(self._effective_hover_delay_ms())
            return

        self._start_preview(card, url)

    def _effective_hover_delay_ms(self) -> int:
        """Ensure hover delay is long enough to allow tooltip visibility."""
        delay = int(self.HOVER_DELAY_MS)
        try:
            app = QApplication.instance()
            if app is not None:
                tooltip_delay = app.style().styleHint(QStyle.StyleHint.SH_ToolTip_WakeUpDelay)
                if isinstance(tooltip_delay, int) and tooltip_delay > 0:
                    delay = max(delay, tooltip_delay + self.TOOLTIP_GRACE_MS)
        except Exception:
            pass
        return delay

    def _is_badge_hovered(self, card: QWidget) -> bool:
        """Return True if the cursor is over a badge/overlay control."""
        try:
            widget = QApplication.widgetAt(QCursor.pos())
            if widget is None:
                return False
            for attr in ("badge_container", "duration_label", "select_btn"):
                target = getattr(card, attr, None)
                if target and (widget is target or target.isAncestorOf(widget)):
                    return True
        except Exception:
            return False
        return False

    def _start_preview(self, card: QWidget, url: str):
        """Start playing preview over the card."""
        # Create preview widget if needed
        if not self._preview_widget_alive():
            self._preview_widget = VideoPreviewWidget()

        # Get proxy URL if enabled
        final_url = self._get_proxy_url(url)

        # Get the thumbnail area from the card
        # PostCard has a thumbnail_label attribute
        target = getattr(card, 'thumbnail_label', card)

        # Verify target widget still exists (may have been deleted during hover delay)
        try:
            if target is None or not target.isVisible():
                return
            # Position and show
            self._preview_widget.show_over(target)
        except RuntimeError:
            # Widget was deleted
            return

        # Start playback
        if self._preview_widget.play(final_url):
            self._active_card_ref = ref(card)
        else:
            # Failed to play - hide widget
            if self._preview_widget_alive():
                try:
                    self._preview_widget.hide_preview()
                except RuntimeError:
                    self._preview_widget = None
            logger.debug(f"Failed to start preview for {url[:50]}")

    def _stop_preview(self):
        """Stop any active preview."""
        self._active_card_ref = None

        if not self._preview_widget_alive():
            self._preview_widget = None
            return
        if self._preview_widget:
            try:
                self._preview_widget.hide_preview()
            except RuntimeError:
                self._preview_widget = None

    def _get_proxy_url(self, url: str) -> str:
        """Get range proxy URL if enabled."""
        if self._ctx is None:
            return url

        try:
            enabled = self._ctx.db.get_config('enable_range_proxy', 'false') == 'true'
            if enabled and hasattr(self._ctx, 'range_proxy') and self._ctx.range_proxy:
                return self._ctx.range_proxy.proxy_url(url)
        except Exception as e:
            logger.debug(f"Failed to get proxy URL: {e}")

        return url

    def cleanup(self):
        """Clean up all resources."""
        self._hover_timer.stop()
        self._pending_card_ref = None
        self._pending_url = None
        self._active_card_ref = None

        if self._preview_widget:
            if not _is_qobject_deleted(self._preview_widget):
                self._preview_widget.cleanup()
            self._preview_widget = None

        logger.debug("VideoPreviewManager cleaned up")

    def prewarm(self, parent: Optional[QWidget]) -> None:
        """Pre-initialize the preview widget/GL context to avoid first-hover flicker."""
        if self._preview_widget_alive():
            return
        try:
            self._preview_widget = VideoPreviewWidget(parent)
            # Keep it hidden and off-screen.
            self._preview_widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            self._preview_widget.setFixedSize(1, 1)
            self._preview_widget.move(-10000, -10000)
            self._preview_widget.show()
            self._preview_widget.hide()
            self._preview_widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        except Exception:
            self._preview_widget = None

    def _preview_widget_alive(self) -> bool:
        if self._preview_widget is None:
            return False
        try:
            if _is_qobject_deleted(self._preview_widget):
                return False
        except Exception:
            pass
        try:
            _ = self._preview_widget.isVisible()
        except RuntimeError:
            return False
        return True
