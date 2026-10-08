from typing import Any
from psycopg_pool import AsyncConnectionPool
from context.sources import Source
from context.items import Item, Situation

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
        
        from config import load_config
        
        limit = 10
        query = situation.query
        
        if not query:
            return []
            
        cfg = load_config()
        cutoff = cfg.memory.note_similarity_cutoff
            
        def search_repo(q, lim):
            return search_notes(self.pool, q, limit=lim, match_any=True)
            
        async def semantic(**kwargs):
            kwargs["min_similarity"] = cutoff
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
                id=f"note:{r.id}",
                text=f"{r.text} (noted {_when(r.created_at)})",
                source=self.name,
                priority=limit - i,
                tags=tuple(getattr(r, "tags", ())),
            ))
        return items

def notes_source(pool: AsyncConnectionPool) -> NotesSource:
    return NotesSource(pool)
