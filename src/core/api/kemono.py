from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional
from datetime import datetime
import logging

from .base import APIError, BaseAPIClient


class KemonoClient(BaseAPIClient):
    BASE_URL = "https://kemono.cr/api"
    PLATFORM = "kemono"
    _logger = logging.getLogger(__name__)

    # --------------------------------------------------
    # Creators
    # --------------------------------------------------

    def get_all_creators(self) -> List[dict]:
        data = self._request(
            "GET",
            "/v1/creators",
            headers={"Accept": "text/css"},
        )
        if not isinstance(data, list):
            raise APIError(f"{self.PLATFORM} creators response not a list")
        return [self.normalize_creator(c) for c in data]

    def stream_all_creators(self) -> Iterable[dict]:
        """
        Stream creators from API, yielding normalized dicts as they're parsed.
        
        This is more memory-efficient than get_all_creators() for large responses
        and allows processing to start before the full response is received.
        """
        for raw in self._request_stream_json_array(
            "GET",
            "/v1/creators",
            headers={"Accept": "text/css"},
        ):
            yield self.normalize_creator(raw)

    def get_creator(self, service: str, creator_id: str) -> dict:
        data = self._request("GET", f"/v1/{service}/user/{creator_id}/profile")
        if not isinstance(data, dict):
            raise APIError(f"{self.PLATFORM} creator response not an object")
        return self.normalize_creator(data)

    def get_creator_posts(
        self,
        *,
        service: str,
        creator_id: str,
        offset: int,
        query: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"o": offset}
        if query:
            params["q"] = query
        if tags:
            params["tag"] = tags
        data = self._request(
            "GET",
            f"/v1/{service}/user/{creator_id}/posts",
            params=params,
        )
        if not isinstance(data, (dict, list)):
            raise APIError(f"{self.PLATFORM} creator_posts response not an object")

        props = data.get("props", {}) if isinstance(data, dict) else {}
        if isinstance(data, list):
            results = data
        elif isinstance(data, dict):
            results = data.get("results") or data.get("posts") or []
        else:
            results = []

        posts = [self.normalize_post(p) for p in results if isinstance(p, dict)]

        # Best-effort creator object derived from props (+ artist if present)
        artist = props.get("artist") or {}
        creator_norm = {
            "id": props.get("id") or creator_id,
            "service": props.get("service") or service,
            "name": props.get("name"),
            "indexed": artist.get("indexed"),
            "updated": artist.get("updated"),
            "public_id": artist.get("public_id"),
            "relation_id": artist.get("relation_id"),
            # post_count is authoritative from profile endpoint, not posts payload
            "post_count": None,
            "display_href": (props.get("display_data") or {}).get("href"),
        }

        limit = int(props.get("limit") or 50)
        total = int(props.get("count") or 0)

        return {
            "props": props,
            "creator": creator_norm,
            "posts": posts,
            "count": len(posts),
            "limit": limit,
            "offset": offset,
            "has_more": (offset + len(posts)) < total if total else (len(posts) == limit),
        }

    def get_post(
        self,
        *,
        service: str,
        post_id: str,
        creator_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if creator_id:
            path = f"/v1/{service}/user/{creator_id}/post/{post_id}"
        else:
            path = f"/v1/{service}/post/{post_id}"

        data = self._request("GET", path)
        if not isinstance(data, dict):
            raise APIError(f"{self.PLATFORM} post response not an object")

        post_raw = data.get("post")
        if isinstance(post_raw, dict):
            return self.normalize_post(post_raw)
        return self.normalize_post(data)

    # --------------------------------------------------
    # Global posts
    # --------------------------------------------------

    def get_posts(
        self,
        *,
        offset: int,
        query: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"o": offset}
        if query:
            params["q"] = query
        if tags:
            params["tag"] = tags

        data = self._request("GET", "/v1/posts", params=params)
        if not isinstance(data, dict):
            raise APIError(f"{self.PLATFORM} posts response not an object")

        posts_raw = data.get("posts", []) or []
        posts = [self.normalize_post(p) for p in posts_raw if isinstance(p, dict)]

        count = int(data.get("count") or 0)  # capped (e.g., 50000)
        true_count = int(data.get("true_count") or count)

        return {
            "posts": posts,
            "count": count,
            "true_count": true_count,
            "offset": offset,
            "has_more": (offset + len(posts)) < count if count else (len(posts) == 50),
        }

    # --------------------------------------------------
    # Random / Popular / Tags
    # --------------------------------------------------

    def get_random_post(self) -> Dict[str, str]:
        data = self._request("GET", "/v1/posts/random")
        if isinstance(data, dict) and "error" in data:
            raise APIError(f"{self.PLATFORM} random post error: {data.get('error')}")
        if not isinstance(data, dict):
            raise APIError(f"{self.PLATFORM} random post response not an object")

        return {
            "service": str(data.get("service")),
            "artist_id": str(data.get("artist_id")),
            "post_id": str(data.get("post_id")),
        }

    def get_popular_posts(
        self,
        *,
        date: Optional[str] = None,
        period: str = "recent",
        offset: int = 0,
    ) -> Dict[str, Any]:
        if date:
            date_param = date
        else:
            date_param = datetime.utcnow().date().isoformat()
        if "T" not in date_param:
            date_param = f"{date_param}T00:00:00"
        params: Dict[str, Any] = {
            "period": period,
            "o": offset,
            "date": date_param,
        }
        self._logger.info(
            "Popular posts request: url=%s params=%s",
            f"{self.BASE_URL}/v1/posts/popular",
            params,
        )

        data = self._request("GET", "/v1/posts/popular", params=params)
        if not isinstance(data, dict):
            raise APIError(f"{self.PLATFORM} popular response not an object")

        results = data.get("results")
        if not results:
            results = data.get("posts", []) or []
        posts = [self.normalize_post(p) for p in results if isinstance(p, dict)]

        props = data.get("props") or {}
        total = int((props or {}).get("count") or 0)
        limit = int((props or {}).get("limit") or 50)

        return {
            "info": data.get("info"),
            "props": props,
            "posts": posts,
            "offset": offset,
            "has_more": (offset + len(posts)) < total if total else (len(posts) == limit),
        }

    def get_tags(self) -> List[Dict[str, Any]]:
        """Get tags with post counts. Returns list of {'name': str, 'count': int}"""
        data = self._request("GET", "/v1/posts/tags")
        tags = []
        
        if isinstance(data, list):
            raw_tags = data
        elif isinstance(data, dict) and "tags" in data:
            raw_tags = data.get("tags", [])
        else:
            return []
        
        for item in raw_tags:
            if isinstance(item, dict):
                tag_name = str(item.get("tag", ""))
                post_count = int(item.get("post_count", 0))
                if tag_name:
                    tags.append({"name": tag_name, "count": post_count})
            elif isinstance(item, str):
                tags.append({"name": str(item), "count": 0})
        
        return tags

    def get_creator_tags(self, service: str, creator_id: str) -> List[Dict[str, Any]]:
        """Get tags for a specific creator. Returns list of {'name': str, 'count': int}"""
        data = self._request("GET", f"/v1/{service}/user/{creator_id}/tags")
        tags = []
        
        if isinstance(data, list):
            raw_tags = data
        elif isinstance(data, dict) and "tags" in data:
            raw_tags = data.get("tags", [])
        else:
            return []
        
        for item in raw_tags:
            if isinstance(item, dict):
                tag_name = str(item.get("tag", ""))
                post_count = int(item.get("post_count", 0))
                if tag_name:
                    tags.append({"name": tag_name, "count": post_count})
            elif isinstance(item, str):
                tags.append({"name": str(item), "count": 0})
        
        return tags

    def get_recommended_creators(
        self, service: str, creator_id: str
    ) -> List[Dict[str, Any]]:
        """Get recommended creators similar to the specified creator."""
        data = self._request("GET", f"/v1/{service}/user/{creator_id}/recommended")
        if not isinstance(data, list):
            raise APIError(f"{self.PLATFORM} recommended creators response not a list")
        
        recommended = []
        for item in data:
            if isinstance(item, dict):
                # Normalize the creator data and include similarity score
                creator = self.normalize_creator(item)
                creator["similarity_score"] = float(item.get("score", 0.0))
                recommended.append(creator)
        
        return recommended

    def get_linked_creators(
        self, service: str, creator_id: str
    ) -> List[Dict[str, Any]]:
        """Get linked/related creators for the specified creator."""
        data = self._request("GET", f"/v1/{service}/user/{creator_id}/links")
        if not isinstance(data, list):
            raise APIError(f"{self.PLATFORM} linked creators response not a list")
        
        linked = []
        for item in data:
            if isinstance(item, dict):
                linked.append(self.normalize_creator(item))
        
        return linked

    def get_random_creator(self) -> Dict[str, str]:
        data = self._request("GET", "/v1/artists/random")
        if isinstance(data, dict) and "error" in data:
            raise APIError(f"{self.PLATFORM} random creator error: {data.get('error')}")
        if not isinstance(data, dict):
            raise APIError(f"{self.PLATFORM} random creator response not an object")

        return {
            "service": str(data.get("service")),
            "artist_id": str(data.get("artist_id")),
        }

    # --------------------------------------------------
    # Normalization
    # --------------------------------------------------

    def normalize_creator(self, raw: dict) -> dict:
        return {
            "id": raw.get("id"),
            "service": raw.get("service"),
            "name": raw.get("name") or raw.get("id"),
            "indexed": raw.get("indexed"),
            "updated": raw.get("updated"),
            "public_id": raw.get("public_id"),
            "relation_id": raw.get("relation_id"),
            "favorited": raw.get("favorited"),
            "post_count": int(raw.get("posts") or raw.get("post_count") or 0),
            "dm_count": raw.get("dm_count"),
            "share_count": raw.get("share_count"),
            "chat_count": raw.get("chat_count"),
            "display_href": (raw.get("display_data") or {}).get("href"),
        }

    def normalize_post(self, raw: dict) -> dict:
        if isinstance(raw.get("post"), dict):
            raw = raw.get("post") or raw

        file_raw = raw.get("file")
        attachments_raw = raw.get("attachments") or raw.get("files") or []
        videos_raw = raw.get("videos") or []
        previews_raw = raw.get("previews") or []
        if isinstance(file_raw, list) and not attachments_raw:
            attachments_raw = file_raw
            file_raw = None
        if not attachments_raw and not file_raw and videos_raw:
            attachments_raw = videos_raw
        if not attachments_raw and not file_raw and previews_raw:
            attachments_raw = previews_raw

        return {
            "id": raw.get("id"),
            "service": raw.get("service"),
            "creator_id": raw.get("user"),
            "title": raw.get("title"),
            "substring": raw.get("substring"),
            "content": raw.get("content"),
            "embed": raw.get("embed"),
            "shared_file": bool(raw.get("shared_file", False)),
            "added": raw.get("added"),
            "published": raw.get("published"),
            "edited": raw.get("edited"),
            "file": file_raw,
            "attachments": attachments_raw,
            "previews": previews_raw,
            "fav_count": raw.get("fav_count"),
        }
