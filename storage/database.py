# storage/database.py
import os
from pgvector.psycopg import register_vector_async
from dotenv import load_dotenv
from psycopg_pool import AsyncConnectionPool

load_dotenv()
async def insert_connection(conn):
    await register_vector_async(conn)

def make_pool():
    return AsyncConnectionPool(
        os.environ["DATABASE_URL"],
        open=False,
        min_size=1,
        max_size=4,
        configure=insert_connection,
    )