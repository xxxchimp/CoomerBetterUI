1. BaseAPIClient: Locked Responsibilities

BaseAPIClient is responsible for transport + normalization only.

It must:

Own HTTP session logic

Own retry / timeout / headers

Normalize API quirks

Return plain Python data (dicts)

It must not:

Cache UI state

Touch Qt

Touch filesystem

Know about thumbnails, downloads, or DB

Know about pagination UI or infinite scroll

2. Public Methods (Final Surface)

These methods are the contract.
Anything not listed is internal and private.

Creators
get_all_creators() -> list[dict]
get_creator(service: str, creator_id: str) -> dict
get_creator_posts(service: str, creator_id: str, offset: int, tags: list[str] | None = None) -> dict

Post detail
get_post(service: str, post_id: str, creator_id: str | None = None) -> dict


Notes:

get_all_creators() returns all creators (20MB+)

No pagination abstraction here

Sorting/filtering is caller responsibility

Posts (Creator-independent)
get_posts(
    *,
    offset: int = 0,
    query: str | None = None,
    tags: list[str] | None = None,
) -> dict


Rules:

Offset is enforced internally (multiple of 50)

count and true_count must be preserved

Same method used for:

All posts

Search results

Platform Feeds
get_popular_posts(date: str | None = None, period: str = "recent", offset: int = 0) -> dict
get_random_post() -> dict
get_tags() -> list[str]

3. DTO Shape Is Part of the Lock

The shape of returned data is part of the contract.

PostsPageDTO (manager output example)
{
    "posts": list[PostDTO],
    "count": int,        # capped (e.g. 50000)
    "true_count": int,   # real platform count
    "offset": int,
}


UI must not infer pagination behavior from raw API responses.
Managers own DTO creation and pagination invariants.

4. Error Contract (Critical)

All platform-specific failures must be normalized.

class APIError(Exception):
    message: str
    status_code: int | None
    platform: str


UI and managers only catch APIError.

Never leak:

requests exceptions

HTTP status enums

JSON decode errors

5. What Is Explicitly Forbidden

The following are contract violations:

UI importing coomer.py or kemono.py directly

UI constructing URLs

UI knowing endpoint paths

UI parsing API JSON manually

UI depending on undocumented response fields

If it's not in BaseAPIClient, the UI doesn't get it.

6. Enforcement Techniques (Practical)

You should do at least two of these:

A. Naming + Visibility

All helpers prefixed with _

Only documented methods remain public

B. Type Hints Everywhere

Return concrete DTOs

Makes violations obvious immediately

C. One Import Rule

UI imports only:

from src.core import CoreContext

from src.core.creators_manager import CreatorsManager

from src.core.posts_manager import PostsManager

from src.core.download_manager import DownloadManager

from src.core.thumbnails import get_thumbnail_manager, ThumbnailHandle


Never deeper.

7. What This Unlocks Next

Once this is locked, you can safely:

Refactor CreatorsManager

Batch and cache thumbnails correctly

Rebuild gallery views without touching API code

Add platform-specific hacks without UI churn
