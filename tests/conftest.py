import pytest
from fakes import FakeManager, make_bot


@pytest.fixture
def manager() -> FakeManager:
    return FakeManager()


@pytest.fixture
def bot(tmp_path, manager):
    bot = make_bot(tmp_path, manager)
    yield bot
    bot.db.close()
