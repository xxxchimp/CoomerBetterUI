from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QGraphicsOpacityEffect
import qtawesome as qta


class SpinnerWidget(qta.IconWidget):
    def __init__(self, parent=None, *, size: int = 50, color: str = "#ffffff", opacity: float = 0.7):
        super().__init__()
        if parent is not None:
            self.setParent(parent)
        self._spin = qta.Spin(self, autostart=False)
        self.setIcon(qta.icon("fa5s.spinner", color=color, animation=self._spin))
        self.setIconSize(QSize(size, size))
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        
        # Apply opacity effect
        opacity_effect = QGraphicsOpacityEffect(self)
        opacity_effect.setOpacity(opacity)
        self.setGraphicsEffect(opacity_effect)
        
        self.setVisible(False)

    def start(self):
        self.setVisible(True)
        self._spin.start()

    def stop(self):
        self._spin.stop()
        self.setVisible(False)
