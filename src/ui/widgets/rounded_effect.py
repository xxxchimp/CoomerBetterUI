"""
Graphics effect for rounded corner clipping with antialiasing
"""
from PyQt6.QtWidgets import QGraphicsEffect
from PyQt6.QtGui import QPainter, QPainterPath, QPixmap
from PyQt6.QtCore import Qt, QPoint, QRectF


class RoundedCornerGraphicsEffect(QGraphicsEffect):
    """Graphics effect for clipping widgets with rounded corners and antialiasing"""

    def __init__(self, radius: float, parent=None):
        """
        Initialize rounded corner effect

        Args:
            radius: Corner radius in pixels
            parent: Parent QObject
        """
        super().__init__(parent)
        self.radius = radius

    def draw(self, painter: QPainter):
        """Apply rounded corner clipping with antialiasing"""
        # Get the widget's rendered content as a pixmap
        # In PyQt6, sourcePixmap returns (pixmap, offset) as a tuple
        result = self.sourcePixmap(Qt.CoordinateSystem.LogicalCoordinates)
        src = result[0]
        offset = result[1] if result[1] is not None else QPoint()

        if src.isNull():
            return

        # Enable antialiasing for smooth edges
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Create rounded rectangle clipping path
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, src.width(), src.height()),
                           self.radius, self.radius)

        # Apply clipping and draw
        painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
        painter.drawPixmap(offset, src)
