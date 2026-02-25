"""
File metadata manager for querying file information from search_hash API.

This is a Core layer service that handles:
- Asynchronous metadata queries
- Threading management
- HTTP requests to search_hash API
- Metadata caching
"""
from PyQt6.QtCore import QObject, QThread, pyqtSignal
import requests
import logging
from typing import Optional, Dict, List
from urllib.parse import urlparse
from pathlib import Path
import re

logger = logging.getLogger(__name__)


class _FileMetadataWorker(QThread):
    """Internal worker thread for metadata queries."""
    
    # Signal: (file_url, metadata_dict)
    metadata_ready = pyqtSignal(str, dict)
    
    # Signal: all queries completed
    all_completed = pyqtSignal()
    
    def __init__(self, urls: List[str], platform: str, hash_re: re.Pattern, timeout: int):
        super().__init__()
        self.urls = urls
        self.platform = platform
        self._hash_re = hash_re
        self._timeout = timeout
        self._cancelled = False

    def cancel(self):
        """Cancel the worker."""
        self._cancelled = True
        
    def run(self):
        """Query metadata for all files."""
        for url in self.urls:
            if self._cancelled:
                break
                
            if not url:
                continue
            
            # Extract hash from URL
            file_hash = self._extract_hash_from_url(url)
            if not file_hash:
                continue
            
            # Query API
            metadata = self._query_metadata(file_hash, url)
            if metadata and not self._cancelled:
                self.metadata_ready.emit(url, metadata)
        
        if not self._cancelled:
            self.all_completed.emit()
    
    def _extract_hash_from_url(self, url: str) -> Optional[str]:
        """Extract file hash from URL."""
        try:
            parsed = urlparse(url)
            stem = Path(parsed.path).stem.lower()
        except Exception:
            return None
        
        if not stem or not self._hash_re.fullmatch(stem):
            return None
        
        return stem
    
    def _query_metadata(self, file_hash: str, url: str) -> Optional[Dict]:
        """Query search_hash API for file metadata."""
        # Determine API base from URL
        if 'kemono' in url.lower():
            api_base = 'https://kemono.cr/api'
        else:
            api_base = 'https://coomer.st/api'
        
        api_url = f"{api_base}/v1/search_hash/{file_hash}"
        
        try:
            resp = requests.get(
                api_url,
                timeout=self._timeout,
                headers={
                    "User-Agent": "Coomer-BetterUI",
                    "Accept": "text/css",
                },
            )
            
            if not resp.ok:
                return None
            
            data = resp.json()
            if not isinstance(data, dict):
                return None
            
            # Extract relevant metadata
            metadata = {}
            
            if 'size' in data:
                try:
                    size = int(data['size'])
                    if size > 0:
                        metadata['size'] = size
                except (TypeError, ValueError):
                    pass
            
            if 'mime' in data and data['mime']:
                metadata['mime'] = data['mime']
            
            if 'ext' in data and data['ext']:
                metadata['ext'] = data['ext']
            
            # Try to find a non-hash filename from posts
            if 'posts' in data and isinstance(data['posts'], list):
                for post in data['posts']:
                    if not isinstance(post, dict):
                        continue
                    
                    # Check for attachment with real filename
                    if 'attachments' in post:
                        attachments = post['attachments']
                        if isinstance(attachments, list):
                            for att in attachments:
                                if isinstance(att, dict):
                                    att_name = att.get('name')
                                    att_path = att.get('path')
                                    # If attachment has the same hash in path and a real name
                                    if att_path and att_name and file_hash in att_path:
                                        # Only use if it's not a hash filename
                                        if not self._hash_re.match(Path(att_name).stem.lower()):
                                            metadata['filename'] = att_name
                                            break
                    
                    if 'filename' in metadata:
                        break
            
            return metadata if metadata else None
            
        except Exception as e:
            logger.debug(f"Failed to query metadata for {file_hash}: {e}")
            return None


class FileMetadataManager(QObject):
    """
    Core service for managing file metadata queries.
    
    UI should call query_metadata() and subscribe to metadata_received signal.
    """
    
    # Signal: (file_url, metadata_dict)
    metadata_received = pyqtSignal(str, dict)
    
    # Signal: batch query completed
    query_completed = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        self._worker = None
        self._hash_re = re.compile(r'^[0-9a-f]{64}$')
        self._timeout = 8
        self._metadata_cache: Dict[str, dict] = {}
        
    def query_metadata(self, urls: List[str], platform: str = "coomer"):
        """
        Query metadata for a list of file URLs.
        
        Args:
            urls: List of file URLs to query
            platform: Platform name ("coomer" or "kemono")
        
        Results are emitted via metadata_received signal.
        """
        # Stop any existing worker
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait()
        
        # Filter to only URLs we don't have cached
        urls_to_query = []
        for url in urls:
            if url in self._metadata_cache:
                # Emit cached result immediately
                self.metadata_received.emit(url, self._metadata_cache[url])
            else:
                urls_to_query.append(url)
        
        if not urls_to_query:
            self.query_completed.emit()
            return
        
        # Start new worker
        self._worker = _FileMetadataWorker(urls_to_query, platform, self._hash_re, self._timeout)
        self._worker.metadata_ready.connect(self._on_metadata_ready)
        self._worker.all_completed.connect(self._on_all_completed)
        self._worker.start()
        
        logger.debug(f"Started metadata query for {len(urls_to_query)} files")
    
    def _on_metadata_ready(self, url: str, metadata: dict):
        """Handle metadata result from worker."""
        # Cache the result
        self._metadata_cache[url] = metadata
        
        # Forward to subscribers
        self.metadata_received.emit(url, metadata)
    
    def _on_all_completed(self):
        """Handle completion of worker."""
        logger.debug("Metadata query batch completed")
        self.query_completed.emit()
    
    def get_cached_metadata(self, url: str) -> Optional[Dict]:
        """Get cached metadata for a URL if available."""
        return self._metadata_cache.get(url)
    
    def clear_cache(self):
        """Clear all cached metadata."""
        self._metadata_cache.clear()
