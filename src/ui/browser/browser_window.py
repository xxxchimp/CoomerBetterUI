"""
    Browser window with sidebar layout
    - Platform selector in menu bar
- Creators sidebar on left (collapsible list view)
- Posts main area on right (always visible)
"""
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QToolBar, QLineEdit, QPushButton, QComboBox,
                             QLabel, QStatusBar, QMenu, QMessageBox,
                             QProgressBar, QSizePolicy, QSplitter,
                             QFrame, QDateEdit, QCheckBox, QToolButton, QDialog,
                             QAbstractSpinBox, QButtonGroup, QTabWidget)
from PyQt6.QtCore import Qt, QTimer, pyqtSlot, QThread, QDate, QSize, QPoint
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence, QIcon, QPixmap
import logging
from typing import Optional, List, Dict
import json
import html
import re
import unicodedata
import qtawesome as qta

from src.ui.widgets.native_widgets import PostGridView, ToastNotification, DownloadProgressBar
from src.ui.widgets.download_panel import DownloadPanel
from src.ui.widgets.m3_calendar import M3DockedCalendar
from src.ui.creators.creators_sidebar import CreatorsSidebar
from src.ui.common.enhanced_pagination import EnhancedPagination
from src.ui.gallery.gallery_post_view import GalleryPostView
from src.ui.creators.creator_utils import resolve_creator_from_manager
from src.ui.browser.browser_workers import PostsLoadWorker, PostDetailWorker, RandomPostWorker
from src.ui.browser.browser_downloads import DownloadMixin
from src.core.dto.post import PostDTO
from src.ui.common.pagination_utils import compute_posts_pagination
from src.ui.common.theme import Styles, Spacing, Colors, Fonts
from src.utils.file_utils import get_resource_path

logger = logging.getLogger(__name__)

TAG_SPAM_RE = re.compile(
    r"^(?:"
    r"advertis(?:e|ed|er|ers|ing|ement|ments?|ment|in)|advertiz(?:e|ed|er|ers|ing)?|"
    r"adversting|advesting|"
    r"ads?(?:24|12)?h?|"
    r"announcement|announcemet|announcemen|"
    r"ann"
    r")$",
    re.IGNORECASE,
)
HTML_TAG_RE = re.compile(r"<[^>]*>?|&lt;[^&]*&gt;|&amp;lt;[^&]*&amp;gt;", re.IGNORECASE)
AT_USERNAME_RE = re.compile(r"@\w", re.IGNORECASE)
ONLY_HASHES_RE = re.compile(r"^\s*#+\s*$")
PROMO_KEYWORD_RE = re.compile(
    r"(?:"
    r"promo(?:code|tion|tional|s)?|"
    r"discount(?:s)?|deal(?:s)?|offer(?:s)?|free(?:bie|bies|bies)?|"
    r"giveaway(?:s)?|subscribe(?:r|rs|s|d|ing)?|subscription(?:s)?|"
    r"\bsub(?:s)?\b|sub4sub|s4s|"
    r"join(?:ed|ing)?|buy(?:ing)?|sale(?:s)?|limited|exclusive|"
    r"leak(?:ed|s)?|link(?:s)?|click(?:ed|ing)?|"
    r"dm(?:s)?|dms"
    r")",
    re.IGNORECASE,
)
BROKEN_SENTENCE_PUNCT_RE = re.compile(r"[.!?]")
LONG_TAG_NO_SPACE_LEN = 32
AD_SHORT_RE = re.compile(r"^ads?\d+$", re.IGNORECASE)
AD_PREFIXES = ("advert", "adversting", "advesting")
ANNOUNCE_PREFIXES = ("announc",)
PROMO_SHORT_TOKENS = {"sub", "subs", "sub4sub", "s4s"}


def _clean_tag(raw_tag: str) -> str:
    """Strip HTML fragments, emoji, and punctuation from a tag, then NFKD-normalize."""
    cleaned = re.sub(r"<[^>]*>?", "", raw_tag or "")
    cleaned = unicodedata.normalize("NFKD", cleaned)
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", cleaned)
    return cleaned.strip()


def _normalize_text(text: str) -> str:
    """NFKD-normalize so unicode bold/italic (𝐅𝐑𝐄𝐄) becomes plain ASCII."""
    return unicodedata.normalize("NFKD", text or "")


def _tokenize_tag(text: str) -> List[str]:
    normalized = _normalize_text(text).lower()
    tokens = re.split(r"[^a-z0-9]+", normalized)
    return [token for token in tokens if token]


def _is_spam_tag(tag_name: str) -> bool:
    raw = tag_name or ""
    if not raw.strip():
        return False
    tokens = _tokenize_tag(raw)
    for token in tokens:
        if token in ("ad", "ads"):
            return True
        if AD_SHORT_RE.match(token):
            return True
        if token.startswith("ads") and len(token) <= 6:
            return True
        if token.startswith(AD_PREFIXES):
            return True
        if token.startswith(ANNOUNCE_PREFIXES):
            return True
        if token in PROMO_SHORT_TOKENS:
            return True
    if HTML_TAG_RE.search(raw):
        return True
    if AT_USERNAME_RE.search(raw):
        return True
    if ONLY_HASHES_RE.match(raw):
        return True
    stripped = raw.strip()
    if stripped.startswith("#") and not any(ch.isalnum() for ch in stripped[1:]):
        return True
    if len(raw) >= LONG_TAG_NO_SPACE_LEN and not re.search(r"\s", raw):
        return True
    normalized = _normalize_text(raw).lower()
    if PROMO_KEYWORD_RE.search(normalized):
        return True
    if " " in raw and (len(raw) >= 40 or BROKEN_SENTENCE_PUNCT_RE.search(raw)):
        return True
    cleaned = _clean_tag(raw)
    return bool(cleaned) and TAG_SPAM_RE.match(cleaned)


def _filter_spam_tags(tags: List[Dict]) -> List[Dict]:
    filtered = []
    for tag in tags or []:
        if isinstance(tag, dict):
            name = str(tag.get("name") or tag.get("tag") or "")
        else:
            name = str(tag or "")
        if not _is_spam_tag(name):
            filtered.append(tag)
    return filtered


class BrowserWindow(DownloadMixin, QMainWindow):
    """
    Browser with sidebar layout
    """

    def __init__(self, core):
        super().__init__()

        self.core = core
        self.db = core.db
        self.posts_manager = core.posts
        self.current_posts = []
        self.current_offset = 0
        self.current_service = 'all'
        self.current_query = ''
        self.current_platform = 'coomer'
        self.current_creator_id = None
        self.current_creator_name = None
        self._creator_service = None  # Service of selected creator (independent from filter)
        self.current_posts_mode = "all"
        self.current_tags: List[str] = []
        self.available_tags: List[Dict] = []  # List of {name: str, count: int}
        self._content_type_filter = "all"     # "all", "images", "videos"
        self._duration_filters: set = set()   # empty = any; subset of {"<1","1-5","5-30","30+"}
        self.current_popular_period = "recent"
        self.current_popular_date: Optional[str] = None
        self.current_popular_anchor: Optional[str] = None
        self._posts_worker = None
        self._posts_token = 0
        self._post_detail_worker = None
        self._post_detail_token = 0
        self._random_post_worker = None
        self._random_post_token = 0
        self._random_post_detail_worker = None
        self._random_post_detail_token = 0
        self._stale_workers: List[QThread] = []
        self.loading_posts = False
        self.sidebar_visible = True
        self._closing = False  # Flag to prevent callbacks during shutdown
        from src.ui.images.image_loader_manager import get_image_loader_manager
        get_image_loader_manager(self.db, cache_dir=self.core.cache.thumbnails, core_context=self.core)

        # Initialize creators manager
        self.creators_manager = core.creators

        # Setup UI
        self.setWindowTitle("Coomer BetterUI")
        self.setGeometry(100, 100, 1600, 900)

        self._create_ui()
        self._create_toolbar()
        self._create_statusbar()
        self._create_menus()
        self._setup_shortcuts()
        self._set_posts_mode(self.current_posts_mode)

        # Load initial content
        QTimer.singleShot(500, self._load_initial_posts)
        # Load creators in sidebar
        QTimer.singleShot(1000, self._load_initial_creators)
        # Load available tags
        QTimer.singleShot(1500, self._load_tags)

    def _get_service_icon(self, service: str) -> QIcon:
        """Load service logo from resources/logos folder"""
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
            # Use qta icon for "all", patreon, or unknown services
            icon_map = {
                'all': 'fa5s.globe',
                'patreon': 'fa5b.patreon'
            }
            return qta.icon(icon_map.get(service, 'fa5s.circle'), color='#f7673a')
        
        # Build path to logo file (handles PyInstaller bundles)
        logo_path = get_resource_path('resources', 'logos', logo_file)
        if not logo_path.exists():
            # Fallback to qta icon if file not found
            return qta.icon('fa5s.circle', color='#e0e0e0')
        
        # Load icon from file
        if logo_file.endswith('.svg'):
            return QIcon(str(logo_path))
        else:  # PNG
            pixmap = QPixmap(str(logo_path))
            if not pixmap.isNull():
                # Scale to appropriate size for combo box
                pixmap = pixmap.scaled(20, 20, Qt.AspectRatioMode.KeepAspectRatio, 
                                      Qt.TransformationMode.SmoothTransformation)
                return QIcon(pixmap)
            else:
                return qta.icon('fa5s.circle', color='#e0e0e0')

    def _create_ui(self):
        """Create main UI layout"""
        central_widget = QWidget()
        central_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCentralWidget(central_widget)
        self._central_widget = central_widget

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Horizontal splitter: Creators sidebar | Posts main area
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(True)
        self.splitter.setHandleWidth(1)
        self.splitter.setStyleSheet("QSplitter::handle { background: transparent; }")
        self.splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.splitter.setMinimumHeight(0)

        # LEFT: Creators sidebar
        self.creators_sidebar = CreatorsSidebar(
            self.creators_manager,
            self.current_platform,
            media_manager=self.core.media,
        )
        self.creators_sidebar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.creators_sidebar.setMinimumHeight(0)
        self.creators_sidebar.creator_selected.connect(self._on_creator_selected)
        self.creators_sidebar.creator_cleared.connect(self._on_creator_cleared)
        self.creators_sidebar.service_changed.connect(self._on_sidebar_service_changed)
        self.creators_sidebar.unsupported_service.connect(self._on_unsupported_service)
        self.splitter.addWidget(self.creators_sidebar)

        # RIGHT: Posts main area
        posts_container = self._create_posts_area()
        posts_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        posts_container.setMinimumHeight(0)
        self.splitter.addWidget(posts_container)
        self._sync_posts_header_height()

        # Set initial splitter sizes (sidebar: 280px, posts: remaining)
        self.splitter.setSizes([Spacing.SIDEBAR_WIDTH, 1320])

        main_layout.addWidget(self.splitter, 1)
        self.splitter.splitterMoved.connect(lambda *_: self._position_sidebar_toggle_tab())

        # Download panel at bottom (Chromium-style with file list)
        self.download_panel = DownloadPanel(self)
        self.download_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        main_layout.addWidget(self.download_panel, 0)
        
        # Initialize download panel signal connections
        self._init_download_panel_signals()

        # Toast notification
        self.toast = ToastNotification(self)
        self._create_sidebar_toggle_tab()
        QTimer.singleShot(0, self._position_sidebar_toggle_tab)

    def _create_sidebar_toggle_tab(self) -> None:
        parent = getattr(self, "_central_widget", self)
        self.sidebar_toggle_btn = QToolButton(parent)
        self._apply_sidebar_toggle_style(self.sidebar_toggle_btn)
        self.sidebar_toggle_btn.clicked.connect(self._toggle_sidebar)
        self._update_sidebar_toggle_icon()
        self._position_sidebar_toggle_tab()
        self.sidebar_toggle_btn.show()
        self._sidebar_toggle_parent = parent
        self._sidebar_toggle_overlay_root = None

    def _apply_sidebar_toggle_style(self, button: QToolButton) -> None:
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

    def _create_posts_area(self) -> QWidget:
        """Create main posts area"""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Posts header with search
        self.posts_header = self._create_posts_header()
        layout.addWidget(self.posts_header)

        # Posts view (grid or detail)
        from PyQt6.QtWidgets import QStackedWidget
        self.posts_stack = QStackedWidget()
        self.posts_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.posts_stack.setMinimumHeight(0)

        # Grid view
        self.grid_view = PostGridView()
        self.grid_view.post_clicked.connect(self._on_post_clicked)
        self.grid_view.creator_clicked.connect(self._on_creator_requested)
        self.grid_view.load_more.connect(self._load_more_posts)
        self.grid_view.selection_changed.connect(self._on_posts_selection_changed)
        self.grid_view.set_creator_lookup(self._resolve_creator_name)
        self.grid_view.set_creators_manager(self.creators_manager)
        self.posts_stack.addWidget(self.grid_view)

        # Detail view (gallery-focused) - lazy initialization
        self.detail_view = None  # Will be created on first use

        layout.addWidget(self.posts_stack, 1)

        # Posts pagination
        self.posts_pagination_container = QWidget()
        self.posts_pagination_container.setObjectName("paginationContainer")
        self.posts_pagination_container.setFixedHeight(Spacing.PAGINATION_HEIGHT)
        pagination_layout = QHBoxLayout(self.posts_pagination_container)
        pagination_layout.setContentsMargins(20, 6, 20, 6)

        self.posts_pagination = EnhancedPagination()
        self.posts_pagination.page_changed.connect(self._on_posts_page_changed)
        pagination_layout.addWidget(self.posts_pagination)

        # Result count
        self.posts_result_label = QLabel("0 posts")
        self.posts_result_label.setObjectName("postsResultLabel")
        pagination_layout.addWidget(self.posts_result_label)

        layout.addWidget(self.posts_pagination_container, 0)

        return container

    def _create_posts_header(self) -> QWidget:
        """Create posts area header with search"""
        header = QWidget()
        header.setObjectName("postsHeader")
        header.setFixedHeight(Spacing.HEADER_HEIGHT)
        header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header.setMinimumWidth(0)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # Master Toggle Button (select/deselect all)
        self.master_selection_btn = QPushButton()
        self.master_selection_btn.setCheckable(True)
        self.master_selection_btn.setFixedSize(Spacing.BTN_LG, Spacing.BTN_LG)
        self.master_selection_btn.setIcon(qta.icon('fa5s.square')) 
        self.master_selection_btn.clicked.connect(self._handle_master_toggle)
        self.master_selection_btn.setToolTip("Select/Deselect all")
        self.master_selection_btn.setVisible(True)
        layout.addWidget(self.master_selection_btn)

        # Title / Creator name
        self.posts_title_label = QLabel("All Posts")
        self.posts_title_label.setObjectName("postsTitleLabel")
        layout.addWidget(self.posts_title_label)

        # Search bar (All mode - shows after title)
        self.search_bar = QLineEdit()
        self.search_bar.setObjectName("postsSearchBar")
        self.search_bar.setPlaceholderText("Search posts...")
        self.search_bar.setFixedWidth(Spacing.CARD_WIDTH)  # ~240
        self.search_bar.setFixedHeight(Spacing.CONTROL_HEIGHT)
        self.search_bar.returnPressed.connect(self._on_search)
        layout.addWidget(self.search_bar)

        # Tag selector button
        self.tag_selector_btn = QPushButton()
        self.tag_selector_btn.setIcon(qta.icon('fa5s.tags'))
        self.tag_selector_btn.setText("")
        self.tag_selector_btn.setObjectName("tagSelectorButton")
        self.tag_selector_btn.setFixedSize(Spacing.BTN_LG, Spacing.BTN_LG)
        self.tag_selector_btn.clicked.connect(self._show_tag_selector)
        self.tag_selector_btn.setToolTip("Select tags to filter posts")
        layout.addWidget(self.tag_selector_btn)

        # Tag selector badge (overlay count)
        self.tag_selector_badge = QLabel(self.tag_selector_btn)
        self.tag_selector_badge.setObjectName("tagSelectorBadge")
        self.tag_selector_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tag_selector_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.tag_selector_badge.setVisible(False)
        self.tag_selector_badge.setStyleSheet(
            f"""
            QLabel#tagSelectorBadge {{
                background-color: {Colors.ACCENT_SECONDARY};
                color: {Colors.TEXT_WHITE};
                border: 1px solid {Colors.BG_PRIMARY};
                border-radius: 8px;
                font-size: {Fonts.SIZE_XS}px;
                font-weight: {Fonts.WEIGHT_BOLD};
                padding: 0px 4px;
            }}
            """
        )

        # Tag chips container (for showing selected tags)
        from PyQt6.QtWidgets import QWidget as QW
        self.tag_chips_container = QW()
        self.tag_chips_container.setObjectName("tagChipsContainer")
        self.tag_chips_layout = QHBoxLayout(self.tag_chips_container)
        self.tag_chips_layout.setContentsMargins(0, 0, 0, 0)
        self.tag_chips_layout.setSpacing(6)
        self.tag_chips_container.setVisible(False)  # Hidden until tags are selected
        layout.addWidget(self.tag_chips_container)

        layout.addStretch()

        # === FILTERS GROUP ===
        
        # Posts mode
        self.posts_mode_combo = QComboBox()
        self.posts_mode_combo.setObjectName("postsModeCombo")
        
        # Add mode items with icons
        mode_items = [
            ("All posts", "fa5s.th", "all", "#10b981"),
            ("Popular", "fa5s.fire", "popular", "#f59e0b")
        ]
        for display_name, icon_name, mode_value, color in mode_items:
            icon = qta.icon(icon_name, color=color)
            self.posts_mode_combo.addItem(icon, display_name, mode_value)
        
        self.posts_mode_combo.setFixedHeight(Spacing.CONTROL_HEIGHT)
        self.posts_mode_combo.setFixedWidth(140)
        self.posts_mode_combo.currentIndexChanged.connect(self._on_posts_mode_changed)
        self.posts_mode_combo.setToolTip("Select post feed mode")
        layout.addWidget(self.posts_mode_combo)

        # Service dropdown (for posts)
        self.posts_service_combo = QComboBox()
        self.posts_service_combo.setObjectName("postsServiceCombo")
        
        # Add service items with logo icons
        for service in ['all', 'onlyfans', 'fansly', 'candfans']:
            icon = self._get_service_icon(service)
            display_name = service.capitalize() if service != 'all' else 'All services'
            self.posts_service_combo.addItem(icon, display_name, service)
        
        self.posts_service_combo.setFixedHeight(Spacing.CONTROL_HEIGHT)
        self.posts_service_combo.setFixedWidth(160)
        self.posts_service_combo.currentIndexChanged.connect(self._on_posts_service_changed)
        self.posts_service_combo.setToolTip("Filter posts by service")
        layout.addWidget(self.posts_service_combo)

        # Filter button (shows popup with mode-specific controls)
        self.filter_toggle_btn = QPushButton()
        self.filter_toggle_btn.setIcon(qta.icon('fa5s.filter'))
        self.filter_toggle_btn.setFixedSize(Spacing.BTN_LG, Spacing.BTN_LG)
        self.filter_toggle_btn.clicked.connect(self._show_filter_popup)
        self.filter_toggle_btn.setToolTip("Show advanced filters")
        layout.addWidget(self.filter_toggle_btn)

        # Show All button (reset filters)
        self.posts_show_all_btn = QPushButton("Clear")
        self.posts_show_all_btn.setObjectName("postsShowAllButton")
        self.posts_show_all_btn.setFixedSize(70, Spacing.CONTROL_HEIGHT)
        self.posts_show_all_btn.clicked.connect(self._on_posts_show_all)
        self.posts_show_all_btn.setToolTip("Reset all filters (service, creator, search)")
        self.posts_show_all_btn.setVisible(False)  # Hidden until filters are applied
        layout.addWidget(self.posts_show_all_btn)

        # Random post button
        self.random_post_btn = QPushButton()
        self.random_post_btn.setObjectName("randomPostButton")
        self.random_post_btn.setIcon(qta.icon('fa5s.dice'))
        self.random_post_btn.setFixedSize(Spacing.BTN_LG, Spacing.BTN_LG)
        self.random_post_btn.clicked.connect(self._on_random_post)
        self.random_post_btn.setToolTip("Random post")
        layout.addWidget(self.random_post_btn)

        # Refresh button
        refresh_btn = QPushButton()
        refresh_btn.setObjectName("refreshButton")
        refresh_btn.setIcon(qta.icon('fa5s.sync-alt'))
        refresh_btn.setFixedSize(Spacing.BTN_LG, Spacing.BTN_LG)
        refresh_btn.clicked.connect(self._refresh_posts)
        refresh_btn.setToolTip("Refresh posts")
        layout.addWidget(refresh_btn)

        # Bulk download controls
        self.selected_count_label = QLabel("0 selected")
        self.selected_count_label.setObjectName("selectedCountLabel")
        self.selected_count_label.setVisible(False)
        layout.addWidget(self.selected_count_label)

        # Download selected button
        self.download_selected_btn = QPushButton()
        self.download_selected_btn.setIcon(qta.icon('fa5s.download'))
        self.download_selected_btn.setFixedSize(Spacing.BTN_LG, Spacing.BTN_LG)
        self.download_selected_btn.clicked.connect(self._download_selected_posts)
        self.download_selected_btn.setToolTip("Download selected posts")
        self.download_selected_btn.setVisible(False)
        layout.addWidget(self.download_selected_btn)
        
        # Create filter popup (initially hidden)
        self._create_filter_popup()

        # Create tag selector popup (initially hidden)
        self._create_tag_selector_popup()

        return header

    def _sync_posts_header_height(self) -> None:
        if not hasattr(self, "posts_header") or not hasattr(self, "creators_sidebar"):
            return
        try:
            top_height = self.creators_sidebar.get_header_height()
        except Exception:
            top_height = None
        if top_height:
            self.posts_header.setFixedHeight(top_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_sidebar_toggle_tab()
    
    def moveEvent(self, event):
        super().moveEvent(event)
        self._position_sidebar_toggle_tab()
    
    def _create_filter_popup(self):
        """Create popup widget for mode-specific filters"""
        self.filter_popup = QWidget(self, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.filter_popup.setObjectName("filterPopup")
        self.filter_popup.setFixedWidth(300)
        
        popup_layout = QVBoxLayout(self.filter_popup)
        popup_layout.setContentsMargins(12, 12, 12, 12)
        popup_layout.setSpacing(8)

        # Tabs for filters vs popular controls
        self.filter_tabs = QTabWidget()
        self.filter_tabs.setObjectName("filterTabs")
        self.filter_tabs.setDocumentMode(True)
        self.filter_tabs.setTabPosition(QTabWidget.TabPosition.North)
        popup_layout.addWidget(self.filter_tabs)

        # Filters tab
        self.filters_tab = QWidget()
        self.filters_tab.setObjectName("filtersTab")
        filters_layout = QVBoxLayout(self.filters_tab)
        filters_layout.setContentsMargins(0, 0, 0, 0)
        filters_layout.setSpacing(12)
        self.filter_tabs.addTab(self.filters_tab, "Filters")

        # Popular tab
        self.popular_tab = QWidget()
        self.popular_tab.setObjectName("popularTab")
        popular_layout = QVBoxLayout(self.popular_tab)
        popular_layout.setContentsMargins(0, 0, 0, 0)
        popular_layout.setSpacing(12)
        self.filter_tabs.addTab(self.popular_tab, "Popular")

        # ── Content filters section ──
        self._content_filter_separator = QFrame()
        self._content_filter_separator.setFrameShape(QFrame.Shape.HLine)
        self._content_filter_separator.setStyleSheet(f"color: {Colors.BORDER_LIGHT};")
        filters_layout.addWidget(self._content_filter_separator)

        content_label = QLabel("Content Type")
        content_label.setObjectName("filterSectionLabel")
        content_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        content_label.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; font-size: {Fonts.SIZE_XS}px; font-weight: 600;"
        )
        filters_layout.addWidget(content_label)
        self._content_filter_label = content_label

        # Segmented toggle: All | Images | Videos
        seg_style = (
            "QPushButton {"
            f"  background-color: transparent;"
            f"  color: {Colors.TEXT_SECONDARY};"
            f"  border: 1px solid {Colors.BORDER_LIGHT};"
            f"  padding: 4px 12px;"
            f"  font-size: {Fonts.SIZE_XS}px;"
            "}"
            "QPushButton:checked {"
            f"  background-color: {Colors.ACCENT_PRIMARY};"
            f"  color: {Colors.TEXT_WHITE};"
            f"  border-color: {Colors.ACCENT_PRIMARY};"
            "}"
            "QPushButton:hover:!checked {"
            f"  background-color: {Colors.BG_HOVER};"
            "}"
        )

        content_row = QWidget()
        content_row.setObjectName("contentTypeRow")
        content_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        content_row_layout = QHBoxLayout(content_row)
        content_row_layout.setContentsMargins(0, 0, 0, 0)
        content_row_layout.setSpacing(0)

        self._content_type_group = QButtonGroup(self)
        self._content_type_group.setExclusive(True)

        ct_buttons = [
            ("All", "all"),
            ("Images", "images"),
            ("Videos", "videos"),
        ]
        self._content_type_btns = {}
        for i, (label, value) in enumerate(ct_buttons):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(Spacing.CONTROL_HEIGHT)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            # Round left corners on first, right on last, flat in middle
            if i == 0:
                btn.setStyleSheet(seg_style + "QPushButton { border-top-right-radius: 0; border-bottom-right-radius: 0; border-top-left-radius: 4px; border-bottom-left-radius: 4px; }")
            elif i == len(ct_buttons) - 1:
                btn.setStyleSheet(seg_style + "QPushButton { border-top-left-radius: 0; border-bottom-left-radius: 0; border-top-right-radius: 4px; border-bottom-right-radius: 4px; border-left: none; }")
            else:
                btn.setStyleSheet(seg_style + "QPushButton { border-radius: 0; border-left: none; }")
            if value == "all":
                btn.setChecked(True)
            self._content_type_group.addButton(btn)
            self._content_type_btns[value] = btn
            content_row_layout.addWidget(btn)

        self._content_type_group.buttonClicked.connect(self._on_content_type_changed)
        filters_layout.addWidget(content_row)
        self._content_type_row = content_row

        # ── Duration filter section ──
        dur_label = QLabel("Duration")
        dur_label.setObjectName("filterSectionLabel")
        dur_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        dur_label.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; font-size: {Fonts.SIZE_XS}px; font-weight: 600;"
        )
        filters_layout.addWidget(dur_label)
        self._duration_filter_label = dur_label

        dur_row = QWidget()
        dur_row.setObjectName("durationRow")
        dur_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        dur_row_layout = QHBoxLayout(dur_row)
        dur_row_layout.setContentsMargins(0, 0, 0, 0)
        dur_row_layout.setSpacing(0)

        dur_buttons = [
            ("Any", "any"),
            ("< 1m", "<1"),
            ("1-5m", "1-5"),
            ("5-30m", "5-30"),
            ("30m+", "30+"),
        ]
        self._duration_btns = {}
        for i, (label, value) in enumerate(dur_buttons):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(Spacing.CONTROL_HEIGHT)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if i == 0:
                btn.setStyleSheet(seg_style + "QPushButton { border-top-right-radius: 0; border-bottom-right-radius: 0; border-top-left-radius: 4px; border-bottom-left-radius: 4px; }")
            elif i == len(dur_buttons) - 1:
                btn.setStyleSheet(seg_style + "QPushButton { border-top-left-radius: 0; border-bottom-left-radius: 0; border-top-right-radius: 4px; border-bottom-right-radius: 4px; border-left: none; }")
            else:
                btn.setStyleSheet(seg_style + "QPushButton { border-radius: 0; border-left: none; }")
            if value == "any":
                btn.setChecked(True)
            btn.clicked.connect(lambda checked, v=value: self._on_duration_filter_changed(v))
            self._duration_btns[value] = btn
            dur_row_layout.addWidget(btn)
        filters_layout.addWidget(dur_row)
        self._duration_row = dur_row
        filters_layout.addStretch(1)

        # Duration controls start disabled (enabled when "Videos" content type selected)
        self._set_duration_filter_enabled(False)

        # Popular mode controls
        self.popular_period_combo = QComboBox()
        self.popular_period_combo.setObjectName("popularPeriodCombo")
        self.popular_period_combo.addItems(["recent", "day", "week", "month"])
        self.popular_period_combo.setFixedHeight(Spacing.CONTROL_HEIGHT)
        self.popular_period_combo.currentTextChanged.connect(self._on_popular_changed)
        self.popular_period_combo.setToolTip("Time period for popular posts")
        popular_layout.addWidget(self.popular_period_combo)

        # Navigation controls container (prev, datepicker, next) - segmented control style
        nav_container = QWidget()
        nav_container.setObjectName("popularDateRow")
        nav_layout = QHBoxLayout(nav_container)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(0)

        # Prev button: rounded left, flat right
        prev_btn_style = (
            "QPushButton {"
            f"  background-color: transparent;"
            f"  color: {Colors.TEXT_PRIMARY};"
            f"  border: none;"
            f"  border-top-left-radius: 4px;"
            f"  border-bottom-left-radius: 4px;"
            f"  border-top-right-radius: 0px;"
            f"  border-bottom-right-radius: 0px;"
            "}"
            "QPushButton:hover {"
            f"  background-color: {Colors.BG_HOVER};"
            "}"
            "QPushButton:disabled {"
            f"  background-color: {Colors.STATE_DISABLED_BG};"
            f"  color: {Colors.TEXT_DISABLED};"
            "}"
        )
        self.popular_prev_btn = QPushButton()
        self.popular_prev_btn.setIcon(qta.icon('fa5s.chevron-left'))
        self.popular_prev_btn.setFixedSize(Spacing.BTN_LG, Spacing.BTN_LG)
        self.popular_prev_btn.clicked.connect(self._on_popular_prev)
        self.popular_prev_btn.setToolTip("Previous period")
        self.popular_prev_btn.setStyleSheet(prev_btn_style)
        nav_layout.addWidget(self.popular_prev_btn)

        # Date input: flat on both sides
        date_input_style = (
            "QDateEdit {"
            f"  background-color: transparent;"
            f"  color: {Colors.TEXT_PRIMARY};"
            f"  border: none;"
            f"  border-radius: 0px;"
            f"  padding: 0 8px;"
            "}"
            "QDateEdit:disabled {"
            f"  background-color: {Colors.STATE_DISABLED_BG};"
            f"  color: {Colors.TEXT_DISABLED};"
            "}"
        )
        self.popular_date_input = QDateEdit()
        self.popular_date_input.setObjectName("popularDateInput")
        self.popular_date_input.setCalendarPopup(False)
        self.popular_date_input.setDisplayFormat("yyyy-MM-dd")
        self.popular_date_input.setDate(QDate.currentDate())
        self.popular_date_input.setFixedHeight(Spacing.CONTROL_HEIGHT)
        self.popular_date_input.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.popular_date_input.setMaximumDate(QDate.currentDate())
        self.popular_date_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.popular_date_input.dateChanged.connect(self._on_popular_date_changed)
        self.popular_date_input.setStyleSheet(date_input_style)
        nav_layout.addWidget(self.popular_date_input, 1)

        # Next button: flat left, rounded right
        next_btn_style = (
            "QPushButton {"
            f"  background-color: transparent;"
            f"  color: {Colors.TEXT_PRIMARY};"
            f"  border: none;"
            f"  border-top-left-radius: 0px;"
            f"  border-bottom-left-radius: 0px;"
            f"  border-top-right-radius: 4px;"
            f"  border-bottom-right-radius: 4px;"
            "}"
            "QPushButton:hover {"
            f"  background-color: {Colors.BG_HOVER};"
            "}"
            "QPushButton:disabled {"
            f"  background-color: {Colors.STATE_DISABLED_BG};"
            f"  color: {Colors.TEXT_DISABLED};"
            "}"
        )
        self.popular_next_btn = QPushButton()
        self.popular_next_btn.setIcon(qta.icon('fa5s.chevron-right'))
        self.popular_next_btn.setFixedSize(Spacing.BTN_LG, Spacing.BTN_LG)
        self.popular_next_btn.clicked.connect(self._on_popular_next)
        self.popular_next_btn.setToolTip("Next period")
        self.popular_next_btn.setStyleSheet(next_btn_style)
        nav_layout.addWidget(self.popular_next_btn)
        
        self.popular_nav_container = nav_container
        popular_layout.addWidget(nav_container)

        # Always-visible calendar (shown under the date input)
        self.popular_calendar = M3DockedCalendar()
        self.popular_calendar.setObjectName("popularCalendar")
        self.popular_calendar.setSelectedDate(self.popular_date_input.date())
        self.popular_calendar.setMaximumDate(QDate.currentDate())
        self.popular_calendar.selectionChanged.connect(self._on_popular_calendar_changed)
        popular_layout.addWidget(self.popular_calendar)
        
        # Tags controls (placeholder for future tag autocomplete)
        self.tags_input = QLineEdit()
        self.tags_input.setObjectName("tagsInput")
        self.tags_input.setPlaceholderText("tag1, tag2")
        self.tags_input.setFixedHeight(Spacing.CONTROL_HEIGHT)
        self.tags_input.returnPressed.connect(self._on_tags_search)
        self.tags_input.setVisible(False)
        popular_layout.addWidget(self.tags_input)

        # Style the popup using theme
        self.filter_popup.setStyleSheet(Styles.filter_popup())

        # Initially hide popup
        self.filter_popup.hide()

    def _create_tag_selector_popup(self):
        """Create tag selector popup widget"""
        from src.ui.widgets.tag_selector_popup import TagSelectorPopup
        
        self.tag_selector_popup = TagSelectorPopup(self)
        self.tag_selector_popup.tags_changed.connect(self._on_tags_changed)
        self.tag_selector_popup.hide()

    def _create_toolbar(self):
        """Create top toolbar (unused)"""
        return

    def _create_statusbar(self):
        """Create status bar"""
        self.posts_spinner = QToolButton()
        self.posts_spinner.setObjectName("postsSpinner")
        self.posts_spinner.setAutoRaise(True)
        self.posts_spinner.setEnabled(True)
        self.posts_spinner.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.posts_spinner.setFixedSize(22, 22)
        self.posts_spinner.setIconSize(QSize(16, 16))
        self.posts_spinner.setStyleSheet(Styles.icon_button_flat())
        self._posts_spinner_anim = qta.Spin(self.posts_spinner)
        self.posts_spinner.setIcon(
            qta.icon("fa5s.spinner", color="#ff6b35", animation=self._posts_spinner_anim)
        )
        self.posts_spinner.setVisible(False)
        self.statusBar().addWidget(self.posts_spinner)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("statusLabel")
        self.statusBar().addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("mainProgressBar")
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress_bar)

    def _create_menus(self):
        """Create menu bar"""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("File")

        settings_action = QAction("Settings", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self._show_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # View menu
        view_menu = menubar.addMenu("View")

        toggle_sidebar_action = QAction("Toggle Creators Sidebar", self)
        toggle_sidebar_action.setShortcut(QKeySequence("Ctrl+B"))
        toggle_sidebar_action.triggered.connect(self._toggle_sidebar)
        view_menu.addAction(toggle_sidebar_action)

        # Help menu
        help_menu = menubar.addMenu("Help")

        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

        # Platform menu
        platform_menu = menubar.addMenu("Platform")
        self._platform_action_group = QActionGroup(self)
        self._platform_action_group.setExclusive(True)
        self._platform_actions = {}
        for platform in ["coomer", "kemono"]:
            action = QAction(platform.capitalize(), self)
            action.setCheckable(True)
            if platform == self.current_platform:
                action.setChecked(True)
            action.triggered.connect(lambda checked, p=platform: self._on_platform_changed(p))
            self._platform_action_group.addAction(action)
            platform_menu.addAction(action)
            self._platform_actions[platform] = action

    def _setup_shortcuts(self):
        """Setup keyboard shortcuts"""
        # Refresh: F5
        refresh_shortcut = QAction(self)
        refresh_shortcut.setShortcut(QKeySequence("F5"))
        refresh_shortcut.triggered.connect(self._refresh_posts)
        self.addAction(refresh_shortcut)

        # Search: Ctrl+F
        search_shortcut = QAction(self)
        search_shortcut.setShortcut(QKeySequence("Ctrl+F"))
        search_shortcut.triggered.connect(lambda: self.search_bar.setFocus())
        self.addAction(search_shortcut)

    def _toggle_sidebar(self):
        """Toggle creators sidebar visibility"""
        self.sidebar_visible = not self.sidebar_visible
        if self.sidebar_visible:
            self.creators_sidebar.setMinimumWidth(Spacing.SIDEBAR_WIDTH)
            self.creators_sidebar.setMaximumWidth(Spacing.SIDEBAR_WIDTH)
            total = max(1, self.splitter.width())
            self.splitter.setSizes([Spacing.SIDEBAR_WIDTH, max(1, total - Spacing.SIDEBAR_WIDTH)])
        else:
            self.creators_sidebar.setMinimumWidth(0)
            self.creators_sidebar.setMaximumWidth(Spacing.SIDEBAR_WIDTH)
            total = max(1, self.splitter.width())
            self.splitter.setSizes([0, total])
        self._update_sidebar_toggle_icon()
        QTimer.singleShot(0, self._position_sidebar_toggle_tab)

    def _update_sidebar_toggle_icon(self) -> None:
        if not hasattr(self, "sidebar_toggle_btn"):
            return
        icon_name = "fa5s.chevron-left" if self.sidebar_visible else "fa5s.user"
        self.sidebar_toggle_btn.setIcon(qta.icon(icon_name, color="#909090"))

    def _position_sidebar_toggle_tab(self) -> None:
        if not hasattr(self, "sidebar_toggle_btn"):
            return
        handle = self.splitter.handle(1)
        if handle is None:
            return
        parent = getattr(self, "_central_widget", self)
        handle_pos = handle.mapTo(parent, QPoint(0, 0))
        geom = self.creators_sidebar.geometry()
        x = max(0, handle_pos.x())
        tabs_widget = getattr(self.creators_sidebar, "tabs_widget", None)
        if self.sidebar_visible and tabs_widget is not None and tabs_widget.height() > 0:
            tabs_pos = tabs_widget.mapTo(parent, QPoint(0, 0))
            y = max(0, tabs_pos.y() + (tabs_widget.height() - self.sidebar_toggle_btn.height()) // 2)
            self._sidebar_toggle_y = y
            x = x+3  # Nudge right when visible to align with tab content
        elif hasattr(self, "_sidebar_toggle_y"):
            y = self._sidebar_toggle_y
        else:
            split_geom = self.splitter.geometry()
            y = max(0, split_geom.top() + (split_geom.height() - self.sidebar_toggle_btn.height()) // 2)
        self.sidebar_toggle_btn.move(x, y + 5)
        self.sidebar_toggle_btn.raise_()

    def _set_sidebar_toggle_overlay_root(self, overlay_root: QWidget | None):
        if not hasattr(self, "sidebar_toggle_btn"):
            return
        if overlay_root is not None:
            self._sidebar_toggle_overlay_root = overlay_root
            self.sidebar_toggle_btn.setVisible(False)
        else:
            self._sidebar_toggle_overlay_root = None
            self.sidebar_toggle_btn.setVisible(True)
        self._position_sidebar_toggle_tab()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._position_sidebar_toggle_tab)
        QTimer.singleShot(0, self._sync_posts_header_height)

    def _on_platform_changed(self, platform: str):
        """Handle platform change"""
        if hasattr(self, "_platform_actions"):
            for key, action in self._platform_actions.items():
                action.blockSignals(True)
                action.setChecked(key == platform)
                action.blockSignals(False)
        if platform != self.current_platform:
            # Cancel all pending async operations before switching
            self._cleanup_grid_view()
            if self.detail_view is not None:
                self._cleanup_detail_view()
            
            # Batch all updates to prevent intermediate repaints
            self.setUpdatesEnabled(False)
            try:
                self.current_platform = platform
                self.current_offset = 0
                self.current_creator_id = None
                self.current_creator_name = None
                self._creator_service = None
                self.current_tags = []
                self.current_popular_date = None

                # Update posts service dropdown
                self.posts_service_combo.blockSignals(True)
                self.posts_service_combo.clear()
                if platform == 'kemono':
                    services = ['all', 'patreon', 'fanbox', 'fantia', 'boosty', 'gumroad', 'subscribestar', 'dlsite']
                else:
                    services = ['all', 'onlyfans', 'fansly', 'candfans']
                
                # Re-add services with logo icons
                for service in services:
                    icon = self._get_service_icon(service)
                    display_name = service.capitalize() if service != 'all' else 'All services'
                    self.posts_service_combo.addItem(icon, display_name, service)
                
                self.current_service = services[0]
                self.posts_service_combo.blockSignals(False)

                # Update creators sidebar
                self.creators_sidebar.set_platform(platform)
            finally:
                self.setUpdatesEnabled(True)

            # Load tags for new platform
            self._load_tags()
            
            # Refresh posts after UI is stable
            self._load_posts()

    def _on_sidebar_service_changed(self, service: str):
        """Handle service change from sidebar - only affects creator search, not posts"""
        # Sidebar service filter is independent from posts service filter
        pass

    def _on_posts_service_changed(self, index: int):
        """Handle service change from posts dropdown"""
        service = self.posts_service_combo.currentData()
        if service and service != self.current_service:
            # If viewing a creator's posts, clear creator first and return to all/popular posts
            if self.current_creator_id:
                self.current_creator_id = None
                self.current_creator_name = None
                self._creator_service = None
                # Update title based on mode
                if self.current_posts_mode == "popular":
                    self.posts_title_label.setText("Popular Posts")
                else:
                    self.posts_title_label.setText("All Posts")
                # Clear sidebar selection
                if hasattr(self.creators_sidebar, "clear_selection"):
                    self.creators_sidebar.clear_selection()

            self.current_service = service
            self.current_offset = 0
            self._update_show_all_button_visibility()
            self._load_posts()

    def _resolve_creator_name(self, platform: str, service: str, creator_id: str):
        """Resolve creator name using centralized utility"""
        return resolve_creator_from_manager(
            self.creators_manager, platform, service, creator_id
        )

    def _on_creator_requested(self, creator_data: Dict):
        if not creator_data:
            return
        self._on_creator_selected(creator_data)

    def _on_creator_selected(self, creator_data: Dict):
        """Handle creator selection from sidebar"""
        if self.posts_stack.currentWidget() is self.detail_view:
            self._show_grid_view()
        self.current_creator_id = creator_data.get('creator_id') or creator_data.get('id')
        self.current_creator_name = creator_data.get('name')
        # Use creator's service for loading their posts, but don't sync UI filters
        self._creator_service = creator_data.get('service')
        self.current_offset = 0
        
        # Clear search + filters when switching creators
        self.current_query = ""
        self.search_bar.clear()
        self.current_tags = []
        self.tags_input.clear()
        self._update_tag_chips()
        self._update_tag_selector_badge()
        if hasattr(self, 'tag_selector_popup'):
            self.tag_selector_popup.set_tags(self.available_tags, [])
        self._content_type_filter = "all"
        self._content_type_btns["all"].setChecked(True)
        self._set_duration_filter_enabled(False)
        if self.current_posts_mode != "all":
            self.posts_mode_combo.setCurrentText("All")

        # Update posts title
        self.posts_title_label.setText(f"Posts by {self.current_creator_name}")

        # Don't sync service filters - they remain independent
        if hasattr(self.creators_sidebar, "select_creator"):
            self.creators_sidebar.select_creator(creator_data)

        # Show Show All button when creator is selected
        self._update_show_all_button_visibility()
        
        # Load creator-specific tags
        self._load_tags()
        
        # Load creator's posts
        self._load_posts()

    def _on_creator_cleared(self):
        """Handle clearing creator filter from sidebar"""
        self.current_creator_id = None
        self.current_creator_name = None
        self._creator_service = None
        self.current_offset = 0

        # Update title
        if self.current_posts_mode == "popular":
            self.posts_title_label.setText("Popular Posts")
        elif self.current_posts_mode == "tags":
            self.posts_title_label.setText("Tagged Posts")
        else:
            self.posts_title_label.setText("All Posts")

        # Update Show All button visibility
        self._update_show_all_button_visibility()

        # Refresh posts
        self._load_posts()

    def _on_unsupported_service(self, service: str, creator_name: str):
        """Handle clicking a creator with an unsupported service"""
        self.toast.show_message(
            f"Service '{service}' is not supported for browsing posts.\n"
            f"Creator: {creator_name}",
            duration=4000
        )

    def _on_posts_show_all(self):
        """Reset all post filters (service, creator, search)"""
        # Reset service to 'all'
        self.posts_service_combo.blockSignals(True)
        for i in range(self.posts_service_combo.count()):
            if self.posts_service_combo.itemData(i) == 'all':
                self.posts_service_combo.setCurrentIndex(i)
                break
        self.posts_service_combo.blockSignals(False)
        self.current_service = 'all'
        
        # Reset creator filter
        self.current_creator_id = None
        self.current_creator_name = None
        self._creator_service = None

        # Clear search
        self.current_query = ""
        self.search_bar.clear()
        
        # Clear tags
        self.current_tags = []
        self._update_tag_chips()
        
        # Reload platform-wide tags
        self._load_tags()
        self.current_tags = []
        self._update_tag_chips()
        self._update_tag_selector_badge()
        if hasattr(self, 'tag_selector_popup'):
            self.tag_selector_popup.set_tags(self.available_tags, [])
        
        # Clear creator selection in sidebar
        self.creators_sidebar.clear_selection()

        # Reset content type / duration filters
        self._content_type_filter = "all"
        self._content_type_btns["all"].setChecked(True)
        self._set_duration_filter_enabled(False)

        # Reset offset
        self.current_offset = 0
        
        # Update title
        if self.current_posts_mode == "popular":
            self.posts_title_label.setText("Popular Posts")
        elif self.current_posts_mode == "tags":
            self.posts_title_label.setText("Tagged Posts")
        else:
            self.posts_title_label.setText("All Posts")
        
        # Hide Show All button
        self.posts_show_all_btn.setVisible(False)
        
        # Refresh posts
        self._load_posts()
    
    def _update_show_all_button_visibility(self):
        """Show/hide Show All button based on active filters"""
        has_filters = (
            self.current_service != 'all' or
            self.current_creator_id is not None or
            self.current_query.strip() != "" or
            len(self.current_tags) > 0 or
            self._content_type_filter != "all" or
            bool(self._duration_filters)
        )
        self.posts_show_all_btn.setVisible(has_filters)

    def _on_search(self):
        """Handle search"""
        self.current_query = self.search_bar.text().strip()
        self.current_offset = 0
        # Keep creator context — search within creator posts via ?q= param
        self._update_show_all_button_visibility()
        self._load_posts()

    def _on_posts_mode_changed(self, index: int):
        """Handle mode change from dropdown"""
        mode = self.posts_mode_combo.currentData()
        if mode:
            self._set_posts_mode(mode)

    def _set_posts_mode(self, mode: str) -> None:
        self.current_posts_mode = mode
        self.current_offset = 0
        if mode in ("popular", "tags"):
            self.current_creator_id = None
            self.current_creator_name = None
            self._creator_service = None
            self.creators_sidebar.clear_selection()

        # Update title
        if mode == "popular":
            self.posts_title_label.setText("Popular Posts")
            self._sync_popular_controls()
        elif mode == "tags":
            self.posts_title_label.setText("Tagged Posts")
        else:
            self.posts_title_label.setText("All Posts")
        
        # Update search bar visibility (only for "All" mode)
        is_all = (mode == "all")
        self.search_bar.setVisible(is_all)
        
        # Filter button always visible (content type + duration filters apply to all modes)
        self.filter_toggle_btn.setVisible(True)

        self._load_posts()
    
    def _show_filter_popup(self):
        """Show filter popup below the filter button"""
        if not self.filter_popup.isVisible():
            # Update popup content based on current mode
            self._update_filter_popup_content()
            
            # Position popup below the filter button
            button_pos = self.filter_toggle_btn.mapToGlobal(QPoint(0, 0))
            popup_x = button_pos.x()
            popup_y = button_pos.y() + self.filter_toggle_btn.height() + 2

            self.filter_popup.adjustSize()
            popup_width = self.filter_popup.width()
            popup_height = self.filter_popup.height()

            window_rect = self.frameGeometry()
            min_x = window_rect.left()
            max_x = window_rect.right() - popup_width + 1
            min_y = window_rect.top()
            max_y = window_rect.bottom() - popup_height + 1

            popup_x = max(min_x, min(popup_x, max_x))
            popup_y = max(min_y, min(popup_y, max_y))
            
            self.filter_popup.move(popup_x, popup_y)
            self.filter_popup.show()
        else:
            self.filter_popup.hide()
    
    def _update_filter_popup_content(self):
        """Update which controls are visible in the filter popup based on current mode"""
        is_popular = (self.current_posts_mode == "popular")
        
        # Popular mode controls
        self.popular_period_combo.setVisible(is_popular)
        
        # Navigation controls visibility based on period
        if is_popular:
            period = self.popular_period_combo.currentText()
            enable_nav = (period in ["day", "week", "month"])
            self.popular_nav_container.setVisible(True)
            self.popular_nav_container.setEnabled(enable_nav)
            self.popular_date_input.setEnabled(enable_nav)
            self.popular_calendar.setVisible(True)
            self.popular_calendar.setEnabled(enable_nav)
        else:
            self.popular_nav_container.setVisible(False)
            self.popular_calendar.setVisible(False)
        
        # Tags controls (future)
        self.tags_input.setVisible(False)

        # Content type + duration filters — always visible
        self._content_filter_separator.setVisible(True)
        self._content_filter_label.setVisible(True)
        self._content_type_row.setVisible(True)
        self._duration_filter_label.setVisible(True)
        self._duration_row.setVisible(True)

        # Tabs: enable/disable popular tab and choose default
        try:
            popular_idx = self.filter_tabs.indexOf(self.popular_tab)
            if popular_idx >= 0:
                self.filter_tabs.setTabEnabled(popular_idx, is_popular)
                if is_popular:
                    self.filter_tabs.setCurrentIndex(popular_idx)
                else:
                    self.filter_tabs.setCurrentIndex(self.filter_tabs.indexOf(self.filters_tab))
        except Exception:
            pass

        # Resize popup to fit content
        self.filter_popup.adjustSize()

    # ── Content / duration filter logic ──

    def _set_duration_filter_enabled(self, enabled: bool):
        """Enable or disable the duration filter row."""
        self._duration_filter_label.setEnabled(enabled)
        for btn in self._duration_btns.values():
            btn.setEnabled(enabled)
        if not enabled:
            # Reset to "any" when disabled
            self._duration_filters = set()
            self._duration_btns["any"].setChecked(True)
            for key in ("<1", "1-5", "5-30", "30+"):
                self._duration_btns[key].setChecked(False)

    def _on_content_type_changed(self, _btn=None):
        """Handle content type toggle button click."""
        checked = self._content_type_group.checkedButton()
        for value, b in self._content_type_btns.items():
            if b is checked:
                self._content_type_filter = value
                break
        # Enable duration filter only when Videos is selected
        self._set_duration_filter_enabled(self._content_type_filter == "videos")
        self._reapply_content_filters()

    def _on_duration_filter_changed(self, value: str):
        """Handle duration range toggle button click."""
        if value == "any":
            # "Any" clicked — clear all specific ranges
            self._duration_filters = set()
            for key in ("<1", "1-5", "5-30", "30+"):
                self._duration_btns[key].setChecked(False)
            self._duration_btns["any"].setChecked(True)
        else:
            # Specific range toggled
            if self._duration_btns[value].isChecked():
                self._duration_filters.add(value)
            else:
                self._duration_filters.discard(value)
            # If nothing selected, revert to "Any"
            if not self._duration_filters:
                self._duration_btns["any"].setChecked(True)
            else:
                self._duration_btns["any"].setChecked(False)
        self._reapply_content_filters()

    @staticmethod
    def _post_has_images(post: 'PostDTO') -> bool:
        from src.ui.gallery.post_card import _is_image_file
        if post.file and _is_image_file(post.file):
            return True
        return any(_is_image_file(att) for att in post.attachments)

    @staticmethod
    def _post_has_videos(post: 'PostDTO') -> bool:
        from src.ui.gallery.post_card import _is_video_file
        if post.file and _is_video_file(post.file):
            return True
        return any(_is_video_file(att) for att in post.attachments)

    def _post_max_duration(self, post: 'PostDTO') -> Optional[float]:
        """Get the maximum video duration for a post (API data + DB cache)."""
        from src.ui.gallery.post_card import _DurationCache, _build_media_url, _is_video_file

        max_dur = 0.0
        cache = _DurationCache.instance()

        files = []
        if post.file:
            files.append(post.file)
        files.extend(post.attachments)

        for f in files:
            if not _is_video_file(f):
                continue
            # Check DTO field first
            if f.duration and f.duration > max_dur:
                max_dur = f.duration
                continue
            # Fall back to DB cache
            if f.path:
                url = _build_media_url(self.current_platform, f.path)
                cached = cache.get(url)
                if cached and cached > max_dur:
                    max_dur = cached

        return max_dur if max_dur > 0 else None

    _DURATION_RANGES = {
        "<1":   (0, 60),
        "1-5":  (60, 300),
        "5-30": (300, 1800),
        "30+":  (1800, float("inf")),
    }

    def _post_matches_duration(self, post: 'PostDTO') -> bool:
        """Check if a post's max video duration falls within any selected range."""
        dur = self._post_max_duration(post)
        if dur is None:
            return True  # include unprobed posts
        for key in self._duration_filters:
            lo, hi = self._DURATION_RANGES.get(key, (0, float("inf")))
            if lo <= dur < hi:
                return True
        return False

    def _apply_content_filters(self, posts: list) -> list:
        """Filter posts by content type and duration (client-side)."""
        filtered = list(posts)  # always copy to avoid aliasing current_posts
        if self._content_type_filter == "images":
            filtered = [p for p in filtered if self._post_has_images(p)]
        elif self._content_type_filter == "videos":
            filtered = [p for p in filtered if self._post_has_videos(p)]

        if self._content_type_filter == "videos" and self._duration_filters:
            filtered = [p for p in filtered if self._post_matches_duration(p)]

        return filtered

    def _reapply_content_filters(self):
        """Re-filter already-loaded posts and refresh the grid (no API call)."""
        if not self.current_posts:
            return
        posts_to_show = self._apply_content_filters(self.current_posts)
        self.grid_view.set_posts(posts_to_show, self.current_platform)
        self._update_show_all_button_visibility()

    def _sync_popular_controls(self) -> None:
        period = self.popular_period_combo.currentText().strip() or "recent"
        self.current_popular_period = period
        self.current_popular_date = self.popular_date_input.date().toString("yyyy-MM-dd")

        if self.current_posts_mode == "popular":
            self.posts_title_label.setText(
                f"Popular Posts ({self.current_popular_period}) • {self.current_popular_date}"
            )

    def _on_popular_changed(self, *args):
        self.current_popular_anchor = None
        self._sync_popular_controls()
        
        # Update navigation visibility based on period
        if hasattr(self, 'popular_nav_container'):
            period = self.popular_period_combo.currentText()
            enable_nav = (period in ["day", "week", "month"])
            self.popular_nav_container.setVisible(True)
            self.popular_nav_container.setEnabled(enable_nav)
            self.popular_date_input.setEnabled(enable_nav)
            if hasattr(self, "popular_calendar"):
                self.popular_calendar.setVisible(True)
                self.popular_calendar.setEnabled(enable_nav)
        
        self.current_offset = 0
        if self.current_posts_mode == "popular":
            self._load_posts()

    def _on_popular_date_changed(self, *args):
        self.current_popular_anchor = None
        if hasattr(self, "popular_calendar"):
            try:
                self.popular_calendar.blockSignals(True)
                self.popular_calendar.setSelectedDate(self.popular_date_input.date())
            finally:
                self.popular_calendar.blockSignals(False)
        self._sync_popular_controls()
        self.current_offset = 0
        if self.current_posts_mode == "popular":
            self._load_posts()

    def _on_popular_calendar_changed(self):
        if not hasattr(self, "popular_calendar"):
            return
        self.popular_date_input.setDate(self.popular_calendar.selectedDate())
    
    def _on_popular_prev(self):
        """Navigate to previous period in popular view"""
        if not self.current_popular_date:
            return
        
        from datetime import datetime, timedelta
        try:
            current = datetime.strptime(self.current_popular_date, "%Y-%m-%d")
            
            # Go back based on period
            if self.current_popular_period == "day":
                new_date = current - timedelta(days=1)
            elif self.current_popular_period == "week":
                new_date = current - timedelta(weeks=1)
            elif self.current_popular_period == "month":
                # Approximate month as 30 days
                new_date = current - timedelta(days=30)
            else:  # recent
                new_date = current - timedelta(days=1)
            
            self.popular_date_input.setDate(QDate(new_date.year, new_date.month, new_date.day))
            self._on_popular_date_changed()
        except Exception as e:
            logger.error(f"Error navigating to previous period: {e}")
    
    def _on_popular_next(self):
        """Navigate to next period in popular view"""
        if not self.current_popular_date:
            return
        
        from datetime import datetime, timedelta
        try:
            current = datetime.strptime(self.current_popular_date, "%Y-%m-%d")
            
            # Go forward based on period
            if self.current_popular_period == "day":
                new_date = current + timedelta(days=1)
            elif self.current_popular_period == "week":
                new_date = current + timedelta(weeks=1)
            elif self.current_popular_period == "month":
                # Approximate month as 30 days
                new_date = current + timedelta(days=30)
            else:  # recent
                new_date = current + timedelta(days=1)
            
            # Don't go beyond today
            today = datetime.now()
            if new_date > today:
                return
            
            self.popular_date_input.setDate(QDate(new_date.year, new_date.month, new_date.day))
            self._on_popular_date_changed()
        except Exception as e:
            logger.error(f"Error navigating to next period: {e}")

    def _show_tag_selector(self):
        """Show tag selector popup below the tag button"""
        if not self.available_tags:
            # Show a message if no tags are available
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                "No Tags Available",
                "No tags available for filtering."
            )
            return
            
        if not self.tag_selector_popup.isVisible():
            # Update popup with current tags
            self.tag_selector_popup.set_tags(self.available_tags, self.current_tags)
            
            # Position popup below the tag selector button
            button_pos = self.tag_selector_btn.mapToGlobal(QPoint(0, 0))
            popup_x = button_pos.x()
            popup_y = button_pos.y() + self.tag_selector_btn.height() + 2

            self.tag_selector_popup.adjustSize()
            popup_width = self.tag_selector_popup.width()
            popup_height = self.tag_selector_popup.height()

            window_rect = self.frameGeometry()
            min_x = window_rect.left()
            max_x = window_rect.right() - popup_width + 1
            min_y = window_rect.top()
            max_y = window_rect.bottom() - popup_height + 1

            popup_x = max(min_x, min(popup_x, max_x))
            popup_y = max(min_y, min(popup_y, max_y))
            
            self.tag_selector_popup.move(popup_x, popup_y)
            self.tag_selector_popup.show()
        else:
            self.tag_selector_popup.hide()
    
    def _on_tags_changed(self, tags: List[str]):
        """Handle tag selection changes from popup"""
        self.current_tags = tags
        logger.info(f"Tags changed: {tags if tags else 'None'} (count: {len(tags)})")
        self._update_tag_chips()
        self._update_tag_selector_badge()
        self._update_show_all_button_visibility()
        
        # Auto-reload posts with new tags
        self.current_offset = 0
        self._load_posts()
    
    def _update_tag_chips(self):
        """Update the tag chips display"""
        from src.ui.widgets.tag_chip import TagChip
        
        # Clear existing chips
        while self.tag_chips_layout.count() > 0:
            item = self.tag_chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        # Show/hide container based on whether tags are selected
        if not self.current_tags:
            self.tag_chips_container.setVisible(False)
            return
        
        self.tag_chips_container.setVisible(True)
        
        # Add chip for each selected tag
        for tag in self.current_tags:
            chip = TagChip(tag)
            chip.removed.connect(self._on_tag_chip_removed)
            self.tag_chips_layout.addWidget(chip)
    
    def _on_tag_chip_removed(self, tag: str):
        """Handle tag chip removal (× clicked)"""
        if tag in self.current_tags:
            self.current_tags.remove(tag)
            self._update_tag_chips()
            self._update_tag_selector_badge()
            self._update_show_all_button_visibility()
            
            # Update popup selection
            if hasattr(self, 'tag_selector_popup'):
                self.tag_selector_popup.set_tags(self.available_tags, self.current_tags)
            
            # Reload posts
            self.current_offset = 0
            self._load_posts()
    
    def _update_tag_selector_badge(self):
        """Update tag selector button to show count badge"""
        if self.current_tags:
            count = len(self.current_tags)
            self.tag_selector_badge.setText(str(count))
            badge_height = 16
            text_width = self.tag_selector_badge.fontMetrics().horizontalAdvance(str(count))
            badge_width = max(badge_height, text_width + 8)
            self.tag_selector_badge.setFixedSize(badge_width, badge_height)
            self._position_tag_selector_badge()
            self.tag_selector_badge.setVisible(True)
        else:
            self.tag_selector_badge.setVisible(False)

    def _position_tag_selector_badge(self):
        """Position the tag selector badge in the top-right corner of the button."""
        if not hasattr(self, "tag_selector_badge"):
            return
        margin = 2
        x = self.tag_selector_btn.width() - self.tag_selector_badge.width() - margin
        y = margin
        self.tag_selector_badge.move(x, y)
        self.tag_selector_badge.raise_()

    def _on_tags_search(self):
        raw = self.tags_input.text().strip()
        self.current_tags = [t.strip() for t in raw.split(",") if t.strip()]
        self.current_offset = 0
        if self.current_posts_mode == "tags":
            self._load_posts()

    def _load_tags(self):
        """Load available tags for current platform or creator"""
        if self._closing:
            return
        try:
            # Load creator-specific tags if creator is selected
            if self.current_creator_id:
                service = self._creator_service
                if not service or service == "all":
                    service = self.current_service if self.current_service not in (None, "all") else None
                if service:
                    tags = self.posts_manager.get_creator_tags(
                        self.current_platform,
                        service,
                        self.current_creator_id
                    )
                    logger.info(
                        "Loading tags for creator %s (%s) via service %s",
                        self.current_creator_name,
                        self.current_creator_id,
                        service,
                    )
                else:
                    tags = self.posts_manager.get_tags(self.current_platform)
                    logger.info(
                        "Loading platform-wide tags for %s (no creator service available)",
                        self.current_platform,
                    )
            else:
                # Load platform-wide tags
                tags = self.posts_manager.get_tags(self.current_platform)
                logger.info(f"Loading platform-wide tags for {self.current_platform}")
            
            if tags:
                filtered_tags = _filter_spam_tags(tags)
                removed_count = len(tags) - len(filtered_tags)
                if removed_count:
                    source_label = "creator" if self.current_creator_id else "platform"
                    logger.info("Filtered %d spam tags from %s tag list", removed_count, source_label)
                # Tags are already in dict format with 'name' and 'count'
                self.available_tags = filtered_tags
                
                # Update tag selector popup if it exists
                if hasattr(self, 'tag_selector_popup'):
                    self.tag_selector_popup.set_tags(self.available_tags, self.current_tags)
                
                # Update tag selector button badge
                if hasattr(self, 'tag_selector_btn'):
                    self._update_tag_selector_badge()
                    self.tag_selector_btn.setEnabled(True)
                    self.tag_selector_btn.setToolTip("Select tags to filter posts")
                
                logger.info(f"Loaded {len(self.available_tags)} tags")
            else:
                # No tags available
                self.available_tags = []
                if hasattr(self, 'tag_selector_btn'):
                    self.tag_selector_btn.setEnabled(False)
                    self.tag_selector_btn.setToolTip("No tags available")
                logger.info("No tags available")
        except Exception as e:
            logger.error(f"Failed to load tags: {e}")
            self.available_tags = []
            if hasattr(self, 'tag_selector_btn'):
                self.tag_selector_btn.setEnabled(False)
                self.tag_selector_btn.setToolTip("Failed to load tags")

    def _on_posts_page_changed(self, page: int):
        """Handle posts pagination"""
        self.current_offset = page * 50  # 50 posts per page
        self._load_posts()

    def _refresh_posts(self):
        """Refresh current posts"""
        self._load_posts()

    def _on_random_post(self):
        """Fetch a random post and open it in the detail view."""
        if self._random_post_worker:
            self._retire_worker(self._random_post_worker)
            self._random_post_worker = None

        self._random_post_token += 1
        self._random_post_worker = RandomPostWorker(
            token=self._random_post_token,
            posts_manager=self.posts_manager,
            platform=self.current_platform,
        )
        self._random_post_worker.loaded.connect(self._on_random_post_loaded)
        self._random_post_worker.failed.connect(self._on_random_post_failed)
        self._random_post_worker.start()

    @pyqtSlot(int, object)
    def _on_random_post_loaded(self, token: int, locator: object):
        if token != self._random_post_token:
            return
        if self._random_post_worker:
            self._retire_worker(self._random_post_worker)
            self._random_post_worker = None

        if hasattr(locator, "service") and hasattr(locator, "creator_id") and hasattr(locator, "post_id"):
            service = getattr(locator, "service")
            creator_id = getattr(locator, "creator_id")
            post_id = getattr(locator, "post_id")
        elif isinstance(locator, dict):
            service = locator.get("service")
            creator_id = locator.get("artist_id") or locator.get("creator_id")
            post_id = locator.get("post_id")
        else:
            logger.info("Random post response invalid: %r", locator)
            self.toast.show_message(
                "Random post response invalid",
                icon_name='fa5s.exclamation-triangle',
                icon_color='#ff6b35',
                duration=5000,
            )
            return

        if not service or not creator_id or not post_id:
            logger.info(
                "Random post response missing data: service=%r artist_id=%r post_id=%r",
                service,
                creator_id,
                post_id,
            )
            self.toast.show_message(
                "Random post response missing data",
                icon_name='fa5s.exclamation-triangle',
                icon_color='#ff6b35',
                duration=5000,
            )
            return

        self._start_random_post_detail(
            service=str(service),
            creator_id=str(creator_id),
            post_id=str(post_id),
        )

    @pyqtSlot(int, str)
    def _on_random_post_failed(self, token: int, error: str):
        if token != self._random_post_token:
            return
        if self._random_post_worker:
            self._retire_worker(self._random_post_worker)
            self._random_post_worker = None
        logger.info("Random post failed: %s", error)
        self.toast.show_message(
            f"Random post failed: {error}",
            icon_name='fa5s.exclamation-triangle',
            icon_color='#ff6b35',
            duration=5000,
        )

    def _start_random_post_detail(self, *, service: str, creator_id: str, post_id: str):
        if self._random_post_detail_worker:
            self._retire_worker(self._random_post_detail_worker)
            self._random_post_detail_worker = None

        self._random_post_detail_token += 1
        self._random_post_detail_worker = PostDetailWorker(
            token=self._random_post_detail_token,
            posts_manager=self.posts_manager,
            platform=self.current_platform,
            service=service,
            creator_id=creator_id,
            post_id=post_id,
        )
        self._random_post_detail_worker.loaded.connect(self._on_random_post_detail_loaded)
        self._random_post_detail_worker.failed.connect(self._on_random_post_detail_failed)
        self._random_post_detail_worker.start()

    @pyqtSlot(int, object)
    def _on_random_post_detail_loaded(self, token: int, post: PostDTO):
        if token != self._random_post_detail_token:
            return
        if self._random_post_detail_worker:
            self._retire_worker(self._random_post_detail_worker)
            self._random_post_detail_worker = None
        if not post:
            return
        self._on_post_clicked(post)

    @pyqtSlot(int, str)
    def _on_random_post_detail_failed(self, token: int, error: str):
        if token != self._random_post_detail_token:
            return
        if self._random_post_detail_worker:
            self._retire_worker(self._random_post_detail_worker)
            self._random_post_detail_worker = None
        logger.info("Random post detail failed: %s", error)
        self.toast.show_message(
            f"Random post detail failed: {error}",
            icon_name='fa5s.exclamation-triangle',
            icon_color='#ff6b35',
            duration=5000,
        )

    def _load_initial_posts(self):
        """Load initial posts"""
        if self._closing:
            return
        self._load_posts()

    def _load_initial_creators(self):
        """Load initial creators in sidebar"""
        if self._closing:
            return
        self.creators_sidebar.load_initial()

    def _load_posts(self):
        """Load posts from Core managers via a UI worker thread."""
        if self.loading_posts:
            logger.info("Already loading posts, skipping")
            return

        self._clear_post_selection()

        # Cancel existing worker
        if self._posts_worker:
            self._retire_worker(self._posts_worker)

        self.loading_posts = True
        self.status_label.setText("Loading posts...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(0)  # Indeterminate
        self.posts_spinner.setVisible(True)
        self._posts_spinner_anim.start()

        # Log query parameters when tags are applied
        if self.current_tags:
            logger.info(
                f"Loading posts WITH TAGS: tags={self.current_tags}, "
                f"platform={self.current_platform}, service={self.current_service}, "
                f"mode={self.current_posts_mode}, offset={self.current_offset}, "
                f"creator_id={self.current_creator_id}, query='{self.current_query}'"
            )

        # Use creator's service when viewing creator's posts, otherwise use filter service
        effective_service = self._creator_service if self.current_creator_id else self.current_service

        self._posts_token += 1
        self._posts_worker = PostsLoadWorker(
            token=self._posts_token,
            posts_manager=self.posts_manager,
            creators_manager=self.creators_manager,
            platform=self.current_platform,
            service=effective_service,
            offset=self.current_offset,
            query=self.current_query,
            mode=self.current_posts_mode,
            tags=self.current_tags,
            popular_period=self.current_popular_period,
            popular_date=self.current_popular_anchor or self.current_popular_date,
            creator_id=self.current_creator_id,
        )
        self._posts_worker.loaded.connect(self._on_posts_loaded_worker)
        self._posts_worker.failed.connect(self._on_posts_error_worker)
        self._posts_worker.start()

    def _load_more_posts(self):
        """Load next page of posts"""
        self.current_offset += 50
        self._load_posts()

    @pyqtSlot(int, list, int, object)
    def _on_posts_loaded_worker(self, token: int, posts: List[PostDTO], total_count: int, popular_info):
        if token != self._posts_token:
            return
        self._on_posts_loaded(posts, total_count, popular_info)

    @pyqtSlot(int, str)
    def _on_posts_error_worker(self, token: int, error: str):
        if token != self._posts_token:
            return
        self._on_posts_error(error)

    def _on_posts_loaded(self, posts: List[PostDTO], total_count: int, popular_info=None):
        """Handle posts loaded"""
        self.loading_posts = False
        self.progress_bar.setVisible(False)
        self.posts_spinner.setVisible(False)
        self._posts_spinner_anim.stop()
        if self.current_posts_mode == "popular" and popular_info:
            info_date = getattr(popular_info, "date", None) or ""
            if info_date:
                self.current_popular_anchor = info_date
                date_only = info_date[:10]
                self.current_popular_date = date_only
                try:
                    self.popular_date_input.blockSignals(True)
                    self.popular_date_input.setDate(QDate.fromString(date_only, "yyyy-MM-dd"))
                finally:
                    self.popular_date_input.blockSignals(False)
                self.posts_title_label.setText(
                    f"Popular Posts ({self.current_popular_period}) • {self.current_popular_date}"
                )
        # Filter posts by service when no creator is selected and service is not 'all'
        if (
            not self.current_creator_id
            and self.current_service != "all"
        ):
            original_count = len(posts)
            posts = [p for p in posts if p.service == self.current_service]
            if original_count > 0 and len(posts) < original_count:
                logger.info(f"Filtered posts by service '{self.current_service}': {original_count} -> {len(posts)}")

        self.current_posts = posts

        # Prefetch creator avatars for loaded posts
        self._prefetch_creator_avatars(posts)

        # Apply client-side content type / duration filters
        posts_to_show = self._apply_content_filters(posts)

        # Update grid
        self.grid_view.set_posts(posts_to_show, self.current_platform)

        # Update pagination
        page = self.current_offset // 50

        creator_post_count = None
        if self.current_creator_id:
            # Use creator's service, not the UI filter service
            creator_service = self._creator_service or self.current_service
            creator = self.creators_manager.get_creator(
                self.current_platform,
                creator_service,
                self.current_creator_id,
            )
            if creator and creator.post_count is not None:
                creator_post_count = int(creator.post_count)

        # Check if viewing unfiltered "All posts" (API has 50000 offset limit = 1000 pages)
        is_all_posts_unfiltered = (
            not self.current_creator_id 
            and not self.current_tags 
            and self.current_posts_mode == "all"
        )

        total_pages, effective_total = compute_posts_pagination(
            offset=self.current_offset,
            posts_len=len(posts),
            total_count=total_count,
            creator_post_count=creator_post_count,
            max_offset=50000 if is_all_posts_unfiltered else None,
        )

        if effective_total > 0:
            logger.info(f"Using actual count: {effective_total} posts = {total_pages} pages")
        else:
            logger.info(f"Estimating pages: got {len(posts)} posts, assuming {total_pages} total pages")

        self.posts_pagination.set_page(page, total_pages)

        # Update result count
        if effective_total > 0:
            # Show "showing X-Y of Z" when we have total count
            start = self.current_offset + 1
            end = min(self.current_offset + len(posts), effective_total)
            self.posts_result_label.setText(f"Showing {start}-{end} of {effective_total} posts")
        else:
            # Show just the current count when estimating
            self.posts_result_label.setText(f"{len(posts)} posts")

        # Update status
        if self.current_creator_name:
            self.status_label.setText(f"Showing posts by {self.current_creator_name}")
        elif self.current_posts_mode == "popular":
            logger.info(
                "Popular posts pagination: offset=%s period=%s date=%s received=%s total=%s",
                self.current_offset,
                self.current_popular_period,
                self.current_popular_date,
                len(posts),
                total_count,
            )
            self.status_label.setText(f"Popular posts ({self.current_popular_period})")
        elif self.current_posts_mode == "tags":
            label = ", ".join(self.current_tags) if self.current_tags else "tags"
            self.status_label.setText(f"Tagged posts: {label}")
        elif self.current_query:
            self.status_label.setText(f"Search results for: {self.current_query}")
        else:
            self.status_label.setText(f"Showing {len(posts)} posts")

    def _on_posts_error(self, error: str):
        """Handle posts loading error"""
        self.loading_posts = False
        self.progress_bar.setVisible(False)
        self.posts_spinner.setVisible(False)
        self._posts_spinner_anim.stop()
        self.status_label.setText(f"Error: {error}")
        QMessageBox.warning(self, "Error Loading Posts", error)

    def _cleanup_worker(self):
        """Cleanup API worker"""
        if self._posts_worker:
            self._retire_worker(self._posts_worker)
            self._posts_worker = None

    def _on_post_clicked(self, post_data: PostDTO):
        """Handle post click - show detail view"""
        # Cleanup grid view if needed (cancel image loading, etc.)
        self._cleanup_grid_view()
        self._clear_post_selection()

        # Lazy initialization of detail view
        if self.detail_view is None:
            self.detail_view = GalleryPostView(db_manager=self.db, cache_dir=self.core.cache.thumbnails, core_context=self.core)
            self.detail_view.back_clicked.connect(self._show_grid_view)
            self.detail_view.download_clicked.connect(self._download_files)
            self.detail_view.creator_requested.connect(self._on_creator_requested)
            self.detail_view.set_creator_lookup(self._resolve_creator_name)
            self.detail_view.set_sidebar_toggle_overlay_handler(self._set_sidebar_toggle_overlay_root)
            self.posts_stack.addWidget(self.detail_view)
            logger.info("GalleryPostView created (lazy initialization)")

        self.detail_view.set_post(post_data, self.current_platform)
        self.posts_stack.setCurrentWidget(self.detail_view)
        self._set_posts_chrome_visible(False)

        if post_data.content is None and post_data.id:
            self._load_post_detail(post_data)

    def _show_grid_view(self):
        """Return to grid view"""
        # Cleanup detail view (stop video, cancel image loading)
        self._cleanup_detail_view()

        self.posts_stack.setCurrentWidget(self.grid_view)
        self._set_posts_chrome_visible(True)

    def _set_posts_chrome_visible(self, visible: bool) -> None:
        if hasattr(self, "posts_header"):
            self.posts_header.setVisible(visible)
        if hasattr(self, "posts_pagination_container"):
            self.posts_pagination_container.setVisible(visible)

    def _prefetch_creator_avatars(self, posts: List[PostDTO]) -> None:
        """Prefetch and cache creator avatars for posts."""
        if not posts:
            return
        
        # Extract unique (service, user_id) tuples
        unique_creators = set()
        for post in posts:
            if post.service and post.user_id:
                unique_creators.add((post.service, post.user_id))
        
        if not unique_creators:
            return
        
        logger.info(f"Prefetching avatars for {len(unique_creators)} unique creators")
        
        from src.ui.images.image_loader_manager import get_image_loader_manager
        from src.core.media_manager import MediaManager
        
        loader_manager = get_image_loader_manager()
        if not loader_manager:
            return
        
        # Queue avatar loads using the grid loader
        for service, user_id in unique_creators:
            try:
                icon_url = MediaManager.build_creator_icon_url(
                    self.current_platform,
                    service,
                    user_id
                )
                # Request load using load_image (will cache automatically)
                loader_manager.grid_loader().load_image(
                    url=icon_url,
                    target_size=(200, 200),  # Standard avatar size
                    is_thumbnail=True
                )
            except Exception as e:
                logger.debug(f"Failed to queue avatar load for {service}/{user_id}: {e}")

    def _cleanup_grid_view(self):
        """Cleanup grid view resources"""
        from src.ui.images.image_loader_manager import get_image_loader_manager
        get_image_loader_manager().cancel_grid_loads()
        logger.info("Grid view image loads cancelled")

    def _cleanup_detail_view(self):
        """Cleanup detail view resources"""
        self._cancel_post_detail_worker()
        # Stop any playing media (only if detail view was created)
        if self.detail_view is not None and hasattr(self.detail_view, 'cleanup'):
            self.detail_view.cleanup()
            logger.debug("Detail view cleaned up")

    def _retire_worker(self, worker: QThread) -> None:
        if not worker:
            return
        try:
            if not worker.isRunning():
                worker.deleteLater()
                return
        except Exception:
            pass
        try:
            if hasattr(worker, "cancel"):
                worker.cancel()
        except Exception:
            pass
        self._stale_workers.append(worker)
        worker.finished.connect(lambda w=worker: self._release_worker(w))

    def _release_worker(self, worker: QThread) -> None:
        try:
            if worker in self._stale_workers:
                self._stale_workers.remove(worker)
        except Exception:
            pass
        try:
            worker.deleteLater()
        except Exception:
            pass

    def _shutdown_threads(self) -> None:
        threads = []
        if self._posts_worker:
            self._retire_worker(self._posts_worker)
            threads.append(self._posts_worker)
        if self._post_detail_worker:
            self._retire_worker(self._post_detail_worker)
            threads.append(self._post_detail_worker)
        if self._random_post_worker:
            self._retire_worker(self._random_post_worker)
            threads.append(self._random_post_worker)
        if self._random_post_detail_worker:
            self._retire_worker(self._random_post_detail_worker)
            threads.append(self._random_post_detail_worker)
        if hasattr(self, "_download_thread") and self._download_thread:
            try:
                if self._download_thread.isRunning():
                    self._download_thread.cancel()
                    threads.append(self._download_thread)
            except Exception:
                pass
        threads.extend(self._stale_workers)

        seen = set()
        for thread in threads:
            if not thread:
                continue
            key = id(thread)
            if key in seen:
                continue
            seen.add(key)
            try:
                if thread.isRunning():
                    thread.wait(5000)
                    if thread.isRunning():
                        logger.warning("Force terminating thread %r during shutdown", thread)
                        thread.terminate()
                        thread.wait(1500)
            except Exception:
                pass

    def _cancel_post_detail_worker(self) -> None:
        if self._post_detail_worker:
            self._retire_worker(self._post_detail_worker)
            self._post_detail_worker = None

    def _load_post_detail(self, post_data: PostDTO) -> None:
        if self._post_detail_worker:
            self._retire_worker(self._post_detail_worker)
            self._post_detail_worker = None

        self._post_detail_token += 1
        self._post_detail_worker = PostDetailWorker(
            token=self._post_detail_token,
            posts_manager=self.posts_manager,
            platform=self.current_platform,
            service=post_data.service,
            creator_id=post_data.user_id,
            post_id=post_data.id,
        )
        self._post_detail_worker.loaded.connect(self._on_post_detail_loaded)
        self._post_detail_worker.failed.connect(self._on_post_detail_error)
        self._post_detail_worker.start()

    @pyqtSlot(int, object)
    def _on_post_detail_loaded(self, token: int, post: PostDTO) -> None:
        if token != self._post_detail_token:
            return
        if self._post_detail_worker:
            self._retire_worker(self._post_detail_worker)
            self._post_detail_worker = None
        current = getattr(self.detail_view, "post_data", None)
        if not current or current.id != post.id:
            return
        self.detail_view.update_post_content(post)

    @pyqtSlot(int, str)
    def _on_post_detail_error(self, token: int, error: str) -> None:
        if token != self._post_detail_token:
            return
        if self._post_detail_worker:
            self._retire_worker(self._post_detail_worker)
            self._post_detail_worker = None
        logger.warning(f"Failed to load post detail: {error}")

    # Download methods are now in DownloadMixin (browser_downloads.py)

    def _clear_post_selection(self) -> None:
        if hasattr(self, "grid_view") and self.grid_view.get_selected_count() > 0:
            self.grid_view.deselect_all()

    def _handle_master_toggle(self):
        """Logic: If any are selected -> Deselect All. Otherwise -> Select All."""
        # We check the actual data state rather than the button's checked state
        # to ensure the toggle feels intuitive.
        currently_selected_count = self.grid_view.get_selected_count() # Ensure you have this helper

        if currently_selected_count > 0:
            self.grid_view.deselect_all()
        else:
            self.grid_view.select_all()

    def _on_posts_selection_changed(self, selected_count: int):
        """Handle posts selection changes and update the Master Button UI"""
        total_posts = self.grid_view.get_total_count() # Ensure you have this helper
        has_selection = selected_count > 0
        all_selected = selected_count == total_posts and total_posts > 0

        # Update UI visibility
        self.selected_count_label.setVisible(has_selection)
        self.download_selected_btn.setVisible(has_selection)

        # Update Master Button Icon and State
        if all_selected:
            self.master_selection_btn.setIcon(qta.icon('fa5s.check-square', color='white'))
            self.master_selection_btn.setChecked(True)
        elif has_selection:
            # "Minus" or "Indeterminate" square icon to show partial selection
            self.master_selection_btn.setIcon(qta.icon('fa5s.minus-square', color='white'))
            self.master_selection_btn.setChecked(True)
        else:
            # Empty square
            self.master_selection_btn.setIcon(qta.icon('fa5s.square'))
            self.master_selection_btn.setChecked(False)

        if has_selection:
            self.selected_count_label.setText(f"{selected_count} selected")

    def _select_all_posts(self):
        """Select all posts in the grid"""
        self.grid_view.select_all()

    def _deselect_all_posts(self):
        """Deselect all posts in the grid"""
        self.grid_view.deselect_all()

    # _download_selected_posts is now in DownloadMixin (browser_downloads.py)

    def _construct_file_url(self, file_path: str) -> str:
        """Construct full URL from file path"""
        return self.core.media.build_media_url(self.current_platform, file_path)

    def _show_settings(self):
        """Show settings dialog"""
        from src.ui.common.settings_dialog import SettingsDialog
        dialog = SettingsDialog(self.db, self, self.core)
        dialog.exec()

    def _show_about(self):
        """Show about dialog"""
        QMessageBox.about(self, "About Coomer BetterUI",
            "Coomer BetterUI\n\nA modern, native UI for browsing Coomer/Kemono content")

    def closeEvent(self, event):
        """Ensure core resources shut down when the main window closes."""
        self._closing = True  # Prevent deferred callbacks
        try:
            if hasattr(self, "_gallery") and self._gallery:
                self._gallery.close()
        except Exception:
            pass
        try:
            if self.detail_view is not None and hasattr(self.detail_view, "cleanup"):
                self.detail_view.cleanup()
        except Exception:
            pass
        try:
            self._shutdown_threads()
        except Exception:
            pass
        try:
            if hasattr(self.core, "close"):
                self.core.close()
        except Exception:
            pass
        try:
            from src.core.thumbnails import get_thumbnail_manager
            get_thumbnail_manager().shutdown()
        except Exception:
            pass
        super().closeEvent(event)
