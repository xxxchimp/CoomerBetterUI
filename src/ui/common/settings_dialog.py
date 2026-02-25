"""
Settings dialog for application configuration
"""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
                             QWidget, QLabel, QLineEdit, QPushButton, QCheckBox,
                             QComboBox, QFileDialog, QGroupBox,
                             QFormLayout, QMessageBox, QPlainTextEdit, QSizePolicy,
                             QListWidget, QListWidgetItem, QStackedWidget, QFrame)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QColor
import logging
import json
from pathlib import Path
from src.ui.common.theme import Colors, Spacing
from src.ui.common.vertical_spinbox import VerticalSpinBox

logger = logging.getLogger(__name__)


class ProxyTestThread(QThread):
    """Background thread for testing proxy connections"""
    finished = pyqtSignal(bool, str, str)  # success, message, ip

    def __init__(self, proxy_url: str, username: str, password: str):
        super().__init__()
        self.proxy_url = proxy_url
        self.username = username
        self.password = password
        self._cancelled = False

    def cancel(self):
        """Cancel the thread."""
        self._cancelled = True

    def run(self):
        """Test the proxy connection"""
        if self._cancelled:
            return
            
        from src.core.http_client import test_proxy_connection_sync
        import requests

        try:
            if self._cancelled:
                return
                
            if self.proxy_url:
                # Format proxy URL with auth if provided
                proxy = self.proxy_url
                if self.username and self.password:
                    from urllib.parse import urlparse
                    parsed = urlparse(proxy)
                    if parsed.port:
                        netloc = f"{self.username}:{self.password}@{parsed.hostname}:{parsed.port}"
                    else:
                        netloc = f"{self.username}:{self.password}@{parsed.hostname}"
                    proxy = f"{parsed.scheme}://{netloc}"

                success, message, ip = test_proxy_connection_sync(proxy)
            else:
                # Direct connection test
                test_url = "https://httpbin.org/ip"
                response = requests.get(test_url, timeout=10)
                if response.status_code == 200:
                    ip = response.json().get("origin", "unknown").split(',')[0].strip()
                    success, message = True, "Connected"
                else:
                    success, message, ip = False, f"HTTP {response.status_code}", ""

            if not self._cancelled:
                self.finished.emit(success, message, ip or "")
        except Exception as e:
            if not self._cancelled:
                self.finished.emit(False, str(e), "")


class DockerOperationThread(QThread):
    """Background thread for Docker operations (start/stop containers)"""
    finished = pyqtSignal(bool, str)  # success, message

    def __init__(self, operation: str):
        super().__init__()
        self.operation = operation  # "start" or "stop"
        self._cancelled = False

    def cancel(self):
        """Cancel the thread."""
        self._cancelled = True

    def run(self):
        """Run the Docker operation"""
        if self._cancelled:
            return
            
        try:
            from src.core.docker_manager import DockerManager
            manager = DockerManager()

            if self._cancelled:
                return

            if self.operation == "start":
                success, message = manager.start_containers()
            elif self.operation == "stop":
                success, message = manager.stop_containers()
            else:
                success, message = False, f"Unknown operation: {self.operation}"

            if not self._cancelled:
                self.finished.emit(success, message)
        except Exception as e:
            if not self._cancelled:
                self.finished.emit(False, str(e))


class SettingsDialog(QDialog):
    """Settings dialog for application configuration"""

    def __init__(self, db_manager, parent=None, core_context=None):
        """
        Initialize settings dialog

        Args:
            db_manager: DatabaseManager instance
            parent: Parent widget
            core_context: Optional CoreContext for range proxy access
        """
        super().__init__(parent)

        self.db = db_manager
        self.core_context = core_context
        self.setWindowTitle("Settings")
        self.setObjectName("SettingsDialog")
        # Use theme constants for dialog sizing - fits smaller displays (1024x768)
        self.setMinimumSize(Spacing.DIALOG_MIN_WIDTH, Spacing.DIALOG_MIN_HEIGHT)
        self.resize(Spacing.DIALOG_PREF_WIDTH, Spacing.DIALOG_PREF_HEIGHT)

        # Track running threads for cleanup
        self._proxy_test_thread = None
        self._docker_thread = None

        # Apply Windows dark mode to title bar
        from src.utils.file_utils import apply_windows_dark_mode
        apply_windows_dark_mode(self)

        self._create_ui()
        self._load_settings()

    def closeEvent(self, event):
        """Cleanup running threads when dialog closes."""
        self._cleanup_threads()
        super().closeEvent(event)

    def reject(self):
        """Cleanup when dialog is rejected (Cancel/Escape)."""
        self._cleanup_threads()
        super().reject()

    def _cleanup_threads(self):
        """Cancel and wait for any running threads."""
        if self._proxy_test_thread and self._proxy_test_thread.isRunning():
            self._proxy_test_thread.cancel()
            self._proxy_test_thread.wait(1000)
        if self._docker_thread and self._docker_thread.isRunning():
            self._docker_thread.cancel()
            self._docker_thread.wait(1000)

    def _create_ui(self):
        """Create UI layout"""
        layout = QVBoxLayout(self)

        # Main content area with sidebar navigation
        content_layout = QHBoxLayout()
        content_layout.setSpacing(0)
        content_layout.setContentsMargins(0, 0, 0, 0)
        
        # Left sidebar navigation list
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("SettingsNavList")
        self.nav_list.setFixedWidth(170)
        self.nav_list.setSpacing(0)
        self.nav_list.setStyleSheet(f"""
            QListWidget {{
                background: {Colors.BG_SECONDARY};
                border: none;
                outline: none;
            }}
            QListWidget::item {{
                background: {Colors.BG_SECONDARY};
                color: {Colors.TEXT_PRIMARY};
                border: none;
                border-bottom: 1px solid {Colors.BORDER_DEFAULT};
                padding: 12px 16px;
            }}
            QListWidget::item:selected {{
                background: {Colors.ACCENT_PRIMARY};
                color: #000000;
                font-weight: bold;
            }}
            QListWidget::item:hover:!selected {{
                background: {Colors.BG_TERTIARY};
            }}
        """)
        
        # Add navigation items
        nav_items = [
            "Media & Playback",
            "Downloads", 
            "Storage & Cache",
            "Performance",
            "Network & Proxy",
            "Logging"
        ]
        for item_text in nav_items:
            item = QListWidgetItem(item_text)
            item.setSizeHint(QSize(170, 44))
            self.nav_list.addItem(item)
        
        self.nav_list.setCurrentRow(0)
        content_layout.addWidget(self.nav_list)
        
        # Right side stacked widget for content pages
        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("SettingsPageStack")
        self.page_stack.setStyleSheet(f"""
            QStackedWidget {{
                background: {Colors.BG_PRIMARY};
                border: none;
            }}
        """)
        
        # Add pages
        self.page_stack.addWidget(self._create_media_tab())
        self.page_stack.addWidget(self._create_download_tab())
        self.page_stack.addWidget(self._create_cache_tab())
        self.page_stack.addWidget(self._create_performance_tab())
        self.page_stack.addWidget(self._create_network_tab())
        self.page_stack.addWidget(self._create_logging_tab())
        
        content_layout.addWidget(self.page_stack)
        
        # Connect navigation to page switching
        self.nav_list.currentRowChanged.connect(self.page_stack.setCurrentIndex)
        
        layout.addLayout(content_layout)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        save_btn = QPushButton("Save")
        save_btn.setObjectName("SettingsSaveBtn")
        save_btn.clicked.connect(self._save_settings)
        button_layout.addWidget(save_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("SettingsCancelBtn")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)
    
    def _create_media_tab(self) -> QWidget:
        """Create media and playback settings tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Thumbnails section
        thumb_group = QGroupBox("Thumbnails")
        thumb_group.setObjectName("MediaThumbnailsGroup")
        thumb_layout = QFormLayout(thumb_group)
        
        self.auto_thumbnails_check = QCheckBox("Automatically generate thumbnails for media")
        self.auto_thumbnails_check.setObjectName("AutoThumbnailsCheck")
        self.auto_thumbnails_check.setToolTip("Generate preview thumbnails for images and videos")
        thumb_layout.addRow(self.auto_thumbnails_check)
        
        self.thumbnail_quality_spin = VerticalSpinBox()
        self.thumbnail_quality_spin.setObjectName("ThumbnailQualitySpin")
        self.thumbnail_quality_spin.setRange(1, 100)
        self.thumbnail_quality_spin.setValue(85)
        self.thumbnail_quality_spin.setToolTip("JPEG quality for thumbnails (1-100). Higher = better quality but larger files")
        thumb_layout.addRow("Thumbnail Quality:", self.thumbnail_quality_spin)
        
        layout.addWidget(thumb_group)

        # Video Playback section
        video_group = QGroupBox("Video Playback")
        video_group.setObjectName("VideoPlaybackGroup")
        video_layout = QFormLayout(video_group)

        self.range_proxy_check = QCheckBox("Enable optimized video streaming (recommended)")
        self.range_proxy_check.setObjectName("RangeProxyCheck")
        self.range_proxy_check.setToolTip(
            "Uses intelligent chunk-based streaming for smoother video playback.\n"
            "Recommended for better performance, especially on slower connections."
        )
        video_layout.addRow(self.range_proxy_check)

        layout.addWidget(video_group)

        # Video Thumbnail Generation Limits section
        video_thumb_group = QGroupBox("Video Thumbnail Limits")
        video_thumb_group.setObjectName("VideoThumbnailLimitsGroup")
        video_thumb_group.setToolTip("Control which videos generate thumbnails in the post grid")
        video_thumb_layout = QFormLayout(video_thumb_group)

        self.video_thumb_max_mb_spin = VerticalSpinBox()
        self.video_thumb_max_mb_spin.setObjectName("VideoThumbMaxMbSpin")
        self.video_thumb_max_mb_spin.setRange(0, 5000)
        self.video_thumb_max_mb_spin.setValue(300)
        self.video_thumb_max_mb_spin.setSuffix(" MB")
        self.video_thumb_max_mb_spin.setToolTip(
            "Maximum video file size for thumbnail generation.\n"
            "Set to 0 for no limit (may slow down browsing large videos)"
        )
        self.video_thumb_max_unlimited_check = QCheckBox("Unlimited")
        self.video_thumb_max_unlimited_check.setObjectName("VideoThumbMaxUnlimitedCheck")
        self.video_thumb_max_unlimited_check.setToolTip(
            "No size limit for video thumbnails (may slow down browsing large videos)."
        )
        self.video_thumb_max_unlimited_check.toggled.connect(self._on_video_thumb_max_unlimited)
        max_video_row = QWidget()
        max_video_row_layout = QHBoxLayout(max_video_row)
        max_video_row_layout.setContentsMargins(0, 0, 0, 0)
        max_video_row_layout.setSpacing(8)
        max_video_row_layout.addWidget(self.video_thumb_max_mb_spin)
        max_video_row_layout.addWidget(self.video_thumb_max_unlimited_check)
        video_thumb_layout.addRow("Max Video Size:", max_video_row)

        self.video_thumb_non_fast_mb_spin = VerticalSpinBox()
        self.video_thumb_non_fast_mb_spin.setObjectName("VideoThumbNonFastMbSpin")
        self.video_thumb_non_fast_mb_spin.setRange(0, 2000)
        self.video_thumb_non_fast_mb_spin.setValue(20)
        self.video_thumb_non_fast_mb_spin.setSuffix(" MB")
        self.video_thumb_non_fast_mb_spin.setToolTip(
            "Size limit for non-optimized MP4 files (non-faststart).\n"
            "Lower than regular limit because these require full download to generate thumbnail.\n"
            "Set to 0 for no limit (not recommended)"
        )
        self.video_thumb_non_fast_unlimited_check = QCheckBox("Unlimited")
        self.video_thumb_non_fast_unlimited_check.setObjectName("VideoThumbNonFastUnlimitedCheck")
        self.video_thumb_non_fast_unlimited_check.setToolTip(
            "No size limit for non-optimized MP4 thumbnails (use with caution)."
        )
        self.video_thumb_non_fast_unlimited_check.toggled.connect(self._on_video_thumb_non_fast_unlimited)
        non_fast_row = QWidget()
        non_fast_row_layout = QHBoxLayout(non_fast_row)
        non_fast_row_layout.setContentsMargins(0, 0, 0, 0)
        non_fast_row_layout.setSpacing(8)
        non_fast_row_layout.addWidget(self.video_thumb_non_fast_mb_spin)
        non_fast_row_layout.addWidget(self.video_thumb_non_fast_unlimited_check)
        video_thumb_layout.addRow("Max Non-Optimized MP4:", non_fast_row)

        # NOTE: video_thumb_retries and video_thumb_retry_delay are NOT used in codebase
        # Removed to avoid confusion

        layout.addWidget(video_thumb_group)

        # Content Display section
        content_group = QGroupBox("Content Display")
        content_group.setObjectName("ContentDisplayGroup")
        content_layout = QFormLayout(content_group)

        self.allow_post_content_media_check = QCheckBox(
            "Display embedded images and iframes in post descriptions"
        )
        self.allow_post_content_media_check.setObjectName("AllowPostContentMediaCheck")
        self.allow_post_content_media_check.setToolTip(
            "When enabled, post descriptions can show embedded images and external content.\n"
            "Disable if you experience loading issues or prefer text-only descriptions."
        )
        content_layout.addRow(self.allow_post_content_media_check)

        layout.addWidget(content_group)
        layout.addStretch()

        return widget

    def _on_video_thumb_max_unlimited(self, checked: bool) -> None:
        if hasattr(self, "video_thumb_max_mb_spin"):
            self.video_thumb_max_mb_spin.setEnabled(not checked)
            if checked:
                self.video_thumb_max_mb_spin.setValue(0)

    def _on_video_thumb_non_fast_unlimited(self, checked: bool) -> None:
        if hasattr(self, "video_thumb_non_fast_mb_spin"):
            self.video_thumb_non_fast_mb_spin.setEnabled(not checked)
            if checked:
                self.video_thumb_non_fast_mb_spin.setValue(0)
    
    def _create_download_tab(self) -> QWidget:
        """Create download settings tab"""
        widget = QWidget()
        widget.setObjectName("DownloadTab")
        layout = QVBoxLayout(widget)
        
        # Download Location section
        location_group = QGroupBox("Download Location")
        location_group.setObjectName("DownloadLocationGroup")
        location_layout = QFormLayout(location_group)
        
        download_layout = QHBoxLayout()
        self.download_dir_edit = QLineEdit()
        self.download_dir_edit.setObjectName("DownloadDirEdit")
        self.download_dir_edit.setPlaceholderText("Select download folder...")
        download_browse_btn = QPushButton("Browse...")
        download_browse_btn.setObjectName("DownloadBrowseBtn")
        download_browse_btn.clicked.connect(self._browse_download_dir)
        download_layout.addWidget(self.download_dir_edit)
        download_layout.addWidget(download_browse_btn)
        location_layout.addRow("Save Files To:", download_layout)
        
        layout.addWidget(location_group)
        
        # Download Behavior section
        behavior_group = QGroupBox("Download Settings")
        behavior_group.setObjectName("DownloadBehaviorGroup")
        behavior_layout = QFormLayout(behavior_group)
        
        self.max_downloads_spin = VerticalSpinBox()
        self.max_downloads_spin.setObjectName("MaxDownloadsSpin")
        self.max_downloads_spin.setRange(1, 10)
        self.max_downloads_spin.setValue(3)
        self.max_downloads_spin.setToolTip(
            "Number of files to download simultaneously.\n"
            "Higher values = faster downloads but more network usage"
        )
        behavior_layout.addRow("Concurrent Downloads:", self.max_downloads_spin)
        
        # NOTE: enable_batch_download setting exists but is NOT used in the codebase
        # Batch downloads are always available regardless of this setting
        # Removed to avoid confusion
        
        self.structured_downloads_check = QCheckBox(
            "Organize downloads into folders by platform/service/creator/post"
        )
        self.structured_downloads_check.setObjectName("StructuredDownloadsCheck")
        self.structured_downloads_check.setToolTip(
            "Creates a folder structure like:\n"
            "Downloads/coomer/patreon/CreatorName/PostID/\n\n"
            "When disabled, all files go directly to the download folder"
        )
        behavior_layout.addRow(self.structured_downloads_check)

        layout.addWidget(behavior_group)
        
        # JDownloader Integration section
        jd_group = QGroupBox("JDownloader Integration")
        jd_group.setObjectName("JDownloaderGroup")
        jd_layout = QFormLayout(jd_group)
        
        self.jdownloader_enabled_check = QCheckBox("Enable JDownloader export option")
        self.jdownloader_enabled_check.setObjectName("JDownloaderEnabledCheck")
        self.jdownloader_enabled_check.setToolTip(
            "When enabled, adds an option to export downloads as .crawljob files\n"
            "that JDownloader's Directory Watch extension can pick up automatically"
        )
        self.jdownloader_enabled_check.stateChanged.connect(self._on_jdownloader_toggle)
        jd_layout.addRow(self.jdownloader_enabled_check)
        
        jd_watch_layout = QHBoxLayout()
        self.jdownloader_watch_dir_edit = QLineEdit()
        self.jdownloader_watch_dir_edit.setObjectName("JDownloaderWatchDirEdit")
        self.jdownloader_watch_dir_edit.setPlaceholderText("JDownloader folderwatch folder path...")
        jd_browse_btn = QPushButton("Browse...")
        jd_browse_btn.setObjectName("JDownloaderBrowseBtn")
        jd_browse_btn.clicked.connect(self._browse_jdownloader_dir)
        jd_watch_layout.addWidget(self.jdownloader_watch_dir_edit)
        jd_watch_layout.addWidget(jd_browse_btn)
        jd_layout.addRow("Watch Folder:", jd_watch_layout)
        
        jd_help = QLabel(
            "<small>Default: <code>%APPDATA%\\JDownloader 2.0\\folderwatch</code> (Windows)<br>"
            "The watch folder is monitored by JDownloader's Directory Watch extension.</small>"
        )
        jd_help.setObjectName("JDownloaderHelpLabel")
        jd_help.setWordWrap(True)
        jd_help.setStyleSheet(f"color: {Colors.TEXT_MUTED}; padding-left: 5px;")
        jd_layout.addRow(jd_help)
        
        layout.addWidget(jd_group)
        layout.addStretch()

        return widget
    
    def _create_cache_tab(self) -> QWidget:
        """Create storage and cache settings tab"""
        widget = QWidget()
        widget.setObjectName("CacheTab")
        layout = QVBoxLayout(widget)

        # Description
        desc_label = QLabel(
            "Caches improve performance by storing frequently accessed data locally.\n"
            "Adjust these settings to balance performance with disk space usage."
        )
        desc_label.setObjectName("CacheDescLabel")
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; margin-bottom: 10px;")
        layout.addWidget(desc_label)

        # API Response Cache Group
        api_cache_group = QGroupBox("API Response Cache")
        api_cache_group.setObjectName("APICacheGroup")
        api_cache_layout = QFormLayout(api_cache_group)

        self.cache_size_spin = VerticalSpinBox()
        self.cache_size_spin.setObjectName("CacheSizeSpin")
        self.cache_size_spin.setRange(100, 50000)
        self.cache_size_spin.setSuffix(" MB")
        self.cache_size_spin.setValue(5000)
        self.cache_size_spin.setToolTip(
            "Maximum disk space for cached API responses and metadata.\n"
            "Larger cache = fewer API calls but more disk space used"
        )
        api_cache_layout.addRow("Max Size:", self.cache_size_spin)

        self.cache_stats_label = QLabel()
        self.cache_stats_label.setObjectName("CacheStatsLabel")
        api_cache_layout.addRow("Current Usage:", self.cache_stats_label)

        self.cleanup_days_spin = VerticalSpinBox()
        self.cleanup_days_spin.setObjectName("CleanupDaysSpin")
        self.cleanup_days_spin.setRange(1, 365)
        self.cleanup_days_spin.setSuffix(" days")
        self.cleanup_days_spin.setValue(30)
        self.cleanup_days_spin.setToolTip("Automatically remove cached data older than this")
        api_cache_layout.addRow("Auto-Cleanup Age:", self.cleanup_days_spin)

        layout.addWidget(api_cache_group)

        # Video Streaming Cache Group with inline metrics
        range_cache_group = QGroupBox("Video Streaming Cache")
        range_cache_group.setObjectName("RangeCacheGroup")
        range_cache_group.setToolTip("Cache for optimized video streaming (when enabled)")
        range_cache_layout = QFormLayout(range_cache_group)

        self.range_cache_size_spin = VerticalSpinBox()
        self.range_cache_size_spin.setObjectName("RangeCacheSizeSpin")
        self.range_cache_size_spin.setRange(1, 100)
        self.range_cache_size_spin.setSuffix(" GB")
        self.range_cache_size_spin.setValue(10)
        self.range_cache_size_spin.setToolTip(
            "Maximum disk space for video streaming cache.\n"
            "Used when 'optimized video streaming' is enabled.\n"
            "Oldest chunks removed when limit exceeded"
        )
        range_cache_layout.addRow("Max Size:", self.range_cache_size_spin)

        self.range_cache_age_spin = VerticalSpinBox()
        self.range_cache_age_spin.setRange(1, 180)
        self.range_cache_age_spin.setSuffix(" days")
        self.range_cache_age_spin.setValue(30)
        self.range_cache_age_spin.setToolTip("Automatically remove cached chunks older than this")
        range_cache_layout.addRow("Auto-Cleanup Age:", self.range_cache_age_spin)

        self.range_cache_stats_label = QLabel("Not available")
        range_cache_layout.addRow("Current Usage:", self.range_cache_stats_label)
        
        # Inline streaming metrics
        self.range_metrics_label = QLabel("Metrics: Not available")
        self.range_metrics_label.setObjectName("RangeMetricsLabel")
        self.range_metrics_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        range_cache_layout.addRow("Performance:", self.range_metrics_label)

        layout.addWidget(range_cache_group)

        # Simplified cache management - single button to open clear dialog
        clear_data_btn = QPushButton("Clear Data...")
        clear_data_btn.setObjectName("ClearDataBtn")
        clear_data_btn.setMinimumHeight(Spacing.BUTTON_HEIGHT)
        clear_data_btn.clicked.connect(self._show_clear_data_dialog)
        clear_data_btn.setToolTip("Choose what cached data to clear")
        layout.addWidget(clear_data_btn)

        layout.addStretch()

        return widget
    
    def _create_performance_tab(self) -> QWidget:
        """Create performance settings tab"""
        widget = QWidget()
        widget.setObjectName("PerformanceTab")
        layout = QVBoxLayout(widget)
        
        # Warning label
        warning_label = QLabel(
            "⚠️ Advanced Settings - Changes may affect performance and stability.\n"
            "Only modify these if you understand their impact."
        )
        warning_label.setObjectName("PerformanceWarningLabel")
        warning_label.setWordWrap(True)
        warning_label.setStyleSheet(f"color: {Colors.ACCENT_WARNING}; margin-bottom: 10px; font-weight: bold;")
        layout.addWidget(warning_label)
        
        # Thumbnail Generation section
        thumb_perf_group = QGroupBox("Thumbnail Generation")
        thumb_perf_group.setObjectName("ThumbPerfGroup")
        thumb_perf_layout = QFormLayout(thumb_perf_group)
        
        self.thumb_image_workers_spin = VerticalSpinBox()
        self.thumb_image_workers_spin.setObjectName("ThumbImageWorkersSpin")
        self.thumb_image_workers_spin.setRange(1, 16)
        self.thumb_image_workers_spin.setValue(6)
        self.thumb_image_workers_spin.setToolTip(
            "Concurrent threads for image thumbnail generation.\n"
            "Higher = faster thumbnail loading but more CPU usage.\n"
            "Recommended: 4-8 threads"
        )
        thumb_perf_layout.addRow("Image Workers:", self.thumb_image_workers_spin)
        
        self.thumb_video_workers_spin = VerticalSpinBox()
        self.thumb_video_workers_spin.setObjectName("ThumbVideoWorkersSpin")
        self.thumb_video_workers_spin.setRange(1, 8)
        self.thumb_video_workers_spin.setValue(2)
        self.thumb_video_workers_spin.setToolTip(
            "Concurrent threads for video thumbnail generation.\n"
            "Video processing is CPU-intensive - keep this lower.\n"
            "Recommended: 1-3 threads"
        )
        thumb_perf_layout.addRow("Video Workers:", self.thumb_video_workers_spin)
        
        self.thumb_video_queue_spin = VerticalSpinBox()
        self.thumb_video_queue_spin.setObjectName("ThumbVideoQueueSpin")
        self.thumb_video_queue_spin.setRange(1, 100)
        self.thumb_video_queue_spin.setValue(10)
        self.thumb_video_queue_spin.setToolTip(
            "Maximum queued video thumbnail requests before throttling.\n"
            "Higher values allow more videos to queue but use more memory.\n"
            "Recommended: 10-20"
        )
        thumb_perf_layout.addRow("Video Queue Limit:", self.thumb_video_queue_spin)
        
        layout.addWidget(thumb_perf_group)
        
        # Network Performance section
        network_group = QGroupBox("Network Performance")
        network_group.setObjectName("NetworkPerfGroup")
        network_layout = QFormLayout(network_group)
        
        self.range_proxy_max_concurrent_spin = VerticalSpinBox()
        self.range_proxy_max_concurrent_spin.setObjectName("RangeProxyMaxConcurrentSpin")
        self.range_proxy_max_concurrent_spin.setRange(1, 20)
        self.range_proxy_max_concurrent_spin.setValue(5)
        self.range_proxy_max_concurrent_spin.setToolTip(
            "Concurrent chunk downloads per video stream.\n"
            "Higher = smoother video seeking but more connections.\n"
            "Recommended: 3-8"
        )
        network_layout.addRow("Video Stream Chunks:", self.range_proxy_max_concurrent_spin)
        
        self.max_connections_per_host_spin = VerticalSpinBox()
        self.max_connections_per_host_spin.setObjectName("MaxConnectionsPerHostSpin")
        self.max_connections_per_host_spin.setRange(1, 50)
        self.max_connections_per_host_spin.setValue(10)
        self.max_connections_per_host_spin.setToolTip(
            "Maximum simultaneous connections to each server.\n"
            "Higher values may trigger rate limiting.\n"
            "Recommended: 8-15"
        )
        network_layout.addRow("Connections Per Host:", self.max_connections_per_host_spin)
        
        self.max_total_connections_spin = VerticalSpinBox()
        self.max_total_connections_spin.setObjectName("MaxTotalConnectionsSpin")
        self.max_total_connections_spin.setRange(10, 500)
        self.max_total_connections_spin.setValue(100)
        self.max_total_connections_spin.setToolTip(
            "Total simultaneous connections across all servers.\n"
            "Controls overall network resource usage.\n"
            "Recommended: 50-150"
        )
        network_layout.addRow("Total Connections:", self.max_total_connections_spin)
        
        layout.addWidget(network_group)
        
        # Other Settings section
        other_group = QGroupBox("Other Settings")
        other_group.setObjectName("OtherPerfGroup")
        other_layout = QFormLayout(other_group)
        
        self.user_agent_edit = QLineEdit()
        self.user_agent_edit.setObjectName("UserAgentEdit")
        self.user_agent_edit.setPlaceholderText("Default user agent...")
        self.user_agent_edit.setToolTip(
            "HTTP User-Agent header sent with requests.\n"
            "Only change if experiencing access issues."
        )
        other_layout.addRow("User Agent:", self.user_agent_edit)
        
        layout.addWidget(other_group)
        layout.addStretch()

        return widget

    def _create_network_tab(self) -> QWidget:
        """Create network and proxy settings tab"""
        from PyQt6.QtWidgets import QScrollArea
        
        # Create scroll area for the tab
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setObjectName("NetworkProxyTabScroll")
        
        widget = QWidget()
        widget.setObjectName("NetworkProxyTab")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # Proxy Configuration section
        proxy_group = QGroupBox("Proxy Configuration")
        proxy_group.setObjectName("ProxyConfigGroup")
        proxy_layout = QFormLayout(proxy_group)

        self.proxy_enabled_check = QCheckBox("Enable proxy for media downloads")
        self.proxy_enabled_check.setObjectName("ProxyEnabledCheck")
        self.proxy_enabled_check.setToolTip(
            "Route media downloads (images, videos) through proxy servers.\n"
            "API requests always use direct connection for reliability.\n"
            "Useful for VPN split tunneling or bypassing geo-restrictions."
        )
        self.proxy_enabled_check.stateChanged.connect(self._on_proxy_enabled_changed)
        proxy_layout.addRow(self.proxy_enabled_check)

        self.proxy_url_edit = QLineEdit()
        self.proxy_url_edit.setObjectName("ProxyUrlEdit")
        self.proxy_url_edit.setPlaceholderText("socks5://127.0.0.1:1080 or http://proxy:port")
        self.proxy_url_edit.setToolTip(
            "Proxy URL in format:\n"
            "  socks5://host:port\n"
            "  socks5h://host:port (DNS through proxy)\n"
            "  http://host:port\n\n"
            "Leave empty to use proxy pool instead."
        )
        proxy_layout.addRow("Proxy URL:", self.proxy_url_edit)

        layout.addWidget(proxy_group)

        # Proxy Pool section
        pool_group = QGroupBox("Proxy Pool (Optional)")
        pool_group.setObjectName("ProxyPoolGroup")
        pool_group.setToolTip("Configure multiple proxies for rotation")
        pool_layout = QVBoxLayout(pool_group)

        pool_desc = QLabel(
            "Add multiple proxy URLs (one per line) for automatic rotation.\n"
            "Used instead of single proxy URL when populated."
        )
        pool_desc.setObjectName("ProxyPoolDescription")
        pool_layout.addWidget(pool_desc)

        self.proxy_pool_edit = QPlainTextEdit()
        self.proxy_pool_edit.setObjectName("ProxyPoolEdit")
        self.proxy_pool_edit.setPlaceholderText(
            "socks5://localhost:1081\n"
            "socks5://localhost:1082\n"
            "socks5://localhost:1083"
        )
        self.proxy_pool_edit.setMinimumHeight(Spacing.MULTILINE_MIN_HEIGHT)
        self.proxy_pool_edit.setMaximumHeight(Spacing.MULTILINE_MAX_HEIGHT)
        self.proxy_pool_edit.setToolTip(
            "One proxy URL per line.\n"
            "Failed proxies will be temporarily skipped."
        )
        pool_layout.addWidget(self.proxy_pool_edit)

        pool_options_layout = QHBoxLayout()
        pool_options_layout.addWidget(QLabel("Rotation:"))
        self.proxy_rotation_combo = QComboBox()
        self.proxy_rotation_combo.setObjectName("ProxyRotationCombo")
        self.proxy_rotation_combo.addItems(["Round Robin", "Random", "Least Used"])
        self.proxy_rotation_combo.setToolTip(
            "Round Robin: Cycle through proxies in order\n"
            "Random: Select random proxy each request\n"
            "Least Used: Prefer proxies used least often"
        )
        pool_options_layout.addWidget(self.proxy_rotation_combo)
        pool_options_layout.addStretch()
        pool_layout.addLayout(pool_options_layout)

        layout.addWidget(pool_group)

        # Proxy Authentication section
        auth_group = QGroupBox("Proxy Authentication (Optional)")
        auth_group.setObjectName("ProxyAuthGroup")
        auth_layout = QFormLayout(auth_group)

        self.proxy_username_edit = QLineEdit()
        self.proxy_username_edit.setObjectName("ProxyUsernameEdit")
        self.proxy_username_edit.setPlaceholderText("Username")
        self.proxy_username_edit.setToolTip("Proxy authentication username (if required)")
        auth_layout.addRow("Username:", self.proxy_username_edit)

        self.proxy_password_edit = QLineEdit()
        self.proxy_password_edit.setObjectName("ProxyPasswordEdit")
        self.proxy_password_edit.setPlaceholderText("Password")
        self.proxy_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.proxy_password_edit.setToolTip("Proxy authentication password (stored encrypted)")
        auth_layout.addRow("Password:", self.proxy_password_edit)

        layout.addWidget(auth_group)

        # Request Pacing section
        pacing_group = QGroupBox("Request Pacing")
        pacing_group.setObjectName("RequestPacingGroup")
        pacing_layout = QFormLayout(pacing_group)

        self.request_delay_spin = VerticalSpinBox()
        self.request_delay_spin.setObjectName("RequestDelaySpinBox")
        self.request_delay_spin.setRange(0, 5000)
        self.request_delay_spin.setValue(0)
        self.request_delay_spin.setSuffix(" ms")
        self.request_delay_spin.setToolTip(
            "Minimum delay between HTTP requests.\n"
            "Helps avoid rate limiting. 0 = no delay.\n"
            "Recommended: 100-500ms if experiencing blocks."
        )
        pacing_layout.addRow("Request Delay:", self.request_delay_spin)

        layout.addWidget(pacing_group)
        
        # Info note about restart requirement
        info_note = QLabel("⚠ Proxy changes require app restart to take effect")
        info_note.setObjectName("ProxyRestartNote")
        info_note.setStyleSheet("color: #f39c12; font-weight: bold; padding: 8px;")
        layout.addWidget(info_note)

        # Test Connection section
        test_group = QGroupBox("Connection Test")
        test_group.setObjectName("ConnectionTestGroup")
        test_layout = QVBoxLayout(test_group)

        test_btn_layout = QHBoxLayout()
        self.test_proxy_btn = QPushButton("Test Proxy Connection")
        self.test_proxy_btn.setObjectName("TestProxyBtn")
        self.test_proxy_btn.clicked.connect(self._test_proxy_connection)
        self.test_proxy_btn.setToolTip("Test if the configured proxy is working")
        test_btn_layout.addWidget(self.test_proxy_btn)

        self.test_direct_btn = QPushButton("Test Direct Connection")
        self.test_direct_btn.setObjectName("TestDirectBtn")
        self.test_direct_btn.clicked.connect(self._test_direct_connection)
        self.test_direct_btn.setToolTip("Test direct connection without proxy")
        test_btn_layout.addWidget(self.test_direct_btn)
        test_btn_layout.addStretch()
        test_layout.addLayout(test_btn_layout)

        self.connection_status_label = QLabel("Status: Not tested")
        self.connection_status_label.setObjectName("ConnectionStatusLabel")
        self.connection_status_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        test_layout.addWidget(self.connection_status_label)

        layout.addWidget(test_group)

        # Docker VPN Setup section
        docker_group = QGroupBox("Docker VPN Proxy Setup (Advanced)")
        docker_group.setObjectName("DockerVPNSetupGroup")
        docker_group.setToolTip(
            "Set up multiple VPN proxy containers using Docker.\n"
            "Requires Docker Desktop to be installed and running."
        )
        docker_layout = QVBoxLayout(docker_group)

        # Docker status
        docker_status_layout = QHBoxLayout()
        self.docker_status_label = QLabel("Docker: Checking...")
        self.docker_status_label.setObjectName("DockerStatusLabel")
        docker_status_layout.addWidget(self.docker_status_label)
        self.refresh_docker_btn = QPushButton("Refresh")
        self.refresh_docker_btn.setObjectName("RefreshDockerBtn")
        self.refresh_docker_btn.setFixedWidth(80)
        self.refresh_docker_btn.clicked.connect(self._check_docker_status)
        docker_status_layout.addWidget(self.refresh_docker_btn)
        docker_status_layout.addStretch()
        docker_layout.addLayout(docker_status_layout)

        # VPN Provider selection
        provider_layout = QHBoxLayout()
        provider_layout.addWidget(QLabel("VPN Provider:"))
        self.vpn_provider_combo = QComboBox()
        self.vpn_provider_combo.setObjectName("VPNProviderCombo")
        self.vpn_provider_combo.addItems([
            "NordVPN", "Mullvad", "Surfshark", "ExpressVPN", "ProtonVPN",
            "Private Internet Access", "Windscribe"
        ])
        self.vpn_provider_combo.currentTextChanged.connect(self._on_vpn_provider_changed)
        provider_layout.addWidget(self.vpn_provider_combo)
        provider_layout.addStretch()
        docker_layout.addLayout(provider_layout)

        # VPN Type selection
        vpn_type_layout = QHBoxLayout()
        vpn_type_layout.addWidget(QLabel("VPN Type:"))
        self.vpn_type_combo = QComboBox()
        self.vpn_type_combo.setObjectName("VPNTypeCombo")
        self.vpn_type_combo.addItems(["WireGuard", "OpenVPN"])
        self.vpn_type_combo.currentTextChanged.connect(self._on_vpn_type_changed)
        vpn_type_layout.addWidget(self.vpn_type_combo)
        vpn_type_layout.addStretch()
        docker_layout.addLayout(vpn_type_layout)

        # Regions input
        regions_layout = QVBoxLayout()
        regions_label = QLabel("Regions (one per line, creates one proxy per region):")
        regions_label.setObjectName("VPNRegionsLabel")
        regions_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;")
        regions_layout.addWidget(regions_label)
        self.vpn_regions_edit = QPlainTextEdit()
        self.vpn_regions_edit.setObjectName("VPNRegionsEdit")
        self.vpn_regions_edit.setPlaceholderText("United States\nGermany\nJapan")
        self.vpn_regions_edit.setMinimumHeight(Spacing.MULTILINE_MIN_HEIGHT)
        self.vpn_regions_edit.setMaximumHeight(Spacing.MULTILINE_MAX_HEIGHT)
        regions_layout.addWidget(self.vpn_regions_edit)
        docker_layout.addLayout(regions_layout)

        # Credentials section
        creds_layout = QFormLayout()
        self.vpn_cred1_edit = QLineEdit()
        self.vpn_cred1_edit.setObjectName("VPNCred1Edit")
        self.vpn_cred1_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.vpn_cred1_edit.setPlaceholderText("WireGuard Private Key")
        self.vpn_cred1_label = QLabel("Private Key:")
        self.vpn_cred1_label.setObjectName("VPNCred1Label")
        creds_layout.addRow(self.vpn_cred1_label, self.vpn_cred1_edit)

        self.vpn_cred2_edit = QLineEdit()
        self.vpn_cred2_edit.setObjectName("VPNCred2Edit")
        self.vpn_cred2_edit.setPlaceholderText("Optional")
        self.vpn_cred2_label = QLabel("Addresses:")
        self.vpn_cred2_label.setObjectName("VPNCred2Label")
        creds_layout.addRow(self.vpn_cred2_label, self.vpn_cred2_edit)
        docker_layout.addLayout(creds_layout)

        # Provider docs link
        self.provider_docs_label = QLabel()
        self.provider_docs_label.setObjectName("ProviderDocsLabel")
        self.provider_docs_label.setOpenExternalLinks(True)
        self.provider_docs_label.setStyleSheet(f"color: {Colors.ACCENT_PRIMARY}; font-size: 11px;")
        docker_layout.addWidget(self.provider_docs_label)

        # Docker action buttons
        docker_btn_layout = QHBoxLayout()
        self.generate_compose_btn = QPushButton("Generate docker-compose.yml")
        self.generate_compose_btn.setObjectName("GenerateComposeBtn")
        self.generate_compose_btn.clicked.connect(self._generate_docker_compose)
        docker_btn_layout.addWidget(self.generate_compose_btn)

        self.start_containers_btn = QPushButton("Start Containers")
        self.start_containers_btn.setObjectName("StartContainersBtn")
        self.start_containers_btn.clicked.connect(self._start_docker_containers)
        docker_btn_layout.addWidget(self.start_containers_btn)

        self.stop_containers_btn = QPushButton("Stop Containers")
        self.stop_containers_btn.setObjectName("StopContainersBtn")
        self.stop_containers_btn.clicked.connect(self._stop_docker_containers)
        docker_btn_layout.addWidget(self.stop_containers_btn)
        docker_layout.addLayout(docker_btn_layout)

        # Container status
        self.container_status_label = QLabel("Containers: Not configured")
        self.container_status_label.setObjectName("ContainerStatusLabel")
        self.container_status_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        docker_layout.addWidget(self.container_status_label)

        # Apply to proxy pool button
        self.apply_docker_proxies_btn = QPushButton("Apply Running Proxies to Pool Above")
        self.apply_docker_proxies_btn.setObjectName("ApplyDockerProxiesBtn")
        self.apply_docker_proxies_btn.clicked.connect(self._apply_docker_proxies_to_pool)
        self.apply_docker_proxies_btn.setToolTip(
            "Detect running Docker VPN proxies and add them to the proxy pool"
        )
        docker_layout.addWidget(self.apply_docker_proxies_btn)

        layout.addWidget(docker_group)
        
        # Add some bottom padding
        layout.addSpacing(20)
        
        scroll.setWidget(widget)
        return scroll

    def _on_proxy_enabled_changed(self, state):
        """Handle proxy enabled checkbox state change"""
        enabled = state == Qt.CheckState.Checked.value
        self.proxy_url_edit.setEnabled(enabled)
        self.proxy_pool_edit.setEnabled(enabled)
        self.proxy_rotation_combo.setEnabled(enabled)
        self.proxy_username_edit.setEnabled(enabled)
        self.proxy_password_edit.setEnabled(enabled)
        self.test_proxy_btn.setEnabled(enabled)

    def _test_proxy_connection(self):
        """Test proxy connection"""
        proxy_url = self.proxy_url_edit.text().strip()
        pool_text = self.proxy_pool_edit.toPlainText().strip()

        # Get proxy to test
        if pool_text:
            proxies = [p.strip() for p in pool_text.split('\n') if p.strip()]
            if proxies:
                proxy_url = proxies[0]  # Test first proxy in pool

        if not proxy_url:
            QMessageBox.warning(
                self,
                "No Proxy Configured",
                "Please enter a proxy URL or add proxies to the pool."
            )
            return

        # Add auth if configured
        username = self.proxy_username_edit.text().strip()
        password = self.proxy_password_edit.text().strip()

        self.connection_status_label.setText("Status: Testing...")
        self.connection_status_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        self.test_proxy_btn.setEnabled(False)

        # Run test in background thread
        self._proxy_test_thread = ProxyTestThread(proxy_url, username, password)
        self._proxy_test_thread.finished.connect(self._on_proxy_test_complete)
        self._proxy_test_thread.start()

    def _on_proxy_test_complete(self, success: bool, message: str, ip: str):
        """Handle proxy test completion"""
        self.test_proxy_btn.setEnabled(True)

        if success:
            self.connection_status_label.setText(f"Status: Connected ✓  IP: {ip}")
            self.connection_status_label.setStyleSheet(f"color: {Colors.ACCENT_SUCCESS};")
        else:
            self.connection_status_label.setText(f"Status: Failed ✗  {message}")
            self.connection_status_label.setStyleSheet(f"color: {Colors.ACCENT_ERROR};")

    def _test_direct_connection(self):
        """Test direct connection without proxy"""
        self.connection_status_label.setText("Status: Testing direct connection...")
        self.connection_status_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        self.test_direct_btn.setEnabled(False)

        # Run test in background thread
        self._direct_test_thread = ProxyTestThread(None, None, None)
        self._direct_test_thread.finished.connect(self._on_direct_test_complete)
        self._direct_test_thread.start()

    def _on_direct_test_complete(self, success: bool, message: str, ip: str):
        """Handle direct connection test completion"""
        self.test_direct_btn.setEnabled(True)

        if success:
            self.connection_status_label.setText(f"Status: Direct connection OK ✓  Your IP: {ip}")
            self.connection_status_label.setStyleSheet(f"color: {Colors.ACCENT_SUCCESS};")
        else:
            self.connection_status_label.setText(f"Status: Direct connection failed ✗  {message}")
            self.connection_status_label.setStyleSheet(f"color: {Colors.ACCENT_ERROR};")

    # --- Docker VPN Setup Methods ---

    def _check_docker_status(self):
        """Check Docker installation and running status"""
        try:
            from src.core.docker_manager import DockerManager
            manager = DockerManager()
            status = manager.check_docker_status()

            if not status.installed:
                self.docker_status_label.setText("Docker: Not installed")
                self.docker_status_label.setStyleSheet(f"color: {Colors.ACCENT_ERROR};")
                self._set_docker_controls_enabled(False)
            elif not status.running:
                self.docker_status_label.setText("Docker: Not running (start Docker Desktop)")
                self.docker_status_label.setStyleSheet(f"color: {Colors.ACCENT_WARNING};")
                self._set_docker_controls_enabled(False)
            else:
                self.docker_status_label.setText(
                    f"Docker: Running ✓  v{status.version}"
                )
                self.docker_status_label.setStyleSheet(f"color: {Colors.ACCENT_SUCCESS};")
                self._set_docker_controls_enabled(True)
                self._update_container_status()

        except Exception as e:
            self.docker_status_label.setText(f"Docker: Error - {e}")
            self.docker_status_label.setStyleSheet(f"color: {Colors.ACCENT_ERROR};")
            self._set_docker_controls_enabled(False)

    def _set_docker_controls_enabled(self, enabled: bool):
        """Enable or disable Docker-related controls"""
        self.vpn_provider_combo.setEnabled(enabled)
        self.vpn_type_combo.setEnabled(enabled)
        self.vpn_regions_edit.setEnabled(enabled)
        self.vpn_cred1_edit.setEnabled(enabled)
        self.vpn_cred2_edit.setEnabled(enabled)
        self.generate_compose_btn.setEnabled(enabled)
        self.start_containers_btn.setEnabled(enabled)
        self.stop_containers_btn.setEnabled(enabled)
        self.apply_docker_proxies_btn.setEnabled(enabled)

    def _on_vpn_provider_changed(self, provider_name: str):
        """Handle VPN provider selection change"""
        from src.core.docker_manager import get_provider_info

        provider_key = provider_name.lower()
        if provider_key == "private internet access":
            provider_key = "private internet access"

        info = get_provider_info(provider_key)
        if not info:
            return

        # Update VPN type options
        self.vpn_type_combo.clear()
        vpn_types = info.get("vpn_types", ["wireguard", "openvpn"])
        self.vpn_type_combo.addItems([t.title() for t in vpn_types])

        # Set default type
        default_type = info.get("default_type", "wireguard")
        index = self.vpn_type_combo.findText(default_type.title())
        if index >= 0:
            self.vpn_type_combo.setCurrentIndex(index)

        # Update sample regions
        sample_regions = info.get("sample_regions", [])
        self.vpn_regions_edit.setPlaceholderText("\n".join(sample_regions[:3]))

        # Update docs link
        docs_url = info.get("docs_url", "")
        if docs_url:
            self.provider_docs_label.setText(
                f'<a href="{docs_url}">View {provider_name} setup guide</a>'
            )
        else:
            self.provider_docs_label.setText("")

        # Update credential labels
        self._on_vpn_type_changed(self.vpn_type_combo.currentText())

    def _on_vpn_type_changed(self, vpn_type: str):
        """Handle VPN type selection change"""
        vpn_type_lower = vpn_type.lower()

        if vpn_type_lower == "wireguard":
            self.vpn_cred1_label.setText("Private Key:")
            self.vpn_cred1_edit.setPlaceholderText("WireGuard Private Key")
            self.vpn_cred2_label.setText("Addresses:")
            self.vpn_cred2_edit.setPlaceholderText("e.g., 10.64.0.1/32 (optional for some providers)")
        else:  # openvpn
            self.vpn_cred1_label.setText("Username:")
            self.vpn_cred1_edit.setPlaceholderText("OpenVPN Username")
            self.vpn_cred2_label.setText("Password:")
            self.vpn_cred2_edit.setPlaceholderText("OpenVPN Password")
            self.vpn_cred2_edit.setEchoMode(QLineEdit.EchoMode.Password)

    def _generate_docker_compose(self):
        """Generate docker-compose.yml for VPN proxies"""
        from src.core.docker_manager import DockerManager

        # Get configuration
        provider = self.vpn_provider_combo.currentText().lower()
        if provider == "private internet access":
            provider = "private internet access"

        vpn_type = self.vpn_type_combo.currentText().lower()

        regions_text = self.vpn_regions_edit.toPlainText().strip()
        if not regions_text:
            QMessageBox.warning(
                self,
                "No Regions",
                "Please enter at least one region/country."
            )
            return

        regions = [r.strip() for r in regions_text.split('\n') if r.strip()]

        cred1 = self.vpn_cred1_edit.text().strip()
        cred2 = self.vpn_cred2_edit.text().strip()

        if not cred1:
            QMessageBox.warning(
                self,
                "Missing Credentials",
                "Please enter your VPN credentials."
            )
            return

        # Build credentials dict
        credentials = {}
        if vpn_type == "wireguard":
            credentials["wireguard_private_key"] = cred1
            if cred2:
                credentials["wireguard_addresses"] = cred2
        else:
            credentials["openvpn_user"] = cred1
            credentials["openvpn_password"] = cred2

        try:
            manager = DockerManager()
            compose_path, proxy_urls = manager.generate_simple_compose(
                provider=provider,
                regions=regions,
                vpn_type=vpn_type,
                credentials=credentials,
            )

            QMessageBox.information(
                self,
                "Docker Compose Generated",
                f"Generated docker-compose.yml at:\n{compose_path}\n\n"
                f"Proxy URLs that will be available:\n" +
                "\n".join(proxy_urls) +
                "\n\nClick 'Start Containers' to launch the VPN proxies."
            )
            self.container_status_label.setText("Containers: Ready to start")
            self.container_status_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")

        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to generate docker-compose.yml:\n{e}"
            )

    def _start_docker_containers(self):
        """Start Docker VPN proxy containers"""
        from src.core.docker_manager import DockerManager

        self.start_containers_btn.setEnabled(False)
        self.container_status_label.setText("Containers: Starting...")
        self.container_status_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")

        # Run in background thread
        self._docker_thread = DockerOperationThread("start")
        self._docker_thread.finished.connect(self._on_docker_operation_complete)
        self._docker_thread.start()

    def _stop_docker_containers(self):
        """Stop Docker VPN proxy containers"""
        self.stop_containers_btn.setEnabled(False)
        self.container_status_label.setText("Containers: Stopping...")
        self.container_status_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")

        # Run in background thread
        self._docker_thread = DockerOperationThread("stop")
        self._docker_thread.finished.connect(self._on_docker_operation_complete)
        self._docker_thread.start()

    def _on_docker_operation_complete(self, success: bool, message: str):
        """Handle Docker operation completion"""
        self.start_containers_btn.setEnabled(True)
        self.stop_containers_btn.setEnabled(True)

        if success:
            self.container_status_label.setText(f"Containers: {message}")
            self.container_status_label.setStyleSheet(f"color: {Colors.ACCENT_SUCCESS};")
            self._update_container_status()
        else:
            self.container_status_label.setText(f"Containers: {message}")
            self.container_status_label.setStyleSheet(f"color: {Colors.ACCENT_ERROR};")

    def _update_container_status(self):
        """Update the container status display"""
        try:
            from src.core.docker_manager import DockerManager
            manager = DockerManager()
            containers = manager.get_container_status()

            if not containers:
                self.container_status_label.setText("Containers: None running")
                self.container_status_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
                return

            running = sum(1 for c in containers if c.running)
            total = len(containers)

            if running == total and running > 0:
                self.container_status_label.setText(
                    f"Containers: {running}/{total} running ✓"
                )
                self.container_status_label.setStyleSheet(f"color: {Colors.ACCENT_SUCCESS};")
            elif running > 0:
                self.container_status_label.setText(
                    f"Containers: {running}/{total} running"
                )
                self.container_status_label.setStyleSheet(f"color: {Colors.ACCENT_WARNING};")
            else:
                self.container_status_label.setText(
                    f"Containers: {total} stopped"
                )
                self.container_status_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")

        except Exception as e:
            logger.warning(f"Failed to update container status: {e}")

    def _apply_docker_proxies_to_pool(self):
        """Get proxy URLs from running containers and add to proxy pool"""
        try:
            from src.core.docker_manager import DockerManager
            manager = DockerManager()
            proxy_urls = manager.get_proxy_urls()

            if not proxy_urls:
                QMessageBox.information(
                    self,
                    "No Proxies Found",
                    "No running Docker VPN proxy containers found.\n"
                    "Make sure containers are started and running."
                )
                return

            # Add to proxy pool text
            current_pool = self.proxy_pool_edit.toPlainText().strip()
            current_proxies = set(p.strip() for p in current_pool.split('\n') if p.strip())

            # Add new proxies
            new_proxies = [url for url in proxy_urls if url not in current_proxies]
            if new_proxies:
                if current_pool:
                    self.proxy_pool_edit.setPlainText(
                        current_pool + '\n' + '\n'.join(new_proxies)
                    )
                else:
                    self.proxy_pool_edit.setPlainText('\n'.join(new_proxies))

                # Enable proxy if not already
                if not self.proxy_enabled_check.isChecked():
                    self.proxy_enabled_check.setChecked(True)

                QMessageBox.information(
                    self,
                    "Proxies Added",
                    f"Added {len(new_proxies)} proxy URL(s) to the pool:\n" +
                    "\n".join(new_proxies)
                )
            else:
                QMessageBox.information(
                    self,
                    "No New Proxies",
                    "All running Docker proxies are already in the pool."
                )

        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to get Docker proxy URLs:\n{e}"
            )

    def _browse_download_dir(self):
        """Browse for download directory"""
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Download Directory",
            str(Path.home() / "Downloads")
        )
        if directory:
            self.download_dir_edit.setText(directory)
    
    def _browse_jdownloader_dir(self):
        """Browse for JDownloader watch folder"""
        # Try to suggest the default JDownloader folderwatch path
        import os
        default_path = ""
        if os.name == 'nt':  # Windows
            appdata = os.environ.get('APPDATA', '')
            if appdata:
                default_path = str(Path(appdata) / "JDownloader 2.0" / "folderwatch")
        else:  # Linux/Mac
            home = Path.home()
            if (home / ".jd2").exists():
                default_path = str(home / ".jd2" / "folderwatch")
            elif (home / "JDownloader 2.0").exists():
                default_path = str(home / "JDownloader 2.0" / "folderwatch")
        
        start_dir = default_path if default_path and Path(default_path).parent.exists() else str(Path.home())
        
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select JDownloader Watch Folder",
            start_dir
        )
        if directory:
            self.jdownloader_watch_dir_edit.setText(directory)
    
    def _on_jdownloader_toggle(self, state):
        """Enable/disable JDownloader watch folder input based on checkbox"""
        enabled = state == Qt.CheckState.Checked.value
        self.jdownloader_watch_dir_edit.setEnabled(enabled)

    def _view_cache_stats(self):
        """Display cache statistics"""
        cache_size = self.db.get_cache_size()
        size_mb = cache_size / (1024 * 1024)
        limit_mb = int(self.db.get_config('cache_size_limit_mb', '5000'))

        QMessageBox.information(
            self,
            "Cache Statistics",
            f"Current cache size: {size_mb:.2f} MB\n"
            f"Cache size limit: {limit_mb} MB"
        )
        self._update_cache_stats_label()
    
    def _clear_cache(self):
        """Clear cache"""
        reply = QMessageBox.question(
            self,
            "Clear Cache",
            "Are you sure you want to clear all cached data?\n"
            "This will remove cached API responses and media thumbnails.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            files_deleted, bytes_freed = self.db.clear_cache()
            QMessageBox.information(
                self,
                "Cache Cleared",
                f"Cleared {files_deleted} cached files.\n"
                f"Freed {bytes_freed / (1024 * 1024):.2f} MB"
            )
            self._update_cache_stats_label()

    def _clear_database(self):
        """Clear local database data while preserving settings and API keys"""
        reply = QMessageBox.question(
            self,
            "Clear Local Database",
            "This will remove local registry, history, favorites, and cache data.\n"
            "Settings and API keys will be kept.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            deleted = self.db.clear_local_data()
            QMessageBox.information(
                self,
                "Database Cleared",
                f"Cleared {deleted} local records"
            )
            self._update_cache_stats_label()
    
    def _create_logging_tab(self) -> QWidget:
        """Create logging configuration tab"""
        from PyQt6.QtWidgets import QScrollArea
        
        # Create scroll area for the tab (many categories may overflow on small screens)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setObjectName("LoggingTabScroll")
        
        widget = QWidget()
        widget.setObjectName("LoggingTab")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)
        
        # Info label with constrained width
        info_label = QLabel(
            "Configure log levels for different subsystems. "
            "Lower levels show more detailed logs.\n"
            "DEBUG: Detailed debug | INFO: General info | "
            "WARNING: Warnings only | ERROR: Errors only"
        )
        info_label.setWordWrap(True)
        info_label.setMaximumWidth(450)
        info_label.setObjectName("LoggingInfoLabel")
        layout.addWidget(info_label)
        
        # Quick presets as combo box at top
        presets_layout = QHBoxLayout()
        presets_label = QLabel("Quick Preset:")
        presets_layout.addWidget(presets_label)
        
        self.log_preset_combo = QComboBox()
        self.log_preset_combo.setObjectName("LogPresetCombo")
        self.log_preset_combo.addItem("Select a preset...", None)
        self.log_preset_combo.addItem("Quiet (Warnings Only)", "quiet")
        self.log_preset_combo.addItem("Balanced (Default)", "balanced")
        self.log_preset_combo.addItem("Verbose (All Info)", "verbose")
        self.log_preset_combo.addItem("Debug (Everything)", "debug")
        self.log_preset_combo.currentIndexChanged.connect(self._on_log_preset_changed)
        presets_layout.addWidget(self.log_preset_combo)
        presets_layout.addStretch()
        layout.addLayout(presets_layout)
        
        # Logger categories
        categories_group = QGroupBox("Logger Categories")
        categories_layout = QVBoxLayout()
        categories_layout.setSpacing(8)
        
        # Import logger categories
        from src.utils.logging_config import LoggerCategory, get_logging_manager
        
        # Store combo boxes for later
        self.log_level_combos = {}
        
        # Level options
        log_levels = [
            ("DEBUG", logging.DEBUG),
            ("INFO", logging.INFO),
            ("WARNING", logging.WARNING),
            ("ERROR", logging.ERROR),
        ]
        
        # Category descriptions
        category_labels = {
            LoggerCategory.CORE: "Core Services (context, cache, managers)",
            LoggerCategory.API: "API Clients (kemono, coomer)",
            LoggerCategory.UI: "UI Components (widgets, windows)",
            LoggerCategory.MEDIA: "Media Processing (thumbnails, video)",
            LoggerCategory.NETWORK: "Network (HTTP, proxies, range proxy)",
            LoggerCategory.DOWNLOAD: "Download Manager",
            LoggerCategory.DATABASE: "Database Operations",
            LoggerCategory.DOCKER: "Docker/VPN Management",
            LoggerCategory.IMAGE_LOADING: "Image Loading & Caching",
            LoggerCategory.VIDEO_PLAYER: "Video Player",
            LoggerCategory.BROWSER: "Browser Window & Navigation",
            LoggerCategory.SETTINGS: "Settings & Configuration",
        }
        
        # Create vertical layout for each category (label on top, combo below)
        for category, description in category_labels.items():
            item_layout = QVBoxLayout()
            item_layout.setSpacing(2)
            
            label = QLabel(f"{description}:")
            label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
            item_layout.addWidget(label)
            
            combo = QComboBox()
            combo.setObjectName(f"LogLevel_{category}")
            for level_name, level_value in log_levels:
                combo.addItem(level_name, level_value)
            
            self.log_level_combos[category] = combo
            item_layout.addWidget(combo)
            
            categories_layout.addLayout(item_layout)
        
        categories_group.setLayout(categories_layout)
        layout.addWidget(categories_group)
        
        layout.addStretch()
        scroll.setWidget(widget)
        return scroll
    
    def _on_log_preset_changed(self, index: int):
        """Handle log preset combo box selection"""
        preset = self.log_preset_combo.itemData(index)
        if preset:
            self._apply_log_preset(preset)
            # Reset combo to "Select a preset..." after applying
            self.log_preset_combo.blockSignals(True)
            self.log_preset_combo.setCurrentIndex(0)
            self.log_preset_combo.blockSignals(False)
    
    def _apply_log_preset(self, preset: str):
        """Apply a logging preset"""
        from src.utils.logging_config import LoggerCategory
        
        if preset == "quiet":
            # Only warnings and errors
            level = logging.WARNING
        elif preset == "balanced":
            # Default balanced settings
            levels = {
                LoggerCategory.CORE: logging.INFO,
                LoggerCategory.API: logging.INFO,
                LoggerCategory.UI: logging.WARNING,
                LoggerCategory.MEDIA: logging.INFO,
                LoggerCategory.NETWORK: logging.INFO,
                LoggerCategory.DOWNLOAD: logging.INFO,
                LoggerCategory.DATABASE: logging.WARNING,
                LoggerCategory.DOCKER: logging.INFO,
                LoggerCategory.IMAGE_LOADING: logging.WARNING,
                LoggerCategory.VIDEO_PLAYER: logging.INFO,
                LoggerCategory.BROWSER: logging.INFO,
                LoggerCategory.SETTINGS: logging.INFO,
            }
            for category, combo in self.log_level_combos.items():
                target_level = levels.get(category, logging.INFO)
                for i in range(combo.count()):
                    if combo.itemData(i) == target_level:
                        combo.setCurrentIndex(i)
                        break
            return
        elif preset == "verbose":
            # All info logging
            level = logging.INFO
        elif preset == "debug":
            # Everything in debug
            level = logging.DEBUG
        else:
            return
        
        # Apply to all categories
        for combo in self.log_level_combos.values():
            for i in range(combo.count()):
                if combo.itemData(i) == level:
                    combo.setCurrentIndex(i)
                    break
    
    def _load_settings(self):
        """Load settings from database"""
        # Media & Playback
        self.auto_thumbnails_check.setChecked(
            self.db.get_config('auto_generate_thumbnails', 'true') == 'true'
        )
        self.thumbnail_quality_spin.setValue(
            int(self.db.get_config('thumbnail_quality', '85'))
        )
        self.range_proxy_check.setChecked(
            self.db.get_config('enable_range_proxy', 'false') == 'true'
        )
        max_video_mb = int(self.db.get_config('video_thumb_max_mb', '300'))
        non_fast_mb = int(self.db.get_config('video_thumb_max_non_faststart_mb', '20'))
        self.video_thumb_max_mb_spin.setValue(max_video_mb)
        self.video_thumb_non_fast_mb_spin.setValue(non_fast_mb)
        self.video_thumb_max_unlimited_check.setChecked(max_video_mb == 0)
        self.video_thumb_max_mb_spin.setEnabled(max_video_mb != 0)
        self.video_thumb_non_fast_unlimited_check.setChecked(non_fast_mb == 0)
        self.video_thumb_non_fast_mb_spin.setEnabled(non_fast_mb != 0)
        # NOTE: video_thumb_retries and video_thumb_retry_delay_ms removed - not used in codebase
        self.allow_post_content_media_check.setChecked(
            self.db.get_config('allow_post_content_media', 'false') == 'true'
        )
        
        # Downloads
        self.download_dir_edit.setText(
            self.db.get_config('download_dir', str(Path.home() / "Downloads"))
        )
        self.max_downloads_spin.setValue(
            int(self.db.get_config('max_concurrent_downloads', '3'))
        )
        # NOTE: enable_batch_download removed - not used in codebase (batch always available)
        self.structured_downloads_check.setChecked(
            self.db.get_config('structured_downloads', 'true') == 'true'
        )
        
        # JDownloader Integration
        jd_enabled = self.db.get_config('jdownloader_enabled', 'false') == 'true'
        self.jdownloader_enabled_check.setChecked(jd_enabled)
        self.jdownloader_watch_dir_edit.setText(
            self.db.get_config('jdownloader_watch_dir', '')
        )
        self.jdownloader_watch_dir_edit.setEnabled(jd_enabled)
        
        # Storage & Cache
        self.cache_size_spin.setValue(
            int(self.db.get_config('cache_size_limit_mb', '5000'))
        )
        self.cleanup_days_spin.setValue(
            int(self.db.get_config('auto_cleanup_cache_days', '30'))
        )
        self.range_cache_size_spin.setValue(
            int(self.db.get_config('range_cache_size_gb', '10'))
        )
        self.range_cache_age_spin.setValue(
            int(self.db.get_config('range_cache_age_days', '30'))
        )
        self._update_cache_stats_label()
        self._update_range_cache_stats_label()
        self._update_range_metrics_label()
        
        # Performance
        self.user_agent_edit.setText(
            self.db.get_config('user_agent',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        )
        self.thumb_image_workers_spin.setValue(
            int(self.db.get_config('thumb_image_workers', '6'))
        )
        self.thumb_video_workers_spin.setValue(
            int(self.db.get_config('thumb_video_workers', '2'))
        )
        self.thumb_video_queue_spin.setValue(
            int(self.db.get_config('thumb_video_queue_limit', '10'))
        )
        self.range_proxy_max_concurrent_spin.setValue(
            int(self.db.get_config('range_proxy_max_concurrent_chunks', '5'))
        )
        self.max_connections_per_host_spin.setValue(
            int(self.db.get_config('max_connections_per_host', '10'))
        )
        self.max_total_connections_spin.setValue(
            int(self.db.get_config('max_total_connections', '100'))
        )

        # Network & Proxy
        proxy_enabled = self.db.get_config('proxy_enabled', 'false') == 'true'
        self.proxy_enabled_check.setChecked(proxy_enabled)
        self.proxy_url_edit.setText(self.db.get_config('proxy_url', ''))
        self.proxy_url_edit.setEnabled(proxy_enabled)

        proxy_pool_json = self.db.get_config('proxy_pool', '[]')
        try:
            proxy_pool = json.loads(proxy_pool_json) if proxy_pool_json else []
            self.proxy_pool_edit.setPlainText('\n'.join(proxy_pool))
        except json.JSONDecodeError:
            self.proxy_pool_edit.setPlainText('')
        self.proxy_pool_edit.setEnabled(proxy_enabled)

        rotation_map = {'round_robin': 0, 'random': 1, 'least_used': 2}
        rotation = self.db.get_config('proxy_rotation_strategy', 'round_robin')
        self.proxy_rotation_combo.setCurrentIndex(rotation_map.get(rotation, 0))
        self.proxy_rotation_combo.setEnabled(proxy_enabled)

        self.proxy_username_edit.setText(self.db.get_config('proxy_username', ''))
        self.proxy_username_edit.setEnabled(proxy_enabled)
        self.proxy_password_edit.setText(self.db.get_config('proxy_password', ''))
        self.proxy_password_edit.setEnabled(proxy_enabled)

        self.request_delay_spin.setValue(
            int(self.db.get_config('request_delay_ms', '0'))
        )
        self.test_proxy_btn.setEnabled(proxy_enabled)

        # Docker VPN Setup - check status and initialize UI
        self._check_docker_status()
        self._on_vpn_provider_changed(self.vpn_provider_combo.currentText())
        
        # Logging - load category levels
        if hasattr(self, 'log_level_combos'):
            from src.utils.logging_config import get_logging_manager, LoggerCategory
            try:
                manager = get_logging_manager(self.db)
                for category, combo in self.log_level_combos.items():
                    level = manager.get_category_level(category)
                    # Set combo to matching level
                    for i in range(combo.count()):
                        if combo.itemData(i) == level:
                            combo.setCurrentIndex(i)
                            break
            except Exception as e:
                logger.warning(f"Failed to load logging settings: {e}")

    def _save_settings(self):
        """Save settings to database"""
        try:
            # Media & Playback
            self.db.set_config('auto_generate_thumbnails',
                             'true' if self.auto_thumbnails_check.isChecked() else 'false')
            self.db.set_config('thumbnail_quality', str(self.thumbnail_quality_spin.value()))
            self.db.set_config('enable_range_proxy',
                             'true' if self.range_proxy_check.isChecked() else 'false')
            max_video_mb = 0 if self.video_thumb_max_unlimited_check.isChecked() else self.video_thumb_max_mb_spin.value()
            non_fast_mb = 0 if self.video_thumb_non_fast_unlimited_check.isChecked() else self.video_thumb_non_fast_mb_spin.value()
            self.db.set_config('video_thumb_max_mb', str(max_video_mb))
            self.db.set_config('video_thumb_max_non_faststart_mb', str(non_fast_mb))
            # NOTE: video_thumb_retries and video_thumb_retry_delay_ms removed - not used in codebase
            self.db.set_config(
                'allow_post_content_media',
                'true' if self.allow_post_content_media_check.isChecked() else 'false',
            )
            
            # Downloads
            self.db.set_config('download_dir', self.download_dir_edit.text())
            self.db.set_config('max_concurrent_downloads', str(self.max_downloads_spin.value()))
            # NOTE: enable_batch_download removed - not used in codebase (batch always available)
            self.db.set_config('structured_downloads',
                             'true' if self.structured_downloads_check.isChecked() else 'false')
            
            # JDownloader Integration
            self.db.set_config('jdownloader_enabled',
                             'true' if self.jdownloader_enabled_check.isChecked() else 'false')
            self.db.set_config('jdownloader_watch_dir', self.jdownloader_watch_dir_edit.text())
            
            # Storage & Cache
            self.db.set_config('cache_size_limit_mb', str(self.cache_size_spin.value()))
            self.db.set_config('auto_cleanup_cache_days', str(self.cleanup_days_spin.value()))
            self.db.set_config('range_cache_size_gb', str(self.range_cache_size_spin.value()))
            self.db.set_config('range_cache_age_days', str(self.range_cache_age_spin.value()))
            try:
                self.db.enforce_cache_limit(self.cache_size_spin.value())
            except Exception as e:
                logger.warning(f"Error enforcing cache limit: {e}")
            self._update_cache_stats_label()
            
            # Performance
            self.db.set_config('user_agent', self.user_agent_edit.text())
            self.db.set_config('thumb_image_workers', str(self.thumb_image_workers_spin.value()))
            self.db.set_config('thumb_video_workers', str(self.thumb_video_workers_spin.value()))
            self.db.set_config('thumb_video_queue_limit', str(self.thumb_video_queue_spin.value()))
            self.db.set_config('range_proxy_max_concurrent_chunks', str(self.range_proxy_max_concurrent_spin.value()))
            self.db.set_config('max_connections_per_host', str(self.max_connections_per_host_spin.value()))
            self.db.set_config('max_total_connections', str(self.max_total_connections_spin.value()))

            # Network & Proxy
            self.db.set_config('proxy_enabled',
                             'true' if self.proxy_enabled_check.isChecked() else 'false')
            self.db.set_config('proxy_url', self.proxy_url_edit.text().strip())

            # Save proxy pool as JSON array
            pool_text = self.proxy_pool_edit.toPlainText().strip()
            proxy_pool = [p.strip() for p in pool_text.split('\n') if p.strip()]
            self.db.set_config('proxy_pool', json.dumps(proxy_pool))

            rotation_map = {0: 'round_robin', 1: 'random', 2: 'least_used'}
            self.db.set_config('proxy_rotation_strategy',
                             rotation_map.get(self.proxy_rotation_combo.currentIndex(), 'round_robin'))

            self.db.set_config('proxy_username', self.proxy_username_edit.text().strip())
            # Store password encrypted
            password = self.proxy_password_edit.text().strip()
            if password:
                self.db.set_config('proxy_password', password, encrypt=True)
            else:
                self.db.set_config('proxy_password', '')

            self.db.set_config('request_delay_ms', str(self.request_delay_spin.value()))
            
            # Logging - save category levels
            if hasattr(self, 'log_level_combos'):
                from src.utils.logging_config import get_logging_manager
                try:
                    manager = get_logging_manager(self.db)
                    for category, combo in self.log_level_combos.items():
                        level = combo.currentData()
                        if level is not None:
                            manager.set_category_level(category, level)
                except Exception as e:
                    logger.warning(f"Failed to save logging settings: {e}")

            from src.ui.images.image_loader_manager import get_image_loader_manager
            get_image_loader_manager(self.db, core_context=self.core_context)
            
            QMessageBox.information(self, "Success", "Settings saved successfully!\n\nSome changes may require a restart to take effect.")
            self.accept()
            
        except Exception as e:
            logger.error(f"Error saving settings: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save settings: {e}")

    def _update_cache_stats_label(self) -> None:
        cache_size = self.db.get_cache_size()
        size_mb = cache_size / (1024 * 1024)
        limit_mb = int(self.db.get_config('cache_size_limit_mb', '5000'))
        self.cache_stats_label.setText(f"{size_mb:.2f} MB / {limit_mb} MB")

    def _update_range_cache_stats_label(self) -> None:
        """Update range proxy cache statistics label"""
        if not self.core_context:
            self.range_cache_stats_label.setText("Not available")
            return

        try:
            proxy = self.core_context.range_proxy if hasattr(self.core_context, 'range_proxy') else None
            if not proxy:
                self.range_cache_stats_label.setText("Not available")
                return
            total_size = proxy.get_cache_size()
            size_gb = total_size / (1024 ** 3)
            limit_gb = int(self.db.get_config('range_cache_size_gb', '10'))
            self.range_cache_stats_label.setText(f"{size_gb:.2f} GB / {limit_gb} GB")
        except Exception as e:
            logger.warning(f"Failed to get range cache stats: {e}")
            self.range_cache_stats_label.setText("Error reading cache")

    def _view_range_proxy_metrics(self):
        """Display range proxy metrics"""
        if not self.core_context:
            QMessageBox.warning(
                self,
                "Range Proxy Not Available",
                "Range proxy is not initialized. Enable it in General settings and restart."
            )
            return

        try:
            proxy = self.core_context.range_proxy if hasattr(self.core_context, 'range_proxy') else None
            if not proxy:
                QMessageBox.warning(
                    self,
                    "Range Proxy Not Available",
                    "Range proxy is not initialized. Enable it in General settings and restart."
                )
                return
            metrics = proxy.get_metrics()

            message = (
                f"Cache Hits: {metrics['cache_hits']}\n"
                f"Cache Misses: {metrics['cache_misses']}\n"
                f"Total Requests: {metrics['total_requests']}\n"
                f"Errors: {metrics['errors']}\n"
                f"Hit Rate: {metrics['hit_rate']:.1%}\n\n"
                f"A higher hit rate means better caching efficiency."
            )

            QMessageBox.information(
                self,
                "Range Proxy Metrics",
                message
            )
        except Exception as e:
            QMessageBox.warning(
                self,
                "Error",
                f"Failed to get range proxy metrics:\n{e}"
            )

    def _clear_range_cache(self):
        """Clear range proxy cache"""
        if not self.core_context:
            QMessageBox.warning(
                self,
                "Range Proxy Not Available",
                "Range proxy is not initialized."
            )
            return

        reply = QMessageBox.question(
            self,
            "Clear Range Proxy Cache",
            "Are you sure you want to clear all range proxy cached data?\n"
            "This will remove cached video/image chunks.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                proxy = self.core_context.range_proxy if hasattr(self.core_context, 'range_proxy') else None
                if not proxy:
                    QMessageBox.warning(
                        self,
                        "Range Proxy Not Available",
                        "Range proxy is not initialized. Enable it in General settings and restart."
                    )
                    return
                files_removed, bytes_freed = proxy.clear_cache()

                QMessageBox.information(
                    self,
                    "Cache Cleared",
                    f"Range proxy cache cleared successfully.\n"
                    f"Removed {files_removed} entries, freed {bytes_freed / (1024 ** 3):.2f} GB"
                )
                self._update_range_cache_stats_label()
            except Exception as e:
                QMessageBox.warning(
                    self,
                    "Error",
                    f"Failed to clear range proxy cache:\n{e}"
                )

    def _show_clear_data_dialog(self):
        """Show dialog with checkboxes to select what data to clear"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Clear Data")
        dialog.setMinimumWidth(400)
        dialog_layout = QVBoxLayout(dialog)
        
        # Warning label
        warning = QLabel("⚠️ Select data to clear. This cannot be undone.")
        warning.setStyleSheet(f"color: {Colors.ACCENT_WARNING}; font-weight: bold; margin-bottom: 10px;")
        dialog_layout.addWidget(warning)
        
        # Checkboxes for different data types
        self._clear_api_cache_check = QCheckBox("API Response Cache")
        self._clear_api_cache_check.setToolTip("Cached API responses and metadata")
        dialog_layout.addWidget(self._clear_api_cache_check)
        
        self._clear_thumbnails_check = QCheckBox("Thumbnail Cache")
        self._clear_thumbnails_check.setToolTip("Generated image and video thumbnails")
        dialog_layout.addWidget(self._clear_thumbnails_check)
        
        self._clear_video_cache_check = QCheckBox("Video Streaming Cache")
        self._clear_video_cache_check.setToolTip("Cached video chunks for streaming")
        dialog_layout.addWidget(self._clear_video_cache_check)
        
        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet(f"background-color: {Colors.BORDER_DEFAULT};")
        dialog_layout.addWidget(separator)
        
        # Database options (more dangerous)
        db_label = QLabel("Database (preserves settings):")
        db_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; margin-top: 8px;")
        dialog_layout.addWidget(db_label)
        
        self._clear_history_check = QCheckBox("Browsing History")
        self._clear_history_check.setToolTip("Clear viewed posts and navigation history")
        dialog_layout.addWidget(self._clear_history_check)
        
        self._clear_favorites_check = QCheckBox("Favorites & Collections")
        self._clear_favorites_check.setToolTip("Clear saved favorites and custom collections")
        dialog_layout.addWidget(self._clear_favorites_check)
        
        self._clear_creators_check = QCheckBox("Linked Creators Registry")
        self._clear_creators_check.setToolTip("Clear linked/followed creators list")
        dialog_layout.addWidget(self._clear_creators_check)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        clear_btn = QPushButton("Clear Selected")
        clear_btn.setStyleSheet(f"QPushButton {{ color: {Colors.ACCENT_ERROR}; font-weight: bold; }}")
        clear_btn.clicked.connect(lambda: self._execute_clear_data(dialog))
        button_layout.addWidget(clear_btn)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_btn)
        
        dialog_layout.addLayout(button_layout)
        dialog.exec()
    
    def _execute_clear_data(self, dialog):
        """Execute the data clearing based on checkbox selections"""
        # Check if anything is selected
        anything_selected = (
            self._clear_api_cache_check.isChecked() or
            self._clear_thumbnails_check.isChecked() or
            self._clear_video_cache_check.isChecked() or
            self._clear_history_check.isChecked() or
            self._clear_favorites_check.isChecked() or
            self._clear_creators_check.isChecked()
        )
        
        if not anything_selected:
            QMessageBox.warning(self, "Nothing Selected", "Please select at least one item to clear.")
            return
        
        # Confirm
        reply = QMessageBox.question(
            self,
            "Confirm Clear",
            "Are you sure you want to clear the selected data?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        results = []
        
        # Clear API cache
        if self._clear_api_cache_check.isChecked():
            try:
                files, bytes_freed = self.db.clear_cache()
                results.append(f"API Cache: {files} files, {bytes_freed / (1024*1024):.1f} MB freed")
            except Exception as e:
                results.append(f"API Cache: Error - {e}")
        
        # Clear thumbnails
        if self._clear_thumbnails_check.isChecked():
            try:
                import shutil
                thumb_path = Path.home() / ".coomer-betterui" / "thumbnails"
                if thumb_path.exists():
                    shutil.rmtree(thumb_path)
                    thumb_path.mkdir(parents=True, exist_ok=True)
                    results.append("Thumbnails: Cleared")
                else:
                    results.append("Thumbnails: Already empty")
            except Exception as e:
                results.append(f"Thumbnails: Error - {e}")
        
        # Clear video streaming cache
        if self._clear_video_cache_check.isChecked():
            try:
                if self.core_context and hasattr(self.core_context, 'range_proxy') and self.core_context.range_proxy:
                    files, bytes_freed = self.core_context.range_proxy.clear_cache()
                    results.append(f"Video Cache: {files} files, {bytes_freed / (1024**3):.2f} GB freed")
                else:
                    results.append("Video Cache: Not available")
            except Exception as e:
                results.append(f"Video Cache: Error - {e}")
        
        # Clear history
        if self._clear_history_check.isChecked():
            try:
                deleted = self.db.clear_history() if hasattr(self.db, 'clear_history') else 0
                results.append(f"History: {deleted} entries cleared")
            except Exception as e:
                results.append(f"History: Error - {e}")
        
        # Clear favorites
        if self._clear_favorites_check.isChecked():
            try:
                deleted = self.db.clear_favorites() if hasattr(self.db, 'clear_favorites') else 0
                results.append(f"Favorites: {deleted} entries cleared")
            except Exception as e:
                results.append(f"Favorites: Error - {e}")
        
        # Clear creators registry
        if self._clear_creators_check.isChecked():
            try:
                deleted = self.db.clear_creators() if hasattr(self.db, 'clear_creators') else 0
                results.append(f"Creators: {deleted} entries cleared")
            except Exception as e:
                results.append(f"Creators: Error - {e}")
        
        # Show results
        QMessageBox.information(
            self,
            "Data Cleared",
            "Results:\n\n" + "\n".join(results)
        )
        
        # Update stats labels
        self._update_cache_stats_label()
        self._update_range_cache_stats_label()
        self._update_range_metrics_label()
        
        dialog.accept()
    
    def _update_range_metrics_label(self):
        """Update the inline range proxy metrics label"""
        try:
            if self.core_context and hasattr(self.core_context, 'range_proxy') and self.core_context.range_proxy:
                metrics = self.core_context.range_proxy.get_metrics()
                self.range_metrics_label.setText(
                    f"Hits: {metrics['cache_hits']} | Misses: {metrics['cache_misses']} | "
                    f"Rate: {metrics['hit_rate']:.0%}"
                )
            else:
                self.range_metrics_label.setText("Not available (streaming disabled)")
        except Exception:
            self.range_metrics_label.setText("Error reading metrics")
