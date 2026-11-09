import pytest

from nanobot.knowledge.database import KnowledgeDatabase
from nanobot.knowledge.repository import KnowledgeRepository


@pytest.fixture
def db(tmp_path):
    database = KnowledgeDatabase(tmp_path / "state.db")
    database.init_schema()
    return database


@pytest.fixture
def repo(db):
    return KnowledgeRepository(db)
