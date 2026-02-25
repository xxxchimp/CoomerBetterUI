from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from typing import Optional

import requests

from src.core.api import CoomerClient, KemonoClient
from pathlib import Path
from src.core.creators_manager import CreatorsManager
from src.core.database import DatabaseManager
from src.core.media_manager import MediaManager
from src.core.posts_manager import PostsManager
from src.core.range_proxy import get_range_proxy, RangeProxy
from src.core.http_client import (
    HttpClient,
    create_http_client_from_settings,
    set_http_client,
)

logger = logging.getLogger(__name__)


def _subprocess_kwargs() -> dict:
    """Get platform-specific subprocess kwargs to hide console windows on Windows."""
    kwargs = {}
    if sys.platform == 'win32':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return kwargs


class CacheConfig:
    """
    Centralized cache directory configuration.

    Provides a single source of truth for all cache directories used by the application.
    All paths are absolute and platform-independent.
    """

    def __init__(self, base_dir: Optional[Path] = None):
        """
        Initialize cache configuration.

        Args:
            base_dir: Base directory for all caches. Defaults to ~/.coomer-betterui
        """
        self.base = base_dir or (Path.home() / ".coomer-betterui")
        self.base.mkdir(parents=True, exist_ok=True)

    @property
    def media(self) -> Path:
        """Media files cache (videos, images downloaded for playback)"""
        path = self.base / "media_cache"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def thumbnails(self) -> Path:
        """Generated thumbnails cache"""
        path = self.base / "thumbnails"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def range_proxy(self) -> Path:
        """Range proxy HTTP chunk cache"""
        path = self.base / "range_cache"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def http_api(self) -> Path:
        """HTTP API response cache"""
        path = self.base / "http_cache"
        path.mkdir(parents=True, exist_ok=True)
        return path


class CoreContext:
    """
    Shared Core dependencies (DB + clients + managers).

    Use a single instance for app lifetime for consistency and performance.
    """

    def __init__(
        self,
        *,
        db: Optional[DatabaseManager] = None,
        session: Optional[requests.Session] = None,
        cache_config: Optional[CacheConfig] = None,
    ):
        self.db = db or DatabaseManager()
        self.cache = cache_config or CacheConfig()

        # Initialize database connection early so we can read settings
        if self.db.conn is None:
            self.db.connect()

        # Create HTTP client from settings (handles cookies, proxy, headers)
        self._http_client = create_http_client_from_settings(self.db)
        set_http_client(self._http_client)
        logger.info(f"HTTP client created - proxy enabled: {self._http_client.config.proxy_config.enabled}, "
                   f"proxy_url: {self._http_client.config.proxy_config.proxy_url}, "
                   f"proxy_pool: {self._http_client.config.proxy_config.proxy_pool}")

        # Use session from HTTP client if not provided
        # IMPORTANT: Create session WITHOUT proxies for API calls
        # Proxies should only be used for media downloads through RangeProxy
        if session is not None:
            self.session = session
        else:
            # Create session without proxy for API requests
            self.session = self._http_client.create_sync_session()
            # Clear any proxy configuration from this session
            self.session.proxies.clear()
            logger.info("API session created without proxies (proxies only used for media downloads)")

        self._coomer = CoomerClient(session=self.session, cache_dir=str(self.cache.http_api))
        self._kemono = KemonoClient(session=self.session, cache_dir=str(self.cache.http_api))

        self.media = MediaManager(
            cache_dir=self.cache.media,
            db_manager=self.db,
            core_context=self,
        )

        self.creators = CreatorsManager(
            coomer=self._coomer,
            kemono=self._kemono,
            db=self.db,
        )
        self.posts = PostsManager(coomer=self._coomer, kemono=self._kemono)


        self._range_proxy: Optional[RangeProxy] = None

        # Eagerly initialize range proxy if enabled in settings
        try:
            enable_range_proxy = self.db.get_config('enable_range_proxy', 'false') == 'true'
        except Exception:
            enable_range_proxy = False
        if enable_range_proxy:
            _ = self.range_proxy  # Force property access to initialize

        self.start()

    @property
    def range_proxy(self) -> RangeProxy:
        """Get the singleton RangeProxy instance for media streaming."""
        if self._range_proxy is None:
            # Read performance settings from database config
            try:
                max_concurrent_chunks = int(self.db.get_config('range_proxy_max_concurrent_chunks', '12'))
            except (TypeError, ValueError):
                max_concurrent_chunks = 12
            
            try:
                max_connections_per_host = int(self.db.get_config('max_connections_per_host', '80'))
            except (TypeError, ValueError):
                max_connections_per_host = 80
            
            try:
                max_total_connections = int(self.db.get_config('max_total_connections', '400'))
            except (TypeError, ValueError):
                max_total_connections = 400
            
            try:
                chunk_size = int(self.db.get_config('range_proxy_chunk_size', str(8 * 1024 * 1024)))
            except (TypeError, ValueError):
                chunk_size = 8 * 1024 * 1024  # 8MB default
            
            # Get proxy configuration from http_client
            proxy_url = None
            proxy_pool = []
            logger.info(f"Configuring RangeProxy - proxy_config.enabled: {self._http_client.config.proxy_config.enabled}")
            if self._http_client.config.proxy_config.enabled:
                if self._http_client.config.proxy_config.proxy_pool:
                    # Use all proxies in pool for parallel downloads
                    proxy_pool = self._http_client.config.proxy_config.proxy_pool
                    logger.info(f"RangeProxy using proxy pool with {len(proxy_pool)} proxies for parallel downloads")
                else:
                    # Fallback to single proxy
                    proxy_url = self._http_client.config.proxy_config.get_proxy()
                    logger.info(f"RangeProxy using single proxy: {proxy_url}")
            else:
                logger.info("RangeProxy proxy disabled - using direct connection")
            
            logger.info(f"Creating RangeProxy with proxy_url={proxy_url}, proxy_pool={proxy_pool}")
            self._range_proxy = get_range_proxy(
                cache_dir=self.cache.range_proxy,
                max_concurrent_chunks=max_concurrent_chunks,
                max_connections_per_host=max_connections_per_host,
                max_total_connections=max_total_connections,
                chunk_size=chunk_size,
                proxy_url=proxy_url,
                proxy_pool=proxy_pool,
            )
        return self._range_proxy

    def start(self) -> None:
        # Database is now connected in __init__ for settings access
        self._check_ffmpeg_availability()

    def _check_ffmpeg_availability(self) -> None:
        """Check if ffmpeg and ffprobe are available for video processing."""
        ffmpeg_path = shutil.which("ffmpeg")
        ffprobe_path = shutil.which("ffprobe")

        if not ffmpeg_path:
            logger.warning(
                "ffmpeg not found in PATH. Video thumbnail generation will be unavailable. "
                "Install ffmpeg: https://ffmpeg.org/download.html"
            )
        else:
            try:
                result = subprocess.run(
                    [ffmpeg_path, "-version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    **_subprocess_kwargs(),
                )
                if result.returncode == 0:
                    version_line = result.stdout.split("\n")[0]
                    logger.info(f"ffmpeg found: {version_line}")
                else:
                    logger.warning(f"ffmpeg found but returned error: {result.stderr}")
            except Exception as e:
                logger.warning(f"ffmpeg found but failed to execute: {e}")

        if not ffprobe_path:
            logger.warning(
                "ffprobe not found in PATH. Video metadata extraction may be limited. "
                "Install ffmpeg (includes ffprobe): https://ffmpeg.org/download.html"
            )
        else:
            try:
                result = subprocess.run(
                    [ffprobe_path, "-version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    **_subprocess_kwargs(),
                )
                if result.returncode == 0:
                    version_line = result.stdout.split("\n")[0]
                    logger.info(f"ffprobe found: {version_line}")
                else:
                    logger.warning(f"ffprobe found but returned error: {result.stderr}")
            except Exception as e:
                logger.warning(f"ffprobe found but failed to execute: {e}")

    def close(self) -> None:
        # Close HTTP client (saves cookies)
        if hasattr(self, '_http_client') and self._http_client:
            self._http_client.close()

        self.db.close()
        self.session.close()
        if self._range_proxy is not None:
            self._range_proxy.stop()
