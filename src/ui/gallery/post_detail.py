"""
Post detail view for displaying a single post with all files.

Extracted from native_widgets.py to reduce file size and improve maintainability.
Uses theme.py for dynamic styling and dark_theme_pro.qss for static widget styles.
"""
from pathlib import Path
from typing import List
import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QGridLayout, QFrame
)
import qtawesome as qta

from src.core.dto.post import PostDTO
from src.core.dto.file import FileDTO
from src.ui.common.view_models import MediaItem
from src.ui.images.image_utils import scale_and_crop_pixmap
from src.ui.gallery.post_card import (
    POST_TITLE_MAX_CHARS,
    _build_media_url,
    _broken_media_icon_pixmap,
    _get_qta_icon,
    _last_suffix,
)
from src.ui.common.utils import truncate_text
from src.ui.common.theme import Colors, Fonts, Spacing, Styles

logger = logging.getLogger(__name__)


class PostDetailView(QWidget):
    """
    Detailed view of a single post with all files.

    Displays post title, meta info, content text, and a grid of file thumbnails.
    Supports downloading all files and opening gallery view.

    Signals:
        download_clicked(urls): Emitted when download is requested
        back_clicked(): Emitted when back button is clicked
        gallery_requested(media_items, index): Emitted to open gallery view
    """

    download_clicked = pyqtSignal(list)  # Emits list of file URLs
    back_clicked = pyqtSignal()
    gallery_requested = pyqtSignal(list, int)  # Emits (media_items, initial_index)

    def __init__(self, parent=None):
        """Initialize detail view."""
        super().__init__(parent)

        self.post_data = None
        self.platform = 'coomer'
        self.current_files = []
        self._setup_ui()

    def _setup_ui(self):
        """Setup detail view UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header with back button (styled by QSS via objectName)
        header = QWidget()
        header.setObjectName("detailHeader")
        header.setFixedHeight(Spacing.HEADER_HEIGHT)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(Spacing.XL, 0, Spacing.XL, 0)

        back_btn = QPushButton("Back")
        back_btn.setObjectName("backButton")
        back_btn.setIcon(qta.icon('fa5s.arrow-left', color=Colors.TEXT_PRIMARY))
        back_btn.clicked.connect(self.back_clicked.emit)
        header_layout.addWidget(back_btn)

        header_layout.addStretch()

        self.download_all_btn = QPushButton()
        self.download_all_btn.setObjectName("downloadButton")
        header_layout.addWidget(self.download_all_btn)

        layout.addWidget(header)

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(Spacing.XXXL, Spacing.XXXL, Spacing.XXXL, Spacing.XXXL)
        self.content_layout.setSpacing(Spacing.XXL)

        scroll.setWidget(content)
        layout.addWidget(scroll)

    def set_post(self, post_data: PostDTO, platform: str = 'coomer'):
        """
        Display post details.

        Args:
            post_data: PostDTO to display
            platform: Platform identifier (coomer/kemono)
        """
        self.post_data = post_data
        self.platform = platform

        # Batch updates to prevent white flashes
        self.setUpdatesEnabled(False)
        try:
            # Clear existing content
            while self.content_layout.count():
                item = self.content_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            # Title (dynamic - use theme.py)
            title = post_data.title or 'Untitled'
            title = truncate_text(title, POST_TITLE_MAX_CHARS)
            title_label = QLabel(title)
            title_label.setObjectName("detailTitleLabel")
            title_label.setWordWrap(True)
            self.content_layout.addWidget(title_label)

            # Meta info (dynamic - use theme.py)
            creator = post_data.user_id or 'Unknown'
            published = post_data.published or ''
            service = post_data.service or 'unknown'

            meta_label = QLabel(f"{creator} • {service} • {published}")
            meta_label.setObjectName("postInfoLabel")
            self.content_layout.addWidget(meta_label)

            # Content text (dynamic - use theme.py for colors)
            content_text = post_data.content or ''
            if content_text:
                content_label = QLabel(content_text)
                content_label.setWordWrap(True)
                content_label.setStyleSheet(Styles.label(
                    color=Colors.TEXT_PRIMARY,
                    size=Fonts.SIZE_LG,
                    padding=Spacing.MD,
                    bg=Colors.BG_TERTIARY
                ))
                self.content_layout.addWidget(content_label)

            # Files grid
            files = self._get_post_files(post_data)
            if files:
                files_label = QLabel(f"{len(files)} Files")
                files_label.setStyleSheet(Styles.label(
                    color=Colors.TEXT_PRIMARY,
                    size=Fonts.SIZE_XXL,
                    weight=Fonts.WEIGHT_MEDIUM
                ))
                self.content_layout.addWidget(files_label)

                # Update download button
                self.download_all_btn.setText(f"Download All ({len(files)} files)")
                self.download_all_btn.setIcon(qta.icon('fa5s.download', color=Colors.TEXT_WHITE))
                self.download_all_btn.clicked.connect(
                    lambda: self.download_clicked.emit([f.url for f in files if f.url])
                )

                # File grid
                file_grid = self._create_file_grid(files)
                self.content_layout.addWidget(file_grid)

            self.content_layout.addStretch()
        finally:
            self.setUpdatesEnabled(True)

    def _file_to_media_item(self, file_dto: FileDTO) -> MediaItem | None:
        """Convert a FileDTO to a MediaItem."""
        if not file_dto or not file_dto.path:
            return None
        file_url = _build_media_url(self.platform, file_dto.path)
        name = file_dto.name or Path(file_dto.path).name
        if file_dto.is_video:
            media_type = "video"
        elif file_dto.is_image:
            media_type = "image"
        else:
            media_type = "file"
        return MediaItem(
            name=name,
            url=file_url,
            media_type=media_type,
            is_downloadable=True,
            duration=file_dto.duration if file_dto.is_video else None,
        )

    def _get_post_files(self, post_data: PostDTO) -> List[MediaItem]:
        """Extract files from post data."""
        files: List[MediaItem] = []

        if post_data.file:
            item = self._file_to_media_item(post_data.file)
            if item:
                files.append(item)

        for attachment in post_data.attachments:
            item = self._file_to_media_item(attachment)
            if item:
                files.append(item)

        return files

    def _create_file_grid(self, files: List[MediaItem]) -> QWidget:
        """Create grid of file thumbnails."""
        # Store files for gallery viewer
        self.current_files = files

        widget = QWidget()
        grid = QGridLayout(widget)
        grid.setSpacing(Spacing.LG)

        row = 0
        col = 0
        max_cols = 4

        for index, file_info in enumerate(files):
            file_card = self._create_file_card(file_info, index)
            grid.addWidget(file_card, row, col)

            col += 1
            if col >= max_cols:
                col = 0
                row += 1

        return widget

    def _create_file_card(self, file_info: MediaItem, index: int) -> QWidget:
        """
        Create individual file card.

        Args:
            file_info: File information
            index: Index in files list (for gallery navigation)
        """
        card = QFrame()
        card.setObjectName("thumbnailCard")
        card.setFixedSize(200, 230)
        card.setCursor(Qt.CursorShape.PointingHandCursor)

        # Make card clickable to open gallery
        def open_gallery():
            self.gallery_requested.emit(self.current_files, index)

        card.mousePressEvent = lambda event: open_gallery() if event.button() == Qt.MouseButton.LeftButton else None

        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Thumbnail placeholder
        thumb = QLabel()
        thumb.setObjectName("thumbnailImage")
        thumb.setFixedSize(198, 180)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Construct file URL
        file_url = file_info.url
        if not file_url:
            loading_icon = qta.icon('fa5s.file', color=Colors.TEXT_SECONDARY)
            thumb.setPixmap(loading_icon.pixmap(32, 32))
            layout.addWidget(thumb)
            return card

        logger.debug(f"Loading file thumbnail: {file_url}")

        from src.ui.images.image_loader_manager import get_image_loader_manager
        loader = get_image_loader_manager().grid_loader()

        # Extract file hash for comparison
        file_hash = file_url.split('/')[-1] if file_url else ''

        def on_thumb_loaded(url, pixmap):
            if file_hash and file_hash in url:
                try:
                    thumb.setPixmap(scale_and_crop_pixmap(pixmap, (198, 180)))
                except RuntimeError:
                    pass

        def on_thumb_failed(url, error):
            if file_hash and file_hash in url:
                try:
                    thumb.setPixmap(_broken_media_icon_pixmap())
                except RuntimeError:
                    pass

        loader.image_loaded.connect(on_thumb_loaded)
        loader.load_failed.connect(on_thumb_failed)

        def _cleanup_thumb():
            try:
                loader.image_loaded.disconnect(on_thumb_loaded)
            except Exception:
                pass
            try:
                loader.load_failed.disconnect(on_thumb_failed)
            except Exception:
                pass

        thumb.destroyed.connect(_cleanup_thumb)
        loader.load_image(file_url, target_size=(198, 180))

        # Placeholder while loading
        loading_icon = qta.icon('fa5s.spinner', color=Colors.TEXT_SECONDARY)
        thumb.setPixmap(loading_icon.pixmap(32, 32))

        layout.addWidget(thumb)

        # Filename
        filename = file_info.name
        if len(filename) > 25:
            display_name = filename[:22] + "..."
        else:
            display_name = filename

        name_label = QLabel(display_name)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setWordWrap(True)
        name_label.setStyleSheet(Styles.label(
            color=Colors.TEXT_PRIMARY,
            size=Fonts.SIZE_SM,
            padding=Spacing.SM
        ))
        name_label.setToolTip(filename)
        layout.addWidget(name_label)

        # File type indicator
        file_type = self._get_file_type(filename)
        type_icons = {
            'image': 'fa.image',
            'video': 'fa.video',
            'audio': 'fa.music',
            'archive': 'fa.archive',
            'file': 'fa.file'
        }

        type_label = QLabel()
        icon = _get_qta_icon(type_icons.get(file_type, 'fa.file'))
        if icon:
            try:
                type_label.setPixmap(icon.pixmap(Spacing.ICON_MD, Spacing.ICON_MD))
            except Exception:
                type_label.setText(file_type[:1].upper())
        else:
            type_label.setText(file_type[:3].upper())
        type_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        type_label.setStyleSheet(Styles.label(
            color=Colors.TEXT_SECONDARY,
            size=Fonts.SIZE_SM,
            padding=Spacing.XS
        ))
        layout.addWidget(type_label)

        return card

    def _get_file_type(self, filename: str) -> str:
        """Determine file type from filename."""
        ext = _last_suffix(filename)

        if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']:
            return 'image'
        elif ext in ['.mp4', '.webm', '.mov', '.avi', '.mkv', '.flv']:
            return 'video'
        elif ext in ['.mp3', '.wav', '.ogg', '.flac', '.m4a']:
            return 'audio'
        elif ext in ['.zip', '.rar', '.7z', '.tar', '.gz']:
            return 'archive'
        else:
            return 'file'
