"""
Compact creators sidebar with list view
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
                             QPushButton, QLineEdit, QComboBox, QScrollArea,
                             QFrame, QSizePolicy)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread
from PyQt6.QtGui import QPixmap, QColor, QIcon
from typing import List, Dict, Optional
import logging
import qtawesome as qta
from src.ui.images.image_utils import scale_and_crop_pixmap, scale_pixmap_to_fill, create_circular_pixmap
from src.ui.common.enhanced_pagination import CompactPagination
from src.ui.widgets.rounded_effect import RoundedCornerGraphicsEffect
from src.ui.images.async_image_widgets import AsyncImageLabel
from src.core.media_manager import MediaManager
from src.ui.creators.creator_utils import create_creator_data_dict
from src.ui.common.theme import Colors, Fonts, Spacing, Styles
from src.utils.file_utils import get_resource_path

logger = logging.getLogger(__name__)

# Supported services by platform - services not in this list cannot be browsed for posts
SUPPORTED_SERVICES = {
    'kemono': {'patreon', 'fanbox', 'fantia', 'boosty', 'gumroad', 'subscribestar', 'dlsite'},
    'coomer': {'onlyfans', 'fansly', 'candfans'},
}

def is_service_supported(platform: str, service: str) -> bool:
    """Check if a service is supported for browsing posts on the given platform."""
    supported = SUPPORTED_SERVICES.get(platform, set())
    return service.lower() in supported


CREATOR_ROW_MARGINS = (8, 6, 8, 6)


def _apply_creator_row_height(widget: QFrame) -> None:
    layout = widget.layout()
    if layout is None:
        return
    layout.activate()
    content_height = layout.sizeHint().height()
    widget.setMinimumHeight(max(Spacing.CREATOR_ROW_HEIGHT, content_height))


class CreatorListItem(QFrame):
    """
    Compact creator list item
    """

    clicked = pyqtSignal(dict)  # Emits creator data

    def __init__(
        self,
        creator_data: Dict,
        platform: str = 'coomer',
        *,
        media_manager: Optional[MediaManager] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.creator_data = creator_data
        self.platform = platform
        self._media = media_manager
        self.selected = False
        self._setup_ui()

    def _setup_ui(self):
        """Setup UI"""
        self.setObjectName("creatorListItem")
        self.setMinimumHeight(Spacing.CREATOR_ROW_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        # Apply base QSS with sizing baked in
        self.setStyleSheet(Styles.creator_list_item(selected=False))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(*CREATOR_ROW_MARGINS)
        layout.setSpacing(8)

        # Avatar (small circle) - AsyncImageLabel for automatic loading
        self.avatar_label = AsyncImageLabel()
        self.avatar_label.setGraphicsEffect(RoundedCornerGraphicsEffect(Spacing.AVATAR_MD // 2, self.avatar_label))
        self.avatar_label.setFixedSize(Spacing.AVATAR_MD, Spacing.AVATAR_MD)
        self.avatar_label.setMinimumSize(Spacing.AVATAR_MD, Spacing.AVATAR_MD)
        self.avatar_label.setMaximumSize(Spacing.AVATAR_MD, Spacing.AVATAR_MD)
        self.avatar_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.avatar_label.setScaledContents(False)
        self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_label.setStyleSheet(f"""
            QLabel {{
                background-color: {Colors.BG_TERTIARY};
                border-radius: {Spacing.AVATAR_MD // 2}px;
                max-width: {Spacing.AVATAR_MD}px;
                max-height: {Spacing.AVATAR_MD}px;
            }}
        """)

        # Load avatar
        self._load_avatar()
        layout.addWidget(self.avatar_label)

        # Name and service info
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)

        # Creator name
        name = self.creator_data.get('name', 'Unknown')
        name_label = QLabel(name)
        name_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                font-size: {Fonts.SIZE_MD}px;
                font-weight: {Fonts.WEIGHT_MEDIUM};
                background: transparent;
                border: none;
            }}
        """)
        name_label.setWordWrap(False)
        name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Truncate long names
        if len(name) > 20:
            name_label.setText(name[:20] + "...")
            name_label.setToolTip(name)
        info_layout.addWidget(name_label)

        # Service tag
        service = self.creator_data.get('service', 'unknown')
        service_label = QLabel(service)
        service_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_MUTED};
                font-size: {Fonts.SIZE_XS}px;
                background: transparent;
                border: none;
            }}
        """)
        info_layout.addWidget(service_label)

        layout.addLayout(info_layout)
        layout.addStretch()

        # Favorited count (if > 0)
        fav_count = self.creator_data.get('favorited_count', 0)
        if fav_count and fav_count > 0:
            fav_btn = QPushButton(str(fav_count))
            fav_btn.setObjectName("favoritedCountButton")
            fav_btn.setIcon(qta.icon('fa5s.heart', color="#ff6969"))
            fav_btn.setFlat(True)
            fav_btn.setStyleSheet(f"""
                QPushButton#favoritedCountButton {{
                    color: {Colors.ACCENT_FAVORITE};
                    font-size: {Fonts.SIZE_XS}px;
                    background: transparent;
                    border: none;
                    padding: 0px;
                }}
            """)
            fav_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            fav_btn.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # Make non-clickable
            layout.addWidget(fav_btn)

        _apply_creator_row_height(self)

    def _load_avatar(self):
        """Load creator avatar"""
        # Set placeholder first
        placeholder = QPixmap(Spacing.AVATAR_MD, Spacing.AVATAR_MD)
        placeholder.fill(Qt.GlobalColor.transparent)
        self.avatar_label.setPixmap(placeholder)

        # Try to load actual avatar
        try:
            service = self.creator_data.get('service', 'unknown')
            creator_id = self.creator_data.get('creator_id') or self.creator_data.get('id', '')

            if creator_id:
                icon_url = (
                    self._media.build_creator_icon_url(self.platform, service, creator_id)
                    if self._media
                    else MediaManager.build_creator_icon_url(self.platform, service, creator_id)
                )

                def _on_loaded(_, pixmap):
                    if not pixmap.isNull():
                        # Circular crop using centralized utility
                        scaled = scale_and_crop_pixmap(pixmap, (Spacing.AVATAR_MD, Spacing.AVATAR_MD))
                        self.avatar_label.setPixmap(scaled)

                # Load using AsyncImageLabel (automatic cleanup)
                self.avatar_label.load_image(
                    url=icon_url,
                    target_size=(Spacing.AVATAR_MD, Spacing.AVATAR_MD),
                    on_loaded=_on_loaded
                )
        except Exception:
            pass

    def mousePressEvent(self, event):
        """Handle click"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.creator_data)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool):
        """Highlight as selected"""
        self.selected = selected
        if selected:
            self.setStyleSheet(Styles.creator_list_item(selected=True))
        else:
            self.setStyleSheet(Styles.creator_list_item(selected=False))


class _CreatorDetailWorker(QThread):
    loaded = pyqtSignal(object)

    def __init__(self, manager, platform: str, service: str, creator_id: str, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._platform = platform
        self._service = service
        self._creator_id = creator_id

    def run(self) -> None:
        result = None
        try:
            result = self._manager.refresh_creator_post_count(
                self._platform,
                self._service,
                self._creator_id,
            )
        except Exception:
            result = None
        self.loaded.emit(result)


class RecommendedCreatorItem(QFrame):
    """
    Compact recommended creator item with similarity score
    """

    clicked = pyqtSignal(dict)  # Emits creator data

    def __init__(
        self,
        creator_data: Dict,
        similarity_score: float,
        platform: str = 'coomer',
        *,
        media_manager: Optional[MediaManager] = None,
        parent=None,
        show_score: bool = True,
    ):
        super().__init__(parent)
        self.creator_data = creator_data
        self.similarity_score = similarity_score
        self.platform = platform
        self._media = media_manager
        self.show_score = show_score
        self._setup_ui()

    def _setup_ui(self):
        """Setup UI"""
        self.setObjectName("recommendedCreatorItem")
        self.setMinimumHeight(Spacing.CREATOR_ROW_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.setStyleSheet(Styles.creator_list_item(selected=False))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(*CREATOR_ROW_MARGINS)
        layout.setSpacing(8)

        # Avatar (smaller than regular)
        self.avatar_label = AsyncImageLabel()
        self.avatar_label.setGraphicsEffect(RoundedCornerGraphicsEffect(Spacing.AVATAR_SM // 2, self.avatar_label))
        self.avatar_label.setFixedSize(Spacing.AVATAR_SM, Spacing.AVATAR_SM)
        self.avatar_label.setMinimumSize(Spacing.AVATAR_SM, Spacing.AVATAR_SM)
        self.avatar_label.setMaximumSize(Spacing.AVATAR_SM, Spacing.AVATAR_SM)
        self.avatar_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.avatar_label.setScaledContents(False)
        self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_label.setStyleSheet(f"""
            QLabel {{
                background-color: {Colors.BG_TERTIARY};
                border-radius: {Spacing.AVATAR_SM // 2}px;
                max-width: {Spacing.AVATAR_SM}px;
                max-height: {Spacing.AVATAR_SM}px;
            }}
        """)

        # Load avatar
        self._load_avatar()
        layout.addWidget(self.avatar_label)

        # Name + service info
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)

        name = self.creator_data.get('name', 'Unknown')
        name_label = QLabel(name)
        name_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                font-size: {Fonts.SIZE_MD}px;
                font-weight: {Fonts.WEIGHT_MEDIUM};
                background: transparent;
                border: none;
            }}
        """)
        name_label.setWordWrap(False)
        name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        if len(name) > 20:
            name_label.setText(name[:20] + "...")
            name_label.setToolTip(name)
        info_layout.addWidget(name_label)

        service = self.creator_data.get('service', 'unknown')
        service_label = QLabel(service)
        service_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_MUTED};
                font-size: {Fonts.SIZE_XS}px;
                background: transparent;
                border: none;
            }}
        """)
        info_layout.addWidget(service_label)

        layout.addLayout(info_layout, 1)

        # Similarity score badge (optional)
        if self.show_score:
            score_pct = int(self.similarity_score * 100)
            score_label = QLabel(f"{score_pct}%")
            score_label.setStyleSheet(f"""
                QLabel {{
                    color: {Colors.ACCENT_PRIMARY};
                    font-size: {Fonts.SIZE_XS}px;
                    font-weight: {Fonts.WEIGHT_BOLD};
                    background: transparent;
                    border: none;
                    padding-right: 2px;
                }}
            """)
            layout.addWidget(score_label)

        _apply_creator_row_height(self)

    def _load_avatar(self):
        """Load creator avatar"""
        service = self.creator_data.get('service', '')
        user_id = self.creator_data.get('creator_id') or self.creator_data.get('id', '')
        
        if self._media and service and user_id:
            icon_url = (
                self._media.build_creator_icon_url(self.platform, service, user_id)
                if self._media
                else MediaManager.build_creator_icon_url(self.platform, service, user_id)
            )
            
            self.avatar_label.load_image(
                url=icon_url,
                target_size=(Spacing.AVATAR_SM, Spacing.AVATAR_SM),
                on_loaded=lambda _, pixmap: (
                    self.avatar_label.setPixmap(scale_and_crop_pixmap(pixmap, (Spacing.AVATAR_SM, Spacing.AVATAR_SM)))
                    if not pixmap.isNull() else None
                )
            )

    def mousePressEvent(self, event):
        """Handle mouse press"""
        self.clicked.emit(self.creator_data)
        super().mousePressEvent(event)


class CreatorsSidebar(QWidget):
    """
    Compact creators sidebar with list view
    """

    HEADER_HEIGHT = 60
    TABS_HEIGHT = 41
    TAB_BUTTON_HEIGHT = 40
    DETAIL_BANNER_HEIGHT = 72
    DETAIL_AVATAR_SIZE = 56
    DETAIL_AVATAR_OVERLAP = 0

    creator_selected = pyqtSignal(dict)  # Creator selected (filter posts)
    creator_cleared = pyqtSignal()  # Clear creator selection
    service_changed = pyqtSignal(str)  # Service filter changed
    unsupported_service = pyqtSignal(str, str)  # (service, creator_name) - service not supported

    def __init__(
        self,
        creators_manager,
        platform: str = 'coomer',
        *,
        media_manager: Optional[MediaManager] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("creatorsSidebar")
        self.creators_manager = creators_manager
        self.platform = platform
        self._media = media_manager
        self.service = 'all'
        self.current_page = 0
        self.page_size = 50  # More items per page for list view
        self.total_count = 0
        self.creator_sort_by = 'favorited'
        self.creator_sort_dir = 'DESC'
        self.creator_items = []
        self.selected_creator = None
        self.recommended_creators = []  # List of (creator, score) tuples
        self.recommended_items = []  # List of UI widgets
        self.linked_items = []  # List of UI widgets for linked tab
        self._creator_items_height = 0
        self.active_tab = 'search'  # 'search', 'recommended', or 'linked'
        self._loading_chunks = False  # Guard to prevent concurrent chunk loading
        self._pending_chunk_timers = []  # Track pending QTimer callbacks
        self._session_load_id = 0  # Track current load session, incremented on each new load
        self._detail_worker = None
        self._detail_refresh_token = 0
        self._detail_refresh_inflight = None
        self._suppress_detail_refresh = False

        self.setMinimumWidth(0)
        self.setMaximumWidth(Spacing.SIDEBAR_WIDTH)
        self._setup_ui()

    def _setup_ui(self):
        """Setup UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        self.header_widget = self._create_header()
        layout.addWidget(self.header_widget)

        # Selected creator detail (banner + avatar + stats)
        self.creator_detail = self._create_creator_detail()
        self.creator_detail.setVisible(False)
        layout.addWidget(self.creator_detail)

        # Tab buttons
        self.tabs_widget = self._create_tabs()
        layout.addWidget(self.tabs_widget)

        # Filters
        filters = self._create_filters()
        layout.addWidget(filters)
        self.filters_widget = filters

        # Creators list (for Search tab)
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("creatorsList")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            f"QScrollArea {{ border: none; background-color: {Colors.BG_SECONDARY}; }}"
        )

        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)
        self.list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.scroll_area.setWidget(self.list_container)
        layout.addWidget(self.scroll_area)

        # Recommended creators list (for Recommended tab)
        self.recommended_scroll_area = QScrollArea()
        self.recommended_scroll_area.setObjectName("recommendedCreatorsList")
        self.recommended_scroll_area.setWidgetResizable(True)
        self.recommended_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recommended_scroll_area.setStyleSheet(f"QScrollArea {{ border: none; background-color: {Colors.BG_SECONDARY}; }}")

        self.recommended_list_container = QWidget()
        self.recommended_list_layout = QVBoxLayout(self.recommended_list_container)
        self.recommended_list_layout.setContentsMargins(0, 0, 0, 0)
        self.recommended_list_layout.setSpacing(0)
        self.recommended_list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.recommended_scroll_area.setWidget(self.recommended_list_container)
        self.recommended_scroll_area.setVisible(False)
        layout.addWidget(self.recommended_scroll_area)

        # Linked creators list (for Linked tab)
        self.linked_scroll_area = QScrollArea()
        self.linked_scroll_area.setObjectName("linkedCreatorsList")
        self.linked_scroll_area.setWidgetResizable(True)
        self.linked_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.linked_scroll_area.setStyleSheet(f"QScrollArea {{ border: none; background-color: {Colors.BG_SECONDARY}; }}")

        self.linked_list_container = QWidget()
        self.linked_list_layout = QVBoxLayout(self.linked_list_container)
        self.linked_list_layout.setContentsMargins(0, 0, 0, 0)
        self.linked_list_layout.setSpacing(0)
        self.linked_list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.linked_scroll_area.setWidget(self.linked_list_container)
        self.linked_scroll_area.setVisible(False)
        layout.addWidget(self.linked_scroll_area)

        # Status label - expands to fill space when scroll areas are hidden
        self.status_label = QLabel("Loading creators...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.status_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_MUTED};
                font-size: {Fonts.SIZE_SM}px;
                padding: 20px;
                background-color: {Colors.BG_SECONDARY};
            }}
        """)
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)

        # Pagination
        self.pagination_container = QWidget()
        self.pagination_container.setFixedHeight(Spacing.PAGINATION_HEIGHT)
        self.pagination_container.setStyleSheet(Styles.pagination_container())
        pagination_layout = QHBoxLayout(self.pagination_container)
        pagination_layout.setContentsMargins(8, 6, 8, 6)

        self.pagination = CompactPagination()
        self.pagination.page_changed.connect(self._on_page_changed)
        pagination_layout.addWidget(self.pagination)

        # Result count
        self.result_label = QLabel("0\ncreators")
        self.result_label.setObjectName("creatorsCountLabel")
        self.result_label.setFixedWidth(90)
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TEXT_SECONDARY};
                font-size: {Fonts.SIZE_XS}px;
                padding: 0px;
            }}
        """)
        pagination_layout.addWidget(self.result_label)

        layout.addWidget(self.pagination_container)

    def _create_header(self) -> QWidget:
        """Create header with title and collapse button"""
        header = QWidget()
        header.setObjectName("creatorsHeader")
        header.setFixedHeight(self.HEADER_HEIGHT)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(12, 8, 12, 8)

        # Title
        title = QLabel("Creators")
        title.setObjectName("creatorsTitleLabel")
        layout.addWidget(title)

        layout.addStretch()

        # Clear selection button
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("creatorsClearButton")
        self.clear_btn.setFixedSize(70, 28)
        self.clear_btn.clicked.connect(self._on_clear_selection)
        self.clear_btn.setVisible(False)
        self.clear_btn.setToolTip("Reset all filters and selection")
        layout.addWidget(self.clear_btn)

        # Refresh button
        self.random_btn = QPushButton()
        self.random_btn.setIcon(qta.icon('fa5s.dice'))
        self.random_btn.setFixedSize(32, 28)
        self.random_btn.clicked.connect(self._on_random_creator)
        self.random_btn.setToolTip("Random creator")
        layout.addWidget(self.random_btn)

        # Refresh button
        refresh_btn = QPushButton()
        refresh_btn.setIcon(qta.icon('fa5s.sync-alt'))
        refresh_btn.setFixedSize(32, 28)
        refresh_btn.clicked.connect(self.refresh)
        refresh_btn.setToolTip("Refresh creators list")
        layout.addWidget(refresh_btn)

        return header

    def _create_tabs(self) -> QWidget:
        """Create tab buttons"""
        container = QWidget()
        container.setStyleSheet(f"background-color: {Colors.BG_HOVER}; border-bottom: 1px solid {Colors.BG_PRIMARY};")
        container.setFixedHeight(self.TABS_HEIGHT)

        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Search tab
        self.search_tab_btn = QPushButton("Search")
        self.search_tab_btn.setObjectName("tabButton")
        self.search_tab_btn.setFixedHeight(self.TAB_BUTTON_HEIGHT)
        self.search_tab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_tab_btn.clicked.connect(lambda: self._switch_tab('search'))
        layout.addWidget(self.search_tab_btn, 1)

        # Recommended tab
        self.recommended_tab_btn = QPushButton("Recommended")
        self.recommended_tab_btn.setObjectName("tabButton")
        self.recommended_tab_btn.setFixedHeight(self.TAB_BUTTON_HEIGHT)
        self.recommended_tab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.recommended_tab_btn.clicked.connect(lambda: self._switch_tab('recommended'))
        layout.addWidget(self.recommended_tab_btn, 1)

        # Linked tab
        self.linked_tab_btn = QPushButton("Linked")
        self.linked_tab_btn.setObjectName("tabButton")
        self.linked_tab_btn.setFixedHeight(self.TAB_BUTTON_HEIGHT)
        self.linked_tab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.linked_tab_btn.clicked.connect(lambda: self._switch_tab('linked'))
        layout.addWidget(self.linked_tab_btn, 1)

        # Style tabs
        self._update_tab_styles()

        return container

    def get_header_height(self) -> int:
        header = getattr(self, "header_widget", None)
        if header is None:
            return self.HEADER_HEIGHT
        header_h = header.height() if header.height() > 0 else header.sizeHint().height()
        if header_h <= 0:
            header_h = self.HEADER_HEIGHT
        return header_h

    def _create_creator_detail(self) -> QWidget:
        container = QWidget()
        container.setObjectName("creatorDetailPanel")
        container.setStyleSheet(
            f"QWidget#creatorDetailPanel {{"
            f"  background-color: {Colors.BG_SECONDARY};"
            f"  border-bottom: 1px solid {Colors.BG_PRIMARY};"
            f"}}"
        )

        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        banner_container = QWidget()
        banner_container.setObjectName("creatorDetailBannerContainer")
        banner_layout = QGridLayout(banner_container)
        banner_layout.setContentsMargins(0, 0, 0, 0)
        banner_layout.setSpacing(0)

        self.creator_banner = AsyncImageLabel()
        self.creator_banner.setObjectName("creatorDetailBanner")
        self.creator_banner.setFixedHeight(self.DETAIL_BANNER_HEIGHT)
        self.creator_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.creator_banner.setStyleSheet(f"background-color: {Colors.BG_TERTIARY};")
        banner_layout.addWidget(self.creator_banner, 0, 0)

        self.creator_service_icon = QLabel()
        self.creator_service_icon.setObjectName("creatorDetailServiceIcon")
        self.creator_service_icon.setFixedSize(34, 34)
        self.creator_service_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.creator_service_icon.setStyleSheet(
            f"background-color: rgba(0, 0, 0, 0.6);"
            f"border-radius: 15px;"
            f"margin-top: 4px;"
            f"margin-right: 4px;"
        )
        banner_layout.addWidget(
            self.creator_service_icon,
            0,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
        )
        layout.addWidget(banner_container)

        info = QWidget()
        info.setObjectName("creatorDetailInfo")
        info_layout = QHBoxLayout(info)
        info_layout.setContentsMargins(12, 10, 12, 10)
        info_layout.setSpacing(10)

        self.creator_avatar = AsyncImageLabel()
        self.creator_avatar.setFixedSize(self.DETAIL_AVATAR_SIZE, self.DETAIL_AVATAR_SIZE)
        self.creator_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.creator_avatar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.creator_avatar.setStyleSheet(
            f"background-color: {Colors.BG_TERTIARY};"
            f"border-radius: {self.DETAIL_AVATAR_SIZE // 2}px;"
        )
        info_layout.addWidget(self.creator_avatar, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)

        self.creator_name_label = QLabel("Creator")
        self.creator_name_label.setObjectName("creatorDetailName")
        self.creator_name_label.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY};"
            f"font-size: {Fonts.SIZE_MD}px;"
            f"font-weight: {Fonts.WEIGHT_SEMIBOLD};"
        )
        text_layout.addWidget(self.creator_name_label)

        stats_row = QHBoxLayout()
        stats_row.setContentsMargins(0, 0, 0, 0)
        stats_row.setSpacing(16)

        self.creator_fav_label = QLabel("0 Favourites")
        self.creator_fav_label.setObjectName("creatorDetailStat")
        self.creator_fav_label.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY};"
            f"font-size: {Fonts.SIZE_XS}px;"
            f"font-weight: {Fonts.WEIGHT_MEDIUM};"
        )
        stats_row.addWidget(self.creator_fav_label)

        self.creator_posts_label = QLabel("0 Posts")
        self.creator_posts_label.setObjectName("creatorDetailStat")
        self.creator_posts_label.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY};"
            f"font-size: {Fonts.SIZE_XS}px;"
            f"font-weight: {Fonts.WEIGHT_MEDIUM};"
        )
        stats_row.addWidget(self.creator_posts_label)
        stats_row.addStretch(1)
        text_layout.addLayout(stats_row)

        self.creator_indexed_label = QLabel("Date Indexed: —")
        self.creator_indexed_label.setObjectName("creatorDetailMeta")
        self.creator_indexed_label.setStyleSheet(
            f"color: {Colors.TEXT_MUTED};"
            f"font-size: {Fonts.SIZE_XS}px;"
        )
        text_layout.addWidget(self.creator_indexed_label)

        self.creator_updated_label = QLabel("Date Updated: —")
        self.creator_updated_label.setObjectName("creatorDetailMeta")
        self.creator_updated_label.setStyleSheet(
            f"color: {Colors.TEXT_MUTED};"
            f"font-size: {Fonts.SIZE_XS}px;"
        )
        text_layout.addWidget(self.creator_updated_label)

        info_layout.addLayout(text_layout, 1)
        layout.addWidget(info)

        return container

    def _format_creator_count(self, value: Optional[int]) -> str:
        try:
            if value is None:
                return "0"
            return f"{int(value):,}"
        except Exception:
            return "0"

    def _format_creator_date(self, value: Optional[object]) -> str:
        if not value:
            return "—"
        try:
            from datetime import datetime
            if isinstance(value, (int, float)):
                ts = float(value)
                if ts > 10**12:
                    ts = ts / 1000.0
                return datetime.fromtimestamp(ts).strftime("%d/%m/%Y")
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned.isdigit():
                    ts = int(cleaned)
                    if ts > 10**12:
                        ts = ts // 1000
                    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y")
                date_str = cleaned.split("T")[0].strip()
                try:
                    return datetime.fromisoformat(date_str).strftime("%d/%m/%Y")
                except Exception:
                    return date_str
            return str(value)
        except Exception:
            return "—"

    def _update_creator_detail(self, creator_data: Dict) -> None:
        if not creator_data:
            self._clear_creator_detail()
            return

        name = creator_data.get("name") or creator_data.get("creator_id") or "Creator"
        self.creator_name_label.setText(name)

        fav = creator_data.get("favorited_count")
        if fav is None:
            fav = creator_data.get("favorited")
        posts = creator_data.get("post_count")

        service = (creator_data.get("service") or "").lower()
        creator_id = creator_data.get("creator_id") or creator_data.get("id", "")

        profile = None
        if service and creator_id:
            profile = self.creators_manager.get_creator(self.platform, service, creator_id)
        if profile:
            if posts in (None, 0) and profile.post_count is not None:
                posts = profile.post_count
            if fav is None and profile.favorited is not None:
                fav = profile.favorited

        self.creator_fav_label.setText(f"{self._format_creator_count(fav)} Favourites")
        self.creator_posts_label.setText(f"{self._format_creator_count(posts)} Posts")

        indexed = creator_data.get("creator_indexed") or creator_data.get("indexed")
        updated = creator_data.get("creator_updated") or creator_data.get("updated")
        if profile:
            if not indexed and profile.indexed:
                indexed = profile.indexed
            if not updated and profile.updated:
                updated = profile.updated
        self.creator_indexed_label.setText(f"Date Indexed: {self._format_creator_date(indexed)}")
        self.creator_updated_label.setText(f"Date Updated: {self._format_creator_date(updated)}")

        if (
            not self._suppress_detail_refresh
            and service
            and creator_id
            and (posts in (None, 0) or not indexed or not updated)
        ):
            self._refresh_creator_detail_profile(service, creator_id)

        banner_placeholder = QPixmap(Spacing.SIDEBAR_WIDTH, self.DETAIL_BANNER_HEIGHT)
        banner_placeholder.fill(QColor(Colors.BG_TERTIARY))
        self.creator_banner.setPixmap(banner_placeholder)
        if service and creator_id:
            base = "https://img.kemono.cr" if self.platform == "kemono" else "https://img.coomer.st"
            banner_url = f"{base}/banners/{service}/{creator_id}"

            def _on_banner_loaded(_, pixmap: QPixmap) -> None:
                if pixmap.isNull():
                    return
                target = (Spacing.SIDEBAR_WIDTH, self.DETAIL_BANNER_HEIGHT)
                self.creator_banner.setPixmap(scale_and_crop_pixmap(pixmap, target))

            self.creator_banner.load_image(
                url=banner_url,
                target_size=(Spacing.SIDEBAR_WIDTH, self.DETAIL_BANNER_HEIGHT),
                on_loaded=_on_banner_loaded,
            )

        avatar_placeholder = QPixmap(self.DETAIL_AVATAR_SIZE, self.DETAIL_AVATAR_SIZE)
        avatar_placeholder.fill(Qt.GlobalColor.transparent)
        self.creator_avatar.setPixmap(avatar_placeholder)
        if creator_id and service:
            try:
                icon_url = (
                    self._media.build_creator_icon_url(self.platform, service, creator_id)
                    if self._media
                    else MediaManager.build_creator_icon_url(self.platform, service, creator_id)
                )

                def _on_avatar_loaded(_, pixmap: QPixmap) -> None:
                    if pixmap.isNull():
                        return
                    scaled = scale_and_crop_pixmap(pixmap, (self.DETAIL_AVATAR_SIZE, self.DETAIL_AVATAR_SIZE))
                    self.creator_avatar.setPixmap(create_circular_pixmap(scaled, self.DETAIL_AVATAR_SIZE))

                self.creator_avatar.load_image(
                    url=icon_url,
                    target_size=(self.DETAIL_AVATAR_SIZE, self.DETAIL_AVATAR_SIZE),
                    on_loaded=_on_avatar_loaded,
                )
            except Exception:
                pass

        self._update_creator_service_icon(service)
        self.creator_detail.setVisible(True)

    def _clear_creator_detail(self) -> None:
        if hasattr(self, "creator_detail"):
            self.creator_detail.setVisible(False)
            if hasattr(self, "creator_service_icon"):
                self.creator_service_icon.setVisible(False)
        self._detail_refresh_inflight = None
        self._detail_refresh_token += 1

    def _refresh_creator_detail_profile(self, service: str, creator_id: str) -> None:
        key = (service, creator_id)
        if self._detail_refresh_inflight == key:
            return
        self._detail_refresh_token += 1
        token = self._detail_refresh_token
        self._detail_refresh_inflight = key
        worker = _CreatorDetailWorker(self.creators_manager, self.platform, service, creator_id, self)
        worker.loaded.connect(lambda creator, t=token: self._on_detail_profile_loaded(creator, key, t))
        worker.finished.connect(worker.deleteLater)
        self._detail_worker = worker
        worker.start()

    def _on_detail_profile_loaded(self, creator, key, token: int) -> None:
        if token != self._detail_refresh_token:
            return
        if self._detail_refresh_inflight != key:
            return
        self._detail_refresh_inflight = None
        if not self.selected_creator:
            return
        service, creator_id = key
        selected_id = self.selected_creator.get("creator_id") or self.selected_creator.get("id")
        if self.selected_creator.get("service") != service or selected_id != creator_id:
            return
        if creator:
            if creator.post_count is not None:
                self.selected_creator["post_count"] = creator.post_count
            if creator.favorited is not None:
                self.selected_creator["favorited"] = creator.favorited
            if creator.indexed:
                self.selected_creator["creator_indexed"] = creator.indexed
            if creator.updated:
                self.selected_creator["creator_updated"] = creator.updated
            if creator.name:
                self.selected_creator["name"] = creator.name
            self._suppress_detail_refresh = True
            try:
                self._update_creator_detail(self.selected_creator)
            finally:
                self._suppress_detail_refresh = False

    def _update_creator_service_icon(self, service: str) -> None:
        if not hasattr(self, "creator_service_icon"):
            return
        if not service:
            self.creator_service_icon.clear()
            self.creator_service_icon.setVisible(False)
            return
        icon = self._get_service_icon(service)
        self.creator_service_icon.setPixmap(icon.pixmap(16, 16))
        self.creator_service_icon.setVisible(True)

    def _get_service_icon(self, service: str) -> QIcon:
        service = (service or "").lower()
        logo_files = {
            'onlyfans': 'onlyfans.svg',
            'fansly': 'fansly.svg',
            'candfans': 'candfans.png',
            'patreon': None,
            'fanbox': 'fanbox.svg',
            'fantia': 'fantia-square-logo.png',
            'boosty': 'boosty.svg',
            'gumroad': 'gumroad.svg',
            'subscribestar': 'subscribestar.png',
            'dlsite': 'DLsite.png',
        }
        logo_file = logo_files.get(service)
        if not logo_file:
            icon_map = {
                'all': 'fa5s.globe',
                'patreon': 'fa5b.patreon',
            }
            return qta.icon(icon_map.get(service, 'fa5s.circle'), color=Colors.ACCENT_PRIMARY)

        logo_path = get_resource_path('resources', 'logos', logo_file)
        if not logo_path.exists():
            return qta.icon('fa5s.circle', color=Colors.TEXT_SECONDARY)

        if logo_file.endswith('.svg'):
            return QIcon(str(logo_path))

        pixmap = QPixmap(str(logo_path))
        if pixmap.isNull():
            return qta.icon('fa5s.circle', color=Colors.TEXT_SECONDARY)
        pixmap = pixmap.scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        return QIcon(pixmap)

    def _update_tab_styles(self):
        """Update tab button styles based on active tab"""
        active_style = f"""
            QPushButton#tabButton {{
                background-color: {Colors.BG_SECONDARY};
                border: none;
                border-radius: 0px;
                color: {Colors.ACCENT_PRIMARY};
                font-weight: {Fonts.WEIGHT_SEMIBOLD};
                font-size: {Fonts.SIZE_SM}px;
            }}
        """
        inactive_style = f"""
            QPushButton#tabButton {{
                background-color: {Colors.BG_DARKEST};
                border: none;
                border-radius: 0px;
                color: {Colors.TEXT_MUTED};
                font-weight: {Fonts.WEIGHT_MEDIUM};
                font-size: {Fonts.SIZE_SM}px;
            }}
            QPushButton#tabButton:hover {{
                background-color: {Colors.BG_DARK};
                color: {Colors.TEXT_SECONDARY};
            }}
        """

        if self.active_tab == 'search':
            self.search_tab_btn.setStyleSheet(active_style)
            self.recommended_tab_btn.setStyleSheet(inactive_style)
            self.linked_tab_btn.setStyleSheet(inactive_style)
        elif self.active_tab == 'recommended':
            self.search_tab_btn.setStyleSheet(inactive_style)
            self.recommended_tab_btn.setStyleSheet(active_style)
            self.linked_tab_btn.setStyleSheet(inactive_style)
        else:  # linked
            self.search_tab_btn.setStyleSheet(inactive_style)
            self.recommended_tab_btn.setStyleSheet(inactive_style)
            self.linked_tab_btn.setStyleSheet(active_style)

    def _switch_tab(self, tab: str):
        """Switch between search, recommended, and linked tabs"""
        if self.active_tab == tab:
            return

        self.active_tab = tab
        self._update_tab_styles()

        if tab == 'search':
            # Show search UI
            self.filters_widget.setVisible(True)
            self.scroll_area.setVisible(True)
            self.recommended_scroll_area.setVisible(False)
            self.linked_scroll_area.setVisible(False)
            self.pagination_container.setVisible(True)
            self.status_label.setVisible(False)
        elif tab == 'recommended':
            # Show recommended UI
            self.filters_widget.setVisible(False)
            self.scroll_area.setVisible(False)
            self.linked_scroll_area.setVisible(False)
            self.pagination_container.setVisible(False)

            # Load recommended if we have a selected creator
            if self.selected_creator:
                self.recommended_scroll_area.setVisible(True)
                self.status_label.setVisible(False)
                self._load_recommended_creators()
            else:
                self.recommended_scroll_area.setVisible(False)
                self.status_label.setText("Select a creator to see recommendations")
                self.status_label.setVisible(True)
        else:  # linked
            # Show linked UI
            self.filters_widget.setVisible(False)
            self.scroll_area.setVisible(False)
            self.recommended_scroll_area.setVisible(False)
            self.pagination_container.setVisible(False)

            # Load linked if we have a selected creator
            if self.selected_creator:
                self.linked_scroll_area.setVisible(True)
                self.status_label.setVisible(False)
                self._load_linked_creators()
            else:
                self.linked_scroll_area.setVisible(False)
                self.status_label.setText("Select a creator to see linked accounts")
                self.status_label.setVisible(True)

    def _create_filters(self) -> QWidget:
        """Create search and filter controls"""
        container = QWidget()
        container.setStyleSheet(f"background-color: {Colors.BG_DARKEST}; border-bottom: 1px solid {Colors.BG_PRIMARY};")

        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Search
        self.search_input = QLineEdit()
        self.search_input.setObjectName("creatorsSearchBar")
        self.search_input.setPlaceholderText("Search creators...")
        self.search_input.setFixedHeight(32)
        self.search_input.returnPressed.connect(self._on_search)
        layout.addWidget(self.search_input)

        # Service filter and sort
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(6)

        # Service dropdown
        self.service_combo = QComboBox()
        self.service_combo.setObjectName("creatorsServiceCombo")
        self.service_combo.addItems(['all', 'onlyfans', 'fansly', 'candfans'])
        self.service_combo.setFixedHeight(32)
        self.service_combo.currentTextChanged.connect(self._on_service_changed)
        controls_layout.addWidget(self.service_combo)

        # Sort dropdown (compact)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems([
            "Name ▲",
            "Name ▼",
            "Updated ▼",
            "Favorited ▼"
        ])
        self.sort_combo.setCurrentIndex(3)
        self.sort_combo.setFixedHeight(32)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        self.sort_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {Colors.BG_HOVER};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_SM}px;
                padding: 4px 8px;
                color: {Colors.TEXT_PRIMARY};
                font-size: {Fonts.SIZE_LG}px;
                font-family: {Fonts.FAMILY};
            }}
        """)
        controls_layout.addWidget(self.sort_combo)

        layout.addLayout(controls_layout)

        return container

    def _load_creators(self, skip_load_check: bool = False):
        """Load creators"""
        try:
            # Only check API updates on first load
            if not skip_load_check:
                success = self.creators_manager.load_creators(self.platform)
                if not success:
                    self.status_label.setText("Failed to load creators")
                    self.status_label.setVisible(True)
                    self.scroll_area.setVisible(False)
                    return

            # Get page of creators
            query = self.search_input.text().strip()
            offset = self.current_page * self.page_size

            if query:
                creators = self.creators_manager.search_creators(
                    self.platform,
                    self.service if self.service != 'all' else None,
                    query,
                    limit=self.page_size,
                    offset=offset,
                    sort_by=self.creator_sort_by,
                    sort_dir=self.creator_sort_dir
                )
                total_count = self.creators_manager.get_creators_count(
                    self.platform,
                    self.service if self.service != 'all' else None,
                    query
                )
            else:
                creators = self.creators_manager.get_creators_paginated(
                    self.platform,
                    self.service if self.service != 'all' else None,
                    limit=self.page_size,
                    offset=offset,
                    sort_by=self.creator_sort_by,
                    sort_dir=self.creator_sort_dir
                )
                total_count = self.creators_manager.get_creators_count(
                    self.platform,
                    self.service if self.service != 'all' else None
                )

            # Normalize to dicts and ensure id uses creator_id when present.
            creators = [dict(c) if not isinstance(c, dict) else c for c in creators]
            for creator in creators:
                if creator.get("creator_id") and not creator.get("id"):
                    creator["id"] = creator["creator_id"]
                # Ensure favorited_count has a default value of 0 so creators without it still show
                if "favorited_count" not in creator or creator["favorited_count"] is None:
                    creator["favorited_count"] = 0

            self.total_count = total_count

            # Display creators
            self._display_creators(creators)

            # Update pagination
            total_pages = max(1, (total_count + self.page_size - 1) // self.page_size)
            self.pagination.set_page(self.current_page, total_pages)

            # Update result count
            label = "creator" if total_count == 1 else "creators"
            self.result_label.setText(f"{total_count}\n{label}")

        except Exception as e:
            logger.error(f"Error loading creators: {e}")
            import traceback
            logger.error(traceback.format_exc())
            self.status_label.setText(f"Error: {str(e)}")
            self.status_label.setVisible(True)
            self.scroll_area.setVisible(False)

    def _display_creators(self, creators: List[Dict]):
        """Display creators in list with chunked async loading"""
        logger.info(f"Loading {len(creators)} creators into sidebar")
        
        # Cancel any pending chunk loads
        if self._loading_chunks:
            logger.warning("Canceling previous chunk loading operation")
            # Note: Can't actually stop QTimer callbacks once queued, but we can ignore them
            self._loading_chunks = False
        
        # Disable updates on scroll area only (not entire sidebar) to prevent flashing
        # This keeps pagination and other static elements from being repainted
        self.scroll_area.setUpdatesEnabled(False)
        self.list_container.setUpdatesEnabled(False)
        
        # Clear existing - cancel any pending image loads first
        for item in self.creator_items:
            # Cancel any pending avatar image loads
            if hasattr(item, 'avatar_label') and hasattr(item.avatar_label, '_cleanup_loader'):
                item.avatar_label._cleanup_loader()
            item.setParent(None)
            item.deleteLater()
        self.creator_items.clear()

        if not creators:
            self.status_label.setText("No creators found")
            self.status_label.setVisible(True)
            self.scroll_area.setVisible(False)
            self.scroll_area.setUpdatesEnabled(True)
            self.list_container.setUpdatesEnabled(True)
            return

        # Reset content height tracking for this load
        self._creator_items_height = 0
        self.list_container.setMinimumHeight(0)

        self.status_label.setVisible(False)
        self.scroll_area.setVisible(True)
        
        # Set loading flag and increment session ID
        self._loading_chunks = True
        self._session_load_id += 1  # New session for this load
        load_id = self._session_load_id
        
        # Load creators in chunks asynchronously
        chunk_size = 10  # Load 10 creators at a time
        chunks = [creators[i:i + chunk_size] for i in range(0, len(creators), chunk_size)]
        
        logger.info(f"Split into {len(chunks)} chunks of size {chunk_size}, session_load_id={load_id}")
        
        # Queue chunks with delays
        for chunk_idx, chunk in enumerate(chunks):
            delay = chunk_idx * 100  # 100ms between chunks
            is_last_chunk = (chunk_idx == len(chunks) - 1)
            QTimer.singleShot(delay, lambda c=chunk, last=is_last_chunk, idx=chunk_idx, lid=load_id: self._add_creator_chunk(c, last, idx, lid))
    
    def _add_creator_chunk(self, creators_chunk: List[Dict], is_last_chunk: bool = False, chunk_idx: int = 0, load_id: int = 0):
        """Add a chunk of creator items at once."""
        # Ignore callbacks from cancelled/stale loads
        if not self._loading_chunks or load_id != self._session_load_id:
            logger.debug(f"Ignoring chunk {chunk_idx} from stale session {load_id} (current={self._session_load_id})")
            return
            
        logger.debug(f"Adding chunk {chunk_idx} with {len(creators_chunk)} creators (load_id={load_id})")
        
        for creator in creators_chunk:
            item = CreatorListItem(
                creator,
                self.platform,
                media_manager=self._media,
            )
            item.clicked.connect(self._on_creator_clicked)
            self.list_layout.addWidget(item)
            self.creator_items.append(item)

            # Highlight if selected
            if self.selected_creator and creator.get('id') == self.selected_creator.get('id'):
                item.set_selected(True)

            self._creator_items_height += max(item.minimumHeight(), item.sizeHint().height())
            self.list_container.setMinimumHeight(self._creator_items_height)
        
        # Process events after each chunk to allow styling to apply
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        
        # Re-enable updates after last chunk is added
        if is_last_chunk:
            logger.info(f"Last chunk loaded. Total creators in sidebar: {len(self.creator_items)} (load_id={load_id})")
            self._loading_chunks = False
            self.list_container.setUpdatesEnabled(True)
            self.scroll_area.setUpdatesEnabled(True)
            # Force style application and repaint on list container only
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()  # Process pending events
            self.list_container.style().polish(self.list_container)
            self.list_container.update()
            self.scroll_area.update()
            
            # After styling is applied, check if we should load more creators to fill the scroll area
            self._continue_loading_if_needed()
    
    def _continue_loading_if_needed(self):
        """Continue loading more creator pages if there's room in the scroll area"""
        # Check if there are more pages to load
        total_pages = max(1, (self.total_count + self.page_size - 1) // self.page_size)
        has_more_pages = self.current_page < total_pages - 1
        
        if not has_more_pages:
            logger.debug("No more creator pages to load")
            return
        
        # Check if we have room in the scroll area (if not yet filled to viewport height)
        viewport_height = self.scroll_area.viewport().height()
        content_height = self.list_container.height()
        
        # Load more if content doesn't fill the viewport yet
        if content_height < viewport_height * 1.5:  # Load until 1.5x viewport height
            logger.info(f"Content height ({content_height}px) < viewport ({viewport_height}px), loading next page...")
            self.current_page += 1
            # Use a small delay before loading next batch to ensure current batch is fully rendered
            QTimer.singleShot(200, self._load_next_page_append)
        else:
            logger.debug(f"Scroll area filled (content: {content_height}px, viewport: {viewport_height}px)")
    
    def _load_next_page_append(self):
        """Load the next page and append to existing creators"""
        try:
            offset = self.current_page * self.page_size
            query = self.search_input.text().strip()
            
            logger.info(f"Loading additional creators page {self.current_page + 1} (offset={offset})")
            
            # Get next page of creators
            if query:
                creators = self.creators_manager.search_creators(
                    self.platform,
                    self.service if self.service != 'all' else None,
                    query,
                    limit=self.page_size,
                    offset=offset,
                    sort_by=self.creator_sort_by,
                    sort_dir=self.creator_sort_dir
                )
            else:
                creators = self.creators_manager.get_creators_paginated(
                    self.platform,
                    self.service if self.service != 'all' else None,
                    limit=self.page_size,
                    offset=offset,
                    sort_by=self.creator_sort_by,
                    sort_dir=self.creator_sort_dir
                )
            
            # Normalize creators
            creators = [dict(c) if not isinstance(c, dict) else c for c in creators]
            for creator in creators:
                if creator.get("creator_id") and not creator.get("id"):
                    creator["id"] = creator["creator_id"]
                if "favorited_count" not in creator or creator["favorited_count"] is None:
                    creator["favorited_count"] = 0
            
            if not creators:
                logger.info("No more creators to load")
                return
            
            # Append creators to existing list (chunked loading)
            self._append_creators(creators)
            
        except Exception as e:
            logger.error(f"Error loading next creator page: {e}")
    
    def _append_creators(self, creators: List[Dict]):
        """Append creators to existing list with chunked async loading"""
        logger.info(f"Appending {len(creators)} creators to sidebar (currently have {len(self.creator_items)})")
        
        # Set loading flag (keep same session_load_id)
        self._loading_chunks = True
        load_id = self._session_load_id  # Use current session ID
        
        # Load creators in chunks asynchronously
        chunk_size = 10
        chunks = [creators[i:i + chunk_size] for i in range(0, len(creators), chunk_size)]
        
        logger.info(f"Appending {len(chunks)} chunks, session_load_id={load_id}")
        
        # Queue chunks with delays
        for chunk_idx, chunk in enumerate(chunks):
            delay = chunk_idx * 100
            is_last_chunk = (chunk_idx == len(chunks) - 1)
            QTimer.singleShot(delay, lambda c=chunk, last=is_last_chunk, idx=chunk_idx, lid=load_id: self._append_creator_chunk(c, last, idx, lid))
    
    def _append_creator_chunk(self, creators_chunk: List[Dict], is_last_chunk: bool = False, chunk_idx: int = 0, load_id: int = 0):
        """Append a chunk of creator items."""
        # Ignore callbacks from stale sessions
        if not self._loading_chunks or load_id != self._session_load_id:
            logger.debug(f"Ignoring append chunk {chunk_idx} from stale session {load_id} (current={self._session_load_id})")
            return
        
        logger.debug(f"Appending chunk {chunk_idx} with {len(creators_chunk)} creators (load_id={load_id})")
        
        for creator in creators_chunk:
            item = CreatorListItem(
                creator,
                self.platform,
                media_manager=self._media,
            )
            item.clicked.connect(self._on_creator_clicked)
            self.list_layout.addWidget(item)
            self.creator_items.append(item)
            
            if self.selected_creator and creator.get('id') == self.selected_creator.get('id'):
                item.set_selected(True)
            
            if not hasattr(self, "_creator_items_height"):
                self._creator_items_height = 0
            self._creator_items_height += max(item.minimumHeight(), item.sizeHint().height())
            self.list_container.setMinimumHeight(self._creator_items_height)
        
        # Process events after each chunk to allow styling to apply
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        
        # After last chunk, check if we need to load more
        if is_last_chunk:
            logger.info(f"Append complete. Total creators: {len(self.creator_items)} (load_id={load_id})")
            self._loading_chunks = False
            # Process events and continue loading if needed
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()
            self._continue_loading_if_needed()

    def _on_creator_clicked(self, creator_data: Dict):
        """Handle creator selection"""
        self.selected_creator = creator_data

        # Update selection highlighting
        for item in self.creator_items:
            item.set_selected(item.creator_data.get('id') == creator_data.get('id'))

        # Update creator detail panel
        self._update_creator_detail(creator_data)

        # Show clear button
        self._update_clear_button_visibility()

        # Load recommended creators
        self._load_recommended_creators()

        # Emit selection
        logger.info(f"Creator selected: {creator_data.get('name')}")
        QTimer.singleShot(0, lambda: self.creator_selected.emit(creator_data))
    
    def _update_clear_button_visibility(self):
        """Show clear button if there are active filters or selection"""
        has_filters = (
            self.selected_creator is not None or
            self.search_input.text().strip() != "" or
            self.service != 'all' or
            self.creator_sort_by != 'favorited' or
            self.creator_sort_dir != 'DESC'
        )
        self.clear_btn.setVisible(has_filters)

    def _load_recommended_creators(self):
        """Load recommended creators for the currently selected creator"""
        logger.info("_load_recommended_creators called")
        
        if not self.selected_creator:
            logger.info("No selected creator")
            return

        # Use creator_id (username) not id (numeric)
        creator_id = self.selected_creator.get('creator_id') or self.selected_creator.get('id')
        service = self.selected_creator.get('service')
        name = self.selected_creator.get('name')
        logger.info(f"Selected creator data: creator_id={creator_id}, name={name}, service={service}")
        
        if not creator_id or not service:
            logger.warning(f"Missing creator_id or service: id={creator_id}, service={service}")
            return

        try:
            # Fetch recommended creators
            logger.info(f"Calling API: platform={self.platform}, service={service}, creator_id={creator_id}")
            results = self.creators_manager.get_recommended_creators(
                self.platform,
                service,
                creator_id
            )

            logger.info(f"API returned {len(results)} recommended creators")
            
            if not results:
                logger.info("No results")
                return

            # Clear existing items
            for item in self.recommended_items:
                self.recommended_list_layout.removeWidget(item)
                item.deleteLater()
            self.recommended_items.clear()

            # Add new items (all results)
            self.recommended_creators = results
            for creator, score in self.recommended_creators:
                # Create dict from CreatorDTO
                creator_dict = {
                    'id': creator.id,
                    'creator_id': creator.id,
                    'name': creator.name,
                    'service': creator.service,
                    'platform': creator.platform,
                    'post_count': creator.post_count,
                    'favorited': creator.favorited,
                }
                item = RecommendedCreatorItem(
                    creator_dict,
                    score,
                    self.platform,
                    media_manager=self._media,
                    parent=self.recommended_list_container
                )
                item.clicked.connect(self._on_recommended_creator_clicked)
                self.recommended_list_layout.addWidget(item)
                self.recommended_items.append(item)

            # Update count badge if it exists
            if hasattr(self, 'recommended_count_label'):
                self.recommended_count_label.setText(str(len(self.recommended_creators)))
            
            logger.info(f"Loaded {len(self.recommended_creators)} recommended creators")

        except Exception as e:
            logger.error(f"Failed to load recommended creators: {e}", exc_info=True)

    def _on_recommended_creator_clicked(self, creator_data: Dict):
        """Handle clicking a recommended creator"""
        service = creator_data.get('service', '')
        name = creator_data.get('name', 'Unknown')

        # Check if service is supported
        if not is_service_supported(self.platform, service):
            logger.info(f"Service '{service}' not supported for platform '{self.platform}'")
            self.unsupported_service.emit(service, name)
            return

        # Switch back to search tab
        self._switch_tab('search')

        # Clear search and update service filter
        self.search_input.clear()
        self.service_combo.setCurrentText(service if service else 'all')

        # Select the creator
        self._on_creator_clicked(creator_data)

    def _load_linked_creators(self):
        """Load linked creators for the currently selected creator"""
        logger.info("_load_linked_creators called")
        
        if not self.selected_creator:
            logger.info("No selected creator")
            return

        # Use creator_id (username) not id (numeric)
        creator_id = self.selected_creator.get('creator_id') or self.selected_creator.get('id')
        service = self.selected_creator.get('service')
        name = self.selected_creator.get('name')
        logger.info(f"Selected creator data: creator_id={creator_id}, name={name}, service={service}")
        
        if not creator_id or not service:
            logger.warning(f"Missing creator_id or service: id={creator_id}, service={service}")
            return

        try:
            # Fetch linked creators
            logger.info(f"Calling API: platform={self.platform}, service={service}, creator_id={creator_id}")
            results = self.creators_manager.get_linked_creators(
                self.platform,
                service,
                creator_id
            )

            logger.info(f"API returned {len(results)} linked creators")
            
            if not results:
                logger.info("No linked creators found")
                self.status_label.setText("No linked accounts found")
                self.status_label.setVisible(True)
                return
            
            self.status_label.setVisible(False)

            # Clear existing items
            if not hasattr(self, 'linked_items'):
                self.linked_items = []
            
            for item in self.linked_items:
                self.linked_list_layout.removeWidget(item)
                item.deleteLater()
            self.linked_items.clear()

            # Add new items
            for creator in results:
                # Create dict from CreatorDTO
                creator_dict = {
                    'id': creator.id,
                    'creator_id': creator.id,
                    'name': creator.name,
                    'service': creator.service,
                    'platform': creator.platform,
                    'post_count': creator.post_count,
                    'favorited': creator.favorited,
                }
                # Reuse RecommendedCreatorItem but without similarity score
                item = RecommendedCreatorItem(
                    creator_dict,
                    0.0,  # No score for linked creators
                    self.platform,
                    media_manager=self._media,
                    parent=self.linked_list_container,
                    show_score=False  # Hide score for linked creators
                )
                item.clicked.connect(self._on_linked_creator_clicked)
                self.linked_list_layout.addWidget(item)
                self.linked_items.append(item)
            
            logger.info(f"Loaded {len(results)} linked creators")

        except Exception as e:
            logger.error(f"Failed to load linked creators: {e}", exc_info=True)
            self.status_label.setText("Failed to load linked accounts")
            self.status_label.setVisible(True)

    def _on_linked_creator_clicked(self, creator_data: Dict):
        """Handle clicking a linked creator"""
        service = creator_data.get('service', '')
        name = creator_data.get('name', 'Unknown')

        # Check if service is supported
        if not is_service_supported(self.platform, service):
            logger.info(f"Service '{service}' not supported for platform '{self.platform}'")
            self.unsupported_service.emit(service, name)
            return

        # Switch back to search tab
        self._switch_tab('search')

        # Clear search and update service filter
        self.search_input.clear()
        self.service_combo.setCurrentText(service if service else 'all')

        # Select the creator
        self._on_creator_clicked(creator_data)

    def select_creator(self, creator_data: Dict) -> None:
        """Select a creator without emitting signals."""
        if not creator_data:
            return
        self.selected_creator = creator_data
        self.clear_btn.setVisible(True)
        for item in self.creator_items:
            item.set_selected(item.creator_data.get('id') == creator_data.get('id'))
        self._update_creator_detail(creator_data)

    def _on_clear_selection(self):
        """Clear creator selection and reset all filters"""
        # Clear selection
        self.selected_creator = None
        self._clear_creator_detail()

        # Clear highlighting
        for item in self.creator_items:
            item.set_selected(False)

        # Clear recommended creators
        for item in self.recommended_items:
            item.setParent(None)
            item.deleteLater()
        self.recommended_items.clear()
        self.recommended_creators = []

        # Clear linked creators
        for item in self.linked_items:
            item.setParent(None)
            item.deleteLater()
        self.linked_items.clear()

        # Reset all filters
        self.search_input.clear()
        self.service_combo.setCurrentText('all')
        self.sort_combo.setCurrentIndex(3)  # Default to favorited DESC

        # Reset to first page
        self.current_page = 0

        # Hide clear button
        self.clear_btn.setVisible(False)

        # Switch back to search tab
        self._switch_tab('search')

        # Reload creators with reset filters
        self._load_creators(skip_load_check=True)

        # Emit clear (for posts view)
        self.creator_cleared.emit()

    def clear_selection(self):
        """Clear selection only, without changing filters or emitting signals."""
        self.selected_creator = None
        self.clear_btn.setVisible(False)
        self._clear_creator_detail()

        # Clear highlighting
        for item in self.creator_items:
            item.set_selected(False)

        # Clear recommended creators
        for item in self.recommended_items:
            item.setParent(None)
            item.deleteLater()
        self.recommended_items.clear()
        self.recommended_creators = []

        # Clear linked creators
        for item in self.linked_items:
            item.setParent(None)
            item.deleteLater()
        self.linked_items.clear()

        # Switch back to search tab if on recommended/linked
        if self.active_tab in ('recommended', 'linked'):
            self._switch_tab('search')

    def _on_search(self):
        """Handle search"""
        self.current_page = 0
        self._update_clear_button_visibility()
        self._load_creators(skip_load_check=True)

    def _on_service_changed(self, service: str):
        """Handle service change"""
        self.service = service
        self.current_page = 0
        self._update_clear_button_visibility()
        self._load_creators(skip_load_check=True)
        # Also emit for posts view
        self.service_changed.emit(service)

    def _on_sort_changed(self, index: int):
        """Handle sort change"""
        mapping = {
            0: ('name', 'ASC'),
            1: ('name', 'DESC'),
            2: ('updated', 'DESC'),
            3: ('favorited', 'DESC')
        }
        self.creator_sort_by, self.creator_sort_dir = mapping.get(index, ('name', 'ASC'))
        self.current_page = 0
        self._update_clear_button_visibility()
        self._load_creators(skip_load_check=True)

    def _on_page_changed(self, page: int):
        """Handle page change"""
        self.current_page = page
        self._load_creators(skip_load_check=True)

    def _on_random_creator(self):
        """Fetch a random creator and select it."""
        try:
            creator = self.creators_manager.get_random_creator(self.platform)
        except Exception as exc:
            logger.error("Random creator fetch failed: %s", exc)
            return

        if not creator:
            logger.warning("Random creator fetch returned no creator")
            return

        # Check if service is supported
        if not is_service_supported(self.platform, creator.service):
            logger.info(f"Random creator has unsupported service '{creator.service}'")
            self.unsupported_service.emit(creator.service, creator.name)
            return

        creator_data = create_creator_data_dict(
            creator_id=creator.id,
            service=creator.service,
            name=creator.name,
            platform=self.platform,
        )
        QTimer.singleShot(0, lambda: self.creator_selected.emit(creator_data))

    def set_platform(self, platform: str):
        """Change platform"""
        if platform != self.platform:
            self.platform = platform
            self.current_page = 0
            self.selected_creator = None
            self.clear_btn.setVisible(False)
            self._clear_creator_detail()

            # Update service dropdown for platform
            self.service_combo.clear()
            if platform == 'kemono':
                self.service_combo.addItems(['all', 'patreon', 'fanbox', 'fantia', 'boosty', 'gumroad', 'subscribestar', 'dlsite'])
            else:
                self.service_combo.addItems(['all', 'onlyfans', 'fansly', 'candfans'])

            self._load_creators()

    def set_service(self, service: str):
        """Set service filter"""
        if service != self.service:
            self.service = service
            self.current_page = 0
            # Update combo without triggering signal
            self.service_combo.blockSignals(True)
            index = self.service_combo.findText(service)
            if index >= 0:
                self.service_combo.setCurrentIndex(index)
            self.service_combo.blockSignals(False)
            self._load_creators(skip_load_check=True)

    def refresh(self):
        """Refresh creators list"""
        self._load_creators()

    def load_initial(self):
        """Load creators for first time"""
        self._load_creators()
