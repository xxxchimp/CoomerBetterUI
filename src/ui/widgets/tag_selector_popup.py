"""Tag selector popup widget with grouped tag list"""
from typing import List, Dict, Set
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
                              QScrollArea, QLabel, QCheckBox, QPushButton, QFrame)
from PyQt6.QtCore import Qt, pyqtSignal
import qtawesome as qta
from src.ui.common.theme import Colors, Fonts, Spacing


class TagSelectorPopup(QWidget):
    """Popup widget for selecting tags with grouped list and search"""
    tags_changed = pyqtSignal(list)  # Emits list of selected tag names
    
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("tagSelectorPopup")
        self.setFixedWidth(350)
        self.setMaximumHeight(500)
        
        self.all_tags: List[Dict] = []  # List of {name: str, count: int}
        self.selected_tags: Set[str] = set()
        self.tag_checkboxes: Dict[str, QCheckBox] = {}
        
        self._setup_ui()
    
    def _setup_ui(self):
        """Create the UI layout"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        
        # Search box
        self.search_input = QLineEdit()
        self.search_input.setObjectName("tagSearchInput")
        self.search_input.setPlaceholderText("Search tags...")
        self.search_input.setFixedHeight(32)
        self.search_input.textChanged.connect(self._filter_tags)
        layout.addWidget(self.search_input)
        
        # Scrollable tag list
        scroll = QScrollArea()
        scroll.setObjectName("tagScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        self.tags_container = QWidget()
        self.tags_layout = QVBoxLayout(self.tags_container)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setSpacing(0)
        self.tags_layout.addStretch()
        
        scroll.setWidget(self.tags_container)
        layout.addWidget(scroll, 1)
        
        # Bottom buttons
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(8)
        
        self.clear_all_btn = QPushButton("Clear All")
        self.clear_all_btn.setObjectName("clearAllButton")
        self.clear_all_btn.setFixedHeight(32)
        self.clear_all_btn.clicked.connect(self._clear_all)
        buttons_layout.addWidget(self.clear_all_btn)
        
        buttons_layout.addStretch()
        
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setObjectName("applyButton")
        self.apply_btn.setFixedHeight(32)
        self.apply_btn.setFixedWidth(80)
        self.apply_btn.clicked.connect(self._apply)
        buttons_layout.addWidget(self.apply_btn)
        
        layout.addLayout(buttons_layout)
        
        # Style
        self.setStyleSheet(f"""
            QWidget#tagSelectorPopup {{
                background-color: {Colors.BG_SECONDARY};
                border: 1px solid {Colors.BORDER_LIGHT};
                border-radius: {Spacing.RADIUS_MD}px;
            }}
            QLineEdit#tagSearchInput {{
                background-color: {Colors.BG_TERTIARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_SM}px;
                padding: 6px;
                color: {Colors.TEXT_PRIMARY};
            }}
            QScrollArea#tagScrollArea {{
                background-color: transparent;
                border: none;
            }}
            QLabel#tagGroupLabel {{
                color: {Colors.ACCENT_PRIMARY};
                font-size: {Fonts.SIZE_MD}px;
                font-weight: bold;
                padding: 8px 4px 4px 4px;
                background-color: {Colors.BG_SECONDARY};
            }}
            QCheckBox {{
                color: {Colors.TEXT_PRIMARY};
                padding: 4px;
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border: 2px solid {Colors.BORDER_DEFAULT};
                border-radius: 3px;
                background-color: {Colors.BG_TERTIARY};
            }}
            QCheckBox::indicator:checked {{
                background-color: {Colors.ACCENT_PRIMARY};
                border-color: {Colors.ACCENT_PRIMARY};
            }}
            QCheckBox::indicator:hover {{
                border-color: {Colors.BORDER_LIGHT};
            }}
            QCheckBox:hover {{
                background-color: {Colors.BG_TERTIARY};
            }}
            QPushButton#clearAllButton, QPushButton#applyButton {{
                background-color: {Colors.BG_TERTIARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_SM}px;
                padding: 6px 12px;
                color: {Colors.TEXT_PRIMARY};
            }}
            QPushButton#clearAllButton:hover, QPushButton#applyButton:hover {{
                background-color: {Colors.BG_HOVER};
                border-color: {Colors.BORDER_LIGHT};
            }}
            QPushButton#applyButton {{
                background-color: {Colors.ACCENT_PRIMARY};
                border-color: {Colors.ACCENT_PRIMARY};
                font-weight: bold;
            }}
            QPushButton#applyButton:hover {{
                background-color: #e85a2f;
            }}
        """)
    
    def set_tags(self, tags: List[Dict], selected: List[str] = None):
        """
        Set available tags and selected state
        
        Args:
            tags: List of dicts with 'name' and 'count' keys
            selected: List of currently selected tag names
        """
        self.all_tags = tags
        self.selected_tags = set(selected) if selected else set()
        self._rebuild_tag_list()
    
    def _rebuild_tag_list(self):
        """Rebuild the tag list with grouping"""
        # Clear existing checkboxes
        while self.tags_layout.count() > 1:  # Keep the stretch
            item = self.tags_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        self.tag_checkboxes.clear()
        
        if not self.all_tags:
            no_tags_label = QLabel("No tags available")
            no_tags_label.setObjectName("noTagsLabel")
            no_tags_label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; padding: 20px; text-align: center;")
            no_tags_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tags_layout.insertWidget(0, no_tags_label)
            return
        
        # Group tags by first character
        groups: Dict[str, List[Dict]] = {}
        for tag in self.all_tags:
            first_char = tag['name'][0].upper() if tag['name'] else '#'
            if not first_char.isalpha():
                first_char = '#'
            if first_char not in groups:
                groups[first_char] = []
            groups[first_char].append(tag)
        
        # Sort groups and tags within groups
        for group_tags in groups.values():
            group_tags.sort(key=lambda t: t['name'].lower())
        
        # Add groups to layout
        for char in sorted(groups.keys()):
            # Group header
            header = QLabel(char)
            header.setObjectName("tagGroupLabel")
            self.tags_layout.insertWidget(self.tags_layout.count() - 1, header)
            
            # Tag checkboxes
            for tag in groups[char]:
                checkbox = QCheckBox(f"{tag['name']} ({tag['count']})")
                checkbox.setChecked(tag['name'] in self.selected_tags)
                checkbox.stateChanged.connect(
                    lambda state, name=tag['name']: self._on_tag_toggled(name, state)
                )
                self.tag_checkboxes[tag['name']] = checkbox
                self.tags_layout.insertWidget(self.tags_layout.count() - 1, checkbox)
    
    def _filter_tags(self, search_text: str):
        """Filter visible tags based on search text"""
        search_lower = search_text.lower()
        
        for tag_name, checkbox in self.tag_checkboxes.items():
            # Show if search is empty or tag name contains search text
            visible = not search_text or search_lower in tag_name.lower()
            checkbox.setVisible(visible)
        
        # Hide/show group headers based on visibility of their tags
        for i in range(self.tags_layout.count() - 1):  # Exclude stretch
            widget = self.tags_layout.itemAt(i).widget()
            if isinstance(widget, QLabel) and widget.objectName() == "tagGroupLabel":
                # Check if any following checkboxes are visible until next header
                has_visible = False
                for j in range(i + 1, self.tags_layout.count() - 1):
                    next_widget = self.tags_layout.itemAt(j).widget()
                    if isinstance(next_widget, QLabel) and next_widget.objectName() == "tagGroupLabel":
                        break
                    if isinstance(next_widget, QCheckBox) and next_widget.isVisible():
                        has_visible = True
                        break
                widget.setVisible(has_visible)
    
    def _on_tag_toggled(self, tag_name: str, state: int):
        """Handle tag checkbox toggle"""
        if state == Qt.CheckState.Checked.value:
            self.selected_tags.add(tag_name)
        else:
            self.selected_tags.discard(tag_name)
    
    def _clear_all(self):
        """Clear all selected tags"""
        self.selected_tags.clear()
        for checkbox in self.tag_checkboxes.values():
            checkbox.setChecked(False)
        self.tags_changed.emit([])
        self.hide()
    
    def _apply(self):
        """Apply current selection and close popup"""
        self.tags_changed.emit(list(self.selected_tags))
        self.hide()
    
    def get_selected_tags(self) -> List[str]:
        """Get list of currently selected tag names"""
        return list(self.selected_tags)
