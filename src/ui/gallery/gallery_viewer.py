"""
DEPRECATED: This module has been replaced by gallery_post_view.py

This file was the original gallery viewer implementation but has been superseded
by GalleryPostView which provides superior functionality including:
- Better media preview with zoom controls
- Responsive thumbnail grid
- Improved navigation
- Better integration with the post system
- Enhanced performance

All functionality has been migrated to src/ui/gallery_post_view.py.
This file is kept only as a stub to prevent import errors.

This module will be removed in a future version.
"""

import warnings


class DeprecatedGalleryViewerError(Exception):
    """Raised when attempting to use deprecated gallery viewer components."""
    pass


def _raise_deprecation_error():
    """Raise an error indicating this module is deprecated."""
    raise DeprecatedGalleryViewerError(
        "gallery_viewer.py has been deprecated and replaced by gallery_post_view.py. "
        "Please update your imports to use GalleryPostView from src.ui.gallery.gallery_post_view instead."
    )


class MediaViewerWidget:
    """DEPRECATED: Use MediaPreviewWidget from gallery_post_view.py instead."""

    def __init__(self, *args, **kwargs):
        _raise_deprecation_error()


class GalleryCarousel:
    """DEPRECATED: Use GalleryPostView from gallery_post_view.py instead."""

    def __init__(self, *args, **kwargs):
        _raise_deprecation_error()


# Emit deprecation warning when module is imported
warnings.warn(
    "gallery_viewer.py is deprecated and will be removed in a future version. "
    "Use gallery_post_view.py instead.",
    DeprecationWarning,
    stacklevel=2
)
