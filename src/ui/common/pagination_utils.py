from __future__ import annotations

from typing import Optional, Tuple


def compute_posts_pagination(
    *,
    offset: int,
    posts_len: int,
    total_count: int,
    creator_post_count: Optional[int] = None,
    max_offset: Optional[int] = None,
) -> Tuple[int, int]:
    """
    Return (total_pages, effective_total_count).

    If creator_post_count is provided, it takes precedence over total_count.
    When no reliable total exists, estimate pages based on page size (50).
    
    Args:
        offset: Current offset in the post list
        posts_len: Number of posts returned in current page
        total_count: Total count from API (may be unreliable for /v1/posts)
        creator_post_count: Post count from creator metadata (more reliable)
        max_offset: Maximum allowed offset (e.g., 50000 for /v1/posts API limit)
    """
    effective_total = total_count
    if creator_post_count is not None:
        effective_total = int(creator_post_count)

    if effective_total > 0:
        total_pages = (effective_total + 49) // 50
        
        # Cap pages if max_offset is specified (API limitation)
        if max_offset is not None:
            max_pages = (max_offset // 50) + 1  # +1 because pages are 0-indexed
            if total_pages > max_pages:
                total_pages = max_pages
                # Adjust effective_total to match capped pages
                effective_total = max_pages * 50
        
        return total_pages, effective_total

    # No reliable total - estimate based on current page
    page = offset // 50
    
    # If we have max_offset, don't estimate beyond it
    if max_offset is not None and offset >= max_offset:
        # At or beyond max offset - this is the last page
        total_pages = (max_offset // 50) + 1
        return total_pages, 0
    
    # Normal estimation: current page + (1 or 2 more based on whether we got full page)
    total_pages = page + (2 if posts_len >= 50 else 1)
    
    # Cap at max_offset if specified
    if max_offset is not None:
        max_pages = (max_offset // 50) + 1
        total_pages = min(total_pages, max_pages)
    
    return total_pages, 0
