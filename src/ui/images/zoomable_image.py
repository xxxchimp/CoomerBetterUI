"""
Zoomable image widget for image viewing with zoom and pan support
"""
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPixmapItem
import math
from PyQt6.QtCore import Qt, QPointF, QTimer
from PyQt6.QtGui import (
    QPixmap,
    QWheelEvent,
    QMouseEvent,
    QCursor,
    QPainter,
    QColor,
    QBrush,
    QImage,
)
import logging
from src.ui.common.theme import Colors

logger = logging.getLogger(__name__)

# ── background effect constants (from viewer.py) ──────────────────────────────
FX_BG_COLOR = QColor(15, 15, 15)
FX_DOT_ALPHA = 255
FX_DOT_MAX = 25.0
FX_DOT_MIN = 1.5
FX_NUM_RINGS = 60
FX_DOTS_PER_RING = 60
FX_SPACING_POWER = 1.5
FX_CORNER_PULL = 0.03
FX_TENSION = 1.0
FX_ZOOM_MAX = 2.0
FX_ZOOM_EFFECT_SPEED = 4.0   # higher = full size inversion reached sooner (1=at FX_ZOOM_MAX)
FX_DENSITY_SCALE = 0.5       # how much ring spacing shifts with zoom (0=none, 1=doubles/halves)
FX_DOT_CURVE = 1.3           # power curve for size gradient (1=linear, >1=tight centre, <1=spread out)
FX_RING_TWIST = 0.05         # clockwise rotation added per ring (radians); 0 = no twist
FX_ANIM_SMOOTH = 0.14        # lerp factor per frame toward zoom_t target (higher = snappier)
# ─────────────────────────────────────────────────────────────────────────────


class ZoomableImageWidget(QGraphicsView):
    """Zoomable and pannable image widget using QGraphicsView"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("zoomableImageView")

        # Setup scene
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)

        # Image item
        self.pixmap_item = None
        self.current_pixmap = None
        self._centered_mode = False

        # Zoom settings
        self.zoom_factor = 1.0
        self.min_zoom = 0.1
        self.max_zoom = 5.0
        self.zoom_step = 0.1

        # Pan settings
        self.panning = False
        self.pan_start_pos = QPointF()

        # Background effect state
        self._pan_offset = QPointF(0.0, 0.0)
        self._zoom_fit = 1.0
        self._zoom_t_anim = 0.0
        self._quad_colors = None
        self._noise_pixmap = None
        self._free_pan_enabled = True
        self._free_pan_scene = QPointF(0.0, 0.0)
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._anim_step)

        # Configure view for proper zooming
        self.setRenderHint(self.renderHints())
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        if self._free_pan_enabled:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        else:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Enable optimizations
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True)

        self.setStyleSheet(f"background-color: {Colors.BG_PRIMARY}; border: none;")

    def set_pixmap(self, pixmap: QPixmap):
        """Set image to display"""
        if pixmap.isNull():
            return

        self._centered_mode = False
        self.current_pixmap = pixmap

        # Clear existing items
        self.scene.clear()

        # Add pixmap to scene
        self.pixmap_item = QGraphicsPixmapItem(pixmap)
        self.pixmap_item.setPos(0, 0)
        self.scene.addItem(self.pixmap_item)

        # Set the scene's bounding rectangle to the pixmap size
        # This is important for proper scrollbar behavior when zooming
        rect = self.pixmap_item.boundingRect()
        self.scene.setSceneRect(rect)

        # Reset view
        self.reset_zoom()
        self._sample_quad_colors()
        self.viewport().update()

    def set_centered_pixmap(self, pixmap: QPixmap):
        """Set a centered pixmap without scaling to fit the view."""
        if pixmap.isNull():
            return

        self._centered_mode = True
        self.current_pixmap = pixmap
        self.scene.clear()
        self.pixmap_item = QGraphicsPixmapItem(pixmap)
        self.scene.addItem(self.pixmap_item)
        self.resetTransform()
        self.zoom_factor = 1.0
        view_rect = self.viewport().rect()
        self.scene.setSceneRect(0, 0, view_rect.width(), view_rect.height())
        x = (view_rect.width() - pixmap.width()) / 2
        y = (view_rect.height() - pixmap.height()) / 2
        self.pixmap_item.setPos(x, y)
        self._free_pan_scene = QPointF(self.pixmap_item.pos())
        self._zoom_fit = 1.0
        self._zoom_t_anim = 0.0
        self._anim_timer.stop()
        self._pan_offset = QPointF(0.0, 0.0)
        self._sample_quad_colors()
        self.viewport().update()

    def clear_pixmap(self):
        """Clear current image from view"""
        self.scene.clear()
        self.pixmap_item = None
        self.current_pixmap = None
        self._centered_mode = False
        self.resetTransform()
        self.zoom_factor = 1.0
        self._free_pan_scene = QPointF(0.0, 0.0)
        self._zoom_fit = 1.0
        self._zoom_t_anim = 0.0
        self._anim_timer.stop()
        self._quad_colors = None
        self._pan_offset = QPointF(0.0, 0.0)
        self.viewport().update()

    def reset_zoom(self):
        """Reset zoom to fit image in view"""
        if not self.pixmap_item:
            return

        # Reset transform
        self.resetTransform()

        # Fit image in view
        self.fitInView(self.pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)

        # Store the initial fit zoom level as our base
        self.zoom_factor = self.transform().m11()
        self._free_pan_scene = QPointF(0.0, 0.0)
        if self.pixmap_item:
            self.pixmap_item.setPos(0, 0)
        self._zoom_fit = self.zoom_factor
        self._zoom_t_anim = 0.0
        self._anim_timer.stop()
        self._pan_offset = QPointF(0.0, 0.0)
        self.viewport().update()

    def zoom_to_native(self):
        """Set zoom to native (1:1) pixel scale."""
        if not self.pixmap_item:
            return

        self.resetTransform()
        self.zoom_factor = 1.0
        rect = self.pixmap_item.boundingRect()
        self.scene.setSceneRect(rect)
        self.centerOn(rect.center())
        self._start_zoom_effect_anim()

    def toggle_fit_native(self):
        """Toggle between fit-to-view and native (1:1) zoom."""
        if not self.pixmap_item:
            return
        if abs(self.zoom_factor - 1.0) < 0.01:
            self.reset_zoom()
        else:
            self.zoom_to_native()

    def zoom_in(self):
        """Zoom in"""
        self._apply_zoom(1 + self.zoom_step)

    def zoom_out(self):
        """Zoom out"""
        self._apply_zoom(1 - self.zoom_step)

    def _apply_zoom(self, factor: float):
        """Apply zoom factor"""
        if not self.pixmap_item:
            return

        # Calculate new zoom level
        new_zoom = self.zoom_factor * factor

        # Clamp zoom level
        if new_zoom < self.min_zoom or new_zoom > self.max_zoom:
            return

        # Apply scaling
        self.scale(factor, factor)
        self.zoom_factor = new_zoom
        self._start_zoom_effect_anim()

    def wheelEvent(self, event: QWheelEvent):
        """Handle mouse wheel for zooming"""
        if not self.pixmap_item:
            return

        # Zoom in/out based on wheel direction
        if event.angleDelta().y() > 0:
            zoom_factor = 1 + self.zoom_step
        else:
            zoom_factor = 1 - self.zoom_step

        self._apply_zoom(zoom_factor)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent):
        """Handle mouse press for panning"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.panning = True
            self.pan_start_pos = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        """Handle mouse move for panning"""
        if self.panning:
            delta = event.pos() - self.pan_start_pos
            self.pan_start_pos = event.pos()
            self._pan_offset += QPointF(float(delta.x()), float(delta.y()))

            # Pan the view
            if self._free_pan_enabled and self.pixmap_item:
                zoom = max(1e-6, self.zoom_factor)
                self._free_pan_scene += QPointF(delta.x() / zoom, delta.y() / zoom)
                self.pixmap_item.setPos(self._free_pan_scene)
            else:
                hbar = self.horizontalScrollBar()
                vbar = self.verticalScrollBar()
                if hbar.minimum() == hbar.maximum():
                    self.translate(delta.x(), 0)
                else:
                    hbar.setValue(hbar.value() - delta.x())
                if vbar.minimum() == vbar.maximum():
                    self.translate(0, delta.y())
                else:
                    vbar.setValue(vbar.value() - delta.y())
            self.viewport().update()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        """Handle mouse release"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def resizeEvent(self, event):
        """Handle resize"""
        super().resizeEvent(event)
        if self._centered_mode and self.pixmap_item:
            view_rect = self.viewport().rect()
            self.scene.setSceneRect(0, 0, view_rect.width(), view_rect.height())
            pixmap = self.pixmap_item.pixmap()
            x = (view_rect.width() - pixmap.width()) / 2
            y = (view_rect.height() - pixmap.height()) / 2
            self.pixmap_item.setPos(x, y)
        self.viewport().update()
        # Don't auto-fit on resize - let user manually reset if needed
        # This prevents annoying behavior when user has zoomed in

    def drawBackground(self, painter: QPainter, _rect):
        painter.save()
        painter.resetTransform()
        self._draw_effect_background(painter)
        painter.restore()

    def _draw_effect_background(self, p: QPainter):
        view_rect = self.viewport().rect()
        if view_rect.isEmpty():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(view_rect, FX_BG_COLOR)
        self._draw_dots(p, view_rect.width(), view_rect.height())
        if self._noise_pixmap is None:
            self._build_noise_tile()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Overlay)
        p.drawTiledPixmap(view_rect, self._noise_pixmap)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    def _build_noise_tile(self):
        import random

        tw, th = 256, 256
        n = tw * th
        buf = bytearray(n * 4)
        rng = random.Random(0)  # fixed seed → stable, non-animating texture
        for i in range(n):
            # Gaussian grey: bright speckles scattered over the dark bg, like light through frosted glass
            v = max(0, min(255, int(rng.gauss(210, 50))))
            o = i * 4
            buf[o] = buf[o + 1] = buf[o + 2] = v  # B G R (grayscale)
            buf[o + 3] = 50  # A — higher alpha needed; overlay is a softer blend
        img = QImage(bytes(buf), tw, th, tw * 4, QImage.Format.Format_ARGB32).copy()
        self._noise_pixmap = QPixmap.fromImage(img)

    def _draw_dots(self, p: QPainter, w: int, h: int):
        dot_cx = w / 2 + self._pan_offset.x()
        dot_cy = h / 2 + self._pan_offset.y()
        fix_cx, fix_cy = w / 2, h / 2

        # 8 pin points: 4 corners + 4 edge midpoints
        pin_points = [
            (0.0, 0.0),  # top-left
            (w / 2, 0.0),  # top-center
            (float(w), 0.0),  # top-right
            (float(w), h / 2),  # right-center
            (float(w), float(h)),  # bottom-right
            (w / 2, float(h)),  # bottom-center
            (0.0, float(h)),  # bottom-left
            (0.0, h / 2),  # left-center
        ]

        zoom_t = self._zoom_t_anim

        # Max radius scales with zoom — rings spread (sparse) when zoomed in, compress when out
        density_factor = 1.0 + zoom_t * FX_DENSITY_SCALE
        max_r = math.hypot(w, h) * 0.78 * density_factor

        # Farthest viewport corner from the dot origin — dots there must reach FX_DOT_MAX
        view_r = max(
            math.hypot(dot_cx, dot_cy),  # top-left
            math.hypot(w - dot_cx, dot_cy),  # top-right
            math.hypot(dot_cx, h - dot_cy),  # bottom-left
            math.hypot(w - dot_cx, h - dot_cy),  # bottom-right
        )

        # Precompute quad colour data for fast per-ring interpolation
        w_f, h_f = float(w), float(h)
        if self._quad_colors:
            q_tl, q_tr, q_bl, q_br = self._quad_colors
        else:
            _fb = (200, 200, 200)
            q_tl = q_tr = q_bl = q_br = _fb

        p.setPen(Qt.PenStyle.NoPen)

        for i in range(1, FX_NUM_RINGS + 1):
            t = i / FX_NUM_RINGS  # 0 < t ≤ 1 (inner→outer)
            r = (t ** FX_SPACING_POWER) * max_r

            # ── tension: outer ring origins blend toward the fixed viewport centre ──
            blend = t * FX_TENSION
            orig_x = dot_cx * (1 - blend) + fix_cx * blend
            orig_y = dot_cy * (1 - blend) + fix_cy * blend

            ring_rotation = i * FX_RING_TWIST
            for j in range(FX_DOTS_PER_RING):
                angle = 2 * math.pi * j / FX_DOTS_PER_RING + ring_rotation
                x = orig_x + r * math.cos(angle)
                y = orig_y + r * math.sin(angle)

                # ── corner pull: nudge outer dots toward their nearest corner ──
                if FX_CORNER_PULL > 0:
                    pull = t * FX_CORNER_PULL
                    nearest = min(pin_points, key=lambda c: (x - c[0]) ** 2 + (y - c[1]) ** 2)
                    x += (nearest[0] - x) * pull
                    y += (nearest[1] - y) * pull

                # ── dot size: based on actual distance from dot origin so the
                #    shrink/grow crossover falls in the visible region at all
                #    zoom levels, not just under the image ──
                td = min(1.0, math.hypot(x - dot_cx, y - dot_cy) / view_r)
                dot_r_n = FX_DOT_MIN + (FX_DOT_MAX - FX_DOT_MIN) * (1.0 - td) ** FX_DOT_CURVE
                dot_r_i = FX_DOT_MIN + (FX_DOT_MAX - FX_DOT_MIN) * td ** FX_DOT_CURVE
                dot_r = max(FX_DOT_MIN, min(FX_DOT_MAX, dot_r_n + (dot_r_i - dot_r_n) * zoom_t))

                # Skip dots well outside the viewport (perf)
                if -dot_r * 2 < x < w + dot_r * 2 and -dot_r * 2 < y < h + dot_r * 2:
                    # ── colour: bilinear interpolation by actual dot screen position ──
                    tx = max(0.0, min(1.0, x / w_f))
                    ty = max(0.0, min(1.0, y / h_f))
                    w_tl = (1 - tx) * (1 - ty)
                    w_tr = tx * (1 - ty)
                    w_bl = (1 - tx) * ty
                    w_br = tx * ty
                    rc = int(q_tl[0] * w_tl + q_tr[0] * w_tr + q_bl[0] * w_bl + q_br[0] * w_br)
                    gc = int(q_tl[1] * w_tl + q_tr[1] * w_tr + q_bl[1] * w_bl + q_br[1] * w_br)
                    bc = int(q_tl[2] * w_tl + q_tr[2] * w_tr + q_bl[2] * w_bl + q_br[2] * w_br)
                    p.setBrush(QBrush(QColor(rc, gc, bc, FX_DOT_ALPHA)))
                    p.drawEllipse(QPointF(x, y), dot_r, dot_r)

    def _anim_step(self):
        zoom = max(1e-6, self.zoom_factor)
        zoom_fit = max(1e-6, self._zoom_fit)
        zf = math.log(zoom / zoom_fit) / math.log(FX_ZOOM_MAX) * FX_ZOOM_EFFECT_SPEED
        target = max(-1.0, min(1.0, zf))
        diff = target - self._zoom_t_anim
        if abs(diff) < 0.001:
            self._zoom_t_anim = target
            self._anim_timer.stop()
        else:
            self._zoom_t_anim += diff * FX_ANIM_SMOOTH
        self.viewport().update()

    def _start_zoom_effect_anim(self):
        if not self.pixmap_item:
            return
        self._anim_timer.start()
        self.viewport().update()

    def _sample_quad_colors(self):
        if not self.current_pixmap or self.current_pixmap.isNull():
            self._quad_colors = None
            return
        # Downscale for fast pixel access — 80×80 is plenty for colour sampling
        img = self.current_pixmap.scaled(
            80,
            80,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        ).toImage()
        iw, ih = img.width(), img.height()
        mx, my = iw // 2, ih // 2
        quads = [
            (0, 0, mx, my),  # TL
            (mx, 0, iw, my),  # TR
            (0, my, mx, ih),  # BL
            (mx, my, iw, ih),  # BR
        ]
        bins = 36  # 10° hue buckets
        result = []
        for x0, y0, x1, y1 in quads:
            # Accumulate weighted RGB per hue bin
            bins_w = [0.0] * bins
            bins_r = [0.0] * bins
            bins_g = [0.0] * bins
            bins_b = [0.0] * bins
            for px in range(x0, x1):
                for py in range(y0, y1):
                    packed = img.pixel(px, py)
                    r = (packed >> 16) & 0xFF
                    g = (packed >> 8) & 0xFF
                    b = packed & 0xFF
                    cmax = max(r, g, b)
                    cmin = min(r, g, b)
                    delta = cmax - cmin
                    if delta == 0 or cmax == 0:
                        continue  # achromatic — skip
                    s = delta / cmax
                    if s < 0.2:
                        continue  # not saturated enough
                    v = cmax / 255.0
                    # hue in [0, 360)
                    if cmax == r:
                        h = ((g - b) / delta) % 6
                    elif cmax == g:
                        h = (b - r) / delta + 2
                    else:
                        h = (r - g) / delta + 4
                    h = h * 60.0
                    bi = int(h / 360.0 * bins) % bins
                    w = s * v  # weight = vibrancy
                    bins_w[bi] += w
                    bins_r[bi] += r * w
                    bins_g[bi] += g * w
                    bins_b[bi] += b * w
            # Winning bin = hue that accumulated the most weighted presence
            best_bi = max(range(bins), key=lambda i: bins_w[i])
            w_total = bins_w[best_bi]
            if w_total > 0:
                rgb = (
                    int(bins_r[best_bi] / w_total),
                    int(bins_g[best_bi] / w_total),
                    int(bins_b[best_bi] / w_total),
                )
            else:
                rgb = (180, 180, 180)  # fallback: neutral gray
            result.append(rgb)
        self._quad_colors = result
