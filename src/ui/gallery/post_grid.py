"""
Grid view for displaying multiple post cards.

Extracted from native_widgets.py to reduce file size and improve maintainability.
Uses theme.py for spacing constants and dark_theme_pro.qss for static widget styles.
"""
from typing import List, Set
import logging

from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtWidgets import QScrollArea, QWidget, QGridLayout

from src.core.dto.post import PostDTO
from src.ui.gallery.post_card import PostCard
from src.ui.common.theme import Spacing
from src.ui.video.video_preview import VideoPreviewManager

logger = logging.getLogger(__name__)


class PostGridView(QScrollArea):
    """
    Grid view displaying multiple post cards.

    Automatically reflows cards on resize and supports multi-selection.

    Signals:
        post_clicked(post): Emitted when a post card is clicked
        creator_clicked(post): Emitted when creator info is clicked
        load_more(): Emitted when more posts should be loaded
        selection_changed(count): Emitted when selection count changes
    """

    post_clicked = pyqtSignal(object)
    creator_clicked = pyqtSignal(object)
    load_more = pyqtSignal()
    selection_changed = pyqtSignal(int)  # Emits count of selected posts

    def __init__(self, parent=None):
        """Initialize grid view."""
        super().__init__(parent)
        self.setObjectName("postGridScrollArea")

        self.posts: List[PostDTO] = []
        self.post_cards: List[PostCard] = []
        self.selected_posts: Set[str] = set()
        self.platform = 'coomer'
        self._creator_lookup = None
        self._creators_manager = None
        self._preview_manager = None  # Lazy-initialized video preview manager

        # Debounce timer for resize events to reduce layout thrashing
        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(100)  # 100ms debounce
        self._resize_timer.timeout.connect(self._do_reflow)

        self._setup_ui()

    def _setup_ui(self):
        """Setup grid UI."""
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Container widget (styled by QSS via objectName)
        container = QWidget()
        container.setObjectName("postGridContainer")
        self.setWidget(container)

        # Grid layout
        self.grid_layout = QGridLayout(container)
        self.grid_layout.setSpacing(Spacing.LG)
        self.grid_layout.setContentsMargins(Spacing.XXL, Spacing.XXL, Spacing.XXL, Spacing.XXL)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        # Prewarm video preview to avoid first-hover flicker.
        QTimer.singleShot(0, self._prewarm_video_preview)

    def _prewarm_video_preview(self):
        try:
            preview_manager = self._get_preview_manager()
            preview_manager.prewarm(self.viewport())
        except Exception:
            pass

    def _get_preview_manager(self) -> VideoPreviewManager:
        """Get or create the video preview manager."""
        if self._preview_manager is None:
            try:
                from src.core.context import CoreContext
                ctx = CoreContext()
                self._preview_manager = VideoPreviewManager.instance(ctx)
            except Exception as e:
                logger.debug(f"Failed to create preview manager with context: {e}")
                self._preview_manager = VideoPreviewManager.instance(None)
        return self._preview_manager

    def get_selected_count(self) -> int:
        """Return the number of currently selected posts."""
        return sum(1 for card in self.post_cards if hasattr(card, 'select_btn') and card.select_btn.isChecked())

    def get_total_count(self) -> int:
        """Return the total number of posts currently loaded."""
        return len(self.posts)

    def set_posts(self, posts: List[PostDTO], platform: str = 'coomer'):
        """
        Set posts to display.

        Args:
            posts: List of PostDTO objects to display
            platform: Platform identifier (coomer/kemono)
        """
        # Disable updates during bulk widget operations to prevent flashing
        self.setUpdatesEnabled(False)

        self.clear()
        self.posts = posts
        self.platform = platform

        # Handle empty posts case - re-enable updates and return early
        if not posts:
            self.setUpdatesEnabled(True)
            self.update()
            return

        # Calculate grid dimensions and card width dynamically
        spacing = self.grid_layout.spacing() or Spacing.LG
        available = max(0, self.width() - self.grid_layout.contentsMargins().left() - self.grid_layout.contentsMargins().right())
        columns = max(1, available // Spacing.CARD_WIDTH)
        # Compute card width to better fill available space
        card_width = max(160, int((available - (columns - 1) * spacing) / columns))

        # Load posts in chunks asynchronously
        chunk_size = 10  # Load 10 posts at a time
        row = 0
        col = 0

        for i in range(0, len(posts), chunk_size):
            chunk = posts[i:i + chunk_size]
            chunk_idx = i // chunk_size
            delay = chunk_idx * 100  # 100ms between chunks
            is_last_chunk = (i + chunk_size >= len(posts))

            # Calculate starting row/col for this chunk
            start_row = row
            start_col = col

            QTimer.singleShot(delay, lambda c=chunk, r=start_row, co=start_col, last=is_last_chunk:
                             self._add_post_chunk(c, r, co, columns, card_width, last))

            # Update row/col for next chunk
            for _ in chunk:
                col += 1
                if col >= columns:
                    col = 0
                    row += 1
    
    def _add_post_chunk(self, posts_chunk: List[PostDTO], start_row: int, start_col: int, columns: int, card_width: int, is_last_chunk: bool = False):
        """Add a chunk of post cards at once."""
        row = start_row
        col = start_col
        
        preview_manager = self._get_preview_manager()

        for post in posts_chunk:
            card = PostCard(post, self.platform, creator_lookup=self._creator_lookup, creators_manager=self._creators_manager)
            card.selected_changed.connect(self._on_card_selected_changed)
            card.set_preview_manager(preview_manager)
            # Apply dynamic width - use update_size() to properly handle placeholder regeneration
            try:
                card.update_size(card_width)
            except Exception:
                pass
            card.clicked.connect(self.post_clicked.emit)
            card.creator_clicked.connect(self.creator_clicked.emit)

            self.grid_layout.addWidget(card, row, col)
            self.post_cards.append(card)

            col += 1
            if col >= columns:
                col = 0
                row += 1

        # Re-enable updates after last chunk is added
        if is_last_chunk:
            self.setUpdatesEnabled(True)
            # Force repaint to apply styling
            self.update()

    def add_posts(self, posts: List[PostDTO]):
        """
        Add posts to existing grid.

        Args:
            posts: List of PostDTO objects to add
        """
        self.posts.extend(posts)

        spacing = self.grid_layout.spacing() or Spacing.LG
        available = max(0, self.width() - self.grid_layout.contentsMargins().left() - self.grid_layout.contentsMargins().right())
        columns = max(1, available // Spacing.CARD_WIDTH)
        card_width = max(160, int((available - (columns - 1) * spacing) / columns))
        current_count = len(self.post_cards)

        row = current_count // columns
        col = current_count % columns

        preview_manager = self._get_preview_manager()

        for post in posts:
            card = PostCard(post, self.platform, creator_lookup=self._creator_lookup)
            card.selected_changed.connect(self._on_card_selected_changed)
            card.set_preview_manager(preview_manager)
            # Apply dynamic width - use update_size() to properly handle placeholder regeneration
            try:
                card.update_size(card_width)
            except Exception:
                pass
            card.clicked.connect(self.post_clicked.emit)
            card.creator_clicked.connect(self.creator_clicked.emit)

            self.grid_layout.addWidget(card, row, col)
            self.post_cards.append(card)

            col += 1
            if col >= columns:
                col = 0
                row += 1

    def clear(self):
        """Clear all posts."""
        for card in self.post_cards:
            card.deleteLater()

        # Use assignment (not .clear()) to avoid mutating lists that callers
        # may still reference (e.g. BrowserWindow.current_posts).
        self.post_cards = []
        self.posts = []

    def set_creator_lookup(self, resolver) -> None:
        """Set the creator name resolver callback."""
        self._creator_lookup = resolver
    
    def set_creators_manager(self, creators_manager) -> None:
        """Set the creators manager for avatar loading."""
        self._creators_manager = creators_manager

    def resizeEvent(self, event):
        """Handle resize to reflow grid (debounced)."""
        super().resizeEvent(event)
        # Restart timer on each resize event - only reflow after 100ms of no resizing
        self._resize_timer.start()

    def _do_reflow(self):
        """Perform actual grid reflow after debounce period."""
        # Reflow existing cards into new column count without recreating widgets
        try:
            margins = Spacing.XXL * 2  # Left + right margins
            spacing = self.grid_layout.spacing() or Spacing.LG
            available = max(0, self.width() - margins)
            columns = max(1, available // Spacing.CARD_WIDTH)
            
            # Calculate new card width to fill available space
            card_width = max(Spacing.CARD_WIDTH - Spacing.XXL, int((available - (columns - 1) * spacing) / columns))
            card_width = min(card_width, Spacing.CARD_WIDTH + Spacing.XXL)  # Cap max width

            # Remove all layout items without deleting widgets
            for i in reversed(range(self.grid_layout.count())):
                item = self.grid_layout.takeAt(i)
                if item:
                    w = item.widget()
                    if w:
                        self.grid_layout.removeWidget(w)

            # Re-add widgets in new positions with updated sizes
            for idx, card in enumerate(self.post_cards):
                row = idx // columns
                col = idx % columns
                # Update card dimensions on reflow (handles placeholder regeneration)
                try:
                    card.update_size(card_width)
                except Exception:
                    pass
                self.grid_layout.addWidget(card, row, col)
        except Exception:
            logger.exception('Error reflowing post grid on resize')

    def _on_card_selected_changed(self, post_data: PostDTO, selected: bool):
        """Handle card selection change."""
        pid = post_data.id
        if not pid:
            return
        if selected:
            self.selected_posts.add(pid)
        else:
            self.selected_posts.discard(pid)

        # Emit selection changed signal
        self.selection_changed.emit(len(self.selected_posts))

    def get_selected_posts(self) -> List[PostDTO]:
        """Return list of selected posts."""
        return [p for p in self.posts if p.id in self.selected_posts]

    def select_all(self):
        """Select all posts in the grid."""
        for card in self.post_cards:
            if hasattr(card, 'select_btn') and not card.select_btn.isChecked():
                card.select_btn.setChecked(True)

    def deselect_all(self):
        """Deselect all posts in the grid."""
        for card in self.post_cards:
            if hasattr(card, 'select_btn') and card.select_btn.isChecked():
                card.select_btn.setChecked(False)
