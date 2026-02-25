"""
Image/Pixmap Utility Functions

Centralized utilities for QPixmap manipulation, scaling, and transformations.
Eliminates duplicate pixmap scaling code across the UI.
"""
from __future__ import annotations

from typing import Optional, Tuple

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QPainter, QImage


def scale_pixmap_to_fill(
    pixmap: QPixmap,
    target_size: QSize | Tuple[int, int],
    smooth: bool = True,
) -> QPixmap:
    """
    Scale pixmap to fill target size (one dimension will overflow).

    Uses KeepAspectRatioByExpanding to ensure the pixmap covers the entire
    target area. The resulting pixmap may be larger than target_size and
    should typically be center-cropped.

    Args:
        pixmap: Source pixmap to scale
        target_size: Target size (QSize or (width, height) tuple)
        smooth: Use smooth transformation (default: True)

    Returns:
        Scaled pixmap that covers target_size (may be larger)

    Example:
        >>> pix = QPixmap("image.jpg")  # 800x600
        >>> scaled = scale_pixmap_to_fill(pix, (200, 200))
        >>> scaled.size()  # QSize(267, 200) - width expanded to maintain aspect
    """
    if isinstance(target_size, tuple):
        target_size = QSize(target_size[0], target_size[1])

    transform = (
        Qt.TransformationMode.SmoothTransformation
        if smooth
        else Qt.TransformationMode.FastTransformation
    )

    return pixmap.scaled(
        target_size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        transform,
    )


def scale_pixmap_to_fit(
    pixmap: QPixmap,
    target_size: QSize | Tuple[int, int],
    smooth: bool = True,
) -> QPixmap:
    """
    Scale pixmap to fit within target size (maintains aspect ratio).

    Uses KeepAspectRatio to ensure the entire pixmap is visible within
    the target area. May result in empty space on one dimension.

    Args:
        pixmap: Source pixmap to scale
        target_size: Maximum size (QSize or (width, height) tuple)
        smooth: Use smooth transformation (default: True)

    Returns:
        Scaled pixmap that fits within target_size

    Example:
        >>> pix = QPixmap("image.jpg")  # 800x600
        >>> scaled = scale_pixmap_to_fit(pix, (200, 200))
        >>> scaled.size()  # QSize(200, 150) - height reduced to maintain aspect
    """
    if isinstance(target_size, tuple):
        target_size = QSize(target_size[0], target_size[1])

    transform = (
        Qt.TransformationMode.SmoothTransformation
        if smooth
        else Qt.TransformationMode.FastTransformation
    )

    return pixmap.scaled(
        target_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        transform,
    )


def center_crop_pixmap(
    pixmap: QPixmap,
    target_size: QSize | Tuple[int, int],
) -> QPixmap:
    """
    Extract center portion of pixmap at exact target size.

    If pixmap is smaller than target, returns the original pixmap.
    Useful after scale_pixmap_to_fill() to get exact dimensions.

    Args:
        pixmap: Source pixmap (should be >= target_size)
        target_size: Exact size to extract (QSize or (width, height) tuple)

    Returns:
        Center-cropped pixmap at exactly target_size

    Example:
        >>> pix = QPixmap("image.jpg")  # 800x600
        >>> scaled = scale_pixmap_to_fill(pix, (200, 200))  # 267x200
        >>> cropped = center_crop_pixmap(scaled, (200, 200))  # 200x200
    """
    if isinstance(target_size, tuple):
        target_size = QSize(target_size[0], target_size[1])

    if pixmap.width() <= target_size.width() and pixmap.height() <= target_size.height():
        return pixmap

    # Calculate center crop coordinates
    sx = (pixmap.width() - target_size.width()) // 2
    sy = (pixmap.height() - target_size.height()) // 2

    return pixmap.copy(sx, sy, target_size.width(), target_size.height())


def scale_and_crop_pixmap(
    pixmap: QPixmap,
    target_size: QSize | Tuple[int, int],
    smooth: bool = True,
) -> QPixmap:
    """
    Scale pixmap to fill target size, then center-crop to exact dimensions.

    Combines scale_pixmap_to_fill() and center_crop_pixmap() into one operation.
    This is the most common pattern for thumbnail generation.

    Args:
        pixmap: Source pixmap
        target_size: Exact target size (QSize or (width, height) tuple)
        smooth: Use smooth transformation (default: True)

    Returns:
        Pixmap scaled and cropped to exactly target_size

    Example:
        >>> pix = QPixmap("image.jpg")  # 800x600
        >>> thumb = scale_and_crop_pixmap(pix, (200, 200))
        >>> thumb.size()  # QSize(200, 200) - exact size
    """
    if isinstance(target_size, tuple):
        target_size = QSize(target_size[0], target_size[1])

    # Scale to fill
    scaled = scale_pixmap_to_fill(pixmap, target_size, smooth=smooth)

    # Center crop to exact size
    return center_crop_pixmap(scaled, target_size)


def create_rounded_pixmap(
    pixmap: QPixmap,
    radius: int,
    target_size: Optional[QSize | Tuple[int, int]] = None,
) -> QPixmap:
    """
    Create a rounded-corner version of pixmap.

    Args:
        pixmap: Source pixmap
        radius: Corner radius in pixels
        target_size: Optional size to scale to before rounding

    Returns:
        Pixmap with rounded corners

    Example:
        >>> pix = QPixmap("avatar.jpg")
        >>> rounded = create_rounded_pixmap(pix, radius=8, target_size=(64, 64))
    """
    if target_size:
        if isinstance(target_size, tuple):
            target_size = QSize(target_size[0], target_size[1])
        pixmap = scale_and_crop_pixmap(pixmap, target_size)

    # Create result pixmap with transparency
    result = QPixmap(pixmap.size())
    result.fill(Qt.GlobalColor.transparent)

    # Draw with rounded rect clipping
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    # Draw rounded rectangle as clip path
    painter.setBrush(Qt.GlobalColor.white)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(result.rect(), radius, radius)

    # Composite original image using composition mode
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()

    return result


def create_circular_pixmap(
    pixmap: QPixmap,
    diameter: Optional[int] = None,
) -> QPixmap:
    """
    Create a circular (fully rounded) version of pixmap.

    Useful for avatar images.

    Args:
        pixmap: Source pixmap
        diameter: Target diameter (if None, uses smallest pixmap dimension)

    Returns:
        Circular pixmap

    Example:
        >>> pix = QPixmap("avatar.jpg")
        >>> circular = create_circular_pixmap(pix, diameter=64)
    """
    if diameter is None:
        diameter = min(pixmap.width(), pixmap.height())

    # Use half diameter as radius for fully circular shape
    return create_rounded_pixmap(
        pixmap,
        radius=diameter // 2,
        target_size=(diameter, diameter),
    )


def blend_pixmaps(
    bottom: QPixmap,
    top: QPixmap,
    opacity: float = 0.5,
) -> QPixmap:
    """
    Blend two pixmaps with specified opacity.

    Args:
        bottom: Bottom layer pixmap
        top: Top layer pixmap (must match bottom size)
        opacity: Opacity of top layer (0.0 = transparent, 1.0 = opaque)

    Returns:
        Blended pixmap
    """
    result = QPixmap(bottom.size())
    result.fill(Qt.GlobalColor.transparent)

    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    # Draw bottom layer
    painter.drawPixmap(0, 0, bottom)

    # Draw top layer with opacity
    painter.setOpacity(opacity)
    painter.drawPixmap(0, 0, top)
    painter.end()

    return result


def qimage_to_qpixmap(image: QImage) -> QPixmap:
    """
    Convert QImage to QPixmap efficiently.

    Args:
        image: Source QImage

    Returns:
        Converted QPixmap
    """
    return QPixmap.fromImage(image)


def qpixmap_to_qimage(pixmap: QPixmap) -> QImage:
    """
    Convert QPixmap to QImage.

    Args:
        pixmap: Source QPixmap

    Returns:
        Converted QImage
    """
    return pixmap.toImage()
