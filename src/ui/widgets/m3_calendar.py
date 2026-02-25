"""
Docked calendar widget with Material-like header controls.
"""
from __future__ import annotations

from PyQt6.QtCore import QDate, QLocale, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QStackedWidget,
    QListWidget,
    QListWidgetItem,
    QToolButton,
    QSizePolicy,
    QGridLayout,
    QLabel,
)
from src.ui.common.theme import Colors


class M3DockedCalendar(QWidget):
    """
    Calendar with month/year pickers docked into the calendar body.
    """

    selectionChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False
        self._max_date = QDate.currentDate()
        self._selected_date = QDate.currentDate()
        self._current_year = self._selected_date.year()
        self._current_month = self._selected_date.month()
        self._cell_width = 40
        self._cell_height = 36
        self._cell_radius = 18
        self._cell_spacing = 6
        self._widget_disabled = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QWidget()
        header.setObjectName("popularCalendarHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)

        # Month button: rounded on left, flat on right
        month_button_style = (
            "QToolButton {"
            f"  background-color: transparent;"
            f"  color: {Colors.TEXT_PRIMARY};"
            f"  border: none;"
            f"  padding: 4px 8px;"
            f"  font-size: 14px;"
            f"  border-top-left-radius: 4px;"
            f"  border-bottom-left-radius: 4px;"
            f"  border-top-right-radius: 0px;"
            f"  border-bottom-right-radius: 0px;"
            "}"
            "QToolButton:hover {"
            f"  background-color: {Colors.BG_HOVER};"
            "}"
            "QToolButton:disabled {"
            f"  color: {Colors.TEXT_DISABLED};"
            f"  background-color: {Colors.STATE_DISABLED_BG};"
            "}"
        )

        # Year button: flat on left, rounded on right
        year_button_style = (
            "QToolButton {"
            f"  background-color: transparent;"
            f"  color: {Colors.TEXT_PRIMARY};"
            f"  border: none;"
            f"  padding: 4px 8px;"
            f"  font-size: 14px;"
            f"  border-top-left-radius: 0px;"
            f"  border-bottom-left-radius: 0px;"
            f"  border-top-right-radius: 4px;"
            f"  border-bottom-right-radius: 4px;"
            "}"
            "QToolButton:hover {"
            f"  background-color: {Colors.BG_HOVER};"
            "}"
            "QToolButton:disabled {"
            f"  color: {Colors.TEXT_DISABLED};"
            f"  background-color: {Colors.STATE_DISABLED_BG};"
            "}"
        )

        self.month_button = QToolButton()
        self.month_button.setObjectName("popularCalendarMonthButton")
        self.month_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.month_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.month_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.month_button.setAutoRaise(True)
        self.month_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.month_button.setStyleSheet(month_button_style)
        self.month_button.clicked.connect(self._show_month_picker)
        header_layout.addWidget(self.month_button)

        self.year_button = QToolButton()
        self.year_button.setObjectName("popularCalendarYearButton")
        self.year_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.year_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.year_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.year_button.setAutoRaise(True)
        self.year_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.year_button.setStyleSheet(year_button_style)
        self.year_button.clicked.connect(self._show_year_picker)
        header_layout.addWidget(self.year_button)

        layout.addWidget(header)

        self.stack = QStackedWidget()
        self.stack.setObjectName("popularCalendarStack")
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.calendar_page = QWidget()
        self.calendar_page.setObjectName("popularCalendarPage")
        page_layout = QVBoxLayout(self.calendar_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(6)

        weekday_row = QHBoxLayout()
        weekday_row.setContentsMargins(0, 0, 0, 0)
        weekday_row.setSpacing(self._cell_spacing)
        weekday_label_style = (
            "QLabel {"
            f"  color: {Colors.TEXT_SECONDARY};"
            f"  background-color: transparent;"
            f"  border: none;"
            f"  font-weight: 600;"
            "}"
        )
        self._weekday_labels = []
        for name in self._weekday_names():
            label = QLabel(name)
            label.setObjectName("popularCalendarWeekdayLabel")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFixedSize(self._cell_width, 20)
            label.setStyleSheet(weekday_label_style)
            weekday_row.addWidget(label)
            self._weekday_labels.append(label)
        page_layout.addLayout(weekday_row)

        grid_container = QWidget()
        grid_container.setObjectName("popularCalendarGrid")
        grid_container.setStyleSheet("QWidget { border: none; background-color: transparent; }")
        self._grid = QGridLayout(grid_container)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(self._cell_spacing)
        self._grid.setVerticalSpacing(self._cell_spacing)
        self._day_buttons = []
        for row in range(6):
            for col in range(7):
                btn = QToolButton()
                btn.setObjectName("popularCalendarDayButton")
                btn.setAutoRaise(True)
                btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setFixedSize(self._cell_width, self._cell_height)
                btn.clicked.connect(lambda checked=False, b=btn: self._on_day_clicked(b))
                self._grid.addWidget(btn, row, col)
                self._day_buttons.append(btn)
        page_layout.addWidget(grid_container)

        self.stack.addWidget(self.calendar_page)

        self.month_list = QListWidget()
        self.month_list.setObjectName("popularCalendarMonthList")
        self.month_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.month_list.itemClicked.connect(self._on_month_selected)
        self.stack.addWidget(self.month_list)

        self.year_list = QListWidget()
        self.year_list.setObjectName("popularCalendarYearList")
        self.year_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.year_list.itemClicked.connect(self._on_year_selected)
        self.stack.addWidget(self.year_list)

        layout.addWidget(self.stack)

        self._populate_months()
        self._populate_years()
        self._sync_header()
        self._render_grid()

    def _weekday_names(self) -> list[str]:
        locale = QLocale()
        first_day = locale.firstDayOfWeek().value
        names = []
        for i in range(7):
            day = ((first_day - 1 + i) % 7) + 1
            name = locale.dayName(day, QLocale.FormatType.NarrowFormat)
            if not name:
                name = locale.dayName(day, QLocale.FormatType.ShortFormat)
            names.append(name[:1])
        return names

    def _populate_months(self) -> None:
        self.month_list.clear()
        locale = QLocale()
        for month in range(1, 13):
            item = QListWidgetItem(locale.monthName(month))
            item.setData(Qt.ItemDataRole.UserRole, month)
            self.month_list.addItem(item)
        self._update_month_list_enabled(self._current_year)

    def _populate_years(self) -> None:
        self.year_list.clear()
        max_year = self._max_date.year()
        for year in range(1990, max_year + 1):
            item = QListWidgetItem(str(year))
            item.setData(Qt.ItemDataRole.UserRole, year)
            self.year_list.addItem(item)

    def _update_month_list_enabled(self, year: int) -> None:
        max_year = self._max_date.year()
        max_month = self._max_date.month()
        for row in range(self.month_list.count()):
            item = self.month_list.item(row)
            month = int(item.data(Qt.ItemDataRole.UserRole))
            enabled = True
            if year == max_year and month > max_month:
                enabled = False
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEnabled if enabled else item.flags() & ~Qt.ItemFlag.ItemIsEnabled)

    def _sync_header(self) -> None:
        locale = QLocale()
        self.month_button.setText(locale.monthName(self._current_month))
        self.year_button.setText(str(self._current_year))
        self._sync_list_selection(self.month_list, self._current_month)
        self._sync_list_selection(self.year_list, self._current_year)

    def setSelectedDate(self, date: QDate) -> None:
        if date > self._max_date:
            date = self._max_date
        self._selected_date = date
        self._current_year = date.year()
        self._current_month = date.month()
        self._sync_header()
        self._render_grid()

    def selectedDate(self) -> QDate:
        return self._selected_date

    def setMaximumDate(self, date: QDate) -> None:
        self._max_date = date
        if self._selected_date > date:
            self._selected_date = date
        self._current_year = self._selected_date.year()
        self._current_month = self._selected_date.month()
        self._populate_years()
        self._update_month_list_enabled(self._current_year)
        self._sync_header()
        self._render_grid()

    def _sync_list_selection(self, list_widget: QListWidget, value: int) -> None:
        for row in range(list_widget.count()):
            item = list_widget.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == value:
                list_widget.setCurrentItem(item)
                list_widget.scrollToItem(item)
                break

    def _show_month_picker(self) -> None:
        self._sync_header()
        self.stack.setCurrentWidget(self.month_list)

    def _show_year_picker(self) -> None:
        self._sync_header()
        self.stack.setCurrentWidget(self.year_list)

    def _on_month_selected(self, item: QListWidgetItem) -> None:
        month = int(item.data(Qt.ItemDataRole.UserRole))
        max_day = QDate(self._current_year, month, 1).daysInMonth()
        day = min(self._selected_date.day(), max_day)
        new_date = QDate(self._current_year, month, day)
        if new_date > self._max_date:
            new_date = self._max_date
        self._current_month = new_date.month()
        self._current_year = new_date.year()
        self._selected_date = new_date
        self._update_month_list_enabled(self._current_year)
        self._sync_header()
        self._render_grid()
        self.stack.setCurrentWidget(self.calendar_page)
        self.selectionChanged.emit()

    def _on_year_selected(self, item: QListWidgetItem) -> None:
        year = int(item.data(Qt.ItemDataRole.UserRole))
        max_day = QDate(year, self._current_month, 1).daysInMonth()
        day = min(self._selected_date.day(), max_day)
        new_date = QDate(year, self._current_month, day)
        if new_date > self._max_date:
            new_date = self._max_date
        self._current_year = new_date.year()
        self._current_month = new_date.month()
        self._selected_date = new_date
        self._update_month_list_enabled(self._current_year)
        self._sync_header()
        self._render_grid()
        self.stack.setCurrentWidget(self.calendar_page)
        self.selectionChanged.emit()

    def _on_day_clicked(self, button: QToolButton) -> None:
        date = button.property("date_value")
        if not isinstance(date, QDate):
            return
        if date > self._max_date:
            return
        self._selected_date = date
        self._current_year = date.year()
        self._current_month = date.month()
        self._sync_header()
        self._render_grid()
        self.selectionChanged.emit()

    def _render_grid(self) -> None:
        first = QDate(self._current_year, self._current_month, 1)
        locale = QLocale()
        first_day = locale.firstDayOfWeek().value
        offset = (first.dayOfWeek() - first_day) % 7
        start = first.addDays(-offset)

        for index, btn in enumerate(self._day_buttons):
            date = start.addDays(index)
            in_month = date.month() == self._current_month and date.year() == self._current_year
            is_future = date > self._max_date
            disabled = is_future or not in_month
            is_today = date == QDate.currentDate()
            is_selected = date == self._selected_date

            btn.setText(str(date.day()))
            btn.setProperty("date_value", date)
            btn.setEnabled(not disabled)
            self._apply_day_style(btn, in_month, is_today, is_selected, disabled)

    def _apply_day_style(
        self,
        button: QToolButton,
        in_month: bool,
        is_today: bool,
        is_selected: bool,
        disabled: bool,
    ) -> None:
        # When entire widget is disabled, use uniform muted styling
        if self._widget_disabled:
            style = (
                "QToolButton {"
                f"background-color: transparent;"
                f"color: {Colors.TEXT_DISABLED};"
                f"border: none;"
                f"border-radius: {self._cell_radius}px;"
                "}"
            )
            button.setStyleSheet(style)
            return

        text_color = Colors.TEXT_PRIMARY
        hover_bg = "#333333"
        bg = "transparent"
        border = "none"
        if disabled:
            text_color = Colors.TEXT_DISABLED
            bg = Colors.STATE_DISABLED_BG
        if is_selected:
            bg = Colors.ACCENT_PRIMARY
            text_color = Colors.TEXT_INVERSE
            border = "none"
        elif is_today:
            border = f"1px solid {Colors.ACCENT_PRIMARY}"
        style = (
            "QToolButton {"
            f"background-color: {bg};"
            f"color: {text_color};"
            f"border: {border};"
            f"border-radius: {self._cell_radius}px;"
            "}"
            "QToolButton:hover {"
            f"background-color: {hover_bg};"
            "}"
        )
        if is_selected or disabled:
            style = style.replace(
                "QToolButton:hover {",
                f"QToolButton:hover {{ background-color: {bg}; ",
            )
        button.setStyleSheet(style)

    def setEnabled(self, enabled: bool) -> None:
        """Enable or disable the entire calendar."""
        self._widget_disabled = not enabled
        super().setEnabled(enabled)

        # Apply grey background when disabled
        if enabled:
            self.setStyleSheet("")
        else:
            self.setStyleSheet(f"background-color: {Colors.STATE_DISABLED_BG};")

        self.month_button.setEnabled(enabled)
        self.year_button.setEnabled(enabled)
        self.month_list.setEnabled(enabled)
        self.year_list.setEnabled(enabled)

        # Re-render grid to update all day styles
        self._render_grid()

        # Disable all day buttons when widget is disabled
        if not enabled:
            for btn in self._day_buttons:
                btn.setEnabled(False)

        for label in self._weekday_labels:
            label.setEnabled(enabled)
            if enabled:
                label.setStyleSheet(
                    "QLabel {"
                    f"  color: {Colors.TEXT_SECONDARY};"
                    f"  background-color: transparent;"
                    f"  border: none;"
                    f"  font-weight: 600;"
                    "}"
                )
            else:
                label.setStyleSheet(
                    "QLabel {"
                    f"  color: {Colors.TEXT_DISABLED};"
                    f"  background-color: transparent;"
                    f"  border: none;"
                    f"  font-weight: 600;"
                    "}"
                )
