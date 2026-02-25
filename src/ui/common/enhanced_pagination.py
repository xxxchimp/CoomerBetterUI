"""
Enhanced pagination widget with first/last buttons and page jump.

Uses theme.py for dynamic styling and dark_theme_pro.qss for static widget styles.
"""
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QPushButton, QLabel,
                             QLineEdit, QSizePolicy)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QIntValidator
import logging
import qtawesome as qta

from src.ui.common.theme import Colors, Fonts, Spacing, Styles

logger = logging.getLogger(__name__)


class EnhancedPagination(QWidget):
    """
    Enhanced pagination control with:
    - First/Previous/Next/Last buttons
    - Page number input for jumping
    - Current page / total pages display
    """

    page_changed = pyqtSignal(int)  # New page number

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_page = 0
        self.total_pages = 1
        self._setup_ui()

    def _setup_ui(self):
        """Setup UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.SM)

        # First button (styled by QSS via objectName)
        self.first_btn = QPushButton("First")
        self.first_btn.setObjectName("paginationButton")
        self.first_btn.setIcon(qta.icon('fa5s.fast-backward', color=Colors.TEXT_PRIMARY))
        self.first_btn.setFixedSize(70, Spacing.BUTTON_HEIGHT)
        self.first_btn.clicked.connect(lambda: self._go_to_page(0))
        self.first_btn.setToolTip("Go to first page")
        layout.addWidget(self.first_btn)

        # Previous button
        self.prev_btn = QPushButton("Prev")
        self.prev_btn.setObjectName("paginationButton")
        self.prev_btn.setIcon(qta.icon('fa5s.chevron-left', color=Colors.TEXT_PRIMARY))
        self.prev_btn.setFixedSize(70, Spacing.BUTTON_HEIGHT)
        self.prev_btn.clicked.connect(lambda: self._go_to_page(self.current_page - 1))
        self.prev_btn.setToolTip("Previous page")
        layout.addWidget(self.prev_btn)

        # Page input container
        page_container = QWidget()
        page_layout = QHBoxLayout(page_container)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(Spacing.XS)

        page_label = QLabel("Page")
        page_label.setStyleSheet(Styles.label(color=Colors.TEXT_SECONDARY))
        page_layout.addWidget(page_label)

        self.page_input = QLineEdit()
        self.page_input.setObjectName("pageJumpInput")
        self.page_input.setFixedSize(50, 28)
        self.page_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_input.setValidator(QIntValidator(1, 999999))
        self.page_input.returnPressed.connect(self._on_page_input)
        self.page_input.setToolTip("Enter page number and press Enter")
        page_layout.addWidget(self.page_input)

        self.page_total_label = QLabel("/ 1")
        self.page_total_label.setObjectName("pageInfoLabel")
        page_layout.addWidget(self.page_total_label)

        layout.addWidget(page_container)

        # Next button
        self.next_btn = QPushButton("Next")
        self.next_btn.setObjectName("paginationButton")
        self.next_btn.setIcon(qta.icon('fa5s.chevron-right', color=Colors.TEXT_PRIMARY))
        self.next_btn.setFixedSize(70, Spacing.BUTTON_HEIGHT)
        self.next_btn.clicked.connect(lambda: self._go_to_page(self.current_page + 1))
        self.next_btn.setToolTip("Next page")
        layout.addWidget(self.next_btn)

        # Last button
        self.last_btn = QPushButton("Last")
        self.last_btn.setObjectName("paginationButton")
        self.last_btn.setIcon(qta.icon('fa5s.fast-forward', color=Colors.TEXT_PRIMARY))
        self.last_btn.setFixedSize(70, Spacing.BUTTON_HEIGHT)
        self.last_btn.clicked.connect(lambda: self._go_to_page(self.total_pages - 1))
        self.last_btn.setToolTip("Go to last page")
        layout.addWidget(self.last_btn)

        layout.addStretch()

        self._update_buttons()

    def set_page(self, current: int, total: int):
        """
        Update pagination state.

        Args:
            current: Current page (0-indexed)
            total: Total number of pages
        """
        self.current_page = max(0, min(current, total - 1))
        self.total_pages = max(1, total)

        # Update display
        self.page_input.setText(str(self.current_page + 1))
        self.page_total_label.setText(f"/ {self.total_pages}")

        self._update_buttons()

    def _update_buttons(self):
        """Update button states."""
        # Disable first/prev if on first page
        self.first_btn.setEnabled(self.current_page > 0)
        self.prev_btn.setEnabled(self.current_page > 0)

        # Disable next/last if on last page
        self.next_btn.setEnabled(self.current_page < self.total_pages - 1)
        self.last_btn.setEnabled(self.current_page < self.total_pages - 1)

    def _go_to_page(self, page: int):
        """
        Navigate to specific page.

        Args:
            page: Page number (0-indexed)
        """
        # Clamp to valid range
        page = max(0, min(page, self.total_pages - 1))

        if page != self.current_page:
            self.current_page = page
            self.page_input.setText(str(page + 1))
            self._update_buttons()
            self.page_changed.emit(page)

    def _on_page_input(self):
        """Handle page input submission."""
        try:
            # Convert from 1-indexed to 0-indexed
            page = int(self.page_input.text()) - 1
            self._go_to_page(page)
        except ValueError:
            # Invalid input, reset to current page
            self.page_input.setText(str(self.current_page + 1))


class CompactPagination(QWidget):
    """
    Compact pagination for sidebars - just prev/next and page display.
    """

    page_changed = pyqtSignal(int)  # New page number

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_page = 0
        self.total_pages = 1
        self._setup_ui()

    def _setup_ui(self):
        """Setup UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.SM - 2)

        # Previous button
        self.prev_btn = QPushButton()
        self.prev_btn.setIcon(qta.icon('fa5s.chevron-left', color=Colors.TEXT_PRIMARY))
        self.prev_btn.setFixedSize(Spacing.BUTTON_HEIGHT, 28)
        self._apply_compact_button_style(self.prev_btn)
        self.prev_btn.clicked.connect(lambda: self._go_to_page(self.current_page - 1))
        layout.addWidget(self.prev_btn)

        # Page input (styled like main pagination)
        page_container = QWidget()
        page_layout = QHBoxLayout(page_container)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(Spacing.XS)

        self.page_input = QLineEdit()
        self.page_input.setObjectName("pageJumpInput")
        self.page_input.setFixedSize(46, 28)
        self.page_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_input.setValidator(QIntValidator(1, 999999))
        self.page_input.returnPressed.connect(self._on_page_input)
        page_layout.addWidget(self.page_input)

        self.page_total_label = QLabel("/ 1")
        self.page_total_label.setObjectName("pageInfoLabel")
        self.page_total_label.setStyleSheet(Styles.label(color=Colors.TEXT_SECONDARY, size=Fonts.SIZE_SM))
        page_layout.addWidget(self.page_total_label)

        layout.addWidget(page_container)

        # Next button
        self.next_btn = QPushButton()
        self.next_btn.setIcon(qta.icon('fa5s.chevron-right', color=Colors.TEXT_PRIMARY))
        self.next_btn.setFixedSize(Spacing.BUTTON_HEIGHT, 28)
        self._apply_compact_button_style(self.next_btn)
        self.next_btn.clicked.connect(lambda: self._go_to_page(self.current_page + 1))
        layout.addWidget(self.next_btn)

        self._update_buttons()

    def set_page(self, current: int, total: int):
        """Update pagination state."""
        # Batch updates to prevent intermediate repaints
        self.setUpdatesEnabled(False)
        try:
            self.current_page = max(0, min(current, total - 1))
            self.total_pages = max(1, total)

            self.page_input.setText(str(self.current_page + 1))
            self.page_total_label.setText(f"/ {self.total_pages}")
            self._update_buttons()
        finally:
            self.setUpdatesEnabled(True)

    def _update_buttons(self):
        """Update button states."""
        self.prev_btn.setEnabled(self.current_page > 0)
        self.next_btn.setEnabled(self.current_page < self.total_pages - 1)

    @staticmethod
    def _apply_compact_button_style(button: QPushButton) -> None:
        button.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BG_TERTIARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_MD}px;
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
            }}
        """)

    def _go_to_page(self, page: int):
        """Navigate to specific page."""
        page = max(0, min(page, self.total_pages - 1))

        if page != self.current_page:
            # Batch updates to prevent intermediate repaints
            self.setUpdatesEnabled(False)
            try:
                self.current_page = page
                self.page_input.setText(str(page + 1))
                self.page_total_label.setText(f"/ {self.total_pages}")
                self._update_buttons()
            finally:
                self.setUpdatesEnabled(True)
            
            self.page_changed.emit(page)

    def _on_page_input(self):
        try:
            page = int(self.page_input.text()) - 1
            self._go_to_page(page)
        except ValueError:
            self.page_input.setText(str(self.current_page + 1))
