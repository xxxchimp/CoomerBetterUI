"""
Centralized theme configuration for the application.

This module provides a single source of truth for all colors, fonts, spacing,
and styling used throughout the UI. Colors are synchronized with dark_theme_pro.qss.

Usage:
    from src.ui.common.theme import Colors, Fonts, Spacing, Styles

    label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
    icon = qta.icon('fa5s.heart', color=Colors.ACCENT_PRIMARY)
"""
from dataclasses import dataclass
from typing import Optional


class Colors:
    """
    Color palette for the application.

    Synchronized with resources/styles/dark_theme_pro.qss palette:
      Backgrounds: #141414 (primary), #1b1b1b (secondary), #232323 (tertiary)
      Borders:     #2e2e2e, #111111 (deep)
      Text:        #e6e6e6 (primary), #9ca3af (secondary), #6b7280 (muted)
      Accent A:    #f7673a (primary action)
      Accent B:    #4a9eff (secondary/neutral highlights)
      Success:     #10b981
      Error:       #ef4444
    """

    # Primary accent - orange (used for active states, highlights, branding)
    ACCENT_PRIMARY = "#f7673a"

    # Secondary accent - blue (used for info, links, secondary actions)
    ACCENT_SECONDARY = "#4a9eff"

    # Semantic accents
    ACCENT_SUCCESS = "#10b981"   # Green for success states
    ACCENT_ERROR = "#ef4444"     # Red for errors
    ACCENT_WARNING = "#f59e0b"   # Amber for warnings
    ACCENT_FAVORITE = "#ef4444"  # Red for favorites/hearts

    # Text colors (light text on dark background)
    TEXT_PRIMARY = "#e6e6e6"     # Main text
    TEXT_SECONDARY = "#9ca3af"   # Muted/secondary text
    TEXT_MUTED = "#6b7280"       # Even more muted
    TEXT_DISABLED = "#666666"    # Disabled state
    TEXT_INVERSE = "#141414"     # Dark text on light background
    TEXT_WHITE = "#ffffff"       # Pure white text

    # Background colors (darkest to lightest)
    BG_PRIMARY = "#141414"       # Main app background
    BG_SECONDARY = "#1b1b1b"     # Panels, sidebars, headers
    BG_TERTIARY = "#232323"      # Cards, elevated surfaces
    BG_INPUT = "#161616"         # Input fields, wells
    BG_HOVER = "#2e2e2e"         # Hover state background
    BG_SELECTED = "#1f2933"      # Selected item background

    # Border colors
    BORDER_DEEP = "#111111"      # Darkest borders, separators
    BORDER_DEFAULT = "#2e2e2e"   # Standard borders
    BORDER_LIGHT = "#3a3a3a"     # Lighter borders
    BORDER_ROW = "#000000"       # Strong row divider
    BORDER_SUBTLE = "#1f1f1f"    # Subtle separators (pagination, headers)
    BORDER_ACCENT = ACCENT_PRIMARY

    # State colors
    STATE_HOVER_BG = "#232323"
    STATE_ACTIVE_BG = "#2e2e2e"
    STATE_DISABLED_BG = "#242424"
    STATE_FOCUS_BORDER = ACCENT_PRIMARY

    # Semantic aliases for consistency
    ICON_DEFAULT = TEXT_SECONDARY
    ICON_ACTIVE = ACCENT_PRIMARY
    ICON_DISABLED = "#ffffff"
    SPINNER = "#ffffff"
    LOADING = TEXT_SECONDARY

    # Legacy aliases (for backward compatibility with refactored code)
    BG_DARKEST = BG_PRIMARY
    BG_DARKER = BG_SECONDARY
    BG_DARK = BG_TERTIARY
    BG_MEDIUM = BG_TERTIARY
    BG_LIGHT = BG_HOVER
    BG_LIGHTER = "#3a3a3a"
    TEXT_TERTIARY = TEXT_MUTED


class Fonts:
    """Font sizes and weights matching dark_theme_pro.qss."""

    # Font family
    FAMILY = '"Fira Sans", "Segoe UI", sans-serif'

    # Font sizes (in pixels) - QSS uses 15px as base
    SIZE_XS = 11
    SIZE_SM = 12
    SIZE_MD = 13
    SIZE_LG = 14
    SIZE_XL = 15      # QSS base size
    SIZE_XXL = 16
    SIZE_TITLE = 24

    # Font weights
    WEIGHT_NORMAL = 400
    WEIGHT_MEDIUM = 500
    WEIGHT_SEMIBOLD = 600
    WEIGHT_BOLD = 700

    @staticmethod
    def css(size: int, weight: int = 400, color: str = Colors.TEXT_PRIMARY) -> str:
        """Generate font CSS string with size validation."""
        # Ensure font size is valid (> 0) to avoid Qt warnings
        safe_size = max(1, size) if size else Fonts.SIZE_MD
        return f"font-size: {safe_size}px; font-weight: {weight}; color: {color};"
    
    @staticmethod
    def safe_size(size: int) -> int:
        """Ensure font size is valid (> 0) to prevent Qt warnings."""
        return max(1, size) if size else Fonts.SIZE_MD


class Spacing:
    """Spacing and sizing constants."""

    # Padding/margin values
    NONE = 0
    XS = 4
    SM = 8
    MD = 12
    LG = 16
    XL = 20
    XXL = 24
    XXXL = 40

    # Border radius (matching QSS)
    RADIUS_SM = 4
    RADIUS_MD = 6
    RADIUS_LG = 8
    RADIUS_XL = 10
    RADIUS_XXL = 12
    RADIUS_ROUND = 9999  # Fully rounded (pill shape)

    # Icon sizes
    ICON_SM = 16
    ICON_MD = 20
    ICON_LG = 24
    ICON_XL = 32

    # Common widget dimensions
    HEADER_HEIGHT = 70
    PAGINATION_HEIGHT = 48
    CREATOR_ROW_HEIGHT = 60
    SIDEBAR_WIDTH = 280
    CARD_WIDTH = 236
    CARD_HEIGHT = 280
    THUMBNAIL_HEIGHT = 220
    PROGRESS_BAR_HEIGHT = 8
    BUTTON_HEIGHT = 32

    # UI element sizes
    BTN_SM = 24          # Small buttons (e.g., select checkbox)
    BTN_MD = 32          # Medium buttons  
    BTN_LG = 36          # Large buttons
    CONTROL_HEIGHT = 36  # Standard control height (search, combo boxes)
    SERVICE_ICON = 25    # Service logo icons
    CARD_BORDER = 1      # Card border width (1px)
    AVATAR_SM = 32       # Small avatar (recommended creators)
    AVATAR_MD = 36       # Medium avatar (creator list items)
    AVATAR_LG = 48       # Large avatar (creator detail)

    # Dialog sizing
    DIALOG_MIN_WIDTH = 600   # Minimum dialog width (fits 1024px screens)
    DIALOG_MIN_HEIGHT = 450  # Minimum dialog height (fits 768px screens)
    DIALOG_PREF_WIDTH = 800  # Preferred dialog width
    DIALOG_PREF_HEIGHT = 600 # Preferred dialog height
    MULTILINE_MIN_HEIGHT = 60   # Minimum height for multiline text areas
    MULTILINE_MAX_HEIGHT = 100  # Maximum height for multiline text areas


class FileSidebar:
    """Sizing constants for the gallery file browser sidebar."""

    CHECKBOX_SIZE = 16
    WIDTH_DEFAULT = 380
    WIDTH_MIN = 250
    WIDTH_MAX = 600
    RESIZE_HANDLE = 5

    HEADER_HEIGHT = 48
    TOOLBAR_HEIGHT = 40
    FOOTER_HEIGHT = 56

    HEADER_ICON = 16
    HEADER_BUTTON = 32
    MASTER_TOGGLE_ICON = 16
    MASTER_TOGGLE_BUTTON = 28

    ITEM_ICON = 20
    ITEM_DOWNLOAD_ICON = 14
    ITEM_DOWNLOAD_BUTTON = 24

    DOWNLOAD_BUTTON_HEIGHT = 36


class Styles:
    """
    Pre-built stylesheet snippets for dynamic/programmatic styling.

    Note: For widgets styled via QSS objectName, these may be overridden.
    Use these for dynamic styles, icons, and widgets not covered by QSS.
    """

    # Card style (matches QSS #postCard)
    CARD = f"""
        QFrame {{
            background-color: {Colors.BG_SECONDARY};
            border: 1px solid {Colors.BG_TERTIARY};
            border-radius: {Spacing.RADIUS_XXL}px;
        }}
        QFrame:hover {{
            border-color: {Colors.ACCENT_PRIMARY};
            background-color: #1f1f1f;
        }}
    """

    # Header bar style
    HEADER = f"""
        background-color: {Colors.BG_SECONDARY};
        border-bottom: 1px solid {Colors.BORDER_DEEP};
    """

    # Toast notification (note: QSS #toastNotification overrides this)
    TOAST = f"""
        QWidget#toastNotification {{
            background-color: {Colors.BG_SECONDARY};
            border: 1px solid {Colors.BORDER_DEFAULT};
            border-radius: {Spacing.RADIUS_XL}px;
        }}
    """

    # Progress bar style
    PROGRESS_BAR = f"""
        QProgressBar {{
            background-color: {Colors.BORDER_DEFAULT};
            border-radius: {Spacing.RADIUS_SM}px;
            border: none;
        }}
        QProgressBar::chunk {{
            background-color: {Colors.ACCENT_SECONDARY};
            border-radius: {Spacing.RADIUS_SM}px;
        }}
    """

    # Download progress container
    DOWNLOAD_PROGRESS = f"""
        QWidget#downloadProgressBar {{
            background-color: {Colors.BG_PRIMARY};
            border-top: 1px solid {Colors.BORDER_DEFAULT};
        }}
        QLabel {{ color: {Colors.TEXT_PRIMARY}; font-size: {Fonts.SIZE_MD}px; }}
        QProgressBar {{
            background-color: {Colors.BORDER_DEFAULT};
            border-radius: {Spacing.RADIUS_SM}px;
            border: none;
        }}
        QProgressBar::chunk {{
            background-color: {Colors.ACCENT_SECONDARY};
            border-radius: {Spacing.RADIUS_SM}px;
        }}
        QPushButton {{
            background-color: {Colors.BG_TERTIARY};
            border: 1px solid {Colors.BORDER_DEFAULT};
            border-radius: {Spacing.RADIUS_MD}px;
            color: {Colors.TEXT_PRIMARY};
            padding: {Spacing.XS}px;
        }}
        QPushButton:hover {{ background-color: {Colors.BG_HOVER}; border-color: {Colors.ACCENT_PRIMARY}; }}
    """

    # Scrollbar styling
    SCROLLBAR = f"""
        QScrollBar:vertical {{
            background-color: {Colors.BG_PRIMARY};
            width: 8px;
            border-radius: {Spacing.RADIUS_SM}px;
        }}
        QScrollBar::handle:vertical {{
            background-color: #3a3a3a;
            border-radius: {Spacing.RADIUS_SM}px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background-color: #4a4a4a;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
    """

    @staticmethod
    def label(
        color: str = Colors.TEXT_PRIMARY,
        size: int = Fonts.SIZE_MD,
        weight: int = Fonts.WEIGHT_NORMAL,
        padding: Optional[int] = None,
        bg: Optional[str] = None,
    ) -> str:
        """Generate label stylesheet with size validation."""
        # Ensure font size is valid (> 0) to avoid Qt warnings
        safe_size = max(1, size) if size else Fonts.SIZE_MD
        style = f"color: {color}; font-size: {safe_size}px; font-weight: {weight};"
        if padding is not None:
            style += f" padding: {padding}px;"
        if bg is not None:
            style += f" background-color: {bg}; border-radius: {Spacing.RADIUS_MD}px;"
        return f"QLabel {{ {style} }}"

    @staticmethod
    def button_primary() -> str:
        """Primary action button style (matches QSS #searchButton)."""
        return f"""
            QPushButton {{
                background-color: {Colors.ACCENT_PRIMARY};
                border: 1px solid {Colors.ACCENT_PRIMARY};
                border-radius: {Spacing.RADIUS_LG}px;
                color: {Colors.TEXT_WHITE};
                font-weight: {Fonts.WEIGHT_SEMIBOLD};
                padding: 9px 18px;
            }}
            QPushButton:hover {{
                background-color: #ff8a60;
                border-color: #ff8a60;
            }}
            QPushButton:pressed {{
                background-color: #e2582b;
                border-color: #e2582b;
            }}
            QPushButton:disabled {{
                background-color: {Colors.STATE_DISABLED_BG};
                border-color: {Colors.STATE_DISABLED_BG};
                color: {Colors.TEXT_DISABLED};
            }}
        """

    @staticmethod
    def button_secondary() -> str:
        """Secondary/outline button style."""
        return f"""
            QPushButton {{
                background-color: {Colors.BG_TERTIARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_LG}px;
                color: {Colors.TEXT_PRIMARY};
                padding: 9px 16px;
                font-weight: {Fonts.WEIGHT_MEDIUM};
            }}
            QPushButton:hover {{
                background-color: {Colors.BG_HOVER};
                border-color: {Colors.ACCENT_PRIMARY};
            }}
            QPushButton:pressed {{
                background-color: {Colors.BG_SECONDARY};
            }}
            QPushButton:disabled {{
                background-color: {Colors.STATE_DISABLED_BG};
                border-color: {Colors.STATE_DISABLED_BG};
                color: {Colors.TEXT_DISABLED};
            }}
        """

    @staticmethod
    def button_flat() -> str:
        """Flat/text button style."""
        return f"""
            QPushButton {{
                background-color: transparent;
                color: {Colors.TEXT_SECONDARY};
                border: none;
                padding: {Spacing.XS}px;
            }}
            QPushButton:hover {{
                color: {Colors.TEXT_PRIMARY};
                background-color: rgba(255, 255, 255, 0.08);
                border-radius: {Spacing.RADIUS_LG}px;
            }}
        """

    @staticmethod
    def button_success() -> str:
        """Success/download button style (matches QSS #downloadButton)."""
        return f"""
            QPushButton {{
                background-color: {Colors.ACCENT_SUCCESS};
                border: 1px solid {Colors.ACCENT_SUCCESS};
                border-radius: {Spacing.RADIUS_LG}px;
                color: {Colors.TEXT_WHITE};
                font-weight: {Fonts.WEIGHT_SEMIBOLD};
                padding: 9px 18px;
            }}
            QPushButton:hover {{
                background-color: #0ea171;
                border-color: #0ea171;
            }}
            QPushButton:pressed {{
                background-color: #059669;
                border-color: #059669;
            }}
        """

    @staticmethod
    def input_field() -> str:
        """Text input field style (matches QSS QLineEdit)."""
        return f"""
            QLineEdit {{
                background-color: {Colors.BG_INPUT};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_LG}px;
                selection-background-color: {Colors.ACCENT_PRIMARY};
                selection-color: {Colors.TEXT_WHITE};
            }}
            QLineEdit:focus {{
                border-color: {Colors.ACCENT_PRIMARY};
            }}
            QLineEdit:disabled {{
                background-color: {Colors.STATE_DISABLED_BG};
                color: {Colors.TEXT_DISABLED};
            }}
        """

    @staticmethod
    def combo_box() -> str:
        """Combo box / dropdown style."""
        return f"""
            QComboBox {{
                background-color: {Colors.BG_TERTIARY};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_LG}px;
                padding: 0px 12px;
                font-size: {Fonts.SIZE_LG}px;
            }}
            QComboBox:hover {{
                border-color: {Colors.ACCENT_PRIMARY};
            }}
            QComboBox:disabled {{
                background-color: {Colors.STATE_DISABLED_BG};
                color: {Colors.TEXT_DISABLED};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 18px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {Colors.BG_SECONDARY};
                color: {Colors.TEXT_PRIMARY};
                selection-background-color: {Colors.ACCENT_PRIMARY};
                selection-color: {Colors.TEXT_WHITE};
                border: 1px solid {Colors.BORDER_DEFAULT};
                outline: none;
            }}
            QComboBox QAbstractItemView::item {{
                height: 28px;
                padding: 0px 12px;
                font-size: {Fonts.SIZE_LG}px;
            }}
        """

    @staticmethod
    def volume_slider() -> str:
        """Volume slider style with theme colors and proper inset."""
        return f"""
            QSlider::groove:horizontal {{
                background-color: rgba(255, 255, 255, 0.2);
                height: 4px;
                border-radius: 2px;
                margin: 0px 8px;
            }}
            QSlider::handle:horizontal {{
                background-color: {Colors.TEXT_WHITE};
                width: 12px;
                height: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }}
            QSlider::handle:horizontal:hover {{
                background-color: {Colors.ACCENT_PRIMARY};
            }}
            QSlider::sub-page:horizontal {{
                background-color: {Colors.TEXT_WHITE};
                border-radius: 2px;
                margin: 0px 8px;
            }}
        """

    @staticmethod
    def volume_controls() -> str:
        """Volume controls container with pill-shaped design matching play button."""
        return f"""
            QWidget#volumeControls {{
                background-color: rgba(255, 255, 255, 0.13);
                border-radius: 24px;
                padding: 0px;
            }}
            QWidget#volumeControls:hover {{
                background-color: rgba(255, 255, 255, 0.26);
            }}
            QPushButton#volumeButton {{
                background-color: rgba(255, 255, 255, 0.26);
                border: none;
                padding: 0px;
                margin-top: 0px;
                margin-bottom: 0px;
                margin-left: 4px;
                margin-right: 4px;
                min-width: 40px;
                max-width: 40px;
                min-height: 40px;
                max-height: 40px;
            }}
            QPushButton#volumeButton:hover {{
                background-color: rgba(255, 255, 255, 0.45);
            }}
        """

    @staticmethod
    def filter_popup() -> str:
        """Filter popup container and child elements styling."""
        return f"""
            QWidget {{
                background-color: {Colors.BG_SECONDARY};
                border: 1px solid {Colors.BORDER_LIGHT};
                border-radius: {Spacing.RADIUS_MD}px;
            }}
            QWidget#filterPopup QLabel#filterSectionLabel,
            QWidget#filterPopup QWidget#contentTypeRow,
            QWidget#filterPopup QWidget#durationRow {{
                background-color: transparent;
                border: none;
            }}
            QTabWidget#filterTabs {{
                border: none;
                background: transparent;
            }}
            QTabWidget#filterTabs QStackedWidget {{
                border: none;
                background: transparent;
            }}
            QTabWidget#filterTabs::pane {{
                border: none;
                background: transparent;
            }}
            QTabWidget#filterTabs QTabBar::tab {{
                border: none;
                border-radius: none;
            }}
            QTabWidget#filterTabs::tab-bar {{
                border: none;
            }}
            QTabWidget#filterTabs QTabBar {{
                border: none;
                background: transparent;
            }}
            QTabWidget#filterTabs QTabBar::base {{
                border: 0px;
                background: transparent;
                margin: 0px;
            }}
            QWidget#filtersTab,
            QWidget#popularTab {{
                border: none;
                background: transparent;
            }}
            QComboBox, QLineEdit, QDateEdit {{
                background-color: {Colors.BG_TERTIARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_SM}px;
                padding: 6px;
                color: {Colors.TEXT_PRIMARY};
            }}
            QPushButton {{
                background-color: {Colors.BG_TERTIARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_SM}px;
                padding: 6px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BG_HOVER};
                border-color: {Colors.BORDER_LIGHT};
            }}
            QLabel#popularDateLabel {{
                color: {Colors.TEXT_SECONDARY};
                font-size: {Fonts.SIZE_XS}px;
                padding-left: 2px;
            }}
            QDateEdit#popularDateInput {{
                background-color: {Colors.BG_TERTIARY};
                border: 1px solid {Colors.BORDER_LIGHT};
                border-radius: {Spacing.RADIUS_MD}px;
                padding: 6px 10px;
                color: {Colors.TEXT_PRIMARY};
            }}
            QDateEdit#popularDateInput:focus {{
                border-color: {Colors.ACCENT_PRIMARY};
            }}
            QWidget#popularCalendarHeader {{
                background-color: {Colors.BG_SECONDARY};
            }}
            QToolButton#popularCalendarMonthButton,
            QToolButton#popularCalendarYearButton {{
                background-color: {Colors.BG_SECONDARY};
                border: 1px solid {Colors.BORDER_LIGHT};
                border-radius: {Spacing.RADIUS_MD}px;
                padding: 4px 8px;
                color: {Colors.TEXT_PRIMARY};
                min-height: 30px;
                text-align: left;
            }}
            QToolButton#popularCalendarMonthButton:hover,
            QToolButton#popularCalendarYearButton:hover {{
                border-color: {Colors.BORDER_LIGHT};
            }}
            QLabel#popularCalendarWeekdayLabel {{
                color: {Colors.TEXT_SECONDARY};
                font-size: {Fonts.SIZE_SM}px;
            }}
            QListWidget#popularCalendarMonthList,
            QListWidget#popularCalendarYearList {{
                background-color: {Colors.BG_SECONDARY};
                border: 1px solid {Colors.BORDER_LIGHT};
                border-radius: {Spacing.RADIUS_LG}px;
                color: {Colors.TEXT_PRIMARY};
            }}
            QListWidget#popularCalendarMonthList::item,
            QListWidget#popularCalendarYearList::item {{
                padding: 8px 12px;
            }}
            QListWidget#popularCalendarMonthList::item:selected,
            QListWidget#popularCalendarYearList::item:selected {{
                background-color: {Colors.BG_HOVER};
                color: {Colors.TEXT_WHITE};
            }}
        """

    @staticmethod
    def tag_selector_badge(active: bool = False) -> str:
        """Tag selector button with count badge styling."""
        if active:
            return f"""
                QPushButton#tagSelectorButton {{
                    background-color: {Colors.ACCENT_PRIMARY};
                    color: {Colors.TEXT_WHITE};
                    font-weight: bold;
                    border-radius: {Spacing.RADIUS_SM}px;
                }}
                QPushButton#tagSelectorButton:hover {{
                    background-color: #e85a2f;
                }}
            """
        return ""

    @staticmethod
    def creator_list_item(selected: bool = False) -> str:
        """Creator list item styling with selection state."""
        if selected:
            return f"""
                QFrame {{
                    background-color: {Colors.BG_SELECTED};
                    border: none;
                    border-bottom: 1px solid {Colors.BORDER_ROW};
                }}
            """
        return f"""
            QFrame {{
                background-color: {Colors.BG_SECONDARY};
                border-bottom: 1px solid {Colors.BORDER_ROW};
            }}
            QFrame:hover {{
                background-color: {Colors.BG_HOVER};
            }}
        """

    @staticmethod
    def creator_avatar() -> str:
        """Creator avatar label styling."""
        return f"""
            QLabel {{
                background-color: {Colors.BG_TERTIARY};
                border-radius: 20px;
            }}
        """

    @staticmethod
    def creator_name_label() -> str:
        """Creator name label styling."""
        return f"""
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                font-size: {Fonts.SIZE_LG}px;
                font-weight: {Fonts.WEIGHT_MEDIUM};
            }}
        """

    @staticmethod
    def creator_service_label() -> str:
        """Creator service label styling."""
        return f"""
            QLabel {{
                color: {Colors.TEXT_SECONDARY};
                font-size: {Fonts.SIZE_XS}px;
            }}
        """

    @staticmethod
    def favorite_button() -> str:
        """Favorite button styling."""
        return f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                padding: 4px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BG_HOVER};
                border-radius: {Spacing.RADIUS_SM}px;
            }}
        """

    @staticmethod
    def recommended_creator_card() -> str:
        """Recommended creator card styling."""
        return f"""
            QFrame {{
                background-color: {Colors.BG_SECONDARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_LG}px;
            }}
            QFrame:hover {{
                border-color: {Colors.ACCENT_PRIMARY};
                background-color: {Colors.BG_TERTIARY};
            }}
        """

    @staticmethod
    def score_label() -> str:
        """Score/match percentage label styling."""
        return f"""
            QLabel {{
                color: {Colors.ACCENT_PRIMARY};
                font-size: {Fonts.SIZE_XS}px;
                font-weight: {Fonts.WEIGHT_MEDIUM};
            }}
        """

    @staticmethod
    def tab_button(active: bool = False) -> str:
        """Tab button styling for sidebar tabs."""
        if active:
            return f"""
                QPushButton {{
                    background-color: {Colors.ACCENT_PRIMARY};
                    color: {Colors.TEXT_WHITE};
                    border: none;
                    padding: 8px 16px;
                    font-weight: {Fonts.WEIGHT_MEDIUM};
                }}
            """
        return f"""
            QPushButton {{
                background-color: transparent;
                color: {Colors.TEXT_SECONDARY};
                border: none;
                padding: 8px 16px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BG_HOVER};
                color: {Colors.TEXT_PRIMARY};
            }}
        """

    @staticmethod
    def scroll_area_transparent() -> str:
        """Transparent scroll area styling."""
        return f"QScrollArea {{ border: none; background-color: {Colors.BG_SECONDARY}; }}"

    @staticmethod
    def status_label() -> str:
        """Status label styling for sidebar."""
        return f"""
            QLabel {{
                color: {Colors.TEXT_SECONDARY};
                font-size: {Fonts.SIZE_SM}px;
                padding: 8px;
            }}
        """

    @staticmethod
    def section_container() -> str:
        """Section container with border styling."""
        return f"background-color: {Colors.BG_HOVER}; border-bottom: 1px solid {Colors.BG_PRIMARY};"

    @staticmethod
    def pagination_container() -> str:
        """Pagination container styling."""
        return f"background-color: {Colors.BG_PRIMARY};"

    @staticmethod
    def sort_combo() -> str:
        """Sort combo box styling."""
        return f"""
            QComboBox {{
                background-color: {Colors.BG_TERTIARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_MD}px;
                padding: 6px 12px;
                color: {Colors.TEXT_PRIMARY};
                font-size: {Fonts.SIZE_LG}px;
                min-width: 100px;
            }}
            QComboBox:hover {{
                border-color: {Colors.ACCENT_PRIMARY};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {Colors.BG_SECONDARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                selection-background-color: {Colors.ACCENT_PRIMARY};
                selection-color: {Colors.TEXT_WHITE};
            }}
            QComboBox QAbstractItemView::item {{
                font-family: {Fonts.FAMILY};
                height: 28px;
                padding: 0px 12px;
                font-size: {Fonts.SIZE_LG}px;
            }}
        """

    @staticmethod
    def zoom_button() -> str:
        """Zoom control button styling for gallery view."""
        return f"""
            QPushButton {{
                background-color: rgba(0, 0, 0, 0.5);
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_SM}px;
                color: {Colors.TEXT_WHITE};
                padding: 4px 8px;
                font-size: {Fonts.SIZE_SM}px;
            }}
            QPushButton:hover {{
                background-color: rgba(0, 0, 0, 0.7);
                border-color: {Colors.ACCENT_PRIMARY};
            }}
        """

    @staticmethod
    def thumbnail_card(selected: bool = False) -> str:
        """Thumbnail card styling with selection state."""
        if selected:
            return f"""
                QFrame {{
                    background-color: {Colors.BG_SECONDARY};
                    border: 2px solid {Colors.ACCENT_PRIMARY};
                    border-radius: {Spacing.RADIUS_LG}px;
                }}
            """
        return f"""
            QFrame {{
                background-color: {Colors.BG_SECONDARY};
                border: 1px solid {Colors.BG_TERTIARY};
                border-radius: {Spacing.RADIUS_LG}px;
            }}
            QFrame:hover {{
                border-color: {Colors.ACCENT_PRIMARY};
            }}
        """

    @staticmethod
    def thumbnail_label() -> str:
        """Thumbnail label background styling."""
        return f"QLabel {{ background-color: {Colors.BG_PRIMARY}; }}"

    @staticmethod
    def toggle_button() -> str:
        """Toggle button styling for sidebar panels."""
        return f"""
            QPushButton {{
                background-color: {Colors.BG_TERTIARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_SM}px;
                color: {Colors.TEXT_PRIMARY};
                padding: 4px 8px;
                font-size: {Fonts.SIZE_SM}px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BG_HOVER};
                border-color: {Colors.ACCENT_PRIMARY};
            }}
            QPushButton:checked {{
                background-color: {Colors.ACCENT_PRIMARY};
                color: {Colors.TEXT_WHITE};
            }}
        """

    @staticmethod
    def splitter_handle() -> str:
        """Splitter handle styling."""
        return f"""
            QSplitter::handle {{
                background-color: {Colors.BG_TERTIARY};
            }}
            QSplitter::handle:hover {{
                background-color: {Colors.ACCENT_PRIMARY};
            }}
        """

    @staticmethod
    def tag_chip() -> str:
        """Tag chip widget styling."""
        return f"""
            QFrame {{
                background-color: {Colors.BG_TERTIARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: 12px;
                padding: 2px 4px;
            }}
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
                font-size: {Fonts.SIZE_SM}px;
            }}
            QPushButton {{
                background-color: transparent;
                border: none;
                color: {Colors.TEXT_SECONDARY};
                font-size: {Fonts.SIZE_SM}px;
            }}
            QPushButton:hover {{
                color: {Colors.ACCENT_ERROR};
            }}
        """

    @staticmethod
    def tag_selector_popup() -> str:
        """Tag selector popup widget styling."""
        return f"""
            QWidget {{
                background-color: {Colors.BG_SECONDARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_LG}px;
            }}
            QLineEdit {{
                background-color: {Colors.BG_INPUT};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_MD}px;
                padding: 8px;
                color: {Colors.TEXT_PRIMARY};
            }}
            QLineEdit:focus {{
                border-color: {Colors.ACCENT_PRIMARY};
            }}
            QScrollArea {{
                border: none;
                background-color: transparent;
            }}
            QCheckBox {{
                color: {Colors.TEXT_PRIMARY};
                spacing: 8px;
                padding: 6px;
            }}
            QCheckBox:hover {{
                background-color: {Colors.BG_HOVER};
                border-radius: {Spacing.RADIUS_SM}px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_SM}px;
                background-color: {Colors.BG_INPUT};
            }}
            QCheckBox::indicator:checked {{
                background-color: {Colors.ACCENT_PRIMARY};
                border-color: {Colors.ACCENT_PRIMARY};
            }}
            QPushButton {{
                background-color: {Colors.BG_TERTIARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_MD}px;
                color: {Colors.TEXT_PRIMARY};
            }}
            QPushButton:hover {{
                background-color: {Colors.BG_HOVER};
                border-color: {Colors.ACCENT_PRIMARY};
            }}
        """

    @staticmethod
    def description_label() -> str:
        """Muted description text styling."""
        return f"color: {Colors.TEXT_MUTED}; margin-bottom: 10px;"

    @staticmethod
    def button_danger() -> str:
        """Danger/destructive action button styling."""
        return f"""
            QPushButton {{
                color: {Colors.ACCENT_ERROR};
                font-weight: bold;
                padding: 5px;
            }}
        """

    @staticmethod
    def warning_label() -> str:
        """Warning label styling."""
        return f"color: {Colors.ACCENT_WARNING}; margin-bottom: 10px; font-weight: bold;"

    @staticmethod
    def progress_bar_error() -> str:
        """Progress bar error state styling."""
        return f"""
            QProgressBar::chunk {{
                background-color: {Colors.ACCENT_ERROR};
                border-radius: {Spacing.RADIUS_SM}px;
            }}
        """

    @staticmethod
    def icon_button_flat() -> str:
        """Flat icon button with no border."""
        return "QToolButton { border: none; padding: 0px; }"

    @staticmethod
    def transparent_overlay() -> str:
        """Transparent overlay styling."""
        return f"background: transparent; color: {Colors.TEXT_WHITE};"

    @staticmethod
    def dark_background() -> str:
        """Dark background styling for image containers."""
        return f"background-color: {Colors.BG_PRIMARY}; border: none;"


# Utility functions
def icon_color(active: bool = False, disabled: bool = False) -> str:
    """Get appropriate icon color based on state."""
    if disabled:
        return Colors.ICON_DISABLED
    if active:
        return Colors.ICON_ACTIVE
    return Colors.ICON_DEFAULT


def hover_color(base_color: str, lighten: int = 20) -> str:
    """
    Generate a hover color by lightening the base color.

    Args:
        base_color: Hex color string (e.g., "#f7673a")
        lighten: Amount to lighten (0-255)

    Returns:
        Lightened hex color string
    """
    if not base_color.startswith('#'):
        return base_color

    hex_color = base_color.lstrip('#')
    if len(hex_color) != 6:
        return base_color

    try:
        r = min(255, int(hex_color[0:2], 16) + lighten)
        g = min(255, int(hex_color[2:4], 16) + lighten)
        b = min(255, int(hex_color[4:6], 16) + lighten)
        return f"#{r:02x}{g:02x}{b:02x}"
    except ValueError:
        return base_color


def rounded_effect(radius: int = None):
    """
    Create a rounded corner graphics effect with theme-consistent radius.

    Args:
        radius: Corner radius in pixels. Defaults to Spacing.RADIUS_XL (10px)

    Returns:
        RoundedCornerGraphicsEffect instance

    Usage:
        thumbnail.setGraphicsEffect(rounded_effect())
        avatar.setGraphicsEffect(rounded_effect(Spacing.RADIUS_ROUND))
    """
    from src.ui.widgets.rounded_effect import RoundedCornerGraphicsEffect
    if radius is None:
        radius = Spacing.RADIUS_XL
    return RoundedCornerGraphicsEffect(radius)
