"""
CORE API CONTRACT — DO NOT MODIFY WITHOUT UPDATING:
docs/CORE_API_CONTRACT.md

This module is intentionally UI-agnostic and DTO-agnostic.
Platform quirks are normalized inside platform clients.

Contract goals:
- Stable, minimal surface area
- Deterministic pagination invariants (offset is enforced by managers)
- Returns plain dict/list payloads (DTO creation belongs to managers)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Optional
from pathlib import Path
from src.core.cache import DeterministicCache, make_cache_key, CacheDecision, CacheRule
from src.core.http_client import API_HEADERS

import requests
import logging

try:
    import ijson
    IJSON_AVAILABLE = True
except ImportError:
    ijson = None
    IJSON_AVAILABLE = False


class APIError(RuntimeError):
    """Raised for platform HTTP / parsing errors."""


logger = logging.getLogger(__name__)


class BaseAPIClient(ABC):
    """
    Authoritative Core API contract.

    UI must NOT call platform clients directly; managers should.
    Platform clients must normalize quirks internally.
    """

    BASE_URL: str  # e.g. https://coomer.st/api
    PLATFORM: str  # "coomer" | "kemono"

    def __init__(self, session: Optional[requests.Session] = None, *, cache_dir: Optional[str] = None):
        self.session = session or requests.Session()
        self._configure_session()

        # Deterministic cache lives in Core, never exposed to UI.
        # version_salt: bump when response normalization or DTO expectations change.
        base_dir = Path(cache_dir) if cache_dir else Path.home() / ".coomer-betterui" / "http_cache"
        self._cache = DeterministicCache(
            version_salt="httpcache-v1",
            memory_limit=256,
            disk_dir=base_dir / self.PLATFORM,
        )

    # ------------------------------------------------------------------
    # Session / Request helpers
    # ------------------------------------------------------------------

    def _configure_session(self) -> None:
        # Use browser-like headers from centralized config.
        # These mimic a real browser to avoid DDoS Guard blocks.
        self.session.headers.update(API_HEADERS)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 120,
    ) -> Any:
        url = f"{self.BASE_URL}{path}"

        # Log the full request URL with parameters (before cache check)
        if params:
            from urllib.parse import urlencode
            logger.debug(f"Request params: {params}")
            query_string = urlencode(params, doseq=True)
            full_url = f"{url}?{query_string}"
            logger.info(f"API Request: {method} {full_url}")
        else:
            logger.info(f"API Request: {method} {url}")

        req_headers = dict(self.session.headers)
        if headers:
            req_headers.update(headers)

        # Only headers that affect response should vary the cache key.
        vary = {}
        if "Accept" in req_headers:
            vary["Accept"] = req_headers["Accept"]

        decision = self._cache_policy(method, path)

        cache_key = None
        if decision.enabled and method.upper() == "GET":
            cache_key = make_cache_key(
                version_salt=self._cache.version_salt,
                platform=self.PLATFORM,
                method=method,
                path=path,
                params=params,
                vary_headers=vary,
            )
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.info(f"  └─ [CACHE HIT]")
                return cached

        try:
            resp = self.session.request(
                method=method,
                url=url,
                params=params,
                headers=req_headers,
                timeout=timeout,
            )
            if not resp.ok:
                raise APIError(f"{self.PLATFORM} API error {resp.status_code}: {resp.text}")

            data = resp.json()
        except Exception as e:
            # Deterministic "stale-if-error"
            if cache_key and decision.stale_if_error:
                cached = self._cache.get(cache_key)
                if cached is not None:
                    return cached
            if isinstance(e, APIError):
                raise
            raise APIError(f"{self.PLATFORM} request failed: {e}") from e

        if cache_key:
            self._cache.set(cache_key, data, ttl_seconds=decision.ttl_seconds)

        return data

    def _request_stream_json_array(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 120,
    ) -> Iterable[dict]:
        """
        Stream a JSON array response, yielding items as they're parsed.
        
        This uses ijson to incrementally parse the JSON response, which:
        - Reduces peak memory usage (don't hold entire list in memory)
        - Allows processing items as they arrive
        - Enables early exit to save bandwidth
        
        Note: This method bypasses caching since streaming is incompatible
        with the current cache design.
        
        Args:
            method: HTTP method (typically "GET")
            path: API path (e.g., "/v1/creators")
            params: Query parameters
            headers: Additional headers
            timeout: Request timeout
            
        Yields:
            Individual dict items from the JSON array
        """
        if not IJSON_AVAILABLE:
            # Fallback to non-streaming if ijson not installed
            logger.warning("ijson not available, falling back to non-streaming request")
            data = self._request(method, path, params=params, headers=headers, timeout=timeout)
            if isinstance(data, list):
                yield from data
            return
        
        url = f"{self.BASE_URL}{path}"
        logger.info(f"API Stream Request: {method} {url}")
        
        req_headers = dict(self.session.headers)
        if headers:
            req_headers.update(headers)
        
        resp = None
        try:
            resp = self.session.request(
                method=method,
                url=url,
                params=params,
                headers=req_headers,
                timeout=timeout,
                stream=True,
            )
            
            if not resp.ok:
                raise APIError(f"{self.PLATFORM} API error {resp.status_code}: {resp.text}")
            
            # Enable automatic gzip/deflate decompression
            resp.raw.decode_content = True
            
            # Stream parse JSON array items
            for item in ijson.items(resp.raw, 'item'):
                yield item
                
        except Exception as e:
            if isinstance(e, APIError):
                raise
            raise APIError(f"{self.PLATFORM} stream request failed: {e}") from e
        finally:
            if resp is not None:
                resp.close()


    # ------------------------------------------------------------------
    # Normalization helpers (platform-specific)
    # ------------------------------------------------------------------

    @abstractmethod
    def normalize_creator(self, raw: dict) -> dict:
        """
        Convert raw creator profile/list object -> normalized dict.
        Expected normalized keys (minimum):
          - id (str)
          - service (str)
          - name (str)
          - indexed (optional)
          - updated (optional)
          - public_id (optional)
          - relation_id (optional)
          - favorited (optional)
          - post_count (int, may be 0/unknown if unavailable)
          - dm_count (optional)
          - share_count (optional)
          - chat_count (optional)
          - display_href (optional)
        """

    @abstractmethod
    def normalize_post(self, raw: dict) -> dict:
        """
        Convert raw post object -> normalized dict.
        Expected normalized keys (minimum):
          - id, service, creator_id(user), title, content
          - added/published/edited
          - file, attachments, embed
          - shared_file (bool)
          - fav_count (optional)
        """

    # ------------------------------------------------------------------
    # Creators
    # ------------------------------------------------------------------

    @abstractmethod
    def get_all_creators(self) -> List[dict]:
        """
        Returns ALL creators (non-paginated, can be very large).
        Note: endpoint may require Accept: text/css while returning JSON.
        Returned list items are normalized dicts (not DTOs).
        """

    @abstractmethod
    def get_creator(self, service: str, creator_id: str) -> dict:
        """
        Returns a single creator profile object (normalized dict).
        IMPORTANT: this is where true post_count is available.
        """

    @abstractmethod
    def get_creator_posts(
        self,
        *,
        service: str,
        creator_id: str,
        offset: int,
        query: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Returns a page of creator posts as a normalized page dict.

        Recommended page dict keys:
          - props (optional passthrough)
          - creator (normalized creator dict, best-effort)
          - posts (list of normalized posts)
          - count/limit/offset/has_more (best-effort)
          - raw (optional passthrough if needed)
        """

    @abstractmethod
    def get_post(
        self,
        *,
        service: str,
        post_id: str,
        creator_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Returns a single post payload as a normalized dict.
        """

    # ------------------------------------------------------------------
    # Global posts (All Posts view)
    # ------------------------------------------------------------------

    @abstractmethod
    def get_posts(
        self,
        *,
        offset: int,
        query: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Platform-wide posts feed.
        Uses /v1/posts with params:
          - o (offset, step 50)
          - q (query)
          - tags (array)
        Returns a normalized page dict:
          - posts (list of normalized posts)
          - count (capped to 50000)
          - true_count (real count)
          - offset
          - has_more (based on capped count)
        """

    # ------------------------------------------------------------------
    # Random / Popular / Tags
    # ------------------------------------------------------------------

    @abstractmethod
    def get_random_post(self) -> Dict[str, str]:
        """
        Returns a navigation locator only:
          { "service": str, "artist_id": str, "post_id": str }
        On 404, API may return { "error": str } which should raise APIError.
        """

    @abstractmethod
    def get_popular_posts(
        self,
        *,
        date: Optional[str] = None,   # "YYYY-MM-DD"
        period: str = "recent",       # "recent"|"day"|"week"|"month"
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Returns popular posts page.
        Expected normalized keys:
          - info (passthrough)
          - props (passthrough)
          - posts (normalized list)
          - offset
          - has_more
        """

    @abstractmethod
    def get_tags(self) -> List[str]:
        """Returns list of available tags via /v1/posts/tags."""

    @abstractmethod
    def get_random_creator(self) -> Dict[str, str]:
        """
        Returns a creator locator only:
          { "service": str, "artist_id": str }
        On 404, API may return { "error": str } which should raise APIError.
        """

    def _cache_policy(self, method: str, path: str) -> CacheDecision:
        """
        Deterministic, explicit rules.
        If you change these, you know exactly what changes.
        """
        method = method.upper()

        # Never cache random endpoints (must be truly random).
        if path.startswith("/v1/posts/random"):
            return CacheDecision(enabled=False)
        if path.startswith("/v1/artists/random"):
            return CacheDecision(enabled=False)

        # Creators list is huge; disk cache helps startup massively.
        if method == "GET" and path == "/v1/creators":
            return CacheDecision(enabled=True, ttl_seconds=6 * 60 * 60, stale_if_error=True)

        # Creator profile changes but not minute-to-minute.
        if method == "GET" and "/user/" in path and path.endswith("/profile"):
            return CacheDecision(enabled=True, ttl_seconds=30 * 60, stale_if_error=True)

        # Posts feeds: short TTL to avoid stale browsing.
        if method == "GET" and path in ("/v1/posts", "/v1/posts/popular"):
            return CacheDecision(enabled=True, ttl_seconds=60, stale_if_error=True)

        # Tags: infrequently changes.
        if method == "GET" and path == "/v1/posts/tags":
            return CacheDecision(enabled=True, ttl_seconds=24 * 60 * 60, stale_if_error=True)

        # Default: uncached
        return CacheDecision(enabled=False)
