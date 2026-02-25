"""
Download manager for file downloads and batch operations
"""
import aiohttp
import asyncio
import logging
import socket
from pathlib import Path
from typing import List, Optional, Callable, Tuple
from datetime import datetime
import zipfile
from io import BytesIO

from src.core.http_client import MEDIA_HEADERS, get_http_client
from src.core.range_proxy import get_range_proxy, RangeProxy

logger = logging.getLogger(__name__)

class DownloadManager:
    """
    Manages file downloads with queue, progress tracking, and batch operations
    """
    
    def __init__(
        self,
        db_manager,
        max_concurrent: int = 3,
        cache_dir: Optional[Path] = None,
        use_range_proxy: Optional[bool] = None,
    ):
        """
        Initialize download manager

        Args:
            db_manager: DatabaseManager instance
            max_concurrent: Maximum concurrent downloads
            cache_dir: Cache directory for downloaded media.
                       Defaults to ~/.coomer-betterui/media_cache if not specified.
        """
        self.db = db_manager
        self.max_concurrent = max_concurrent
        if cache_dir is None:
            cache_dir = Path.home() / ".coomer-betterui" / "media_cache"
        self.cache_dir = cache_dir
        self.session: Optional[aiohttp.ClientSession] = None
        self._direct_session: Optional[aiohttp.ClientSession] = None
        self.active_downloads = {}  # queue_id -> download task info
        self._paused_downloads: set = set()  # Set of paused queue_ids
        self._cancelled_downloads: set = set()  # Set of cancelled queue_ids
        self.download_queue = asyncio.Queue()
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._range_proxy: Optional[RangeProxy] = None
        self._use_range_proxy = self._resolve_use_range_proxy(use_range_proxy)
        # Retry settings
        self._max_retries = 3
        self._retry_delay_base = 2.0  # Exponential backoff base (seconds)
        self._chunk_size = 64 * 1024  # 64KB chunks for resilient downloading
    
    async def __aenter__(self):
        """Async context manager entry"""
        await self.create_session()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close_session()
    
    async def create_session(self):
        """Create aiohttp session with proper headers and proxy support"""
        if not self.session:
            # Try to use centralized HTTP client if available
            http_client = get_http_client()
            if http_client:
                self.session = await http_client.create_async_session(
                    headers=MEDIA_HEADERS,
                    total_timeout=None,  # No total timeout for large downloads
                )
            else:
                # Fallback to basic session
                timeout = aiohttp.ClientTimeout(total=None, sock_read=60)
                self.session = aiohttp.ClientSession(
                    timeout=timeout,
                    headers=MEDIA_HEADERS,
                )
        if not self._direct_session:
            timeout = aiohttp.ClientTimeout(total=None, sock_read=60)
            connector = aiohttp.TCPConnector(family=socket.AF_INET)
            self._direct_session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers=MEDIA_HEADERS,
            )

    def _resolve_use_range_proxy(self, override: Optional[bool]) -> bool:
        if override is not None:
            return override
        try:
            return self.db.get_config('enable_range_proxy', 'false') == 'true'
        except Exception:
            return False

    def _get_range_proxy(self) -> Optional[RangeProxy]:
        if not self._use_range_proxy:
            return None
        if self._range_proxy is not None:
            return self._range_proxy

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
            chunk_size = 8 * 1024 * 1024

        proxy_url = None
        proxy_pool = []
        http_client = get_http_client()
        if http_client and http_client.config.proxy_config.enabled:
            if http_client.config.proxy_config.proxy_pool:
                proxy_pool = http_client.config.proxy_config.proxy_pool
            else:
                proxy_url = http_client.config.proxy_config.get_proxy()

        self._range_proxy = get_range_proxy(
            cache_dir=Path.home() / ".coomer-betterui" / "range_cache",
            max_concurrent_chunks=max_concurrent_chunks,
            max_connections_per_host=max_connections_per_host,
            max_total_connections=max_total_connections,
            chunk_size=chunk_size,
            proxy_url=proxy_url,
            proxy_pool=proxy_pool,
        )
        return self._range_proxy
    
    async def close_session(self):
        """Close aiohttp session"""
        if self.session:
            await self.session.close()
            self.session = None
        if self._direct_session:
            await self._direct_session.close()
            self._direct_session = None
    
    async def download_file(self, url: str, destination: Path,
                           progress_callback: Optional[Callable] = None,
                           queue_id: Optional[int] = None) -> bool:
        """
        Download single file

        Args:
            url: URL to download
            destination: Destination file path
            progress_callback: Optional callback for progress updates (progress, total)
            queue_id: Optional queue ID for database tracking

        Returns:
            True if successful, False otherwise
        """
        async with self.semaphore:
            try:
                # Create destination directory
                destination.parent.mkdir(parents=True, exist_ok=True)

                # Try to assemble from RangeProxy cached chunks first
                # This reuses chunks downloaded during video playback
                range_proxy = self._get_range_proxy()
                if range_proxy:
                    cached_pct = range_proxy.get_cached_percentage(url)
                    if cached_pct == 100.0:
                        logger.info(f"Attempting to assemble from cached video chunks: {url}")
                        if range_proxy.assemble_cached_file(url, destination, progress_callback):
                            logger.info(f"Assembled from cached chunks: {destination}")
                            if queue_id:
                                try:
                                    if hasattr(self.db, 'update_download_progress'):
                                        self.db.update_download_progress(queue_id, 100.0, 'completed')
                                except Exception:
                                    pass
                            return True
                        logger.debug(f"Cache assembly failed, falling back to download")
                    elif cached_pct > 0:
                        logger.info(f"Partial cache ({cached_pct:.1f}%) for {url} - will stream via proxy")

                # Check media cache (different from range proxy cache)
                import shutil
                cached = None
                if hasattr(self.db, 'get_cached_media'):
                    try:
                        cached = self.db.get_cached_media(url)
                    except:
                        pass

                if cached and cached.get('file_path'):
                    cached_path = Path(cached['file_path'])
                    if cached_path.exists():
                        # File is in cache - copy it to destination
                        logger.info(f"Using cached file: {cached_path} -> {destination}")
                        try:
                            shutil.copy2(cached_path, destination)

                            # Report progress as complete
                            if progress_callback:
                                file_size = cached_path.stat().st_size
                                progress_callback(file_size, file_size)

                            if queue_id:
                                try:
                                    if hasattr(self.db, 'update_download_progress'):
                                        self.db.update_download_progress(queue_id, 100.0, 'completed')
                                except:
                                    pass

                            logger.info(f"Copied from cache: {destination}")
                            return True
                        except Exception as e:
                            logger.warning(f"Failed to copy from cache: {e}. Downloading instead.")

                # Ensure session is created
                if not self.session:
                    await self.create_session()

                # Update status to downloading
                if queue_id:
                    try:
                        if hasattr(self.db, 'update_download_progress'):
                            self.db.update_download_progress(queue_id, 0.0, 'downloading', downloaded_bytes=0)
                    except Exception:
                        pass

                # Check for partial download to resume
                existing_size = 0
                if destination.exists():
                    existing_size = destination.stat().st_size
                    logger.info(f"Found partial download: {existing_size} bytes at {destination}")

                # Build headers for resume
                headers = {}
                if existing_size > 0:
                    headers['Range'] = f'bytes={existing_size}-'
                    logger.info(f"Resuming download from byte {existing_size}")

                logger.info(f"Downloading: {url} -> {destination}")

                download_url = url
                range_proxy = self._get_range_proxy()
                if range_proxy:
                    download_url = range_proxy.proxy_url(url)
                    logger.info(f"Downloading via RangeProxy: {url} -> {download_url}")

                request_session = self.session
                if download_url.startswith("http://127.0.0.1") or download_url.startswith("http://localhost"):
                    request_session = self._direct_session

                async with request_session.get(download_url, headers=headers) as response:
                    # Accept 200 (full) or 206 (partial/resume)
                    if response.status not in (200, 206):
                        error_msg = f"HTTP {response.status}"
                        logger.error(f"Download failed: {error_msg}")
                        if queue_id:
                            try:
                                if hasattr(self.db, 'update_download_progress'):
                                    self.db.update_download_progress(queue_id, 0.0, 'failed', error_msg)
                            except:
                                pass
                        return False

                    # Parse content info
                    if response.status == 206:
                        # Resuming - get total size from Content-Range header
                        # Format: "bytes 12345-67890/123456" or "bytes 12345-67890/*"
                        content_range = response.headers.get('Content-Range', '')
                        total_size = 0
                        if '/' in content_range:
                            total_part = content_range.split('/')[-1].strip()
                            if total_part != '*':
                                try:
                                    total_size = int(total_part)
                                except (ValueError, TypeError):
                                    total_size = 0
                        
                        if total_size == 0:
                            # Fallback: use existing_size + content-length
                            content_length = response.headers.get('content-length', '0')
                            try:
                                total_size = existing_size + int(content_length)
                            except (ValueError, TypeError):
                                total_size = 0
                        
                        downloaded = existing_size
                        file_mode = 'ab'  # Append mode
                        logger.info(f"Resuming: {existing_size}/{total_size} bytes")
                    else:
                        # Fresh download (server doesn't support resume or sent full file)
                        content_length = response.headers.get('content-length', '0')
                        try:
                            total_size = int(content_length)
                        except (ValueError, TypeError):
                            total_size = 0
                        downloaded = 0
                        existing_size = 0  # Reset - server sent full file
                        file_mode = 'wb'  # Write mode

                    with open(destination, file_mode) as f:
                        async for chunk in response.content.iter_chunked(self._chunk_size):
                            # Check if paused or cancelled
                            if queue_id and queue_id in self._cancelled_downloads:
                                logger.info(f"Download cancelled: {destination}")
                                self._cancelled_downloads.discard(queue_id)
                                return False
                            
                            if queue_id and queue_id in self._paused_downloads:
                                # Save progress and return
                                logger.info(f"Download paused at {downloaded}/{total_size} bytes: {destination}")
                                if hasattr(self.db, 'update_download_progress'):
                                    progress = (downloaded / total_size * 100) if total_size > 0 else 0
                                    self.db.update_download_progress(
                                        queue_id, progress, 'paused',
                                        downloaded_bytes=downloaded, file_size=total_size
                                    )
                                return False
                            
                            f.write(chunk)
                            downloaded += len(chunk)

                            # Update progress
                            if total_size > 0:
                                progress = (downloaded / total_size) * 100
                                if progress_callback:
                                    progress_callback(downloaded, total_size)
                                if queue_id:
                                    try:
                                        if hasattr(self.db, 'update_download_progress'):
                                            # Update progress with byte tracking (throttled)
                                            if downloaded % (self._chunk_size * 16) < self._chunk_size:
                                                self.db.update_download_progress(
                                                    queue_id, progress, 'downloading',
                                                    downloaded_bytes=downloaded, file_size=total_size
                                                )
                                    except Exception:
                                        pass

                logger.info(f"Download complete: {destination}")
                if queue_id:
                    try:
                        if hasattr(self.db, 'update_download_progress'):
                            self.db.update_download_progress(
                                queue_id, 100.0, 'completed',
                                downloaded_bytes=downloaded, file_size=total_size
                            )
                    except Exception:
                        pass
                
                return True
                
            except aiohttp.ClientError as e:
                error_msg = f"Network error: {e}"
                logger.error(f"Download error for {url}: {error_msg}")
                if queue_id:
                    try:
                        if hasattr(self.db, 'update_download_progress'):
                            self.db.update_download_progress(queue_id, 0.0, 'failed', error_msg)
                    except Exception:
                        pass
                return False
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Download error for {url}: {error_msg}")
                if queue_id:
                    try:
                        if hasattr(self.db, 'update_download_progress'):
                            self.db.update_download_progress(queue_id, 0.0, 'failed', error_msg)
                    except Exception:
                        pass
                return False
    
    async def batch_download(self, urls: List[str], destination_dir: Path,
                            create_zip: bool = False, zip_filename: Optional[str] = None,
                            progress_callback: Optional[Callable] = None) -> List[Path]:
        """
        Download multiple files
        
        Args:
            urls: List of URLs to download
            destination_dir: Destination directory
            create_zip: Whether to create a ZIP archive
            zip_filename: ZIP filename (if create_zip is True)
            progress_callback: Optional callback for overall progress
            
        Returns:
            List of successfully downloaded file paths
        """
        items = []
        destination_dir.mkdir(parents=True, exist_ok=True)
        for url in urls:
            filename = Path(url).name
            items.append((url, destination_dir / filename))

        return await self.batch_download_items(
            items,
            create_zip=create_zip,
            zip_filename=zip_filename,
            progress_callback=progress_callback,
        )

    async def batch_download_items(
        self,
        items: List[Tuple[str, Path]],
        *,
        create_zip: bool = False,
        zip_filename: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
    ) -> List[Path]:
        """
        Download multiple files to explicit destinations.

        Args:
            items: List of (url, destination_path)
            create_zip: Whether to create a ZIP archive
            zip_filename: ZIP filename (if create_zip is True)
            progress_callback: Optional callback for overall progress

        Returns:
            List of successfully downloaded file paths
        """
        if not items:
            return []

        # Add to queue
        queue_ids = []
        for url, destination in items:
            filename = destination.name
            try:
                if hasattr(self.db, 'add_to_download_queue'):
                    queue_id = self.db.add_to_download_queue(url, filename, str(destination.parent))
                    queue_ids.append(queue_id)
                else:
                    queue_ids.append(None)
            except:
                queue_ids.append(None)

        # Download all files
        tasks = []
        downloaded_files = []

        for i, ((url, destination), queue_id) in enumerate(zip(items, queue_ids)):
            def make_progress_cb(index, total):
                def cb(downloaded, total_size):
                    if progress_callback:
                        overall = ((index + (downloaded / total_size if total_size > 0 else 0)) / total) * 100
                        progress_callback(overall)
                return cb

            task = self.download_file(url, destination, make_progress_cb(i, len(items)), queue_id)
            tasks.append((url, destination, task))

        # Execute downloads
        results = await asyncio.gather(*[task for _, _, task in tasks], return_exceptions=True)

        for (url, destination, _), result in zip(tasks, results):
            if result is True and destination.exists():
                downloaded_files.append(destination)

        logger.info(f"Batch download complete: {len(downloaded_files)}/{len(items)} files")

        # Create ZIP if requested
        if create_zip and downloaded_files:
            if zip_filename is None:
                zip_filename = f"download_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"

            base_dir = downloaded_files[0].parent
            zip_path = base_dir / zip_filename
            await self._create_zip(downloaded_files, zip_path)

            # Optionally delete original files
            delete_after_zip = False
            try:
                if hasattr(self.db, 'get_config'):
                    delete_after_zip = self.db.get_config('delete_after_zip', 'false') == 'true'
            except:
                pass

            if delete_after_zip:
                for file in downloaded_files:
                    try:
                        file.unlink()
                    except Exception as e:
                        logger.error(f"Error deleting {file}: {e}")

            return [zip_path]

        return downloaded_files
    
    async def batch_download_to_zip(self, urls: List[str], zip_path: Path,
                                   progress_callback: Optional[Callable] = None) -> Optional[Path]:
        """
        Download multiple files and create ZIP archive
        
        Args:
            urls: List of URLs to download
            zip_path: Path for output ZIP file
            progress_callback: Optional callback for progress updates
            
        Returns:
            Path to created ZIP file or None if failed
        """
        destination_dir = zip_path.parent
        zip_filename = zip_path.name
        
        results = await self.batch_download(
            urls,
            destination_dir,
            create_zip=True,
            zip_filename=zip_filename,
            progress_callback=progress_callback
        )
        
        return results[0] if results and results[0].exists() else None
    
    async def _create_zip(self, file_paths: List[Path], zip_path: Path):
        """
        Create ZIP archive from files
        
        Args:
            file_paths: List of file paths to include
            zip_path: Output ZIP path
        """
        try:
            logger.info(f"Creating ZIP: {zip_path}")
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._create_zip_sync,
                file_paths,
                zip_path
            )
            
            logger.info(f"ZIP created: {zip_path} ({zip_path.stat().st_size} bytes)")
            
        except Exception as e:
            logger.error(f"Error creating ZIP: {e}")
    
    def _create_zip_sync(self, file_paths: List[Path], zip_path: Path):
        """
        Synchronous ZIP creation
        
        Args:
            file_paths: List of file paths
            zip_path: Output ZIP path
        """
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file_path in file_paths:
                if file_path.exists():
                    zf.write(file_path, file_path.name)
    
    async def download_with_cache(self, url: str, media_type: str = 'other',
                                 force_download: bool = False) -> Optional[Path]:
        """
        Download file with caching support
        
        Args:
            url: URL to download
            media_type: Type of media (video, image, gif, other)
            force_download: Force re-download even if cached
            
        Returns:
            Path to cached file or None
        """
        # Check cache
        if not force_download:
            cached = self.db.get_cached_media(url)
            if cached:
                cached_path = Path(cached['file_path'])
                if cached_path.exists():
                    logger.info(f"Cache hit: {url}")
                    return cached_path
        
        # Download to cache directory
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        filename = Path(url).name
        destination = self.cache_dir / filename
        
        success = await self.download_file(url, destination)
        
        if success and destination.exists():
            # Add to cache database
            self.db.cache_media(
                url=url,
                file_path=str(destination),
                media_type=media_type,
                file_size=destination.stat().st_size
            )

            # Enforce cache size limit (default 1000MB)
            try:
                if hasattr(self.db, 'enforce_cache_limit'):
                    cache_limit_raw = self.db.get_config('cache_size_limit_mb', '1000')
                    try:
                        cache_limit_mb = int(cache_limit_raw)
                    except (TypeError, ValueError):
                        cache_limit_mb = 1000
                    self.db.enforce_cache_limit(cache_limit_mb)
            except Exception as e:
                logger.warning(f"Error enforcing cache limit: {e}")

            return destination

        return None
    
    async def resume_pending_downloads(self):
        """Resume pending downloads from database"""
        pending = self.db.get_pending_downloads()
        
        if not pending:
            logger.info("No pending downloads to resume")
            return
        
        logger.info(f"Resuming {len(pending)} pending downloads")
        
        for download in pending:
            destination = Path(download['destination_path']) / download['filename']
            await self.download_file(
                download['url'],
                destination,
                queue_id=download['id']
            )
    
    def cancel_download(self, queue_id: int):
        """
        Cancel active download. The download will stop at the next chunk boundary.
        
        Args:
            queue_id: Queue ID to cancel
        """
        # Mark for cancellation (checked in download loop)
        self._cancelled_downloads.add(queue_id)
        
        # Update database status
        self.db.update_download_progress(queue_id, 0.0, 'cancelled')
        
        # Remove from active downloads
        if queue_id in self.active_downloads:
            del self.active_downloads[queue_id]
        
        logger.info(f"Download cancelled: {queue_id}")

    def pause_download(self, queue_id: int):
        """
        Pause an active download. Progress is saved and can be resumed later.
        
        Args:
            queue_id: Queue ID to pause
        """
        self._paused_downloads.add(queue_id)
        logger.info(f"Download pause requested: {queue_id}")

    def unpause_download(self, queue_id: int):
        """
        Remove pause flag (use resume_download to actually resume).
        
        Args:
            queue_id: Queue ID to unpause
        """
        self._paused_downloads.discard(queue_id)

    async def resume_download(self, queue_id: int) -> bool:
        """
        Resume a paused or failed download.
        
        Args:
            queue_id: Queue ID to resume
            
        Returns:
            True if resumed successfully, False otherwise
        """
        # Remove from paused set
        self._paused_downloads.discard(queue_id)
        self._cancelled_downloads.discard(queue_id)
        
        # Get download info from database
        if not hasattr(self.db, 'get_download_by_id'):
            logger.warning("Database doesn't support get_download_by_id")
            return False
        
        download = self.db.get_download_by_id(queue_id)
        if not download:
            logger.error(f"Download {queue_id} not found in database")
            return False
        
        if download['status'] not in ('paused', 'failed', 'downloading'):
            logger.warning(f"Download {queue_id} is not resumable (status: {download['status']})")
            return False
        
        destination = Path(download['destination_path']) / download['filename']
        logger.info(f"Resuming download {queue_id}: {download['url']} -> {destination}")
        
        return await self.download_file(
            download['url'],
            destination,
            queue_id=queue_id
        )

    async def download_file_resilient(
        self,
        url: str,
        destination: Path,
        progress_callback: Optional[Callable] = None,
        queue_id: Optional[int] = None,
        max_retries: Optional[int] = None,
    ) -> bool:
        """
        Download a file with automatic retry and resume on failure.
        
        This is a more resilient version of download_file that will:
        - Automatically retry on network errors with exponential backoff
        - Resume from the last successful byte on retry
        - Track progress persistently in the database
        
        Args:
            url: URL to download
            destination: Destination file path
            progress_callback: Optional callback for progress updates
            queue_id: Optional queue ID for database tracking
            max_retries: Maximum retry attempts (default: self._max_retries)
            
        Returns:
            True if successful, False otherwise
        """
        retries = max_retries if max_retries is not None else self._max_retries
        last_error = None
        
        for attempt in range(retries + 1):
            if attempt > 0:
                # Exponential backoff
                delay = self._retry_delay_base * (2 ** (attempt - 1))
                logger.info(f"Retry {attempt}/{retries} for {url} after {delay:.1f}s delay")
                await asyncio.sleep(delay)
            
            # Check if cancelled
            if queue_id and queue_id in self._cancelled_downloads:
                logger.info(f"Download cancelled before retry: {url}")
                return False
            
            try:
                success = await self.download_file(
                    url, destination, progress_callback, queue_id
                )
                if success:
                    return True
                    
                # Check if paused (not an error)
                if queue_id and queue_id in self._paused_downloads:
                    return False
                    
            except aiohttp.ClientError as e:
                last_error = f"Network error: {e}"
                logger.warning(f"Attempt {attempt + 1}/{retries + 1} failed: {last_error}")
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Attempt {attempt + 1}/{retries + 1} failed: {last_error}")
        
        # All retries exhausted
        logger.error(f"Download failed after {retries + 1} attempts: {url}")
        if queue_id and last_error:
            try:
                if hasattr(self.db, 'update_download_progress'):
                    self.db.update_download_progress(
                        queue_id, 0.0, 'failed', 
                        error=f"Failed after {retries + 1} attempts: {last_error}"
                    )
            except Exception:
                pass
        return False

    async def resume_all_downloads(self) -> dict:
        """
        Resume all paused and resumable failed downloads.
        
        Returns:
            Dictionary with counts of resumed, failed, and skipped downloads
        """
        results = {'resumed': 0, 'failed': 0, 'skipped': 0}
        
        if not hasattr(self.db, 'get_resumable_downloads'):
            # Fallback to get_pending_downloads
            downloads = self.db.get_pending_downloads()
        else:
            downloads = self.db.get_resumable_downloads()
        
        if not downloads:
            logger.info("No downloads to resume")
            return results
        
        logger.info(f"Attempting to resume {len(downloads)} downloads")
        
        for download in downloads:
            queue_id = download['id']
            
            # Skip if already being downloaded
            if queue_id in self.active_downloads:
                results['skipped'] += 1
                continue
            
            try:
                success = await self.resume_download(queue_id)
                if success:
                    results['resumed'] += 1
                else:
                    results['failed'] += 1
            except Exception as e:
                logger.error(f"Error resuming download {queue_id}: {e}")
                results['failed'] += 1
        
        logger.info(f"Resume results: {results}")
        return results
    
    def get_download_stats(self) -> dict:
        """
        Get download statistics
        
        Returns:
            Dictionary with download stats
        """
        pending = self.db.get_pending_downloads()
        
        stats = {
            'pending': len([d for d in pending if d['status'] == 'pending']),
            'downloading': len([d for d in pending if d['status'] == 'downloading']),
            'paused': len([d for d in pending if d['status'] == 'paused']),
            'completed': 0,  # Would need separate query
            'failed': 0  # Would need separate query
        }
        
        return stats
