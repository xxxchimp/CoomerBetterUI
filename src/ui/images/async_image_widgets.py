"""
Async Image Loading Widgets

Centralized widgets for asynchronous image loading with automatic cleanup.
Eliminates boilerplate signal connection/disconnection code across UI.
"""
from __future__ import annotations

from typing import Callable, Optional, Tuple
from PyQt6.QtWidgets import QLabel
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap
import logging

logger = logging.getLogger(__name__)


# Type aliases
ImageLoadedCallback = Callable[[str, QPixmap], None]
ImageFailedCallback = Callable[[str, str], None]


class AsyncImageLabel(QLabel):
    """
    QLabel with built-in async image loading support.

    Automatically handles:
    - Image loader signal connections/disconnections
    - Widget destruction cleanup
    - URL validation
    - Error handling

    Example:
        >>> label = AsyncImageLabel()
        >>> label.load_image(
        ...     url="https://example.com/image.jpg",
        ...     target_size=(200, 200),
        ...     on_loaded=lambda url, pix: label.setPixmap(pix),
        ...     on_failed=lambda url, err: print(f"Failed: {err}")
        ... )
    """

    def __init__(self, parent=None, use_preview_loader: bool = False):
        """
        Initialize async image label.

        Args:
            parent: Parent widget
            use_preview_loader: Use preview loader (high priority) instead of grid loader
        """
        super().__init__(parent)
        self._use_preview_loader = use_preview_loader
        self._current_url: Optional[str] = None
        self._loader = None
        self._loaded_callback = None
        self._failed_callback = None

    def load_image(
        self,
        url: str,
        target_size: Optional[Tuple[int, int] | QSize] = None,
        on_loaded: Optional[ImageLoadedCallback] = None,
        on_failed: Optional[ImageFailedCallback] = None,
    ) -> None:
        """
        Load image asynchronously.

        Args:
            url: Image URL to load
            target_size: Target size for thumbnail generation (width, height)
            on_loaded: Callback(url, pixmap) called when image loads successfully
            on_failed: Callback(url, error) called when image load fails

        Example:
            >>> label.load_image(
            ...     "https://example.com/img.jpg",
            ...     target_size=(200, 200),
            ...     on_loaded=lambda url, pix: label.setPixmap(pix)
            ... )
        """
        # Cleanup previous load
        self._cleanup_loader()

        # Store current URL for validation
        self._current_url = url

        # Store callbacks
        self._loaded_callback = on_loaded
        self._failed_callback = on_failed

        # Get image loader
        from src.ui.images.image_loader_manager import get_image_loader_manager
        manager = get_image_loader_manager()
        self._loader = manager.preview_loader() if self._use_preview_loader else manager.grid_loader()

        # Connect signals
        self._loader.image_loaded.connect(self._on_image_loaded)
        self._loader.load_failed.connect(self._on_image_failed)

        # Convert target_size to tuple if needed
        if isinstance(target_size, QSize):
            target_size = (target_size.width(), target_size.height())

        # Load image
        self._loader.load_image(url, target_size=target_size)

    def _on_image_loaded(self, url: str, pixmap: QPixmap) -> None:
        """Handle image loaded signal"""
        # Validate URL matches current request
        if url != self._current_url:
            return

        # Call user callback
        if self._loaded_callback:
            try:
                self._loaded_callback(url, pixmap)
            except RuntimeError:
                # Widget was deleted during callback
                pass
            except Exception as e:
                logger.warning(f"Error in image loaded callback for {url}: {e}")

    def _on_image_failed(self, url: str, error: str) -> None:
        """Handle image load failed signal"""
        # Validate URL matches current request
        if url != self._current_url:
            return

        # Call user callback
        if self._failed_callback:
            try:
                self._failed_callback(url, error)
            except RuntimeError:
                # Widget was deleted during callback
                pass
            except Exception as e:
                logger.warning(f"Error in image failed callback for {url}: {e}")

    def _cleanup_loader(self) -> None:
        """Disconnect loader signals"""
        if self._loader:
            try:
                self._loader.image_loaded.disconnect(self._on_image_loaded)
            except Exception:
                pass
            try:
                self._loader.load_failed.disconnect(self._on_image_failed)
            except Exception:
                pass
            self._loader = None

        self._loaded_callback = None
        self._failed_callback = None
        self._current_url = None

    def hideEvent(self, event) -> None:
        """Override hide to cleanup loader"""
        self._cleanup_loader()
        super().hideEvent(event)

    def __del__(self) -> None:
        """Cleanup on destruction"""
        self._cleanup_loader()


class ImageLoadRequest:
    """
    Helper for one-off async image loads without creating a widget.

    Useful when you need to load an image and process it,
    but don't need a persistent QLabel widget.

    Example:
        >>> def process(url, pixmap):
        ...     print(f"Loaded {url}: {pixmap.size()}")
        >>>
        >>> request = ImageLoadRequest.load(
        ...     url="https://example.com/image.jpg",
        ...     target_size=(200, 200),
        ...     on_loaded=process
        ... )
        >>> # Keep request alive until load completes
    """

    def __init__(self):
        """Initialize request"""
        self._loader = None
        self._url: Optional[str] = None
        self._loaded_callback: Optional[ImageLoadedCallback] = None
        self._failed_callback: Optional[ImageFailedCallback] = None

    @staticmethod
    def load(
        url: str,
        target_size: Optional[Tuple[int, int] | QSize] = None,
        on_loaded: Optional[ImageLoadedCallback] = None,
        on_failed: Optional[ImageFailedCallback] = None,
        use_preview_loader: bool = False,
    ) -> 'ImageLoadRequest':
        """
        Create and start an image load request.

        Args:
            url: Image URL to load
            target_size: Target size for thumbnail generation
            on_loaded: Callback(url, pixmap) called on success
            on_failed: Callback(url, error) called on failure
            use_preview_loader: Use preview loader instead of grid loader

        Returns:
            ImageLoadRequest instance (keep reference until load completes)

        Example:
            >>> self.request = ImageLoadRequest.load(
            ...     url="https://example.com/img.jpg",
            ...     target_size=(200, 200),
            ...     on_loaded=self._handle_loaded,
            ...     on_failed=self._handle_failed
            ... )
        """
        request = ImageLoadRequest()
        request._url = url
        request._loaded_callback = on_loaded
        request._failed_callback = on_failed

        # Get image loader
        from src.ui.images.image_loader_manager import get_image_loader_manager
        manager = get_image_loader_manager()
        request._loader = manager.preview_loader() if use_preview_loader else manager.grid_loader()

        # Connect signals
        request._loader.image_loaded.connect(request._on_image_loaded)
        request._loader.load_failed.connect(request._on_image_failed)

        # Convert target_size to tuple if needed
        if isinstance(target_size, QSize):
            target_size = (target_size.width(), target_size.height())

        # Load image
        request._loader.load_image(url, target_size=target_size)

        return request

    def _on_image_loaded(self, url: str, pixmap: QPixmap) -> None:
        """Handle image loaded"""
        if url == self._url:
            if self._loaded_callback:
                try:
                    self._loaded_callback(url, pixmap)
                except Exception as e:
                    logger.warning(f"Error in image loaded callback for {url}: {e}")
            # Only cleanup when OUR url loads
            self._cleanup()

    def _on_image_failed(self, url: str, error: str) -> None:
        """Handle image failed"""
        if url == self._url:
            if self._failed_callback:
                try:
                    self._failed_callback(url, error)
                except Exception as e:
                    logger.warning(f"Error in image failed callback for {url}: {e}")
            # Only cleanup when OUR url fails
            self._cleanup()

    def _cleanup(self) -> None:
        """Disconnect signals"""
        if self._loader:
            try:
                self._loader.image_loaded.disconnect(self._on_image_loaded)
            except Exception:
                pass
            try:
                self._loader.load_failed.disconnect(self._on_image_failed)
            except Exception:
                pass
            self._loader = None

    def cancel(self) -> None:
        """Cancel the load request"""
        self._cleanup()

    def __del__(self) -> None:
        """Cleanup on destruction"""
        self._cleanup()


def load_image_async(
    url: str,
    target_size: Optional[Tuple[int, int]] = None,
    on_loaded: Optional[ImageLoadedCallback] = None,
    on_failed: Optional[ImageFailedCallback] = None,
    use_preview_loader: bool = False,
) -> ImageLoadRequest:
    """
    Convenience function for async image loading.

    Args:
        url: Image URL to load
        target_size: Target size for thumbnail generation
        on_loaded: Callback(url, pixmap) called on success
        on_failed: Callback(url, error) called on failure
        use_preview_loader: Use preview loader instead of grid loader

    Returns:
        ImageLoadRequest instance (keep reference until load completes)

    Example:
        >>> self.load_request = load_image_async(
        ...     url="https://example.com/img.jpg",
        ...     target_size=(200, 200),
        ...     on_loaded=lambda url, pix: self.display(pix)
        ... )
    """
    return ImageLoadRequest.load(
        url=url,
        target_size=target_size,
        on_loaded=on_loaded,
        on_failed=on_failed,
        use_preview_loader=use_preview_loader,
    )
