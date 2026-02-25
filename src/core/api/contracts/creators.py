from __future__ import annotations

from typing import Dict, List, Protocol


class CreatorsAPIClient(Protocol):
    PLATFORM: str

    def get_all_creators(self) -> List[dict]:
        ...

    def get_creator(
        self,
        service: str,
        creator_id: str,
    ) -> dict:
        ...

    def get_random_creator(self) -> Dict[str, str]:
        ...
