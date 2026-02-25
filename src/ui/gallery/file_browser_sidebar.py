"""
File browser sidebar for gallery post view.

Slide-out sidebar showing all post files (images, videos, attachments) with:
- File type grouping
- Size/type sorting
- Multi-selection for bulk download
- Compact detail view layout
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QScrollArea, QFrame, QCheckBox, QSizePolicy, QToolButton, QMenu
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QPoint
from PyQt6.QtGui import QCursor, QMouseEvent
import qtawesome as qta
import logging
from pathlib import Path
from typing import List, Dict
from enum import Enum

from src.core.dto.file import FileDTO
from src.core.media_manager import MediaManager
from src.core.file_metadata_manager import FileMetadataManager
from src.ui.common.theme import Colors, FileSidebar
from src.ui.common.view_models import MediaItem

logger = logging.getLogger(__name__)


class FileCategory(Enum):
    """File category for grouping."""
    IMAGES = "Images"
    VIDEOS = "Videos"
    DOCUMENTS = "Documents"
    ARCHIVES = "Archives"
    AUDIO = "Audio"
    OTHER = "Other Files"


class SortBy(Enum):
    """Sort options."""
    NAME = "Name"
    SIZE = "Size"
    TYPE = "Type"


class GroupBy(Enum):
    """Group by options."""
    CATEGORY = "File Category"
    EXTENSION = "File Extension"
    ALPHANUMERIC = "A-Z, 0-9"
    NONE = "Off"


# File type to category and icon mapping
FILE_TYPE_INFO = {
    # Images
    'jpg': ('image', FileCategory.IMAGES, ('fa5s.file-image', '#3498db')),
    'jpeg': ('image', FileCategory.IMAGES, ('fa5s.file-image', '#3498db')),
    'png': ('image', FileCategory.IMAGES, ('fa5s.file-image', '#3498db')),
    'gif': ('image', FileCategory.IMAGES, ('fa5s.file-image', '#3498db')),
    'webp': ('image', FileCategory.IMAGES, ('fa5s.file-image', '#3498db')),
    'bmp': ('image', FileCategory.IMAGES, ('fa5s.file-image', '#3498db')),
    
    # Videos
    'mp4': ('video', FileCategory.VIDEOS, ('fa5s.file-video', '#9b59b6')),
    'webm': ('video', FileCategory.VIDEOS, ('fa5s.file-video', '#9b59b6')),
    'mkv': ('video', FileCategory.VIDEOS, ('fa5s.file-video', '#9b59b6')),
    'avi': ('video', FileCategory.VIDEOS, ('fa5s.file-video', '#9b59b6')),
    'mov': ('video', FileCategory.VIDEOS, ('fa5s.file-video', '#9b59b6')),
    'm4v': ('video', FileCategory.VIDEOS, ('fa5s.file-video', '#9b59b6')),
    
    # Documents
    'pdf': ('document', FileCategory.DOCUMENTS, ('fa5s.file-pdf', '#e74c3c')),
    'doc': ('document', FileCategory.DOCUMENTS, ('fa5s.file-word', '#2980b9')),
    'docx': ('document', FileCategory.DOCUMENTS, ('fa5s.file-word', '#2980b9')),
    'txt': ('document', FileCategory.DOCUMENTS, ('fa5s.file-alt', '#95a5a6')),
    'md': ('document', FileCategory.DOCUMENTS, ('fa5s.file-alt', '#95a5a6')),
    
    # Archives
    'zip': ('archive', FileCategory.ARCHIVES, ('fa5s.file-archive', '#f39c12')),
    'rar': ('archive', FileCategory.ARCHIVES, ('fa5s.file-archive', '#f39c12')),
    '7z': ('archive', FileCategory.ARCHIVES, ('fa5s.file-archive', '#f39c12')),
    'tar': ('archive', FileCategory.ARCHIVES, ('fa5s.file-archive', '#f39c12')),
    'gz': ('archive', FileCategory.ARCHIVES, ('fa5s.file-archive', '#f39c12')),
    
    # Audio
    'mp3': ('audio', FileCategory.AUDIO, ('fa5s.file-audio', '#1abc9c')),
    'wav': ('audio', FileCategory.AUDIO, ('fa5s.file-audio', '#1abc9c')),
    'flac': ('audio', FileCategory.AUDIO, ('fa5s.file-audio', '#1abc9c')),
    'ogg': ('audio', FileCategory.AUDIO, ('fa5s.file-audio', '#1abc9c')),
}


def get_file_extension(filename: str) -> str:
    """Extract file extension from filename."""
    if '.' in filename:
        return filename.rsplit('.', 1)[-1].lower()
    return ''


def get_file_info(filename: str) -> tuple:
    """Get file type, category, and icon info."""
    ext = get_file_extension(filename)
    if ext in FILE_TYPE_INFO:
        return FILE_TYPE_INFO[ext]
    return ('file', FileCategory.OTHER, ('fa5s.file', '#7f8c8d'))


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    if size_bytes is None:
        return "Unknown"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


class FileListItem(QFrame):
    """Single file item in detail view style."""
    
    selection_changed = pyqtSignal(bool)  # selected state
    download_clicked = pyqtSignal()
    jdownloader_clicked = pyqtSignal()  # JDownloader export for single file
    
    def __init__(self, file_data: dict, jdownloader_enabled: bool = False, parent=None):
        super().__init__(parent)
        self.file_data = file_data
        self.checkbox = None
        self._jdownloader_enabled = jdownloader_enabled
        self._setup_ui()
        
    def _setup_ui(self):
        """Setup compact detail view item."""
        self.setObjectName("fileListItem")
        self.setMinimumWidth(FileSidebar.WIDTH_MIN)
        self.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)
        
        # Checkbox
        self.checkbox = QCheckBox()
        self.checkbox.setFixedSize(FileSidebar.CHECKBOX_SIZE, FileSidebar.CHECKBOX_SIZE)
        self.checkbox.stateChanged.connect(lambda: self.selection_changed.emit(self.checkbox.isChecked()))
        layout.addWidget(self.checkbox)
        
        # Icon
        icon_name, icon_color = self.file_data['icon']
        icon_label = QLabel()
        icon_label.setPixmap(qta.icon(icon_name, color=icon_color).pixmap(QSize(FileSidebar.ITEM_ICON, FileSidebar.ITEM_ICON)))
        icon_label.setFixedSize(FileSidebar.ITEM_ICON, FileSidebar.ITEM_ICON)
        layout.addWidget(icon_label)
        
        # Filename (flexible width)
        name_label = QLabel(self.file_data['name'])
        name_label.setObjectName("fileListItemName")
        name_label.setWordWrap(True)
        name_label.setMinimumWidth(40)
        name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(name_label, 1)
        
        # Type
        type_label = QLabel(self.file_data['extension'].upper())
        type_label.setObjectName("fileListItemType")
        type_label.setFixedWidth(40)
        type_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(type_label)
        
        # Size
        size_label = QLabel(format_file_size(self.file_data.get('size')))
        size_label.setObjectName("fileListItemSize")
        size_label.setFixedWidth(70)
        size_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(size_label)
        
        # Download button with dropdown menu if JDownloader enabled
        if self._jdownloader_enabled:
            download_btn = QToolButton()
            download_btn.setIcon(qta.icon('fa5s.download', color=Colors.TEXT_SECONDARY))
            download_btn.setIconSize(QSize(FileSidebar.ITEM_DOWNLOAD_ICON, FileSidebar.ITEM_DOWNLOAD_ICON))
            download_btn.setFixedSize(FileSidebar.ITEM_DOWNLOAD_BUTTON, FileSidebar.ITEM_DOWNLOAD_BUTTON)
            download_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            download_btn.setObjectName("fileListItemDownloadButton")
            download_btn.setStyleSheet(
                """
                QToolButton#fileListItemDownloadButton::menu-indicator {
                    image: none;
                    width: 0px;
                }
                """
            )

            # Create dropdown menu (no arrow)
            menu = QMenu(download_btn)
            download_action = menu.addAction(qta.icon('fa5s.download', color=Colors.TEXT_SECONDARY), "Download")
            download_action.triggered.connect(self.download_clicked.emit)
            jd_action = menu.addAction(qta.icon('fa5s.external-link-alt', color=Colors.TEXT_SECONDARY), "Send to JDownloader")
            jd_action.triggered.connect(self.jdownloader_clicked.emit)
            download_btn.setMenu(menu)
            download_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        else:
            download_btn = QPushButton()
            download_btn.setIcon(qta.icon('fa5s.download', color=Colors.TEXT_SECONDARY))
            download_btn.setIconSize(QSize(FileSidebar.ITEM_DOWNLOAD_ICON, FileSidebar.ITEM_DOWNLOAD_ICON))
            download_btn.setFixedSize(FileSidebar.ITEM_DOWNLOAD_BUTTON, FileSidebar.ITEM_DOWNLOAD_BUTTON)
            download_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            download_btn.setObjectName("fileListItemDownloadButton")
            download_btn.clicked.connect(self.download_clicked.emit)
        
        layout.addWidget(download_btn)
    
    def is_selected(self) -> bool:
        """Check if item is selected."""
        return self.checkbox.isChecked()
    
    def set_selected(self, selected: bool):
        """Set selection state."""
        self.checkbox.setChecked(selected)


class FileCategoryGroup(QWidget):
    """Collapsible group of files by category."""
    
    def __init__(self, category: FileCategory, parent=None):
        super().__init__(parent)
        self.category = category
        self.file_items: List[FileListItem] = []
        self._collapsed = False
        self._setup_ui()
        
    def _setup_ui(self):
        """Setup category group."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
        # Header
        header = QFrame()
        header.setObjectName("fileCategoryHeader")
        header.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        header.mousePressEvent = lambda e: self._toggle_collapse()
        
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(4, 4, 4, 4)
        header_layout.setSpacing(8)
        
        # Collapse icon
        self.collapse_icon = QLabel()
        self._update_collapse_icon()
        header_layout.addWidget(self.collapse_icon)
        
        # Category name
        self.title_label = QLabel(self.category.value)
        self.title_label.setObjectName("fileCategoryTitle")
        header_layout.addWidget(self.title_label)
        
        # Count
        self.count_label = QLabel("0")
        self.count_label.setObjectName("fileCategoryCount")
        header_layout.addWidget(self.count_label)
        
        header_layout.addStretch()
        layout.addWidget(header)
        
        # Container for items
        self.items_container = QWidget()
        self.items_layout = QVBoxLayout(self.items_container)
        self.items_layout.setContentsMargins(0, 0, 0, 0)
        self.items_layout.setSpacing(0)
        self.items_layout.addStretch()
        
        layout.addWidget(self.items_container)
        
    def _update_collapse_icon(self):
        """Update collapse/expand icon."""
        icon_name = 'fa5s.chevron-down' if not self._collapsed else 'fa5s.chevron-right'
        self.collapse_icon.setPixmap(
            qta.icon(icon_name, color=Colors.TEXT_SECONDARY).pixmap(QSize(12, 12))
        )
        
    def _toggle_collapse(self):
        """Toggle collapsed state."""
        self._collapsed = not self._collapsed
        self.items_container.setVisible(not self._collapsed)
        self._update_collapse_icon()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        self.items_container.setVisible(not self._collapsed)
        self._update_collapse_icon()
        
    def add_file(self, file_item: FileListItem):
        """Add file item to group."""
        self.file_items.append(file_item)
        self.items_layout.insertWidget(self.items_layout.count() - 1, file_item)
        self.count_label.setText(str(len(self.file_items)))
        
    def clear_files(self):
        """Clear all file items."""
        for item in self.file_items:
            item.deleteLater()
        self.file_items.clear()
        self.count_label.setText("0")
    
    def get_selected_files(self) -> List[dict]:
        """Get selected file data."""
        return [item.file_data for item in self.file_items if item.is_selected()]
    
    def select_all(self):
        """Select all files in this group."""
        for item in self.file_items:
            item.set_selected(True)
    
    def select_none(self):
        """Deselect all files in this group."""
        for item in self.file_items:
            item.set_selected(False)


class FileBrowserSidebar(QWidget):
    """Slide-out sidebar for browsing and selecting post files."""
    
    download_requested = pyqtSignal(list)  # List of file URLs
    jdownloader_requested = pyqtSignal(list)  # List of file URLs for JDownloader export
    closed = pyqtSignal()
    
    def __init__(
        self,
        parent=None,
        metadata_manager: FileMetadataManager = None,
        db_manager=None,
        *,
        embedded: bool = False,
    ):
        super().__init__(parent)
        self.files = []
        self.category_groups: Dict[FileCategory, FileCategoryGroup] = {}
        self._embedded = bool(embedded)
        self._current_width = FileSidebar.WIDTH_DEFAULT
        self._resizing = False
        self._resize_start_x = 0
        self._resize_start_width = 0
        self._last_resize_width = 0  # Track last applied width to avoid redundant updates
        self._current_sort = SortBy.NAME  # Default sort
        self._sort_ascending = True
        self._current_group = GroupBy.CATEGORY  # Default group
        self._metadata_manager = metadata_manager or FileMetadataManager()
        self._db = db_manager
        self._jdownloader_enabled = self._check_jdownloader_enabled()
        self._url_to_file_map: Dict[str, dict] = {}  # Map URL to file data for metadata updates
        self._setup_ui()
    
    def _check_jdownloader_enabled(self) -> bool:
        """Check if JDownloader export is enabled in settings."""
        if self._db:
            return self._db.get_config('jdownloader_enabled', 'false') == 'true'
        return False
    
    def refresh_jdownloader_setting(self):
        """Refresh JDownloader enabled state from settings."""
        self._jdownloader_enabled = self._check_jdownloader_enabled()
        
    def _setup_ui(self):
        """Setup sidebar UI."""
        # Set width constraints (resizable between 250-600px)
        self.setObjectName("fileBrowserSidebar")
        if self._embedded:
            self.setMinimumWidth(0)
            self.setMaximumWidth(FileSidebar.WIDTH_MAX)
        else:
            self.setMinimumWidth(0)  # Start hidden
            self.setMaximumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header
        header = self._create_header()
        layout.addWidget(header)
        
        # Toolbar
        toolbar = self._create_toolbar()
        layout.addWidget(toolbar)
        
        # Scroll area for file groups
        scroll = QScrollArea()
        scroll.setObjectName("fileBrowserScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        self.container = QWidget()
        self.container.setObjectName("fileBrowserContainer")
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(8, 8, 8, 8)
        self.container_layout.setSpacing(4)
        self.container_layout.addStretch()
        
        scroll.setWidget(self.container)
        layout.addWidget(scroll, 1)
        
        # Footer with bulk actions
        footer = self._create_footer()
        layout.addWidget(footer)
        
        # Enable mouse tracking for resize cursor
        self.setMouseTracking(True)
        
        # Connect to Core metadata manager signals
        self._metadata_manager.metadata_received.connect(self._on_metadata_ready)
        self._metadata_manager.query_completed.connect(self._on_metadata_complete)
    
    def mousePressEvent(self, event: QMouseEvent):
        """Handle mouse press for resize."""
        if self._embedded:
            return super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            # Check if clicking near left edge (within 5px)
            if event.pos().x() <= FileSidebar.RESIZE_HANDLE:
                self._resizing = True
                self._resize_start_x = event.globalPosition().x()
                self._resize_start_width = self.width()
                self._last_resize_width = self.width()  # Initialize tracking
                event.accept()
                return
        super().mousePressEvent(event)
    
    def mouseMoveEvent(self, event: QMouseEvent):
        """Handle mouse move for resize cursor and dragging."""
        if self._embedded:
            return super().mouseMoveEvent(event)
        if self._resizing:
            # Calculate new width (drag left increases width)
            delta = self._resize_start_x - event.globalPosition().x()
            new_width = self._resize_start_width + delta
            
            # Clamp to min/max and convert to int
            new_width = int(max(FileSidebar.WIDTH_MIN, min(FileSidebar.WIDTH_MAX, new_width)))
            
            # Only update if width changed by at least 1px to reduce repaints
            if abs(new_width - self._last_resize_width) >= 1:
                self.setFixedWidth(new_width)
                self._last_resize_width = new_width
            event.accept()
        elif event.pos().x() <= FileSidebar.RESIZE_HANDLE:
            # Show resize cursor when hovering near left edge
            self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        
        super().mouseMoveEvent(event)
    
    def mouseReleaseEvent(self, event: QMouseEvent):
        """Handle mouse release to stop resizing."""
        if self._embedded:
            return super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self._resizing:
            self._resizing = False
            # Save the new width and keep it fixed
            self._current_width = self.width()
            # Keep the sidebar at the resized width
            self.setFixedWidth(self._current_width)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        
    def _create_header(self) -> QWidget:
        """Create header with title and close button."""
        header = QFrame()
        header.setObjectName("fileBrowserHeader")
        header.setFixedHeight(FileSidebar.HEADER_HEIGHT)
        
        layout = QHBoxLayout(header)
        layout.setContentsMargins(12, 8, 12, 8)
        
        # Title
        title = QLabel("Files")
        title.setObjectName("fileBrowserHeaderTitle")
        layout.addWidget(title)
        
        # Count
        self.total_count_label = QLabel("0 files")
        self.total_count_label.setObjectName("fileBrowserHeaderCount")
        layout.addWidget(self.total_count_label)

        # Group menu
        self.group_menu_btn = QToolButton()
        self.group_menu_btn.setObjectName("fileBrowserGroupButton")
        self.group_menu_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.group_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.group_menu_btn.setText(f"Group: {self._current_group.value}")
        self.group_menu_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.group_menu_btn.setStyleSheet(
            """
            QToolButton#fileBrowserGroupButton::menu-indicator {
                image: none;
                width: 0px;
            }
            """
        )
        self._group_menu = QMenu(self.group_menu_btn)
        for group in GroupBy:
            action = self._group_menu.addAction(group.value)
            action.triggered.connect(lambda checked, value=group: self._set_group(value))
        self.group_menu_btn.setMenu(self._group_menu)
        layout.addWidget(self.group_menu_btn)
        
        layout.addStretch()
        
        if not self._embedded:
            # Close button
            close_btn = QPushButton()
            close_btn.setIcon(qta.icon('fa5s.times', color=Colors.TEXT_PRIMARY))
            close_btn.setIconSize(QSize(FileSidebar.HEADER_ICON, FileSidebar.HEADER_ICON))
            close_btn.setFixedSize(FileSidebar.HEADER_BUTTON, FileSidebar.HEADER_BUTTON)
            close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            close_btn.setObjectName("fileBrowserCloseButton")
            close_btn.clicked.connect(self.hide_sidebar)
            layout.addWidget(close_btn)
        
        return header
    
    def _create_toolbar(self) -> QWidget:
        """Create toolbar with column headers and group controls."""
        toolbar = QFrame()
        toolbar.setObjectName("fileBrowserToolbar")
        toolbar.setMinimumWidth(FileSidebar.WIDTH_MIN)
        toolbar.setFixedHeight(FileSidebar.TOOLBAR_HEIGHT)
        
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(18, 0, 9, 0)
        layout.setSpacing(0)
        
        # Master selection toggle button (aligns with checkbox column)
        self.master_selection_btn = QPushButton()
        self.master_selection_btn.setCheckable(True)
        self.master_selection_btn.setFixedSize(FileSidebar.CHECKBOX_SIZE, FileSidebar.CHECKBOX_SIZE)
        self.master_selection_btn.setIcon(qta.icon('fa5s.square', color=Colors.TEXT_SECONDARY))
        self.master_selection_btn.setIconSize(QSize(FileSidebar.MASTER_TOGGLE_ICON, FileSidebar.MASTER_TOGGLE_ICON))
        self.master_selection_btn.clicked.connect(self._handle_master_toggle)
        self.master_selection_btn.setToolTip("Select/Deselect all")
        self.master_selection_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.master_selection_btn.setObjectName("fileBrowserMasterToggle")
        layout.addWidget(self.master_selection_btn)

        # Spacer to align with icon column
        icon_spacer = QWidget()
        icon_spacer.setFixedWidth(10)
        icon_spacer.setObjectName("fileBrowserIconSpacer")
        layout.addWidget(icon_spacer)

        # Column headers
        self._sort_buttons = {}
        name_btn = QToolButton()
        name_btn.setObjectName("fileBrowserColumnName")
        name_btn.setProperty("fileBrowserColumn", True)
        name_btn.setMinimumWidth(80)
        name_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        name_btn.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred)
        name_btn.clicked.connect(lambda: self._set_sort(SortBy.NAME))
        layout.addWidget(name_btn, 1)
        self._sort_buttons[SortBy.NAME] = name_btn

        type_btn = QToolButton()
        type_btn.setObjectName("fileBrowserColumnType")
        type_btn.setProperty("fileBrowserColumn", True)
        type_btn.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred)
        type_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        type_btn.setFixedWidth(70)
        type_btn.clicked.connect(lambda: self._set_sort(SortBy.TYPE))
        layout.addWidget(type_btn, 1)
        self._sort_buttons[SortBy.TYPE] = type_btn

        size_btn = QToolButton()
        size_btn.setObjectName("fileBrowserColumnSize")
        size_btn.setProperty("fileBrowserColumn", True)
        size_btn.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred)
        size_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        size_btn.setFixedWidth(94)
        size_btn.clicked.connect(lambda: self._set_sort(SortBy.SIZE))
        layout.addWidget(size_btn, 1)
        self._sort_buttons[SortBy.SIZE] = size_btn

        # Spacer to align with download button column
        download_spacer = QWidget()
        download_spacer.setFixedWidth(FileSidebar.ITEM_DOWNLOAD_BUTTON)
        download_spacer.setObjectName("fileBrowserDownloadSpacer")
        #layout.addWidget(download_spacer)

        self._update_sort_headers()
        
        return toolbar
    
    def _create_footer(self) -> QWidget:
        """Create footer with bulk download."""
        footer = QFrame()
        footer.setObjectName("fileBrowserFooter")
        footer.setFixedHeight(FileSidebar.FOOTER_HEIGHT)
        
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(12, 8, 12, 8)
        
        # Selected count
        self.selected_count_label = QLabel("0 selected")
        self.selected_count_label.setObjectName("fileBrowserSelectedCount")
        layout.addWidget(self.selected_count_label)
        
        layout.addStretch()

        # Download selected button with dropdown menu
        self.download_btn = QPushButton()
        self.download_btn.setObjectName("fileBrowserDownloadButton")
        self.download_btn.setText("Download Selected")
        self.download_btn.setIcon(qta.icon('fa5s.download', color=Colors.TEXT_PRIMARY))
        self.download_btn.setFixedHeight(FileSidebar.DOWNLOAD_BUTTON_HEIGHT)
        self.download_btn.setMinimumWidth(190)
        self.download_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.download_btn.setEnabled(False)
        self.download_btn.setStyleSheet(
            """
            QPushButton#fileBrowserDownloadButton {
                text-align: center;
            }
            """
        )

        # Create download menu
        self._download_menu = QMenu(self.download_btn)
        download_action = self._download_menu.addAction(
            qta.icon('fa5s.download', color=Colors.TEXT_SECONDARY),
            "Download Selected"
        )
        download_action.triggered.connect(self._download_selected)

        # Add JDownloader option if enabled
        if self._jdownloader_enabled:
            jd_action = self._download_menu.addAction(
                qta.icon('fa5s.external-link-alt', color=Colors.TEXT_SECONDARY),
                "Send to JDownloader"
            )
            jd_action.triggered.connect(self._jdownloader_selected)

        self.download_btn.clicked.connect(self._show_download_menu)
        layout.addWidget(self.download_btn)
        
        return footer

    def _show_download_menu(self):
        if not hasattr(self, "_download_menu") or not self._download_menu:
            return
        if not self.download_btn.isEnabled():
            return
        menu_pos = self.download_btn.mapToGlobal(QPoint(0, self.download_btn.height()))
        self._download_menu.popup(menu_pos)
    
    def set_files(self, media_items: List[MediaItem], file_dtos: List[FileDTO], platform: str = "coomer"):
        """Set files to display."""
        # Batch updates to prevent white flashes
        self.setUpdatesEnabled(False)
        try:
            # Clear existing
            for group in self.category_groups.values():
                group.clear_files()
                group.deleteLater()
            self.category_groups.clear()
        
            # Combine media items and file DTOs into unified list
            all_files = []
        
            # Add media items
            for media_item in media_items:
                ext = get_file_extension(media_item.name)
                file_type, category, icon_info = get_file_info(media_item.name)
                all_files.append({
                    'name': media_item.name,
                    'url': media_item.url,
                    'extension': ext,
                    'type': file_type,
                    'category': category,
                    'icon': icon_info,
                    'size': None,  # Size not available in MediaItem
                })
        
            # Add file DTOs
            for file_dto in file_dtos:
                ext = get_file_extension(file_dto.name)
                file_type, category, icon_info = get_file_info(file_dto.name)
                # Build URL from path
                url = MediaManager.build_media_url(platform, file_dto.path) if file_dto.path else None
                all_files.append({
                    'name': file_dto.name,
                    'url': url,
                    'file_dto': file_dto,
                    'extension': ext,
                    'type': file_type,
                    'category': category,
                    'icon': icon_info,
                    'size': getattr(file_dto, 'size', None),
                })
        
            self.files = all_files
            self.total_count_label.setText(f"{len(all_files)} files")
        
            # Build URL to file mapping for metadata updates
            self._url_to_file_map = {f['url']: f for f in all_files if f.get('url')}
        
            # Build groups with current sort order
            self._rebuild_groups()
        finally:
            self.setUpdatesEnabled(True)
        
        # Start asynchronous metadata query
        self._start_metadata_query(platform)
    
    def _update_selection_count(self):
        """Update selected count label and master button state."""
        selected = sum(len(group.get_selected_files()) for group in self.category_groups.values())
        total = len(self.files)
        
        self.selected_count_label.setText(f"{selected} selected")
        self.download_btn.setEnabled(selected > 0)

        # Update master button icon and state
        if selected == total and total > 0:
            # All selected
            self.master_selection_btn.setIcon(qta.icon('fa5s.check-square', color=Colors.TEXT_PRIMARY))
            self.master_selection_btn.setChecked(True)
        elif selected > 0:
            # Partial selection
            self.master_selection_btn.setIcon(qta.icon('fa5s.minus-square', color=Colors.TEXT_PRIMARY))
            self.master_selection_btn.setChecked(True)
        else:
            # None selected
            self.master_selection_btn.setIcon(qta.icon('fa5s.square', color=Colors.TEXT_SECONDARY))
            self.master_selection_btn.setChecked(False)
    
    def _handle_master_toggle(self):
        """Handle master selection toggle button."""
        # If button is checked (becoming checked), select all; otherwise deselect all
        if self.master_selection_btn.isChecked():
            for group in self.category_groups.values():
                group.select_all()
        else:
            for group in self.category_groups.values():
                group.select_none()
        self._update_selection_count()
    
    def _start_metadata_query(self, platform: str):
        """Request metadata query from Core service."""
        # Only query if we have files
        if not self.files:
            return
        
        # Extract URLs for images and videos only
        urls_to_query = []
        for file_data in self.files:
            url = file_data.get('url')
            if not url:
                continue
            
            # Only query for images/videos
            file_type = file_data.get('type')
            if file_type in ['image', 'video']:
                urls_to_query.append(url)
        
        if not urls_to_query:
            return
        
        # Request metadata from Core
        self._metadata_manager.query_metadata(urls_to_query, platform)
        
        logger.debug(f"Requested metadata query for {len(urls_to_query)} files")
    
    def _on_metadata_ready(self, url: str, metadata: dict):
        """Handle metadata result for a file."""
        # Find the file in our data
        file_data = self._url_to_file_map.get(url)
        if not file_data:
            return
        
        # Update file data with metadata
        updated = False
        
        if 'size' in metadata and not file_data.get('size'):
            file_data['size'] = metadata['size']
            updated = True
        
        if 'mime' in metadata:
            file_data['mime'] = metadata['mime']
            updated = True
        
        if 'filename' in metadata:
            # Update name to use real filename instead of hash
            old_name = file_data['name']
            new_name = metadata['filename']
            file_data['name'] = new_name
            file_data['original_name'] = old_name
            updated = True
            logger.debug(f"Found real filename: {old_name} -> {new_name}")
        
        # If data was updated and sorting is by name or size, might need to re-sort
        if updated:
            if self._current_sort in (SortBy.NAME, SortBy.SIZE):
                # Re-build groups to reflect updated data
                self._rebuild_groups()
    
    def _on_metadata_complete(self):
        """Handle completion of metadata queries."""
        logger.debug("Metadata query completed for all files")
    
    def _sort_files(self, files: List[dict]) -> List[dict]:
        """Sort files based on current sort order."""
        if self._current_sort == SortBy.NAME:
            return sorted(files, key=lambda f: f['name'].lower(), reverse=not self._sort_ascending)
        elif self._current_sort == SortBy.SIZE:
            # Sort by size, put None sizes at end
            if self._sort_ascending:
                return sorted(files, key=lambda f: (f['size'] is None, f['size'] or 0))
            return sorted(files, key=lambda f: (f['size'] is None, -(f['size'] or 0)))
        elif self._current_sort == SortBy.TYPE:
            # Sort by extension
            return sorted(files, key=lambda f: f['extension'].lower(), reverse=not self._sort_ascending)
        return files
    
    def _rebuild_groups(self):
        """Rebuild category groups with current sort and group order."""
        if not self.files:
            return
        
        collapsed_map = {key: group.is_collapsed() for key, group in self.category_groups.items()}

        # Clear existing groups
        for group in self.category_groups.values():
            group.clear_files()
            group.deleteLater()
        self.category_groups.clear()

        self._collapsed_groups = collapsed_map
        
        # Sort files
        sorted_files = self._sort_files(self.files)
        
        if self._current_group == GroupBy.NONE:
            # No grouping - single flat list
            self._create_flat_list(sorted_files)
        elif self._current_group == GroupBy.EXTENSION:
            # Group by file extension
            self._create_extension_groups(sorted_files)
        elif self._current_group == GroupBy.ALPHANUMERIC:
            # Group by first character (A-Z, 0-9, *)
            self._create_alphanumeric_groups(sorted_files)
        else:
            # Default: Group by file category
            self._create_category_groups(sorted_files)
        
        if hasattr(self, "_collapsed_groups"):
            delattr(self, "_collapsed_groups")
        self._update_selection_count()

    def _apply_group_collapse(self, key, group: FileCategoryGroup) -> None:
        collapsed = getattr(self, "_collapsed_groups", {}).get(key)
        if collapsed is not None:
            group.set_collapsed(collapsed)
    
    def _create_flat_list(self, files: List[dict]):
        """Create a single group with all files (no grouping)."""
        # Create a single "All Files" group
        group = FileCategoryGroup(FileCategory.OTHER)  # Reuse FileCategoryGroup
        group.title_label.setText("All Files")
        group.count_label.setText(str(len(files)))
        self.category_groups[FileCategory.OTHER] = group
        self.container_layout.insertWidget(self.container_layout.count() - 1, group)
        self._apply_group_collapse(FileCategory.OTHER, group)
        
        # Add all files
        for file_data in files:
            file_item = FileListItem(file_data, jdownloader_enabled=self._jdownloader_enabled)
            file_item.selection_changed.connect(self._update_selection_count)
            file_item.download_clicked.connect(lambda fd=file_data: self._download_single(fd))
            file_item.jdownloader_clicked.connect(lambda fd=file_data: self._jdownloader_single(fd))
            group.add_file(file_item)
    
    def _create_extension_groups(self, files: List[dict]):
        """Group files by extension."""
        # Group files by extension
        ext_groups = {}
        for file_data in files:
            ext = file_data['extension'].upper()
            if ext not in ext_groups:
                ext_groups[ext] = []
            ext_groups[ext].append(file_data)
        
        # Sort extensions alphabetically
        for ext in sorted(ext_groups.keys()):
            category_files = ext_groups[ext]
            
            # Create group (reuse FileCategory enum with custom label)
            category = FileCategory.OTHER  # Use OTHER as placeholder
            group = FileCategoryGroup(category)
            group.title_label.setText(f".{ext.lower()}")
            group.count_label.setText(str(len(category_files)))
            group_key = f"ext_{ext}"
            self.category_groups[group_key] = group  # Use string key for extensions
            self.container_layout.insertWidget(self.container_layout.count() - 1, group)
            self._apply_group_collapse(group_key, group)
            
            # Add files
            for file_data in category_files:
                file_item = FileListItem(file_data, jdownloader_enabled=self._jdownloader_enabled)
                file_item.selection_changed.connect(self._update_selection_count)
                file_item.download_clicked.connect(lambda fd=file_data: self._download_single(fd))
                file_item.jdownloader_clicked.connect(lambda fd=file_data: self._jdownloader_single(fd))
                group.add_file(file_item)
    
    def _create_alphanumeric_groups(self, files: List[dict]):
        """Group files by first character (A-Z, 0-9, *)."""
        # Group files by first character
        char_groups = {}
        for file_data in files:
            first_char = file_data['name'][0].upper() if file_data['name'] else '*'
            if first_char.isalpha():
                key = first_char
            elif first_char.isdigit():
                key = first_char
            else:
                key = '*'
            
            if key not in char_groups:
                char_groups[key] = []
            char_groups[key].append(file_data)
        
        # Sort keys: A-Z, then 0-9, then *
        def sort_key(k):
            if k.isalpha():
                return (0, k)
            elif k.isdigit():
                return (1, k)
            else:
                return (2, k)
        
        for char in sorted(char_groups.keys(), key=sort_key):
            category_files = char_groups[char]
            
            # Create group
            category = FileCategory.OTHER  # Use OTHER as placeholder
            group = FileCategoryGroup(category)
            group.title_label.setText(char)
            group.count_label.setText(str(len(category_files)))
            group_key = f"char_{char}"
            self.category_groups[group_key] = group
            self.container_layout.insertWidget(self.container_layout.count() - 1, group)
            self._apply_group_collapse(group_key, group)
            
            # Add files
            for file_data in category_files:
                file_item = FileListItem(file_data, jdownloader_enabled=self._jdownloader_enabled)
                file_item.selection_changed.connect(self._update_selection_count)
                file_item.download_clicked.connect(lambda fd=file_data: self._download_single(fd))
                file_item.jdownloader_clicked.connect(lambda fd=file_data: self._jdownloader_single(fd))
                group.add_file(file_item)
    
    def _create_category_groups(self, files: List[dict]):
        """Group files by file category (default)."""
        # Define category display order
        category_order = [
            FileCategory.IMAGES,
            FileCategory.VIDEOS,
            FileCategory.DOCUMENTS,
            FileCategory.ARCHIVES,
            FileCategory.AUDIO,
            FileCategory.OTHER
        ]
        
        # Group files by category in order
        for category in category_order:
            category_files = [f for f in files if f['category'] == category]
            if not category_files:
                continue
            
            # Create group
            group = FileCategoryGroup(category)
            self.category_groups[category] = group
            self.container_layout.insertWidget(self.container_layout.count() - 1, group)
            self._apply_group_collapse(category, group)
            
            # Add files to group
            for file_data in category_files:
                file_item = FileListItem(file_data, jdownloader_enabled=self._jdownloader_enabled)
                file_item.selection_changed.connect(self._update_selection_count)
                file_item.download_clicked.connect(lambda fd=file_data: self._download_single(fd))
                file_item.jdownloader_clicked.connect(lambda fd=file_data: self._jdownloader_single(fd))
                group.add_file(file_item)
    
    def _on_sort_changed(self, sort_by: str):
        """Handle sort change."""
        # Map string to enum
        sort_map = {s.value: s for s in SortBy}
        self._set_sort(sort_map.get(sort_by, SortBy.NAME))
    
    def _on_group_changed(self, group_by: str):
        """Handle group by change."""
        # Map string to enum
        group_map = {g.value: g for g in GroupBy}
        self._set_group(group_map.get(group_by, GroupBy.CATEGORY))

    def _set_sort(self, sort_by: SortBy):
        """Set sort column, toggling direction when re-selecting the same column."""
        if self._current_sort == sort_by:
            self._sort_ascending = not self._sort_ascending
        else:
            self._current_sort = sort_by
            self._sort_ascending = True
        self._update_sort_headers()
        self._rebuild_groups()

    def _set_group(self, group_by: GroupBy):
        """Set current group mode and rebuild."""
        self._current_group = group_by
        if hasattr(self, "group_menu_btn"):
            self.group_menu_btn.setText(f"Group: {group_by.value}")
        self._rebuild_groups()

    def _update_sort_headers(self):
        """Update column header labels with sort direction."""
        for sort_by, button in self._sort_buttons.items():
            label = sort_by.value
            if sort_by == self._current_sort:
                label = f"{label} {'^' if self._sort_ascending else 'v'}"
            button.setText(label)
    
    def _download_single(self, file_data: dict):
        """Download single file."""
        url = file_data.get('url')
        if url:
            self.download_requested.emit([url])
    
    def _download_selected(self):
        """Download selected files."""
        urls = []
        for group in self.category_groups.values():
            selected = group.get_selected_files()
            for file_data in selected:
                url = file_data.get('url')
                if url:
                    urls.append(url)
        
        if urls:
            self.download_requested.emit(urls)
    
    def _jdownloader_single(self, file_data: dict):
        """Send single file to JDownloader."""
        url = file_data.get('url')
        if url:
            self.jdownloader_requested.emit([url])
    
    def _jdownloader_selected(self):
        """Send selected files to JDownloader."""
        urls = []
        for group in self.category_groups.values():
            selected = group.get_selected_files()
            for file_data in selected:
                url = file_data.get('url')
                if url:
                    urls.append(url)
        
        if urls:
            self.jdownloader_requested.emit(urls)
    
    def show_sidebar(self):
        """Show sidebar instantly."""
        self.setVisible(True)
        if not self._embedded:
            self.setFixedWidth(self._current_width)
    
    def hide_sidebar(self):
        """Hide sidebar instantly."""
        # Save current width for next show
        if not self._embedded:
            self._current_width = max(FileSidebar.WIDTH_MIN, min(FileSidebar.WIDTH_MAX, self.width()))
        self.setVisible(False)
        self.closed.emit()
