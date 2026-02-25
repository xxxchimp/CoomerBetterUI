import os
import sys
from urllib.parse import parse_qs, unquote, urlparse
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider,
    QFrame, QStyle, QLabel, QDialog, QGraphicsOpacityEffect, QSizePolicy, QApplication, QToolButton
)
from PyQt6.QtCore import (
    Qt, QSize, QTimer, QEvent, QPropertyAnimation, QPoint, QEasingCurve, pyqtProperty, pyqtSignal
)
from PyQt6.QtGui import QPainter, QColor, QFont, QImage
import qtawesome as qta

# Import separated modules
from src.ui.widgets.ambient_effects import AmbientWorker
from .video_containers import AmbientVideoContainer
from .player_controls import MPVSignals, BufferedSlider
from src.ui.common.theme import Colors, Spacing, Styles

# --------------------------------------------------
# mpv bootstrap
# --------------------------------------------------

def bundled_mpv_dir():
    """Get the directory containing libmpv-2.dll"""
    # In PyInstaller onedir mode, _MEIPASS points to _internal folder
    # The mpv DLL is bundled into a 'mpv' subfolder within _internal
    if hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
        # Check _internal/mpv first (onedir mode structure)
        mpv_path = os.path.join(base, "mpv")
        if os.path.exists(os.path.join(mpv_path, "libmpv-2.dll")):
            return mpv_path
        # Also check if DLL is directly in base
        if os.path.exists(os.path.join(base, "libmpv-2.dll")):
            return base
        return mpv_path
    else:
        # Development mode - check multiple common locations
        # 1. Project root mpv folder
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        candidates = [
            os.path.join(project_root, "mpv"),
            os.path.join(project_root, "src", "ui", "mpv"),
            os.path.join(project_root, "venv", "Scripts"),
        ]
        for candidate in candidates:
            if os.path.exists(os.path.join(candidate, "libmpv-2.dll")):
                return candidate
        # Fallback
        return os.path.join(project_root, "mpv")


def _setup_mpv_path():
    """Set up PATH for mpv DLL loading with detailed logging."""
    mpv_dir = bundled_mpv_dir()
    
    # Check specifically for libmpv-2.dll
    dll_path = os.path.join(mpv_dir, "libmpv-2.dll")
    if os.path.exists(dll_path):
        print(f"[mpv] Found libmpv-2.dll at: {dll_path}")
    else:
        print(f"[mpv] Warning: libmpv-2.dll not found at: {dll_path}")
        # In bundled mode, search for it
        if hasattr(sys, "_MEIPASS"):
            print(f"[mpv] Searching in _MEIPASS: {sys._MEIPASS}")
            for root, dirs, files in os.walk(sys._MEIPASS):
                if "libmpv-2.dll" in files:
                    found_path = os.path.join(root, "libmpv-2.dll")
                    print(f"[mpv] Found libmpv-2.dll at: {found_path}")
                    mpv_dir = root
                    break
    
    # Add to PATH
    current_path = os.environ.get("PATH", "")
    if mpv_dir not in current_path:
        os.environ["PATH"] = mpv_dir + os.pathsep + current_path
        print(f"[mpv] Added to PATH: {mpv_dir}")
    
    return mpv_dir


# Set up mpv path before importing
_setup_mpv_path()

# Import mpv with error handling
try:
    import mpv  # noqa
    print(f"[mpv] Successfully imported mpv module")
except ImportError as e:
    print(f"[mpv] FAILED to import mpv: {e}")
    # Try to get more details about DLL loading
    import ctypes
    try:
        mpv_dir = bundled_mpv_dir()
        dll_path = os.path.join(mpv_dir, "libmpv-2.dll")
        if os.path.exists(dll_path):
            print(f"[mpv] Attempting direct DLL load from: {dll_path}")
            ctypes.CDLL(dll_path)
            print(f"[mpv] Direct DLL load succeeded")
        else:
            print(f"[mpv] DLL not found at expected path")
    except Exception as dll_err:
        print(f"[mpv] DLL load error: {dll_err}")
    raise

import logging
import weakref
import subprocess

logger = logging.getLogger(__name__)


def _subprocess_kwargs() -> dict:
    """Get platform-specific subprocess kwargs to hide console windows on Windows."""
    kwargs = {}
    if sys.platform == 'win32':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return kwargs


def _log_available_gpus():
    """Log available GPUs on the system for debugging."""
    try:
        # Use wmic to get GPU info on Windows
        result = subprocess.run(
            ["wmic", "path", "win32_VideoController", "get", "name"],
            capture_output=True,
            text=True,
            timeout=5,
            **_subprocess_kwargs(),
        )
        if result.returncode == 0:
            lines = [line.strip() for line in result.stdout.strip().split('\n') if line.strip() and line.strip() != 'Name']
            logger.info(f"Available GPUs: {lines}")
    except Exception as e:
        logger.debug(f"Could not enumerate GPUs: {e}")

# --------------------------------------------------
# Video Player
# --------------------------------------------------

class VideoPlayerWidget(QWidget):
    _global_volume = None
    _global_last_volume = None
    prev_requested = pyqtSignal()
    next_requested = pyqtSignal()

    def __init__(self, url: str, parent=None, core_context=None):
        super().__init__(parent)

        self.url = url
        self._core_context = core_context
        self.duration = 0.0
        self.seeking = False
        self._at_end = False
        self._autoloop = False
        self._paused = True
        self._started = False
        self._has_played_once = False  # Track if user has initiated playback at least once
        self._fs_window = None
        self._fs_placeholder = None
        self._fs_parent = None
        self._fs_parent_layout = None
        self._fs_parent_index = -1
        self._fs_exiting = False
        self._overlay_window = None
        self._overlay_root = None
        self._controls_auto_hide = True
        self._controls_hide_timer = QTimer(self)
        self._controls_hide_timer.setSingleShot(True)
        self._controls_hide_timer.setInterval(2000)
        self._controls_hide_timer.timeout.connect(self._hide_controls)
        self._controls_visible = False
        self._auto_hide_force_timer = QTimer(self)
        self._auto_hide_force_timer.setSingleShot(True)
        self._auto_hide_force_timer.setInterval(2500)
        self._auto_hide_force_timer.timeout.connect(self._force_hide_controls)
        self._overlay_enabled = True
        self._nav_prev_btn = None
        self._nav_next_btn = None
        self._sidebar_toggle_btn = None
        self._nav_hover_visible = False
        self._nav_parent_hover = False
        self._controls_opacity = QGraphicsOpacityEffect(self)
        self._controls_anim = QPropertyAnimation(self._controls_opacity, b"opacity", self)
        self._controls_anim.setDuration(220)
        self._top_opacity = QGraphicsOpacityEffect(self)
        self._top_anim = QPropertyAnimation(self._top_opacity, b"opacity", self)
        self._top_anim.setDuration(220)
        self._volume_anim = None
        self._volume_collapsed_width = 48
        self._volume_expanded_width = 150
        self._app_state_connected = False
        self._thumb_capture_state = None
        self._last_buffer_end = None
        self._last_buffer_duration = None
        self._last_position = 0.0
        self._buffer_ranges_bytes = []
        self._buffer_total_size = None
        self._range_proxy = None
        self._proxy_source_url = None
        self._buffer_poll_timer = QTimer(self)
        # Slower polling for range proxy to reduce overhead
        self._buffer_poll_timer.setInterval(2000)  # 2s instead of 800ms
        self._buffer_poll_timer.timeout.connect(self._refresh_buffer_segments)
        self._ambient_timer = QTimer(self)
        self._ambient_timer.setInterval(1000)  # Update ambient every 1s
        self._ambient_timer.timeout.connect(self._update_ambient)
        self._ambient_worker = None
        self._ambient_widget = None
        self._ambient_enabled = True
        self._using_range_proxy = False  # Disable video sampling for range proxy
        self._ambient_test_mode = False  # Set True only when testing overlay visibility
        self._ambient_opacity = 0.95  # Stronger ambient visibility
        self._video_aspect = 16.0 / 9.0
        self._aspect_ratio_set = False  # Will be set True after aspect ratio is detected
        self._pending_play = False  # Flag to track if user tried to play before aspect ratio was ready
        self._detecting_aspect = False  # Flag to track if we're in aspect detection phase
        self._cleanup_started = False  # Flag to prevent operations during cleanup
        self._initial_volume = self._load_saved_volume()
        self._last_volume = self._load_last_volume()
        
        # Repeating timer for slow-loading videos - keeps polling until detected
        self._aspect_poll_timer = QTimer(self)
        self._aspect_poll_timer.setInterval(2000)  # Poll every 2 seconds
        self._aspect_poll_timer.timeout.connect(self._detect_aspect_ratio_from_first_frame)

        self.signals = MPVSignals()

        self._build_ui()
        self._setup_player()
        self._connect_signals()

    def _get_db(self):
        ctx = self._core_context
        if ctx is not None and getattr(ctx, "db", None) is not None:
            return ctx.db
        try:
            from src.core.context import CoreContext
            return CoreContext().db
        except Exception:
            return None

    def _load_saved_volume(self) -> int:
        if VideoPlayerWidget._global_volume is not None:
            return int(VideoPlayerWidget._global_volume)
        default = 80
        value = default
        db = self._get_db()
        if db is not None:
            try:
                value = int(db.get_config("video_player_volume", str(default)))
            except Exception:
                value = default
        value = max(0, min(100, value))
        VideoPlayerWidget._global_volume = value
        return value

    def _load_last_volume(self) -> int:
        if VideoPlayerWidget._global_last_volume is not None:
            return int(VideoPlayerWidget._global_last_volume)
        default = 80
        value = default
        db = self._get_db()
        if db is not None:
            try:
                value = int(db.get_config("video_player_last_volume", str(default)))
            except Exception:
                value = default
        value = max(1, min(100, value))
        VideoPlayerWidget._global_last_volume = value
        return value

    def _persist_volume(self, value: int) -> None:
        value = max(0, min(100, int(value)))
        VideoPlayerWidget._global_volume = value
        if value > 0:
            VideoPlayerWidget._global_last_volume = value
        db = self._get_db()
        if db is None:
            return
        try:
            db.set_config("video_player_volume", str(value))
            if value > 0:
                db.set_config("video_player_last_volume", str(value))
        except Exception:
            return
        # Defer buffer segments setup to avoid blocking initialization
        QTimer.singleShot(500, self._setup_buffer_segments)
    
    # --------------------------------------------------
    
    def _start_ambient_timer_if_ready(self):
        """Start ambient timer only if video is actually ready."""
        if not self._ambient_enabled or not self._ambient_timer:
            return
        if self._paused or not self._started:
            return
        try:
            # Verify video is ready before starting timer
            if not self.player or not hasattr(self.player, 'video_params'):
                return
            video_params = self.player.video_params
            if not video_params or not video_params.get('w'):
                return
            if not self._ambient_timer.isActive():
                self._ambient_timer.start()
        except Exception:
            return

    # --------------------------------------------------

    def _build_ui(self):
        self.setObjectName("videoPlayerWidget")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Container handles ambient + video with height-fill behavior
        self.ambient_container = AmbientVideoContainer()
        self.ambient_container.installEventFilter(self)
        root.addWidget(self.ambient_container, 1)

        # Reference the video widget for mpv
        self.video_container = self.ambient_container.video_widget
        self.video_container.setMouseTracking(True)
        self.video_container.installEventFilter(self)

        # Reference ambient widget from container
        self._ambient_widget = self.ambient_container._ambient_widget

        self.top_gradient = QFrame()
        self.top_gradient.setObjectName("videoTopGradient")
        self.top_gradient.setFixedHeight(48)
        self.top_gradient.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.top_gradient.setGraphicsEffect(self._top_opacity)
        self._top_opacity.setOpacity(0.0)
        self.top_gradient.setVisible(False)

        self.controls = QFrame()
        self.controls.setObjectName("videoControls")
        self.controls.setFixedHeight(70)
        self.controls.setMouseTracking(True)
        self.controls.installEventFilter(self)
        self.controls.setGraphicsEffect(self._controls_opacity)
        self._controls_opacity.setOpacity(0.0)
        controls_layout = QVBoxLayout(self.controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(6)

        self.slider = BufferedSlider()
        self.slider.setObjectName("videoProgressSlider")
        self.slider.sliderPressed.connect(lambda: setattr(self, "seeking", True))
        self.slider.sliderReleased.connect(self._commit_seek)
        controls_layout.addWidget(self.slider)

        ctrl = QHBoxLayout()
        ctrl.setContentsMargins(0, 0, 0, 0)
        ctrl.setSpacing(10)
        controls_layout.addLayout(ctrl)

        left_group = QWidget()
        left_group.setObjectName("videoControlsLeft")
        left_layout = QHBoxLayout(left_group)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        self.play_btn = QPushButton()
        self.play_btn.setObjectName("playPauseButton")
        self.play_btn.setFlat(True)
        self.play_btn.setIconSize(QSize(20, 20))
        self.play_btn.clicked.connect(self.toggle_play)
        left_layout.addWidget(self.play_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self.volume_combo = QWidget()
        self.volume_combo.setObjectName("volumeControls")
        self.volume_combo.setStyleSheet(Styles.volume_controls())
        self.volume_combo.setFixedHeight(40)
        self.volume_combo.setMouseTracking(True)
        self.volume_combo.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.volume_combo.installEventFilter(self)
        self.volume_combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.volume_combo.setMinimumWidth(self._volume_collapsed_width)
        self.volume_combo.setMaximumWidth(self._volume_collapsed_width)
        volume_layout = QHBoxLayout(self.volume_combo)
        volume_layout.setContentsMargins(0, 0, 0, 0)
        volume_layout.setSpacing(0)
        volume_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setObjectName("volumeSlider")
        self.volume.setRange(0, 100)
        self.volume.setValue(self._initial_volume)
        self.volume.setMaximumWidth(90)
        self.volume.setFixedHeight(40)
        self.volume.setVisible(False)
        self.volume.setProperty("collapsed", True)
        self.volume.setStyleSheet(Styles.volume_slider())
        
        # Volume slider opacity tracking (0.0 to 1.0)
        self._volume_slider_opacity = 0.0
        
        # Volume slider fade animation using stylesheet opacity
        # windowOpacity doesn't work on child widgets, so we use a custom property
        self._volume_slider_anim = QPropertyAnimation(self, b"_volume_opacity_property", self)
        self._volume_slider_anim.setDuration(150)
        self._volume_slider_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        self.mute_btn = QPushButton()
        self.mute_btn.setObjectName("volumeButton")
        self.mute_btn.setFlat(True)
        self.mute_btn.setIconSize(QSize(20, 20))
        self.mute_btn.setFixedSize(40, 40)
        self.mute_btn.clicked.connect(self._toggle_mute)
        volume_layout.addWidget(self.mute_btn)
        volume_layout.addWidget(self.volume)
        volume_layout.setAlignment(self.mute_btn, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        volume_layout.setAlignment(self.volume, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        left_layout.addWidget(self.volume_combo, 0, Qt.AlignmentFlag.AlignLeft)

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setObjectName("videoTimeLabel")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        left_layout.addWidget(self.time_label, 0, Qt.AlignmentFlag.AlignLeft)

        ctrl.addWidget(left_group)
        ctrl.addStretch(1)

        right_group = QWidget()
        right_group.setObjectName("videoControlsRight")
        right_layout = QHBoxLayout(right_group)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        self.loop_btn = QPushButton()
        self.loop_btn.setObjectName("videoLoopButton")
        self.loop_btn.setFlat(True)
        self.loop_btn.setIconSize(QSize(20, 20))
        self.loop_btn.clicked.connect(self._toggle_loop)
        right_layout.addWidget(self.loop_btn)

        self.fullscreen_btn = QPushButton()
        self.fullscreen_btn.setObjectName("videoFullscreenButton")
        self.fullscreen_btn.setFlat(True)
        self.fullscreen_btn.setIconSize(QSize(20, 20))
        self.fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        right_layout.addWidget(self.fullscreen_btn)
        ctrl.addWidget(right_group)


        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._setup_overlay()
        self._layout_controls()
        self._set_controls_opacity(True)

    # --------------------------------------------------

    def _fmt_time(self, sec: float) -> str:
        sec = int(sec)
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    # --------------------------------------------------

    def _setup_player(self):
        # Log available GPUs for debugging
        _log_available_gpus()

        # Check if range proxy is enabled in settings (not just URL pattern)
        # Range proxy causes MPV to behave differently and video sampling crashes
        try:
            from src.core.context import CoreContext
            ctx = CoreContext()
            using_proxy = ctx.db.get_config('enable_range_proxy', 'false') == 'true'
            self._using_range_proxy = using_proxy
        except Exception:
            # Fallback to URL check if context unavailable
            using_proxy = self.url and "/proxy?url=" in self.url
            self._using_range_proxy = using_proxy
        
        # Adjust cache settings for range proxy compatibility
        if using_proxy:
            # Disable MPV cache entirely - let range proxy handle all caching
            # MPV's cache conflicts with proxy's chunk-based caching strategy
            cache_enabled = "no"
            demuxer_bytes = 16 * 1024 * 1024  # 16MB buffer (2 chunks) - breathing room for seeks
            demuxer_back_bytes = 8 * 1024 * 1024  # 8MB back buffer
            readahead_secs = 5  # 5 sec readahead for smoother post-seek playback
            # Ambient background now works with minimal MPV cache
        else:
            # Full cache for direct streams
            cache_enabled = "yes"
            demuxer_bytes = 96 * 1024 * 1024
            demuxer_back_bytes = 16 * 1024 * 1024
            readahead_secs = 30
        
        self.player = mpv.MPV(
            wid=int(self.video_container.winId()),
            osc="no",
            input_default_bindings="no",
            keep_open="yes",
            vo="gpu",  # Use GPU video output
            gpu_context="d3d11",  # Force D3D11 context for hwdec interop
            hwdec="auto-safe",  # Let mpv pick the best available hardware decoder
            msg_level="all=no",
            cache=cache_enabled,
            cache_pause="yes",  # Start with paused buffering (will disable after first play)
            demuxer_max_bytes=demuxer_bytes,
            demuxer_max_back_bytes=demuxer_back_bytes,
            demuxer_readahead_secs=readahead_secs,
            keepaspect="no",  # We handle aspect ratio at the container level
        )

        if self._initial_volume > 0:
            self._last_volume = self._initial_volume
        self.player.volume = int(self._initial_volume)  # type: ignore[attr-defined]
        self.volume.valueChanged.connect(self._on_volume_changed)

        self.player.observe_property("time-pos", self._mpv_time)
        self.player.observe_property("duration", self._mpv_duration)
        self.player.observe_property("pause", self._mpv_pause)
        self.player.observe_property("demuxer-cache-state", self._mpv_buffer)
        self.player.observe_property("demuxer-cache-duration", self._mpv_buffer)
        self.player.observe_property("eof-reached", self._mpv_eof)
        self.player.observe_property("video-params", self._mpv_video_params)
        self.player.observe_property("hwdec-current", self._mpv_hwdec)

        self.player.play(self.url)
        # Start paused - aspect ratio will be detected from first decoded frame
        self.player.pause = True  # type: ignore[attr-defined]
        self._paused = True
        self._update_icon(True)
        self._update_mute_icon()
        self._update_fullscreen_icon()
        self._update_loop_icon()
        self.ambient_container.mousePressEvent = lambda e: self.toggle_play() if e.button() == Qt.MouseButton.LeftButton else None

        # Aspect ratio detection via MPV's video-params observer callback + polling fallback
        # Multiple polls at different intervals to catch videos at various loading speeds
        self._detecting_aspect = True
        logger.info(f"Video player initialized for {self.url[:80]}, scheduling aspect ratio detection")
        QTimer.singleShot(100, self._detect_aspect_ratio_from_first_frame)
        QTimer.singleShot(300, self._detect_aspect_ratio_from_first_frame)
        QTimer.singleShot(600, self._detect_aspect_ratio_from_first_frame)
        QTimer.singleShot(1500, self._detect_aspect_ratio_from_first_frame)
        QTimer.singleShot(3000, self._detect_aspect_ratio_from_first_frame)
        QTimer.singleShot(5000, self._detect_aspect_ratio_from_first_frame)
        QTimer.singleShot(10000, self._detect_aspect_ratio_from_first_frame)
        # Start repeating timer for very slow loading videos (every 2 sec until detected)
        self._aspect_poll_timer.start()

    def _detect_aspect_ratio_from_first_frame(self):
        """Detect aspect ratio from first decoded frame (while paused)."""
        # Skip if cleanup started or already detected
        if self._cleanup_started or self._aspect_ratio_set:
            return

        if not hasattr(self, 'player') or not self.player:
            logger.debug("Aspect ratio poll: no player")
            return

        try:
            # Check if first frame has been decoded and video_params is available
            video_params = self.player.video_params
            if video_params:
                aspect = video_params.get('aspect')
                if aspect and aspect > 0:
                    logger.info(f"Polling detected aspect ratio: {aspect}")
                    self._set_detected_aspect_ratio(aspect)
                    return

                # Fallback: calculate from width/height
                w = video_params.get('w')
                h = video_params.get('h')
                if w and h and h > 0:
                    aspect = float(w) / float(h)
                    logger.info(f"Polling calculated aspect from w/h: {aspect} (w={w}, h={h})")
                    self._set_detected_aspect_ratio(aspect)
                    return
                else:
                    logger.debug(f"Polling: video_params present but no w/h yet: {video_params}")
            else:
                logger.debug("Polling: video_params is None - video data not yet loaded")
        except Exception as e:
            logger.debug(f"Error polling video_params: {e}")

    def _setup_overlay(self):
        if self._overlay_window:
            return
        overlay_parent = self._overlay_parent_window()
        self._overlay_window = QDialog(
            overlay_parent,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.ToolTip,
        )
        self._overlay_window.setObjectName("videoOverlay")
        self._overlay_window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._overlay_window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._overlay_window.hide()  # Start hidden to prevent flash
        self._overlay_window.installEventFilter(self)

        self._overlay_root = QWidget(self._overlay_window)
        self._overlay_root.setObjectName("videoOverlayRoot")
        self._overlay_root.setMouseTracking(True)
        self._overlay_root.installEventFilter(self)
        if overlay_parent:
            overlay_parent.installEventFilter(self)
        if not self._app_state_connected:
            app = QApplication.instance()
            if app:
                app.applicationStateChanged.connect(self._on_app_state_changed)
                app.focusChanged.connect(self._on_focus_changed)
                self._app_state_connected = True

        self.controls.setParent(self._overlay_root)
        self.top_gradient.setParent(self._overlay_root)
        self._setup_nav_overlay_buttons()

        self._overlay_root.mousePressEvent = lambda e: self.toggle_play() if e.button() == Qt.MouseButton.LeftButton else None
        QTimer.singleShot(0, self._sync_overlay_geometry)
        # Don't start ambient timer here - wait for playback to start

    def get_overlay_root(self):
        return self._overlay_root

    def register_overlay_widget(self, widget) -> None:
        if widget is None:
            self._external_overlay_widgets = []
            return
        try:
            if self._overlay_root and widget.parent() is not self._overlay_root:
                widget.setParent(self._overlay_root)
                widget.show()
        except Exception:
            pass
        self._external_overlay_widgets.append(weakref.ref(widget))
        self._raise_external_overlays()

    def _raise_external_overlays(self) -> None:
        if not getattr(self, "_external_overlay_widgets", None):
            return
        alive = []
        for ref in self._external_overlay_widgets:
            widget = ref()
            if widget:
                try:
                    widget.raise_()
                except Exception:
                    pass
                alive.append(ref)
        self._external_overlay_widgets = alive

    def _teardown_overlay(self):
        if not self._overlay_window:
            return
        try:
            self._overlay_window.removeEventFilter(self)
        except Exception:
            pass
        if self._overlay_root:
            self._overlay_root.removeEventFilter(self)
        if self._overlay_window:
            try:
                self._overlay_window.close()
            except Exception:
                pass
            self._overlay_window.deleteLater()
        self._overlay_window = None
        self._overlay_root = None
        self._external_overlay_widgets = []
        self._sidebar_toggle_btn = None
        self._external_overlay_widgets = []
        self._nav_prev_btn = None
        self._nav_next_btn = None
        self._nav_hover_visible = False

    def _sync_overlay_geometry(self):
        # Safety check for cleanup state
        if self._cleanup_started:
            return
        if not self._overlay_window or not self.ambient_container:
            return
        if not self._overlay_enabled:
            self._overlay_window.hide()
            self._set_nav_overlay_visible(False)
            return
        app = QApplication.instance()
        if app and app.applicationState() != Qt.ApplicationState.ApplicationActive:
            self._overlay_window.hide()
            self._set_nav_overlay_visible(False)
            return
        # Hide overlay when any modal dialog is active (e.g., Settings)
        if app:
            modal = app.activeModalWidget()
            if modal and modal is not self._overlay_window:
                self._overlay_window.hide()
                return
        if not self.ambient_container.isVisible():
            self._overlay_window.hide()
            self._set_nav_overlay_visible(False)
            return
        top_left = self.ambient_container.mapToGlobal(QPoint(0, 0))
        size = self.ambient_container.size()
        self._overlay_window.setGeometry(top_left.x(), top_left.y(), size.width(), size.height())
        self._overlay_root.setGeometry(0, 0, size.width(), size.height())
        if not self._overlay_window.isVisible():
            self._overlay_window.show()
        self._layout_controls()
        self._position_nav_overlay_buttons()
        self._refresh_nav_overlay_visibility()

    def _setup_nav_overlay_buttons(self) -> None:
        if not self._overlay_root:
            return
        if self._nav_prev_btn is None:
            self._nav_prev_btn = QPushButton(self._overlay_root)
        else:
            self._nav_prev_btn.setParent(self._overlay_root)
        self._nav_prev_btn.setObjectName("prevMediaButton")
        self._nav_prev_btn.setIcon(qta.icon('fa5s.chevron-left'))
        self._nav_prev_btn.setFixedSize(32, 32)
        self._nav_prev_btn.clicked.connect(self.prev_requested.emit)
        self._nav_prev_btn.setVisible(False)

        if self._nav_next_btn is None:
            self._nav_next_btn = QPushButton(self._overlay_root)
        else:
            self._nav_next_btn.setParent(self._overlay_root)
        self._nav_next_btn.setObjectName("nextMediaButton")
        self._nav_next_btn.setIcon(qta.icon('fa5s.chevron-right'))
        self._nav_next_btn.setFixedSize(32, 32)
        self._nav_next_btn.clicked.connect(self.next_requested.emit)
        self._nav_next_btn.setVisible(False)

        if self._sidebar_toggle_btn is None:
            self._sidebar_toggle_btn = QToolButton(self._overlay_root)
            self._sidebar_toggle_btn.clicked.connect(self._toggle_sidebar_from_video)
            self._apply_sidebar_toggle_style(self._sidebar_toggle_btn)
        else:
            self._sidebar_toggle_btn.setParent(self._overlay_root)
        self._update_sidebar_toggle_overlay_icon()
        self._sidebar_toggle_btn.setVisible(True)

        self._position_nav_overlay_buttons()
        self._nav_prev_btn.raise_()
        self._nav_next_btn.raise_()
        if self._sidebar_toggle_btn is not None:
            self._sidebar_toggle_btn.raise_()

    def _apply_sidebar_toggle_style(self, button: QToolButton) -> None:
        window = self.window()
        if window is not None and hasattr(window, "_apply_sidebar_toggle_style"):
            try:
                window._apply_sidebar_toggle_style(button)
                return
            except Exception:
                pass
        button.setObjectName("creatorsSidebarTabButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoRaise(True)
        button.setFixedSize(22, 44)
        button.setStyleSheet(
            f"QToolButton#creatorsSidebarTabButton {{"
            f"  background-color: {Colors.BG_DARKEST};"
            f"  border: 1px solid {Colors.BORDER_DEFAULT};"
            f"  border-left: none;"
            f"  border-top-right-radius: 8px;"
            f"  border-bottom-right-radius: 8px;"
            f"}}"
            f"QToolButton#creatorsSidebarTabButton:hover {{"
            f"  background-color: {Colors.BG_HOVER};"
            f"}}"
        )

    def _update_sidebar_toggle_overlay_icon(self) -> None:
        if self._sidebar_toggle_btn is None:
            return
        window = self.window()
        visible = True
        if window is not None and hasattr(window, "sidebar_visible"):
            try:
                visible = bool(window.sidebar_visible)
            except Exception:
                visible = True
        icon_name = "fa5s.chevron-left" if visible else "fa5s.user"
        self._sidebar_toggle_btn.setIcon(qta.icon(icon_name, color="#909090"))

    def _toggle_sidebar_from_video(self) -> None:
        window = self.window()
        if window is not None and hasattr(window, "_toggle_sidebar"):
            try:
                window._toggle_sidebar()
            except Exception:
                pass
        self._update_sidebar_toggle_overlay_icon()
        self._position_sidebar_toggle_overlay_btn()

    def _position_nav_overlay_buttons(self) -> None:
        if not self._overlay_root or not self._nav_prev_btn or not self._nav_next_btn:
            return
        w = self._overlay_root.width()
        h = self._overlay_root.height()
        btn_w = self._nav_prev_btn.width()
        btn_h = self._nav_prev_btn.height()
        y = max(0, (h - btn_h) // 2)
        margin = 12
        self._nav_prev_btn.move(margin, y)
        self._nav_next_btn.move(max(margin, w - margin - btn_w), y)
        self._position_sidebar_toggle_overlay_btn()

    def _position_sidebar_toggle_overlay_btn(self) -> None:
        if self._sidebar_toggle_btn is None or not self._overlay_root:
            return
        window = self.window()
        if window is None:
            return
        x = 0
        try:
            splitter = getattr(window, "splitter", None)
            handle = splitter.handle(1) if splitter is not None else None
            if handle is not None:
                handle_global = handle.mapToGlobal(QPoint(0, 0))
                handle_local = self._overlay_root.mapFromGlobal(handle_global)
                x = max(0, handle_local.x())
        except Exception:
            x = 0

        y = None
        try:
            tabs_widget = getattr(getattr(window, "creators_sidebar", None), "tabs_widget", None)
            if tabs_widget is not None and tabs_widget.height() > 0:
                tabs_global = tabs_widget.mapToGlobal(QPoint(0, 0))
                tabs_local = self._overlay_root.mapFromGlobal(tabs_global)
                y = tabs_local.y() + (tabs_widget.height() - self._sidebar_toggle_btn.height()) // 2
        except Exception:
            y = None

        if y is None and hasattr(window, "_sidebar_toggle_y"):
            try:
                parent = getattr(window, "_central_widget", window)
                y_global = parent.mapToGlobal(QPoint(0, window._sidebar_toggle_y))
                y = self._overlay_root.mapFromGlobal(y_global).y()
            except Exception:
                y = None

        if y is None:
            y = (self._overlay_root.height() - self._sidebar_toggle_btn.height()) // 2

        x = max(0, x + 3)
        y = max(0, y + 5)
        self._sidebar_toggle_btn.move(x, y)
        self._sidebar_toggle_btn.raise_()

    def _set_nav_overlay_visible(self, visible: bool) -> None:
        self._nav_hover_visible = bool(visible)
        if self._nav_prev_btn is not None:
            self._nav_prev_btn.setVisible(self._nav_hover_visible and not self._is_fullscreen_active())
        if self._nav_next_btn is not None:
            self._nav_next_btn.setVisible(self._nav_hover_visible and not self._is_fullscreen_active())
        self._update_sidebar_toggle_visibility()

    def set_nav_parent_hover(self, hovering: bool) -> None:
        self._nav_parent_hover = bool(hovering)
        self._refresh_nav_overlay_visibility()

    def _refresh_nav_overlay_visibility(self) -> None:
        if not self._overlay_root or not self._overlay_enabled:
            self._set_nav_overlay_visible(False)
            return
        visible = False
        try:
            if self._overlay_root.underMouse():
                visible = True
            elif self.controls and self.controls.underMouse():
                visible = True
            elif self.top_gradient and self.top_gradient.underMouse():
                visible = True
            elif self._nav_parent_hover:
                visible = True
        except Exception:
            visible = False
        self._set_nav_overlay_visible(visible)

    def _is_fullscreen_active(self) -> bool:
        return bool(self._fs_window)

    def _update_sidebar_toggle_visibility(self) -> None:
        if self._sidebar_toggle_btn is None:
            return
        self._sidebar_toggle_btn.setVisible(not self._is_fullscreen_active())

    def _overlay_parent_window(self):
        return self._fs_window or self.window()

    def _reparent_overlay(self, new_parent):
        if not self._overlay_window or not new_parent:
            return
        if self._overlay_window.parent() is new_parent:
            return
        self._overlay_window.setParent(new_parent)
        new_parent.installEventFilter(self)
        self._overlay_window.show()

    def _setup_buffer_segments(self) -> None:
        """Setup buffer segment tracking for range proxy."""
        source = self._extract_proxy_source_url(self.url)
        if not source or not self._core_context:
            return
        try:
            self._range_proxy = self._core_context.range_proxy
            self._proxy_source_url = source
            logger.info(f"Range proxy initialized for: {source[:50]}...")
        except Exception as e:
            logger.warning(f"Range proxy setup failed: {e}")
            self._range_proxy = None
            self._proxy_source_url = None
            return
        # Start timer but don't call refresh immediately - let video initialize first
        self._buffer_poll_timer.start()

    def _refresh_buffer_segments(self) -> None:
        if not self._range_proxy or not self._proxy_source_url:
            return
        try:
            total_size, ranges = self._range_proxy.cached_ranges(self._proxy_source_url)
        except Exception:
            return
        self._buffer_total_size = total_size
        self._buffer_ranges_bytes = ranges
        self._apply_buffer_segments()

    def _apply_buffer_segments(self) -> None:
        if not self.duration or not self._buffer_total_size or not self._buffer_ranges_bytes:
            self.slider.set_buffer_segments([])
            return
        total = float(self._buffer_total_size)
        segments = []
        for start, end in self._buffer_ranges_bytes:
            if end <= start:
                continue
            start_ratio = max(0.0, min(1.0, start / total))
            end_ratio = max(0.0, min(1.0, (end + 1) / total))
            if end_ratio <= start_ratio:
                continue
            segments.append((start_ratio, end_ratio))
        self.slider.set_buffer_segments(segments)

    @staticmethod
    def _extract_proxy_source_url(url: str) -> str | None:
        """Extract source URL from range proxy URL."""
        try:
            parsed = urlparse(url)
        except Exception:
            return None
        
        # Check if this is a proxy URL
        if "/proxy" not in parsed.path:
            return None
        
        if not parsed.query:
            return None
        
        params = parse_qs(parsed.query)
        target_list = params.get("url")
        if not target_list:
            return None
        
        target = target_list[0]
        if not target:
            return None
        
        return unquote(target)

    # --------------------------------------------------
    # mpv callbacks
    # --------------------------------------------------

    def _mpv_time(self, _, v):
        try:
            pos = float(v)
        except (TypeError, ValueError):
            return
        self._last_position = pos
        if self.duration:
            if not self._started and pos > 0:
                self._started = True
            self.signals.position.emit(pos / self.duration)
            if self._last_buffer_duration is not None:
                self._emit_buffer_duration(self._last_buffer_duration)

    def _mpv_duration(self, _, v):
        if v:
            try:
                duration = float(v)
            except (TypeError, ValueError):
                return
            self.duration = duration
            self.signals.duration.emit(duration)
            if self._last_buffer_end is not None and duration > 0:
                self._emit_buffer_end(self._last_buffer_end)
            elif self._last_buffer_duration is not None and duration > 0:
                self._emit_buffer_duration(self._last_buffer_duration)
            if self._buffer_ranges_bytes:
                self._apply_buffer_segments()

    def _mpv_pause(self, _, v):
        self._paused = bool(v)
        self.signals.pause.emit(self._paused)
        # Don't control timer here - it's called from MPV thread

    def _mpv_buffer(self, _, v):
        if isinstance(v, dict):
            end = self._read_buffer_value(v, ("end", "cache_end", "cache-end"))
            if end is not None:
                self._last_buffer_end = end
                self._emit_buffer_end(end)
                return
            cached = self._read_buffer_value(v, ("duration", "cache_duration", "cache-duration"))
            if cached is not None:
                self._last_buffer_duration = cached
                self._emit_buffer_duration(cached)
                return
            return
        try:
            cached = float(v)
        except (TypeError, ValueError):
            return
        self._last_buffer_duration = cached
        self._emit_buffer_duration(cached)

    def _emit_buffer_end(self, end: float) -> None:
        if not self.duration:
            return
        ratio = end / self.duration
        self.signals.buffer.emit(max(0.0, min(1.0, ratio)))

    def _emit_buffer_duration(self, cached: float) -> None:
        if not self.duration:
            return
        end = self._last_position + cached
        ratio = end / self.duration
        self.signals.buffer.emit(max(0.0, min(1.0, ratio)))

    @staticmethod
    def _read_buffer_value(payload: dict, keys: tuple[str, ...]) -> float | None:
        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def _mpv_eof(self, _, v):
        if v:
            if self._autoloop:
                self._at_end = False
                return
            self._at_end = True
            # Don't access player.pause from MPV thread - use signal instead
            self.signals.pause.emit(True)
            # Pause via main thread using QTimer
            QTimer.singleShot(0, lambda: setattr(self.player, "pause", True))
            QTimer.singleShot(0, self.signals.eof.emit)

    def _mpv_hwdec(self, _, v):
        """Log which hardware decoder MPV is using."""
        if v:
            logger.info(f"MPV hardware decoder active: {v}")

    def _mpv_video_params(self, _, video_params):
        """Called from MPV thread when video-params changes (first frame decoded)."""
        logger.debug(f"_mpv_video_params called: video_params={video_params}, already_set={self._aspect_ratio_set}")

        if not video_params or self._aspect_ratio_set:
            return

        # Extract aspect ratio
        aspect = video_params.get('aspect')
        if not aspect or aspect <= 0:
            # Fallback: calculate from width/height
            w = video_params.get('w')
            h = video_params.get('h')
            if w and h and h > 0:
                aspect = float(w) / float(h)
                logger.debug(f"Calculated aspect from w/h: {aspect} (w={w}, h={h})")
            else:
                logger.debug(f"No valid aspect info yet: aspect={aspect}, w={w}, h={h}")
                return  # No valid aspect info yet
        else:
            logger.debug(f"Got aspect from video_params: {aspect}")

        # Marshal to main thread to update UI
        logger.debug(f"Scheduling aspect ratio update to main thread: {aspect}")
        QTimer.singleShot(0, lambda: self._set_detected_aspect_ratio(aspect))

    # --------------------------------------------------

    def _connect_signals(self):
        self.signals.position.connect(self._on_position)
        self.signals.duration.connect(lambda d: setattr(self, "duration", d))
        self.signals.pause.connect(self._update_icon)
        self.signals.pause.connect(self._on_pause_changed)  # Control ambient timer
        self.signals.buffer.connect(self.slider.set_buffer)

    # --------------------------------------------------

    def _on_pause_changed(self, paused: bool):
        """Handle pause state changes - control ambient timer from main thread."""
        if paused:
            # Stop ambient updates when paused
            if self._ambient_timer and self._ambient_timer.isActive():
                self._ambient_timer.stop()
        else:
            # Start fade-in animation when playback begins
            if hasattr(self, 'ambient_container') and self.ambient_container:
                self.ambient_container._ambient_widget.fade_in()
            
            # Start ambient updates when playing, but delay start to allow buffering
            if self._ambient_enabled and self._ambient_timer and not self._ambient_timer.isActive():
                # Delay first ambient sample by 1 second to allow initial buffering
                QTimer.singleShot(1000, lambda: self._start_ambient_timer_if_ready())

    def _on_position(self, ratio: float):
        if not self.seeking:
            self.slider.setValue(int(ratio * 1000))

        cur = ratio * self.duration
        self.time_label.setText(
            f"{self._fmt_time(cur)} / {self._fmt_time(self.duration)}"
        )
        # No auto-hide scheduling here to avoid flicker during buffering.

    def request_thumbnail_capture(self, target_seconds: float, callback) -> bool:
        if self._thumb_capture_state is not None:
            return False
        try:
            target_seconds = float(target_seconds)
        except (TypeError, ValueError):
            return False
        if target_seconds < 0:
            target_seconds = 0.0
        try:
            duration = float(self.duration or 0.0)
        except (TypeError, ValueError):
            duration = 0.0

        try:
            original_time = float(self.player.time_pos or 0.0)
        except Exception:
            original_time = None
        try:
            was_paused = bool(self.player.pause)
        except Exception:
            was_paused = True

        if duration > 0:
            target_seconds = min(target_seconds, duration)
        if duration <= 0 and not was_paused:
            return False

        self._thumb_capture_state = {
            "target": target_seconds,
            "callback": callback,
            "original_time": original_time,
            "was_paused": was_paused,
            "attempts": 0,
            "mode": "wait" if not was_paused else "seek",
        }

        if not was_paused:
            try:
                self.signals.position.connect(self._on_thumb_capture_position)
            except Exception:
                pass
            return True

        try:
            self.player.pause = True  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            self.player.time_pos = target_seconds  # type: ignore[attr-defined]
        except Exception:
            self._thumb_capture_state = None
            return False
        self._schedule_thumb_capture(140)
        return True

    def _on_thumb_capture_position(self, ratio: float) -> None:
        state = self._thumb_capture_state
        if not state or state.get("mode") != "wait":
            return
        try:
            duration = float(self.duration or 0.0)
        except (TypeError, ValueError):
            return
        if duration <= 0:
            return
        target_ratio = state["target"] / duration
        if ratio >= target_ratio:
            image = self.capture_frame()
            self._finish_thumb_capture(image)

    def _schedule_thumb_capture(self, delay_ms: int = 150) -> None:
        state = self._thumb_capture_state
        if not state or state.get("mode") != "seek":
            return
        if state.get("pending"):
            return
        state["pending"] = True
        QTimer.singleShot(delay_ms, self._try_thumb_capture)

    def _try_thumb_capture(self) -> None:
        state = self._thumb_capture_state
        if not state or state.get("mode") != "seek":
            return
        state["pending"] = False
        image = self.capture_frame()
        if image is not None and not image.isNull():
            self._finish_thumb_capture(image)
            return
        state["attempts"] += 1
        if state["attempts"] < 6:
            self._schedule_thumb_capture(180)
            return
        self._finish_thumb_capture(None)

    def _finish_thumb_capture(self, image) -> None:
        state = self._thumb_capture_state
        if not state:
            return
        self._thumb_capture_state = None
        if state.get("mode") == "wait":
            try:
                self.signals.position.disconnect(self._on_thumb_capture_position)
            except Exception:
                pass
        if state.get("mode") == "seek":
            original_time = state.get("original_time")
            if original_time is not None:
                try:
                    self.player.time_pos = original_time  # type: ignore[attr-defined]
                except Exception:
                    pass
            if not state.get("was_paused", True):
                try:
                    self.player.pause = False  # type: ignore[attr-defined]
                except Exception:
                    pass
        callback = state.get("callback")
        if callback:
            try:
                callback(image)
            except Exception:
                pass

    def _commit_seek(self):
        if self.duration:
            self.player.time_pos = (self.slider.value() / 1000) * self.duration  # type: ignore[attr-defined]
        self.seeking = False
        self._at_end = False
        self._schedule_hide_controls()

    def _start_pending_playback(self):
        """Start playback if user tried to play before aspect ratio was ready."""
        logger.info(f"_start_pending_playback called: pending_play={self._pending_play}, aspect_ratio_set={self._aspect_ratio_set}")

        if self._pending_play and self._aspect_ratio_set:
            self._pending_play = False
            logger.info("Starting playback (aspect ratio ready)")
            try:
                self.player.pause = False  # type: ignore[attr-defined]
                self._paused = False
                self._update_icon(False)
                self._schedule_hide_controls()
                logger.info("Playback started successfully")
            except Exception as e:
                logger.error(f"Failed to start playback: {e}")
        else:
            logger.debug(f"Not starting playback: pending_play={self._pending_play}, aspect_ratio_set={self._aspect_ratio_set}")

    def _set_detected_aspect_ratio(self, aspect: float):
        """Set aspect ratio from detected video params (runs on main thread)."""
        logger.info(f"_set_detected_aspect_ratio called: aspect={aspect}, current={self._video_aspect}, already_set={self._aspect_ratio_set}")

        # Mark as detected and stop polling timer
        if not self._aspect_ratio_set:
            self._aspect_ratio_set = True
            logger.info("Aspect ratio detection complete")
            # Stop the repeating poll timer now that we have the aspect ratio
            if hasattr(self, '_aspect_poll_timer') and self._aspect_poll_timer.isActive():
                self._aspect_poll_timer.stop()
                logger.debug("Stopped aspect ratio polling timer")

        # Update aspect ratio if it changed
        if abs(aspect - self._video_aspect) > 0.001:
            logger.info(f"Updating aspect ratio: {self._video_aspect} -> {aspect}")
            self._video_aspect = aspect
            self.ambient_container.set_aspect_ratio(aspect)
        else:
            logger.debug(f"Aspect ratio unchanged: {aspect}")

    def toggle_play(self):
        logger.info(f"toggle_play called: paused={self._paused}")

        if self._at_end:
            try:
                self.player.time_pos = 0
            except Exception:
                pass
            self._at_end = False
        self.player.pause = not self.player.pause  # type: ignore[attr-defined]
        self._paused = bool(self.player.pause)  # type: ignore[attr-defined]
        
        # Track that user has initiated playback at least once
        # After first play, disable cache-pause so buffering continues even when paused
        if not self._paused and not self._has_played_once:
            self._has_played_once = True
            try:
                self.player["cache-pause"] = "no"  # Continue buffering when paused
                logger.info("First play initiated - disabled cache-pause for continuous buffering")
            except Exception as e:
                logger.warning(f"Failed to disable cache-pause: {e}")
        
        if self.player.pause:
            self._show_controls()
        else:
            self._schedule_hide_controls()

    def _update_icon(self, paused: bool):
        name = "fa5s.play" if paused else "fa5s.pause"
        self.play_btn.setIcon(qta.icon(name, color=Colors.TEXT_PRIMARY))
        if paused:
            self._show_controls()

    def _on_volume_changed(self, value: int):
        self.player.volume = value  # type: ignore[attr-defined]
        if value > 0:
            self._last_volume = value
        self._persist_volume(value)
        self._update_mute_icon()

    def _toggle_mute(self):
        if self.volume.value() == 0:
            self.volume.setValue(max(1, self._last_volume))
        else:
            self.volume.setValue(0)

    def _update_mute_icon(self):
        name = "fa5s.volume-mute" if self.volume.value() == 0 else "fa5s.volume-up"
        self.mute_btn.setIcon(qta.icon(name, color=Colors.TEXT_PRIMARY))

    def set_nav_enabled(self, prev_enabled: bool, next_enabled: bool) -> None:
        if self._nav_prev_btn is not None:
            self._nav_prev_btn.setEnabled(bool(prev_enabled))
        if self._nav_next_btn is not None:
            self._nav_next_btn.setEnabled(bool(next_enabled))

    def _toggle_fullscreen(self):
        if self._fs_window:
            self._exit_fullscreen()
        else:
            self._enter_fullscreen()

    def _update_fullscreen_icon(self):
        name = "fa5s.compress" if self._fs_window else "fa5s.expand"
        self.fullscreen_btn.setIcon(qta.icon(name, color=Colors.TEXT_PRIMARY))

    def _toggle_loop(self):
        self._autoloop = not self._autoloop
        try:
            self.player.loop_file = "inf" if self._autoloop else "no"
        except Exception:
            pass
        self._update_loop_icon()

    def _update_loop_icon(self):
        color = Colors.ACCENT_PRIMARY if self._autoloop else Colors.TEXT_PRIMARY
        self.loop_btn.setIcon(qta.icon("fa5s.redo", color=color))
        if self._autoloop:
            self.loop_btn.setStyleSheet(f"color: {Colors.ACCENT_PRIMARY};")
        else:
            self.loop_btn.setStyleSheet("")

    def _enter_fullscreen(self):
        if self._fs_window:
            return
        # Disable aspect ratio constraint in fullscreen
        self.ambient_container.set_constraint_enabled(False)
        parent = self.parentWidget()
        layout = parent.layout() if parent else None
        if parent and layout:
            self._fs_parent = parent
            self._fs_parent_layout = layout
            self._fs_parent_index = layout.indexOf(self)
            self._fs_placeholder = QWidget()
            self._fs_placeholder.setFixedSize(self.size())
            layout.insertWidget(self._fs_parent_index, self._fs_placeholder)
        self._fs_window = QDialog(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self._fs_window.setObjectName("videoFullscreen")
        self._fs_window.installEventFilter(self)
        fs_layout = QVBoxLayout(self._fs_window)
        fs_layout.setContentsMargins(0, 0, 0, 0)
        fs_layout.setSpacing(0)
        self.setParent(self._fs_window)
        fs_layout.addWidget(self)
        self._fs_window.showFullScreen()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self._update_fullscreen_icon()
        self._show_controls()
        self._teardown_overlay()
        self._setup_overlay()
        self._sync_overlay_geometry()

    def _exit_fullscreen(self):
        if not self._fs_window or self._fs_exiting:
            return
        self._fs_exiting = True
        # Re-enable aspect ratio constraint
        self.ambient_container.set_constraint_enabled(True)
        self.setParent(self._fs_parent)
        if self._fs_parent_layout and self._fs_parent_index >= 0:
            if self._fs_placeholder:
                self._fs_parent_layout.removeWidget(self._fs_placeholder)
                self._fs_placeholder.deleteLater()
            self._fs_parent_layout.insertWidget(self._fs_parent_index, self)
        try:
            self._fs_window.removeEventFilter(self)
        except Exception:
            pass
        self._fs_window.close()
        self._fs_window.deleteLater()
        self._fs_window = None
        self._fs_placeholder = None
        self._fs_parent = None
        self._fs_parent_layout = None
        self._fs_parent_index = -1
        self._fs_exiting = False
        self._update_fullscreen_icon()
        self._show_controls()
        self._teardown_overlay()
        self._setup_overlay()
        self._sync_overlay_geometry()

    def _layout_controls(self):
        if not self._overlay_root:
            return
        w = self._overlay_root.width()
        h = self._overlay_root.height()
        self.top_gradient.setGeometry(0, 0, w, self.top_gradient.height())
        self.top_gradient.raise_()
        self.controls.setGeometry(0, max(0, h - self.controls.height()), w, self.controls.height())
        self.controls.raise_()
        self._raise_external_overlays()

        # Ambient is handled by AmbientVideoContainer - no masking needed

    def _update_ambient(self):
        # Don't start ambient if cleanup has started
        if self._cleanup_started:
            return
        if not self._ambient_enabled:
            return
        # Only update during active playback
        if self._paused or not self._started:
            return
        # Check if video is loaded
        if not self.duration or self.duration <= 0:
            return
        if not self.isVisible() or not self.ambient_container.isVisible():
            return
        # Don't start new worker if one is already running
        if self._ambient_worker and self._ambient_worker.isRunning():
            return
        # Don't start worker if player is not available or being cleaned up
        if not hasattr(self, 'player') or not self.player:
            return
        
        # Critical safety check: ensure video is actually playing and has frames
        # This prevents crashes with range proxy where buffering is gradual
        try:
            if not self.player or not hasattr(self.player, 'video_params'):
                return
        except Exception:
            return
        
        # Separate property checks to isolate failures
        try:
            video_params = self.player.video_params
            if not video_params or not video_params.get('w') or not video_params.get('h'):
                return
        except Exception:
            return
        
        # Check playback position
        try:
            time_pos = self.player.time_pos
            if time_pos is None or time_pos < 1.0:  # Wait at least 1.0s into playback
                return
        except Exception:
            return
        
        # Check cache state (this can fail with range proxy)
        try:
            cache_state = self.player.demuxer_cache_state
            if not cache_state:
                return
            # Don't require seekable-ranges for range proxy
        except Exception:
            # If we can't get cache state, still allow if other checks passed
            pass

        worker = AmbientWorker(self.player)
        # Use Qt.QueuedConnection to ensure UI updates happen on main thread
        worker.ready.connect(self._apply_ambient, Qt.ConnectionType.QueuedConnection)
        self._ambient_worker = worker
        worker.start()

    def _apply_ambient(self, colors: dict, aspect: float):
        if not self._ambient_widget or not colors:
            return

        # Update aspect ratio from actual video frames (safer than MPV property observer)
        if aspect > 0:
            # Update aspect ratio if it changed
            if abs(aspect - self._video_aspect) > 0.001:
                self._video_aspect = aspect
                self.ambient_container.set_aspect_ratio(aspect)
            # Mark as detected (fallback in case early detection didn't work)
            if not self._aspect_ratio_set:
                self._aspect_ratio_set = True

        # Apply brightness boost to each corner color
        boosted_colors = {}
        for corner in ['top_left', 'top_right', 'bottom_left', 'bottom_right']:
            color = colors.get(corner, QColor(0, 0, 0))

            # Moderate brightness boost to preserve color while ensuring visibility
            # Multiply by 1.3 and add baseline of 30
            boosted_r = min(255, int(color.red() * 1.3) + 30)
            boosted_g = min(255, int(color.green() * 1.3) + 30)
            boosted_b = min(255, int(color.blue() * 1.3) + 30)

            boosted_colors[corner] = QColor(boosted_r, boosted_g, boosted_b)

        # Update the radial gradients at each corner
        self._ambient_widget.set_colors(boosted_colors, self._paused)

    def _show_controls(self):
        self._set_controls_opacity(True)
        self._controls_hide_timer.stop()

    def _hide_controls(self):
        if not self._controls_auto_hide:
            return
        if self._overlay_in_use():
            # Keep controls visible while user is interacting.
            self._controls_hide_timer.start()
            return
        if self._paused or self.seeking or not self._started:
            return
        self._set_controls_opacity(False)

    def _schedule_hide_controls(self):
        if not self._controls_auto_hide:
            return
        if self._paused or not self._started:
            self._show_controls()
            return
        self._set_controls_opacity(True)
        self._controls_hide_timer.start()
        self._auto_hide_force_timer.start()

    def _overlay_in_use(self) -> bool:
        """Return True while the user is hovering or focusing overlay controls."""
        try:
            if not self._overlay_root or not self.controls:
                return False
            if self._overlay_root.underMouse() or self.controls.underMouse():
                return True
            if self.top_gradient and self.top_gradient.underMouse():
                return True
            # Keep visible while actively dragging sliders.
            if hasattr(self, "slider") and getattr(self.slider, "isSliderDown", None):
                if self.slider.isSliderDown():
                    return True
            if hasattr(self, "volume") and getattr(self.volume, "isSliderDown", None):
                if self.volume.isSliderDown():
                    return True
            if self.seeking:
                return True
        except Exception:
            return False
        return False

    def _set_controls_opacity(self, visible: bool):
        if visible == self._controls_visible:
            return
        self._controls_visible = visible
        try:
            self._controls_anim.stop()
            self._top_anim.stop()
            try:
                self._controls_anim.finished.disconnect()
            except Exception:
                pass
            try:
                self._top_anim.finished.disconnect()
            except Exception:
                pass
            start = self._controls_opacity.opacity() if self.controls.isVisible() else (0.0 if visible else 1.0)
            end = 1.0 if visible else 0.0
            self.controls.setVisible(True)
            self.top_gradient.setVisible(True)
            self._controls_anim.setStartValue(start)
            self._controls_anim.setEndValue(end)
            self._controls_anim.start()
            self._top_anim.setStartValue(start)
            self._top_anim.setEndValue(end)
            self._top_anim.start()
            if not visible:
                def _finish_hide():
                    if self._controls_visible:
                        return
                    self.controls.setVisible(False)
                    self.top_gradient.setVisible(False)
                self._controls_anim.finished.connect(_finish_hide)
        except RuntimeError:
            return

    def _force_hide_controls(self):
        if self._paused or self.seeking:
            return
        if self._overlay_in_use():
            return
        if self._controls_visible:
            self._set_controls_opacity(False)

    def set_overlay_enabled(self, enabled: bool) -> None:
        self._overlay_enabled = bool(enabled)
        if not self._overlay_enabled:
            if self._overlay_window:
                self._overlay_window.hide()
            self._set_controls_opacity(False)
            self._set_nav_overlay_visible(False)
            return
        self._sync_overlay_geometry()
        self._show_controls()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_overlay_geometry()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_overlay_geometry)

    def eventFilter(self, obj, event):
        # Safety check for cleanup or uninitialized state
        if self._cleanup_started or not hasattr(self, "ambient_container"):
            return super().eventFilter(obj, event)
        if obj in (self.ambient_container, getattr(self, "video_container", None), getattr(self, "_overlay_root", None), getattr(self, "controls", None)):
            if event.type() in (QEvent.Type.MouseMove, QEvent.Type.Enter):
                self._show_controls()
                self._schedule_hide_controls()
                self._set_nav_overlay_visible(True)
            elif event.type() == QEvent.Type.Leave:
                self._schedule_hide_controls()
                QTimer.singleShot(0, self._refresh_nav_overlay_visibility)
            elif event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
                self._sync_overlay_geometry()
        if obj is getattr(self, "volume_combo", None):
            if event.type() in (QEvent.Type.Enter, QEvent.Type.HoverEnter):
                self._expand_volume_controls()
            elif event.type() in (QEvent.Type.Leave, QEvent.Type.HoverLeave):
                self._collapse_volume_controls()
        if obj is self._fs_window:
            if event.type() in (QEvent.Type.Close, QEvent.Type.Hide):
                self._exit_fullscreen()
            elif event.type() == QEvent.Type.WindowStateChange:
                if self._fs_window and self._fs_window.isMinimized():
                    self._exit_fullscreen()
        if obj is self._overlay_window:
            if event.type() in (QEvent.Type.Close, QEvent.Type.Hide):
                if self._overlay_window:
                    self._overlay_window.hide()
        if obj is self.window():
            if event.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide):
                self._sync_overlay_geometry()
            elif event.type() == QEvent.Type.WindowStateChange:
                if self.window() and self.window().isMinimized():
                    if self._overlay_window:
                        self._overlay_window.hide()
                else:
                    self._sync_overlay_geometry()
        return super().eventFilter(obj, event)

    def _on_app_state_changed(self, state: Qt.ApplicationState):
        # Safety check for cleanup state
        if self._cleanup_started:
            return
        if not self._overlay_window:
            return
        if state != Qt.ApplicationState.ApplicationActive:
            self._overlay_window.hide()
        else:
            self._sync_overlay_geometry()

    def _on_focus_changed(self, old, new):
        """Handle focus changes to detect modal dialogs opening/closing."""
        # Safety check for cleanup state
        if self._cleanup_started:
            return
        # Sync overlay geometry when focus changes - this will hide overlay if modal is active
        QTimer.singleShot(50, self._sync_overlay_geometry)

    def _expand_volume_controls(self):
        # Stop any ongoing collapse animation and disconnect callbacks
        self._volume_slider_anim.stop()
        try:
            self._volume_slider_anim.finished.disconnect()
        except Exception:
            pass
        
        # Show slider and ensure it's rendered
        self.volume.setVisible(True)
        
        # Fade in slider using custom opacity property
        self._volume_slider_anim.setStartValue(self._volume_slider_opacity)
        self._volume_slider_anim.setEndValue(1.0)
        self._volume_slider_anim.start()
        
        # Expand width
        self._animate_volume_width(self._volume_expanded_width)

    def _collapse_volume_controls(self):
        # Fade out slider using custom opacity property
        self._volume_slider_anim.stop()
        try:
            self._volume_slider_anim.finished.disconnect()
        except Exception:
            pass
        self._volume_slider_anim.setStartValue(self._volume_slider_opacity)
        self._volume_slider_anim.setEndValue(0.0)
        # Hide slider only after fade completes
        def _on_fade_complete():
            self.volume.setVisible(False)
        self._volume_slider_anim.finished.connect(_on_fade_complete)
        self._volume_slider_anim.start()
        # Collapse width (without hiding slider in callback)
        self._animate_volume_width(self._volume_collapsed_width, hide_slider=False)
    
    @pyqtProperty(float)
    def _volume_opacity_property(self):
        """Get current volume slider opacity."""
        return self._volume_slider_opacity
    
    @_volume_opacity_property.setter
    def _volume_opacity_property(self, value: float):
        """Set volume slider opacity and update stylesheet."""
        self._volume_slider_opacity = max(0.0, min(1.0, value))
        # Update slider stylesheet with opacity
        base_style = Styles.volume_slider()
        opacity_style = f"QSlider {{ opacity: {self._volume_slider_opacity}; }}"
        self.volume.setStyleSheet(base_style + "\n" + opacity_style)

    def _animate_volume_width(self, target_width: int, hide_slider: bool = False):
        if self._volume_anim:
            self._volume_anim.stop()
        try:
            self._volume_anim.finished.disconnect()
        except Exception:
            pass
        try:
            self._volume_anim.valueChanged.disconnect()
        except Exception:
            pass
        
        # Use fixed width during animation to prevent layout jitter
        current_width = self.volume_combo.width()
        self.volume_combo.setFixedWidth(current_width)
        
        self._volume_anim = QPropertyAnimation(self.volume_combo, b"minimumWidth", self)
        self._volume_anim.setDuration(180)
        self._volume_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        
        self._volume_anim.setStartValue(current_width)
        self._volume_anim.setEndValue(target_width)
        
        # Update fixed width during animation to maintain position lock
        def _update_fixed_width(value):
            self.volume_combo.setFixedWidth(int(value))
        
        self._volume_anim.valueChanged.connect(_update_fixed_width)
        
        # Restore size policy after animation completes
        def _restore_sizing():
            self.volume_combo.setMinimumWidth(target_width)
            self.volume_combo.setMaximumWidth(target_width)
            self.volume_combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        
        self._volume_anim.finished.connect(_restore_sizing)
        self._volume_anim.start()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Space:
            self.toggle_play()
            event.accept()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            if self.duration:
                delta = -5 if key == Qt.Key.Key_Left else 5
                self.player.time_pos = max(0.0, min(self.duration, (self.player.time_pos or 0.0) + delta))  # type: ignore[attr-defined]
            event.accept()
            return
        if key == Qt.Key.Key_M:
            self._toggle_mute()
            event.accept()
            return
        if key == Qt.Key.Key_F:
            self._toggle_fullscreen()
            event.accept()
            return
        if key == Qt.Key.Key_L:
            self._toggle_loop()
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self._fs_window:
            self._exit_fullscreen()
            event.accept()
            return
        super().keyPressEvent(event)

    # --------------------------------------------------

    def capture_frame(self) -> QImage | None:
        try:
            image = self.player.screenshot_raw("video")
        except Exception:
            return None
        if image is None:
            return None
        try:
            if getattr(image, "mode", None) != "RGB":
                image = image.convert("RGB")
            width, height = image.size
            data = image.tobytes("raw", "RGB")
            qimg = QImage(data, width, height, width * 3, QImage.Format.Format_RGB888)
            return qimg.copy()
        except Exception:
            return None

    def cleanup(self):
        # Set cleanup flag to prevent new operations
        self._cleanup_started = True

        # Disconnect app signals to prevent callbacks during cleanup
        try:
            app = QApplication.instance()
            if app and self._app_state_connected:
                try:
                    app.applicationStateChanged.disconnect(self._on_app_state_changed)
                except Exception:
                    pass
                try:
                    app.focusChanged.disconnect(self._on_focus_changed)
                except Exception:
                    pass
                self._app_state_connected = False
        except Exception:
            pass

        # Stop all timers first
        if hasattr(self, '_controls_hide_timer') and self._controls_hide_timer:
            self._controls_hide_timer.stop()
        if hasattr(self, '_auto_hide_force_timer') and self._auto_hide_force_timer:
            self._auto_hide_force_timer.stop()

        # Stop aspect ratio polling timer
        if hasattr(self, '_aspect_poll_timer') and self._aspect_poll_timer:
            self._aspect_poll_timer.stop()

        # Stop ambient timer and worker
        if self._ambient_timer:
            self._ambient_timer.stop()

        # Cancel and wait for ambient worker to finish (avoid terminate())
        if self._ambient_worker:
            self._ambient_worker.cancel()
            # Wait with longer timeout - terminate() is dangerous
            if self._ambient_worker.isRunning():
                if not self._ambient_worker.wait(2000):
                    # Worker didn't finish in time - log but don't terminate
                    # terminate() can corrupt state and cause crashes
                    logger.warning("Ambient worker did not finish in time during cleanup")
            self._ambient_worker = None

        if self._overlay_window:
            try:
                self._overlay_window.close()
            except Exception:
                pass
            self._overlay_window = None
        if self._buffer_poll_timer:
            self._buffer_poll_timer.stop()
        
        try:
            self.player.terminate()
        except Exception:
            pass
