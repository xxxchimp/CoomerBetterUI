"""
Centralized HTTP client configuration.

Provides unified session management for both sync (requests) and async (aiohttp)
HTTP clients with:
- Browser-like headers (User-Agent, Accept, etc.)
- Cookie persistence across sessions
- Proxy support (single proxy or rotating proxy pool)
- DDoS Guard cookie handling
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import socket
import time
from http.cookiejar import LWPCookieJar
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp
import requests
from aiohttp import TCPConnector, ClientTimeout
try:
    from aiohttp_socks import ProxyConnector
    SOCKS_SUPPORT = True
except ImportError:
    ProxyConnector = None
    SOCKS_SUPPORT = False

logger = logging.getLogger(__name__)


# Browser-like headers that mimic Chrome on Windows
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Not A(Brand";v="8", "Chromium";v="144", "Brave";v="144"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Headers specifically for API requests (JSON expected)
API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "Accept": "text/css",  # kemono/coomer API quirk - returns JSON with this Accept header
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Not A(Brand";v="8", "Chromium";v="144", "Brave";v="144"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}

# Headers for media/file downloads
MEDIA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity;q=1, *;q=0",
    "Sec-Ch-Ua": '"Not A(Brand";v="8", "Chromium";v="144", "Brave";v="144"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "video",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-origin",
}


def get_media_headers_with_referer(url: str) -> dict:
    """
    Get media headers with a Referer header derived from the URL.
    Many CDNs check Referer to prevent hotlinking.
    """
    headers = MEDIA_HEADERS.copy()
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.hostname:
            # Use the origin as referer (e.g., https://coomer.st)
            referer = f"{parsed.scheme}://{parsed.hostname}/"
            headers["Referer"] = referer
            headers["Origin"] = f"{parsed.scheme}://{parsed.hostname}"
    except Exception:
        pass
    return headers


class ProxyConfig:
    """Configuration for proxy support."""

    def __init__(
        self,
        enabled: bool = False,
        proxy_url: Optional[str] = None,
        proxy_pool: Optional[List[str]] = None,
        rotation_strategy: str = "round_robin",  # "round_robin", "random", "least_used"
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.enabled = enabled
        self.proxy_url = proxy_url
        self.proxy_pool = proxy_pool or []
        self.rotation_strategy = rotation_strategy
        self.username = username
        self.password = password

        # State for rotation
        self._pool_index = 0
        self._pool_usage: Dict[str, int] = {}
        self._pool_failures: Dict[str, int] = {}

    def get_proxy(self) -> Optional[str]:
        """Get a proxy URL based on configuration."""
        if not self.enabled:
            return None

        # Single proxy mode
        if self.proxy_url and not self.proxy_pool:
            return self._format_proxy_url(self.proxy_url)

        # Pool mode
        if self.proxy_pool:
            return self._get_pool_proxy()

        return None

    def _get_pool_proxy(self) -> Optional[str]:
        """Get next proxy from pool based on rotation strategy."""
        available = [p for p in self.proxy_pool if self._pool_failures.get(p, 0) < 3]
        if not available:
            # Reset failures and try again
            self._pool_failures.clear()
            available = self.proxy_pool

        if not available:
            return None

        if self.rotation_strategy == "random":
            proxy = random.choice(available)
        elif self.rotation_strategy == "least_used":
            proxy = min(available, key=lambda p: self._pool_usage.get(p, 0))
        else:  # round_robin
            self._pool_index = self._pool_index % len(available)
            proxy = available[self._pool_index]
            self._pool_index += 1

        self._pool_usage[proxy] = self._pool_usage.get(proxy, 0) + 1
        return self._format_proxy_url(proxy)

    def _format_proxy_url(self, url: str) -> str:
        """Format proxy URL with credentials if needed."""
        if not self.username or not self.password:
            return url

        parsed = urlparse(url)
        if parsed.username:  # Already has credentials
            return url

        # Insert credentials
        if parsed.port:
            netloc = f"{self.username}:{self.password}@{parsed.hostname}:{parsed.port}"
        else:
            netloc = f"{self.username}:{self.password}@{parsed.hostname}"

        return f"{parsed.scheme}://{netloc}{parsed.path}"

    def mark_proxy_failed(self, proxy_url: str):
        """Mark a proxy as failed (for pool rotation)."""
        # Extract base URL without credentials
        parsed = urlparse(proxy_url)
        base_url = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port:
            base_url += f":{parsed.port}"

        for pool_proxy in self.proxy_pool:
            if base_url in pool_proxy or pool_proxy in proxy_url:
                self._pool_failures[pool_proxy] = self._pool_failures.get(pool_proxy, 0) + 1
                logger.warning(f"Proxy marked as failed: {pool_proxy} (failures: {self._pool_failures[pool_proxy]})")
                break

    def reset_failures(self):
        """Reset all failure counts."""
        self._pool_failures.clear()

    def get_requests_proxies(self) -> Optional[Dict[str, str]]:
        """Get proxy dict for requests library."""
        proxy = self.get_proxy()
        if not proxy:
            return None
        return {"http": proxy, "https": proxy}

    def to_dict(self) -> Dict[str, Any]:
        """Serialize config to dict for storage."""
        return {
            "enabled": self.enabled,
            "proxy_url": self.proxy_url,
            "proxy_pool": self.proxy_pool,
            "rotation_strategy": self.rotation_strategy,
            "username": self.username,
            "password": self.password,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProxyConfig":
        """Create config from dict."""
        return cls(
            enabled=data.get("enabled", False),
            proxy_url=data.get("proxy_url"),
            proxy_pool=data.get("proxy_pool", []),
            rotation_strategy=data.get("rotation_strategy", "round_robin"),
            username=data.get("username"),
            password=data.get("password"),
        )


class HttpClientConfig:
    """Central HTTP client configuration."""

    def __init__(
        self,
        cookie_jar_path: Optional[Path] = None,
        proxy_config: Optional[ProxyConfig] = None,
        request_delay_ms: int = 0,
        max_connections_per_host: int = 10,
        max_total_connections: int = 100,
        connect_timeout: int = 60,
        read_timeout: int = 120,
    ):
        self.cookie_jar_path = cookie_jar_path
        self.proxy_config = proxy_config or ProxyConfig()
        self.request_delay_ms = request_delay_ms
        self.max_connections_per_host = max_connections_per_host
        self.max_total_connections = max_total_connections
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

        # Cookie jar for persistence
        self._cookie_jar: Optional[LWPCookieJar] = None
        self._last_request_time: float = 0

    def get_cookie_jar(self) -> LWPCookieJar:
        """Get or create the cookie jar."""
        if self._cookie_jar is None:
            self._cookie_jar = LWPCookieJar()
            if self.cookie_jar_path and self.cookie_jar_path.exists():
                try:
                    self._cookie_jar.load(str(self.cookie_jar_path), ignore_discard=True, ignore_expires=True)
                    logger.info(f"Loaded {len(self._cookie_jar)} cookies from {self.cookie_jar_path}")
                except Exception as e:
                    logger.warning(f"Failed to load cookies: {e}")
        return self._cookie_jar

    def save_cookies(self):
        """Save cookies to disk."""
        if self._cookie_jar is not None and self.cookie_jar_path:
            try:
                self.cookie_jar_path.parent.mkdir(parents=True, exist_ok=True)
                self._cookie_jar.save(str(self.cookie_jar_path), ignore_discard=True, ignore_expires=True)
                logger.debug(f"Saved {len(self._cookie_jar)} cookies to {self.cookie_jar_path}")
            except Exception as e:
                logger.warning(f"Failed to save cookies: {e}")

    def apply_request_delay(self):
        """Apply configured delay between requests."""
        if self.request_delay_ms > 0:
            elapsed = (time.time() - self._last_request_time) * 1000
            if elapsed < self.request_delay_ms:
                sleep_time = (self.request_delay_ms - elapsed) / 1000
                time.sleep(sleep_time)
        self._last_request_time = time.time()

    async def apply_request_delay_async(self):
        """Apply configured delay between requests (async version)."""
        import asyncio
        if self.request_delay_ms > 0:
            elapsed = (time.time() - self._last_request_time) * 1000
            if elapsed < self.request_delay_ms:
                sleep_time = (self.request_delay_ms - elapsed) / 1000
                await asyncio.sleep(sleep_time)
        self._last_request_time = time.time()


class HttpClient:
    """
    Centralized HTTP client factory.

    Creates and configures both sync (requests) and async (aiohttp) sessions
    with shared configuration for headers, cookies, and proxies.
    """

    def __init__(self, config: Optional[HttpClientConfig] = None):
        self.config = config or HttpClientConfig()
        self._sync_session: Optional[requests.Session] = None
        self._async_session: Optional[aiohttp.ClientSession] = None

    def create_sync_session(self, headers: Optional[Dict[str, str]] = None) -> requests.Session:
        """
        Create a configured requests.Session for synchronous HTTP.

        Args:
            headers: Optional headers to use (defaults to API_HEADERS)

        Returns:
            Configured requests.Session
        """
        session = requests.Session()

        # Set headers
        session.headers.update(headers or API_HEADERS)

        # Set cookie jar
        session.cookies = self.config.get_cookie_jar()

        # Set proxy if configured
        if self.config.proxy_config.enabled:
            proxies = self.config.proxy_config.get_requests_proxies()
            if proxies:
                session.proxies.update(proxies)
                logger.info(f"Sync session using proxy: {proxies.get('https', proxies.get('http'))}")

        self._sync_session = session
        return session

    def get_sync_session(self) -> requests.Session:
        """Get existing session or create new one."""
        if self._sync_session is None:
            return self.create_sync_session()
        return self._sync_session

    async def create_async_session(
        self,
        headers: Optional[Dict[str, str]] = None,
        total_timeout: Optional[int] = None,
    ) -> aiohttp.ClientSession:
        """
        Create a configured aiohttp.ClientSession for async HTTP.

        Args:
            headers: Optional headers to use (defaults to MEDIA_HEADERS)
            total_timeout: Total request timeout (None for no limit)

        Returns:
            Configured aiohttp.ClientSession
        """
        # Check if we need a SOCKS proxy connector
        proxy_url = self.config.proxy_config.get_proxy() if self.config.proxy_config.enabled else None
        use_socks = proxy_url and proxy_url.startswith('socks')
        
        if use_socks and SOCKS_SUPPORT:
            # Use ProxyConnector for SOCKS proxies
            logger.debug(f"[SOCKS] Creating ProxyConnector with proxy_url: {proxy_url!r}")
            if not proxy_url or not isinstance(proxy_url, str):
                logger.error(f"[SOCKS] Invalid proxy_url passed to ProxyConnector: {proxy_url!r}")
                raise ValueError(f"Invalid proxy_url for SOCKS: {proxy_url!r}")
            connector = ProxyConnector.from_url(
                proxy_url,
                limit=self.config.max_total_connections,
                limit_per_host=self.config.max_connections_per_host,
                ttl_dns_cache=300,
                rdns=False,
                family=socket.AF_INET,
                force_close=False,
            )
            logger.info(f"Async session using SOCKS proxy: {proxy_url}")
        elif use_socks and not SOCKS_SUPPORT:
            logger.warning("SOCKS proxy requested but aiohttp-socks not installed. Install via: pip install aiohttp-socks")
            # Fallback to regular connector
            connector_kwargs = {
                "limit": self.config.max_total_connections,
                "limit_per_host": self.config.max_connections_per_host,
                "ttl_dns_cache": 300,
                "family": socket.AF_INET,
                "force_close": False,
            }
            connector = TCPConnector(**connector_kwargs)
        else:
            # Regular TCP connector for HTTP proxies or no proxy
            connector_kwargs = {
                "limit": self.config.max_total_connections,
                "limit_per_host": self.config.max_connections_per_host,
                "ttl_dns_cache": 300,
                "family": socket.AF_INET,
                "force_close": False,
            }
            connector = TCPConnector(**connector_kwargs)

        # Configure timeout
        timeout = ClientTimeout(
            total=total_timeout,
            connect=self.config.connect_timeout,
            sock_read=self.config.read_timeout,
        )

        # Build session
        session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers=headers or MEDIA_HEADERS,
            cookie_jar=self._create_aiohttp_cookie_jar(),
            raise_for_status=False,
        )

        self._async_session = session
        return session

    def _create_aiohttp_cookie_jar(self) -> aiohttp.CookieJar:
        """
        Create an aiohttp cookie jar and populate from shared cookies.

        Note: aiohttp uses its own CookieJar implementation, so we need to
        transfer cookies from the requests cookie jar.
        """
        from yarl import URL

        jar = aiohttp.CookieJar(unsafe=True)  # unsafe=True allows cookies for IP addresses

        # Transfer cookies from requests jar
        requests_jar = self.config.get_cookie_jar()
        for cookie in requests_jar:
            # Build a proper URL for the cookie's domain
            domain = cookie.domain or "localhost"
            if domain.startswith("."):
                domain = domain[1:]  # Remove leading dot
            response_url = URL(f"https://{domain}/")
            jar.update_cookies({cookie.name: cookie.value}, response_url=response_url)

        return jar

    async def close_async_session(self):
        """Close the async session and save cookies."""
        if self._async_session:
            # Extract cookies before closing
            self._sync_cookies_from_async()
            await self._async_session.close()
            self._async_session = None

    def _sync_cookies_from_async(self):
        """Sync cookies from async session back to shared cookie jar."""
        if self._async_session and self._async_session.cookie_jar:
            # aiohttp cookie jar doesn't easily expose cookies, but we can
            # rely on Set-Cookie headers being captured in the sync session
            # if they share the same endpoints
            pass

    def close(self):
        """Close all sessions and save state."""
        if self._sync_session:
            self._sync_session.close()
            self._sync_session = None

        self.config.save_cookies()

    def update_proxy_config(self, proxy_config: ProxyConfig):
        """Update proxy configuration and apply to existing sessions."""
        self.config.proxy_config = proxy_config

        # Update sync session if it exists
        if self._sync_session:
            if proxy_config.enabled:
                proxies = proxy_config.get_requests_proxies()
                if proxies:
                    self._sync_session.proxies.update(proxies)
            else:
                self._sync_session.proxies.clear()


def create_http_client_from_settings(db_manager) -> HttpClient:
    """
    Create HttpClient configured from database settings.

    Args:
        db_manager: DatabaseManager instance to read settings from

    Returns:
        Configured HttpClient instance
    """
    # Read proxy settings
    proxy_enabled = db_manager.get_config("proxy_enabled", "false") == "true"
    proxy_url = db_manager.get_config("proxy_url", "")
    proxy_pool_json = db_manager.get_config("proxy_pool", "[]")
    proxy_rotation = db_manager.get_config("proxy_rotation_strategy", "round_robin")
    proxy_username = db_manager.get_config("proxy_username", "")
    proxy_password = db_manager.get_config("proxy_password", "")

    # Parse proxy pool
    try:
        proxy_pool = json.loads(proxy_pool_json) if proxy_pool_json else []
    except json.JSONDecodeError:
        proxy_pool = []

    proxy_config = ProxyConfig(
        enabled=proxy_enabled,
        proxy_url=proxy_url if proxy_url else None,
        proxy_pool=proxy_pool,
        rotation_strategy=proxy_rotation,
        username=proxy_username if proxy_username else None,
        password=proxy_password if proxy_password else None,
    )

    # Read other settings
    request_delay = db_manager.get_config("request_delay_ms", 0)
    max_conn_per_host = db_manager.get_config("max_connections_per_host", 10)
    max_total_conn = db_manager.get_config("max_total_connections", 100)

    # Cookie jar path
    data_dir = Path(db_manager.db_path).parent
    cookie_jar_path = data_dir / "cookies.txt"

    config = HttpClientConfig(
        cookie_jar_path=cookie_jar_path,
        proxy_config=proxy_config,
        request_delay_ms=int(request_delay),
        max_connections_per_host=int(max_conn_per_host),
        max_total_connections=int(max_total_conn),
    )

    return HttpClient(config)


async def test_proxy_connection(proxy_url: str, timeout: int = 10) -> Tuple[bool, str, Optional[str]]:
    """
    Test if a proxy is working by making a request to a test endpoint.

    Args:
        proxy_url: The proxy URL to test
        timeout: Connection timeout in seconds

    Returns:
        Tuple of (success, message, detected_ip)
    """
    import aiohttp

    # Use httpbin.org which is more permissive for testing
    test_url = "https://httpbin.org/ip"

    try:
        # Use ProxyConnector for SOCKS proxies
        use_socks = proxy_url.startswith('socks')
        if use_socks and SOCKS_SUPPORT:
            connector = ProxyConnector.from_url(proxy_url, rdns=False, family=socket.AF_INET)
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as session:
                async with session.get(test_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        ip = data.get("origin", "unknown").split(',')[0].strip()  # httpbin returns "origin"
                        return True, f"Connected successfully", ip
                    else:
                        return False, f"HTTP {response.status}", None
        elif use_socks and not SOCKS_SUPPORT:
            return False, "SOCKS proxy requires aiohttp-socks: pip install aiohttp-socks", None
        else:
            # HTTP proxy - use regular connector with proxy parameter
            connector = aiohttp.TCPConnector(family=socket.AF_INET)
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as session:
                async with session.get(test_url, proxy=proxy_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        ip = data.get("origin", "unknown").split(',')[0].strip()
                        return True, f"Connected successfully", ip
                    else:
                        return False, f"HTTP {response.status}", None
    except aiohttp.ClientProxyConnectionError as e:
        return False, f"Proxy connection failed: {e}", None
    except aiohttp.ClientConnectorError as e:
        return False, f"Connection error: {e}", None
    except asyncio.TimeoutError:
        return False, "Connection timed out", None
    except Exception as e:
        return False, f"Error: {e}", None


def test_proxy_connection_sync(proxy_url: str, timeout: int = 10) -> Tuple[bool, str, Optional[str]]:
    """
    Test if a proxy is working (synchronous version).

    Args:
        proxy_url: The proxy URL to test
        timeout: Connection timeout in seconds

    Returns:
        Tuple of (success, message, detected_ip)
    """
    test_url = "https://httpbin.org/ip"

    try:
        proxies = {"http": proxy_url, "https": proxy_url}
        response = requests.get(test_url, proxies=proxies, timeout=timeout)

        if response.status_code == 200:
            data = response.json()
            ip = data.get("origin", "unknown").split(',')[0].strip()
            return True, "Connected successfully", ip
        else:
            return False, f"HTTP {response.status_code}", None
    except requests.exceptions.ProxyError as e:
        return False, f"Proxy error: {e}", None
    except requests.exceptions.ConnectTimeout:
        return False, "Connection timed out", None
    except requests.exceptions.ConnectionError as e:
        return False, f"Connection error: {e}", None
    except Exception as e:
        return False, f"Error: {e}", None


# Singleton instance (initialized by CoreContext)
_http_client: Optional[HttpClient] = None


def get_http_client() -> Optional[HttpClient]:
    """Get the global HTTP client instance."""
    return _http_client


def set_http_client(client: HttpClient):
    """Set the global HTTP client instance."""
    global _http_client
    _http_client = client
