from __future__ import annotations

from datetime import datetime
import logging
from typing import Dict, Iterable, List, Optional, Tuple

from src.core.api import CoomerClient, KemonoClient
from src.core.api.contracts.creators import CreatorsAPIClient
from src.core.database import DatabaseManager
from src.core.dto.creator import CreatorDTO


CreatorKey = Tuple[str, str, str]  # (platform, service, creator_id)
logger = logging.getLogger(__name__)


class CreatorsManager:
    """
    Authoritative domain manager for creator metadata.

    Responsibilities:
    - Fetch creators from all platforms
    - Normalize into CreatorDTO
    - Lazily resolve post counts from profile endpoints
    - Maintain in-memory registry
    - Optionally persist registry + per-creator metadata via DatabaseManager

    Explicit non-responsibilities:
    - UI signaling
    - Threading / async
    """

    def __init__(
        self,
        coomer: Optional[CreatorsAPIClient] = None,
        kemono: Optional[CreatorsAPIClient] = None,
        db: Optional[DatabaseManager] = None,
    ):
        self._coomer = coomer or CoomerClient()
        self._kemono = kemono or KemonoClient()
        self._db = db

        self._creators: Dict[CreatorKey, CreatorDTO] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh_all(self) -> List[CreatorDTO]:
        """
        Fetch all creators from all platforms and rebuild registry.
        """
        self._creators.clear()

        for platform, client in (("coomer", self._coomer), ("kemono", self._kemono)):
            creators = list(self._fetch_platform_creators(platform, client))
            for creator in creators:
                self._creators[self._key_for(creator)] = creator
            if self._db:
                self._db.replace_creators_for_platform(
                    platform,
                    [
                        {
                            "id": c.id,
                            "service": c.service,
                            "name": c.name,
                            "creator_indexed": c.indexed,
                            "creator_updated": c.updated,
                            "public_id": c.public_id,
                            "relation_id": c.relation_id,
                            "favorited": c.favorited,
                            "post_count": c.post_count,
                            "dm_count": c.dm_count,
                            "share_count": c.share_count,
                            "chat_count": c.chat_count,
                            "display_href": c.display_href,
                        }
                        for c in creators
                    ],
                )

        return self.list_creators()

    def list_creators(self) -> List[CreatorDTO]:
        return sorted(
            self._creators.values(),
            key=lambda c: (c.name.lower(), c.service),
        )

    def get_registry_updated(self, platform: str) -> Optional[str]:
        """
        Return last registry update timestamp (ISO string) for a platform.
        """
        if not self._db:
            return None
        return self._db.get_creators_registry_updated(platform)

    def load_from_registry(
        self,
        *,
        platform: Optional[str] = None,
        service: Optional[str] = None,
        include_hidden: bool = False,
    ) -> List[CreatorDTO]:
        """
        Hydrate in-memory registry from the persisted creators table.
        """
        if not self._db:
            return []

        self._creators.clear()
        rows = self._db.get_creators(
            platform=platform,
            service=service,
            include_hidden=include_hidden,
        )
        for row in rows:
            creator = CreatorDTO(
                id=row.get("creator_id"),
                service=row.get("service"),
                name=row.get("name"),
                platform=row.get("platform"),
                indexed=row.get("creator_indexed"),
                updated=row.get("creator_updated"),
                public_id=row.get("public_id"),
                relation_id=row.get("relation_id"),
                favorited=row.get("favorited_count"),
                post_count=row.get("post_count"),
                dm_count=row.get("dm_count"),
                share_count=row.get("share_count"),
                chat_count=row.get("chat_count"),
                display_href=row.get("display_href"),
            )
            self._creators[self._key_for(creator)] = creator

        return self.list_creators()

    def get_creator(
        self,
        platform: str,
        service: str,
        creator_id: str,
    ) -> Optional[CreatorDTO]:
        return self._creators.get((platform, service, creator_id))

    def get_random_creator(self, platform: str) -> Optional[CreatorDTO]:
        """
        Fetch a random creator locator and resolve it to a creator profile.
        """
        client = self._client(platform)
        try:
            locator = client.get_random_creator()
        except Exception as exc:
            logger.debug(
                "Random creator fetch failed: platform=%s error=%s",
                platform,
                exc,
            )
            return None

        service = str(locator.get("service") or "")
        creator_id = str(locator.get("artist_id") or "")
        if not service or not creator_id:
            return None

        existing = self._creators.get((platform, service, creator_id))
        if existing:
            return existing

        try:
            profile = client.get_creator(service, creator_id)
        except Exception as exc:
            logger.debug(
                "Random creator profile fetch failed: platform=%s service=%s creator_id=%s error=%s",
                platform,
                service,
                creator_id,
                exc,
            )
            return None

        creator = CreatorDTO(
            id=profile.get("id") or creator_id,
            service=profile.get("service") or service,
            name=profile.get("name") or creator_id,
            platform=platform,
            indexed=profile.get("indexed"),
            updated=profile.get("updated"),
            public_id=profile.get("public_id"),
            relation_id=profile.get("relation_id"),
            favorited=profile.get("favorited"),
            post_count=profile.get("post_count"),
            dm_count=profile.get("dm_count"),
            share_count=profile.get("share_count"),
            chat_count=profile.get("chat_count"),
            display_href=profile.get("display_href"),
        )
        self._creators[self._key_for(creator)] = creator

        if self._db:
            self._db.update_creator_post_count(
                platform,
                service,
                creator.id,
                int(creator.post_count or 0),
                dm_count=creator.dm_count,
                share_count=creator.share_count,
                chat_count=creator.chat_count,
                favorited=creator.favorited,
                creator_indexed=creator.indexed,
                creator_updated=creator.updated,
                public_id=creator.public_id,
                relation_id=creator.relation_id,
                name=creator.name,
                display_href=creator.display_href,
            )

        return creator
    
    def get_recommended_creators(
        self,
        platform: str,
        service: str,
        creator_id: str,
    ) -> List[Tuple[CreatorDTO, float]]:
        """
        Get recommended creators similar to the specified creator.
        Returns list of (CreatorDTO, similarity_score) tuples sorted by score.
        """
        client = self._client(platform)
        try:
            raw_creators = client.get_recommended_creators(service, creator_id)
        except Exception as exc:
            logger.debug(
                "Recommended creators fetch failed: platform=%s service=%s creator_id=%s error=%s",
                platform,
                service,
                creator_id,
                exc,
            )
            return []
        
        results = []
        for raw in raw_creators:
            similarity_score = float(raw.pop("similarity_score", 0.0))
            
            creator = CreatorDTO(
                id=raw.get("id") or raw.get("creator_id", ""),
                service=raw.get("service", service),
                name=raw.get("name", ""),
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
            
            # Cache the creator
            key = self._key_for(creator)
            if key not in self._creators:
                self._creators[key] = creator
                if self._db:
                    self._db.update_creator_post_count(
                        platform,
                        service,
                        creator.id,
                        int(creator.post_count or 0),
                        dm_count=creator.dm_count,
                        share_count=creator.share_count,
                        chat_count=creator.chat_count,
                        favorited=creator.favorited,
                        creator_indexed=creator.indexed,
                        creator_updated=creator.updated,
                        public_id=creator.public_id,
                        relation_id=creator.relation_id,
                        name=creator.name,
                        display_href=creator.display_href,
                    )
            
            results.append((creator, similarity_score))
        
        return results

    def get_linked_creators(
        self,
        platform: str,
        service: str,
        creator_id: str,
    ) -> List[CreatorDTO]:
        """
        Get linked/related creators for the specified creator.
        Returns list of CreatorDTO objects.
        """
        client = self._client(platform)
        try:
            raw_creators = client.get_linked_creators(service, creator_id)
        except Exception as exc:
            logger.debug(
                "Linked creators fetch failed: platform=%s service=%s creator_id=%s error=%s",
                platform,
                service,
                creator_id,
                exc,
            )
            return []
        
        results = []
        for raw in raw_creators:
            creator = CreatorDTO(
                id=raw.get("id") or raw.get("creator_id", ""),
                service=raw.get("service", service),
                name=raw.get("name", ""),
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
            
            # Cache the creator
            key = self._key_for(creator)
            if key not in self._creators:
                self._creators[key] = creator
                if self._db:
                    self._db.update_creator_post_count(
                        platform,
                        service,
                        creator.id,
                        int(creator.post_count or 0),
                        dm_count=creator.dm_count,
                        share_count=creator.share_count,
                        chat_count=creator.chat_count,
                        favorited=creator.favorited,
                        creator_indexed=creator.indexed,
                        creator_updated=creator.updated,
                        public_id=creator.public_id,
                        relation_id=creator.relation_id,
                        name=creator.name,
                        display_href=creator.display_href,
                    )
            
            results.append(creator)
        
        return results

    def by_platform(self, platform: str) -> List[CreatorDTO]:
        return [c for c in self._creators.values() if c.platform == platform]

    def by_service(self, service: str) -> List[CreatorDTO]:
        return [c for c in self._creators.values() if c.service == service]

    def load_creators(self, platform: str) -> bool:
        """
        Load creators for a platform from registry, falling back to API refresh.
        """
        if self._db:
            cached = self.load_from_registry(platform=platform, include_hidden=True)
            if cached:
                return True

        creators = list(self._fetch_platform_creators(platform, self._client(platform)))
        if not creators:
            return False

        for creator in creators:
            self._creators[self._key_for(creator)] = creator

        if self._db:
            self._db.replace_creators_for_platform(
                platform,
                [
                    {
                        "id": c.id,
                        "service": c.service,
                        "name": c.name,
                        "creator_indexed": c.indexed,
                        "creator_updated": c.updated,
                        "public_id": c.public_id,
                        "relation_id": c.relation_id,
                        "favorited": c.favorited,
                        "post_count": c.post_count,
                        "dm_count": c.dm_count,
                        "share_count": c.share_count,
                        "chat_count": c.chat_count,
                        "display_href": c.display_href,
                    }
                    for c in creators
                ],
            )
        return True

    def get_creators_paginated(
        self,
        platform: str,
        service: Optional[str] = None,
        *,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "name",
        sort_dir: str = "ASC",
    ) -> List[Dict]:
        if not self._db:
            return []
        return self._db.get_creators_paginated(
            platform,
            service,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )

    def search_creators(
        self,
        platform: str,
        service: Optional[str],
        query: str,
        *,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "name",
        sort_dir: str = "ASC",
    ) -> List[Dict]:
        if not self._db:
            return []
        return self._db.search_creators(
            platform,
            service,
            query,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )

    def get_creators_count(
        self,
        platform: str,
        service: Optional[str],
        query: Optional[str] = None,
    ) -> int:
        if not self._db:
            return 0
        return self._db.get_creators_count(platform, service, query)

    def set_creator_favorited(
        self,
        platform: str,
        service: str,
        creator_id: str,
        favorited: bool,
    ) -> None:
        if not self._db:
            return
        self._db.upsert_creator_meta(
            platform,
            service,
            creator_id,
            favorited=favorited,
        )

    def set_creator_pinned(
        self,
        platform: str,
        service: str,
        creator_id: str,
        pinned: bool,
    ) -> None:
        if not self._db:
            return
        self._db.upsert_creator_meta(
            platform,
            service,
            creator_id,
            pinned=pinned,
        )

    def set_creator_hidden(
        self,
        platform: str,
        service: str,
        creator_id: str,
        hidden: bool,
    ) -> None:
        if not self._db:
            return
        self._db.upsert_creator_meta(
            platform,
            service,
            creator_id,
            hidden=hidden,
        )

    def mark_creator_seen(
        self,
        platform: str,
        service: str,
        creator_id: str,
        seen_at: Optional[datetime] = None,
    ) -> None:
        if not self._db:
            return
        self._db.upsert_creator_meta(
            platform,
            service,
            creator_id,
            last_seen=seen_at or datetime.utcnow(),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_all_creators(self) -> Iterable[CreatorDTO]:
        yield from self._fetch_platform_creators("coomer", self._coomer)
        yield from self._fetch_platform_creators("kemono", self._kemono)

    def _fetch_platform_creators(
        self,
        platform: str,
        client,
        *,
        use_streaming: bool = True,
    ) -> Iterable[CreatorDTO]:
        """
        Fetch creators list (no per-creator profile calls).
        
        Args:
            platform: Platform name ("coomer" or "kemono")
            client: API client instance
            use_streaming: If True, use streaming JSON parsing for lower memory usage
        """
        # Use streaming if available and enabled
        if use_streaming and hasattr(client, 'stream_all_creators'):
            raw_creators = client.stream_all_creators()
        else:
            raw_creators = client.get_all_creators()

        for raw in raw_creators:
            creator = CreatorDTO(
                id=raw.get("id"),
                service=raw.get("service"),
                name=raw.get("name"),
                platform=platform,
                indexed=raw.get("indexed"),
                updated=raw.get("updated"),
                public_id=raw.get("public_id"),
                relation_id=raw.get("relation_id"),
                favorited=raw.get("favorited"),
                post_count=None,
                dm_count=None,
                share_count=None,
                chat_count=None,
                display_href=raw.get("display_href"),
            )

            yield creator

    def refresh_creator_post_count(
        self,
        platform: str,
        service: str,
        creator_id: str,
    ) -> Optional[CreatorDTO]:
        """
        Fetch a creator profile and update post_count for that creator only.
        """
        client = self._client(platform)
        logger.debug(
            "Fetching creator profile: platform=%s service=%s creator_id=%s",
            platform,
            service,
            creator_id,
        )
        try:
            profile = client.get_creator(service, creator_id)
        except Exception as exc:
            logger.debug(
                "Creator profile fetch failed: platform=%s service=%s creator_id=%s error=%s",
                platform,
                service,
                creator_id,
                exc,
            )
            return None

        count = profile.get("post_count")
        dm_count = profile.get("dm_count")
        share_count = profile.get("share_count")
        chat_count = profile.get("chat_count")
        creator_indexed = profile.get("indexed")
        creator_updated = profile.get("updated")
        public_id = profile.get("public_id")
        relation_id = profile.get("relation_id")
        name = profile.get("name")
        favorited = profile.get("favorited")
        creator = self._creators.get((platform, service, creator_id))
        if creator and count is not None:
            creator = creator.with_profile_counts(
                post_count=count,
                dm_count=dm_count,
                share_count=share_count,
                chat_count=chat_count,
                favorited=favorited,
                indexed=creator_indexed,
                updated=creator_updated,
                public_id=public_id,
                relation_id=relation_id,
                name=name,
            )
            self._creators[self._key_for(creator)] = creator
            if self._db:
                self._db.update_creator_post_count(
                    platform,
                    service,
                    creator_id,
                    int(creator.post_count),
                    dm_count=creator.dm_count,
                    share_count=creator.share_count,
                    chat_count=creator.chat_count,
                    favorited=creator.favorited,
                    creator_indexed=creator_indexed,
                    creator_updated=creator_updated,
                    public_id=public_id,
                    relation_id=relation_id,
                    name=name,
                )
            return creator
        return creator

    @staticmethod
    def _key_for(c: CreatorDTO) -> CreatorKey:
        return (c.platform, c.service, c.id)

    def _client(self, platform: str):
        if platform == "coomer":
            return self._coomer
        if platform == "kemono":
            return self._kemono
        raise ValueError(f"Unknown platform: {platform}")
