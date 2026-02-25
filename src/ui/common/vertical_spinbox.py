"""
Custom vertical spinbox with QtAwesome icons for up/down buttons
"""
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, 
                             QPushButton, QSizePolicy)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent
from PyQt6.QtGui import QIntValidator
import qtawesome as qta
from src.ui.common.theme import Colors, Spacing


class VerticalSpinBox(QWidget):
    """
    A spin box with vertically stacked up/down buttons using QtAwesome icons.
    Provides the same interface as QSpinBox for easy replacement.
    """
    valueChanged = pyqtSignal(int)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._minimum = 0
        self._maximum = 99
        self._value = 0
        self._suffix = ""
        self._single_step = 1
        self._editing = False
        
        self._setup_ui()
        self._update_display()
    
    def _setup_ui(self):
        """Create the UI with value display and vertical buttons"""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Value display (editable)
        self._line_edit = QLineEdit()
        self._line_edit.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._line_edit.setValidator(QIntValidator(self._minimum, self._maximum))
        self._line_edit.editingFinished.connect(self._on_text_edited)
        self._line_edit.installEventFilter(self)
        self._line_edit.setStyleSheet(f"""
            QLineEdit {{
                background-color: {Colors.BG_SECONDARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-right: none;
                border-radius: 4px 0 0 4px;
                padding: 4px 8px;
                color: {Colors.TEXT_PRIMARY};
                min-height: {Spacing.BUTTON_HEIGHT - 10}px;
            }}
            QLineEdit:focus {{
                border-color: {Colors.ACCENT_PRIMARY};
            }}
        """)
        layout.addWidget(self._line_edit, 1)
        
        # Button container
        button_container = QWidget()
        button_layout = QVBoxLayout(button_container)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(0)
        
        # Up button
        self._up_btn = QPushButton()
        self._up_btn.setIcon(qta.icon('fa5s.chevron-up', color=Colors.TEXT_SECONDARY))
        self._up_btn.clicked.connect(self._increment)
        self._up_btn.setAutoRepeat(True)
        self._up_btn.setAutoRepeatInterval(50)
        self._up_btn.setAutoRepeatDelay(300)
        self._up_btn.setFixedSize(28, 16)
        self._up_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._up_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BG_TERTIARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-left: none;
                border-bottom: none;
                border-radius: 0 4px 0 0;
                padding: 0;
            }}
            QPushButton:hover {{
                background-color: {Colors.BG_HOVER};
            }}
            QPushButton:pressed {{
                background-color: {Colors.ACCENT_PRIMARY};
            }}
        """)
        button_layout.addWidget(self._up_btn)
        
        # Down button
        self._down_btn = QPushButton()
        self._down_btn.setIcon(qta.icon('fa5s.chevron-down', color=Colors.TEXT_SECONDARY))
        self._down_btn.clicked.connect(self._decrement)
        self._down_btn.setAutoRepeat(True)
        self._down_btn.setAutoRepeatInterval(50)
        self._down_btn.setAutoRepeatDelay(300)
        self._down_btn.setFixedSize(28, 16)
        self._down_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._down_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BG_TERTIARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-left: none;
                border-radius: 0 0 4px 0;
                padding: 0;
            }}
            QPushButton:hover {{
                background-color: {Colors.BG_HOVER};
            }}
            QPushButton:pressed {{
                background-color: {Colors.ACCENT_PRIMARY};
            }}
        """)
        button_layout.addWidget(self._down_btn)
        
        layout.addWidget(button_container)
    
    def _update_display(self):
        """Update the displayed value with suffix"""
        if self._editing:
            self._line_edit.setText(str(self._value))
            return
        display_text = str(self._value)
        if self._suffix:
            display_text += self._suffix
        self._line_edit.setText(display_text)
    
    def _on_text_edited(self):
        """Handle manual text input"""
        text = self._line_edit.text()
        # Remove suffix if present
        if self._suffix and text.endswith(self._suffix):
            text = text[:-len(self._suffix)]
        try:
            new_value = int(text.strip())
            self.setValue(new_value)
        except ValueError:
            self._update_display()  # Revert to current value
    
    def _increment(self):
        """Increase value by single step"""
        self._commit_edit()
        self.setValue(self._value + self._single_step)
    
    def _decrement(self):
        """Decrease value by single step"""
        self._commit_edit()
        self.setValue(self._value - self._single_step)

    def _commit_edit(self):
        """Commit any pending text edits."""
        if self._editing:
            self._on_text_edited()
    
    # QSpinBox compatible interface
    def value(self) -> int:
        return self._value
    
    def setValue(self, value: int):
        value = max(self._minimum, min(self._maximum, value))
        if value != self._value:
            self._value = value
            self._update_display()
            self.valueChanged.emit(self._value)
    
    def minimum(self) -> int:
        return self._minimum
    
    def setMinimum(self, minimum: int):
        self._minimum = minimum
        self._update_validator()
        if self._value < minimum:
            self.setValue(minimum)
    
    def maximum(self) -> int:
        return self._maximum
    
    def setMaximum(self, maximum: int):
        self._maximum = maximum
        self._update_validator()
        if self._value > maximum:
            self.setValue(maximum)
    
    def setRange(self, minimum: int, maximum: int):
        self._minimum = minimum
        self._maximum = maximum
        self._update_validator()
        if self._value < minimum:
            self.setValue(minimum)
        elif self._value > maximum:
            self.setValue(maximum)
    
    def suffix(self) -> str:
        return self._suffix
    
    def setSuffix(self, suffix: str):
        self._suffix = suffix
        self._update_display()
    
    def singleStep(self) -> int:
        return self._single_step
    
    def setSingleStep(self, step: int):
        self._single_step = step
    
    def setToolTip(self, tip: str):
        super().setToolTip(tip)
        self._line_edit.setToolTip(tip)
    
    def setObjectName(self, name: str):
        super().setObjectName(name)
        self._line_edit.setObjectName(f"{name}Edit")
    
    def setEnabled(self, enabled: bool):
        super().setEnabled(enabled)
        self._line_edit.setEnabled(enabled)
        self._up_btn.setEnabled(enabled)
        self._down_btn.setEnabled(enabled)

    def _update_validator(self):
        self._line_edit.setValidator(QIntValidator(self._minimum, self._maximum))

    def eventFilter(self, obj, event):
        if obj is self._line_edit:
            if event.type() == QEvent.Type.FocusIn:
                self._editing = True
                self._line_edit.setText(str(self._value))
                self._line_edit.selectAll()
            elif event.type() == QEvent.Type.FocusOut:
                self._editing = False
                self._on_text_edited()
                self._update_display()
        return super().eventFilter(obj, event)
