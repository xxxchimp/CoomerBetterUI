"""
Video container widgets for managing layout and aspect ratios.

This module contains container widgets that handle video sizing, aspect ratio
constraints, and integration with ambient lighting effects.
"""

from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, QRect
from src.ui.widgets.ambient_effects import RadialGradientWidget


class AmbientVideoContainer(QWidget):
    """Container that sizes video to fill height, with ambient background in pillarbox areas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._video_aspect = 16.0 / 9.0
        self._constraint_enabled = True  # Enable aspect ratio constraint
        self._in_layout = False  # Prevent recursive layout calls
        self._last_video_geometry = QRect()  # Track last video position
        self.setMouseTracking(True)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )

        # Ambient background fills entire container with radial gradient
        self._ambient_widget = RadialGradientWidget(self)
        self._ambient_widget.setObjectName("videoAmbientBackdrop")
        self._ambient_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._ambient_widget.lower()
        self._ambient_widget.show()

        # Video widget - will be sized in resizeEvent
        self.video_widget = QWidget(self)
        self.video_widget.setObjectName("videoWidget")
        self.video_widget.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.video_widget.raise_()  # Ensure video is above ambient

    def set_aspect_ratio(self, ratio: float):
        """Update video aspect ratio and recalculate layout."""
        if ratio <= 0:
            return
        if abs(ratio - self._video_aspect) > 0.001:
            self._video_aspect = ratio
            self._layout_video()
            self._layout_ambient()

    def set_constraint_enabled(self, enabled: bool):
        """Enable/disable aspect ratio constraint (e.g., for fullscreen)."""
        if enabled != self._constraint_enabled:
            self._constraint_enabled = enabled
            self._layout_video()
            self._layout_ambient()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._in_layout:
            return
        self._in_layout = True
        try:
            self._layout_video()
            self._layout_ambient()
        finally:
            self._in_layout = False

    def _layout_video(self):
        """Size video to fill height, width = height * aspect_ratio, centered horizontally."""
        container_h = self.height()
        container_w = self.width()

        if not self._constraint_enabled:
            # Fullscreen: maintain aspect ratio, fit to screen
            container_aspect = container_w / max(1, container_h)
            
            if self._video_aspect > container_aspect:
                # Video is wider - fit to width
                video_w = container_w
                video_h = int(video_w / self._video_aspect)
                video_x = 0
                video_y = (container_h - video_h) // 2
            else:
                # Video is taller - fit to height
                video_h = container_h
                video_w = int(video_h * self._video_aspect)
                video_x = (container_w - video_w) // 2
                video_y = 0
            
            self.video_widget.setGeometry(video_x, video_y, video_w, video_h)
            return

        # Video fills height
        video_h = container_h
        video_w = int(video_h * self._video_aspect)

        # Center horizontally (creates pillarbox areas for ambient)
        video_x = (container_w - video_w) // 2
        video_y = 0

        self.video_widget.setGeometry(video_x, video_y, video_w, video_h)

    def _layout_ambient(self):
        """Position ambient to fill entire container, centered."""
        container_w = self.width()
        container_h = self.height()
        
        # Ambient fills the entire container (centered by default)
        self._ambient_widget.setGeometry(0, 0, container_w, container_h)
        
        # Update gradient positions and constraints based on video geometry
        video_geom = self.video_widget.geometry()
        if video_geom != self._last_video_geometry:
            self._last_video_geometry = video_geom
            
            # Calculate pillarbox/letterbox constraint
            # Gradient width constraint: (Full Container Width - Video Width) / 2
            bar_width = (container_w - video_geom.width()) // 2
            bar_height = (container_h - video_geom.height()) // 2
            
            self._ambient_widget.set_video_rect(video_geom)
            self._ambient_widget.set_gradient_constraint(bar_width, bar_height)
