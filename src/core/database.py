"""
Database schema and management for Coomer BetterUI.
Handles configuration, indices, and offline storage.
"""
import sqlite3
import json
import logging
from pathlib import Path
from typing import Any, Iterable, Optional, Dict, List, Tuple
from datetime import datetime
from cryptography.fernet import Fernet
import keyring

logger = logging.getLogger(__name__)

class DatabaseManager:
    """Manages SQLite database operations for configuration and caching"""
    
    VERSION = "1.0.0"
    
    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize database manager
        
        Args:
            db_path: Path to SQLite database file. Defaults to user data directory.
        """
        if db_path is None:
            db_path = Path.home() / "AppData" / "Local" / "CoomerBetterUI" / "data.db"
        
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._encryption_key = self._get_or_create_encryption_key()
        
    def _get_or_create_encryption_key(self) -> bytes:
        """
        Retrieve or create encryption key from Windows Credential Manager
        
        Returns:
            Fernet encryption key
        """
        service_name = "CoomerBetterUI"
        key_name = "encryption_key"
        
        try:
            key_str = keyring.get_password(service_name, key_name)
            if key_str:
                return key_str.encode()
        except Exception as e:
            logger.warning(f"Could not retrieve encryption key: {e}")
        
        # Generate new key
        key = Fernet.generate_key()
        try:
            keyring.set_password(service_name, key_name, key.decode())
        except Exception as e:
            logger.error(f"Could not store encryption key: {e}")
        
        return key
    
    def _encrypt_value(self, value: str) -> str:
        """Encrypt sensitive value"""
        f = Fernet(self._encryption_key)
        return f.encrypt(value.encode()).decode()
    
    def _decrypt_value(self, encrypted_value: str) -> str:
        """Decrypt sensitive value"""
        f = Fernet(self._encryption_key)
        return f.decrypt(encrypted_value.encode()).decode()
    
    def connect(self):
        """Establish database connection"""
        self.conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=30.0
        )
        self.conn.row_factory = sqlite3.Row
        self._initialize_schema()
    
    def _initialize_schema(self):
        """Create database schema if not exists"""
        cursor = self.conn.cursor()

        # Check if schema already exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='config'")
        schema_exists = cursor.fetchone() is not None

        # Configuration table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                is_encrypted INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # API credentials table (encrypted)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_credentials (
                provider TEXT PRIMARY KEY,
                api_key TEXT NOT NULL,
                additional_config TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Media cache table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS media_cache (
                url TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                media_type TEXT,
                file_size INTEGER,
                thumbnail_path TEXT,
                metadata TEXT,
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 1
            )
        """)

        # Content-ID cache table (deduplicated media metadata)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS media_content_cache (
                content_id TEXT PRIMARY KEY,
                media_type TEXT,
                content_hash TEXT,
                etag TEXT,
                last_modified TEXT,
                content_length INTEGER,
                duration REAL,
                width INTEGER,
                height INTEGER,
                codec TEXT,
                thumbnail_path TEXT,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 1
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS media_url_map (
                url TEXT PRIMARY KEY,
                content_id TEXT NOT NULL,
                etag TEXT,
                last_modified TEXT,
                content_length INTEGER,
                mapped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 1
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS media_thumbnail_cache (
                content_id TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                thumbnail_path TEXT NOT NULL,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 1,
                PRIMARY KEY (content_id, width, height)
            )
        """)
        
        # Post/content cache table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS content_cache (
                post_id TEXT PRIMARY KEY,
                service TEXT NOT NULL,
                user_id TEXT NOT NULL,
                title TEXT,
                content TEXT,
                attachments TEXT,
                metadata TEXT,
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Download queue table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS download_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                filename TEXT NOT NULL,
                destination_path TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                progress REAL DEFAULT 0.0,
                file_size INTEGER,
                downloaded_bytes INTEGER DEFAULT 0,
                retry_count INTEGER DEFAULT 0,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                paused_at TIMESTAMP
            )
        """)
        
        # Browsing history table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS browsing_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT,
                visit_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                tab_id INTEGER
            )
        """)
        
        # Favorites/bookmarks table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT NOT NULL,
                service TEXT NOT NULL,
                user_id TEXT NOT NULL,
                title TEXT,
                thumbnail_url TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(post_id, service)
            )
        """)

        # Creators registry table for efficient searching and browsing
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                service TEXT NOT NULL,
                creator_id TEXT NOT NULL,
                name TEXT NOT NULL,
                indexed_name TEXT NOT NULL,
                creator_indexed TEXT,
                creator_updated TEXT,
                public_id TEXT,
                relation_id INTEGER,
                post_count INTEGER,
                dm_count INTEGER,
                share_count INTEGER,
                chat_count INTEGER,
                display_href TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                favorited_count INTEGER DEFAULT 0,
                UNIQUE(platform, service, creator_id)
            )
        """)

        # Creators registry metadata (per-platform last refresh timestamp)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creators_registry_meta (
                platform TEXT PRIMARY KEY,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Per-creator metadata (favorites/pinned/hidden/last_seen)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_meta (
                platform TEXT NOT NULL,
                service TEXT NOT NULL,
                creator_id TEXT NOT NULL,
                favorited INTEGER DEFAULT 0,
                pinned INTEGER DEFAULT 0,
                hidden INTEGER DEFAULT 0,
                last_seen TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(platform, service, creator_id)
            )
        """)

        # Download index (URL -> local path + integrity)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS download_index (
                url TEXT PRIMARY KEY,
                local_path TEXT NOT NULL,
                file_size INTEGER,
                sha256 TEXT,
                verified_at TIMESTAMP,
                downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Optional post index for local search / offline browsing
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS post_index (
                platform TEXT NOT NULL,
                service TEXT NOT NULL,
                creator_id TEXT NOT NULL,
                post_id TEXT NOT NULL,
                title TEXT,
                published_at TIMESTAMP,
                file_count INTEGER,
                tags_json TEXT,
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(platform, service, creator_id, post_id)
            )
        """)

        # Track URLs flagged as too large (size exceeds configured limits)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS oversized_files (
                url TEXT PRIMARY KEY,
                file_size INTEGER NOT NULL,
                size_limit INTEGER NOT NULL,
                flagged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Ensure migrations: add favorited_count/post_count columns if missing (for older DBs)
        try:
            cursor.execute("PRAGMA table_info(creators)")
            cols = [r[1] for r in cursor.fetchall()]
            if 'favorited_count' not in cols:
                cursor.execute("ALTER TABLE creators ADD COLUMN favorited_count INTEGER DEFAULT 0")
            if 'post_count' not in cols:
                cursor.execute("ALTER TABLE creators ADD COLUMN post_count INTEGER")
            if 'dm_count' not in cols:
                cursor.execute("ALTER TABLE creators ADD COLUMN dm_count INTEGER")
            if 'share_count' not in cols:
                cursor.execute("ALTER TABLE creators ADD COLUMN share_count INTEGER")
            if 'chat_count' not in cols:
                cursor.execute("ALTER TABLE creators ADD COLUMN chat_count INTEGER")
            if 'creator_indexed' not in cols:
                cursor.execute("ALTER TABLE creators ADD COLUMN creator_indexed TEXT")
            if 'creator_updated' not in cols:
                cursor.execute("ALTER TABLE creators ADD COLUMN creator_updated TEXT")
            if 'public_id' not in cols:
                cursor.execute("ALTER TABLE creators ADD COLUMN public_id TEXT")
            if 'relation_id' not in cols:
                cursor.execute("ALTER TABLE creators ADD COLUMN relation_id INTEGER")
            if 'display_href' not in cols:
                cursor.execute("ALTER TABLE creators ADD COLUMN display_href TEXT")
        except Exception:
            # Non-fatal migration failure; log and continue
            logger.debug('Creators table migration (favorited_count/post_count/profile counts) skipped or failed')

        # Create indexes for performance
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_media_cache_accessed 
            ON media_cache(last_accessed DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_media_content_accessed
            ON media_content_cache(last_accessed DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_media_url_map_content
            ON media_url_map(content_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_media_thumbnail_accessed
            ON media_thumbnail_cache(last_accessed DESC)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_content_cache_service_user 
            ON content_cache(service, user_id, cached_at DESC)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_download_queue_status 
            ON download_queue(status, created_at)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_time
            ON browsing_history(visit_time DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_creators_platform_service
            ON creators(platform, service, name)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_creators_indexed_name
            ON creators(indexed_name)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_creators_updated
            ON creators(platform, updated_at DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_creator_meta_flags
            ON creator_meta(platform, favorited, pinned, hidden)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_creator_meta_seen
            ON creator_meta(platform, last_seen DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_download_index_accessed
            ON download_index(last_accessed DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_post_index_creator
            ON post_index(platform, service, creator_id, published_at DESC)
        """)

        # Insert default configuration
        self._set_default_config()

        self.conn.commit()

        # Only log if this was a new database
        if not schema_exists:
            logger.info("Database schema initialized")
        else:
            logger.debug("Database schema verified")
    
    def _set_default_config(self):
        """Set default configuration values"""
        defaults = {
            'app_version': self.VERSION,
            'theme': 'dark',
            'cache_size_limit_mb': '5000',
            'auto_generate_thumbnails': 'true',
            'thumbnail_quality': '85',
            'enable_ai_thumbnails': 'false',
            'default_ai_provider': 'claude',
            'gallery_transition_speed': '400',
            'gallery_per_page': '1',
            'enable_coverflow': 'true',
            'auto_cleanup_cache_days': '30',
            'max_concurrent_downloads': '3',
            'video_preview_on_hover': 'true',
            'enable_batch_download': 'true',
            'compress_downloads': 'false',
            'structured_downloads': 'true',
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'allow_post_content_media': 'false',
            'enable_range_proxy': 'false',
            'video_thumb_max_mb': '300',
            'video_thumb_max_non_faststart_mb': '20',
            'video_thumb_retries': '1',
            'video_thumb_retry_delay_ms': '200',
        }
        
        cursor = self.conn.cursor()
        for key, value in defaults.items():
            cursor.execute("""
                INSERT OR IGNORE INTO config (key, value) 
                VALUES (?, ?)
            """, (key, value))
        self.conn.commit()
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """
        Retrieve configuration value
        
        Args:
            key: Configuration key
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT value, is_encrypted FROM config WHERE key = ?
        """, (key,))
        
        row = cursor.fetchone()
        if row:
            value = row['value']
            if row['is_encrypted']:
                value = self._decrypt_value(value)
            return value
        return default
    
    def set_config(self, key: str, value: Any, encrypt: bool = False):
        """
        Set configuration value
        
        Args:
            key: Configuration key
            value: Configuration value
            encrypt: Whether to encrypt the value
        """
        str_value = str(value)
        if encrypt:
            str_value = self._encrypt_value(str_value)
        
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO config (key, value, is_encrypted, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, (key, str_value, 1 if encrypt else 0))
        self.conn.commit()
    
    def get_all_config(self) -> Dict[str, str]:
        """Get all configuration as dictionary (excluding encrypted values)"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT key, value FROM config WHERE is_encrypted = 0
        """)
        return {row['key']: row['value'] for row in cursor.fetchall()}

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Alias for get_config to clarify app settings usage."""
        return self.get_config(key, default)

    def set_setting(self, key: str, value: Any, encrypt: bool = False):
        """Alias for set_config to clarify app settings usage."""
        self.set_config(key, value, encrypt=encrypt)
    
    def set_api_credential(self, provider: str, api_key: str, additional_config: Optional[Dict] = None):
        """
        Store API credentials (encrypted)
        
        Args:
            provider: API provider name ('claude', 'gemini')
            api_key: API key to encrypt and store
            additional_config: Additional provider configuration
        """
        encrypted_key = self._encrypt_value(api_key)
        config_json = json.dumps(additional_config) if additional_config else None
        
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO api_credentials 
            (provider, api_key, additional_config, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, (provider, encrypted_key, config_json))
        self.conn.commit()
    
    def get_api_credential(self, provider: str) -> Optional[tuple[str, Optional[Dict]]]:
        """
        Retrieve decrypted API credentials
        
        Args:
            provider: API provider name
            
        Returns:
            Tuple of (api_key, additional_config) or None
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT api_key, additional_config FROM api_credentials WHERE provider = ?
        """, (provider,))
        
        row = cursor.fetchone()
        if row:
            api_key = self._decrypt_value(row['api_key'])
            config = json.loads(row['additional_config']) if row['additional_config'] else None
            return (api_key, config)
        return None
    
    def cache_media(self, url: str, file_path: str, media_type: str, 
                   file_size: int, thumbnail_path: Optional[str] = None,
                   metadata: Optional[Dict] = None):
        """
        Cache media file information
        
        Args:
            url: Original media URL
            file_path: Local file system path
            media_type: Type of media (video, image, gif)
            file_size: File size in bytes
            thumbnail_path: Path to generated thumbnail
            metadata: Additional metadata as dictionary
        """
        cursor = self.conn.cursor()
        metadata_json = json.dumps(metadata) if metadata else None
        
        cursor.execute("""
            INSERT OR REPLACE INTO media_cache 
            (url, file_path, media_type, file_size, thumbnail_path, metadata, 
             cached_at, last_accessed, access_count)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                    COALESCE((SELECT access_count + 1 FROM media_cache WHERE url = ?), 1))
        """, (url, file_path, media_type, file_size, thumbnail_path, metadata_json, url))
        self.conn.commit()

    def cache_media_content(
        self,
        content_id: str,
        *,
        media_type: Optional[str] = None,
        content_hash: Optional[str] = None,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        content_length: Optional[int] = None,
        duration: Optional[float] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        codec: Optional[str] = None,
        thumbnail_path: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> None:
        cursor = self.conn.cursor()
        metadata_json = json.dumps(metadata) if metadata else None
        cursor.execute("""
            INSERT OR REPLACE INTO media_content_cache
            (content_id, media_type, content_hash, etag, last_modified, content_length,
             duration, width, height, codec, thumbnail_path, metadata,
             created_at, last_accessed, access_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT created_at FROM media_content_cache WHERE content_id = ?), CURRENT_TIMESTAMP),
                    CURRENT_TIMESTAMP,
                    COALESCE((SELECT access_count + 1 FROM media_content_cache WHERE content_id = ?), 1))
        """, (
            content_id,
            media_type,
            content_hash,
            etag,
            last_modified,
            content_length,
            duration,
            width,
            height,
            codec,
            thumbnail_path,
            metadata_json,
            content_id,
            content_id,
        ))
        self.conn.commit()

    def get_cached_content(self, content_id: str) -> Optional[Dict]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM media_content_cache WHERE content_id = ?
        """, (content_id,))
        row = cursor.fetchone()
        if row:
            cursor.execute("""
                UPDATE media_content_cache
                SET last_accessed = CURRENT_TIMESTAMP, access_count = access_count + 1
                WHERE content_id = ?
            """, (content_id,))
            self.conn.commit()
            result = dict(row)
            if result.get('metadata'):
                result['metadata'] = json.loads(result['metadata'])
            return result
        return None

    def map_media_url(
        self,
        url: str,
        content_id: str,
        *,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        content_length: Optional[int] = None,
    ) -> None:
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO media_url_map
            (url, content_id, etag, last_modified, content_length,
             mapped_at, last_accessed, access_count)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                    COALESCE((SELECT access_count + 1 FROM media_url_map WHERE url = ?), 1))
        """, (url, content_id, etag, last_modified, content_length, url))
        self.conn.commit()

    def get_content_id_for_url(self, url: str) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT content_id FROM media_url_map WHERE url = ?
        """, (url,))
        row = cursor.fetchone()
        if row:
            cursor.execute("""
                UPDATE media_url_map
                SET last_accessed = CURRENT_TIMESTAMP, access_count = access_count + 1
                WHERE url = ?
            """, (url,))
            self.conn.commit()
            return row['content_id']
        return None

    def cache_thumbnail_for_content(
        self,
        content_id: str,
        width: int,
        height: int,
        thumbnail_path: str,
        metadata: Optional[Dict] = None,
    ) -> None:
        cursor = self.conn.cursor()
        metadata_json = json.dumps(metadata) if metadata else None
        cursor.execute("""
            INSERT OR REPLACE INTO media_thumbnail_cache
            (content_id, width, height, thumbnail_path, metadata,
             created_at, last_accessed, access_count)
            VALUES (?, ?, ?, ?, ?,
                    COALESCE((SELECT created_at FROM media_thumbnail_cache WHERE content_id = ? AND width = ? AND height = ?), CURRENT_TIMESTAMP),
                    CURRENT_TIMESTAMP,
                    COALESCE((SELECT access_count + 1 FROM media_thumbnail_cache WHERE content_id = ? AND width = ? AND height = ?), 1))
        """, (
            content_id,
            int(width),
            int(height),
            thumbnail_path,
            metadata_json,
            content_id,
            int(width),
            int(height),
            content_id,
            int(width),
            int(height),
        ))
        self.conn.commit()

    def get_cached_thumbnail(
        self,
        content_id: str,
        width: int,
        height: int,
    ) -> Optional[Dict]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM media_thumbnail_cache
            WHERE content_id = ? AND width = ? AND height = ?
        """, (content_id, int(width), int(height)))
        row = cursor.fetchone()
        if row:
            cursor.execute("""
                UPDATE media_thumbnail_cache
                SET last_accessed = CURRENT_TIMESTAMP, access_count = access_count + 1
                WHERE content_id = ? AND width = ? AND height = ?
            """, (content_id, int(width), int(height)))
            self.conn.commit()
            result = dict(row)
            if result.get('metadata'):
                result['metadata'] = json.loads(result['metadata'])
            return result
        return None

    def get_thumbnail_variants(self, content_id: str) -> List[Dict]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM media_thumbnail_cache
            WHERE content_id = ?
            ORDER BY (width * height) DESC
        """, (content_id,))
        rows = cursor.fetchall()
        results = []
        for row in rows:
            result = dict(row)
            if result.get('metadata'):
                result['metadata'] = json.loads(result['metadata'])
            results.append(result)
        return results

    def touch_thumbnail_entry(self, content_id: str, width: int, height: int) -> None:
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE media_thumbnail_cache
            SET last_accessed = CURRENT_TIMESTAMP, access_count = access_count + 1
            WHERE content_id = ? AND width = ? AND height = ?
        """, (content_id, int(width), int(height)))
        self.conn.commit()
    
    def get_cached_media(self, url: str) -> Optional[Dict]:
        """
        Retrieve cached media information
        
        Args:
            url: Media URL
            
        Returns:
            Dictionary with cache information or None
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM media_cache WHERE url = ?
        """, (url,))
        
        row = cursor.fetchone()
        if row:
            # Update last accessed
            cursor.execute("""
                UPDATE media_cache 
                SET last_accessed = CURRENT_TIMESTAMP, access_count = access_count + 1
                WHERE url = ?
            """, (url,))
            self.conn.commit()
            
            result = dict(row)
            if result['metadata']:
                result['metadata'] = json.loads(result['metadata'])
            return result
        return None
    
    def cleanup_old_cache(self, days: int = 30):
        """
        Remove cache entries older than specified days.
        Only removes database records - use clear_cache() to also remove files.

        Args:
            days: Number of days to retain
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            DELETE FROM media_cache
            WHERE last_accessed < datetime('now', '-' || ? || ' days')
        """, (days,))

        deleted = cursor.rowcount
        self.conn.commit()
        logger.info(f"Cleaned up {deleted} old cache entries")
        return deleted

    def clear_cache(self) -> tuple[int, int]:
        """
        Clear all cache files from disk and database records.

        Returns:
            Tuple of (files_deleted, bytes_freed).
        """
        import shutil

        # Get size before clearing
        bytes_freed = self.get_cache_size()
        files_deleted = 0

        # Delete files from all cache directories
        for root in self._cache_roots():
            if not root.exists():
                continue
            try:
                for path in root.rglob("*"):
                    if path.is_file():
                        files_deleted += 1
                shutil.rmtree(root, ignore_errors=True)
                root.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"Failed to clear cache directory {root}: {e}")

        # Clear database records
        cursor = self.conn.cursor()
        for table in ["media_cache", "media_content_cache", "media_url_map",
                      "media_thumbnail_cache", "content_cache"]:
            try:
                cursor.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        self.conn.commit()

        logger.info(f"Cleared {files_deleted} cache files ({bytes_freed / (1024 * 1024):.2f} MB)")
        return files_deleted, bytes_freed
    
    def get_cache_size(self) -> int:
        """
        Calculate total cache size in bytes (disk)

        Returns:
            Total size in bytes
        """
        total = 0
        for path in self._iter_cache_files():
            try:
                total += path.stat().st_size
            except Exception:
                pass
        return total

    def _cache_roots(self) -> List[Path]:
        return [
            Path.home() / ".coomer-betterui" / "http_cache",
            Path.home() / ".coomer-betterui" / "media_cache",
            Path.home() / ".coomer-betterui" / "thumbnails",
            Path.home() / "AppData" / "Local" / "CoomerBetterUI" / "cache",
        ]

    def _iter_cache_files(self) -> Iterable[Path]:
        for root in self._cache_roots():
            if not root.exists():
                continue
            try:
                for path in root.rglob("*"):
                    try:
                        if path.is_file():
                            yield path
                    except Exception:
                        continue
            except Exception:
                continue

    def clear_local_data(self) -> int:
        """
        Clear local data tables while preserving config and API credentials.
        Returns total rows deleted across tables.
        """
        tables = [
            "media_cache",
            "media_content_cache",
            "media_url_map",
            "media_thumbnail_cache",
            "content_cache",
            "download_queue",
            "browsing_history",
            "favorites",
            "creators",
            "creators_registry_meta",
            "creator_meta",
            "download_index",
            "post_index",
        ]
        total_deleted = 0
        cursor = self.conn.cursor()
        for table in tables:
            cursor.execute(f"DELETE FROM {table}")
            if cursor.rowcount and cursor.rowcount > 0:
                total_deleted += cursor.rowcount
        self.conn.commit()
        return total_deleted

    def enforce_cache_limit(self, max_size_mb: int = 1000):
        """
        Enforce cache size limit by removing oldest cache files on disk

        Args:
            max_size_mb: Maximum cache size in megabytes
        """
        max_size_bytes = max_size_mb * 1024 * 1024
        current_size = self.get_cache_size()

        if current_size <= max_size_bytes:
            return 0  # No cleanup needed

        # Calculate how much to delete
        target_size = int(max_size_bytes * 0.8)  # Delete until we're at 80% of max
        to_delete_bytes = current_size - target_size

        logger.info(f"Cache size {current_size / 1024 / 1024:.2f}MB exceeds limit {max_size_mb}MB. Deleting {to_delete_bytes / 1024 / 1024:.2f}MB")

        candidates: List[Tuple[float, int, Path]] = []
        for path in self._iter_cache_files():
            try:
                stat = path.stat()
            except Exception:
                continue
            candidates.append((stat.st_mtime, stat.st_size, path))

        candidates.sort(key=lambda item: item[0])

        deleted_count = 0
        deleted_size = 0
        cursor = self.conn.cursor()

        for _, file_size, file_path_obj in candidates:
            if deleted_size >= to_delete_bytes:
                break
            try:
                file_path_obj.unlink()
                logger.debug(f"Deleted cached file: {file_path_obj}")
            except Exception as e:
                logger.error(f"Error deleting cached file {file_path_obj}: {e}")
                continue
            try:
                cursor.execute("DELETE FROM media_cache WHERE file_path = ?", (str(file_path_obj),))
            except Exception:
                pass
            deleted_count += 1
            deleted_size += file_size

        self.conn.commit()
        logger.info(f"Deleted {deleted_count} cached files ({deleted_size / 1024 / 1024:.2f}MB)")

        return deleted_count

    def get_all_cached_urls(self) -> List[str]:
        """
        Get list of all cached URLs

        Returns:
            List of cached URLs
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT url FROM media_cache")
        return [row['url'] for row in cursor.fetchall()]
    
    def add_to_download_queue(self, url: str, filename: str, destination_path: str) -> int:
        """
        Add item to download queue
        
        Args:
            url: URL to download
            filename: Target filename
            destination_path: Destination directory path
            
        Returns:
            Queue item ID
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO download_queue (url, filename, destination_path, status)
            VALUES (?, ?, ?, 'pending')
        """, (url, filename, destination_path))
        self.conn.commit()
        return cursor.lastrowid
    
    def update_download_progress(self, queue_id: int, progress: float, 
                                 status: str = 'downloading', error: Optional[str] = None,
                                 downloaded_bytes: Optional[int] = None,
                                 file_size: Optional[int] = None):
        """Update download queue item progress and state.
        
        Args:
            queue_id: The download queue item ID
            progress: Progress percentage (0-100)
            status: Download status (pending, downloading, paused, completed, failed, cancelled)
            error: Error message if failed
            downloaded_bytes: Number of bytes downloaded so far
            file_size: Total file size in bytes
        """
        cursor = self.conn.cursor()
        
        if status == 'completed':
            cursor.execute("""
                UPDATE download_queue 
                SET progress = ?, status = ?, completed_at = CURRENT_TIMESTAMP,
                    downloaded_bytes = COALESCE(?, downloaded_bytes),
                    file_size = COALESCE(?, file_size)
                WHERE id = ?
            """, (progress, status, downloaded_bytes, file_size, queue_id))
        elif status == 'paused':
            cursor.execute("""
                UPDATE download_queue 
                SET progress = ?, status = ?, paused_at = CURRENT_TIMESTAMP,
                    downloaded_bytes = COALESCE(?, downloaded_bytes),
                    file_size = COALESCE(?, file_size)
                WHERE id = ?
            """, (progress, status, downloaded_bytes, file_size, queue_id))
        elif status == 'downloading' and downloaded_bytes is not None:
            cursor.execute("""
                UPDATE download_queue 
                SET progress = ?, status = ?, downloaded_bytes = ?,
                    file_size = COALESCE(?, file_size),
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP)
                WHERE id = ?
            """, (progress, status, downloaded_bytes, file_size, queue_id))
        elif error:
            cursor.execute("""
                UPDATE download_queue 
                SET progress = ?, status = ?, error_message = ?,
                    retry_count = retry_count + 1
                WHERE id = ?
            """, (progress, status, error, queue_id))
        else:
            cursor.execute("""
                UPDATE download_queue 
                SET progress = ?, status = ?
                WHERE id = ?
            """, (progress, status, queue_id))
        
        self.conn.commit()
    
    def get_pending_downloads(self) -> List[Dict]:
        """Retrieve all pending and paused downloads for resuming."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM download_queue 
            WHERE status IN ('pending', 'downloading', 'paused')
            ORDER BY created_at
        """)
        return [dict(row) for row in cursor.fetchall()]

    def get_download_by_id(self, queue_id: int) -> Optional[Dict]:
        """Get a specific download queue item by ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM download_queue WHERE id = ?", (queue_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_resumable_downloads(self) -> List[Dict]:
        """Get downloads that can be resumed (paused or failed with retries remaining)."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM download_queue 
            WHERE status IN ('paused', 'downloading')
               OR (status = 'failed' AND retry_count < 3)
            ORDER BY created_at
        """)
        return [dict(row) for row in cursor.fetchall()]
    
    def add_favorite(self, post_id: str, service: str, user_id: str, 
                    title: Optional[str] = None, thumbnail_url: Optional[str] = None):
        """Add post to favorites"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO favorites 
            (post_id, service, user_id, title, thumbnail_url)
            VALUES (?, ?, ?, ?, ?)
        """, (post_id, service, user_id, title, thumbnail_url))
        self.conn.commit()
    
    def remove_favorite(self, post_id: str, service: str):
        """Remove post from favorites"""
        cursor = self.conn.cursor()
        cursor.execute("""
            DELETE FROM favorites WHERE post_id = ? AND service = ?
        """, (post_id, service))
        self.conn.commit()
    
    def get_favorites(self, service: Optional[str] = None) -> List[Dict]:
        """Retrieve favorites, optionally filtered by service"""
        cursor = self.conn.cursor()
        if service:
            cursor.execute("""
                SELECT * FROM favorites WHERE service = ? ORDER BY added_at DESC
            """, (service,))
        else:
            cursor.execute("""
                SELECT * FROM favorites ORDER BY added_at DESC
            """)
        return [dict(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Creators registry persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _index_name(name: str) -> str:
        return (name or "").strip().lower()

    def replace_creators_for_platform(
        self,
        platform: str,
        creators: Iterable[Dict[str, Any]],
    ):
        """
        Replace creators registry for a platform and update last_updated.
        Expected creator dict keys: id, service, name, favorited (optional), post_count (optional),
        dm_count/share_count/chat_count (optional), plus profile/list metadata
        like creator_indexed/creator_updated/public_id/relation_id/display_href.
        """
        cursor = self.conn.cursor()

        rows = []
        for c in creators:
            creator_id = c.get("id") or c.get("creator_id")
            service = c.get("service")
            name = c.get("name") or ""
            rows.append((
                platform,
                service,
                creator_id,
                name,
                self._index_name(name),
                c.get("creator_indexed"),
                c.get("creator_updated"),
                c.get("public_id"),
                c.get("relation_id"),
                c.get("favorited"),
                c.get("post_count"),
                c.get("dm_count"),
                c.get("share_count"),
                c.get("chat_count"),
                c.get("display_href"),
            ))

        if rows:
            cursor.executemany("""
                INSERT INTO creators
                (platform, service, creator_id, name, indexed_name, creator_indexed, creator_updated,
                 public_id, relation_id, favorited_count, post_count, dm_count, share_count, chat_count, display_href)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, service, creator_id) DO UPDATE SET
                  name = excluded.name,
                  indexed_name = excluded.indexed_name,
                  creator_indexed = COALESCE(excluded.creator_indexed, creators.creator_indexed),
                  creator_updated = COALESCE(excluded.creator_updated, creators.creator_updated),
                  public_id = COALESCE(excluded.public_id, creators.public_id),
                  relation_id = COALESCE(excluded.relation_id, creators.relation_id),
                  favorited_count = COALESCE(excluded.favorited_count, creators.favorited_count),
                  post_count = COALESCE(excluded.post_count, creators.post_count),
                  dm_count = COALESCE(excluded.dm_count, creators.dm_count),
                  share_count = COALESCE(excluded.share_count, creators.share_count),
                  chat_count = COALESCE(excluded.chat_count, creators.chat_count),
                  display_href = COALESCE(excluded.display_href, creators.display_href)
            """, rows)

        cursor.execute("""
            INSERT OR REPLACE INTO creators_registry_meta (platform, last_updated)
            VALUES (?, CURRENT_TIMESTAMP)
        """, (platform,))

        self.conn.commit()

    def get_creators_registry_updated(self, platform: str) -> Optional[str]:
        """Return registry last_updated timestamp (ISO string) for a platform."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT last_updated FROM creators_registry_meta WHERE platform = ?
        """, (platform,))
        row = cursor.fetchone()
        return row["last_updated"] if row else None

    def get_creators(
        self,
        platform: Optional[str] = None,
        service: Optional[str] = None,
        include_hidden: bool = False,
    ) -> List[Dict]:
        """
        Retrieve creators from registry, optionally filtered by platform/service.
        """
        cursor = self.conn.cursor()
        filters = []
        params: List[Any] = []

        if platform:
            filters.append("c.platform = ?")
            params.append(platform)
        if service:
            filters.append("c.service = ?")
            params.append(service)
        if not include_hidden:
            filters.append("(m.hidden IS NULL OR m.hidden = 0)")

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        cursor.execute(f"""
            SELECT c.*, m.favorited, m.pinned, m.hidden, m.last_seen
            FROM creators c
            LEFT JOIN creator_meta m
              ON c.platform = m.platform AND c.service = m.service AND c.creator_id = m.creator_id
            {where_clause}
            ORDER BY c.indexed_name ASC
        """, params)

        return [dict(row) for row in cursor.fetchall()]

    def get_creators_paginated(
        self,
        platform: str,
        service: Optional[str] = None,
        *,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "name",
        sort_dir: str = "ASC",
        include_hidden: bool = False,
    ) -> List[Dict]:
        sort_map = {
            "name": "c.indexed_name",
            "updated": "c.updated_at",
            "favorited": "c.favorited_count",
        }
        sort_col = sort_map.get(sort_by, "c.indexed_name")
        sort_dir = "DESC" if str(sort_dir).upper() == "DESC" else "ASC"

        filters = ["c.platform = ?"]
        params: List[Any] = [platform]
        if service:
            filters.append("c.service = ?")
            params.append(service)
        if not include_hidden:
            filters.append("(m.hidden IS NULL OR m.hidden = 0)")

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            SELECT c.*, m.favorited, m.pinned, m.hidden, m.last_seen
            FROM creators c
            LEFT JOIN creator_meta m
              ON c.platform = m.platform AND c.service = m.service AND c.creator_id = m.creator_id
            {where_clause}
            ORDER BY {sort_col} {sort_dir}
            LIMIT ? OFFSET ?
            """,
            params + [int(limit), int(offset)],
        )
        return [dict(row) for row in cursor.fetchall()]

    def search_creators(
        self,
        platform: str,
        service: Optional[str],
        query: str,
        *,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "name",
        sort_dir: str = "ASC",
        include_hidden: bool = False,
    ) -> List[Dict]:
        sort_map = {
            "name": "c.indexed_name",
            "updated": "c.updated_at",
            "favorited": "c.favorited_count",
        }
        sort_col = sort_map.get(sort_by, "c.indexed_name")
        sort_dir = "DESC" if str(sort_dir).upper() == "DESC" else "ASC"

        filters = ["c.platform = ?"]
        params: List[Any] = [platform]
        if service:
            filters.append("c.service = ?")
            params.append(service)
        if query:
            filters.append("c.indexed_name LIKE ?")
            params.append(f"%{query.strip().lower()}%")
        if not include_hidden:
            filters.append("(m.hidden IS NULL OR m.hidden = 0)")

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            SELECT c.*, m.favorited, m.pinned, m.hidden, m.last_seen
            FROM creators c
            LEFT JOIN creator_meta m
              ON c.platform = m.platform AND c.service = m.service AND c.creator_id = m.creator_id
            {where_clause}
            ORDER BY {sort_col} {sort_dir}
            LIMIT ? OFFSET ?
            """,
            params + [int(limit), int(offset)],
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_creators_count(
        self,
        platform: str,
        service: Optional[str] = None,
        query: Optional[str] = None,
        *,
        include_hidden: bool = False,
    ) -> int:
        filters = ["c.platform = ?"]
        params: List[Any] = [platform]
        if service:
            filters.append("c.service = ?")
            params.append(service)
        if query:
            filters.append("c.indexed_name LIKE ?")
            params.append(f"%{query.strip().lower()}%")
        if not include_hidden:
            filters.append("(m.hidden IS NULL OR m.hidden = 0)")

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            SELECT COUNT(*) as count
            FROM creators c
            LEFT JOIN creator_meta m
              ON c.platform = m.platform AND c.service = m.service AND c.creator_id = m.creator_id
            {where_clause}
            """,
            params,
        )
        row = cursor.fetchone()
        return int(row["count"] or 0)

    def update_creator_post_count(
        self,
        platform: str,
        service: str,
        creator_id: str,
        post_count: int,
        *,
        dm_count: Optional[int] = None,
        share_count: Optional[int] = None,
        chat_count: Optional[int] = None,
        favorited: Optional[int] = None,
        creator_indexed: Optional[str] = None,
        creator_updated: Optional[str] = None,
        public_id: Optional[str] = None,
        relation_id: Optional[int] = None,
        name: Optional[str] = None,
        display_href: Optional[str] = None,
    ) -> None:
        updates = ["post_count = ?"]
        params: List[Any] = [post_count]

        if dm_count is not None:
            updates.append("dm_count = ?")
            params.append(dm_count)
        if share_count is not None:
            updates.append("share_count = ?")
            params.append(share_count)
        if chat_count is not None:
            updates.append("chat_count = ?")
            params.append(chat_count)
        if favorited is not None:
            updates.append("favorited_count = ?")
            params.append(favorited)
        if creator_indexed is not None:
            updates.append("creator_indexed = ?")
            params.append(creator_indexed)
        if creator_updated is not None:
            updates.append("creator_updated = ?")
            params.append(creator_updated)
        if public_id is not None:
            updates.append("public_id = ?")
            params.append(public_id)
        if relation_id is not None:
            updates.append("relation_id = ?")
            params.append(relation_id)
        if name is not None:
            updates.append("name = ?")
            params.append(name)
            updates.append("indexed_name = ?")
            params.append(self._index_name(name))
        if display_href is not None:
            updates.append("display_href = ?")
            params.append(display_href)

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.extend([platform, service, creator_id])

        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            UPDATE creators
            SET {", ".join(updates)}
            WHERE platform = ? AND service = ? AND creator_id = ?
            """,
            params,
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Per-creator metadata
    # ------------------------------------------------------------------

    def upsert_creator_meta(
        self,
        platform: str,
        service: str,
        creator_id: str,
        *,
        favorited: Optional[bool] = None,
        pinned: Optional[bool] = None,
        hidden: Optional[bool] = None,
        last_seen: Optional[datetime] = None,
    ):
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO creator_meta
            (platform, service, creator_id)
            VALUES (?, ?, ?)
        """, (platform, service, creator_id))

        updates = []
        params: List[Any] = []
        if favorited is not None:
            updates.append("favorited = ?")
            params.append(1 if favorited else 0)
        if pinned is not None:
            updates.append("pinned = ?")
            params.append(1 if pinned else 0)
        if hidden is not None:
            updates.append("hidden = ?")
            params.append(1 if hidden else 0)
        if last_seen is not None:
            updates.append("last_seen = ?")
            params.append(last_seen)

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.extend([platform, service, creator_id])
            cursor.execute(f"""
                UPDATE creator_meta
                SET {", ".join(updates)}
                WHERE platform = ? AND service = ? AND creator_id = ?
            """, params)

        self.conn.commit()

    # ------------------------------------------------------------------
    # Oversized files tracking
    # ------------------------------------------------------------------

    def is_file_oversized(self, url: str) -> Optional[Dict]:
        """
        Check if a URL has been flagged as too large.
        
        Returns:
            Dict with file_size, size_limit, flagged_at if oversized, None otherwise
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT file_size, size_limit, flagged_at
            FROM oversized_files
            WHERE url = ?
        """, (url,))
        row = cursor.fetchone()
        if row:
            return {
                'file_size': row[0],
                'size_limit': row[1],
                'flagged_at': row[2]
            }
        return None

    def flag_file_as_oversized(
        self,
        url: str,
        file_size: int,
        size_limit: int
    ):
        """
        Record that a file exceeds size limits and should not be requested.
        
        Args:
            url: File URL
            file_size: Detected file size in bytes
            size_limit: The limit that was exceeded
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO oversized_files (url, file_size, size_limit, flagged_at, checked_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(url) DO UPDATE SET
                file_size = excluded.file_size,
                size_limit = excluded.size_limit,
                checked_at = CURRENT_TIMESTAMP
        """, (url, file_size, size_limit))
        self.conn.commit()

    def remove_oversized_flag(self, url: str):
        """
        Remove oversized flag (e.g., if limits changed or manual override)
        """
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM oversized_files WHERE url = ?", (url,))
        self.conn.commit()

    def clear_old_oversized_flags(self, days: int = 30):
        """
        Clear oversized flags older than specified days (allows re-checking)
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            DELETE FROM oversized_files
            WHERE julianday('now') - julianday(checked_at) > ?
        """, (days,))
        self.conn.commit()

    # ------------------------------------------------------------------
    # Download index
    # ------------------------------------------------------------------

    def upsert_download_index(
        self,
        url: str,
        local_path: str,
        *,
        file_size: Optional[int] = None,
        sha256: Optional[str] = None,
        verified_at: Optional[datetime] = None,
    ):
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO download_index
            (url, local_path, file_size, sha256, verified_at, downloaded_at, last_accessed)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(url) DO UPDATE SET
                local_path = excluded.local_path,
                file_size = COALESCE(excluded.file_size, download_index.file_size),
                sha256 = COALESCE(excluded.sha256, download_index.sha256),
                verified_at = COALESCE(excluded.verified_at, download_index.verified_at),
                last_accessed = CURRENT_TIMESTAMP
        """, (url, local_path, file_size, sha256, verified_at))
        self.conn.commit()

    def get_download_by_url(self, url: str) -> Optional[Dict]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM download_index WHERE url = ?
        """, (url,))
        row = cursor.fetchone()
        if not row:
            return None
        cursor.execute("""
            UPDATE download_index SET last_accessed = CURRENT_TIMESTAMP WHERE url = ?
        """, (url,))
        self.conn.commit()
        return dict(row)

    # ------------------------------------------------------------------
    # Post index (optional)
    # ------------------------------------------------------------------

    def upsert_post_index(
        self,
        platform: str,
        service: str,
        creator_id: str,
        post_id: str,
        *,
        title: Optional[str] = None,
        published_at: Optional[datetime] = None,
        file_count: Optional[int] = None,
        tags: Optional[List[str]] = None,
    ):
        tags_json = json.dumps(tags) if tags else None
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO post_index
            (platform, service, creator_id, post_id, title, published_at, file_count, tags_json, cached_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(platform, service, creator_id, post_id) DO UPDATE SET
                title = COALESCE(excluded.title, post_index.title),
                published_at = COALESCE(excluded.published_at, post_index.published_at),
                file_count = COALESCE(excluded.file_count, post_index.file_count),
                tags_json = COALESCE(excluded.tags_json, post_index.tags_json),
                cached_at = CURRENT_TIMESTAMP
        """, (
            platform,
            service,
            creator_id,
            post_id,
            title,
            published_at,
            file_count,
            tags_json,
        ))
        self.conn.commit()

    def get_post_index(
        self,
        platform: str,
        service: str,
        creator_id: str,
    ) -> List[Dict]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM post_index
            WHERE platform = ? AND service = ? AND creator_id = ?
            ORDER BY published_at DESC
        """, (platform, service, creator_id))
        rows = [dict(r) for r in cursor.fetchall()]
        for row in rows:
            if row.get("tags_json"):
                row["tags"] = json.loads(row["tags_json"])
        return rows
    
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            self.conn = None
