from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import socket
import threading
import time
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import quote, urlparse, parse_qs, unquote

import aiohttp
from aiohttp import web, ClientTimeout, TCPConnector
from aiohttp.client_exceptions import ClientConnectionError, ServerTimeoutError
try:
    from aiohttp_socks import ProxyConnector
    SOCKS_SUPPORT = True
except ImportError:
    ProxyConnector = None
    SOCKS_SUPPORT = False

from src.core.http_client import MEDIA_HEADERS, get_media_headers_with_referer

logger = logging.getLogger(__name__)

# Log SOCKS support status at module load
if SOCKS_SUPPORT:
    logger.info("aiohttp-socks loaded - SOCKS5 proxy support enabled")
else:
    logger.warning("aiohttp-socks not installed - SOCKS5 proxies will not work")


class RangeProxy:
    def __init__(
        self,
        *,
        cache_dir: Optional[Path] = None,
        host: str = "127.0.0.1",
        port: int = 0,
        chunk_size: int = 8 * 1024 * 1024,
        allowed_hosts: Optional[set[str]] = None,
        max_cache_size_gb: float = 10.0,
        max_cache_age_days: int = 30,
        max_concurrent_chunks: int = 12,
        max_connections_per_host: int = 80,
        max_total_connections: int = 400,
        proxy_url: Optional[str] = None,
        proxy_pool: Optional[list[str]] = None,
    ):
        self._host = host
        self._port = int(port)
        self._chunk_size = max(256 * 1024, int(chunk_size))
        self._allowed_hosts = allowed_hosts
        self._proxy_url = proxy_url
        self._proxy_pool = proxy_pool or []  # List of proxy URLs for parallel downloads
        self._cache_dir = Path(
            cache_dir
            if cache_dir is not None
            else Path.home() / ".coomer-betterui" / "range_cache"
        )
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._max_cache_size_bytes = int(max_cache_size_gb * 1024 * 1024 * 1024)
        self._max_cache_age_seconds = max_cache_age_days * 24 * 60 * 60
        self._max_concurrent_chunks = max(1, int(max_concurrent_chunks))
        self._max_connections_per_host = max(1, int(max_connections_per_host))
        self._max_total_connections = max(10, int(max_total_connections))
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._started = threading.Event()
        self._start_error: Optional[Exception] = None
        self._key_lock: Optional[asyncio.Lock] = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_last_used: dict[str, float] = {}  # Track when locks were last used
        self._hash_re = re.compile(r"^[0-9a-f]{32,128}$")
        self._search_hash_timeout_s = 8
        # Semaphore for limiting concurrent chunk downloads per stream
        self._chunk_semaphore: Optional[asyncio.Semaphore] = None
        # Background prefetch tracking
        self._prefetch_tasks: dict[str, asyncio.Task] = {}  # key -> prefetch task
        self._prefetch_enabled = True
        # Proxy usage statistics
        self._proxy_usage_stats: dict[str, int] = {}  # proxy_url -> request count
        # Metrics
        self._cache_hits = 0
        self._cache_misses = 0
        self._total_requests = 0
        self._errors = 0
        self._resolved_urls: dict[str, str] = {}
        self._resolved_url_logged: set[str] = set()

        # Seek detection and prefetch abandonment (SOCKS5-friendly)
        self._last_request_position: dict[str, int] = {}  # key -> last byte position
        self._seek_threshold = 2 * self._chunk_size  # 16MB jump = seek
        self._abandoned_prefetches: set[str] = set()  # keys with abandoned prefetch
        self._priority_chunks: dict[str, int] = {}  # key -> chunk index that needs priority (post-seek)

        logger.info(f"RangeProxy __init__ - proxy_url: {proxy_url}, proxy_pool: {proxy_pool}")

    def proxy_url(self, url: str) -> str:
        if not url:
            raise ValueError("Missing URL for range proxy")
        self.start()
        return f"http://{self._host}:{self._port}/proxy?url={quote(url, safe='')}"

    def start(self, timeout_s: float = 3.0) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="range-proxy", daemon=True)
        self._thread.start()
        if not self._started.wait(timeout_s):
            raise RuntimeError("Range proxy failed to start (timeout)")
        if self._start_error:
            raise self._start_error

    def stop(self) -> None:
        loop = self._loop
        if not loop:
            return
        loop.call_soon_threadsafe(loop.stop)

    def get_metrics(self) -> dict:
        """Get cache and request metrics."""
        metrics = {
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "total_requests": self._total_requests,
            "errors": self._errors,
            "hit_rate": (
                self._cache_hits / (self._cache_hits + self._cache_misses)
                if (self._cache_hits + self._cache_misses) > 0
                else 0.0
            ),
            "active_locks": len(self._locks),
        }

        # Add connection pool stats if available
        if self._session and hasattr(self._session, "connector"):
            connector = self._session.connector
            if hasattr(connector, "_acquired"):
                metrics["active_connections"] = len(connector._acquired)
            if hasattr(connector, "_acquired_per_host"):
                metrics["connections_per_host"] = {
                    str(host): len(conns)
                    for host, conns in connector._acquired_per_host.items()
                }

        return metrics

    def get_cache_size(self) -> int:
        """
        Get total size of the cache in bytes.

        Returns:
            Total cache size in bytes.
        """
        if not self._cache_dir.exists():
            return 0

        total_size = 0
        try:
            for shard_dir in self._cache_dir.iterdir():
                if not shard_dir.is_dir():
                    continue
                for entry_dir in shard_dir.iterdir():
                    if not entry_dir.is_dir():
                        continue
                    total_size += self._get_entry_size(entry_dir)
        except Exception as e:
            logger.warning(f"Failed to calculate cache size: {e}")

        return total_size

    def clear_cache(self) -> tuple[int, int]:
        """
        Clear all cache entries.

        Returns:
            Tuple of (files_removed, bytes_freed).
        """
        import shutil

        if not self._cache_dir.exists():
            return 0, 0

        total_size = self.get_cache_size()
        files_removed = 0

        try:
            for shard_dir in self._cache_dir.iterdir():
                if shard_dir.is_dir():
                    for entry_dir in shard_dir.iterdir():
                        if entry_dir.is_dir():
                            files_removed += 1
            shutil.rmtree(self._cache_dir, ignore_errors=True)
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"Failed to clear cache: {e}")

        return files_removed, total_size

    def cleanup_cache(self) -> tuple[int, int]:
        """
        Clean up old and oversized cache entries.
        Returns (files_removed, bytes_freed).
        """
        if not self._cache_dir.exists():
            return 0, 0

        now = time.time()
        files_removed = 0
        bytes_freed = 0
        entries_by_age = []

        # Scan all cache entries
        try:
            for shard_dir in self._cache_dir.iterdir():
                if not shard_dir.is_dir():
                    continue
                for entry_dir in shard_dir.iterdir():
                    if not entry_dir.is_dir():
                        continue

                    meta_path = entry_dir / "meta.json"
                    if not meta_path.exists():
                        continue

                    try:
                        stat = meta_path.stat()
                        mtime = stat.st_mtime
                        entry_size = self._get_entry_size(entry_dir)
                        entries_by_age.append((mtime, entry_dir, entry_size))
                    except Exception as e:
                        logger.debug(f"Failed to stat entry {entry_dir.name}: {e}")
        except Exception as e:
            logger.warning(f"Failed to scan cache directory: {e}")
            return 0, 0

        # Sort by age (oldest first)
        entries_by_age.sort(key=lambda x: x[0])

        # Remove entries older than max age
        age_cutoff = now - self._max_cache_age_seconds
        for mtime, entry_dir, size in entries_by_age[:]:
            if mtime < age_cutoff:
                try:
                    self._remove_entry(entry_dir)
                    files_removed += 1
                    bytes_freed += size
                    entries_by_age.remove((mtime, entry_dir, size))
                except Exception as e:
                    logger.warning(f"Failed to remove old entry {entry_dir.name}: {e}")

        # Check total cache size and remove oldest entries if needed
        total_size = sum(size for _, _, size in entries_by_age)
        while total_size > self._max_cache_size_bytes and entries_by_age:
            mtime, entry_dir, size = entries_by_age.pop(0)
            try:
                self._remove_entry(entry_dir)
                files_removed += 1
                bytes_freed += size
                total_size -= size
            except Exception as e:
                logger.warning(f"Failed to remove oversized entry {entry_dir.name}: {e}")
                break

        if files_removed > 0:
            logger.info(
                f"Cache cleanup: removed {files_removed} entries, "
                f"freed {bytes_freed / (1024 * 1024):.2f} MB"
            )

        return files_removed, bytes_freed

    def _get_entry_size(self, entry_dir: Path) -> int:
        """Calculate total size of a cache entry in bytes."""
        total = 0
        try:
            for item in entry_dir.rglob("*"):
                if item.is_file():
                    try:
                        total += item.stat().st_size
                    except Exception:
                        pass
        except Exception:
            pass
        return total

    def _remove_entry(self, entry_dir: Path) -> None:
        """Remove a cache entry directory and all its contents."""
        import shutil

        shutil.rmtree(entry_dir, ignore_errors=True)

    @staticmethod
    def _error_response(
        status: int,
        error_code: str,
        user_message: str,
        details: Optional[str] = None,
    ) -> web.Response:
        """Create a normalized error response following Core API Contract."""
        error_body = {
            "error": error_code,
            "user_message": user_message,
        }
        if details:
            error_body["details"] = details

        return web.Response(
            status=status,
            text=json.dumps(error_body),
            content_type="application/json",
        )

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._start_async())
        except Exception as exc:
            self._start_error = exc
        finally:
            self._started.set()
        if self._start_error:
            return
        try:
            self._loop.run_forever()
        finally:
            try:
                self._loop.run_until_complete(self._shutdown_async())
            except Exception as e:
                logger.warning(f"Error during proxy shutdown: {e}")

    async def _start_async(self) -> None:
        self._key_lock = asyncio.Lock()
        # Initialize semaphore for limiting concurrent chunk downloads
        self._chunk_semaphore = asyncio.Semaphore(self._max_concurrent_chunks)

        # Configure connection limits and timeouts
        # Check if any proxy in pool or single proxy is SOCKS
        proxies_to_check = self._proxy_pool if self._proxy_pool else ([self._proxy_url] if self._proxy_url else [])
        uses_socks = any(p and p.startswith('socks') for p in proxies_to_check)
        
        if uses_socks and SOCKS_SUPPORT and self._proxy_url and not self._proxy_pool:
            # Single SOCKS proxy - use ProxyConnector
            connector = ProxyConnector.from_url(
                self._proxy_url,
                limit=self._max_total_connections,
                limit_per_host=self._max_connections_per_host,
                ttl_dns_cache=300,
                rdns=False,
                family=socket.AF_INET,
                force_close=False,
                enable_cleanup_closed=True,
                keepalive_timeout=60,
            )
            logger.info(f"RangeProxy using SOCKS proxy: {self._proxy_url}")
        elif uses_socks and not SOCKS_SUPPORT:
            logger.warning("SOCKS proxy configured but aiohttp-socks not installed. Install via: pip install aiohttp-socks")
            # Fallback to regular connector
            connector = TCPConnector(
                limit=self._max_total_connections,
                limit_per_host=self._max_connections_per_host,
                ttl_dns_cache=300,
                family=socket.AF_INET,
                force_close=False,
                enable_cleanup_closed=True,
                keepalive_timeout=60,
            )
        else:
            # HTTP proxy or no proxy - use regular TCPConnector
            # Note: For proxy pools, we pass proxy per-request instead of per-session
            connector = TCPConnector(
                limit=self._max_total_connections,
                limit_per_host=self._max_connections_per_host,
                ttl_dns_cache=300,
                family=socket.AF_INET,
                force_close=False,
                enable_cleanup_closed=True,
                keepalive_timeout=60,
            )

        timeout = ClientTimeout(
            total=600,  # 10 minutes total timeout (increased for VPN)
            connect=20,  # 20 seconds to establish connection (increased for VPN)
            sock_read=60,  # 60 seconds between reads (increased for VPN)
        )

        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers=MEDIA_HEADERS,
            raise_for_status=False,
        )

        app = web.Application()
        app.router.add_get("/proxy", self._handle_proxy)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()
        if self._site and self._site._server and self._site._server.sockets:
            sock = self._site._server.sockets[0]
            self._port = int(sock.getsockname()[1])
        self._started.set()

        # Run initial cache cleanup in background
        asyncio.create_task(self._cleanup_cache_background())

        # Run periodic lock cleanup
        asyncio.create_task(self._cleanup_locks_periodic())
        
        # Run periodic prefetch task cleanup
        asyncio.create_task(self._cleanup_prefetch_tasks_periodic())

    async def _cleanup_cache_background(self) -> None:
        """Run cache cleanup in a background thread to avoid blocking."""
        try:
            await asyncio.to_thread(self.cleanup_cache)
        except Exception as e:
            logger.warning(f"Background cache cleanup failed: {e}")

    async def _cleanup_locks_periodic(self) -> None:
        """Periodically clean up locks that haven't been used in a while."""
        while True:
            try:
                await asyncio.sleep(300)  # Run every 5 minutes
                await self._cleanup_old_locks()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Lock cleanup failed: {e}")

    async def _cleanup_old_locks(self) -> None:
        """Remove locks that haven't been used in over 10 minutes."""
        if not self._key_lock:
            return

        now = time.time()
        max_age = 600  # 10 minutes
        keys_to_remove = []

        async with self._key_lock:
            for key, last_used in list(self._lock_last_used.items()):
                if now - last_used > max_age:
                    # Only remove if lock is not currently held
                    lock = self._locks.get(key)
                    if lock and not lock.locked():
                        keys_to_remove.append(key)

            for key in keys_to_remove:
                self._locks.pop(key, None)
                self._lock_last_used.pop(key, None)

            if keys_to_remove:
                logger.debug(f"Cleaned up {len(keys_to_remove)} unused locks")

    async def _cleanup_prefetch_tasks_periodic(self) -> None:
        """Periodically clean up completed prefetch tasks."""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                completed = [key for key, task in list(self._prefetch_tasks.items()) if task.done()]
                for key in completed:
                    task = self._prefetch_tasks.pop(key, None)
                    if task and not task.cancelled():
                        try:
                            await task  # Retrieve any exceptions
                        except Exception as e:
                            logger.debug(f"Prefetch task for {key[:8]} completed with error: {e}")
                if completed:
                    logger.debug(f"Cleaned up {len(completed)} completed prefetch tasks")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Prefetch task cleanup failed: {e}")

    async def _prefetch_remaining_chunks(self, url: str, start_chunk: int, total_size: int) -> None:
        """Background task to prefetch remaining chunks of a video."""
        key = self._key(url)
        total_chunks = (total_size + self._chunk_size - 1) // self._chunk_size

        logger.debug(f"[PREFETCH] Starting for {url[:50]}")
        logger.debug(f"[PREFETCH] Total chunks: {total_chunks}, starting from: {start_chunk}")
        
        # Determine batch size based on proxy availability
        # CDNs often rate-limit parallel connections from same IP, so use sequential for direct
        if self._proxy_pool:
            batch_size = min(len(self._proxy_pool), 3)  # Max 3 parallel per batch
            logger.debug(f"[PREFETCH] Using {len(self._proxy_pool)} proxies: {[p.split('//')[-1] for p in self._proxy_pool]}")
            logger.debug(f"[PREFETCH] Parallel batch size: {batch_size}")
        elif self._proxy_url:
            batch_size = 1  # Single proxy = sequential
            logger.debug(f"[PREFETCH] Using single proxy: {self._proxy_url.split('//')[-1]}")
            logger.debug(f"[PREFETCH] Sequential mode (single proxy)")
        else:
            batch_size = 1  # Direct connection = sequential to avoid CDN rate limiting
            logger.debug(f"[PREFETCH] Using direct connection (no proxies)")
            logger.debug(f"[PREFETCH] Sequential mode (direct connection avoids CDN rate limiting)")

        prefetched_count = 0
        for batch_start in range(start_chunk, total_chunks, batch_size):
            # Check for abandonment before starting new batch (user seeked away)
            if key in self._abandoned_prefetches:
                logger.debug(f"[PREFETCH] Abandoned at batch {batch_start} (user seeked)")
                self._abandoned_prefetches.discard(key)
                return

            batch_end = min(batch_start + batch_size, total_chunks)

            if batch_size > 1:
                logger.debug(f"[PREFETCH] Batch: chunks {batch_start}-{batch_end-1}")
            else:
                logger.debug(f"[PREFETCH] Fetching chunk {batch_start}")

            # Fetch batch in parallel (or single if batch_size=1)
            tasks = []
            for idx in range(batch_start, batch_end):
                # Skip if already cached
                chunk_path = self._chunk_path(key, idx)
                if chunk_path.exists():
                    logger.debug(f"[PREFETCH] Chunk {idx} already cached, skipping")
                    continue

                # Create fetch task
                task = asyncio.create_task(self._get_chunk(url, idx, total_size))
                tasks.append((idx, task))

            # Wait for batch to complete
            if tasks:
                for idx, task in tasks:
                    # Check abandonment between chunk awaits
                    if key in self._abandoned_prefetches:
                        logger.debug(f"[PREFETCH] Abandoned during batch (user seeked)")
                        self._abandoned_prefetches.discard(key)
                        return
                    try:
                        chunk = await task
                        if chunk:
                            prefetched_count += 1
                            logger.debug(f"[PREFETCH] ✓ Chunk {idx}/{total_chunks-1} ({len(chunk)} bytes)")
                        else:
                            logger.warning(f"[PREFETCH] ✗ Chunk {idx} fetch returned None")
                    except Exception as e:
                        logger.warning(f"[PREFETCH] ✗ Chunk {idx} error: {e}")

                # Small delay between batches (only meaningful for parallel batches)
                if batch_size > 1:
                    await asyncio.sleep(0.3)

        logger.debug(f"[PREFETCH] Completed: {prefetched_count} chunks fetched for {url[:50]}")
        if self._proxy_usage_stats:
            logger.debug(f"[PREFETCH] Final proxy usage: {self._proxy_usage_stats}")

    async def _shutdown_async(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        if self._session:
            await self._session.close()

    async def _handle_proxy(self, request: web.Request) -> web.StreamResponse:
        self._total_requests += 1

        if self._session is None:
            self._errors += 1
            return self._error_response(
                503,
                "PROXY_NOT_READY",
                "The proxy service is not ready yet. Please try again.",
            )
        url = request.query.get("url")
        if not url:
            return self._error_response(
                400,
                "MISSING_URL",
                "No URL specified for proxying.",
            )
        
        range_header = request.headers.get("Range")
        logger.info(f"RangeProxy request #{self._total_requests}: {range_header or 'full'} for {url}")
        logger.debug(f"Full URL length: {len(url)} chars")
        
        # Log proxy pool status
        if self._proxy_pool:
            logger.info(f"Proxy pool active: {len(self._proxy_pool)} proxies")
        elif self._proxy_url:
            logger.info(f"Single proxy: {self._proxy_url.split('//')[-1]}")
        else:
            logger.debug(f"Direct connection (no proxy)")
        
        # Log usage stats every 5 requests
        if self._total_requests % 5 == 0 and self._proxy_usage_stats:
            logger.info(f"Connection stats: {dict(sorted(self._proxy_usage_stats.items()))}")
        
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return self._error_response(
                400,
                "UNSUPPORTED_SCHEME",
                "Only HTTP and HTTPS URLs are supported.",
                details=f"Provided scheme: {parsed.scheme}",
            )

        # Validate coomer/kemono URL structure to catch truncated paths early
        if parsed.hostname and ("coomer" in parsed.hostname or "kemono" in parsed.hostname):
            path_parts = parsed.path.split("/")
            # Expected: /data/XX/YY/HASH.EXT - at least 5 parts when split
            if len(path_parts) >= 5 and path_parts[1] == "data":
                filename = path_parts[-1]  # Should be HASH.EXT
                # Check if filename has extension
                if "." not in filename:
                    logger.warning(f"URL appears truncated - no file extension: {url}")
                # Check hash length (should be ~64 chars for SHA256 before extension)
                file_stem = filename.rsplit(".", 1)[0] if "." in filename else filename
                if len(file_stem) < 60:  # SHA256 is 64 chars, allow some margin
                    logger.warning(f"URL hash appears truncated ({len(file_stem)} chars): {url}")

        if self._allowed_hosts and parsed.hostname not in self._allowed_hosts:
            return self._error_response(
                403,
                "FORBIDDEN_HOST",
                "This content source is not permitted.",
                details=f"Host '{parsed.hostname}' is not in the allowed list.",
            )

        range_header = request.headers.get("Range")
        total_size, content_type, resolved_url = await self._ensure_meta(url)
        self._cache_resolved_url(url, resolved_url)
        start, end = self._parse_range(range_header, total_size)
        if range_header and start is None:
            return await self._stream_passthrough_range(request, url, range_header)

        if start is None:
            return await self._stream_full(request, url, content_type, total_size)

        if end is None and total_size is None and range_header:
            return await self._stream_passthrough_range(request, url, range_header)

        if total_size is not None:
            end = total_size - 1 if end is None else min(end, total_size - 1)
            if start >= total_size:
                return self._error_response(
                    416,
                    "RANGE_OUT_OF_BOUNDS",
                    "The requested byte range is outside the file size.",
                    details=f"Requested start: {start}, File size: {total_size}",
                )
            
            # Start background prefetch if enabled
            if self._prefetch_enabled and total_size:
                key = self._key(url)
                current_chunk = start // self._chunk_size
                next_chunk = current_chunk + 1
                total_chunks = (total_size + self._chunk_size - 1) // self._chunk_size

                # Detect seek: large jump from last request position
                last_pos = self._last_request_position.get(key, 0)
                is_seek = abs(start - last_pos) > self._seek_threshold
                self._last_request_position[key] = start

                # On seek: abandon old prefetch and mark first chunk as priority
                if is_seek:
                    # Mark first chunk at new position as priority (gets racing strategy)
                    self._priority_chunks[key] = current_chunk
                    logger.debug(f"[SEEK] Detected seek from {last_pos} to {start} ({abs(start - last_pos)} bytes) - chunk {current_chunk} marked priority")

                    if key in self._prefetch_tasks and not self._prefetch_tasks[key].done():
                        self._abandoned_prefetches.add(key)
                        logger.debug(f"[SEEK] Abandoning old prefetch (SOCKS5-friendly)")

                # In direct connection mode (no proxy pool), don't start prefetch until 
                # chunk 0 is cached - prevents parallel downloads that trigger CDN rate limiting
                chunk_0_path = self._chunk_path(key, 0)
                direct_mode = not self._proxy_pool and not self._proxy_url
                if direct_mode and not chunk_0_path.exists():
                    logger.debug(f"[PREFETCH] Deferred - waiting for chunk 0 to cache (direct connection mode)")
                else:
                    # Start new prefetch if not already running (or was abandoned)
                    should_start = (
                        key not in self._prefetch_tasks or
                        self._prefetch_tasks[key].done() or
                        key in self._abandoned_prefetches
                    )

                    if should_start and next_chunk < total_chunks:
                        # Clear abandonment flag before starting new prefetch
                        self._abandoned_prefetches.discard(key)
                        logger.debug(f"[PREFETCH] Initiating background fetch from chunk {next_chunk}/{total_chunks-1}")
                        self._prefetch_tasks[key] = asyncio.create_task(
                            self._prefetch_remaining_chunks(url, next_chunk, total_size)
                        )
                    elif next_chunk >= total_chunks:
                        logger.debug(f"[PREFETCH] No prefetch needed - already at last chunk ({current_chunk}/{total_chunks-1})")

        return await self._stream_range(request, url, start, end, total_size, content_type)

    async def _stream_full(
        self,
        request: web.Request,
        url: str,
        content_type: Optional[str],
        total_size: Optional[int],
    ) -> web.StreamResponse:
        effective_url = self._effective_url(url)
        
        # Build request headers with Referer for anti-hotlinking CDNs
        request_headers = get_media_headers_with_referer(effective_url)
        
        response_headers = {}
        if content_type:
            response_headers["Content-Type"] = content_type
        if total_size is not None:
            response_headers["Content-Length"] = str(total_size)
        response_headers["Accept-Ranges"] = "bytes"

        resp = web.StreamResponse(status=200, headers=response_headers)
        await resp.prepare(request)

        try:
            async with self._session.get(effective_url, headers=request_headers, proxy=self._proxy_url) as origin:
                async for chunk in origin.content.iter_chunked(self._chunk_size):
                    # Check if client is still connected before writing
                    if request.transport is None or request.transport.is_closing():
                        logger.debug(f"Client disconnected during full stream for {url[:50]}")
                        break
                    await resp.write(chunk)
            await resp.write_eof()
        except (ClientConnectionError, ConnectionResetError, BrokenPipeError) as e:
            # Client disconnected (e.g., video player seeking or closing)
            logger.debug(f"Client connection error during full stream: {e}")
        except (asyncio.CancelledError, ServerTimeoutError) as e:
            logger.debug(f"Request cancelled or timed out: {e}")
            raise
        except Exception as e:
            logger.warning(f"Unexpected error during full stream: {e}")
            self._errors += 1
        return resp

    async def _stream_range(
        self,
        request: web.Request,
        url: str,
        start: int,
        end: Optional[int],
        total_size: Optional[int],
        content_type: Optional[str],
    ) -> web.StreamResponse:
        if end is None and total_size is None:
            return await self._stream_passthrough_range(
                request,
                url,
                f"bytes={start}-",
            )

        last = end if end is not None else start
        length = last - start + 1 if end is not None else None

        headers = {
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{last}/{total_size or '*'}",
        }
        if content_type:
            headers["Content-Type"] = content_type
        if length is not None:
            headers["Content-Length"] = str(length)

        resp = web.StreamResponse(status=206, headers=headers)
        await resp.prepare(request)
        logger.debug(f"[STREAM] Starting range stream for {url[:50]}: bytes {start}-{last}")

        try:
            key = self._key(url)
            first_chunk = start // self._chunk_size
            last_chunk = last // self._chunk_size
            total_chunks = (total_size + self._chunk_size - 1) // self._chunk_size if total_size else None
            direct_mode = not self._proxy_pool and not self._proxy_url
            
            logger.debug(f"[STREAM] Need chunks {first_chunk}-{last_chunk} for range {start}-{last}")
            for idx in range(first_chunk, last_chunk + 1):
                # Check if client is still connected before fetching next chunk
                if request.transport is None or request.transport.is_closing():
                    logger.debug(f"Client disconnected during range stream at chunk {idx}")
                    break

                logger.debug(f"[STREAM] Fetching chunk {idx}...")
                chunk = await self._get_chunk(url, idx, total_size)
                
                # In direct mode, start prefetch AFTER chunk 0 completes to avoid parallel downloads
                if idx == 0 and direct_mode and self._prefetch_enabled and total_chunks and total_chunks > 1:
                    should_start = (
                        key not in self._prefetch_tasks or
                        self._prefetch_tasks[key].done()
                    )
                    if should_start:
                        logger.debug(f"[STREAM] Chunk 0 complete - starting sequential prefetch for remaining {total_chunks - 1} chunks")
                        self._prefetch_tasks[key] = asyncio.create_task(
                            self._prefetch_remaining_chunks(url, 1, total_size)
                        )
                
                if chunk is None:
                    # Chunk fetch failed after all retries - try direct connection as last resort
                    logger.warning(f"Chunk {idx} fetch failed for {url[:50]}, trying direct connection")
                    remaining_start = idx * self._chunk_size
                    remaining_end = last
                    # Build fallback headers with Referer
                    fallback_headers = get_media_headers_with_referer(url)
                    fallback_headers["Range"] = f"bytes={remaining_start}-{remaining_end}"
                    try:
                        # Direct fetch without proxy
                        async with self._session.get(url, headers=fallback_headers) as direct_resp:
                            if direct_resp.status == 206:
                                fallback_data = await direct_resp.read()
                                slice_start = max(start, remaining_start) - remaining_start
                                await resp.write(fallback_data[slice_start:])
                                logger.debug(f"Direct fallback succeeded for chunk {idx}")
                                break
                            else:
                                logger.debug(f"Direct fallback returned HTTP {direct_resp.status}")
                                break
                    except Exception as e:
                        logger.debug(f"Direct fallback error for chunk {idx}: {e}")
                        break

                chunk_start = idx * self._chunk_size
                chunk_end = chunk_start + len(chunk) - 1
                slice_start = max(start, chunk_start) - chunk_start
                slice_end = min(last, chunk_end) - chunk_start
                if slice_end >= slice_start:
                    bytes_to_write = slice_end - slice_start + 1
                    logger.debug(f"[STREAM] Writing chunk {idx} slice [{slice_start}:{slice_end+1}] = {bytes_to_write} bytes")
                    await resp.write(chunk[slice_start:slice_end + 1])

            logger.debug(f"[STREAM] Range stream complete for {url[:50]}")
            await resp.write_eof()
        except (ClientConnectionError, ConnectionResetError, BrokenPipeError) as e:
            # Client disconnected (e.g., video player seeking or closing)
            logger.debug(f"Client connection error during range stream: {e}")
        except (asyncio.CancelledError, ServerTimeoutError) as e:
            logger.debug(f"Request cancelled or timed out: {e}")
            raise
        except Exception as e:
            logger.warning(f"Unexpected error during range stream: {e}")
            self._errors += 1
        return resp

    async def _stream_passthrough_range(
        self,
        request: web.Request,
        url: str,
        range_header: str,
    ) -> web.StreamResponse:
        effective_url = self._effective_url(url)
        # Build headers with Referer for anti-hotlinking CDNs
        request_headers = get_media_headers_with_referer(effective_url)
        request_headers["Range"] = range_header
        async with self._session.get(effective_url, headers=request_headers, proxy=self._proxy_url) as origin:
            content_type = origin.headers.get("Content-Type")
            content_range = origin.headers.get("Content-Range")
            content_length = origin.headers.get("Content-Length")
            accept_ranges = origin.headers.get("Accept-Ranges")
            total_size = self._total_size_from_content_range(content_range)
            if total_size is not None or content_type:
                await self._update_meta(url, total_size, content_type, self._resolved_urls.get(url))

            resp_headers = {}
            if content_type:
                resp_headers["Content-Type"] = content_type
            if content_range:
                resp_headers["Content-Range"] = content_range
            if content_length:
                resp_headers["Content-Length"] = content_length
            resp_headers["Accept-Ranges"] = accept_ranges or "bytes"

            resp = web.StreamResponse(status=origin.status, headers=resp_headers)
            await resp.prepare(request)
            try:
                async for chunk in origin.content.iter_chunked(self._chunk_size):
                    # Check if client is still connected before writing
                    if request.transport is None or request.transport.is_closing():
                        logger.debug(f"Client disconnected during passthrough range for {url[:50]}")
                        return resp
                    await resp.write(chunk)
            except (ClientConnectionError, ConnectionResetError, BrokenPipeError) as e:
                # Client disconnected (e.g., video player seeking or closing)
                logger.debug(f"Client connection error during passthrough: {e}")
                return resp
            except (asyncio.CancelledError, ServerTimeoutError) as e:
                logger.debug(f"Request cancelled or timed out during passthrough: {e}")
                raise
        try:
            await resp.write_eof()
        except (ClientConnectionError, ConnectionResetError, BrokenPipeError):
            pass
        return resp

    async def _get_chunk(
        self,
        url: str,
        index: int,
        total_size: Optional[int],
    ) -> Optional[bytes]:
        key = self._key(url)
        path = self._chunk_path(key, index)
        if path.exists():
            try:
                data = await asyncio.to_thread(path.read_bytes)
                self._cache_hits += 1
                logger.debug(f"[CHUNK] Chunk {index} cache HIT - {len(data)} bytes from disk")
                return data
            except Exception as e:
                logger.warning(f"Failed to read cached chunk {index} for {key[:8]}: {e}")

        self._cache_misses += 1
        # Lock per-chunk (not per-URL) to allow parallel chunk downloads
        chunk_lock_key = f"{key}_{index}"
        lock = await self._get_lock(chunk_lock_key)

        # Use semaphore to limit concurrent chunk downloads
        if self._chunk_semaphore:
            async with self._chunk_semaphore:
                return await self._fetch_chunk_with_lock(lock, path, url, index, total_size, key)
        else:
            return await self._fetch_chunk_with_lock(lock, path, url, index, total_size, key)
    
    async def _fetch_chunk_with_lock(
        self,
        lock: asyncio.Lock,
        path: Path,
        url: str,
        index: int,
        total_size: Optional[int],
        key: str,
    ) -> Optional[bytes]:
        async with lock:
            if path.exists():
                try:
                    logger.debug(f"Chunk {index} cache HIT for {key[:8]}")
                    self._cache_hits += 1
                    return await asyncio.to_thread(path.read_bytes)
                except Exception as e:
                    logger.warning(f"Failed to read cached chunk {index} for {key[:8]}: {e}")
                    return None
            
            # Cache miss - need to fetch
            self._cache_misses += 1
            logger.info(f"Chunk {index} cache MISS for {key[:8]} - fetching from origin")

            chunk_start = index * self._chunk_size
            chunk_end = chunk_start + self._chunk_size - 1
            if total_size is not None:
                chunk_end = min(chunk_end, total_size - 1)
                if chunk_start >= total_size:
                    return None
            headers = {"Range": f"bytes={chunk_start}-{chunk_end}"}
            logger.debug(f"Fetching chunk {index}: Range bytes={chunk_start}-{chunk_end}")

            # Critical chunks use racing - try ALL proxies in parallel
            # Critical = chunk 0-1 (start of video) OR first chunk after seek
            is_priority = self._priority_chunks.get(key) == index
            is_critical = (index <= 1 or is_priority) and self._proxy_pool and len(self._proxy_pool) >= 2

            if is_priority:
                # Clear priority flag after use
                self._priority_chunks.pop(key, None)
                if is_critical:
                    logger.info(f"[SEEK-PRIORITY] Chunk {index} is first after seek - using racing strategy")
                else:
                    logger.info(f"[SEEK-PRIORITY] Chunk {index} is first after seek - using direct fetch (no proxy pool)")
            elif index <= 1:
                if is_critical:
                    logger.info(f"[CRITICAL] Chunk {index} is critical for playback - using racing strategy")
                else:
                    logger.info(f"[CRITICAL] Chunk {index} is critical for playback - using direct fetch (no proxy pool)")

            if is_critical:
                data = await self._fetch_range_racing(url, headers, index)
            else:
                # Try with retries using different proxies
                max_retries = len(self._proxy_pool) if self._proxy_pool else 2
                data = None
                for attempt in range(max_retries):
                    # Use different proxy for each retry attempt
                    retry_chunk_index = index + attempt  # Offset to get different proxy
                    data = await self._fetch_range(url, headers, chunk_index=retry_chunk_index)
                    if data is not None:
                        if attempt > 0:
                            logger.info(f"Chunk {index} succeeded on retry {attempt} with different proxy")
                        break
                    if attempt < max_retries - 1:
                        logger.warning(f"Chunk {index} fetch failed, retrying with different proxy ({attempt + 1}/{max_retries})")
                        await asyncio.sleep(0.5)  # Brief delay before retry

            if data is None:
                logger.error(f"Chunk {index} failed after all attempts")
                return None
            try:
                await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
                await asyncio.to_thread(path.write_bytes, data)
            except Exception as e:
                logger.warning(f"Failed to cache chunk {index} for {key[:8]}: {e}")
                return data
            return data

    def _select_proxy(self, chunk_index: int) -> Optional[str]:
        """Select proxy based on chunk index to distribute load across proxy pool."""
        if self._proxy_pool:
            # Distribute chunks evenly across available proxies
            proxy = self._proxy_pool[chunk_index % len(self._proxy_pool)]
            # Track usage
            self._proxy_usage_stats[proxy] = self._proxy_usage_stats.get(proxy, 0) + 1
            logger.info(f"Chunk {chunk_index} → {proxy} (uses: {self._proxy_usage_stats[proxy]})")
            return proxy
        elif self._proxy_url:
            self._proxy_usage_stats[self._proxy_url] = self._proxy_usage_stats.get(self._proxy_url, 0) + 1
            logger.info(f"Chunk {chunk_index} → {self._proxy_url} (uses: {self._proxy_usage_stats[self._proxy_url]})")
            return self._proxy_url
        else:
            self._proxy_usage_stats['direct'] = self._proxy_usage_stats.get('direct', 0) + 1
            if chunk_index % 5 == 0:  # Log every 5th chunk to avoid spam
                logger.info(f"Chunk {chunk_index} → direct connection (total direct: {self._proxy_usage_stats['direct']})")
            return None

    async def _fetch_range_racing(self, url: str, headers: dict, chunk_index: int) -> Optional[bytes]:
        """
        Race ALL proxies AND direct connection in parallel for critical chunks.
        Returns data from the first one that succeeds.
        """
        effective_url = self._effective_url(url)
        range_header = headers.get("Range")
        range_start, range_end = self._parse_range_header(range_header)
        if not self._proxy_pool or len(self._proxy_pool) < 2:
            # No pool or single proxy - use normal fetch
            return await self._fetch_range(url, headers, chunk_index)

        logger.info(f"[RACE] Chunk {chunk_index} - racing {len(self._proxy_pool)} proxies + direct")
        
        # Build headers with Referer for anti-hotlinking CDNs
        request_headers = get_media_headers_with_referer(effective_url)
        request_headers.update(headers)  # Merge in Range header etc.

        async def try_proxy(proxy: Optional[str], proxy_idx: int) -> tuple[int, Optional[bytes], str]:
            """Try fetching via a specific proxy (or direct if proxy is None)."""
            source = proxy.split('//')[-1] if proxy else "direct"
            try:
                if proxy and proxy.startswith('socks') and SOCKS_SUPPORT:
                    connector = ProxyConnector.from_url(
                        proxy,
                        ttl_dns_cache=300,
                        force_close=False,
                        rdns=False,
                        family=socket.AF_INET,
                    )
                    # Aggressive timeout for racing - fail fast
                    timeout = ClientTimeout(total=30, connect=8, sock_read=20)
                    async with aiohttp.ClientSession(
                        connector=connector,
                        timeout=timeout,
                        headers=request_headers,
                        raise_for_status=False,
                    ) as session:
                        async with session.get(effective_url) as resp:
                            if resp.status == 206:
                                data = await resp.read()
                                return (proxy_idx, data, source)
                            if resp.status == 200:
                                data = await self._read_range_from_full_response(resp, range_start, range_end)
                                logger.info(
                                    f"[RACE] {source} sliced from full response "
                                    f"range={range_start}-{range_end} bytes={len(data)}"
                                )
                                resp.release()
                                return (proxy_idx, data, source)
                            logger.debug(f"[RACE] {source} returned HTTP {resp.status}")
                            return (proxy_idx, None, source)
                else:
                    # Direct connection or HTTP proxy
                    timeout = ClientTimeout(total=30, connect=8, sock_read=20)
                    async with aiohttp.ClientSession(
                        timeout=timeout,
                        headers=request_headers,
                        raise_for_status=False,
                    ) as session:
                        async with session.get(effective_url, proxy=proxy) as resp:
                            if resp.status == 206:
                                data = await resp.read()
                                return (proxy_idx, data, source)
                            if resp.status == 200:
                                data = await self._read_range_from_full_response(resp, range_start, range_end)
                                logger.info(
                                    f"[RACE] {source} sliced from full response "
                                    f"range={range_start}-{range_end} bytes={len(data)}"
                                )
                                resp.release()
                                return (proxy_idx, data, source)
                            logger.debug(f"[RACE] {source} returned HTTP {resp.status}")
                            return (proxy_idx, None, source)
            except Exception as e:
                logger.debug(f"[RACE] {source} failed: {type(e).__name__}: {e}")
                return (proxy_idx, None, source)

        # Create tasks for all proxies PLUS direct connection
        tasks = []
        for idx, proxy in enumerate(self._proxy_pool):
            tasks.append(asyncio.create_task(try_proxy(proxy, idx)))
        # Add direct connection as last option (index = len(proxy_pool))
        tasks.append(asyncio.create_task(try_proxy(None, len(self._proxy_pool))))

        # Wait for first successful result
        winner_idx = None
        result_data = None
        winner_source = None
        pending = set(tasks)
        failed_sources = []

        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    proxy_idx, data, source = task.result()
                    if data is not None:
                        winner_idx = proxy_idx
                        result_data = data
                        winner_source = source
                        # Don't cancel remaining tasks - let them complete gracefully
                        # This avoids "broken pipe" errors in SOCKS proxies
                        # Tasks will timeout naturally or complete in background
                        for p in pending:
                            # Fire-and-forget: schedule cleanup without waiting
                            asyncio.create_task(self._graceful_task_cleanup(p))
                        pending.clear()
                        break
                    else:
                        failed_sources.append(source)
                except Exception as e:
                    logger.debug(f"[RACE] Task exception: {e}")

        if result_data:
            logger.info(f"[RACE] Chunk {chunk_index} won by {winner_source} - {len(result_data)} bytes")
            if winner_idx < len(self._proxy_pool):
                winner_proxy = self._proxy_pool[winner_idx]
                self._proxy_usage_stats[winner_proxy] = self._proxy_usage_stats.get(winner_proxy, 0) + 1
            else:
                self._proxy_usage_stats['direct'] = self._proxy_usage_stats.get('direct', 0) + 1
            return result_data

        logger.error(f"[RACE] Chunk {chunk_index} - ALL sources failed: {failed_sources}")
        return None

    async def _graceful_task_cleanup(self, task: asyncio.Task) -> None:
        """Let a racing task complete gracefully without blocking."""
        try:
            # Wait for task to complete naturally (with timeout)
            await asyncio.wait_for(task, timeout=5.0)
        except asyncio.TimeoutError:
            # If still running after 5s, cancel it
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        except Exception:
            # Ignore any errors from the task
            pass

    @staticmethod
    def _parse_range_header(range_header: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
        if not range_header:
            return None, None
        if not range_header.startswith("bytes="):
            return None, None
        spec = range_header[6:].strip()
        if "," in spec:
            spec = spec.split(",", 1)[0].strip()
        if "-" not in spec:
            return None, None
        start_s, end_s = spec.split("-", 1)
        try:
            start = int(start_s) if start_s else None
        except (TypeError, ValueError):
            return None, None
        try:
            end = int(end_s) if end_s else None
        except (TypeError, ValueError):
            return None, None
        return start, end

    @staticmethod
    async def _read_range_from_full_response(
        response: aiohttp.ClientResponse,
        start: Optional[int],
        end: Optional[int],
    ) -> bytes:
        if start is None or end is None or end < start:
            return await response.read()

        target_parts: list[bytes] = []
        offset = 0
        async for piece in response.content.iter_chunked(256 * 1024):
            piece_end = offset + len(piece) - 1
            if piece_end < start:
                offset += len(piece)
                continue
            if offset > end:
                break
            slice_start = max(start - offset, 0)
            slice_end = min(end - offset, len(piece) - 1)
            if slice_end >= slice_start:
                target_parts.append(piece[slice_start:slice_end + 1])
            offset += len(piece)
            if offset > end:
                break
        return b"".join(target_parts)

    async def _fetch_range(self, url: str, headers: dict, chunk_index: int = 0) -> Optional[bytes]:
        effective_url = self._effective_url(url)
        range_header = headers.get("Range")
        range_start, range_end = self._parse_range_header(range_header)
        proxy = self._select_proxy(chunk_index)
        
        # Build headers with Referer for anti-hotlinking CDNs
        request_headers = get_media_headers_with_referer(effective_url)
        request_headers.update(headers)  # Merge in Range header etc.

        # For SOCKS proxies, we need to use ProxyConnector, not proxy parameter
        if proxy and proxy.startswith('socks') and SOCKS_SUPPORT:
            try:
                logger.debug(f"[FETCH] Creating SOCKS connector for chunk {chunk_index} via {proxy}")
                # Create a temporary session with SOCKS connector for this request
                connector = ProxyConnector.from_url(
                    proxy,
                    ttl_dns_cache=300,
                    rdns=False,
                    family=socket.AF_INET,
                    force_close=False,
                )
                timeout = ClientTimeout(
                    total=180,  # 3 minutes total for VPN connections
                    connect=20,  # 20 seconds to establish connection through VPN
                    sock_read=60,  # 60 seconds between reads - VPNs can be slow
                )
                logger.debug(f"[FETCH] Opening SOCKS session for chunk {chunk_index}")
                async with aiohttp.ClientSession(
                    connector=connector,
                    timeout=timeout,
                    headers=request_headers,
                    raise_for_status=False,
                ) as temp_session:
                    logger.debug(f"[FETCH] Sending request for chunk {chunk_index} to {effective_url[:60]}")
                    async with temp_session.get(effective_url) as resp:
                        logger.debug(f"[FETCH] Chunk {chunk_index} response: HTTP {resp.status}")
                        if resp.status != 206:
                            if resp.status == 200:
                                data = await self._read_range_from_full_response(resp, range_start, range_end)
                                logger.info(
                                    f"[FETCH] Chunk {chunk_index} sliced from full response "
                                    f"range={range_start}-{range_end} bytes={len(data)}"
                                )
                                resp.release()
                                return data
                            logger.warning(f"Range request failed with status {resp.status} for {effective_url[:50]}")
                            return None
                        # Stream the data in pieces to track progress
                        content_length = resp.headers.get('Content-Length', 'unknown')
                        logger.debug(f"[FETCH] Chunk {chunk_index} starting download, Content-Length: {content_length}")
                        chunks = []
                        bytes_received = 0
                        async for piece in resp.content.iter_chunked(256 * 1024):  # 256KB pieces
                            chunks.append(piece)
                            bytes_received += len(piece)
                            if bytes_received % (1024 * 1024) < 256 * 1024:  # Log every ~1MB
                                logger.debug(f"[FETCH] Chunk {chunk_index} progress: {bytes_received / (1024*1024):.1f} MB")
                        data = b''.join(chunks)
                        logger.debug(f"[FETCH] Chunk {chunk_index} received: {len(data)} bytes")
                        return data
            except (asyncio.TimeoutError, ServerTimeoutError) as e:
                logger.warning(f"Timeout fetching range from {url[:50]} via SOCKS proxy {proxy}: {e}")
                self._errors += 1
                return None
            except ClientConnectionError as e:
                logger.warning(f"Connection error fetching range from {url[:50]} via SOCKS proxy {proxy}: {e}")
                self._errors += 1
                return None
            except Exception as e:
                logger.warning(f"Unexpected error fetching range from {url[:50]} via SOCKS proxy {proxy}: {e}")
                self._errors += 1
                return None
        elif proxy and proxy.startswith('socks') and not SOCKS_SUPPORT:
            logger.warning(f"SOCKS proxy {proxy} requested but aiohttp-socks not installed")
            return None
        else:
            # HTTP proxy or no proxy - use regular session with proxy parameter
            try:
                logger.debug(f"[FETCH] Chunk {chunk_index} starting direct request to {effective_url[:80]}...")
                logger.debug(f"[FETCH] Chunk {chunk_index} headers: {request_headers}")
                async with self._session.get(effective_url, headers=request_headers, proxy=proxy) as resp:
                    logger.debug(f"[FETCH] Chunk {chunk_index} response: HTTP {resp.status}, Content-Length: {resp.headers.get('Content-Length', 'unknown')}")
                    if resp.status != 206:
                        if resp.status == 200:
                            data = await self._read_range_from_full_response(resp, range_start, range_end)
                            logger.debug(
                                f"[FETCH] Chunk {chunk_index} sliced from full response "
                                f"range={range_start}-{range_end} bytes={len(data)}"
                            )
                            resp.release()
                            return data
                        logger.warning(f"[FETCH] Chunk {chunk_index} failed with status {resp.status} for {effective_url[:50]}")
                        return None
                    # Stream data in chunks to show progress
                    logger.debug(f"[FETCH] Chunk {chunk_index} starting to stream body...")
                    chunks = []
                    bytes_read = 0
                    try:
                        async for piece in resp.content.iter_chunked(256 * 1024):  # 256KB pieces
                            chunks.append(piece)
                            bytes_read += len(piece)
                            if bytes_read % (2 * 1024 * 1024) < 256 * 1024:  # Log every ~2MB
                                logger.debug(f"[FETCH] Chunk {chunk_index} progress: {bytes_read / (1024*1024):.1f} MB")
                    except Exception as stream_error:
                        logger.error(f"[FETCH] Chunk {chunk_index} streaming error after {bytes_read} bytes: {type(stream_error).__name__}: {stream_error}")
                        raise
                    data = b''.join(chunks)
                    logger.debug(f"[FETCH] Chunk {chunk_index} received {len(data)} bytes")
                    return data
            except (asyncio.TimeoutError, ServerTimeoutError) as e:
                logger.warning(f"[FETCH] Chunk {chunk_index} TIMEOUT from {url[:50]}: {e}")
                self._errors += 1
                return None
            except ClientConnectionError as e:
                logger.warning(f"[FETCH] Chunk {chunk_index} CONNECTION ERROR from {url[:50]}: {e}")
                self._errors += 1
                return None
            except Exception as e:
                logger.warning(f"[FETCH] Chunk {chunk_index} UNEXPECTED ERROR from {url[:50]}: {type(e).__name__}: {e}")
                self._errors += 1
                return None

    async def _get_lock(self, key: str) -> asyncio.Lock:
        async with self._key_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            # Track last usage time
            self._lock_last_used[key] = time.time()
            return lock

    def _key(self, url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def _entry_dir(self, key: str) -> Path:
        shard = key[:2]
        return self._cache_dir / shard / key

    def _chunk_path(self, key: str, index: int) -> Path:
        return self._entry_dir(key) / f"chunk_{index}.bin"

    def _meta_path(self, key: str) -> Path:
        return self._entry_dir(key) / "meta.json"

    def _cache_resolved_url(self, original_url: str, resolved_url: Optional[str]) -> None:
        if resolved_url and resolved_url != original_url:
            if self._resolved_urls.get(original_url) != resolved_url:
                logger.info(f"Resolved mirror cached: {original_url[:60]} -> {resolved_url[:60]}")
            self._resolved_urls[original_url] = resolved_url

    def _effective_url(self, original_url: str) -> str:
        resolved = self._resolved_urls.get(original_url)
        if resolved and resolved != original_url and original_url not in self._resolved_url_logged:
            logger.info(f"Using resolved mirror for requests: {original_url[:60]} -> {resolved[:60]}")
            self._resolved_url_logged.add(original_url)
        return resolved or original_url

    def cached_ranges(self, url: str) -> Tuple[Optional[int], list[Tuple[int, int]]]:
        original = self._decode_proxy_url(url)
        key = self._key(original)
        entry_dir = self._entry_dir(key)
        total_size = None
        chunk_size = self._chunk_size
        meta_path = self._meta_path(key)
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                total_size_raw = meta.get("total_size")
                if total_size_raw is not None:
                    try:
                        total_size = int(total_size_raw)
                    except (TypeError, ValueError):
                        total_size = None
                chunk_size = int(meta.get("chunk_size") or chunk_size)
            except Exception as e:
                logger.debug(f"Failed to parse meta file for {key[:8]}: {e}")
        if not entry_dir.exists():
            return total_size, []
        ranges = []
        try:
            for path in entry_dir.glob("chunk_*.bin"):
                name = path.stem
                if not name.startswith("chunk_"):
                    continue
                try:
                    index = int(name.split("_", 1)[1])
                except (TypeError, ValueError):
                    continue
                try:
                    size = path.stat().st_size
                except Exception:
                    continue
                if size <= 0:
                    continue
                start = index * chunk_size
                end = start + size - 1
                ranges.append((start, end))
        except Exception as e:
            logger.warning(f"Failed to scan cached chunks for {key[:8]}: {e}")
            return total_size, []
        if not ranges:
            return total_size, []
        ranges.sort(key=lambda item: item[0])
        merged: list[Tuple[int, int]] = [ranges[0]]
        for start, end in ranges[1:]:
            last_start, last_end = merged[-1]
            if start <= last_end + 1:
                merged[-1] = (last_start, max(last_end, end))
            else:
                merged.append((start, end))
        return total_size, merged

    def assemble_cached_file(
        self,
        url: str,
        destination: Path,
        progress_callback: Optional[callable] = None,
    ) -> bool:
        """
        Assemble a complete file from cached chunks.

        This allows reusing chunks that were downloaded during video playback
        for full file downloads, avoiding redundant network requests.

        Args:
            url: The original media URL (not the proxy URL).
            destination: Path where the assembled file will be written.
            progress_callback: Optional callback(bytes_written, total_size) for progress.

        Returns:
            True if file was successfully assembled from cache, False if chunks are missing.
        """
        original = self._decode_proxy_url(url)
        key = self._key(original)
        meta_path = self._meta_path(key)

        if not meta_path.exists():
            logger.debug(f"No cache metadata for {key[:8]} - cannot assemble")
            return False

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to read cache metadata for {key[:8]}: {e}")
            return False

        total_size = meta.get("total_size")
        chunk_size = meta.get("chunk_size", self._chunk_size)

        if not total_size:
            logger.debug(f"No total_size in metadata for {key[:8]} - cannot assemble")
            return False

        # Calculate number of chunks needed
        num_chunks = (total_size + chunk_size - 1) // chunk_size

        # Verify all chunks exist before starting assembly
        missing_chunks = []
        for i in range(num_chunks):
            chunk_path = self._chunk_path(key, i)
            if not chunk_path.exists():
                missing_chunks.append(i)

        if missing_chunks:
            cached_pct = ((num_chunks - len(missing_chunks)) / num_chunks) * 100
            logger.info(
                f"Cannot assemble {key[:8]}: missing {len(missing_chunks)}/{num_chunks} chunks "
                f"({cached_pct:.1f}% cached)"
            )
            return False

        # All chunks present - assemble the file
        logger.info(
            f"Assembling {key[:8]} from {num_chunks} cached chunks "
            f"({total_size / (1024 * 1024):.2f} MB)"
        )

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            bytes_written = 0

            with open(destination, "wb") as f:
                for i in range(num_chunks):
                    chunk_path = self._chunk_path(key, i)
                    chunk_data = chunk_path.read_bytes()
                    f.write(chunk_data)
                    bytes_written += len(chunk_data)

                    if progress_callback:
                        try:
                            progress_callback(bytes_written, total_size)
                        except Exception:
                            pass

            # Verify final size
            actual_size = destination.stat().st_size
            if actual_size != total_size:
                logger.warning(
                    f"Assembled file size mismatch for {key[:8]}: "
                    f"expected {total_size}, got {actual_size}"
                )
                # Don't delete - might still be usable

            logger.info(f"Successfully assembled {key[:8]} -> {destination}")
            return True

        except Exception as e:
            logger.error(f"Failed to assemble file from cache for {key[:8]}: {e}")
            # Clean up partial file
            try:
                if destination.exists():
                    destination.unlink()
            except Exception:
                pass
            return False

    def get_cached_percentage(self, url: str) -> float:
        """
        Get the percentage of a file that is cached.

        Args:
            url: The media URL.

        Returns:
            Percentage cached (0.0 to 100.0), or 0.0 if not cached.
        """
        total_size, ranges = self.cached_ranges(url)
        if not total_size or not ranges:
            return 0.0

        cached_bytes = sum(end - start + 1 for start, end in ranges)
        return (cached_bytes / total_size) * 100.0

    @staticmethod
    def _decode_proxy_url(url: str) -> str:
        try:
            parsed = urlparse(url)
        except Exception:
            return url
        if not parsed.query:
            return url
        params = parse_qs(parsed.query)
        target_list = params.get("url")
        if not target_list:
            return url
        target = target_list[0]
        if not target:
            return url
        return unquote(target)

    async def _update_meta(
        self,
        url: str,
        total_size: Optional[int],
        content_type: Optional[str],
        resolved_url: Optional[str] = None,
    ) -> None:
        """Update metadata cache with new information."""
        key = self._key(url)
        meta_path = self._meta_path(key)

        # Load existing meta if available
        meta = {}
        if meta_path.exists():
            try:
                content = await asyncio.to_thread(meta_path.read_text, encoding="utf-8")
                meta = json.loads(content)
            except Exception as e:
                logger.debug(f"Failed to load existing meta for {key[:8]}: {e}")

        # Update fields
        meta["url"] = url
        meta["chunk_size"] = self._chunk_size
        if total_size is not None:
            meta["total_size"] = total_size
        if content_type is not None:
            meta["content_type"] = content_type
        if resolved_url:
            meta["resolved_url"] = resolved_url
        meta["updated_at"] = time.time()

        # Write back
        try:
            await asyncio.to_thread(meta_path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(meta_path.write_text, json.dumps(meta), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to write meta for {key[:8]}: {e}")

    async def _ensure_meta(self, url: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        key = self._key(url)
        meta_path = self._meta_path(key)
        if meta_path.exists():
            try:
                content = await asyncio.to_thread(meta_path.read_text, encoding="utf-8")
                meta = json.loads(content)
                resolved_url = meta.get("resolved_url")
                self._cache_resolved_url(url, resolved_url)
                return meta.get("total_size"), meta.get("content_type"), resolved_url
            except Exception as e:
                logger.debug(f"Failed to read cached meta for {key[:8]}: {e}")
        total_size, content_type, resolved_url = await self._probe_origin(url)
        meta = {
            "url": url,
            "chunk_size": self._chunk_size,
            "total_size": total_size,
            "content_type": content_type,
            "resolved_url": resolved_url,
            "updated_at": time.time(),
        }
        try:
            await asyncio.to_thread(meta_path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(meta_path.write_text, json.dumps(meta), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to cache meta for {key[:8]}: {e}")
        self._cache_resolved_url(url, resolved_url)
        return total_size, content_type, resolved_url

    async def _probe_origin(self, url: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        effective_url = self._effective_url(url)
        resolved_url = effective_url
        total_size = None
        content_type = None
        
        # Build headers with Referer for anti-hotlinking CDNs
        request_headers = get_media_headers_with_referer(effective_url)
        request_headers["Range"] = "bytes=0-0"

        # Use first proxy from pool if available, otherwise single proxy
        probe_proxy = self._proxy_pool[0] if self._proxy_pool else self._proxy_url
        logger.debug(f"Probing origin via: {probe_proxy or 'direct'}")

        # For SOCKS proxies, we need a separate session
        if probe_proxy and probe_proxy.startswith('socks') and SOCKS_SUPPORT:
            try:
                logger.debug(f"[PROBE] Creating SOCKS session via {probe_proxy}")
                connector = ProxyConnector.from_url(probe_proxy, ttl_dns_cache=300, rdns=False, family=socket.AF_INET)
                timeout = ClientTimeout(total=30, connect=10, sock_read=20)
                async with aiohttp.ClientSession(
                    connector=connector,
                    timeout=timeout,
                    headers=request_headers,
                ) as probe_session:
                    logger.debug(f"[PROBE] Sending probe request to {effective_url[:60]}")
                    async with probe_session.get(effective_url) as resp:
                        logger.debug(f"[PROBE] Response: HTTP {resp.status} for {url[:60]}")
                        if resp.status >= 400:
                            logger.warning(f"Origin returned HTTP {resp.status} for {url}")
                        resolved_url = str(resp.url) if resp.url else effective_url
                        self._cache_resolved_url(url, resolved_url)
                        content_type = resp.headers.get("Content-Type")
                        content_range = resp.headers.get("Content-Range")
                        if content_range and "/" in content_range:
                            try:
                                total_size = int(content_range.split("/")[-1])
                            except (TypeError, ValueError):
                                total_size = None
                        if total_size is None:
                            length = resp.headers.get("Content-Length")
                            if length:
                                try:
                                    total_size = int(length)
                                except (TypeError, ValueError):
                                    total_size = None
                if total_size is None:
                    total_size = await self._lookup_size_from_search_hash(url)
                return total_size, content_type, resolved_url
            except Exception as e:
                logger.error(f"[PROBE] SOCKS probe failed for {url[:60]}: {e}")
                # Fall through to try direct connection
                probe_proxy = None

        # HTTP proxy or direct - use main session
        async with self._session.get(effective_url, headers=request_headers, proxy=probe_proxy) as resp:
            logger.debug(f"Probe origin response: {resp.status} for {url[:60]}")
            if resp.status >= 400:
                logger.warning(f"Origin returned HTTP {resp.status} for {url}")
            resolved_url = str(resp.url) if resp.url else effective_url
            self._cache_resolved_url(url, resolved_url)
            content_type = resp.headers.get("Content-Type")
            content_range = resp.headers.get("Content-Range")
            if content_range and "/" in content_range:
                try:
                    total_size = int(content_range.split("/")[-1])
                except (TypeError, ValueError):
                    total_size = None
            if total_size is None:
                length = resp.headers.get("Content-Length")
                if length:
                    try:
                        total_size = int(length)
                    except (TypeError, ValueError):
                        total_size = None
        if total_size is None:
            total_size = await self._lookup_size_from_search_hash(url)
        return total_size, content_type, resolved_url

    async def _lookup_size_from_search_hash(self, url: str) -> Optional[int]:
        file_hash = self._extract_hash_from_url(url)
        if not file_hash:
            return None
        base = self._search_hash_api_base(url)
        if not base:
            return None
        api_url = f"{base}/v1/search_hash/{file_hash}"
        try:
            async with self._session.get(
                api_url,
                timeout=self._search_hash_timeout_s,
                headers={
                    "User-Agent": "Coomer-BetterUI",
                    "Accept": "text/css",
                },
                proxy=self._proxy_url,
            ) as resp:
                if not resp.ok:
                    return None
                data = await resp.json()
        except Exception as e:
            logger.debug(f"Failed to lookup size from search_hash API for {file_hash}: {e}")
            return None
        if not isinstance(data, dict):
            return None
        size_raw = data.get("size")
        try:
            size = int(size_raw)
        except (TypeError, ValueError):
            return None
        if size <= 0:
            return None
        return size

    def _extract_hash_from_url(self, url: str) -> Optional[str]:
        try:
            parsed = urlparse(url)
            stem = Path(parsed.path).stem.lower()
        except Exception:
            return None
        if not stem or not self._hash_re.fullmatch(stem):
            return None
        return stem

    def _search_hash_api_base(self, url: str) -> Optional[str]:
        try:
            parsed = urlparse(url)
            host = (parsed.netloc or "").lower()
        except Exception:
            return None
        if "coomer" in host:
            return "https://coomer.st/api"
        if "kemono" in host:
            return "https://kemono.cr/api"
        return None

    @staticmethod
    def _total_size_from_content_range(content_range: Optional[str]) -> Optional[int]:
        """Extract total size from Content-Range header (e.g., 'bytes 0-1/12345')."""
        if not content_range:
            return None
        if "/" not in content_range:
            return None
        try:
            total_part = content_range.split("/")[-1].strip()
            if total_part == "*":
                return None
            return int(total_part)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_range(value: Optional[str], total_size: Optional[int]) -> Tuple[Optional[int], Optional[int]]:
        if not value:
            return None, None
        if not value.startswith("bytes="):
            return None, None
        spec = value[6:].strip()
        if "," in spec:
            spec = spec.split(",", 1)[0].strip()
        if spec.startswith("-"):
            if total_size is None:
                return None, None
            try:
                length = int(spec[1:])
            except (TypeError, ValueError):
                return None, None
            if length <= 0:
                return None, None
            start = max(0, total_size - length)
            end = total_size - 1
            return start, end
        if "-" not in spec:
            return None, None
        start_s, end_s = spec.split("-", 1)
        try:
            start = int(start_s) if start_s else None
        except (TypeError, ValueError):
            return None, None
        try:
            end = int(end_s) if end_s else None
        except (TypeError, ValueError):
            return None, None
        if start is None:
            return None, None
        if total_size is not None and start >= total_size:
            return None, None
        if end is not None and start > end:
            return None, None
        return start, end


_proxy_instance: Optional[RangeProxy] = None


def get_range_proxy(
    cache_dir: Optional[Path] = None,
    max_concurrent_chunks: int = 5,
    max_connections_per_host: int = 10,
    max_total_connections: int = 100,
    chunk_size: int = 8 * 1024 * 1024,
    proxy_url: Optional[str] = None,
    proxy_pool: Optional[list[str]] = None,
) -> RangeProxy:
    """
    Get the singleton RangeProxy instance.

    Args:
        cache_dir: Optional cache directory. Only used on first initialization.
                   Subsequent calls will return the existing instance.
        max_concurrent_chunks: Maximum concurrent chunk downloads per stream.
        max_connections_per_host: Maximum connections to each host.
        max_total_connections: Maximum total connections across all hosts.
        chunk_size: Size of each chunk in bytes (default: 8MB).
        proxy_url: Single proxy URL (used if proxy_pool is empty).
        proxy_pool: List of proxy URLs to distribute chunks across.
    """
    global _proxy_instance
    if _proxy_instance is None:
        _proxy_instance = RangeProxy(
            cache_dir=cache_dir,
            max_concurrent_chunks=max_concurrent_chunks,
            max_connections_per_host=max_connections_per_host,
            max_total_connections=max_total_connections,
            chunk_size=chunk_size,
            proxy_url=proxy_url,
            proxy_pool=proxy_pool,
        )
    return _proxy_instance
