from PyQt6.QtCore import QObject, pyqtSignal


class ThumbnailHandle(QObject):
    """
    Handle for tracking thumbnail generation requests.

    Attributes:
        ready: Signal emitted when thumbnail is ready (ThumbnailResult)
        failed: Signal emitted on failure with error message (str)
        _future: Internal future object for the thumbnail generation task
        _cancelled: Whether this request has been cancelled
        _timeout_resets: Number of times the timeout has been extended (used by ThumbnailManager)
    """
    ready = pyqtSignal(object)   # ThumbnailResult
    failed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._future = None
        self._cancelled = False
        self._timeout_resets = 0

    def cancel(self) -> None:
        self._cancelled = True
        future = getattr(self, "_future", None)
        if future and not future.done():
            future.cancel()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def timeout_resets(self) -> int:
        """Number of times the timeout has been extended."""
        return self._timeout_resets

    @timeout_resets.setter
    def timeout_resets(self, value: int) -> None:
        """Set the number of timeout resets."""
        self._timeout_resets = max(0, int(value))
