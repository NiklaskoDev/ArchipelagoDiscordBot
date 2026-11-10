"""Stand-ins for the manager (httpx.MockTransport) and for Discord (small recording objects). No real network."""

import json
import httpx

from apbot.config import Config
from apbot.manager.client import ManagerClient

BOT_TOKEN = "apm_botTokenForTests"
USER_TOKEN = "apm_userTokenForTests"
SITE_URL = "https://manager.example"


def make_config(tmp_path, **overrides) -> Config:
    values = {
        "discord_token": "discord-token-for-tests",
        "manager_url": "http://manager.test:8080",
        "manager_api_token": BOT_TOKEN,
        "data_dir": tmp_path,
        "log_level": "INFO",
        "dev_guild_id": None,
    }
    return Config(**{**values, **overrides})


class FakeManager:
    """Routes requests by method and path, and records every request that arrives (headers included)."""

    def __init__(self):
        self._routes: dict[tuple[str, str], list] = {}
        self.requests: list[httpx.Request] = []
        self.client = ManagerClient("http://manager.test", transport=httpx.MockTransport(self._handle), sleep=self._sleep)
        self.sleeps: list[float] = []

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def add(self, method: str, path: str, *answers) -> None:
        """Each answer is a json body, an (status, body) tuple, an httpx.Response, a callable(request) or an Exception.
        Answers are used in order; the last one repeats."""
        self._routes[(method, path)] = list(answers)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answers = self._routes.get((request.method, request.url.path))
        if not answers:
            return httpx.Response(404, json={"detail": "Not found"})
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer(request)
        if isinstance(answer, httpx.Response):
            return answer
        if isinstance(answer, tuple):
            status, body = answer
            return httpx.Response(status, json=body)
        return httpx.Response(200, json=answer)

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


# ---------------------------------------------------------------------------------------------------------------------
# what the manager sends. The sensitive fields are in on purpose: the bot must never show them.
# ---------------------------------------------------------------------------------------------------------------------
SECRETS = {
    "password": "room-password-s3cret",
    "server_password": "admin-server-pass-s3cret",
    "discord_webhook_url": "https://discord.com/api/webhooks/1/webhook-token-s3cret",
    "notes": "organizer-notes-s3cret",
    "spectate": {"token": "spectate-token-s3cret", "activity": True},
}


def slot_json(slot: int = 1, name: str = "Alice", game: str = "Timespinner", **overrides) -> dict:
    slot_data = {
        "slot": slot, "name": name, "game": game, "type": 1, "locations": 100, "user_id": 7, "owner": "alice",
        "mine": False, "claimable": False, "checked": 40, "hints_used": 0, "goal_at": None, "last_activity": None,
        "status": "online", "player_status": None, "status_note": None, "status_at": None, "handover": None,
        "files": [],
    }
    return {**slot_data, **overrides}


def game_json(game_id: int = 1, name: str = "Friday Run", **overrides) -> dict:
    game = {
        "id": game_id, "name": name, "seed_name": "seed123", "source": "generated", "created_at": 1_700_000_000.0,
        "race_mode": False, "state": "running", "error": None, "started_at": 1_700_000_100.0,
        "desired_state": "running", "sleep_minutes": 0, "port": 38281, "address": "ap.example:38281", "tls": False,
        "has_password": True, "games": ["Timespinner", "Hollow Knight"], "missing_games": [],
        "ap_version": "0.6.8", "player_count": 2, "claimed_count": 1, "visibility": "all", "claimable": True,
        "single_claim": False, "play_started_at": None, "finished_at": None, "outcome": None, "start": None,
        "goal_count": 0, "my_slots": [], "offered_slots": [], "my_file_count": 0, "can_control": False,
        "can_manage": False, "can_archive": False, "return_blocked": False,
        "slots": [slot_json(1), slot_json(2, "Bob", "Hollow Knight", owner=None, user_id=None, checked=0)],
        "progress": {"checked": 40, "total": 200},
        **SECRETS,
    }
    return {**game, **overrides}


def signup_json(signup_id: int = 1, name: str = "Spring Async", **overrides) -> dict:
    signup = {
        "id": signup_id, "name": name, "description": "", "status": "open", "open": True, "deadline": 1_800_000_000.0,
        "created_at": 1_700_000_000.0, "closed_at": None, "creator": "olga", "slots_per_user": 2,
        "allowed_games": [], "entry_count": 5, "user_count": 3, "can_manage": False, "my_entry_count": 0,
    }
    return {**signup, **overrides}


# ---------------------------------------------------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------------------------------------------------
class FakeResponse:
    def __init__(self):
        self.done = False
        self.sent: list[dict] = []
        self.deferred = False

    def is_done(self) -> bool:
        return self.done

    async def defer(self, **kwargs) -> None:
        self.done = self.deferred = True

    async def send_message(self, **kwargs) -> None:
        self.done = True
        self.sent.append(kwargs)


class FakeFollowup:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, **kwargs) -> None:
        self.sent.append(kwargs)


class FakeInteraction:
    def __init__(self, bot=None, locale: str = "en-US"):
        self.client = bot
        self.locale = locale
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.deleted_original = False

    async def delete_original_response(self) -> None:
        self.deleted_original = True

    @property
    def messages(self) -> list[dict]:
        """Everything the person was sent, in order, whether as the first answer or as a follow-up."""
        return [*self.response.sent, *self.followup.sent]


def json_text(embeds) -> str:
    """All text of one or more embeds, as one string to search in."""
    embeds = embeds if isinstance(embeds, list | tuple) else [embeds]
    return json.dumps([embed.to_dict() for embed in embeds], ensure_ascii=False)


def make_bot(tmp_path, manager: FakeManager):
    from apbot.bot import ApBot

    return ApBot(make_config(tmp_path), manager=manager.client)

