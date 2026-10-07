from typing import Any
from psycopg_pool import AsyncConnectionPool
from context.sources import Source
from context.items import Item, Situation
from storage.repo.persistent_entries import list_persistent_entries, SECTION_ORDER

class PersistentFileSource:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool
        self.name = "persistent_file"
        
    async def fetch(self, situation: Situation) -> list[Item]:
        entries = await list_persistent_entries(self.pool, include_retired=True) # The packer filters exclude_status
        items = []
        for e in entries:
            items.append(Item(
                id=f"persistent:{e['id']}",
                text=e['text'],
                group=e['section'].title(),
                source=self.name,
                status=e['status'],
                priority=-SECTION_ORDER.get(e['section'], 999),
            ))
        return items

def persistent_file_source(pool: AsyncConnectionPool) -> PersistentFileSource:
    return PersistentFileSource(pool)
