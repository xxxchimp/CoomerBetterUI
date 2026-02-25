"""
Download Panel Widget - Chromium-style download list with individual file progress.

Provides a collapsible panel showing:
- Overall download progress indicator
- List of individual files with their own progress bars
- Pause/Resume/Cancel controls per file
- Auto-hide on completion with dismiss option
"""
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QScrollArea, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont
import qtawesome as qta

from src.ui.common.theme import Colors, Fonts, Spacing

import logging

logger = logging.getLogger(__name__)


class DownloadStatus(Enum):
    """Status of an individual download."""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class DownloadItem:
    """Represents a single download in the panel."""
    id: int
    url: str
    filename: str
    destination: Path
    status: DownloadStatus = DownloadStatus.PENDING
    progress: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    error_message: str = ""
    speed: str = ""


class DownloadItemWidget(QFrame):
    """Widget displaying a single download item with progress."""
    
    pause_clicked = pyqtSignal(int)  # download_id
    resume_clicked = pyqtSignal(int)  # download_id
    cancel_clicked = pyqtSignal(int)  # download_id
    retry_clicked = pyqtSignal(int)  # download_id
    open_folder_clicked = pyqtSignal(int)  # download_id
    
    def __init__(self, item: DownloadItem, parent=None):
        super().__init__(parent)
        self.item = item
        self.setObjectName("downloadItemWidget")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._init_ui()
        self._apply_styles()
        self.update_item(item)
    
    def _init_ui(self):
        """Initialize UI components."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        
        # File icon based on extension
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(32, 32)
        self._update_icon()
        layout.addWidget(self.icon_label)
        
        # Info container (filename, progress bar, status)
        info_container = QVBoxLayout()
        info_container.setSpacing(4)
        
        # Top row: filename and size
        top_row = QHBoxLayout()
        self.filename_label = QLabel(self.item.filename)
        self.filename_label.setObjectName("downloadFilename")
        font = self.filename_label.font()
        font.setPointSize(10)
        self.filename_label.setFont(font)
        self.filename_label.setWordWrap(False)
        self.filename_label.setMaximumWidth(300)
        top_row.addWidget(self.filename_label, 1)
        
        self.size_label = QLabel("")
        self.size_label.setObjectName("downloadSize")
        top_row.addWidget(self.size_label)
        info_container.addLayout(top_row)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setObjectName("downloadItemProgress")
        info_container.addWidget(self.progress_bar)
        
        # Bottom row: status and speed
        bottom_row = QHBoxLayout()
        self.status_label = QLabel("Waiting...")
        self.status_label.setObjectName("downloadStatus")
        bottom_row.addWidget(self.status_label)
        bottom_row.addStretch()
        
        self.speed_label = QLabel("")
        self.speed_label.setObjectName("downloadSpeed")
        bottom_row.addWidget(self.speed_label)
        info_container.addLayout(bottom_row)
        
        layout.addLayout(info_container, 1)
        
        # Action buttons container
        btn_container = QHBoxLayout()
        btn_container.setSpacing(4)
        
        # Pause/Resume button
        self.pause_btn = QPushButton()
        self.pause_btn.setFixedSize(28, 28)
        self.pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pause_btn.setToolTip("Pause")
        self.pause_btn.clicked.connect(self._on_pause_resume)
        btn_container.addWidget(self.pause_btn)
        
        # Cancel/Retry button
        self.cancel_btn = QPushButton()
        self.cancel_btn.setFixedSize(28, 28)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setToolTip("Cancel")
        self.cancel_btn.clicked.connect(self._on_cancel_retry)
        btn_container.addWidget(self.cancel_btn)
        
        # Open folder button (shown on complete)
        self.folder_btn = QPushButton()
        self.folder_btn.setFixedSize(28, 28)
        self.folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folder_btn.setToolTip("Show in folder")
        self.folder_btn.setIcon(qta.icon('fa5s.folder-open', color=Colors.TEXT_SECONDARY))
        self.folder_btn.clicked.connect(lambda: self.open_folder_clicked.emit(self.item.id))
        self.folder_btn.setVisible(False)
        btn_container.addWidget(self.folder_btn)
        
        layout.addLayout(btn_container)
        
        self._update_buttons()
    
    def _apply_styles(self):
        """Apply widget styles."""
        self.setStyleSheet(f"""
            QFrame#downloadItemWidget {{
                background-color: {Colors.BG_SECONDARY};
                border-radius: {Spacing.RADIUS_SM}px;
                margin: 2px 0;
            }}
            QFrame#downloadItemWidget:hover {{
                background-color: {Colors.BG_TERTIARY};
            }}
            QLabel#downloadFilename {{
                color: {Colors.TEXT_PRIMARY};
                font-weight: 500;
            }}
            QLabel#downloadSize, QLabel#downloadStatus, QLabel#downloadSpeed {{
                color: {Colors.TEXT_SECONDARY};
                font-size: {Fonts.SIZE_SM}px;
            }}
            QProgressBar#downloadItemProgress {{
                background-color: {Colors.BORDER_DEFAULT};
                border-radius: 2px;
                border: none;
            }}
            QProgressBar#downloadItemProgress::chunk {{
                background-color: {Colors.ACCENT_SECONDARY};
                border-radius: 2px;
            }}
            QPushButton {{
                background-color: transparent;
                border: none;
                border-radius: {Spacing.RADIUS_SM}px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BG_HOVER};
            }}
        """)
    
    def _update_icon(self):
        """Update file icon based on extension."""
        ext = Path(self.item.filename).suffix.lower()
        if ext in ('.mp4', '.webm', '.mov', '.avi', '.mkv'):
            icon = qta.icon('fa5s.film', color=Colors.ACCENT_SECONDARY)
        elif ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
            icon = qta.icon('fa5s.image', color=Colors.ACCENT_PRIMARY)
        elif ext in ('.zip', '.rar', '.7z'):
            icon = qta.icon('fa5s.file-archive', color=Colors.TEXT_SECONDARY)
        else:
            icon = qta.icon('fa5s.file', color=Colors.TEXT_SECONDARY)
        self.icon_label.setPixmap(icon.pixmap(24, 24))
    
    def _update_buttons(self):
        """Update button states based on download status."""
        status = self.item.status
        
        if status == DownloadStatus.DOWNLOADING:
            self.pause_btn.setIcon(qta.icon('fa5s.pause', color=Colors.TEXT_SECONDARY))
            self.pause_btn.setToolTip("Pause")
            self.pause_btn.setVisible(True)
            self.cancel_btn.setIcon(qta.icon('fa5s.times', color=Colors.TEXT_SECONDARY))
            self.cancel_btn.setToolTip("Cancel")
            self.cancel_btn.setVisible(True)
            self.folder_btn.setVisible(False)
        elif status == DownloadStatus.PAUSED:
            self.pause_btn.setIcon(qta.icon('fa5s.play', color=Colors.ACCENT_SECONDARY))
            self.pause_btn.setToolTip("Resume")
            self.pause_btn.setVisible(True)
            self.cancel_btn.setIcon(qta.icon('fa5s.times', color=Colors.TEXT_SECONDARY))
            self.cancel_btn.setToolTip("Cancel")
            self.cancel_btn.setVisible(True)
            self.folder_btn.setVisible(False)
        elif status == DownloadStatus.COMPLETED:
            self.pause_btn.setVisible(False)
            self.cancel_btn.setVisible(False)
            self.folder_btn.setVisible(True)
        elif status == DownloadStatus.FAILED:
            self.pause_btn.setVisible(False)
            self.cancel_btn.setIcon(qta.icon('fa5s.redo', color=Colors.ACCENT_ERROR))
            self.cancel_btn.setToolTip("Retry")
            self.cancel_btn.setVisible(True)
            self.folder_btn.setVisible(False)
        elif status == DownloadStatus.PENDING:
            self.pause_btn.setVisible(False)
            self.cancel_btn.setIcon(qta.icon('fa5s.times', color=Colors.TEXT_SECONDARY))
            self.cancel_btn.setToolTip("Cancel")
            self.cancel_btn.setVisible(True)
            self.folder_btn.setVisible(False)
        else:  # CANCELLED
            self.pause_btn.setVisible(False)
            self.cancel_btn.setIcon(qta.icon('fa5s.redo', color=Colors.TEXT_SECONDARY))
            self.cancel_btn.setToolTip("Retry")
            self.cancel_btn.setVisible(True)
            self.folder_btn.setVisible(False)
    
    def _on_pause_resume(self):
        """Handle pause/resume button click."""
        if self.item.status == DownloadStatus.PAUSED:
            self.resume_clicked.emit(self.item.id)
        else:
            self.pause_clicked.emit(self.item.id)
    
    def _on_cancel_retry(self):
        """Handle cancel/retry button click."""
        if self.item.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
            self.retry_clicked.emit(self.item.id)
        else:
            self.cancel_clicked.emit(self.item.id)
    
    def _format_size(self, bytes_val: int) -> str:
        """Format bytes to human readable size."""
        if bytes_val is None or bytes_val < 0:
            return "0 B"
        if bytes_val < 1024:
            return f"{bytes_val} B"
        elif bytes_val < 1024 * 1024:
            return f"{bytes_val / 1024:.1f} KB"
        elif bytes_val < 1024 * 1024 * 1024:
            return f"{bytes_val / (1024 * 1024):.1f} MB"
        else:
            return f"{bytes_val / (1024 * 1024 * 1024):.2f} GB"
    
    def update_item(self, item: DownloadItem):
        """Update the widget with new item data."""
        self.item = item
        
        # Update filename (truncate if too long)
        filename = item.filename
        if len(filename) > 40:
            filename = filename[:37] + "..."
        self.filename_label.setText(filename)
        self.filename_label.setToolTip(item.filename)
        
        # Update size label
        if item.total_bytes > 0:
            downloaded = self._format_size(item.downloaded_bytes)
            total = self._format_size(item.total_bytes)
            self.size_label.setText(f"{downloaded} / {total}")
        elif item.downloaded_bytes > 0:
            self.size_label.setText(self._format_size(item.downloaded_bytes))
        else:
            self.size_label.setText("")
        
        # Update progress bar
        self.progress_bar.setValue(int(item.progress))
        
        # Update progress bar color based on status
        if item.status == DownloadStatus.FAILED:
            self.progress_bar.setStyleSheet(f"""
                QProgressBar#downloadItemProgress {{
                    background-color: {Colors.BORDER_DEFAULT};
                    border-radius: 2px;
                }}
                QProgressBar#downloadItemProgress::chunk {{
                    background-color: {Colors.ACCENT_ERROR};
                    border-radius: 2px;
                }}
            """)
        elif item.status == DownloadStatus.COMPLETED:
            self.progress_bar.setStyleSheet(f"""
                QProgressBar#downloadItemProgress {{
                    background-color: {Colors.BORDER_DEFAULT};
                    border-radius: 2px;
                }}
                QProgressBar#downloadItemProgress::chunk {{
                    background-color: {Colors.ACCENT_SUCCESS};
                    border-radius: 2px;
                }}
            """)
        elif item.status == DownloadStatus.PAUSED:
            self.progress_bar.setStyleSheet(f"""
                QProgressBar#downloadItemProgress {{
                    background-color: {Colors.BORDER_DEFAULT};
                    border-radius: 2px;
                }}
                QProgressBar#downloadItemProgress::chunk {{
                    background-color: {Colors.TEXT_SECONDARY};
                    border-radius: 2px;
                }}
            """)
        
        # Update status label
        status_text = {
            DownloadStatus.PENDING: "Waiting...",
            DownloadStatus.DOWNLOADING: "Downloading...",
            DownloadStatus.PAUSED: "Paused",
            DownloadStatus.COMPLETED: "Completed",
            DownloadStatus.FAILED: f"Failed: {item.error_message[:30]}..." if len(item.error_message) > 30 else f"Failed: {item.error_message}" if item.error_message else "Failed",
            DownloadStatus.CANCELLED: "Cancelled",
        }
        self.status_label.setText(status_text.get(item.status, "Unknown"))
        
        # Update speed label
        self.speed_label.setText(item.speed if item.status == DownloadStatus.DOWNLOADING else "")
        
        self._update_buttons()


class DownloadPanel(QWidget):
    """
    Chromium-style download panel showing individual file progress.
    
    Features:
    - Collapsible header with overall progress
    - Scrollable list of download items
    - Individual pause/resume/cancel per file
    - Auto-collapse on completion
    """
    
    cancel_all_clicked = pyqtSignal()
    pause_all_clicked = pyqtSignal()
    resume_all_clicked = pyqtSignal()
    clear_completed_clicked = pyqtSignal()
    item_cancelled = pyqtSignal(int)  # download_id
    item_paused = pyqtSignal(int)  # download_id
    item_resumed = pyqtSignal(int)  # download_id
    item_retried = pyqtSignal(int)  # download_id
    open_folder_requested = pyqtSignal(int)  # download_id
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("downloadPanel")
        self.setVisible(False)
        
        self._items: Dict[int, DownloadItem] = {}
        self._item_widgets: Dict[int, DownloadItemWidget] = {}
        self._next_id = 1
        self._expanded = True
        
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._auto_hide)
        
        self._init_ui()
        self._apply_styles()
    
    def _init_ui(self):
        """Initialize UI components."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Header bar (always visible)
        self.header = QFrame()
        self.header.setObjectName("downloadPanelHeader")
        self.header.setFixedHeight(48)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(16, 0, 16, 0)
        header_layout.setSpacing(12)
        
        # Download icon
        self.header_icon = QLabel()
        self.header_icon.setPixmap(
            qta.icon('fa5s.download', color=Colors.ACCENT_SECONDARY).pixmap(20, 20)
        )
        header_layout.addWidget(self.header_icon)
        
        # Overall progress info
        self.header_label = QLabel("Downloads")
        self.header_label.setObjectName("downloadPanelTitle")
        header_layout.addWidget(self.header_label)
        
        # Overall progress bar (compact)
        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        self.overall_progress.setFixedWidth(100)
        self.overall_progress.setFixedHeight(6)
        self.overall_progress.setTextVisible(False)
        self.overall_progress.setObjectName("overallProgress")
        header_layout.addWidget(self.overall_progress)
        
        header_layout.addStretch()
        
        # Stats label (X of Y files)
        self.stats_label = QLabel("")
        self.stats_label.setObjectName("downloadStats")
        header_layout.addWidget(self.stats_label)
        
        # Expand/collapse button
        self.expand_btn = QPushButton()
        self.expand_btn.setFixedSize(28, 28)
        self.expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.expand_btn.setToolTip("Collapse")
        self.expand_btn.setIcon(qta.icon('fa5s.chevron-down', color=Colors.TEXT_SECONDARY))
        self.expand_btn.clicked.connect(self._toggle_expanded)
        header_layout.addWidget(self.expand_btn)
        
        # Clear all button
        self.clear_btn = QPushButton()
        self.clear_btn.setFixedSize(28, 28)
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.setToolTip("Clear completed")
        self.clear_btn.setIcon(qta.icon('fa5s.broom', color=Colors.TEXT_SECONDARY))
        self.clear_btn.clicked.connect(self._on_clear_completed)
        header_layout.addWidget(self.clear_btn)
        
        # Close button
        self.close_btn = QPushButton()
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setToolTip("Close")
        self.close_btn.setIcon(qta.icon('fa5s.times', color=Colors.TEXT_SECONDARY))
        self.close_btn.clicked.connect(self.hide)
        header_layout.addWidget(self.close_btn)
        
        main_layout.addWidget(self.header)
        
        # Scrollable content area
        self.content_area = QFrame()
        self.content_area.setObjectName("downloadPanelContent")
        content_layout = QVBoxLayout(self.content_area)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(4)
        
        # Scroll area for items
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setObjectName("downloadScrollArea")
        
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(4)
        self.scroll_layout.addStretch()
        
        self.scroll_area.setWidget(self.scroll_content)
        content_layout.addWidget(self.scroll_area)
        
        main_layout.addWidget(self.content_area)
        
        # Set size constraints
        self.setMinimumWidth(400)
        self.setMaximumHeight(350)
    
    def _apply_styles(self):
        """Apply widget styles."""
        self.setStyleSheet(f"""
            QWidget#downloadPanel {{
                background-color: {Colors.BG_PRIMARY};
                border-top: 1px solid {Colors.BORDER_DEFAULT};
            }}
            QFrame#downloadPanelHeader {{
                background-color: {Colors.BG_SECONDARY};
                border-bottom: 1px solid {Colors.BORDER_DEFAULT};
            }}
            QLabel#downloadPanelTitle {{
                color: {Colors.TEXT_PRIMARY};
                font-size: {Fonts.SIZE_MD}px;
                font-weight: 600;
            }}
            QLabel#downloadStats {{
                color: {Colors.TEXT_SECONDARY};
                font-size: {Fonts.SIZE_SM}px;
            }}
            QProgressBar#overallProgress {{
                background-color: {Colors.BORDER_DEFAULT};
                border-radius: 3px;
                border: none;
            }}
            QProgressBar#overallProgress::chunk {{
                background-color: {Colors.ACCENT_SECONDARY};
                border-radius: 3px;
            }}
            QFrame#downloadPanelContent {{
                background-color: {Colors.BG_PRIMARY};
            }}
            QScrollArea#downloadScrollArea {{
                border: none;
                background-color: transparent;
            }}
            QScrollArea#downloadScrollArea > QWidget > QWidget {{
                background-color: transparent;
            }}
            QPushButton {{
                background-color: transparent;
                border: none;
                border-radius: {Spacing.RADIUS_SM}px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BG_HOVER};
            }}
        """)
    
    def _toggle_expanded(self):
        """Toggle expanded/collapsed state."""
        self._expanded = not self._expanded
        self.content_area.setVisible(self._expanded)
        
        if self._expanded:
            self.expand_btn.setIcon(qta.icon('fa5s.chevron-down', color=Colors.TEXT_SECONDARY))
            self.expand_btn.setToolTip("Collapse")
            self.setMaximumHeight(350)
        else:
            self.expand_btn.setIcon(qta.icon('fa5s.chevron-up', color=Colors.TEXT_SECONDARY))
            self.expand_btn.setToolTip("Expand")
            self.setMaximumHeight(48)
    
    def _on_clear_completed(self):
        """Clear completed downloads from the list."""
        to_remove = [
            item_id for item_id, item in self._items.items()
            if item.status in (DownloadStatus.COMPLETED, DownloadStatus.CANCELLED)
        ]
        for item_id in to_remove:
            self.remove_item(item_id)
        
        self.clear_completed_clicked.emit()
        
        # Hide if no items left
        if not self._items:
            self.hide()
    
    def _auto_hide(self):
        """Auto-hide when all downloads complete."""
        if all(item.status in (DownloadStatus.COMPLETED, DownloadStatus.CANCELLED, DownloadStatus.FAILED)
               for item in self._items.values()):
            # Don't hide, just collapse
            if self._expanded:
                self._toggle_expanded()
    
    def _update_overall_progress(self):
        """Update overall progress indicator."""
        if not self._items:
            self.overall_progress.setValue(0)
            self.stats_label.setText("")
            return
        
        total_progress = sum(item.progress for item in self._items.values())
        avg_progress = total_progress / len(self._items)
        self.overall_progress.setValue(int(avg_progress))
        
        completed = sum(1 for item in self._items.values() if item.status == DownloadStatus.COMPLETED)
        total = len(self._items)
        self.stats_label.setText(f"{completed} of {total}")
        
        # Update header label
        downloading = sum(1 for item in self._items.values() if item.status == DownloadStatus.DOWNLOADING)
        if downloading > 0:
            self.header_label.setText(f"Downloading {downloading} file(s)")
        elif completed == total:
            self.header_label.setText("Downloads complete")
        else:
            pending = sum(1 for item in self._items.values() if item.status == DownloadStatus.PENDING)
            if pending > 0:
                self.header_label.setText(f"{pending} file(s) waiting")
            else:
                self.header_label.setText("Downloads")
    
    def add_download(self, url: str, filename: str, destination: Path) -> int:
        """
        Add a new download to the panel.
        
        Args:
            url: Download URL
            filename: Filename to display
            destination: Destination path
            
        Returns:
            Download ID for tracking
        """
        item_id = self._next_id
        self._next_id += 1
        
        item = DownloadItem(
            id=item_id,
            url=url,
            filename=filename,
            destination=destination,
            status=DownloadStatus.PENDING,
        )
        self._items[item_id] = item
        
        # Create widget
        widget = DownloadItemWidget(item)
        widget.pause_clicked.connect(self._on_item_pause)
        widget.resume_clicked.connect(self._on_item_resume)
        widget.cancel_clicked.connect(self._on_item_cancel)
        widget.retry_clicked.connect(self._on_item_retry)
        widget.open_folder_clicked.connect(self._on_open_folder)
        
        self._item_widgets[item_id] = widget
        
        # Add to layout (before the stretch)
        self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, widget)
        
        self._update_overall_progress()
        self.setVisible(True)
        self._hide_timer.stop()
        
        return item_id
    
    def update_download(
        self,
        item_id: int,
        *,
        status: Optional[DownloadStatus] = None,
        progress: Optional[float] = None,
        downloaded_bytes: Optional[int] = None,
        total_bytes: Optional[int] = None,
        error_message: Optional[str] = None,
        speed: Optional[str] = None,
    ):
        """Update a download item's state."""
        if item_id not in self._items:
            return
        
        item = self._items[item_id]
        
        if status is not None:
            item.status = status
        if progress is not None:
            item.progress = progress
        if downloaded_bytes is not None:
            item.downloaded_bytes = downloaded_bytes
        if total_bytes is not None:
            item.total_bytes = total_bytes
        if error_message is not None:
            item.error_message = error_message
        if speed is not None:
            item.speed = speed
        
        # Update widget
        if item_id in self._item_widgets:
            self._item_widgets[item_id].update_item(item)
        
        self._update_overall_progress()
        
        # Check if all downloads are done
        if all(item.status in (DownloadStatus.COMPLETED, DownloadStatus.CANCELLED, DownloadStatus.FAILED)
               for item in self._items.values()):
            self._hide_timer.start(5000)  # Auto-collapse after 5 seconds
    
    def remove_item(self, item_id: int):
        """Remove a download item from the panel."""
        if item_id in self._items:
            del self._items[item_id]
        
        if item_id in self._item_widgets:
            widget = self._item_widgets.pop(item_id)
            self.scroll_layout.removeWidget(widget)
            widget.deleteLater()
        
        self._update_overall_progress()
    
    def start_batch(self, items: List[Tuple[str, Path]]) -> List[int]:
        """
        Start a batch of downloads.
        
        Args:
            items: List of (url, destination) tuples
            
        Returns:
            List of download IDs
        """
        ids = []
        for url, destination in items:
            filename = destination.name
            item_id = self.add_download(url, filename, destination)
            ids.append(item_id)
        return ids
    
    def get_item(self, item_id: int) -> Optional[DownloadItem]:
        """Get a download item by ID."""
        return self._items.get(item_id)
    
    def get_all_items(self) -> Dict[int, DownloadItem]:
        """Get all download items."""
        return self._items.copy()
    
    def _on_item_pause(self, item_id: int):
        """Handle item pause request."""
        if item_id in self._items:
            self._items[item_id].status = DownloadStatus.PAUSED
            self._item_widgets[item_id].update_item(self._items[item_id])
            self._update_overall_progress()
            self.item_paused.emit(item_id)
    
    def _on_item_resume(self, item_id: int):
        """Handle item resume request."""
        if item_id in self._items:
            self._items[item_id].status = DownloadStatus.DOWNLOADING
            self._item_widgets[item_id].update_item(self._items[item_id])
            self._update_overall_progress()
            self.item_resumed.emit(item_id)
    
    def _on_item_cancel(self, item_id: int):
        """Handle item cancel request."""
        if item_id in self._items:
            self._items[item_id].status = DownloadStatus.CANCELLED
            self._item_widgets[item_id].update_item(self._items[item_id])
            self._update_overall_progress()
            self.item_cancelled.emit(item_id)
    
    def _on_item_retry(self, item_id: int):
        """Handle item retry request."""
        if item_id in self._items:
            self._items[item_id].status = DownloadStatus.PENDING
            self._items[item_id].progress = 0
            self._items[item_id].error_message = ""
            self._item_widgets[item_id].update_item(self._items[item_id])
            self._update_overall_progress()
            self.item_retried.emit(item_id)
    
    def _on_open_folder(self, item_id: int):
        """Handle open folder request."""
        self.open_folder_requested.emit(item_id)


# Export for use in other modules
__all__ = ['DownloadPanel', 'DownloadItem', 'DownloadStatus', 'DownloadItemWidget']
