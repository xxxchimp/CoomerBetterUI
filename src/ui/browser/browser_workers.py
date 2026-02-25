"""
Background worker threads for browser window operations.

Extracted from browser_window.py to reduce file size and improve maintainability.
"""
from PyQt6.QtCore import QThread, pyqtSignal
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)


class PostsLoadWorker(QThread):
    """
    Background worker for loading posts from the API.

    Supports multiple modes:
    - Creator posts: Load posts from a specific creator
    - Popular posts: Load popular posts with date/period filters
    - Tags posts: Load posts filtered by tags
    - All posts: Load all posts with optional search query

    Signals:
        loaded(token, posts, total_count, popular_info): Emitted on success
        failed(token, error): Emitted on failure
    """
    loaded = pyqtSignal(int, list, int, object)  # token, posts, total_count, popular_info
    failed = pyqtSignal(int, str)

    def __init__(
        self,
        *,
        token: int,
        posts_manager,
        creators_manager,
        platform: str,
        service: str,
        offset: int,
        query: str,
        mode: str,
        tags: Optional[List[str]],
        popular_period: str,
        popular_date: Optional[str],
        creator_id: Optional[str],
    ):
        super().__init__()
        self._token = token
        self._posts_manager = posts_manager
        self._creators_manager = creators_manager
        self._platform = platform
        self._service = service
        self._offset = offset
        self._query = query
        self._mode = mode
        self._tags = tags or []
        self._popular_period = popular_period
        self._popular_date = popular_date
        self._creator_id = creator_id
        self._cancelled = False

    @property
    def token(self) -> int:
        """Get the worker's token for identifying responses."""
        return self._token

    def cancel(self) -> None:
        """Cancel the worker. Safe to call multiple times."""
        self._cancelled = True

    def run(self) -> None:
        """Execute the posts loading operation."""
        try:
            if self._creator_id:
                self._creators_manager.refresh_creator_post_count(
                    self._platform,
                    self._service,
                    self._creator_id,
                )
                page = self._posts_manager.get_creator_posts(
                    self._platform,
                    self._service,
                    self._creator_id,
                    offset=self._offset,
                    query=self._query or None,
                    tags=self._tags or None,
                )
            elif self._mode == "popular":
                page = self._posts_manager.get_popular_posts(
                    self._platform,
                    date=self._popular_date,
                    period=self._popular_period,
                    offset=self._offset,
                )
            elif self._mode == "tags":
                page = self._posts_manager.get_all_posts(
                    self._platform,
                    offset=self._offset,
                    tags=self._tags,
                )
            else:
                page = self._posts_manager.get_all_posts(
                    self._platform,
                    offset=self._offset,
                    query=self._query,
                    tags=self._tags,
                )
            if self._cancelled:
                return
            posts = list(page.posts)
            total_count = int(getattr(page, "true_count", None) or page.count or 0)
            popular_info = getattr(page, "info", None) if self._mode == "popular" else None
            self.loaded.emit(self._token, posts, total_count, popular_info)
        except Exception as e:
            if not self._cancelled:
                self.failed.emit(self._token, str(e))


class PostDetailWorker(QThread):
    """
    Background worker for loading a single post's details.

    Used when transitioning from grid view to detail view to fetch
    complete post information including all attachments.

    Signals:
        loaded(token, post): Emitted on success with full PostDTO
        failed(token, error): Emitted on failure
    """
    loaded = pyqtSignal(int, object)  # token, post
    failed = pyqtSignal(int, str)

    def __init__(
        self,
        *,
        token: int,
        posts_manager,
        platform: str,
        service: str,
        creator_id: str,
        post_id: str,
    ):
        super().__init__()
        self._token = token
        self._posts_manager = posts_manager
        self._platform = platform
        self._service = service
        self._creator_id = creator_id
        self._post_id = post_id
        self._cancelled = False

    @property
    def token(self) -> int:
        """Get the worker's token for identifying responses."""
        return self._token

    def cancel(self) -> None:
        """Cancel the worker. Safe to call multiple times."""
        self._cancelled = True

    def run(self) -> None:
        """Execute the post detail loading operation."""
        try:
            post = self._posts_manager.get_post(
                self._platform,
                self._service,
                self._post_id,
                creator_id=self._creator_id,
            )
            if self._cancelled:
                return
            self.loaded.emit(self._token, post)
        except Exception as e:
            if not self._cancelled:
                self.failed.emit(self._token, str(e))


class RandomPostWorker(QThread):
    """
    Background worker for fetching a random post locator.

    Signals:
        loaded(token, locator): Emitted on success with locator dict
        failed(token, error): Emitted on failure
    """
    loaded = pyqtSignal(int, object)  # token, locator
    failed = pyqtSignal(int, str)

    def __init__(
        self,
        *,
        token: int,
        posts_manager,
        platform: str,
    ):
        super().__init__()
        self._token = token
        self._posts_manager = posts_manager
        self._platform = platform
        self._cancelled = False

    @property
    def token(self) -> int:
        """Get the worker's token for identifying responses."""
        return self._token

    def cancel(self) -> None:
        """Cancel the worker. Safe to call multiple times."""
        self._cancelled = True

    def run(self) -> None:
        """Execute the random post lookup."""
        try:
            locator = self._posts_manager.get_random_post(self._platform)
            if self._cancelled:
                return
            self.loaded.emit(self._token, locator)
        except Exception as e:
            if not self._cancelled:
                self.failed.emit(self._token, str(e))
