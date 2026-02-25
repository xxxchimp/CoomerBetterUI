"""
Native Qt widgets for displaying posts, galleries, and content.

This module re-exports widgets from their respective modules for backward compatibility.
New code should import directly from the specific modules:
    - post_card.py: PostCard
    - post_grid.py: PostGridView
    - post_detail.py: PostDetailView
    - notification_widgets.py: ToastNotification, DownloadProgressBar
"""

# Re-export all public classes for backward compatibility
from src.ui.gallery.post_card import PostCard
from src.ui.gallery.post_grid import PostGridView
from src.ui.gallery.post_detail import PostDetailView
from src.ui.widgets.notification_widgets import ToastNotification, DownloadProgressBar

__all__ = [
    'PostCard',
    'PostGridView',
    'PostDetailView',
    'ToastNotification',
    'DownloadProgressBar',
]
