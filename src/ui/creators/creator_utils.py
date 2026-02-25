"""
Creator Resolution Utilities

Centralized utilities for resolving creator names and display information.
Eliminates duplicate creator lookup logic across UI components.
"""
from __future__ import annotations

from typing import Optional, Callable, Dict, Any


# Type alias for creator lookup callback
CreatorLookupFunc = Callable[[str, str, str], Optional[str]]


def resolve_creator_name(
    creator_lookup: Optional[CreatorLookupFunc],
    platform: str,
    service: str,
    creator_id: str,
    fallback: Optional[str] = None,
) -> str:
    """
    Resolve creator display name using lookup callback with fallback logic.

    This is the centralized implementation of creator name resolution used
    throughout the UI. It handles all edge cases and provides consistent
    fallback behavior.

    Args:
        creator_lookup: Optional callback function(platform, service, creator_id) -> name
        platform: Platform identifier (e.g., "coomer", "kemono")
        service: Service identifier (e.g., "patreon", "onlyfans")
        creator_id: Creator ID to look up
        fallback: Optional fallback value (defaults to creator_id)

    Returns:
        Creator display name or fallback value

    Example:
        >>> def lookup(platform, service, creator_id):
        ...     return "John Doe" if creator_id == "123" else None
        >>> resolve_creator_name(lookup, "coomer", "patreon", "123")
        "John Doe"
        >>> resolve_creator_name(lookup, "coomer", "patreon", "999")
        "999"
        >>> resolve_creator_name(None, "coomer", "patreon", "123", "Unknown")
        "Unknown"
    """
    # Default fallback is creator_id
    if fallback is None:
        fallback = creator_id or "Unknown"

    # No lookup function provided
    if not creator_lookup:
        return fallback

    # Missing required parameters
    if not platform or not service or not creator_id:
        return fallback

    # Attempt lookup
    try:
        name = creator_lookup(platform, service, creator_id)
        if name and isinstance(name, str):
            return name
    except Exception:
        # Lookup failed, use fallback
        pass

    return fallback


def resolve_creator_from_manager(
    creators_manager,
    platform: str,
    service: str,
    creator_id: str,
    fallback: Optional[str] = None,
) -> str:
    """
    Resolve creator name directly from CreatorsManager instance.

    Convenience wrapper around resolve_creator_name() that extracts the name
    from a CreatorsManager.get_creator() result.

    Args:
        creators_manager: CreatorsManager instance
        platform: Platform identifier
        service: Service identifier
        creator_id: Creator ID to look up
        fallback: Optional fallback value (defaults to creator_id)

    Returns:
        Creator display name or fallback value

    Example:
        >>> name = resolve_creator_from_manager(
        ...     mgr, "coomer", "patreon", "123", fallback="Unknown"
        ... )
    """
    if fallback is None:
        fallback = creator_id or "Unknown"

    if not platform or not service or not creator_id or not creators_manager:
        return fallback

    try:
        creator = creators_manager.get_creator(platform, service, creator_id)
    except Exception:
        return fallback

    if not creator:
        return fallback

    # Extract name from creator object or dict
    name = None

    # Try attribute access (dataclass/object)
    if hasattr(creator, "name"):
        name = getattr(creator, "name", None)

    # Try dict access
    if not name and isinstance(creator, dict):
        name = creator.get("name")

    if name and isinstance(name, str):
        return name

    return fallback


def create_creator_data_dict(
    creator_id: str,
    service: str,
    name: Optional[str] = None,
    platform: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create standardized creator data dictionary.

    Used for creator selection/navigation throughout the UI.
    Ensures consistent data structure.

    Args:
        creator_id: Creator ID
        service: Service name
        name: Optional display name (defaults to creator_id)
        platform: Optional platform identifier

    Returns:
        Standardized creator data dict

    Example:
        >>> data = create_creator_data_dict("123", "patreon", "John Doe")
        >>> data
        {"id": "123", "creator_id": "123", "service": "patreon", "name": "John Doe"}
    """
    data = {
        "id": creator_id,
        "creator_id": creator_id,
        "service": service,
        "name": name or creator_id,
    }

    if platform:
        data["platform"] = platform

    return data


def format_creator_service_label(
    creator_name: str,
    service: str,
    separator: str = " • ",
) -> str:
    """
    Format creator and service as display label.

    Args:
        creator_name: Creator display name
        service: Service name
        separator: Separator string (default: " • ")

    Returns:
        Formatted label string

    Example:
        >>> format_creator_service_label("John Doe", "patreon")
        "John Doe • patreon"
    """
    return f"{creator_name}{separator}{service}"


class CreatorResolver:
    """
    Stateful creator name resolver with caching.

    Wraps a creator lookup function and caches results to avoid
    redundant lookups for the same creator.
    """

    def __init__(self, lookup_func: Optional[CreatorLookupFunc] = None):
        """
        Initialize resolver with optional lookup function.

        Args:
            lookup_func: Optional callback(platform, service, creator_id) -> name
        """
        self._lookup = lookup_func
        self._cache: Dict[tuple, str] = {}

    def set_lookup(self, lookup_func: CreatorLookupFunc) -> None:
        """Set or update the lookup function."""
        self._lookup = lookup_func
        self._cache.clear()  # Clear cache when lookup changes

    def resolve(
        self,
        platform: str,
        service: str,
        creator_id: str,
        fallback: Optional[str] = None,
    ) -> str:
        """
        Resolve creator name with caching.

        Args:
            platform: Platform identifier
            service: Service identifier
            creator_id: Creator ID
            fallback: Optional fallback value

        Returns:
            Creator display name or fallback
        """
        # Create cache key
        cache_key = (platform, service, creator_id)

        # Check cache
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Resolve and cache
        result = resolve_creator_name(
            self._lookup,
            platform,
            service,
            creator_id,
            fallback=fallback,
        )

        self._cache[cache_key] = result
        return result

    def clear_cache(self) -> None:
        """Clear the name resolution cache."""
        self._cache.clear()

    def cache_size(self) -> int:
        """Get number of cached entries."""
        return len(self._cache)
