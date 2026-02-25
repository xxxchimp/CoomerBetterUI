"""
Notification widgets for user feedback.

Extracted from native_widgets.py to reduce file size and improve maintainability.
Contains ToastNotification and DownloadProgressBar widgets.
"""
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QPropertyAnimation, QEasingCurve, QRectF
from PyQt6.QtGui import QPainter, QColor, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QProgressBar
)
import qtawesome as qta

from src.ui.common.theme import Colors, Fonts, Spacing, Styles


class ToastNotification(QWidget):
    """
    Toast notification widget that appears and auto-dismisses.

    Shows a message with an icon at the bottom-right of the parent widget.
    Automatically fades out after the specified duration.
    Uses custom painting for rounded corners with opacity animation support.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toastNotification")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.ToolTip |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.hide()  # Start hidden to prevent flash

        self._radius = Spacing.RADIUS_XL
        self._bg_color = QColor(Colors.BG_TERTIARY)
        self._border_color = QColor(Colors.BORDER_DEFAULT)

        # Main layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Spacing.LG, Spacing.MD, Spacing.LG, Spacing.MD)
        layout.setSpacing(Spacing.MD)

        # Icon label
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(Spacing.ICON_LG, Spacing.ICON_LG)
        layout.addWidget(self.icon_label)

        # Message label
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; font-size: {Fonts.SIZE_LG}px; background: transparent;"
        )
        layout.addWidget(self.message_label, 1)

        # Close button
        close_btn = QPushButton()
        close_btn.setIcon(qta.icon('fa5s.times', color=Colors.TEXT_SECONDARY))
        close_btn.setFixedSize(Spacing.ICON_MD, Spacing.ICON_MD)
        close_btn.setFlat(True)
        close_btn.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent; }} "
            f"QPushButton:hover {{ background-color: rgba(255,255,255,0.1); border-radius: {Spacing.RADIUS_LG}px; }}"
        )
        close_btn.clicked.connect(self.hide)
        close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(close_btn)

        # Auto-hide timer
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._fade_out)

        # Fade animation using window opacity (not graphics effect)
        self._opacity = 1.0
        self.fade_animation = QPropertyAnimation(self, b"windowOpacity")
        self.fade_animation.setDuration(300)

    def paintEvent(self, event):
        """Paint rounded rectangle background with border."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Create rounded rect path
        path = QPainterPath()
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path.addRoundedRect(rect, self._radius, self._radius)

        # Fill background
        painter.fillPath(path, self._bg_color)

        # Draw border
        painter.setPen(QPen(self._border_color, 1))
        painter.drawPath(path)

    def show_message(
        self,
        message: str,
        icon_name: str = 'fa5s.info-circle',
        icon_color: str = Colors.ACCENT_SECONDARY,
        duration: int = 3000
    ):
        """
        Show toast notification.

        Args:
            message: Message to display
            icon_name: QtAwesome icon name
            icon_color: Icon color (defaults to Colors.ACCENT_SECONDARY)
            duration: Duration in milliseconds before auto-hide
        """
        self.message_label.setText(message)
        self.icon_label.setPixmap(
            qta.icon(icon_name, color=icon_color).pixmap(Spacing.ICON_LG, Spacing.ICON_LG)
        )

        # Position at bottom-right of parent
        if self.parent():
            parent_rect = self.parent().rect()
            self.adjustSize()
            x = parent_rect.width() - self.width() - Spacing.XL
            y = parent_rect.height() - self.height() - 80
            self.move(x, y)

        # Fade in using window opacity
        self.setWindowOpacity(0)
        self.show()
        self.fade_animation.setStartValue(0.0)
        self.fade_animation.setEndValue(1.0)
        self.fade_animation.start()

        # Start auto-hide timer
        self.hide_timer.start(duration)

    def _fade_out(self):
        """Fade out animation."""
        # Disconnect any previous connections to avoid multiple hides
        try:
            self.fade_animation.finished.disconnect()
        except TypeError:
            pass
        self.fade_animation.setStartValue(1.0)
        self.fade_animation.setEndValue(0.0)
        self.fade_animation.finished.connect(self.hide)
        self.fade_animation.start()


class DownloadProgressBar(QWidget):
    """
    Download progress bar widget with cancel/dismiss functionality.

    Shows download progress with animated bar, file count, and action button.
    Auto-hides after completion.
    """

    cancel_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("downloadProgressBar")
        self.setFixedHeight(70)
        self.setVisible(False)

        # Track active timer to prevent accidental hiding
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        self._init_ui()
        self._apply_styles()

    def _init_ui(self):
        """Initialize UI components."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Spacing.XL, Spacing.SM, Spacing.XL, Spacing.SM)
        layout.setSpacing(15)

        # Icon
        self.icon_label = QLabel()
        self.icon_label.setPixmap(
            qta.icon('fa5s.download', color=Colors.ACCENT_SECONDARY).pixmap(
                Spacing.ICON_LG, Spacing.ICON_LG
            )
        )
        layout.addWidget(self.icon_label)

        # Progress info container
        info_layout = QVBoxLayout()

        # Top Row: Status and Stats
        label_row = QHBoxLayout()
        self.status_label = QLabel("Ready")
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; font-size: {Fonts.SIZE_SM}px;"
        )
        label_row.addWidget(self.status_label)
        label_row.addStretch()
        label_row.addWidget(self.stats_label)
        info_layout.addLayout(label_row)

        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedHeight(Spacing.PROGRESS_BAR_HEIGHT)
        self.progress_bar.setTextVisible(False)
        info_layout.addWidget(self.progress_bar)

        layout.addLayout(info_layout, 1)

        # Action Button
        self.action_btn = QPushButton("Cancel")
        self.action_btn.setFixedWidth(80)
        self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_btn.clicked.connect(self._handle_action)
        layout.addWidget(self.action_btn)

    def _apply_styles(self):
        """Apply widget styles."""
        self.setStyleSheet(Styles.DOWNLOAD_PROGRESS)

    def _handle_action(self):
        """Handle action button click."""
        if self.action_btn.text() == "Cancel":
            self.cancel_clicked.emit()
        else:
            self.hide()

    def start_download(self, file_count: int):
        """
        Start a new download.

        Args:
            file_count: Number of files to download
        """
        self._hide_timer.stop()
        self.progress_bar.setStyleSheet("")  # Reset to default blue
        self.action_btn.setText("Cancel")
        self.status_label.setText(f"Preparing {file_count} files...")
        self.progress_bar.setValue(0)
        self.setVisible(True)

    def update_progress(self, progress: int, current: int, total: int, speed: str = ""):
        """
        Update download progress.

        Args:
            progress: Progress percentage (0-100)
            current: Current file number
            total: Total file count
            speed: Optional speed string (e.g., "2 MB/s")
        """
        # Smoothly animate the bar
        self.animation = QPropertyAnimation(self.progress_bar, b"value")
        self.animation.setDuration(300)
        self.animation.setEndValue(progress)
        self.animation.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.animation.start()

        self.status_label.setText(f"Downloading ({current}/{total})")
        if speed:
            self.stats_label.setText(speed)

    def complete_download(self, success_count: int, failed_count: int, message: str):
        """
        Mark download as complete.

        Args:
            success_count: Number of successful downloads
            failed_count: Number of failed downloads
            message: Completion message to display
        """
        self.action_btn.setText("Dismiss")
        self.stats_label.setText("")
        self.status_label.setText(message)

        if failed_count > 0:
            self.progress_bar.setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {Colors.ACCENT_ERROR}; }}"
            )
        else:
            self.progress_bar.setValue(100)

        self._hide_timer.start(5000)  # Auto-hide after 5 seconds
