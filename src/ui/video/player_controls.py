"""
Video player control widgets including buffered progress slider.

This module contains UI controls for video playback, including a custom
slider widget with buffer visualization and MPV signal definitions.
"""

from PyQt6.QtWidgets import QSlider
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QRect
from PyQt6.QtGui import QPainter, QColor


class MPVSignals(QObject):
    position = pyqtSignal(float)
    duration = pyqtSignal(float)
    pause = pyqtSignal(bool)
    buffer = pyqtSignal(float)
    eof = pyqtSignal()


class BufferedSlider(QSlider):
    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.buffer_ratio = 0.0
        self.buffer_segments = []
        self.setMouseTracking(True)
        self.setRange(0, 1000)
        self.setFixedHeight(18)

    def set_buffer(self, ratio: float):
        self.buffer_ratio = max(0.0, min(1.0, ratio))
        self.update()

    def set_buffer_segments(self, segments):
        clean = []
        for start, end in segments:
            try:
                start_f = float(start)
                end_f = float(end)
            except (TypeError, ValueError):
                continue
            if end_f <= start_f:
                continue
            clean.append((start_f, end_f))
        self.buffer_segments = clean
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            ratio = event.position().x() / max(1, self.width())
            self.setValue(int(ratio * self.maximum()))
            event.accept()
        super().mousePressEvent(event)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        groove_h = 4
        r = self.rect().adjusted(
            8,
            (self.height() - groove_h) // 2,
            -8,
            -(self.height() - groove_h) // 2,
        )

        # base
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(r, 2, 2)

        # buffered
        if self.buffer_segments:
            p.setBrush(QColor(255, 255, 255, 120))
            w = r.width()
            for start, end in self.buffer_segments:
                start_px = max(0, min(w, int(w * start)))
                end_px = max(0, min(w, int(w * end)))
                if end_px <= start_px:
                    end_px = min(w, start_px + 2)
                width = max(2, end_px - start_px)
                if start_px + width > w:
                    width = w - start_px
                if width <= 0:
                    continue
                seg = QRect(r.left() + start_px, r.top(), width, r.height())
                p.drawRoundedRect(seg, 2, 2)
        elif self.buffer_ratio > 0:
            bw = int(r.width() * self.buffer_ratio)
            bw = max(2, min(r.width(), bw))
            p.setBrush(QColor(255, 255, 255, 120))
            p.drawRoundedRect(r.adjusted(0, 0, bw - r.width(), 0), 2, 2)

        # played
        played = self.value() / self.maximum()
        pw = int(r.width() * played)
        p.setBrush(QColor(255, 107, 53))
        p.drawRoundedRect(r.adjusted(0, 0, pw - r.width(), 0), 2, 2)

        # handle
        hx = r.left() + pw
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(hx - 5, r.center().y() - 5, 10, 10)
