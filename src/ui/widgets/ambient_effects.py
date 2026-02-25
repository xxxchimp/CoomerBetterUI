"""
Ambient lighting effects for video player.

This module contains the ambient background rendering system that samples colors
from video frames and creates smooth, animated gradient effects around the video.
"""

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QTimer, QRect, QThread
from PyQt6.QtGui import QPainter, QColor, QImage, QRadialGradient, QLinearGradient, QPixmap
import random
import math


class AmbientWorker(QThread):
    """Background thread that samples colors from video frames using K-means clustering."""
    
    ready = pyqtSignal(dict, float)  # (colors_dict, aspect)

    def __init__(self, player, parent=None):
        super().__init__(parent)
        self._player = player
        self._cancelled = False

    def cancel(self):
        """Cancel this worker."""
        self._cancelled = True
        self._player = None  # Clear reference to allow cleanup

    def run(self):  # runs in worker thread
        try:
            # Quick check if cancelled or player cleared before starting
            if self._cancelled or not self._player:
                return

            player = self._player  # Local reference for thread safety
            if not player:
                return

            # Check if video is ready before attempting screenshot
            # This is crucial for range proxy where buffering is gradual
            # Wrap property access separately to avoid crashes
            try:
                video_params = player.video_params
            except Exception:
                return
            if not video_params or not video_params.get('w') or not video_params.get('h'):
                return

            # Check cancellation again
            if self._cancelled or not self._player:
                return

            # Additional check: ensure we have actual playback time
            try:
                time_pos = player.time_pos
                if time_pos is None or time_pos < 1.0:  # Wait at least 1.0s into playback
                    return
            except Exception:
                return

            # Check cancellation before expensive screenshot operation
            if self._cancelled or not self._player:
                return

            # Try screenshot with reasonable timeout handling
            # screenshot-raw can hang if MPV is in a bad state
            snap = player.command("screenshot-raw", "video")
        except Exception:
            return
        if not snap:
            return
        data = snap.get("data")
        w = snap.get("w")
        h = snap.get("h")
        stride = snap.get("stride")
        fmt = snap.get("format")
        if not data or not w or not h or not stride:
            return
        
        # Final cancellation check before emitting
        if self._cancelled:
            return
        
        try:
            # mpv screenshot-raw can return different pixel formats; choose the
            # correct QImage format to avoid swapped/inverted colors.
            if fmt in ("bgra",):
                qfmt = QImage.Format_ARGB32
            elif fmt in ("bgr0",):
                qfmt = QImage.Format_RGB32
            elif fmt in ("rgba",):
                qfmt = QImage.Format_RGBA8888
            elif fmt in ("rgb0", "rgbx"):
                qfmt = QImage.Format_RGBX8888
            else:
                # Best-effort fallback: Windows typically returns BGRA/BGR0.
                qfmt = QImage.Format_ARGB32 if sys.platform == "win32" else QImage.Format_RGBA8888
            img = QImage(data, w, h, stride, qfmt)
        except Exception:
            return
        if img.isNull():
            return

        # Sample colors from edges (YouTube-style)
        colors = self._sample_edge_colors(img, w, h)
        aspect = float(w) / float(h) if h else 1.0
        self.ready.emit(colors, aspect)

    def _sample_edge_colors(self, img: QImage, w: int, h: int) -> dict:
        """Sample dominant colors from the corners using K-means clustering."""
        # Sample corner regions (15% of each dimension)
        corner_w = max(1, w // 7)
        corner_h = max(1, h // 7)

        # Sample four corners with K-means clustering
        top_left = self._dominant_region_color(img, 0, 0, corner_w, corner_h)
        top_right = self._dominant_region_color(img, w - corner_w, 0, corner_w, corner_h)
        bottom_left = self._dominant_region_color(img, 0, h - corner_h, corner_w, corner_h)
        bottom_right = self._dominant_region_color(img, w - corner_w, h - corner_h, corner_w, corner_h)

        return {
            'top_left': top_left,
            'top_right': top_right,
            'bottom_left': bottom_left,
            'bottom_right': bottom_right
        }

    def _dominant_region_color(self, img: QImage, x: int, y: int, width: int, height: int) -> QColor:
        """Find dominant color in a region using simple K-means clustering."""
        # Sample pixels from the region
        pixels = []
        step_y = max(1, height // 8)  # Sample more points for better clustering
        step_x = max(1, width // 8)
        
        for py in range(y, min(y + height, img.height()), step_y):
            for px in range(x, min(x + width, img.width()), step_x):
                color = img.pixelColor(px, py)
                pixels.append([color.red(), color.green(), color.blue()])
        
        if not pixels or len(pixels) < 3:
            return QColor(0, 0, 0)
        
        # Simple K-means with k=3 clusters (find 3 dominant colors)
        k = min(3, len(pixels))
        
        # Initialize centroids randomly from pixel samples
        centroids = random.sample(pixels, k)
        
        # Run K-means for 5 iterations (balance between quality and speed)
        for _ in range(5):
            # Assign pixels to nearest centroid
            clusters = [[] for _ in range(k)]
            for pixel in pixels:
                distances = [self._color_distance(pixel, c) for c in centroids]
                closest = distances.index(min(distances))
                clusters[closest].append(pixel)
            
            # Update centroids
            for i in range(k):
                if clusters[i]:
                    centroids[i] = [
                        sum(p[0] for p in clusters[i]) // len(clusters[i]),
                        sum(p[1] for p in clusters[i]) // len(clusters[i]),
                        sum(p[2] for p in clusters[i]) // len(clusters[i])
                    ]
        
        # Find the largest cluster (most representative color)
        largest_cluster_idx = max(range(k), key=lambda i: len(clusters[i]) if clusters[i] else 0)
        dominant_color = centroids[largest_cluster_idx]
        
        return QColor(
            max(0, min(255, dominant_color[0])),
            max(0, min(255, dominant_color[1])),
            max(0, min(255, dominant_color[2]))
        )
    
    @staticmethod
    def _color_distance(c1, c2):
        """Calculate Euclidean distance between two RGB colors."""
        return ((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2) ** 0.5


class RadialGradientWidget(QWidget):
    """Widget that paints multiple overlapping radial gradients with smooth transitions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Start with dark neutral colors until video sampling begins
        default_color = QColor(20, 20, 20)
        
        # Target colors for each corner
        self._corner_colors = {
            'top_left': QColor(default_color),
            'top_right': QColor(default_color),
            'bottom_left': QColor(default_color),
            'bottom_right': QColor(default_color)
        }
        
        # Current colors (for smooth interpolation)
        self._current_corners = {
            'top_left': QColor(default_color),
            'top_right': QColor(default_color),
            'bottom_left': QColor(default_color),
            'bottom_right': QColor(default_color)
        }

        # Animation progress (0.0 to 1.0)
        self._animation_progress = 1.0

        # Figure-8 movement (continuous pattern)
        self._figure8_time = 0.0  # Time parameter for figure-8 pattern
        
        # Random phase offsets for each gradient to avoid uniform movement
        self._phase_offsets = {
            'top_left': random.uniform(0, 6.28),  # Random start phase (0 to 2π)
            'top_right': random.uniform(0, 6.28),
            'bottom_left': random.uniform(0, 6.28),
            'bottom_right': random.uniform(0, 6.28),
            'top_mid': random.uniform(0, 6.28),
            'right_mid': random.uniform(0, 6.28),
            'bottom_mid': random.uniform(0, 6.28),
            'left_mid': random.uniform(0, 6.28)
        }
        
        # Is video paused
        self._is_paused = True
        
        # Video position within container (for gradient positioning)
        self._video_rect = QRect(0, 0, 100, 100)
        
        # Gradient constraints (pillarbox/letterbox bar dimensions)
        self._bar_width = 0  # Width of pillarbox bars
        self._bar_height = 0  # Height of letterbox bars
        
        # Fade-in opacity (0.0 = invisible, 1.0 = fully visible)
        self._fade_opacity = 0.0
        self._has_faded_in = False
        
        # Prevent recursive updates
        self._updating = False
        
        # Static frosted glass texture (generated once)
        self._noise_texture = None
        self._noise_texture_layer2 = None  # Second layer for more depth
        
        # Dither pattern for gradient banding reduction
        self._dither_pattern = None
        
        # Debug mode to make texture visible
        self._debug_texture = False  # Set to True to see debug pattern

        # Animation timer for smooth transitions and figure-8 movement
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(20)  # 50 FPS
        self._animation_timer.timeout.connect(self._animate_step)
    
    def _generate_dither_pattern(self, width: int, height: int):
        """Generate a subtle dither pattern for gradient banding reduction."""
        # Small repeating tile for performance
        tile_size = 64
        image = QImage(tile_size, tile_size, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        
        # Add very subtle random noise for dithering
        for y in range(tile_size):
            for x in range(tile_size):
                # Very subtle noise (±2 on RGB, low alpha)
                noise = random.randint(-2, 2)
                gray = 128 + noise
                alpha = random.randint(3, 8)  # Very subtle
                image.setPixelColor(x, y, QColor(gray, gray, gray, alpha))
        
        self._dither_pattern = QPixmap.fromImage(image)
    
    def _generate_noise_texture(self, width: int, height: int):
        """Generate static frosted glass noise texture (Layer 1: fine grain)."""
        # === ADJUSTABLE PARAMETER ===
        NOISE_LAYER1_OPACITY = 0.25  # Range: 0.0 (invisible) to 1.0 (opaque)
        # ===========================
        
        # Create small tile that will be repeated
        tile_size = 128
        
        # LAYER 1: Fine-grain noise
        image1 = QImage(tile_size, tile_size, QImage.Format.Format_ARGB32)
        
        if self._debug_texture:
            # DEBUG MODE: Very visible pattern to confirm placement
            image1.fill(QColor(255, 0, 0, 80))  # Semi-transparent red
            # Add checkerboard pattern
            for y in range(tile_size):
                for x in range(tile_size):
                    if (x // 8 + y // 8) % 2 == 0:
                        image1.setPixelColor(x, y, QColor(0, 255, 0, 80))  # Semi-transparent green
        else:
            # NORMAL MODE: Fine-grain Gaussian noise
            image1.fill(QColor(0, 0, 0, 0))  # Transparent background
            
            # Fine-grain noise with higher frequency
            for y in range(tile_size):
                for x in range(tile_size):
                    # Stronger Gaussian noise (mean=0, std=15 for more contrast)
                    noise = random.gauss(0, 15)
                    base_alpha = 40 + noise
                    alpha = int(max(0, min(255, base_alpha * NOISE_LAYER1_OPACITY)))
                    
                    # Stronger Gaussian color variation for more visible texture
                    color_noise = random.gauss(0, 25)
                    gray = int(max(0, min(255, 128 + color_noise)))
                    
                    image1.setPixelColor(x, y, QColor(gray, gray, gray, alpha))
        
        self._noise_texture = QPixmap.fromImage(image1)
    
    def _generate_noise_texture_layer2(self, width: int, height: int):
        """Generate static frosted glass noise texture (Layer 2: coarse grain)."""
        # === ADJUSTABLE PARAMETER ===
        NOISE_LAYER2_OPACITY = 0.1  # Range: 0.0 (invisible) to 1.0 (opaque)
        # ===========================
        
        # Create small tile that will be repeated
        tile_size = 128
        
        # LAYER 2: Coarser grain noise for depth
        image2 = QImage(tile_size, tile_size, QImage.Format.Format_ARGB32)
        
        if not self._debug_texture:
            image2.fill(QColor(0, 0, 0, 0))
            
            # Coarser noise - sample every other pixel and interpolate
            for y in range(0, tile_size, 2):
                for x in range(0, tile_size, 2):
                    # Softer, wider Gaussian for larger features
                    noise = random.gauss(0, 20)
                    base_alpha = 30 + noise
                    alpha = int(max(0, min(255, base_alpha * NOISE_LAYER2_OPACITY)))
                    
                    # Less color variation for subtler layer
                    color_noise = random.gauss(0, 15)
                    gray = int(max(0, min(255, 128 + color_noise)))
                    
                    color = QColor(gray, gray, gray, alpha)
                    # Fill 2x2 block for coarser appearance
                    for dy in range(2):
                        for dx in range(2):
                            if x + dx < tile_size and y + dy < tile_size:
                                image2.setPixelColor(x + dx, y + dy, color)
        else:
            # Debug: Blue tint for second layer
            image2.fill(QColor(0, 0, 255, 40))
        
        self._noise_texture_layer2 = QPixmap.fromImage(image2)

    def set_video_rect(self, video_rect: QRect):
        """Update the video widget's position for accurate gradient placement."""
        if self._updating or video_rect == self._video_rect:
            return
        self._video_rect = video_rect
        self.update()
    
    def set_gradient_constraint(self, bar_width: int, bar_height: int):
        """Set the pillarbox/letterbox bar dimensions to constrain gradient rendering."""
        if self._updating:
            return
        self._bar_width = max(0, bar_width)
        self._bar_height = max(0, bar_height)
        self.update()
    
    def fade_in(self):
        """Start fade-in animation when playback begins."""
        if self._has_faded_in:
            return
        self._has_faded_in = True
        self._fade_opacity = 0.0
        
        # Use timer to gradually increase opacity
        if not self._animation_timer.isActive():
            self._animation_timer.start()
    
    def set_colors(self, corner_colors: dict, is_paused: bool = False):
        """Update gradient colors with smooth transition."""
        if self._updating:
            return
            
        # Set target colors with alpha around 25% (64/255)
        colors_changed = False
        for corner in ['top_left', 'top_right', 'bottom_left', 'bottom_right']:
            if corner in corner_colors:
                new_color = QColor(corner_colors[corner])
                new_color.setAlpha(64)  # 25% opacity
                if self._corner_colors[corner] != new_color:
                    self._corner_colors[corner] = new_color
                    colors_changed = True
        
        # Only restart animation if colors actually changed
        if not colors_changed and self._is_paused == is_paused:
            return
        
        # Update pause state
        self._is_paused = is_paused
        
        # Control animation timer based on pause state and color changes
        if is_paused:
            # Stop animation when paused
            if self._animation_timer.isActive() and self._animation_progress >= 1.0:
                self._animation_timer.stop()
        else:
            # Always animate during playback (for figure-8 movement)
            if not self._animation_timer.isActive():
                self._animation_timer.start()

        # Start color transition if colors changed
        if colors_changed:
            self._animation_progress = 0.0
            if not self._animation_timer.isActive():
                self._animation_timer.start()

    def _animate_step(self):
        """Smooth interpolation between current and target colors and jitter."""
        if self._updating:
            return
            
        needs_update = False
        
        # Animate fade-in opacity
        if self._fade_opacity < 1.0:
            self._fade_opacity += 0.02  # Fade in over ~1 second (50 frames at 20ms)
            self._fade_opacity = min(1.0, self._fade_opacity)
            needs_update = True
        
        # Animate colors (3 second duration for ultra-smooth transitions)
        if self._animation_progress < 1.0:
            self._animation_progress += 0.0067  # 3000ms transition for seamless effect
            t = min(1.0, self._animation_progress)
            # Ultra-smooth easing for seamless transitions (no bounce/overshoot)
            ease = self._ease_in_out_quint(t)

            # Interpolate all corner colors
            for corner in ['top_left', 'top_right', 'bottom_left', 'bottom_right']:
                self._current_corners[corner] = self._lerp_color(
                    self._current_corners[corner], 
                    self._corner_colors[corner], 
                    ease
                )
            needs_update = True
        
        # Animate figure-8 movement (only when not paused)
        if not self._is_paused:
            # Increment time slowly for very subtle movement
            self._figure8_time += 0.005  # Very slow movement
            needs_update = True
        
        if needs_update:
            self.update()
        elif self._animation_timer.isActive() and self._is_paused:
            # Only stop timer when paused AND no animation is happening
            if self._animation_progress >= 1.0 and self._fade_opacity >= 1.0:
                self._animation_timer.stop()
    
    @staticmethod
    def _ease_in_out_quint(t: float) -> float:
        """
        Quintic (5th order) easing for ultra-smooth, seamless transitions.
        No overshoot or bounce - pure smooth acceleration/deceleration.
        """
        if t < 0.5:
            return 16 * t * t * t * t * t
        else:
            return 1 - pow(-2 * t + 2, 5) / 2

    def _lerp_color(self, from_color: QColor, to_color: QColor, t: float) -> QColor:
        """Linear interpolation between two colors with clamped values."""
        # Clamp t to [0, 1] as extra safety
        t = max(0.0, min(1.0, t))
        
        r = int(from_color.red() + (to_color.red() - from_color.red()) * t)
        g = int(from_color.green() + (to_color.green() - from_color.green()) * t)
        b = int(from_color.blue() + (to_color.blue() - from_color.blue()) * t)
        a = int(from_color.alpha() + (to_color.alpha() - from_color.alpha()) * t)
        
        # Clamp all values to valid range [0, 255]
        return QColor(
            max(0, min(255, r)),
            max(0, min(255, g)),
            max(0, min(255, b)),
            max(0, min(255, a))
        )

    def paintEvent(self, event):
        """Paint radial gradients with all effects applied."""
        if self._updating:
            return
            
        self._updating = True
        try:
            super().paintEvent(event)
            
            rect = self.rect()
            
            # Generate noise textures if needed
            if self._noise_texture is None:
                self._generate_noise_texture(rect.width(), rect.height())
            if self._noise_texture_layer2 is None:
                self._generate_noise_texture_layer2(rect.width(), rect.height())
            
            # Generate dither pattern if needed
            if self._dither_pattern is None:
                self._generate_dither_pattern(rect.width(), rect.height())
            
            # Render everything to an offscreen buffer first
            buffer = QImage(rect.width(), rect.height(), QImage.Format.Format_ARGB32_Premultiplied)
            buffer.fill(Qt.GlobalColor.transparent)
            
            buffer_painter = QPainter(buffer)
            buffer_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            buffer_painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            
            # Use video widget position for gradient corners
            video_x = self._video_rect.x()
            video_y = self._video_rect.y()
            video_w = self._video_rect.width()
            video_h = self._video_rect.height()
            
            # Smaller radius for glow effect instead of filling entire area
            radius = min(video_h, video_w) * 1.5
            
            # Helper to calculate figure-8 offset with phase
            amplitude = 64.0
            def get_figure8_offset(position_name):
                """Calculate figure-8 position with unique phase offset."""
                t = self._figure8_time + self._phase_offsets.get(position_name, 0)
                x = amplitude * math.sin(t)
                y = amplitude * math.sin(t) * math.cos(t)
                return x, y
            
            # Define corner positions with individual figure-8 movement
            corners = {}
            for corner_name in ['top_left', 'top_right', 'bottom_left', 'bottom_right']:
                fx, fy = get_figure8_offset(corner_name)
                if corner_name == 'top_left':
                    corners[corner_name] = (video_x + fx, video_y + fy)
                elif corner_name == 'top_right':
                    corners[corner_name] = (video_x + video_w - fx, video_y + fy)
                elif corner_name == 'bottom_left':
                    corners[corner_name] = (video_x + fx, video_y + video_h - fy)
                else:  # bottom_right
                    corners[corner_name] = (video_x + video_w - fx, video_y + video_h - fy)
            
            # Add midpoint gradients for smoother coverage
            midpoints = {}
            for mid_name in ['top_mid', 'right_mid', 'bottom_mid', 'left_mid']:
                fx, fy = get_figure8_offset(mid_name)
                if mid_name == 'top_mid':
                    midpoints[mid_name] = (video_x + video_w // 2 + fx, video_y + fy)
                elif mid_name == 'right_mid':
                    midpoints[mid_name] = (video_x + video_w - fx, video_y + video_h // 2 + fy)
                elif mid_name == 'bottom_mid':
                    midpoints[mid_name] = (video_x + video_w // 2 + fx, video_y + video_h - fy)
                else:  # left_mid
                    midpoints[mid_name] = (video_x + fx, video_y + video_h // 2 + fy)
            
            # Determine constraint regions based on bar sizes
            constrained_region = None
            if self._bar_width > 0:
                constrained_region = 'pillarbox'
            elif self._bar_height > 0:
                constrained_region = 'letterbox'
            
            # Helper function to draw a gradient with clipping
            def draw_gradient_for_position(corner_name, cx, cy, color):
                gradient = QRadialGradient(cx, cy, radius)
                
                # Glow effect with many stops to reduce banding
                gradient.setColorAt(0.0, color)
                gradient.setColorAt(0.05, color)
                gradient.setColorAt(0.10, color)
                gradient.setColorAt(0.15, color)
                
                # Smooth falloff with fine-grained stops
                stops = [
                    (0.20, 0.85), (0.25, 0.70), (0.30, 0.60),
                    (0.35, 0.50), (0.40, 0.40), (0.45, 0.32),
                    (0.50, 0.25), (0.55, 0.20), (0.60, 0.15),
                    (0.65, 0.11), (0.70, 0.08), (0.75, 0.06),
                    (0.80, 0.04), (0.85, 0.02), (0.90, 0.01),
                    (0.95, 0.005)
                ]
                
                for position, alpha_mult in stops:
                    alpha = int(color.alpha() * alpha_mult)
                    gradient.setColorAt(position, QColor(color.red(), color.green(), color.blue(), alpha))
                
                gradient.setColorAt(1.0, QColor(76, 77, 86, 0))  # Fade to transparent
                
                buffer_painter.setBrush(gradient)
                buffer_painter.setPen(Qt.PenStyle.NoPen)
                
                # Apply subtle dither before drawing gradient
                if self._dither_pattern:
                    old_mode = buffer_painter.compositionMode()
                    buffer_painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                    for dy in range(0, rect.height(), self._dither_pattern.height()):
                        for dx in range(0, rect.width(), self._dither_pattern.width()):
                            buffer_painter.drawPixmap(dx, dy, self._dither_pattern)
                    buffer_painter.setCompositionMode(old_mode)
                
                # Draw gradient constrained to appropriate region
                if constrained_region == 'pillarbox':
                    if corner_name in ['top_left', 'bottom_left', 'left_mid']:
                        clip_rect = QRect(0, 0, self._bar_width, rect.height())
                        buffer_painter.setClipRect(clip_rect)
                        buffer_painter.drawRect(rect)
                        buffer_painter.setClipping(False)
                    elif corner_name in ['top_right', 'bottom_right', 'right_mid']:
                        clip_rect = QRect(rect.width() - self._bar_width, 0, self._bar_width, rect.height())
                        buffer_painter.setClipRect(clip_rect)
                        buffer_painter.drawRect(rect)
                        buffer_painter.setClipping(False)
                elif constrained_region == 'letterbox':
                    if corner_name in ['top_left', 'top_right', 'top_mid']:
                        clip_rect = QRect(0, 0, rect.width(), self._bar_height)
                        buffer_painter.setClipRect(clip_rect)
                        buffer_painter.drawRect(rect)
                        buffer_painter.setClipping(False)
                    elif corner_name in ['bottom_left', 'bottom_right', 'bottom_mid']:
                        clip_rect = QRect(0, rect.height() - self._bar_height, rect.width(), self._bar_height)
                        buffer_painter.setClipRect(clip_rect)
                        buffer_painter.drawRect(rect)
                        buffer_painter.setClipping(False)
                else:
                    buffer_painter.drawRect(rect)
            
            # Paint gradient at each corner
            for corner_name, (cx, cy) in corners.items():
                color = QColor(self._current_corners[corner_name])
                draw_gradient_for_position(corner_name, cx, cy, color)
            
            # Paint midpoint gradients (blend colors from adjacent corners)
            for midpoint_name, (cx, cy) in midpoints.items():
                if midpoint_name == 'top_mid':
                    color1 = self._current_corners['top_left']
                    color2 = self._current_corners['top_right']
                elif midpoint_name == 'right_mid':
                    color1 = self._current_corners['top_right']
                    color2 = self._current_corners['bottom_right']
                elif midpoint_name == 'bottom_mid':
                    color1 = self._current_corners['bottom_left']
                    color2 = self._current_corners['bottom_right']
                else:  # left_mid
                    color1 = self._current_corners['top_left']
                    color2 = self._current_corners['bottom_left']
                
                blended_color = QColor(
                    (color1.red() + color2.red()) // 2,
                    (color1.green() + color2.green()) // 2,
                    (color1.blue() + color2.blue()) // 2,
                    (color1.alpha() + color2.alpha()) // 2
                )
                
                draw_gradient_for_position(midpoint_name, cx, cy, blended_color)
            
            # Apply static frosted glass texture (Layer 1: fine grain)
            if self._noise_texture:
                buffer_painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
                for y in range(0, rect.height(), self._noise_texture.height()):
                    for x in range(0, rect.width(), self._noise_texture.width()):
                        buffer_painter.drawPixmap(x, y, self._noise_texture)
            
            # Apply second layer (Layer 2: coarse grain)
            if self._noise_texture_layer2:
                buffer_painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Overlay)
                for y in range(0, rect.height(), self._noise_texture_layer2.height()):
                    for x in range(0, rect.width(), self._noise_texture_layer2.width()):
                        buffer_painter.drawPixmap(x, y, self._noise_texture_layer2)
            
            # Add subtle black overlay to reduce banding
            buffer_painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_ColorBurn)
            buffer_painter.fillRect(rect, QColor(0, 0, 0, 100))
            
            # Apply vignette alpha mask (fade to transparent at left/right edges)
            buffer_painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            h_gradient = QLinearGradient(0, 0, rect.width(), 0)
            h_gradient.setColorAt(0.0, QColor(255, 255, 255, 0))
            h_gradient.setColorAt(0.30, QColor(255, 255, 255, 255))
            h_gradient.setColorAt(0.70, QColor(255, 255, 255, 255))
            h_gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
            buffer_painter.fillRect(rect, h_gradient)
            
            # Finish painting to buffer
            buffer_painter.end()
            
            # Draw the masked buffer to the actual widget with fade opacity
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setOpacity(self._fade_opacity)
            painter.drawImage(0, 0, buffer)
        finally:
            self._updating = False
