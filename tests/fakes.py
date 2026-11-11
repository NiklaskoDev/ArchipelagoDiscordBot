"""Stand-ins for the manager (httpx.MockTransport) and for Discord (small recording objects). No real network."""

import json
from types import SimpleNamespace

import discord
import httpx
from cryptography.fernet import Fernet

from apbot.config import Config
from apbot.manager.client import ManagerClient

BOT_TOKEN = "apm_botTokenForTests"
USER_TOKEN = "apm_userTokenForTests"
SITE_URL = "https://manager.example"
ENCRYPTION_KEY = Fernet.generate_key().decode()  # a fresh key per test run; nothing is ever encrypted for real


def make_config(tmp_path, **overrides) -> Config:
    values = {
        "discord_token": "discord-token-for-tests",
        "manager_url": "http://manager.test:8080",
        "manager_api_token": BOT_TOKEN,
        "encryption_keys": (ENCRYPTION_KEY,),
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


def by_token(answers: dict[str, object]):
    """An answer for `FakeManager.add` that depends on whose token asked: {token: body | (status, body)}."""

    def answer(request: httpx.Request):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        return answers.get(token, (401, {"detail": "This API token is not valid (any more)"}))

    return answer


def activity_json(entries: list[dict], last: int | None = None) -> dict:
    """The manager's answer to GET /api/games/{id}/activity."""
    return {"entries": entries, "last": last if last is not None else max((e["seq"] for e in entries), default=0)}


def entry(seq: int, kind: str, **fields) -> dict:
    return {"seq": seq, "at": 1_700_000_000.0 + seq, "kind": kind, **fields}


def tracker_json(hints=(), **overrides) -> dict:
    tracker = {"slot": 1, "name": "Alice", "game": "Timespinner", "goal": False, "items": [], "locations": [],
               "hints": list(hints), "hints_used": 0, "items_left": 0, "hint_points": 12, "hint_cost": 5,
               "points_per_check": 1, "release": "available", "collect": "available"}
    return {**tracker, **overrides}


def received_json(item="Sword", flags=0, sender="Bob", location="Chest", start=False) -> dict:
    return {"item": item, "from": sender, "location": location, "flags": flags, "start": start}


def hint_json(item="Sword", receiver="Alice", finder="Bob", location="Chest", **overrides) -> dict:
    hint = {"item": item, "receiver": receiver, "for_me": True, "location": location, "finder": finder,
            "in_my_world": False, "entrance": "", "found": False, "flags": 0, "status": 0, "finder_slot": 2,
            "location_id": 77}
    return {**hint, **overrides}


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


def entry_json(entry_id: int = 1, name: str = "alice.yaml", mine: bool = True, **overrides) -> dict:
    """An entry of a round as the manager sends it; `name`, `config_id` and `generation_error` only to its owner and
    to the round's organizer (pass `name=None` for what a player sees of someone else's entry)."""
    entry = {
        "id": entry_id, "user_id": 7, "username": "alice", "mine": mine, "player_names": ["Alice"],
        "games": ["Timespinner"], "slots": 1, "missing_games": [], "missing_roms": [], "issues": [],
        "created_at": 1_700_000_000.0, "updated_at": 1_700_000_000.0, "name": name, "config_id": 40 + entry_id,
        "outdated": False, "generation_error": None,
    }
    entry = {**entry, **overrides}
    if entry["name"] is None:  # someone else's entry: the manager leaves these out
        for key in ("name", "config_id", "outdated", "generation_error"):
            entry.pop(key)
    return entry


def signup_detail_json(signup_id: int = 1, name: str = "Spring Async", entries=(), **overrides) -> dict:
    """The manager's answer to GET /api/signups/{id}, with the fields only an organizer gets under `overrides`."""
    detail = signup_json(
        signup_id, name, mystery=False, mystery_weights=None, my_slot_count=0, auto_generate=True, options={},
        game_id=None, job_id=None, error=None, test=None, entries=list(entries), configs_per_user=2,
    )
    return {**detail, **overrides}


def config_json(config_id: int = 40, name: str = "alice.yaml", **overrides) -> dict:
    config = {"id": config_id, "user_id": 7, "name": name, "games": ["Timespinner"], "player_names": ["Alice"],
              "description": None, "size": 100, "shared": False, "missing_games": [], "missing_roms": []}
    return {**config, **overrides}


def job_json(job_id: int = 9, status: str = "queued", **overrides) -> dict:
    """The manager's answer to POST /api/jobs and GET /api/jobs/{id}: a generation job with its players."""
    job = {
        "id": job_id, "name": None, "status": status, "created_at": 1_700_000_000.0, "started_at": None,
        "finished_at": None, "game_id": None, "error": None, "options": {}, "host_after": True, "test": False,
        "created_by": 10, "creator": "alice", "queue_position": None, "meta": None, "signup": None,
        "players": [{"filename": "alice.yaml", "user_id": 10, "owner": "alice", "config_id": 40, "names": ["Alice"],
                     "games": ["Timespinner"], "error": None}],
    }
    return {**job, **overrides}


def friend_json(user_id: int = 20, username: str = "bob", **overrides) -> dict:
    """One person in the manager's friend list or in a request."""
    return {"id": user_id, "username": username, **overrides}


def friends_json(friends=(), incoming=(), outgoing=(), suggestions=()) -> dict:
    """The manager's answer to GET /api/friends."""
    return {
        "friends": list(friends), "incoming": list(incoming), "outgoing": list(outgoing),
        "suggestions": list(suggestions),
    }


def check_json(ok: bool = True, **overrides) -> dict:
    check = {"ok": ok, "errors": [], "warnings": [],
             "players": [{"name": "Alice", "games": ["Timespinner"], "errors": [], "warnings": []}],
             "missing_roms": []}
    return {**check, **overrides}


# ---------------------------------------------------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------------------------------------------------
class FakeResponse:
    def __init__(self):
        self.done = False
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.modals: list = []
        self.deferred = False
        self.defer_kwargs: dict = {}

    def is_done(self) -> bool:
        return self.done

    async def defer(self, **kwargs) -> None:
        self.done = self.deferred = True
        self.defer_kwargs = kwargs

    async def send_message(self, **kwargs) -> None:
        self.done = True
        self.sent.append(kwargs)

    async def edit_message(self, **kwargs) -> None:
        self.done = True
        self.edited.append(kwargs)

    async def send_modal(self, modal) -> None:
        self.done = True
        self.modals.append(modal)


class FakeFollowup:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, **kwargs) -> None:
        self.sent.append(kwargs)


class FakeUser:
    """A server member: `roles` are the ids of the roles they have."""

    def __init__(self, user_id: int = 1001, roles=()):
        self.id = user_id
        self._roles = set(roles)

    def get_role(self, role_id: int):
        return object() if role_id in self._roles else None


def _http_error(cls, status: int, reason: str):
    return cls(SimpleNamespace(status=status, reason=reason), reason)


def not_found() -> discord.NotFound:
    return _http_error(discord.NotFound, 404, "Not Found")


def forbidden() -> discord.Forbidden:
    return _http_error(discord.Forbidden, 403, "Forbidden")


class FakeMember(FakeUser):
    """A member the bot can give roles to: records the calls."""

    def __init__(self, user_id: int = 1001, roles=()):
        super().__init__(user_id, roles)
        self.added: list[int] = []
        self.removed: list[int] = []

    async def add_roles(self, *roles, reason=None) -> None:
        self._roles |= {role.id for role in roles}
        self.added += [role.id for role in roles]

    async def remove_roles(self, *roles, reason=None) -> None:
        self._roles -= {role.id for role in roles}
        self.removed += [role.id for role in roles]


class FakeBotMember(FakeUser):
    def __init__(self, **permissions):
        super().__init__(1)
        self.guild_permissions = discord.Permissions(**permissions)


class FakeRole:
    def __init__(self, role_id: int, *, default: bool = False):
        self.id = role_id
        self.mention = f"<@&{role_id}>"
        self._default = default
        self.mentionable = False
        self.edits: list[dict] = []
        self.deleted = False

    def is_default(self) -> bool:
        return self._default

    async def edit(self, **kwargs) -> None:
        self.edits.append(kwargs)
        self.mentionable = kwargs.get("mentionable", self.mentionable)

    async def delete(self, **kwargs) -> None:
        self.deleted = True


class FakeMessage:
    def __init__(self, message_id: int, channel, content=None, **kwargs):
        self.id = message_id
        self.channel = channel
        self.content = content
        self.kwargs = kwargs
        self.edits: list[dict] = []
        self.pinned = False
        self.deleted = False

    async def edit(self, **kwargs) -> None:
        self.edits.append(kwargs)

    async def fetch(self):
        if self.deleted:
            raise not_found()
        return self

    async def delete(self) -> None:
        self.deleted = True

    async def pin(self) -> None:
        self.pinned = True


class FakeMessageable:
    """A channel, thread or person that records what is sent into it."""

    def __init__(self, channel_id: int):
        self.id = channel_id
        self.mention = f"<#{channel_id}>"
        self.messages: dict[int, FakeMessage] = {}
        self.fail_with: Exception | None = None
        self._next_message = channel_id * 1000

    @property
    def sent(self) -> list[FakeMessage]:
        return list(self.messages.values())

    async def send(self, content=None, **kwargs) -> FakeMessage:
        if self.fail_with:
            raise self.fail_with
        self._next_message += 1
        message = FakeMessage(self._next_message, self, content, **kwargs)
        self.messages[message.id] = message
        return message

    def get_partial_message(self, message_id: int) -> FakeMessage:
        found = self.messages.get(message_id)
        if found is not None:
            return found
        gone = FakeMessage(message_id, self)  # never sent, or deleted: every call on it says "not found"
        gone.deleted = True

        async def missing(**kwargs):
            raise not_found()

        gone.edit = missing
        return gone


class FakeThread(FakeMessageable):
    def __init__(self, thread_id: int, name: str = ""):
        super().__init__(thread_id)
        self.name = name
        self.archived = False
        self.locked = False
        self.edits: list[dict] = []

    async def edit(self, **kwargs) -> None:
        self.edits.append(kwargs)
        self.archived = kwargs.get("archived", self.archived)
        self.locked = kwargs.get("locked", self.locked)


class FakeTextChannel(FakeMessageable):
    def __init__(self, channel_id: int):
        super().__init__(channel_id)
        self.threads: list[FakeThread] = []
        self.thread_requests: list[dict] = []
        self._next_thread = channel_id * 100

    async def create_thread(self, **kwargs) -> FakeThread:
        if self.fail_with:
            raise self.fail_with
        self.thread_requests.append(kwargs)
        self._next_thread += 1
        thread = FakeThread(self._next_thread, kwargs.get("name", ""))
        self.threads.append(thread)
        return thread


class FakeScheduledEvent:
    def __init__(self, event_id: int, **kwargs):
        self.id = event_id
        self.kwargs = kwargs
        self.edits: list[dict] = []
        self.deleted = False

    async def edit(self, **kwargs) -> None:
        self.edits.append(kwargs)

    async def delete(self) -> None:
        self.deleted = True


class FakeGuild:
    """A server the bot is in: its channels, roles, members and scheduled events by id."""

    def __init__(self, guild_id: int = 5000, **bot_permissions):
        self.id = guild_id
        self.me = FakeBotMember(**bot_permissions)
        self.channels: dict[int, FakeTextChannel] = {}
        self.roles: dict[int, FakeRole] = {}
        self.members: dict[int, FakeMember] = {}
        self.events: dict[int, FakeScheduledEvent] = {}
        self.created_roles: list[dict] = []
        self.created_events: list[dict] = []
        self._next_role = guild_id * 10
        self._next_event = guild_id * 10

    def add_channel(self, channel_id: int) -> FakeTextChannel:
        channel = self.channels[channel_id] = FakeTextChannel(channel_id)
        return channel

    def get_channel(self, channel_id: int):
        return self.channels.get(channel_id)

    def get_thread(self, thread_id: int):
        return next((t for channel in self.channels.values() for t in channel.threads if t.id == thread_id), None)

    async def fetch_channel(self, channel_id: int):
        found = self.get_thread(channel_id) or self.get_channel(channel_id)
        if found is None:
            raise not_found()
        return found

    def get_role(self, role_id: int):
        return self.roles.get(role_id)

    async def create_role(self, **kwargs) -> FakeRole:
        self._next_role += 1
        role = self.roles[self._next_role] = FakeRole(self._next_role)
        self.created_roles.append(kwargs)
        return role

    def add_member(self, user_id: int, roles=()) -> FakeMember:
        member = self.members[user_id] = FakeMember(user_id, roles)
        return member

    async def fetch_member(self, user_id: int) -> FakeMember:
        if user_id not in self.members:
            raise not_found()
        return self.members[user_id]

    async def create_scheduled_event(self, **kwargs) -> FakeScheduledEvent:
        self._next_event += 1
        event = self.events[self._next_event] = FakeScheduledEvent(self._next_event, **kwargs)
        self.created_events.append(kwargs)
        return event

    async def fetch_scheduled_event(self, event_id: int) -> FakeScheduledEvent:
        if event_id not in self.events:
            raise not_found()
        return self.events[event_id]


class FakeDiscordClient:
    """What the services need of the bot's Discord connection."""

    def __init__(self, *guilds: FakeGuild, ready: bool = True):
        self.guilds = list(guilds)
        self._ready = ready
        self.presence: list = []

    def is_ready(self) -> bool:
        return self._ready

    def get_guild(self, guild_id: int):
        return next((g for g in self.guilds if g.id == guild_id), None)

    async def change_presence(self, **kwargs) -> None:
        self.presence.append(kwargs["activity"])


class FakeDirectory:
    """`fetch_user` for the alert engine: one person (something to send direct messages into) per id."""

    def __init__(self):
        self.users: dict[int, FakeMessageable] = {}

    async def __call__(self, user_id: int) -> FakeMessageable:
        return self.users.setdefault(user_id, FakeMessageable(user_id))


class FakeChannel:
    def __init__(self, channel_id: int, permissions: discord.Permissions):
        self.id = channel_id
        self.mention = f"<#{channel_id}>"
        self._permissions = permissions

    def permissions_for(self, member) -> discord.Permissions:
        return self._permissions


class FakeAttachment:
    """An uploaded file: its name, the size Discord reports and the bytes a download gives."""

    def __init__(self, filename: str = "alice.yaml", data: bytes = b"name: Alice\ngame: Timespinner\n", size=None):
        self.filename = filename
        self._data = data
        self.size = len(data) if size is None else size
        self.reads = 0
        self.fail_with: Exception | None = None

    async def read(self) -> bytes:
        self.reads += 1
        if self.fail_with:
            raise self.fail_with
        return self._data


class FakeInteraction:
    def __init__(
        self,
        bot=None,
        locale: str = "en-US",
        *,
        user_id: int = 1001,
        roles=(),
        guild_id: int | None = None,
        manage_guild: bool = False,
        namespace: dict | None = None,
    ):
        self.namespace = SimpleNamespace(**(namespace or {}))  # the options typed so far, for autocomplete
        self.client = bot
        self.locale = locale
        self.user = FakeUser(user_id, roles)
        self.guild = FakeGuild(guild_id) if guild_id is not None else None
        self.permissions = discord.Permissions(manage_guild=manage_guild)
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.deleted_original = False
        self.original_edits: list[dict] = []

    async def delete_original_response(self) -> None:
        self.deleted_original = True

    async def edit_original_response(self, **kwargs) -> None:
        self.original_edits.append(kwargs)

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

