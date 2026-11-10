# 001 — Archipelago Discord bot for the Archipelago Manager

Status: draft
Type: new project
Version impact: minor — first release. Recommended: M1 ships as `0.1.0`, every later milestone is one minor (`0.2.0` … `0.5.0`), see Decisions. Nothing is released, tagged or published without the programmer's explicit instruction.

## Goal
A Discord bot, shipped as a multi-arch Docker image (amd64 + arm64), that sits next to an existing Archipelago Manager (`NiklaskoDev/ArchipelagoManagerDocker`, manager ≥ 1.3.0) and uses only its REST API. Players see games and sign-ups, link their manager account, follow their games in per-game threads, get opt-in DMs, use tracker/hints/text client, manage their slots and enter sign-ups from Discord, in EN/DE/FR/NL. Organizers start/stop games and run sign-up rounds; server admins configure everything with `/setup`.

Scope = core + all 10 scout suggestions (nothing declined). Explicitly out of scope: an Unraid template for the bot; message-content intent and prefix commands; any change to the manager; creating/deleting manager tokens or accounts from Discord (the manager forbids it for tokens); editing sign-up options/allowed games/mystery weights, uploading games, admin console, backups (web UI only); slot unclaim and assignment; showing room passwords anywhere.

## Reuse
Manager repo (read-only reference, path below as `MGR`) — consumed, never changed:
- API auth: `Authorization: Bearer apm_…`; tokens are `read` (GET only) or `write`. Tokens are refused on `/api/tokens*`, `/api/auth/*` except `GET /api/auth/status`, and non-GET `/api/users*` (`MGR/manager/archipelago_manager/main.py` `_token_refusal`). Error bodies are `{"detail": str}`; 401 "This API token is not valid (any more)", 403 "This API token can only read".
- Endpoints used (exact shapes in **Interfaces → ManagerClient**), all from `routes/games.py`, `routes/signups.py`, `routes/configs.py`, `routes/auth.py`, `main.py`.
- Permission rules the bot relies on (the manager enforces them; the bot never re-implements them): `get_game` (visibility `all`, participant, offered-to, or manager), `can_control` (manager, or participant when setting `users_can_start_rooms`), `wake` (participant or manager), `_own_slot` (text client, action, hint status, names: slot owner only, admins excluded), tracker (owner or game manager), status (owner or manager), claim (game `claimable`, `single_claim`), handover rules, sign-up `_managed` (organizer who created it or admin), entries (own configs).
- `game_summary` leaks `password` to participants/managers and `_detail` adds `server_password`, `discord_webhook_url`, `notes`, `spectate` for managers → the bot uses a **whitelist** of fields for anything public.
- Activity feed (`games/activity.py`): `GET /api/games/{id}/activity?after=N` → `{"entries":[{seq, at, kind, …}], "last": int}`; kinds and fields: `goal|release|collect {player}`, `join {player, game}`, `part {player}`, `server {text}`, `hint {receiver, item, location, finder, entrance?, status}`, `item {finder, item, receiver, location}`, `chat {player, text}`, `status {player, status, note?}`, `handover {player, from, to?, note?}`, `handover_done {player, to, from?}`. In-memory window of 1000 entries; `seq` persists across manager restarts.
- Text client (`games/textclient.py`): session per (game, slot, user), closes after 90 s without a poll; `POST …/client` opens it; messages `{seq, time, type, parts:[{text, kind?…}], mine}`; `say` rejects text starting with `/`, max 1000 chars; `client/names` returns `{items:[], locations:[]}` only once a session exists.
- Tracker (`ap_helper.cmd_tracker` + `rooms.slot_rules`): `{slot, name, game, goal, items:[{item, from, location, flags, start}], locations:[{location, checked}], hints:[{item, receiver, for_me, location, finder, in_my_world, entrance, found, flags, status, finder_slot, location_id}], hints_used, items_left, hint_points, hint_cost, points_per_check, release, collect}` (`release`/`collect` ∈ available|after_goal|automatic|disabled|done).
- Hint statuses: 10 no priority, 20 avoid, 30 priority, 40 found (`routes/games.py` `HINT_STATUSES`).
- Translation pattern: manager `notify/de.py` etc. — English text is the key, per-language dict `MESSAGES`, a test checks keys and placeholders (`tests/test_translations.py`, AST scan of `t("…")` calls). The bot copies this pattern.
- Docker: manager `Dockerfile` final stage (python:3.12-slim-bookworm, tini, tzdata, `docker/entrypoint.sh` with PUID/PGID 99:100 + `setpriv`, HEALTHCHECK), `docker-compose.yml` (service `archipelago-manager`, port 8080, image `ghcr.io/niklaskodev/archipelagomanagerdocker:latest`), `.github/workflows/ci.yml` (per-arch native runners `ubuntu-latest` / `ubuntu-24.04-arm`, push-by-digest, `imagetools create`, actions pinned by commit SHA), `ruff.toml` (py312, line 120, `E,F,W,B,UP`). Copy and trim these; do not reinvent.
- This repo: guideline template (`CLAUDE.md`, `.github/workflows/{ci,security,release}.yml`, `.github/dependabot.yml`, `.env.example`, `.gitignore`, `CHANGELOG.md`). Nothing else exists yet — every bot file below is new because there is no code to reuse.

## Code layout
Package `apbot/` at the repo root (justification: new project, mirrors the manager's single-package layout).

Repo root
- `apbot/__init__.py` — `__version__ = "0.1.0"` (version source of truth).
- `apbot/__main__.py` — `main()`: load `Config`, set up logging, open DB, run `ApBot`.
- `apbot/config.py` — `Config` dataclass + `load_config(env: Mapping[str,str]) -> Config`; raises `ConfigError` with the variable *name* only.
- `apbot/logging_setup.py` — `setup_logging(level)`; `RedactingFilter` that replaces `apm_[A-Za-z0-9_-]+`, Discord bot tokens and Fernet keys in every log record with `[redacted]`; sets `httpx`/`httpcore` loggers to WARNING.
- `apbot/heartbeat.py` — writes `DATA_DIR/heartbeat` (unix time) every 30 s while the gateway is connected; used by HEALTHCHECK.
- `apbot/bot.py` — `ApBot(commands.Bot)`: intents `Intents.none()` + `guilds`; `allowed_mentions=AllowedMentions.none()`; `setup_hook` sets the translator, loads cogs, starts services, syncs commands (global, or `DISCORD_DEV_GUILD_ID` only when set); `on_app_command_error` → localized ephemeral error.
- `apbot/errors.py` — `UserFacingError(key, **values)` (translated message), `NotLinkedError`, `MissingRoleError`.
- `apbot/manager/client.py` — `ManagerClient` (one `httpx.AsyncClient`), all endpoint methods, error mapping, GET retry.
- `apbot/manager/errors.py` — `ManagerError(status, detail)`, subclasses `ManagerUnavailable` (network, 5xx, 502/503/504), `TokenInvalid` (401), `ReadOnlyToken` (403 with detail "This API token can only read"), `Forbidden` (403), `NotFound` (404), `Conflict` (409), `TooManyRequests` (429), `BadRequest` (400/413/422).
- `apbot/manager/views.py` — pure functions turning manager JSON into whitelisted dataclasses: `GameSummary`, `GameDetail`, `SlotView`, `SignupSummary`, `SignupDetail`, `TrackerView`, `HintView`; **no password/server_password/webhook/notes/spectate field exists on any of them**.
- `apbot/i18n/__init__.py` — `LANGUAGES = ("en","de","fr","nl")`, `language_of(locale: discord.Locale | str | None) -> str`, `t(text: str, lang: str, **values) -> str`, `BotTranslator(app_commands.Translator)`.
- `apbot/i18n/de.py`, `fr.py`, `nl.py` — `MESSAGES: dict[str,str]` (replies, embeds, buttons), `COMMANDS: dict[str,str]` (command/option/choice names and descriptions).
- `apbot/db/database.py` — `Database` (stdlib `sqlite3`, WAL, `foreign_keys=ON`, `busy_timeout=5000`), `migrate()` applying `apbot/db/migrations/NNN_*.sql` in order inside a transaction, recording them in `schema_migrations`.
- `apbot/db/migrations/001_links_settings.sql` (M2), `002_live.sql` (M3), `003_signups.sql` (M5).
- `apbot/db/links.py` — `LinkStore`: `get(discord_id)`, `by_manager_user(id)`, `save(...)`, `mark_invalid(discord_id)`, `delete(discord_id)`, `set_locale(...)`, `all_active()`.
- `apbot/db/guilds.py` — `GuildSettingsStore`: `get(guild_id) -> GuildSettings` (defaults when absent), `update(guild_id, **fields)`.
- `apbot/db/live.py` — `GameSpaceStore`, `FeedCursorStore`, `AlertPrefStore`, `OverviewStore`, `ScheduledEventStore` (M3).
- `apbot/db/reminders.py` — `ReminderStore` (M5).
- `apbot/crypto.py` — `TokenCipher(keys: list[str])` on `cryptography.fernet.MultiFernet`: `encrypt(str) -> bytes`, `decrypt(bytes) -> str` (raises `TokenUndecryptable`), `needs_rotation(bytes) -> bool`.
- `apbot/access.py` — `Access` service: `public_client()`, `user_token(interaction) -> str` (raises `NotLinkedError`), `require_role(interaction, kind: Literal["organizer","admin"])`, `on_token_invalid(discord_id)`.
- `apbot/ui/style.py` — the embed design system (colours, limits, constants; see UI).
- `apbot/ui/format.py` — `escape(text)` (markdown + mentions), `clip(text, n)`, `ts(unix, style)`, `progress(a, b)`, `chunk_lines(lines, limit) -> list[str]`.
- `apbot/ui/embeds.py` — pure builders: `games_list_embed`, `game_detail_embeds`, `signups_embed`, `signup_detail_embed`, `tracker_embed`, `check_result_embed`, `overview_embed`, `link_status_embed`, `settings_embed`, `error_embed`.
- `apbot/ui/views.py` — `ConfirmView` (Confirm/Cancel, author-only, 60 s), `OpenInManagerButton` (link button).
- `apbot/ui/dynamic.py` — persistent `DynamicItem` buttons: `HandoverAcceptButton`, `HandoverDeclineButton`, `HintStatusButton` (custom_id templates in Interfaces).
- `apbot/autocomplete.py` — `game_choices_public`, `game_choices_mine`, `slot_choices_mine`, `claimable_slot_choices`, `item_choices`, `location_choices`, `signup_choices_open`, `signup_choices_managed`, `config_choices`, `my_entry_choices`; 60 s per-user cache.
- `apbot/cogs/games.py` — `/games`, `/game`, `/wake`, `/start`, `/stop`, `/restart`.
- `apbot/cogs/linking.py` — `/link` (modal), `/unlink`, `/mydata`.
- `apbot/cogs/setup.py` — `/setup …` group.
- `apbot/cogs/alerts.py` — `/alerts`.
- `apbot/cogs/tracker.py` — `/tracker`, `/hints`.
- `apbot/cogs/textclient.py` — `/hint`, `/hint_location`, `/remaining`, `/say`, `/release`, `/collect`.
- `apbot/cogs/slots.py` — `/status`, `/claim`, `/handover offer|cancel`.
- `apbot/cogs/signups.py` — `/signups`, `/signup show|join|withdraw|open|close|test`.
- `apbot/services/scheduler.py` — `Periodic(name, interval, coro)`: runs in a loop, logs and survives exceptions, cancellable.
- `apbot/services/game_cache.py` — `GameCache`: bot-visible games (bot token), refreshed every 60 s; `site_url` from auth/status.
- `apbot/services/user_sync.py` — `UserSync`: per active link every 120 s `GET /api/games` with the user's token → `my_slots`, `offered_slots`, state; emits `UserGameEvent`s (game online, slot offered) and role-membership data.
- `apbot/services/feeds.py` — `FeedPoller`: picks a reader token per game, polls activity, persists cursors, fans out to `Relay` and `AlertEngine`.
- `apbot/services/relay.py` — `Relay`: per-game thread + role lifecycle, filters, batching, formatting.
- `apbot/services/alerts.py` — `AlertEngine`: pure rules (`rules_for(entry, context) -> list[Alert]`) + DM sender with per-user batching.
- `apbot/services/overview.py` — pinned overview message + bot presence.
- `apbot/services/events.py` — Discord scheduled events sync.
- `apbot/services/text_client.py` — `TextClientBridge`: open session, wait for `connected`, say, collect answer.
- `apbot/services/signup_reminders.py` — deadline reminders.
- `apbot/services/timeparse.py` — `parse_local_datetime(text, tz) -> float` (`YYYY-MM-DD HH:MM`).
- `tests/…` — see Tests; `tests/fakes.py` (fake manager via `httpx.MockTransport`, fake Discord sender/thread/role objects).
- `requirements.txt` (pinned runtime), `requirements-dev.txt` (`-r requirements.txt` + pinned test/lint tools), `ruff.toml` (copy of manager's, `extend-exclude` removed), `pytest.ini` (`asyncio_mode = auto`, `testpaths = tests`).
- `Dockerfile`, `docker/entrypoint.sh` (copy of manager's, names changed `archipelago` → `apbot`, `DATA_DIR=/data`), `.dockerignore`, `docker-compose.yml` (bot + manager example).
- `README.md` — what it is, Discord application setup (scopes `bot applications.commands`, permissions below, no privileged intents), manager account + token setup, env vars, compose, commands per role, privacy (what is stored, `/mydata`, `/unlink`).
- `.env.example`, `.gitignore` (+ `/data/`), `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `.github/dependabot.yml`, `CHANGELOG.md`, `CLAUDE.md` Project block.

## Data
SQLite file `DATA_DIR/bot.sqlite3` (volume `/data`), stdlib `sqlite3`, plain-SQL versioned migrations, never edited once applied. All timestamps REAL unix seconds. Discord snowflakes INTEGER.

`schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at REAL NOT NULL)` — created by `migrate()`.

`001_links_settings.sql` (M2)
- `user_links(discord_user_id INTEGER PRIMARY KEY, manager_user_id INTEGER NOT NULL UNIQUE, manager_username TEXT NOT NULL, token_ciphertext BLOB, token_hint TEXT, status TEXT NOT NULL CHECK (status IN ('active','invalid')), locale TEXT NOT NULL DEFAULT 'en' CHECK (locale IN ('en','de','fr','nl')), linked_at REAL NOT NULL, checked_at REAL NOT NULL)` — `token_ciphertext` NULL when invalid; `token_hint` = last 4 chars only (shown in `/mydata`).
- `guild_settings(guild_id INTEGER PRIMARY KEY, language TEXT NOT NULL DEFAULT 'en' CHECK (language IN ('en','de','fr','nl')), timezone TEXT NOT NULL DEFAULT 'UTC', games_channel_id INTEGER, signups_channel_id INTEGER, overview_channel_id INTEGER, organizer_role_id INTEGER, admin_role_id INTEGER, scheduled_events INTEGER NOT NULL DEFAULT 0 CHECK (scheduled_events IN (0,1)), updated_at REAL NOT NULL)`.

`002_live.sql` (M3)
- `game_spaces(guild_id INTEGER NOT NULL, game_id INTEGER NOT NULL, thread_id INTEGER, role_id INTEGER, kinds TEXT NOT NULL, closed_at REAL, created_at REAL NOT NULL, PRIMARY KEY (guild_id, game_id))` — `kinds` = JSON list of relayed feed kinds.
- `feed_cursors(game_id INTEGER PRIMARY KEY, last_seq INTEGER NOT NULL, updated_at REAL NOT NULL)`.
- `alert_prefs(discord_user_id INTEGER NOT NULL REFERENCES user_links(discord_user_id) ON DELETE CASCADE, kind TEXT NOT NULL CHECK (kind IN ('item_received','hint_found','stuck_in_my_world','game_online','slot_offered')), PRIMARY KEY (discord_user_id, kind))` — row present = alert on (all off by default).
- `overview_messages(guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL, content_hash TEXT NOT NULL)`.
- `scheduled_events(guild_id INTEGER NOT NULL, source TEXT NOT NULL CHECK (source IN ('game_start','signup_deadline')), source_id INTEGER NOT NULL, event_id INTEGER NOT NULL, starts_at REAL NOT NULL, PRIMARY KEY (guild_id, source, source_id))`.

`003_signups.sql` (M5)
- `signup_reminders(guild_id INTEGER NOT NULL, signup_id INTEGER NOT NULL, deadline REAL NOT NULL, before_seconds INTEGER NOT NULL, sent_at REAL NOT NULL, PRIMARY KEY (guild_id, signup_id, deadline, before_seconds))`.

In memory only (rebuilt after restart, first pass primes without alerting): per-user game states and offers, hint caches, autocomplete caches, item/location name caches.

Deletion: `/unlink` deletes the `user_links` row (cascades `alert_prefs`). Leaving a guild deletes its `guild_settings`, `game_spaces`, `overview_messages`, `scheduled_events`, `signup_reminders` rows (`on_guild_remove`).

## Interfaces

### Environment (`apbot/config.py`)
| Name | Required | Default | Notes |
|---|---|---|---|
| `DISCORD_TOKEN` | yes | — | secret |
| `MANAGER_URL` | yes | — | `http(s)://host[:port]`, no path; trailing `/` stripped; e.g. `http://archipelago-manager:8080` |
| `MANAGER_API_TOKEN` | yes | — | secret; must match `^apm_[A-Za-z0-9_-]+$` |
| `BOT_ENCRYPTION_KEY` | yes (from M2) | — | secret; one or more comma-separated Fernet keys, first encrypts |
| `DATA_DIR` | no | `/data` | |
| `LOG_LEVEL` | no | `INFO` | |
| `DISCORD_DEV_GUILD_ID` | no | — | sync commands to this guild only (development) |
| `PUID` / `PGID` / `UMASK` / `TZ` | no | `99` / `100` / `022` / — | container only |

### Startup (`ApBot.setup_hook`)
1. `GET /api/health` → expects `{"ok": true, "version", "archipelago", "ready", "archipelago_error"}`. Unreachable → log error and retry every 30 s in the background; the bot still connects and commands reply "The manager cannot be reached right now". `version` < `MIN_MANAGER_VERSION = "1.3.0"` → log warning.
2. `GET /api/auth/status` with `MANAGER_API_TOKEN` → `user` null or 401 → log "MANAGER_API_TOKEN is not accepted by the manager" and exit code 2. `user.role != "user"` → log warning "The bot account should be a plain user so public views never include private games". Cache `site_url`.
3. From M2: decrypt-test every active link lazily (not at startup); re-encrypt with the first key when `needs_rotation`.

### ManagerClient (`apbot/manager/client.py`)
Constructor `ManagerClient(base_url, bot_token, *, transport=None)`; timeout `httpx.Timeout(10, connect=5)`; `slow` methods (config check, release/collect, hint) use 200 s / 30 s per call as noted. GET retried 3× on `ManagerUnavailable` with backoff 0.5/1/2 s; non-GET never retried. Every method takes `token: str` (bot or user) explicitly; the token never appears in URLs, logs or exceptions. Non-2xx → `ManagerError` subclass with `detail` from JSON (`detail` may be a list for 422 — join `msg`s).

| Method | HTTP | Body / query | Returns |
|---|---|---|---|
| `health()` | GET `/api/health` | — (no token) | dict |
| `auth_status(token)` | GET `/api/auth/status` | — | dict (`user: {id, username, role, discord_name…}|None`, `site_url`, `permissions`) |
| `list_games(token)` | GET `/api/games` | — | list of game summaries |
| `game(token, game_id)` | GET `/api/games/{id}` | — | game detail |
| `activity(token, game_id, after)` | GET `/api/games/{id}/activity` | `?after=` | `{entries, last}` |
| `wake/start/stop/restart(token, game_id)` | POST `/api/games/{id}/wake|start|stop|restart` | none | game detail |
| `tracker(token, game_id, slot)` | GET `/api/games/{id}/slots/{slot}/tracker` | — | tracker (30 s timeout) |
| `client_poll(token, game_id, slot, after)` | POST `…/slots/{slot}/client` | `{"after": int}` | `{state, error, last, messages, hint_points, hint_cost}` |
| `client_say(token, game_id, slot, text)` | POST `…/client/say` | `{"text"}` | `{ok}` |
| `client_names(token, game_id, slot)` | GET `…/client/names` | — | `{items, locations}` |
| `slot_action(token, game_id, slot, action)` | POST `…/action` | `{"action": "release"|"collect"}` | `{done, message}` (30 s) |
| `set_hint_status(token, game_id, slot, finder, location, status)` | PUT `…/hints/{finder}/{location}` | `{"status": 10|20|30}` | `{status}` (30 s) |
| `set_slot_status(token, game_id, slot, status, note)` | PUT `…/status` | `{"status": "playing"|"stuck"|"away"|null, "note"}` | game detail |
| `claim(token, game_id, slot)` | POST `…/claim` | none | detail + `configs_added` |
| `offer_handover(token, game_id, slot, to, note)` | PUT `…/handover` | `{"to": username|null, "note"}` | game detail |
| `cancel_handover(token, game_id, slot)` | DELETE `…/handover` | — | detail or `{"declined": true}` |
| `accept_handover(token, game_id, slot)` | POST `…/handover/accept` | none | detail |
| `list_signups(token)` | GET `/api/signups` | — | list |
| `signup(token, signup_id)` | GET `/api/signups/{id}` | — | detail with `entries`, `test` |
| `create_signup(token, name, description, deadline, slots_per_user)` | POST `/api/signups` | JSON | detail |
| `close_signup / test_signup(token, id)` | POST `/api/signups/{id}/close|test` | none | detail |
| `add_entry(token, signup_id, config_id)` | POST `/api/signups/{id}/entries` | `{"config_id"}` | detail + `warnings` |
| `remove_entry(token, signup_id, entry_id)` | DELETE `/api/signups/{id}/entries/{entry_id}` | — | detail |
| `list_configs(token)` | GET `/api/configs` | — | list (`id, name, games, player_names, missing_games…`) |
| `create_config(token, name, content)` | POST `/api/configs/text` | `{"name" ≤120, "content" ≤1 MiB}` | config |
| `check_config(token, content)` | POST `/api/configs/check` | `{"content"}` | `{ok, errors, warnings, players:[{name, games, errors, warnings}], missing_roms}` (200 s; 429 when a check of this user runs) |

Sign-up errors from the manager are `{"detail", "missing_games"}` — keep `missing_games` on `BadRequest/Conflict` as an attribute.

### Access rules (bot side, on top of the manager's)
- Public read views (`/games`, `/game`, `/signups`, overview, relay, scheduled events, reminders) use **only** the bot token and only whitelisted fields.
- Every other command uses the caller's linked token (`Access.user_token`). Not linked → ephemeral "Link your manager account first with /link". `TokenInvalid` → `LinkStore.mark_invalid` + ephemeral "Your manager token no longer works. Make a new one and run /link again". `ReadOnlyToken` → ephemeral "Your linked token can only read. Link a token with *Read and change* access".
- Discord-role gate (only if the role is configured in `/setup`): `organizer` role required for `/start`, `/stop`, `/restart`, `/signup open|close|test`; `admin` role **or** Manage Server for `/setup`. `/setup` is registered with `default_permissions=manage_guild` (decision 10).
- Buttons (DM or ephemeral) re-resolve the link from `interaction.user.id`; `custom_id` only carries ids, parsed as bounded ints; never identity.
- All sends use `AllowedMentions.none()` except role pings in relay "game online" posts (`AllowedMentions(roles=[that role])`).
- All replies to user-token commands are ephemeral. All errors are ephemeral.

### Slash commands (names are EN keys; DE/FR/NL localized via `BotTranslator`)
Game / slot options: `game` = autocomplete returning game id as string (validated `int`, else "Pick a game from the list"); `slot` = autocomplete of the caller's `my_slots` for that game, optional when they have exactly one.

M1 (bot token)
- `/games` — non-finished bot-visible games, running first then newest; per line: name, state, worlds `len(games)`, players `claimed_count/player_count`, goals `goal_count/player_count`; max 25 lines then "…and N more". Public reply.
- `/game game:` — detail embed(s): state, address `host:port` + "encrypted (wss)" when `tls`, AP version, planned start (`<t:…:F>` + `<t:…:R>`), progress `checked/total`, goals, slot lines `name — world — owner or "unclaimed" — checked/locations — online|offline|goal` (+ player status), split with `chunk_lines` to fit 1024/field, 25 fields, 6000 chars; "Open in manager" link button to `{site_url}/#/games/{id}` when `site_url` known. Public reply.
- `/signups` — open rounds (`open == true`), soonest deadline first: name, deadline `<t:…:R>`, `entry_count`/`user_count`, `slots_per_user`, creator. Public reply.

M2 (linked user)
- `/link` — opens modal "Link manager account" with one `TextInput` (label "API token", style short, min 10, max 200, required) and an explanation: create a token in the manager under *API tokens*, access *Read and change*, name it "Discord bot". On submit: defer ephemeral; validate `^apm_[A-Za-z0-9_-]+$`; `auth_status(token)` → user null / 401 → "This token is not valid"; user disabled → same; `manager_user_id` already linked to a different Discord user → "This manager account is already linked to another Discord account. Unlink it there first"; else encrypt + upsert, store `locale = language_of(interaction.locale)`, reply "Linked as **{username}**". The token value is never echoed, logged or stored in plain text.
- `/unlink` — `ConfirmView`; on confirm delete row (+ alert prefs), remove the user from per-game roles in all guilds, reply "Unlinked. Revoke the token in the manager under API tokens as well" (the bot cannot revoke it).
- `/mydata` — ephemeral export of everything stored about the caller: manager username, token hint `…abcd`, status, locale, linked/checked time, enabled alerts. ("Deletion and export must be possible", `docs/ai/security.md`.)
- `/wake game:` — user token; replies with state after the call (state field of returned detail).
- `/start|/stop|/restart game:` — role gate + user token; manager 403/409 detail shown.
- `/setup show` — settings embed + manager check (health version/ready, bot account username/role, site_url).
- `/setup manager` — runs the startup checks now and reports each (reachable, version ≥ min, token accepted, account role).
- `/setup channel kind:(games|signups|overview) channel:` — verifies the bot can send (and for games: create threads, manage threads; overview: manage messages) in that channel; else lists missing permissions.
- `/setup role kind:(organizer|admin) role:` and `/setup role_clear kind:`.
- `/setup language language:(en|de|fr|nl)` — guild language for feeds, overview, events, reminders.
- `/setup timezone timezone:` — autocomplete over `zoneinfo.available_timezones()` (filtered, 25); validated.
- `/setup events enabled:bool` (M3) — requires Manage Events permission check.
- `/setup feed game:` (M3) — ephemeral multi-select (`discord.ui.Select`, min 0, max 11) of feed kinds for that game's space; saves `game_spaces.kinds`.

M3
- `/alerts` — ephemeral multi-select of the 5 alert kinds, pre-selected from `alert_prefs`; saving replaces the set. Requires link.

M4
- `/tracker game: slot:` — tracker embed: received items count (+ last 10 progression items, flag bit 1), checked `n/total`, hint points/cost, release/collect rule, hints for me (not found, max 10), hints in my world (not found, max 10). Below: one `discord.ui.Select` of the unfound `for_me` hints (max 25) and three buttons "Mark as priority" (30), "No priority" (10), "Avoid" (20) → `set_hint_status`; on 409 show detail. View timeout 10 min, author only.
- `/hints game: slot:` — same embed, hints sections only (shortcut; reuses `tracker_embed(sections=("hints",))`).
- `/hint game: slot: item:` — `item` autocomplete via `TextClientBridge.names` (opens session on first call, returns "Loading names… try again in a moment" as the only choice until cached; cache 10 min). Runs `!hint {item}`.
- `/hint_location game: slot: location:` — same with locations, `!hint_location {location}`.
- `/remaining game: slot:` — `!remaining`.
- `/say game: slot: text:` (max 1000, must not start with `/`) — chat as the player; reply "Sent".
- `/release game: slot:` and `/collect game: slot:` — first `tracker` → if rule is `disabled|done|automatic|after_goal` explain and stop; else `ConfirmView` ("Release all remaining items from {slot}? This cannot be undone.") → `slot_action`; reply `message`, `done`.
- `/status game: slot: status:(playing|stuck|away|clear) note:` (note ≤ 140).
- `/claim game: slot:` — `slot` autocomplete from the user's view of the game: `claimable` slots.
- `/handover offer game: slot: member: username: note:` — `member` (Discord user, optional) is resolved to their linked `manager_username`; else `username` text; neither → open offer. `/handover cancel game: slot:` → `cancel_handover`.

M5
- `/signup show round:` — detail: status, deadline, my entries (name, games, issues, `generation_error`), test result for managers (`test.status`, `current`).
- `/signup join round: file:(attachment, optional) config:(autocomplete, optional)` — exactly one required. File: extension `.yaml|.yml`, `size ≤ 1 MiB`, UTF-8 (BOM stripped) else error; defer; `check_config(content)` → show `check_result_embed`; if `ok` false stop; `create_config(name=filename, content)` → `add_entry(config_id)` → show warnings + entry count. Config: `add_entry` directly, then `check_config` of that config is **not** re-run (the manager already analysed it); warnings shown.
- `/signup withdraw round: entry:` — `entry` autocomplete of the caller's entries (`mine`), `ConfirmView`, `remove_entry`.
- `/signup open name: deadline:(YYYY-MM-DD HH:MM, guild timezone) slots_per_user:(1–20, default 1) description:(≤2000)` — role gate; `create_signup`.
- `/signup close round:` — role gate; `ConfirmView`; `close_signup`.
- `/signup test round:` — role gate; `test_signup`; then follow up by polling `signup()` every 10 s up to 10 min until `test.status` ∈ `succeeded|failed`; edit the ephemeral reply with the result (and per-entry `generation_error`).

### Background services
| Service | Interval | Does |
|---|---|---|
| `GameCache.refresh` | 60 s | `list_games(bot)`; `auth_status(bot)` every 10 min for `site_url` |
| `UserSync.run` | 120 s, users staggered | per active link `list_games(user)`; diff vs last snapshot → `game_online` (state became `running` for a game with `my_slots`), `slot_offered` (new `offered_slots` entry with `to_me`); first pass per user primes only. Feeds role membership (`my_slots` non-empty) to `Relay.sync_roles`. 401 → mark invalid, DM once "Your manager token stopped working". |
| `FeedPoller.run` | 5 s for games `starting|running`, 60 s otherwise | reader per game = bot token if in `GameCache`, else the first active linked participant's token; `activity(after=cursor)`; first time (no cursor) → set cursor to `last`, emit nothing; `last < cursor` → reset cursor to `last`; entries → `Relay.publish(game, entries)` (bot-visible games only) and `AlertEngine.handle(game, entries)`. |
| `Relay.maintain` | 60 s | for each guild with `games_channel_id`: create a public thread "{game name}" (≤100 chars) in that channel for each non-finished bot-visible game without a space (starter message = `game_detail_embeds` without slots), create role "AP {game name}" (not mentionable, no permissions); add/remove linked players to the role; on `finished_at` set → post final summary, archive+lock thread, delete role, set `closed_at`. |
| `Overview.run` | 60 s | per guild with `overview_channel_id`: `overview_embed` (games + open sign-ups); edit when `content_hash` changed; recreate + pin if the message is gone. Presence: "Watching {n} games online" (EN, global). |
| `Events.sync` | 5 min | guilds with `scheduled_events=1`: games with future `start.at` → external event "Start: {game}", location = address or site_url, start `start.at`, end +3 h; open rounds with future deadline → "Sign-ups close: {name}", start deadline, end +15 min; update when time changed, delete when source gone/past. |
| `SignupReminders.run` | 5 min | open rounds: post in `signups_channel_id` 24 h and 1 h before deadline once per (guild, round, deadline, before). |

Relay defaults per new space: kinds `goal, release, collect, hint, chat, server, status, handover, handover_done` (off: `item, join, part`). Batching: per poll all entries of a game go into as few messages as possible (≤ 2000 chars each, max 5 messages; overflow → "…and {n} more events"). Every user-supplied text (player names, chat, items, locations, notes) passes `ui.format.escape`. "Game online" posts in the thread ping the game role.

Alert rules (`AlertEngine`, all opt-in, DMs in the user's stored locale, batched per user per poll, Discord `Forbidden` on DM → stop DMing that user for 24 h):
- `item_received`: `item` entry with `receiver` ∈ the user's slot names in that game (and `finder` ≠ receiver).
- `hint_found`: `item` entry whose (receiver, item, finder, location) matches an unfound hint for the user (hint cache seeded from `tracker` on first sight of a game/slot, kept current from `hint` entries).
- `stuck_in_my_world`: `status` entry with `status == "stuck"` for player P ≠ me → fetch my tracker → unfound hints with `in_my_world` and `receiver == P`; DM only if ≥ 1, listing up to 8.
- `game_online`: from `UserSync`.
- `slot_offered`: from `UserSync`; DM carries `HandoverAcceptButton` and `HandoverDeclineButton`.

Persistent button custom_ids (`ui/dynamic.py`, regex-validated): `apbot:ho:accept:{game_id}:{slot}`, `apbot:ho:decline:{game_id}:{slot}`, `apbot:hint:{game_id}:{slot}:{finder}:{location}:{status}` (status ∈ 10|20|30).

`TextClientBridge.run(token, game_id, slot, text) -> list[str]`: `client_poll(after=0)` to open; poll every 1 s until `state == "connected"` (max 20 s; `offline` → "The game's server is not running", `refused` → manager `error`); remember `last`; `client_say`; poll every 1 s for up to 8 s, stop 1.5 s after the first new non-`Client` message; return rendered lines (`"".join(part.text)`), escaped, max 15 lines. `names(token, game_id, slot)` cached 10 min.

## Security and user data
- Secrets (names only): `DISCORD_TOKEN`, `MANAGER_API_TOKEN`, `BOT_ENCRYPTION_KEY`. Env only; `.env.example` lists names with placeholders; `.env` and `/data/` git-ignored. `docker-compose.yml` reads them from a git-ignored `.env` next to it.
- Personal data stored: Discord user id, manager user id + username, encrypted manager API token (+ last 4 chars), preferred language, alert choices. Each has a stated use above. Nothing else (no messages, no Discord names).
- Per-user tokens: encrypted at rest with Fernet (`cryptography`) — vetted library, no own crypto. Decrypted only in memory right before a request. Never logged (`RedactingFilter`), never in URLs, exception messages or replies. Invalid → ciphertext set NULL.
- Bot token account: plain manager *user* with a *read* token (decision 4) → public views can never show private games, and passwords are absent server-side; the whitelist in `manager/views.py` is the second line of defence. Room passwords are never shown anywhere by the bot (also not ephemeral).
- `/link` input arrives via a modal (only the bot sees it). A token pasted into a normal channel by mistake is not something the bot can read (no message-content intent); README tells users to revoke such a token.
- Authorisation: the manager decides every action as the linked user; the bot adds Discord-role gates and never widens rights. Buttons re-check the clicker.
- Input validation at the Discord boundary: option lengths (Discord `max_length`), ints parsed and bounded, YAML attachment extension/size/encoding, deadline format/future, timezone in `available_timezones()`, custom_ids by regex. Output: markdown/mention escaping, `AllowedMentions.none()` by default.
- Transport: `MANAGER_URL` may be `http://` only for the internal Docker network; README recommends `https://` otherwise (tokens travel in the header). TLS verification never disabled.
- Data deletion/export: `/unlink`, `/mydata`, guild data dropped on `on_guild_remove`.
- Container: unprivileged 99:100 via entrypoint, read-only code, `/data` volume only.
- Discord permissions to request (README invite URL): View Channels, Send Messages, Send Messages in Threads, Create Public Threads, Manage Threads, Embed Links, Read Message History, Manage Messages (pin overview), Manage Roles (game roles), Manage Events (scheduled events). Intents: `guilds` only; no privileged intents.

## UI
Discord embeds and components are the UI; no design system exists, so this plan proposes one (approved with the plan), in `apbot/ui/style.py`:
- Colour: neutral by default (no embed colour); one accent `0x2F6F9F` for informational embeds (game, sign-up, tracker, overview); state colours only where they carry meaning: success `0x2E7D32` (running, linked, done), warning `0xB26A00` (stuck, sleeping, deadline < 24 h), error `0xC62828` (errors, crashed).
- Text: sentence case, plain and specific; no emoji anywhere (titles, buttons, fields); no exclamation marks; times as Discord timestamps (`<t:…:R>`, `<t:…:F>`) so each viewer sees their own time zone.
- Structure: title = the thing (game/round name), description = one status line, fields for facts; lists as monospace-free plain lines `name — world — owner`; long lists truncated with "…and N more" (never silently).
- Components: one primary button per view (`ButtonStyle.primary` for the main action, `danger` only for Release/Collect/Withdraw confirm, `secondary` for Cancel/Decline); labels are actions ("Mark as priority", "Take over slot", "Release items", "Cancel").
- States every command handles: loading (deferred "thinking" for anything that calls the manager), empty ("No games are running. Organizers can add one in the manager."), error (what happened + what to do), not linked, read-only token, manager unreachable, success.
- Limits: `ui.style` constants `EMBED_TOTAL=6000`, `FIELD_VALUE=1024`, `FIELDS=25`, `DESCRIPTION=4096`, `MESSAGE=2000`, `SELECT_OPTIONS=25`, `AUTOCOMPLETE=25`.

## Steps
Each milestone ends green (`ruff check .`, `pytest`, `docker build`) with its tests and a CHANGELOG line under `[Unreleased]`. Localisation is part of every step: every user-visible string goes through `t()` / `locale_str` and gets DE/FR/NL entries in the same step.

### M1 — Foundation, core read views, Docker (→ 0.1.0)
1. Repo scaffolding: `apbot/__init__.py` (`__version__ = "0.1.0"`), `requirements.txt` (pinned: `discord.py`, `httpx`, `cryptography`, `tzdata` — exact latest stable versions verified on PyPI by exact name at implementation time), `requirements-dev.txt` (`pytest`, `pytest-asyncio`, `ruff`, pinned), `ruff.toml`, `pytest.ini`, `.gitignore` (+ `/data/`), `.dockerignore`.
2. `config.py` + `logging_setup.py` (+ tests for required vars, URL/token format, redaction).
3. `i18n/` infrastructure: `t()`, `language_of()`, `BotTranslator`; empty catalogs; `tests/test_i18n.py` (AST scan of `t("…")` and `locale_str("…")` in `apbot/**.py`; every key in each of de/fr/nl; same `{placeholders}`; no extra keys; localized command/option names match `^[-_\w]{1,32}$` lowercase and are unique per scope).
4. `manager/errors.py`, `manager/client.py` (methods needed for M1: `health`, `auth_status`, `list_games`, `game`, `list_signups`), `manager/views.py` whitelist parsers.
5. `ui/style.py`, `ui/format.py`, `ui/embeds.py` (`games_list_embed`, `game_detail_embeds`, `signups_embed`, `error_embed`), `ui/views.py` (`OpenInManagerButton`).
6. `services/scheduler.py`, `services/game_cache.py`, `heartbeat.py`, `bot.py` (startup checks as above), `__main__.py`, `errors.py`, `autocomplete.game_choices_public`.
7. `cogs/games.py` with `/games`, `/game`; `cogs/signups.py` with `/signups`. DE/FR/NL for all strings and command names.
8. Docker: `Dockerfile` (python:3.12-slim-bookworm; venv `/opt/venv`; apt `tini`; copy `apbot`; `ENV DATA_DIR=/data PUID=99 PGID=100 UMASK=022 PYTHONUNBUFFERED=1`; `VOLUME /data`; `HEALTHCHECK` = heartbeat file younger than 90 s; `ENTRYPOINT ["/usr/bin/tini","--","/entrypoint.sh"]`; `CMD ["python","-m","apbot"]`; OCI labels), `docker/entrypoint.sh` (manager's, adapted), `docker-compose.yml` (manager service as in the manager repo + `ap-discord-bot` service: image `ghcr.io/niklaskodev/<repo>:latest` with commented `build: .`, `restart: unless-stopped`, `depends_on: archipelago-manager: condition: service_healthy`, env `MANAGER_URL: http://archipelago-manager:8080`, secrets via `${DISCORD_TOKEN}` etc. from `.env`, `TZ`, `PUID/PGID`, volume `./bot-data:/data`).
9. CI/dependabot (guideline): `ci.yml` keeps only the Python block (pinned `python-version: "3.12"`, `pip install -r requirements-dev.txt`, `ruff check .`, `pytest`) and adds job `docker` (matrix amd64 on `ubuntu-latest`, arm64 on `ubuntu-24.04-arm`, `docker/setup-buildx-action` + `docker/build-push-action` with `load: true`, no push, gha cache per arch; smoke: `docker run --rm IMAGE python -c "import apbot; print(apbot.__version__)"` and `docker run --rm -e PUID=99 -e PGID=100 IMAGE id -u` → `99`); remove Node/Go/Rust/.NET/Java/custom blocks and the "no known stack" step. Pin third-party actions by commit SHA as the manager does. `dependabot.yml`: keep `github-actions`, add `pip` (directory `/`, weekly, group minor+patch) and `docker` (directory `/`, weekly).
10. `release.yml`: after `test`, enable the version-file check (`python -c "import apbot; print(apbot.__version__)"` == tag) and add the image publish (per-arch build by digest + `imagetools create`, tags `{{version}}`, `{{major}}.{{minor}}`, `latest` for non-`-pr` tags; `packages: write` on that job only) — exact form per decision 8.
11. `README.md`, `.env.example` (all names from the env table with placeholders/comments), `CHANGELOG.md` (`[Unreleased]` → "Added: Discord bot with /games, /game, /signups; Docker image"; leave the template's `[0.1.0] - YYYY-MM-DD` heading for the release step), `CLAUDE.md` Project block (below).

### M2 — Linking, settings, game control (→ 0.2.0)
12. `crypto.py` + tests; `BOT_ENCRYPTION_KEY` becomes required (startup fails with a clear message naming the variable).
13. `db/database.py` + `migrations/001_links_settings.sql`, `db/links.py`, `db/guilds.py` (+ tests on a temp file DB).
14. `access.py`; `ManagerClient` methods `wake/start/stop/restart`.
15. `cogs/linking.py` (`/link` modal, `/unlink`, `/mydata`), `ui/embeds.link_status_embed`.
16. `cogs/setup.py` (`show`, `manager`, `channel`, `role`, `role_clear`, `language`, `timezone`), `ui/embeds.settings_embed`, permission check helper.
17. `/wake`, `/start`, `/stop`, `/restart` in `cogs/games.py` with role gate; `on_guild_remove` cleanup; translations.

### M3 — Live: threads, relay, DMs, overview, events (→ 0.3.0)
18. `migrations/002_live.sql`, `db/live.py`.
19. `services/user_sync.py`, `services/feeds.py` (reader choice, cursors), `ManagerClient.activity`, `tracker`.
20. `services/relay.py` (spaces, roles, filters, batching, formatting per kind) + `/setup feed`.
21. `services/alerts.py` (pure rules + sender) + `cogs/alerts.py` (`/alerts`) + `ui/dynamic.py` handover buttons (`add_dynamic_items` in `setup_hook`), `ManagerClient.accept_handover/cancel_handover`.
22. `services/overview.py` (pinned message, presence), `services/events.py` + `/setup events`.

### M4 — Player tools (→ 0.4.0)
23. `services/text_client.py`, `ManagerClient.client_*`, `slot_action`, `set_hint_status`.
24. `cogs/tracker.py` (`/tracker`, `/hints`, hint select + buttons, `HintStatusButton`), `ui/embeds.tracker_embed`.
25. `cogs/textclient.py` (`/hint`, `/hint_location`, `/remaining`, `/say`, `/release`, `/collect`), `autocomplete.item_choices/location_choices`.
26. `cogs/slots.py` (`/status`, `/claim`, `/handover offer|cancel`), `ManagerClient.set_slot_status/claim/offer_handover`.

### M5 — Sign-ups from Discord (→ 0.5.0)
27. `ManagerClient` sign-up/config methods, `ui/embeds.signup_detail_embed`, `check_result_embed`, `services/timeparse.py`.
28. `/signup show|join|withdraw` (attachment + saved config paths).
29. `/signup open|close|test` with role gate and test follow-up.
30. `migrations/003_signups.sql`, `db/reminders.py`, `services/signup_reminders.py`.
31. README per-command section complete; CHANGELOG lines.

### CLAUDE.md Project block (fill in during step 11; update when stale)
```
- Purpose: Discord bot for Archipelago multiworld games, using the Archipelago Manager's REST API; runs as a Docker container.
- Stack: Python 3.12, discord.py, httpx, SQLite (stdlib sqlite3), cryptography (Fernet); pytest + ruff; Docker (amd64/arm64).
- Commands: run `python -m apbot` · test `pytest` · lint `ruff check .` · build `docker build -t ap-discord-bot .`
- Version source of truth: `apbot/__init__.py` (`__version__`)
- Layout: apbot/cogs → slash commands (thin) · apbot/services → background work and logic · apbot/manager → manager API client + whitelisted views · apbot/db → SQLite + migrations · apbot/i18n → EN/DE/FR/NL catalogs · apbot/ui → embeds, views, style · docker/ → entrypoint · tests/ → pytest
```

## Tests
Fakes: manager via `httpx.MockTransport` (`tests/fakes.py` routes by method+path, records requests incl. headers); Discord via small fake objects for interaction/channel/thread/role/user with recorded sends. No real network, no real clock (inject `now`).
- Config: missing `DISCORD_TOKEN` → `ConfigError` naming the variable, value not in message; `MANAGER_API_TOKEN` without `apm_` → error; URL with path → error; defaults applied.
- Redaction: log record containing `apm_abc123` / a Fernet key → output shows `[redacted]`.
- ManagerClient: sends `Authorization: Bearer <token>` and never puts it in the URL; 401 → `TokenInvalid`; 403 "This API token can only read" → `ReadOnlyToken`; 409 detail preserved; 422 list detail joined; GET retried on 503 then succeeds; POST not retried; timeout → `ManagerUnavailable`.
- Views whitelist: a detail JSON containing `password`, `server_password`, `discord_webhook_url`, `notes`, `spectate` → none appear in `GameDetail` nor in any embed text built from it (assert over the serialized embed).
- Embeds: 0/1/40 games (truncation line present, ≤ 6000 chars, ≤ 25 fields); slot lists split across fields ≤ 1024; escaped names (`*a*`, `@everyone`) rendered inert; tls → "encrypted (wss)"; no `site_url` → no button.
- i18n: catalog completeness/placeholders/command-name regex (see step 3); `language_of("de")=="de"`, `("en-GB")=="en"`, `("pt-BR")=="en"`.
- Startup: health unreachable → bot keeps running and retries; auth_status user null → exit code 2; role admin → warning logged.
- Crypto: round-trip; wrong key → `TokenUndecryptable`; two keys: old ciphertext decrypts and `needs_rotation` true.
- DB: migrations apply once, idempotent on second run, recorded; `manager_user_id` unique; deleting a link cascades alert prefs; `on_guild_remove` deletes the guild's rows.
- Linking: valid token → row with ciphertext ≠ token and hint = last 4; invalid → no row; manager user linked to other Discord id → refused; reply text never contains the token; `/unlink` removes data; `/mydata` lists exactly the stored fields.
- Access: not linked → `NotLinkedError`; role gate with configured role missing → refused, not configured → allowed; button clicked by another user resolves that user's link.
- Game control: `/start` sends POST with the user's token (not the bot's); manager 409 detail shown ephemeral.
- Feeds: first poll sets cursor without emitting; later entries emitted once and cursor persisted; `last < cursor` resets; private game read with a participant's token and never relayed publicly.
- Relay: default kinds filter `item/join/part`; batching ≤ 2000 chars and ≤ 5 messages + overflow line; chat with `@everyone`/markdown escaped and `AllowedMentions.none()`; finished game → thread archived/locked, role deleted.
- Alerts rules (pure): each of the 5 kinds positive and negative cases; stuck with zero hints in my world → no DM; prefs off → nothing; DM `Forbidden` → muted 24 h.
- UserSync: first pass primes (no alerts); state stopped→running → `game_online`; new `offered_slots` with `to_me` → `slot_offered`; 401 → link invalid + one DM.
- Overview: unchanged content → no edit; missing message → recreated and pinned.
- Events: future start → create; changed time → update; past/removed → delete; disabled → none.
- TextClientBridge: waits for `connected`; `offline` → user error; collects answer lines and stops after quiet period; `/` text rejected before calling the manager.
- Tracker/hints: embed sections and truncation; hint button sends PUT with `finder_slot`/`location_id` and status; 409 shown; custom_id regex rejects malformed ids.
- Release/collect: rule `disabled` → no POST; confirm → POST `{"action":"release"}`; cancel → nothing.
- Slots: `/handover offer member:` resolves linked username; unlinked member → error; open offer sends `to: null`.
- Sign-ups: `.txt` or > 1 MiB attachment rejected before any manager call; check `ok:false` → no config created; ok → `create_config` then `add_entry`; withdraw confirm → DELETE; `open` deadline parsing in guild timezone, past → error; test follow-up stops on `succeeded`/`failed`.
- Reminders: sent once per (guild, round, deadline, before); deadline moved → sent again for the new deadline.
- Docker (CI): image imports `apbot` and runs as uid 99 on amd64 and arm64.

## Decisions for the programmer
1. **Stack** — Python 3.12 + discord.py 2.x + httpx + SQLite (stdlib `sqlite3`) + ruff/pytest (same language/linter as the manager) · Node + discord.js · Python + hikari. **Recommend: Python + discord.py.**
2. **New dependencies** — runtime: `discord.py`, `httpx`, `cryptography` (Fernet), `tzdata` (time zones on slim images and Windows); dev: `pytest`, `pytest-asyncio`, `ruff`; all pinned exactly. No ORM, no `aiosqlite`, no `respx` (httpx's built-in MockTransport). **Recommend: approve this list.**
3. **Token encryption key** — (A) `BOT_ENCRYPTION_KEY` env var, comma-separated keys for rotation (MultiFernet); a stolen DB/backup alone is useless · (B) key file auto-generated in `/data` (zero setup, but sits next to the DB) · (C) no encryption (conflicts with `docs/ai/security.md`). **Recommend: A.** Losing the key means everyone re-runs `/link`.
4. **Bot's manager account for public views** — (A) dedicated manager account with role *user* and a *read* token: public views only ever show games with visibility "all", never passwords · (B) an admin/organizer token: sees every game, including private ones, which would then be posted publicly. **Recommend: A** (bot warns at startup otherwise).
5. **Database and schema** — SQLite file in `/data`, plain-SQL versioned migrations, tables as in *Data* (links, guild settings, game spaces, feed cursors, alert prefs, overview, scheduled events, reminders). **Recommend: approve.**
6. **Per-game Discord space** — (A) public thread per game in one configured text channel + per-game role, archived/deleted when the game finishes · (B) post per game in a forum channel · (C) a channel per game (needs Manage Channels, clutters fast). **Recommend: A.**
7. **Private games (visibility "participants")** — (A) no public thread/relay; their feeds only drive DMs for linked players · (B) private threads for linked players (unlinked players miss out; more permissions). **Recommend: A.**
8. **Image publishing** — (A) publish to GHCR (`ghcr.io/niklaskodev/<repo>`) only from `release.yml` on version tags (`X.Y.Z`, `X.Y`, `latest`) — matches the guideline's "no publish without release" · (B) also push `latest` on every `main` push like the manager. **Recommend: A.** Also: repo name (e.g. `NiklaskoDev/ArchipelagoDiscordBot`) and whether it is public.
9. **Release cadence** — (A) release each milestone: M1 `0.1.0` … M5 `0.5.0`, each only on your explicit "release" · (B) one `0.1.0` after M5. **Recommend: A** (usable early, smaller reviews).
10. **Who may run `/setup`** — (A) Discord "Manage Server" by default plus the optional admin role from `/setup role` · (B) admin role only. **Recommend: A.**
11. **Licence** — the manager repo has no LICENSE file. Options: none (all rights reserved) · MIT · match whatever you add to the manager. **Recommend: decide together with the manager; MIT if public.**

**Resolved 2026-11-10:** the programmer approved the plan with every recommended option: 1 Python + discord.py · 2 dependency list as above · 3A `BOT_ENCRYPTION_KEY` · 4A dedicated *user* account with read token · 5 schema as in *Data* · 6A public thread + role per game · 7A private games DM-only · 8A GHCR publish on version tags only; image `ghcr.io/niklaskodev/archipelagodiscordbot` (repo `NiklaskoDev/ArchipelagoDiscordBot`, visibility not yet decided) · 9A one release per milestone · 10A Manage Server + optional admin role · 11 no LICENSE file until the repo's visibility is decided.

## Risks / follow-ups
- Duplicate notifications: the manager can already post to Discord webhooks (game online, stuck, hand-over, sign-up reminders 24 h before). If both are on for the same channel, posts double. README advises turning off the manager webhook for channels the bot relays to.
- Text client sessions opened by `/hint` etc. make the server announce "joined"/"stopped viewing" lines; `join`/`part` are off by default in relays for that reason.
- Manager error details are English only; the bot shows a localized heading plus the manager's `detail`. Follow-up: reuse the manager web UI's server-message translations.
- The manager does not expose a user's Discord id, so `/link` cannot prove the Discord account matches the manager account; whoever holds a write token already has those rights, so the risk is limited to the token holder.
- A linked admin's write token lets the bot act with admin rights for that user; README advises a dedicated, expiring token.
- Polling load: N linked users × 1 request/120 s plus 1 request/5 s per running game; fine for community-sized servers; intervals are constants if tuning is needed.
- `client/names` returns nothing until a text client session exists, so the first `/hint` autocomplete shows "Loading names…".
- Discord role limit (250/guild): roles are deleted when games finish.
- The bot depends on manager API behaviour of 1.3.0; a breaking manager change needs a bot update (`MIN_MANAGER_VERSION` warning).
