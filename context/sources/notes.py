from typing import Any
from psycopg_pool import AsyncConnectionPool
from context.sources import Source
from context.items import Item, Situation

def _when(created) -> str:
    return f"{created:%d %b %Y}" if hasattr(created, "strftime") else str(created)


def _when(created) -> str:
    return f"{created:%d %b %Y}" if hasattr(created, "strftime") else str(created)


class NotesSource:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool
        self.name = "notes"
        
    async def fetch(self, situation: Situation) -> list[Item]:
        from knowledge.notes import search_notes_combined
        from storage.repo.notes import search_notes
        from storage import search
        
        limit = 10
        query = situation.query
        
        if not query:
            return []
            
        def search_repo(q, lim):
            return search_notes(self.pool, q, limit=lim)
            
        async def semantic(**kwargs):
            return await search(self.pool, **kwargs)
            
        results = await search_notes_combined(
            query,
            search_notes_repo=search_repo,
            semantic_search=semantic,
            limit=limit
        )
        
        items = []
        for i, r in enumerate(results):
            items.append(Item(
                id=f"note:{r.note['id']}",
                text=f"{r.note['text']} (noted {_when(r.note['created_at'])})",
                source=self.name,
                priority=limit - i,
            ))
        return items

def notes_source(pool: AsyncConnectionPool) -> NotesSource:
    return NotesSource(pool)
