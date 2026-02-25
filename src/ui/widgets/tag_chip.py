"""Tag chip widget for displaying selected tags"""
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtCore import pyqtSignal, Qt
import qtawesome as qta
from src.ui.common.theme import Colors, Fonts, Spacing


class TagChip(QPushButton):
    """Removable tag chip widget"""
    removed = pyqtSignal(str)  # Emits tag name when removed

    def __init__(self, tag: str, parent=None):
        super().__init__(parent)
        self.tag = tag

        # Set text with × icon
        self.setText(f"{tag} ×")
        self.setObjectName("tagChip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Connect click to removal
        self.clicked.connect(self._on_remove)

        # Style
        self.setStyleSheet(f"""
            QPushButton#tagChip {{
                background-color: {Colors.ACCENT_PRIMARY};
                color: {Colors.TEXT_WHITE};
                border: none;
                border-radius: 5px;
                font-family: {Fonts.FAMILY};
                font-size: {Fonts.SIZE_SM}px;
                font-weight: {Fonts.WEIGHT_MEDIUM};
            }}
            QPushButton#tagChip:hover {{
                background-color: #e85a2f;
            }}
        """)
    
    def _on_remove(self):
        """Emit removed signal when clicked"""
        self.removed.emit(self.tag)
