import asyncio
import json
import os
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from storage.models_extra import SkillRow
import sys

async def main():
    url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://")
    engine = create_async_engine(url)
    Session = async_sessionmaker(engine)
    
    with open('docs/skills.starter.json', 'r') as f:
        skills_data = json.load(f)
        
    async with Session() as session:
        for skill in skills_data:
            session.add(SkillRow(
                name=skill["name"],
                body=skill["content"],
                use_when=", ".join(skill.get("tags", [])),
                affects=""
            ))
        await session.commit()
    print("Seeded skills")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())

