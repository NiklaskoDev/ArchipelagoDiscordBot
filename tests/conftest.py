import pytest
from fakes import FakeManager, make_bot


@pytest.fixture
def manager() -> FakeManager:
    return FakeManager()


@pytest.fixture
def bot(tmp_path, manager):
    return make_bot(tmp_path, manager)
