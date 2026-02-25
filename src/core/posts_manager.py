from __future__ import annotations

from typing import Any, Dict, List, Optional
from pathlib import Path
from urllib.parse import urlparse
import logging

from src.core.api import CoomerClient, KemonoClient
from src.core.api.contracts.posts import PostsAPIClient
from src.core.dto.creator import CreatorDTO
from src.core.dto.file import FileDTO
from src.core.dto.post import PostDTO, PostsPageDTO, PostLocatorDTO
from src.core.dto.popular import PopularPostsPageDTO
from src.core.dto.popular import PopularPostsInfoDTO
from src.core.media_manager import MediaManager


POSTS_PAGE_LIMIT = 50

logger = logging.getLogger(__name__)


class PostsManager:
    """
    Authoritative domain manager for all post retrieval.

    Guarantees:
    - Enforces pagination invariants
    - Normalizes API quirks
    - Returns DTOs only
    - Zero UI logic
    """

    def __init__(
        self,
        coomer: Optional[PostsAPIClient] = None,
        kemono: Optional[PostsAPIClient] = None,
    ):
        self._coomer = coomer or CoomerClient()
        self._kemono = kemono or KemonoClient()

    # ---------------------------------------------------------
    # Creator Posts
    # ---------------------------------------------------------

    def get_creator_posts(
        self,
        platform: str,
        service: str,
        creator_id: str,
        *,
        offset: int = 0,
        query: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> PostsPageDTO:
        self._validate_offset(offset)

        data = self._client(platform).get_creator_posts(
            service=service,
            creator_id=creator_id,
            offset=offset,
            query=query,
            tags=tags,
        )

        posts = self._posts_from_raw(data.get("posts", []), platform=platform)
        creator = self._creator_from_raw(data.get("creator"), platform)

        props = data.get("props") if isinstance(data, dict) else None
        total = None
        if isinstance(props, dict) and props.get("count") is not None:
            total = int(props.get("count") or 0)
        elif data.get("count") is not None:
            total = int(data.get("count") or 0)
        else:
            total = len(posts)

        limit = int(data.get("limit") or POSTS_PAGE_LIMIT)
        true_count = int(data.get("true_count") or total)

        return PostsPageDTO(
            creator=creator,
            offset=offset,
            limit=limit,
            count=total,
            true_count=true_count,
            posts=posts,
        )

    # ---------------------------------------------------------
    # Post Detail
    # ---------------------------------------------------------

    def get_post(
        self,
        platform: str,
        service: str,
        post_id: str,
        *,
        creator_id: Optional[str] = None,
    ) -> PostDTO:
        creator_id = creator_id or None
        data = self._client(platform).get_post(
            service=service,
            post_id=post_id,
            creator_id=creator_id,
        )
        post = self._post_from_raw(data, platform=platform)
        if post is None:
            raise ValueError("Invalid post payload")
        return post

    # ---------------------------------------------------------
    # Global Posts
    # ---------------------------------------------------------

    def get_all_posts(
        self,
        platform: str,
        *,
        offset: int = 0,
        query: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> PostsPageDTO:
        self._validate_offset(offset)

        logger.debug(f"get_all_posts called with tags={tags}")

        data = self._client(platform).get_posts(
            offset=offset,
            query=query,
            tags=tags,
        )

        posts = self._posts_from_raw(data.get("posts", []), platform=platform)
        count = int(data.get("count") or 0)
        true_count = int(data.get("true_count") or count)

        return PostsPageDTO(
            creator=None,
            offset=offset,
            limit=POSTS_PAGE_LIMIT,
            count=count,
            true_count=true_count,
            posts=posts,
        )

    # ---------------------------------------------------------
    # Random
    # ---------------------------------------------------------

    def get_random_post(self, platform: str) -> PostLocatorDTO:
        """
        Returns a navigation target, not a post payload.
        """
        data = self._client(platform).get_random_post()
        if isinstance(data, dict) and "artist_id" in data and "creator_id" not in data:
            data = dict(data)
            data["creator_id"] = data.pop("artist_id")
        return PostLocatorDTO(**data)

    # ---------------------------------------------------------
    # Popular
    # ---------------------------------------------------------

    def get_popular_posts(
        self,
        platform: str,
        *,
        date: Optional[str] = None,
        period: str = "recent",
        offset: int = 0,
    ) -> PopularPostsPageDTO:
        self._validate_offset(offset)

        data = self._client(platform).get_popular_posts(
            date=date,
            period=period,
            offset=offset,
        )

        posts = self._posts_from_raw(data.get("posts", []), platform=platform)
        props = data.get("props") or {}
        limit = int(props.get("limit") or POSTS_PAGE_LIMIT)
        count = int(props.get("count") or data.get("count") or len(posts))
        info_raw = data.get("info") or {}

        info = PopularPostsInfoDTO(
            date=str(info_raw.get("date") or (date or "")),
            min_date=str(info_raw.get("min_date") or ""),
            max_date=str(info_raw.get("max_date") or ""),
            range_desc=str(info_raw.get("range_desc") or info_raw.get("range") or ""),
            scale=str(info_raw.get("scale") or period),
        )

        return PopularPostsPageDTO(
            info=info,
            posts=posts,
            offset=offset,
            limit=limit,
            count=count,
        )

    # ---------------------------------------------------------
    # Tags
    # ---------------------------------------------------------

    def get_tags(self, platform: str) -> List[Dict[str, Any]]:
        """Get tags with post counts for the platform"""
        return self._client(platform).get_tags()

    def get_creator_tags(self, platform: str, service: str, creator_id: str) -> List[Dict[str, Any]]:
        """Get tags with post counts for a specific creator"""
        return self._client(platform).get_creator_tags(service, creator_id)

    # ---------------------------------------------------------
    # Internal
    # ---------------------------------------------------------

    def _client(self, platform: str):
        if platform == "coomer":
            return self._coomer
        if platform == "kemono":
            return self._kemono
        raise ValueError(f"Unknown platform: {platform}")

    @staticmethod
    def _creator_from_raw(raw: Optional[Dict[str, Any]], platform: str) -> Optional[CreatorDTO]:
        if not isinstance(raw, dict):
            return None
        return CreatorDTO(
            id=str(raw.get("id")),
            service=str(raw.get("service")),
            name=str(raw.get("name") or raw.get("id") or ""),
            platform=platform,
            indexed=raw.get("indexed"),
            updated=raw.get("updated"),
            public_id=raw.get("public_id"),
            relation_id=raw.get("relation_id"),
            favorited=raw.get("favorited"),
            post_count=raw.get("post_count"),
            dm_count=raw.get("dm_count"),
            share_count=raw.get("share_count"),
            chat_count=raw.get("chat_count"),
            display_href=raw.get("display_href"),
        )

    @staticmethod
    def _suffix_from_path(value: str) -> str:
        if not value:
            return ""
        try:
            parsed = urlparse(value)
            if parsed.scheme in {"http", "https", "file"}:
                value = parsed.path
        except Exception:
            pass
        return Path(value).suffix.lower()

    @staticmethod
    def _file_from_raw(raw: Optional[Dict[str, Any]]) -> Optional[FileDTO]:
        if raw is None:
            return None
        logger.debug(f"_file_from_raw: raw type={type(raw).__name__}, raw={str(raw)[:200]}")
        if isinstance(raw, str):
            path = raw
            logger.debug(f"_file_from_raw: string path={path} (len={len(path)})")
            name = Path(path).name
            ext = PostsManager._suffix_from_path(path)
            is_image = ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
            is_video = ext in {".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v"}
            return FileDTO(
                name=name,
                path=path,
                size=None,
                mime=None,
                width=None,
                height=None,
                duration=None,
                is_image=is_image,
                is_video=is_video,
            )
        if not isinstance(raw, dict):
            return None
        mime = raw.get("mime")
        path = str(
            raw.get("path")
            or raw.get("file_path")
            or raw.get("url")
            or raw.get("src")
            or ""
        )
        logger.debug(f"_file_from_raw: dict path={path} (len={len(path) if path else 0})")
        server = raw.get("server") or raw.get("host") or ""
        if server and path and not str(path).startswith("http"):
            server = str(server).rstrip("/")
            normalized = str(path)
            if not normalized.startswith("/"):
                normalized = f"/{normalized}"
            if not normalized.startswith("/data/"):
                normalized = f"/data{normalized}"
            path = f"{server}{normalized}"
        name = str(raw.get("name") or raw.get("filename") or "")
        if not path and not name:
            return None
        ext = PostsManager._suffix_from_path(path or name)
        is_image = bool(raw.get("is_image")) if "is_image" in raw else str(mime or "").startswith("image/")
        is_video = bool(raw.get("is_video")) if "is_video" in raw else str(mime or "").startswith("video/")
        if not is_image and not is_video:
            if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
                is_image = True
            elif ext in {".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v"}:
                is_video = True
        return FileDTO(
            name=name,
            path=path,
            size=raw.get("size"),
            mime=mime,
            width=raw.get("width"),
            height=raw.get("height"),
            duration=raw.get("duration"),
            is_image=is_image,
            is_video=is_video,
        )

    def _post_from_raw(self, raw: Any, platform: str) -> Optional[PostDTO]:
        if not isinstance(raw, dict):
            return None
        post_raw = raw.get("post") if isinstance(raw.get("post"), dict) else raw

        def _pick(key: str, default: Any = None):
            value = post_raw.get(key) if isinstance(post_raw, dict) else None
            if value is not None:
                return value
            return raw.get(key, default)

        file_dto = self._file_from_raw(_pick("file"))
        attachments_raw = _pick("attachments", []) or []
        if not isinstance(attachments_raw, list):
            attachments_raw = []
        attachments = [
            f for f in (self._file_from_raw(a) for a in attachments_raw) if f is not None
        ]
        previews_raw = _pick("previews", []) or []
        thumbnail_url = self._thumbnail_url_from_previews(previews_raw, platform=platform)

        return PostDTO(
            id=str(_pick("id")),
            service=str(_pick("service")),
            user_id=str(
                _pick("creator_id")
                or _pick("user_id")
                or _pick("user")
                or ""
            ),
            title=_pick("title"),
            substring=_pick("substring"),
            content=_pick("content"),
            added=_pick("added") or "",
            published=_pick("published"),
            edited=_pick("edited"),
            shared_file=bool(_pick("shared_file", False)),
            embed=_pick("embed"),
            file=file_dto,
            attachments=attachments,
            thumbnail_url=thumbnail_url,
        )

    def _posts_from_raw(self, items: Any, *, platform: str) -> List[PostDTO]:
        posts: List[PostDTO] = []
        if not isinstance(items, list):
            return posts

        for raw in items:
            post = self._post_from_raw(raw, platform)
            if post is not None:
                posts.append(post)

        return posts

    def _thumbnail_url_from_previews(
        self,
        previews_raw: Any,
        *,
        platform: str,
    ) -> Optional[str]:
        if not isinstance(previews_raw, list):
            return None
        for raw in previews_raw:
            file_dto = self._file_from_raw(raw)
            if not file_dto or not file_dto.path:
                continue
            path = str(file_dto.path)
            if path.startswith("http"):
                return path
            return MediaManager.build_media_url(platform, path)
        return None

    @staticmethod
    def _validate_offset(offset: int) -> None:
        if offset < 0 or offset % POSTS_PAGE_LIMIT != 0:
            raise ValueError(
                f"offset must be >= 0 and a multiple of {POSTS_PAGE_LIMIT}"
            )
