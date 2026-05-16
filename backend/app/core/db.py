import os
from typing import Optional

import databases

DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://ajtrade:ajtrade@localhost:5432/ajtrade_db')

database: Optional[databases.Database] = None


def get_database() -> databases.Database:
    global database
    if database is None:
        database = databases.Database(DATABASE_URL)
    return database
