# 006 — Generate, archive and delete games; friends, friend alerts and config sharing

Status: approved
Type: feature
Version impact: minor (0.3.0) — new commands, new alert kinds and a backward compatible DB migration. Entries under
`[Unreleased]` → `### Added`. No version bump in this plan (§8).

## Goal
Linked users can generate a game from their own configs and the configs their friends share with them, follow the
generation in their reply, move a game to the archive and back, and delete a game — all as themselves (their linked
token; the manager decides). They can see and answer their friend list and requests, add friends, get a DM with
Accept/Decline for each new friend request, get a DM when a friend puts them in a game, and share one of their configs
with everyone, all friends or chosen friends (or stop sharing). A manager without the friends feature gets a clear
message instead of an error.

Out of scope: the manager's "People you played with" friend suggestions, an alert when one's own request is accepted,
copying shared configs, the generator's expert options (`plando`, `meta`, `skip_prog_balancing`, `ap_version`,
`server_options`), job logs and job lists, `/games` showing jobs, any public post about a new game (the existing
relay/overview picks a public game up as today).

The work is split into three parts, coded, tested and reviewed in this order: **P1** games (generate, archive, delete),
**P2** friends (list, add, request alerts), **P3** generate with friends + config sharing. Each part ends green
(`ruff check .`, `pytest`) and gets its own CHANGELOG/README lines.

## Reuse
- `Access.user_token` / `Access.call_as` / `Access.run` / `Access.require_role` (`apbot/access.py`) — all manager calls
  as the caller; refused token → link marked invalid; read-only token explained.
- `ManagerClient._get/_post/_put/_send`, `QUICK_TIMEOUT`, `CONTROL_TIMEOUT` (`apbot/manager/client.py`).
- Whitelist parsers in `apbot/manager/views.py` (`_require_id`, `_text`, `_int`, `_texts`, `_parse_all`,
  `parse_config(s)`, `parse_game_summary/detail`).
- `TtlCache` (`apbot/services/ttl_cache.py`) and the `SignupDirectory` pattern (60 s, `quick=True` for suggestions).
- `SignupDirectory.configs` + `config_choices` (`apbot/autocomplete.py`) — the caller's own configs (P1 picker,
  `/config share`).
- `MyGames` (`apbot/services/my_games.py`) + `game_choices` — games the caller's token sees (archive/delete suggestions).
- `ConfirmView`, `AuthorView`, `MultiSelectView`, `open_in_manager_view` (`apbot/ui/views.py`).
- Dynamic persistent buttons pattern (`apbot/ui/dynamic.py`: `HandoverAcceptButton`, `_remove_buttons`) and
  `bot.add_dynamic_items`.
- `UserSync` loop (per link, staggered, token-refusal handling) and `AlertEngine._send` (mute on closed DMs),
  `AlertPrefStore`, `ALERT_KINDS`, `ALERT_KIND_LABELS`, `/alerts` (`MultiSelectView` over `ALERT_KINDS`, so new kinds
  appear without cog changes), `alerts_text` in `/mydata`.
- `SlotsCog._recipient` (member → linked manager username) — moved to `apbot/cogs/common.py` and shared.
- The signup-test polling pattern (`SignupsCog._test_result`, injectable `sleep`) for following a job.
- `error_embed`, `notice_embed`, `user_message`, `safe_text`, `fit_lines`, `reply`, i18n catalogs + `tests/test_i18n.py`.
- New (one line why each):
  - `apbot/services/friends.py` (`FriendDirectory`) — no cache holds a person's friend overview or their
    own-plus-friends' config library; also the one place that turns "route missing" into the clear message.
  - `apbot/cogs/generate.py`, `apbot/cogs/friends.py`, `apbot/cogs/configs.py` — new command families; games.py keeps
    only game look/control.
  - `apbot/ui/generate.py`, `apbot/ui/friends.py` — embeds/views for these, like `apbot/ui/signups.py`.
  - Migration `004_friend_alerts.sql` — `alert_prefs.kind` has a CHECK list; SQLite cannot alter it in place.

## Code layout
- `apbot/manager/client.py` — new calls (jobs, archive, delete, friends, sharing); `list_configs(friends=)`.
- `apbot/manager/views.py` — `JobView`, `JobPlayer`, `FriendView`, `FriendsOverview`, parsers; extra fields on
  `GameSummary` and `ConfigView`.
- `apbot/services/friends.py` — new `FriendDirectory`, `FriendsUnsupported`.
- `apbot/services/user_sync.py` — friend-request polling + `FriendRequestEvent` (P2).
- `apbot/services/alerts.py` — `handle_friend_requests`, `friend_game` (P2/P3).
- `apbot/db/live.py` — `ALERT_KINDS` += `friend_request` (P2), `friend_game` (P3).
- `apbot/db/migrations/004_friend_alerts.sql` — rebuild `alert_prefs` with both new kinds (P2).
- `apbot/cogs/generate.py` — `/generate` (P1; friends in P3).
- `apbot/cogs/games.py` — `/game_archive`, `/game_delete` (P1) (names: Decision 1).
- `apbot/cogs/friends.py` — `/friends`, `/friend add` (P2).
- `apbot/cogs/configs.py` — `/config share` (P3).
- `apbot/cogs/common.py` — `resolve_username` (moved from `SlotsCog._recipient`); `apbot/cogs/slots.py` uses it.
- `apbot/autocomplete.py` — `archive_game_choices`, `delete_game_choices` (P1), `generate_config_choices` (P3).
- `apbot/ui/generate.py` — `job_embed`, `GenerationView` (P1).
- `apbot/ui/friends.py` — `friends_embed`, `FriendsView` (P2).
- `apbot/ui/dynamic.py` — `FriendAnswerButton`, `friend_request_view` (P2).
- `apbot/ui/embeds.py` — `ALERT_KIND_LABELS` entries.
- `apbot/bot.py` — construct `FriendDirectory`, add cogs, wire UserSync listener, register dynamic item.
- `apbot/i18n/de.py`, `fr.py`, `nl.py` — every new `MESSAGES` and `COMMANDS` string.
- `README.md`, `CHANGELOG.md`.
- Tests: `tests/test_generate_cog.py`, `tests/test_game_archive.py`, `tests/test_friends_cog.py`,
  `tests/test_friend_directory.py`, `tests/test_friend_buttons.py`, `tests/test_config_share.py` (new);
  `tests/test_manager_client.py`, `tests/test_user_sync.py`, `tests/test_alerts.py`, `tests/test_database.py`,
  `tests/test_live_db.py`, `tests/test_autocomplete.py`, `tests/test_slots.py`, `tests/test_embeds.py`,
  `tests/test_i18n.py` (extend). Use `tests/fakes.py` fakes; extend them as needed.

## Data
- P2: migration `apbot/db/migrations/004_friend_alerts.sql` (Decision 4):
  ```sql
  CREATE TABLE alert_prefs_new (
      discord_user_id  INTEGER NOT NULL REFERENCES user_links (discord_user_id) ON DELETE CASCADE,
      kind             TEXT NOT NULL
          CHECK (kind IN ('item_received', 'hint_found', 'stuck_in_my_world', 'game_online', 'slot_offered',
                          'friend_request', 'friend_game')),
      PRIMARY KEY (discord_user_id, kind)
  );
  INSERT INTO alert_prefs_new (discord_user_id, kind) SELECT discord_user_id, kind FROM alert_prefs;
  DROP TABLE alert_prefs;
  ALTER TABLE alert_prefs_new RENAME TO alert_prefs;
  ```
  Copy any index/trigger that `002_live.sql` defines on `alert_prefs` (check the file; today only the PK). Runs in the
  existing per-migration transaction; nothing references `alert_prefs`, so `foreign_keys = ON` is fine. Existing rows
  are kept; new kinds default to off like all alerts.
- In memory only (lost on restart): `FriendDirectory` caches (60 s, max 500), `UserSync` incoming-request ids per person
  (only for people with `friend_request` on). Nothing else is stored; `/mydata` shows the new alert kinds through
  `alerts_text` automatically.

## Interfaces

### Manager client (`apbot/manager/client.py`)
All changes are not retried (existing `_post/_put/_send` behaviour).
- `start_job(token, config_ids: Sequence[int], *, name: str | None, spoiler: int, race: bool, seed: int | None,
  host_after: bool) -> dict` — `POST /api/jobs`, body exactly `{config_ids, name, spoiler, race, seed, host_after}`.
- `job(token, job_id, *, quick=False) -> dict` — `GET /api/jobs/{id}`.
- `cancel_job(token, job_id) -> dict` — `POST /api/jobs/{id}/cancel` (409 when no longer queued/running).
- `archive_game(token, game_id, archived: bool) -> dict` — `POST /api/games/{id}/archive` `{"archived": bool}`,
  `timeout=CONTROL_TIMEOUT` (archiving stops the server).
- `delete_game(token, game_id) -> dict` — `DELETE /api/games/{id}`, `timeout=CONTROL_TIMEOUT`.
- `friends(token, *, quick=False) -> dict` — `GET /api/friends`.
- `ask_friend(token, username: str) -> dict` — `POST /api/friends` `{"username"}` → `{status: asked|accepted, user}`.
- `accept_friend(token, other_id) -> dict` — `POST /api/friends/{id}/accept`.
- `remove_friend(token, other_id) -> dict` — `DELETE /api/friends/{id}` → `{removed: friend|declined|withdrawn}`.
- `share_config(token, config_id, *, shared: bool, share_with: str = "everyone", friends: Sequence[int] = ()) -> dict`
  — `PUT /api/configs/{id}/shared` `{shared, share_with, friends}`.
- `list_configs(token, *, quick=False, friends=False)` — path `/api/configs?friends=1` when `friends`. Existing callers
  unchanged.
- All ids through `int(...)` as today.

### Views (`apbot/manager/views.py`)
- `GameSummary` gains kw_only `can_manage: bool = False`, `can_archive: bool = False`,
  `return_blocked: str | None = None` (from the same keys; `bool(...)`/`_text`). Never shown in embeds; used for
  suggestions and pre-checks. `GameDetail` inherits them.
- `ConfigView` gains kw_only `owner_id: int | None = None` (`user_id`), `owner: str | None = None`,
  `share_with: str | None = None` (only when `shared` is true), `shared_with: tuple[int, ...] = ()` (ids from
  `shared_with[].id`). No `player_names`, `description`, `content`.
- `@dataclass(frozen=True) class JobPlayer: user_id: int | None; owner: str | None; config_id: int | None; filename: str`
- `@dataclass(frozen=True) class JobView: id: int; name: str | None; status: str  # queued|running|succeeded|failed|cancelled;
  game_id: int | None; error: str | None; queue_position: int | None; host_after: bool; players: tuple[JobPlayer, ...]`
- `parse_job(raw) -> JobView` — `ValueError` without id; unknown status kept as text; `players` only from mappings.
- `JOB_FINISHED = frozenset({"succeeded", "failed", "cancelled"})`.
- `@dataclass(frozen=True) class FriendView: id: int; username: str; at: float | None  # since / asked_at;
  shared_configs: int = 0; games_together: int = 0`
- `@dataclass(frozen=True) class FriendsOverview: friends: tuple[FriendView, ...]; incoming: tuple[...]; outgoing: tuple[...]`
- `parse_friends(raw) -> FriendsOverview` — `ValueError` when not a mapping with list values for the three keys;
  `suggestions` is ignored (out of scope); entries without id/username skipped.

### `apbot/services/friends.py`
```
FRIENDS_TTL = 60.0; MAX_CACHED = 500
class FriendsUnsupported(UserFacingError):
    # "This manager has no friends feature yet. Ask an admin to update the Archipelago Manager."
class FriendDirectory:
    def __init__(self, client: ManagerClient, *, now=time.monotonic)
    async def overview(self, owner: int, token: str, *, quick: bool = False) -> FriendsOverview
        # cached by owner; on miss parse_friends(await client.friends(token, quick=quick)).
        # NotFound -> raise FriendsUnsupported (an older manager has no /api/friends route; FastAPI answers 404).
        # ValueError from parsing -> raise ManagerUnavailable(None, "The manager sent a friend list the bot does not know")
    def forget(self, owner: int) -> None
    async def library(self, owner: int, token: str) -> tuple[ConfigView, ...]    # P3
        # own + friends' shared configs: parse_configs(await client.list_configs(token, quick=True, friends=True)), cached
    def cached_library(self, owner: int) -> tuple[ConfigView, ...] | None        # P3
    def forget_library(self, owner: int) -> None                                 # P3
```
Rule: every command that writes friend or sharing data (`/friend add`, `/config share`) first calls
`overview(...)` (with `call_as`), so an old manager stops with `FriendsUnsupported` before any POST/PUT — an old manager
answers those with 405 or, for sharing, might ignore `share_with` (Decision 9).

### Commands (names per Decision 1; all replies ephemeral; all need a link)
- **P1 `/generate`** (`apbot/cogs/generate.py`, `GenerateCog(bot, *, sleep=asyncio.sleep)`), options:
  `config` (required), `config2` … `config10` (optional) — `Range[str, 1, 20]`, autocomplete `config_choices` in P1,
  `generate_config_choices` in P3 (Decision 3); `name: Range[str, 1, 120] | None`; `spoiler: int` choices
  0 "No spoiler log" / 1 "Basic" / 2 "Playthrough" / 3 "Full", default 1; `race: bool = False`;
  `seed: Range[str, 1, 18] | None` (digits only, else `UserFacingError("The seed must be a whole number")`);
  `start: bool = True` (→ `host_after`). `guild_only` and `require_role("organizer")` per Decision 2.
- **P1 `/game_archive game: action:`** (`GamesCog`), `action` choices `archive` / `restore`, autocomplete
  `archive_game_choices`; **`/game_delete game:`**, autocomplete `delete_game_choices`. Role gate per Decision 2.
- **P2 `/friends`** — the list with the `FriendsView`. **`/friend add member: username:`** — `member:
  discord.User | discord.Member | None`, `username: Range[str, 1, 64] | None`; exactly one.
- **P3 `/config share config: audience:`** — `config` autocomplete `config_choices` (own configs); `audience` choices
  `everyone`, `friends`, `chosen`, `stop`.

### Autocomplete (`apbot/autocomplete.py`)
- `archive_game_choices(interaction, current)` — `bot.my_games.games(id, token, quick=True)` (fallback
  `cached` / nothing, never raises, like `game_choices_visible`), keep `can_archive`; `action == "restore"`
  (`interaction.namespace.action`) → only `finished_at is not None and return_blocked is None`; else only
  `finished_at is None`. Then `game_choices(...)`.
- `delete_game_choices` — same source, keep `can_manage`, all games.
- P3 `generate_config_choices` — `bot.friend_directory.library(...)`; leave out ids already picked in the other
  `config*` options (`interaction.namespace`); own configs: `clip(name, CHOICE_NAME)`; a friend's: name clipped to
  `CHOICE_NAME - len(suffix)` + `SEPARATOR + t("from {owner}", lang, owner=owner)`. Own = `owner_id ==
  bot.links.get(id).manager_user_id`. Errors → `cached_library` or `[]`.

### Dynamic buttons (P2, `apbot/ui/dynamic.py`)
- `FRIEND_ID = "apbot:fr:{action}:{user}"`, template `r"apbot:fr:(?P<action>accept|decline):(?P<user>[0-9]{1,9})"`.
- `class FriendAnswerButton(DynamicItem[Button])` — accept: label "Accept" (primary) → `accept_friend`; decline:
  "Decline" (secondary) → `remove_friend`. Callback exactly like `HandoverAcceptButton`: clicker's `user_token` before
  defer, `defer(ephemeral=True, thinking=True)`, `call_as`, `_remove_buttons`, notice "You are friends now" /
  "You declined the friend request"; `bot.friend_directory.forget(clicker)`. The id carries only the asker's manager
  user id; the manager only lets the clicker accept requests addressed to them.
- `friend_request_view(other_id, lang) -> View(timeout=None)`.

### UserSync / alerts (P2, P3)
- `apbot/services/user_sync.py`:
  - `FRIEND_REQUEST = "friend_request"`; `@dataclass(frozen=True) class FriendRequestEvent: discord_user_id: int;
    from_id: int; from_name: str`.
  - `listen(..., on_friend_requests=None, wants_friend_requests=None)` — `wants_friend_requests: Callable[[int], bool]`.
  - `sync_user`: after the games part succeeded, if `wants_friend_requests(discord_id)`: `_sync_friends(link, token)`;
    else drop `self._incoming[discord_id]`.
  - `_sync_friends`: skip while `self._friends_missing_until > now` (monotonic); `parse_friends(await
    client.friends(token))`; `TokenInvalid` → `token_refused(link)`; `NotFound` → set `_friends_missing_until = now +
    FRIENDS_RETRY` (3600 s), log debug once; other `ManagerError`/`ValueError` → log debug, keep state. New ids =
    incoming ids − previous; first look (no previous) only remembers. Events → `on_friend_requests(events)`.
  - `forget` / `_drop` also clear `_incoming`.
- `apbot/services/alerts.py`:
  - `async handle_friend_requests(events)` — per person, if `FRIEND_REQUEST` enabled: one DM per request:
    `Embed(title=t("Friend request", lang), description=t("**{user}** wants to be your friend on the manager", ...),
    colour=ACCENT)`, `view=friend_request_view(from_id, lang)`, via `_send`.
  - P3 `async friend_game(link, *, by: str, game: str | None, configs: Sequence[str])` — if `FRIEND_GAME` enabled:
    DM `t("**{user}** is generating a game with your config {configs}: **{game}**. Once it is ready it is among your
    games.")`; `game` None → `t("a new game")`; config names `safe_text`, joined ", ".

## Security and user data
- No new secret. Every call uses the caller's (or, for DM buttons, the clicker's) linked token via `Access`; the bot
  token is never used for these features. Tokens never logged; log only `exc.detail` / class names.
- The manager is the authority: `/generate` (users_can_generate, limits, friendship, shared-with checks), archive
  (`can_archive`), delete (`manages`), friends, sharing (owner only). The bot's pre-checks (`can_archive`,
  `can_manage`) only avoid a pointless confirmation; the manager's refusal is shown as it is.
- Organizer role on top per Decision 2 (`require_role` before `user_token`, like `_control`).
- Write access: archive/delete/generate/friends/share need a *Read and change* token; `call_as` explains a read-only one.
  API tokens may use all these routes (`_token_refusal` only blocks tokens/auth/users).
- Privacy: everything is ephemeral or a DM to the person concerned. `/friends` shows manager usernames only (no
  Discord identities of others; the bot does not reveal which friends linked Discord). `/friend add member:` shows the
  member's mention, not their manager username. Friend-request DMs contain only the asker's manager username.
  Job errors are shown only to the job's creator (as in the web UI), cut with `safe_text`.
- Sharing with everyone asks for confirmation first (Decision 11): the config's content, player names included,
  becomes readable by every player.
- Delete is irreversible on the manager: danger `ConfirmView`, name of the game in the question.
- Custom ids carry only numeric ids, parsed with `[0-9]` regexes; all Discord option text validated before use.

## UI
Reused: `ConfirmView` (60 s, Cancel), `AuthorView`, `MultiSelectView`, `notice_embed`, `error_embed`,
`open_in_manager_view`, `game_state_embed`, colours `ACCENT/SUCCESS/WARNING/ERROR`, `SEPARATOR`, `NAME_LIMIT`,
`fit_lines`/`_list_embed` "…and N more". Text labels only, no emoji (ui.md).
- `/generate` progress (`apbot/ui/generate.py` `job_embed(job, lang)`): title = job name or `t("New game")`;
  description by status: queued → "Waiting to be generated (place {n} in the queue)" / without position "Waiting to be
  generated"; running → "Generating…"; succeeded → "Generated. The game is ready." plus, with `host_after`, "Its server
  starts now." (SUCCESS); failed → "Generation failed" + `safe_text(error, 900)` in a code-free line (ERROR);
  cancelled → "Cancelled" (WARNING). Field "Players": `filename` stems, a friend's one with `SEPARATOR + owner`
  (`fit_lines`, cap 1024). `GenerationView(AuthorView)`: one danger button "Cancel generation" while queued/running.
  On success the view is replaced by `open_in_manager_view(site_url, f"/games/{game_id}")`; on the poll limit by
  notice "Still generating. Follow it in the manager, or look at /games later." + `open_in_manager_view(...,
  f"/jobs/{id}")`.
- Refusal before queueing: `error_embed(t("The game could not be generated: {detail}", detail=safe_text(detail, 300)))`
  — covers missing games ("These games are not installed: …"), the player limits (worlds / unfinished games), "only
  administrators can generate", configs not shared / not a friend.
- `/game_archive`: confirm "Move **{game}** to the archive? Its server stops. It is listed as finished if every player
  reached their goal, otherwise as aborted." (button "Move to archive"); restore: "Take **{game}** out of the archive?"
  (button "Back to running games"). Result: `game_state_embed` of the answer plus line "**{game}** is in the archive
  ({outcome})" / "**{game}** is back among the running games".
- `/game_delete`: danger confirm "Delete **{game}** for good? Its files, save and history are removed from the manager.
  This cannot be undone." (button "Delete game") → notice "**{game}** was deleted" (SUCCESS).
- `/friends` (`apbot/ui/friends.py` `friends_embed(overview, lang)`): title "Your friends"; fields "Requests for you",
  "Your requests", "Friends" (only non-empty ones), lines `**name**` + `SEPARATOR` + `<t:at:R>`; friends add
  "shares {n} configs with you" when n > 0. All empty → description "You have no friends on the manager yet. Add
  someone with /friend add." and no view. `FriendsView(AuthorView, timeout=SELECT_TIMEOUT)`: one select "Choose a
  person" (incoming, then outgoing, then friends; max `SELECT_OPTIONS`; option description "Asks to be your friend" /
  "Your request" / "Friend"). Choosing replaces the message with a notice about that person and buttons: incoming →
  "Accept" (primary) + "Decline"; outgoing → "Take back request"; friend → "Remove friend" (danger) — the second
  click is the confirmation. Result notice: "You and **{user}** are friends now" / "You declined **{user}**'s request" /
  "You took back your request to **{user}**" / "**{user}** is no longer your friend. Configs you shared only with
  each other by name are no longer shared."
- `/friend add`: "You asked **{user}** to be your friend. They see it in the manager." / "You and **{user}** are friends
  now: they had asked you already."
- `/config share`: success notices "**{config}** is shared with every player" / "with all your friends" / "with:
  {names}" / "**{config}** is no longer shared". `chosen`: `MultiSelectView` of friends (max 25, current
  `shared_with` preselected, placeholder "Choose friends"); nothing chosen →
  `UserFacingError("Choose at least one friend, or pick stop to stop sharing")`; no friends →
  `UserFacingError("You have no friends to share it with yet. Add some with /friend add")`.
- Alerts labels: `friend_request` "Someone asks to be my friend", `friend_game` "A friend puts me in a game they
  generate".
- Unsupported manager: `FriendsUnsupported` text in `error_embed` via the normal error handler.

## Steps

### P1 — generate, archive, delete
1. `views.py`: `GameSummary` fields; `JobPlayer`, `JobView`, `parse_job`, `JOB_FINISHED`.
2. `client.py`: `start_job`, `job`, `cancel_job`, `archive_game`, `delete_game`.
3. `ui/generate.py`: `job_embed`, `GenerationView` (button callback: `await interaction.response.defer()`; `await
   bot.access.run(interaction, lambda tk: bot.manager.cancel_job(tk, job_id))`; `Conflict` → ignore (already over);
   set `self.cancel_requested = True`; `on_error` → `client.on_app_command_error` like `MultiSelectView`).
4. `cogs/generate.py` `/generate`:
   1. `lang`; role gate (Decision 2); collect `config`…`config10` in order, skip None, `number_or_none` each
      (None → `UserFacingError("Pick a config from the list")`), duplicates → `UserFacingError("The same config was
      picked twice")`; parse seed.
   2. `token = access.user_token(...)`; `defer(ephemeral=True)`.
   3. `raw = await access.call_as(..., start_job(...))`; `except (BadRequest, Conflict, Forbidden, NotFound) as exc` →
      `reply(error_embed(... "The game could not be generated: {detail}" ...), ephemeral=True)` and return.
   4. `job = parse_job(raw)`; `bot.my_games.forget(user)`; `reply(embed=job_embed(job), view=GenerationView, ephemeral=True)`.
   5. Loop `JOB_POLLS = 84` × `JOB_POLL_INTERVAL = 10` s (< Discord's 15-minute interaction limit): `await
      self._sleep(...)`; `job = parse_job(await access.call_as(..., manager.job(tk, id)))`, `ManagerUnavailable` →
      continue; `edit_original_response(embed=job_embed(job), view=...)`; break when `job.status in JOB_FINISHED`.
   6. Finished: view None (or game link on success); on success `bot.my_games.forget(user)` and `await
      bot.cache.refresh()` inside `try/except ManagerError` (log debug). Not finished → "Still generating" notice.
      `GenerationView.stop()` in every case.
5. `autocomplete.py`: `archive_game_choices`, `delete_game_choices`.
6. `cogs/games.py`: `/game_archive`, `/game_delete`:
   1. `parse_game_id`; role gate; `token = user_token`; `defer(ephemeral=True)`.
   2. `detail = parse_game_detail(await call_as(manager.game))` (`NotFound` → existing "That game was not found…").
   3. Pre-checks: archive needs `can_archive` else `UserFacingError("Only whoever runs this game, or the player who
      generated it, can move it to the archive")`; `action == "archive"` and `finished_at` set →
      `UserFacingError("That game is in the archive already")`; `restore` and `finished_at is None` →
      `UserFacingError("That game is not in the archive")`; delete needs `can_manage` else `UserFacingError("Only the
      organizer of this game and administrators can delete it")`. `return_blocked` is left to the manager (its 409
      text is shown).
   4. `ConfirmView.ask(...)`; not confirmed → return.
   5. Call `archive_game(..., archived=action == "archive")` / `delete_game`; refusals (`Conflict`, `Forbidden`) pass to
      the error handler ("The manager answered: …"). Then `edit_original_response(embed=..., view=None)`;
      `bot.my_games.forget(user)`; `await bot.cache.refresh()` (guarded as in 4.6). Relay removes threads/roles of a game
      that is gone/finished on its next `maintain` (already does).
7. `bot.py`: `await self.add_cog(GenerateCog(self))`.
8. i18n DE/FR/NL for all P1 strings and command/option names/descriptions/choices.
9. README: new "Generating and archiving" table (`/generate`, `/game_archive`, `/game_delete`; who may: linked users,
   organizer role if set; manager limits for players: 15 worlds, 3 unfinished games, 1 running; the bot follows the
   generation for up to 14 minutes). `/setup role` row: list the commands the organizer role limits.
10. CHANGELOG `[Unreleased]` `### Added`: "`/generate` generates a game from your configs and follows it; `/game_archive`
    moves a game to the archive and back; `/game_delete` deletes one. All act as you, after a confirmation for archive
    and delete."
11. Tests P1; `ruff check .`; `pytest`.

### P2 — friends list, add, request alerts
12. `views.py`: `FriendView`, `FriendsOverview`, `parse_friends`.
13. `client.py`: `friends`, `ask_friend`, `accept_friend`, `remove_friend`.
14. `services/friends.py`: `FriendsUnsupported`, `FriendDirectory.overview/forget`. `bot.py`:
    `self.friend_directory = FriendDirectory(self.manager)`.
15. `cogs/common.py`: move `SlotsCog._recipient` to `resolve_username(bot, member, username) -> str | None` (same
    behaviour and messages); `slots.py` calls it.
16. `ui/friends.py`: `friends_embed`, `FriendsView` (select → action buttons; each action: `defer()`, `access.run`,
    `friend_directory.forget(author)`, `edit_original_response(embed=notice, view=None)`; `on_error` like
    `MultiSelectView`).
17. `cogs/friends.py`: `FriendsCog` with `/friends` (`user_token`, `defer(ephemeral=True)`, `forget` then
    `overview(...)` via `call_as`, reply embed + view) and `friend` group with `add` (`resolve_username`; None →
    `UserFacingError("Choose a member or type a manager username")`; `user_token`; defer; `overview` via `call_as`
    (support check); `ask_friend`; `forget`; reply). `bot.py`: add cog.
18. Migration `004_friend_alerts.sql` (Data). `live.py`: `ALERT_KINDS` += `"friend_request"`. `embeds.py` label.
19. `ui/dynamic.py`: `FriendAnswerButton`, `friend_request_view`; `bot.py` `add_dynamic_items(..., FriendAnswerButton)`.
20. `user_sync.py`: friend polling as in Interfaces. `alerts.py`: `handle_friend_requests`. `bot.py` `listen(...,
    on_friend_requests=self.alerts.handle_friend_requests, wants_friend_requests=lambda d: "friend_request" in
    self.alert_prefs.enabled(d))`.
21. `linking.py`: `friend_directory.forget(id)` next to `my_games.forget` on `/link` and `/unlink`.
22. i18n, README ("Friends" table; `/alerts` list gets the new kind; Privacy: what is polled for whom), CHANGELOG:
    "`/friends` lists your friends and requests with buttons to accept, decline, take back or remove; `/friend add`
    asks a member or a manager user; a new alert sends a direct message with Accept and Decline for each new friend
    request."
23. Tests P2; ruff; pytest.

### P3 — generate with friends, config sharing
24. `views.py`: `ConfigView` fields. `client.py`: `list_configs(friends=)`, `share_config`.
25. `services/friends.py`: `library`, `cached_library`, `forget_library`.
26. `autocomplete.py`: `generate_config_choices`; `cogs/generate.py` switches the ten `config*` options to it.
27. `/generate` after step 4.4: friend DMs — `me = bot.links.get(user).manager_user_id`; group `job.players` by
    `user_id` not in `{None, me}`; for each: `link = bot.links.by_manager_user(uid)`; if link:
    `await bot.alerts.friend_game(link, by=<caller's manager_username>, game=job.name, configs=[filename stems])`.
    Sent once, when the job is queued (Decision 6). `friend_directory.forget_library(user)` not needed (library
    unchanged).
28. `live.py`: `ALERT_KINDS` += `"friend_game"`; `embeds.py` label; `alerts.py` `friend_game`.
29. `cogs/configs.py`: `ConfigsCog` with group `config` → `share`:
    1. `number_or_none(config)`; `user_token`; `defer(ephemeral=True)`.
    2. `overview = await call_as(friend_directory.overview(..., quick=False))` (support check; also the friend list).
    3. Find the config in `parse_configs(await call_as(list_configs(tk)))` by id (own only; not found →
       `UserFacingError("Pick one of your configs from the list")`) — gives name and current `shared_with`.
    4. `everyone` → `ConfirmView` (button "Share with everyone") then `share_config(shared=True,
       share_with="everyone")`; `friends` → `share_config(shared=True, share_with="friends")`; `stop` →
       `share_config(shared=False)`; `chosen` → no friends → error; else `MultiSelectView` whose `save` calls
       `access.run(... share_config(shared=True, share_with="chosen", friends=[ints]))` and returns the notice.
    5. After success: `signup_directory.forget_configs(user)`, `friend_directory.forget_library(user)`.
    `bot.py`: add cog.
30. i18n, README ("Sharing configs" row; `/generate` row mentions friends' configs and that picking one puts that
    friend in the game and DMs them if they turned it on), CHANGELOG: "`/generate` also offers the configs your friends
    share with you; the friend gets a direct message if they turned that alert on. `/config share` shares one of your
    configs with everyone, all your friends or chosen friends, or stops sharing it."
31. Tests P3; ruff; pytest.

## Tests
P1
- `start_job` sends exactly the body above to `POST /api/jobs`; `job`, `cancel_job`, `archive_game` (`{"archived":
  true|false}`, CONTROL_TIMEOUT), `delete_game` (`DELETE`, CONTROL_TIMEOUT); none retried.
- `parse_job`: all statuses; missing id → ValueError; players parsed, junk skipped. `GameSummary` reads
  `can_manage/can_archive/return_blocked`, defaults False/None.
- `/generate` unlinked → not-linked error before defer; role gate applies first when set (Decision 2).
- Duplicate configs, non-number config, bad seed → error before any manager call.
- Manager 409 with "These games are not installed: X" → ephemeral "The game could not be generated: These games are
  not installed: X"; 409 limit text and 403 "Only administrators…" shown the same way; nothing raised.
- Happy path with fake sleep: queued (position shown) → running → succeeded: final embed SUCCESS, game link view,
  `my_games.forget` called, `cache.refresh` awaited; failed → error text shown; cancelled → "Cancelled".
- A `ManagerUnavailable` during a poll is skipped; after 84 polls without end → "Still generating" with `/jobs/{id}` link.
- Cancel button: only the author can press; calls `cancel_job` with the clicker's token; 409 ignored.
- `/game_archive archive` on an unfinished game with `can_archive` → confirm → `archive_game(archived=True)` →
  "is in the archive (aborted|finished)"; cancel → no call. `restore` on a finished game → `archived=False`.
- Pre-checks: no `can_archive` → error, no confirm; archive on finished / restore on unfinished → errors.
- `/game_delete`: no `can_manage` → error; confirm uses danger style; confirmed → `delete_game`; refusal 403 shown
  as "The manager answered: …".
- `archive_game_choices`: only `can_archive`; restore lists only finished, non-blocked games; manager error → cached
  or empty, no exception. `delete_game_choices`: only `can_manage`.
- i18n catalog test passes for all new strings and command names.

P2
- `parse_friends`: three lists parsed; `suggestions` ignored; broken shape → ValueError.
- `FriendDirectory.overview`: cache hit within 60 s; `NotFound` → `FriendsUnsupported` with the clear text; nothing
  cached on error; `forget`.
- `/friends`: empty → "no friends yet" text, no view; with data → three fields; select options ordered
  incoming/outgoing/friends, ≤ 25; choosing an incoming person shows Accept/Decline; Accept → `accept_friend(id)` with
  the author's token; Decline / Take back / Remove → `remove_friend(id)`; another user pressing → "not for you".
- `/friends` on an old manager (404) → "This manager has no friends feature yet…" ephemeral.
- `/friend add member:` linked → `ask_friend(<their manager username>)`, reply shows the mention; unlinked member →
  existing "{member} has not linked a manager account"; both/neither → errors; `username:` path; status `accepted` →
  "friends now" text; old manager → `FriendsUnsupported`, `ask_friend` never called.
- `resolve_username` keeps `/handover offer` behaviour (existing slot tests still pass).
- Migration 004: a DB at version 3 with alert rows migrates, keeps rows, accepts `friend_request` and `friend_game`,
  still rejects an unknown kind; fresh DB migrates to 4.
- `UserSync`: friend polling only for people with `friend_request` on; first look quiet; a new incoming id → one
  `FriendRequestEvent`; same id next time → none; turning the alert off and on → quiet first look again; `TokenInvalid`
  on `/api/friends` → `token_refused`; `NotFound` → no further friend calls for 3600 s, games sync unaffected.
- `AlertEngine.handle_friend_requests`: DM with title "Friend request", asker's name escaped, `friend_request_view`;
  nothing when the kind is off; muted recipient skipped.
- `FriendAnswerButton`: custom id round-trips via the template; non-digit ids do not match; accept/decline call the
  right endpoint with the clicker's token; unlinked clicker → not-linked error; buttons removed after success.
- `/alerts` offers the new kind; `/mydata` lists it when on.

P3
- `list_configs(friends=True)` requests `/api/configs?friends=1`; default path unchanged. `ConfigView` reads
  `user_id/owner/share_with/shared_with`; `share_with` None when not shared.
- `generate_config_choices`: own configs plain, friends' with "— from {owner}" in the caller's language, clipped to 100;
  already-picked ids left out; errors → cached/empty.
- `/generate` with a friend's config: after queueing, `friend_game` called once per friend with the caller's manager
  username, the job name and that friend's config names; friend not linked → no DM; own configs → no DM; friend with
  `friend_game` off → no DM; a refused generation → no DM.
- `/config share` each audience sends the right body; `everyone` asks first (cancel → no call); `chosen` preselects
  current friends, empty choice → error; no friends → error; a config not in the caller's own list → error;
  old manager → `FriendsUnsupported` before any PUT; caches forgotten after success.

## Decisions for the programmer
1. **Command names** — Discord cannot have both a `/game` command and `/game archive` subcommands, nor `/friends` and
   `/friends add`. A: `/game_archive`, `/game_delete` (like `/hint_location`), `/friends` + `/friend add` (like
   `/signups` + `/signup …`), `/config share`; B: turn `/game` into a group (`/game show|archive|delete`) — renames an
   existing public command; C: a `/friends` group with `list` and `add`. → **A**.
2. **Organizer role gate** — A: `/generate`, `/game_archive`, `/game_delete` need the organizer role when one is set,
   like `/start`/`/stop`/`/restart`; B: only archive and delete; C: none (manager rules only). Friends and sharing are
   never gated. → **A**.
3. **How configs are picked in `/generate`** — A: `config` + `config2`…`config10` options with autocomplete (works with
   200 configs, typed search); B: a select menu after the command (max 25 configs shown). → **A** (10 options).
4. **DB migration** — the new alert kinds need `004_friend_alerts.sql`, which rebuilds `alert_prefs` with a longer
   CHECK list (rows kept). A: do it; B: drop the CHECK and validate kinds in code only. → **A**.
5. **New DMs opt-in** — A: `friend_request` and `friend_game` are new `/alerts` kinds, off until chosen, like all
   alerts; B: always sent to linked users. → **A**.
6. **When the friend gets the DM in `/generate`** — A: when the generation is queued ("is generating a game with your
   config"); B: when the game is ready (lost if it takes over 14 minutes or the bot restarts). → **A**.
7. **Friend-request detection / polling rate** — the manager has no per-user push (its Discord pings are a server-wide
   webhook), so polling is the only source. A: inside the existing `UserSync` pass every 120 s, only for people who
   turned the alert on (one extra `GET /api/friends` per such person per pass); B: own job every 300 s. Requests that
   arrive while the bot is down are not alerted (first look is quiet). → **A**.
8. **Following a generation** — A: poll `GET /api/jobs/{id}` every 10 s for up to 14 minutes (Discord's 15-minute
   limit for editing the reply), then "still generating" with a link to the job, plus a "Cancel generation" button;
   B: same without the Cancel button; C: also keep watching in the background and DM the result. → **A**.
9. **Manager without friends** — A: a 404 on `GET /api/friends` (the caller's token, cached 60 s) means the feature is
   missing; `/friends`, `/friend add`, `/config share` check it before any write and show "This manager has no friends
   feature yet. Ask an admin to update the Archipelago Manager."; the poll then pauses friend checks for an hour;
   B: compare `/api/health` `version` with a minimum version (needs the exact manager version that added friends).
   → **A**.
10. **Public vs private** — A: every reply ephemeral, DMs only to the person concerned, nothing posted in channels, no
    Discord identities of friends shown; B: also post a public note when a game is generated. → **A**.
11. **Sharing with everyone** — A: confirmation first (content and player names become readable by every player);
    B: no confirmation. → **A**.

**Resolved 2026-11-11:** the programmer approved the plan with every recommended option (1A–11A).

## Risks / follow-ups
- Manager behaviour: a player's `/api/games` list only includes games they have a slot in (or `visibility = all`);
  a game a player generated only from friends' configs and with visibility "participants" is invisible to them, so
  `/game_archive` cannot suggest it. Manager-side issue; report, do not work around.
- The 15-minute interaction limit caps how long the reply can follow a job (Decision 8).
- `MultiSelectView.save` answers the select within Discord's 3 s; the share PUT is a quick DB write, but a slow
  manager would make that interaction fail (the error handler then answers in a follow-up).
- More than 25 friends/requests: the embed lists all (with "…and N more"), the select and the chosen-friends picker
  show the first 25; the rest via the web UI.
- Friend polling adds at most one request per opted-in person per 2 minutes.
- On an old manager `?friends=1` is ignored (only own configs are suggested) — harmless.
