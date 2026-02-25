from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Union


@dataclass(frozen=True)
class CreatorDTO:
    id: str
    service: str
    name: str
    platform: str

    indexed: Optional[Union[int, str]]
    updated: Optional[Union[int, str]]

    public_id: Optional[str] = None
    relation_id: Optional[int] = None
    favorited: Optional[int] = None

    post_count: Optional[int] = None
    dm_count: Optional[int] = None
    share_count: Optional[int] = None
    chat_count: Optional[int] = None
    display_href: Optional[str] = None

    def with_post_count(self, count: int) -> "CreatorDTO":
        return replace(self, post_count=count)

    def with_profile_counts(
        self,
        *,
        post_count: Optional[int] = None,
        dm_count: Optional[int] = None,
        share_count: Optional[int] = None,
        chat_count: Optional[int] = None,
        favorited: Optional[int] = None,
        indexed: Optional[str] = None,
        updated: Optional[str] = None,
        public_id: Optional[str] = None,
        relation_id: Optional[int] = None,
        name: Optional[str] = None,
        display_href: Optional[str] = None,
    ) -> "CreatorDTO":
        return replace(
            self,
            post_count=post_count if post_count is not None else self.post_count,
            dm_count=dm_count if dm_count is not None else self.dm_count,
            share_count=share_count if share_count is not None else self.share_count,
            chat_count=chat_count if chat_count is not None else self.chat_count,
            favorited=favorited if favorited is not None else self.favorited,
            indexed=indexed if indexed is not None else self.indexed,
            updated=updated if updated is not None else self.updated,
            public_id=public_id if public_id is not None else self.public_id,
            relation_id=relation_id if relation_id is not None else self.relation_id,
            name=name if name is not None else self.name,
            display_href=display_href if display_href is not None else self.display_href,
        )
