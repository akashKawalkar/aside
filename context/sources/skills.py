from typing import Any
from psycopg_pool import AsyncConnectionPool
from context.sources import Source
from context.items import Item, Situation
from knowledge.skills import load_relevant_skills

class SkillsSource:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool
        self.name = "skills"
        
    async def fetch(self, situation: Situation) -> list[Item]:
        # query might be in situation.query or we can combine
        text = situation.query
        skills = await load_relevant_skills(self.pool, text, limit=20)
        
        items = []
        for i, s in enumerate(skills):
            items.append(Item(
                id=f"skill:{s['id']}",
                text=s['body'],
                group=s['name'],
                source=self.name,
                priority=len(skills) - i, # higher priority first
            ))
        return items

def skills_source(pool: AsyncConnectionPool) -> SkillsSource:
    return SkillsSource(pool)
