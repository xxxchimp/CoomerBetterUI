"""
Centralized logging configuration with categorized loggers.

This module provides a flexible logging system with:
- Named categories for different subsystems
- Per-category log level control
- Persistent configuration via database
"""
import logging
from typing import Dict, Optional
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler


# Logger categories for different subsystems
class LoggerCategory:
    """Named categories for application loggers"""
    CORE = "core"                  # Core services (database, cache, context)
    API = "api"                    # API clients (kemono, coomer)
    UI = "ui"                      # UI components (widgets, windows)
    MEDIA = "media"                # Media processing (thumbnails, video)
    NETWORK = "network"            # Network operations (HTTP, proxy, range_proxy)
    DOWNLOAD = "download"          # Download manager and workers
    DATABASE = "database"          # Database operations
    DOCKER = "docker"              # Docker/VPN management
    IMAGE_LOADING = "image"        # Image loading and caching
    VIDEO_PLAYER = "video"         # Video player components
    BROWSER = "browser"            # Browser window and navigation
    SETTINGS = "settings"          # Settings and configuration


# Default log levels for each category
DEFAULT_LOG_LEVELS = {
    LoggerCategory.CORE: logging.INFO,
    LoggerCategory.API: logging.INFO,
    LoggerCategory.UI: logging.WARNING,  # Reduce UI noise
    LoggerCategory.MEDIA: logging.INFO,
    LoggerCategory.NETWORK: logging.INFO,
    LoggerCategory.DOWNLOAD: logging.INFO,
    LoggerCategory.DATABASE: logging.WARNING,  # Reduce DB query noise
    LoggerCategory.DOCKER: logging.INFO,
    LoggerCategory.IMAGE_LOADING: logging.WARNING,  # Reduce image loading noise
    LoggerCategory.VIDEO_PLAYER: logging.INFO,
    LoggerCategory.BROWSER: logging.INFO,
    LoggerCategory.SETTINGS: logging.INFO,
}


# Map module names to categories
MODULE_TO_CATEGORY = {
    # Core
    'src.core': LoggerCategory.CORE,
    'src.core.context': LoggerCategory.CORE,
    'src.core.cache': LoggerCategory.CORE,
    'src.core.creators_manager': LoggerCategory.CORE,
    'src.core.posts_manager': LoggerCategory.CORE,
    'src.core.media_manager': LoggerCategory.CORE,
    
    # API
    'src.core.api': LoggerCategory.API,
    'src.core.api.coomer': LoggerCategory.API,
    'src.core.api.kemono': LoggerCategory.API,
    
    # Network
    'src.core.http_client': LoggerCategory.NETWORK,
    'src.core.range_proxy': LoggerCategory.NETWORK,
    'src.core.async_api_worker': LoggerCategory.NETWORK,
    
    # Database
    'src.core.database': LoggerCategory.DATABASE,
    
    # Download
    'src.core.download_manager': LoggerCategory.DOWNLOAD,
    'src.core.download_worker': LoggerCategory.DOWNLOAD,
    
    # Docker
    'src.core.docker_manager': LoggerCategory.DOCKER,
    
    # Media
    'src.media': LoggerCategory.MEDIA,
    'src.media.processor': LoggerCategory.MEDIA,
    'src.core.thumbnails': LoggerCategory.MEDIA,
    'src.ai.thumbnail_generator': LoggerCategory.MEDIA,
    
    # UI
    'src.ui': LoggerCategory.UI,
    'src.ui.common': LoggerCategory.UI,
    'src.ui.gallery': LoggerCategory.UI,
    'src.ui.creators': LoggerCategory.UI,
    
    # Browser
    'src.ui.browser': LoggerCategory.BROWSER,
    'src.ui.browser.browser_window': LoggerCategory.BROWSER,
    'src.ui.browser.browser_workers': LoggerCategory.BROWSER,
    'src.ui.browser.browser_downloads': LoggerCategory.BROWSER,
    
    # Image Loading
    'src.ui.images': LoggerCategory.IMAGE_LOADING,
    'src.ui.images.image_loader': LoggerCategory.IMAGE_LOADING,
    'src.ui.images.image_loader_manager': LoggerCategory.IMAGE_LOADING,
    'src.ui.images.async_image_widgets': LoggerCategory.IMAGE_LOADING,
    'src.ui.images.zoomable_image': LoggerCategory.IMAGE_LOADING,
    'src.core.priority_image_loader': LoggerCategory.IMAGE_LOADING,
    
    # Video
    'src.ui.video': LoggerCategory.VIDEO_PLAYER,
    'src.ui.video.video_player': LoggerCategory.VIDEO_PLAYER,
    
    # Settings
    'src.ui.common.settings_dialog': LoggerCategory.SETTINGS,
}


class LoggingManager:
    """Manages application-wide logging configuration"""
    
    def __init__(self, log_dir: Optional[Path] = None, db_manager=None):
        """
        Initialize logging manager.
        
        Args:
            log_dir: Directory for log files
            db_manager: Database manager for persistent configuration
        """
        self.log_dir = log_dir or (Path.home() / "AppData" / "Local" / "CoomerBetterUI" / "logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.db_manager = db_manager
        self._category_levels: Dict[str, int] = {}
        self._load_levels_from_db()
    
    def _load_levels_from_db(self):
        """Load log levels from database configuration"""
        if not self.db_manager:
            self._category_levels = DEFAULT_LOG_LEVELS.copy()
            return
        
        for category, default_level in DEFAULT_LOG_LEVELS.items():
            config_key = f'log_level_{category}'
            level_name = self.db_manager.get_config(config_key, logging.getLevelName(default_level))
            try:
                level = logging.getLevelName(level_name)
                if isinstance(level, int):
                    self._category_levels[category] = level
                else:
                    self._category_levels[category] = default_level
            except (ValueError, AttributeError):
                self._category_levels[category] = default_level
    
    def get_category_level(self, category: str) -> int:
        """Get log level for a category"""
        return self._category_levels.get(category, logging.INFO)
    
    def set_category_level(self, category: str, level: int):
        """Set log level for a category"""
        self._category_levels[category] = level
        if self.db_manager:
            config_key = f'log_level_{category}'
            self.db_manager.set_config(config_key, logging.getLevelName(level))
        
        # Update all loggers in this category
        self._apply_category_level(category, level)
    
    def _apply_category_level(self, category: str, level: int):
        """Apply level to all loggers in a category"""
        for module_name, cat in MODULE_TO_CATEGORY.items():
            if cat == category:
                logger = logging.getLogger(module_name)
                logger.setLevel(level)
    
    def setup_logging(self, root_level: int = logging.INFO):
        """
        Setup application logging with categories.
        
        Args:
            root_level: Root logger level (default: INFO)
        """
        log_file = self.log_dir / "coomer_betterui.log"
        
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # File handler with rotation
        file_handler = TimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=7,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        
        # Console handler
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        
        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(root_level)
        
        # Remove existing handlers
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
        
        root_logger.addHandler(file_handler)
        root_logger.addHandler(stream_handler)
        
        # Apply category levels
        for category, level in self._category_levels.items():
            self._apply_category_level(category, level)
        
        # Silence noisy third-party loggers
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('requests').setLevel(logging.WARNING)
        logging.getLogger('aiohttp').setLevel(logging.WARNING)
        logging.getLogger('asyncio').setLevel(logging.WARNING)
    
    def get_all_levels(self) -> Dict[str, int]:
        """Get all category log levels"""
        return self._category_levels.copy()


# Global instance
_logging_manager: Optional[LoggingManager] = None


def get_logging_manager(db_manager=None) -> LoggingManager:
    """Get or create the global logging manager"""
    global _logging_manager
    if _logging_manager is None:
        _logging_manager = LoggingManager(db_manager=db_manager)
    return _logging_manager


def setup_logging(db_manager=None):
    """Setup application logging (convenience function)"""
    manager = get_logging_manager(db_manager)
    manager.setup_logging()
    return manager
