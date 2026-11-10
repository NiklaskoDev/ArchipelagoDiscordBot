# 005 — Show linked users their private games in /games, /game and the game-control suggestions

Status: approved
Type: feature
Version impact: minor (0.2.0) — new, backward compatible behaviour for linked users; unlinked users see no change. Entry under `[Unreleased]` → `### Added`. No version bump in this plan (§8).

## Goal
A linked user's `/games` also lists the games their own manager account can see that the bot account cannot (games with
visibility "participants" they play in, are offered a slot in, organize, or, for admins, all). `/game` and the `game:`
suggestions of `/game`, `/wake`, `/start`, `/stop`, `/restart` offer those games too, and `/game` shows them. Anything
only the caller's token revealed is shown to the caller only, never posted publicly. Unlinked callers keep today's
behaviour exactly. Room passwords stay hidden (views whitelist).

Out of scope: the player-tool suggestions (`game_choices_mine`: `/tracker`, `/hint`, …) stay as they are, `/signups`,
the overview message, threads/roles, feeds, events, and any change to the bot account's role.

## Reuse
- `Access.user_token` / `Access.call_as` (`apbot/access.py`) — token resolution incl. not linked, invalid link, plan-002
  unreadable token; `call_as` marks a refused token invalid.
- `TtlCache` (`apbot/services/ttl_cache.py`) and the `SignupDirectory` pattern (`apbot/services/signup_directory.py`):
  per-Discord-id cache, 60 s TTL, `quick=True` calls for suggestions.
- `ManagerClient.list_games` (`apbot/manager/client.py`) + `QUICK_TIMEOUT` and the `quick`/`retry` pattern of
  `list_signups`.
- `parse_games`, `listed_games`, `GameSummary` (`apbot/manager/views.py`) — whitelist; `GameSummary` has no password
  field, so nothing new can leak.
- `games_list_embed` / `_game_line` / `_list_embed` (`apbot/ui/embeds.py`), `error_embed`, `user_message`
  (`apbot/errors.py`), `reply` (`apbot/ui/respond.py`), `game_choices` (`apbot/autocomplete.py`).
- `GamesCog._control` (`apbot/cogs/games.py`) — already resolves the caller's token before deferring, defers
  ephemerally and calls the manager via `call_as`; it needs no change for private games (3B).
- New: `apbot/services/my_games.py` (`MyGames`) — one line why: no existing cache holds `GameSummary` lists per person
  (`UserSync` keeps the slimmer `UserGame` every 2 min for events/roles).

## Code layout
- `apbot/manager/client.py` — `list_games(token, *, quick=False)`.
- `apbot/services/my_games.py` — new: `MyGames`, per-person cached list of the games a linked token sees; `private_games`.
- `apbot/bot.py` — construct `self.my_games = MyGames(self.manager)` next to `self.signup_directory`.
- `apbot/cogs/games.py` — `/games` adds the caller's private games in an ephemeral followup; `/game` picks public or
  private reply; `/game`, `/wake`, `/start`, `/stop`, `/restart` use `game_choices_visible`.
- `apbot/autocomplete.py` — new `game_choices_visible`; remove `game_choices_public` once no command uses it (check with
  grep; keep it if anything else still imports it).
- `apbot/ui/embeds.py` — `private_games_embed`.
- `apbot/cogs/linking.py` — `my_games.forget(id)` after `/link` saves and after `/unlink`.
- `apbot/i18n/de.py`, `fr.py`, `nl.py` — every new string.
- `README.md`, `CHANGELOG.md`.
- Tests: `tests/test_my_games.py` (new), `tests/test_cogs.py`, `tests/test_game_control.py`, `tests/test_autocomplete.py`,
  `tests/test_embeds.py`, `tests/test_manager_client.py`, `tests/test_linking.py`, `tests/test_i18n.py` (only if it
  asserts which autocomplete a command uses).

## Data
None. No table, no migration. `MyGames` is in memory only (lost on restart, max 500 entries, 60 s TTL).

## Interfaces
- `ManagerClient.list_games(token: str, *, quick: bool = False) -> list[dict]` —
  `self._get("/api/games", token, timeout=QUICK_TIMEOUT if quick else None, retry=not quick)`. Existing callers unchanged.
- `apbot/services/my_games.py`:
  ```
  MY_GAMES_TTL = 60.0; MAX_CACHED = 500
  class MyGames:
      def __init__(self, client: ManagerClient, *, now=time.monotonic)
      async def games(self, owner: int, token: str, *, quick: bool = False) -> tuple[GameSummary, ...]
          # cached by owner (Discord id); on miss: tuple(parse_games(await client.list_games(token, quick=quick)))
          # raises ManagerError / TypeError (not a list) like SignupDirectory; nothing cached on error
      def cached(self, owner: int) -> tuple[GameSummary, ...] | None   # no manager call; for autocomplete fallback
      def forget(self, owner: int) -> None
  def private_games(mine: Iterable[GameSummary], public: Iterable[GameSummary]) -> list[GameSummary]
      # games in `mine` whose id is not in `public`; pure. "Private" = only the caller's token revealed it.
  ```
  Definition used everywhere: a game is private for the caller iff its id is not in `bot.cache.games`. This is exactly
  the set that must never be posted publicly; `visibility` is not consulted (a game made public a moment ago that the
  cache has not seen yet is treated as private — the safe direction).
- `/games` (`GamesCog.games`): public list exactly as today, then (linked callers only) an ephemeral followup with the
  private games; see Steps 6.
- `/game` (`GamesCog.game`):
  - `game_id in {g.id for g in bot.cache.games}` → today's path exactly (public defer, bot token).
  - else, caller linked (`access.user_token` succeeds — call it *before* deferring, as `_control` does) →
    `defer(ephemeral=True)`, `raw = await access.call_as(interaction, token, lambda tok: manager.game(tok, game_id))`,
    same embeds and button, `reply(..., ephemeral=True)`. `NotFound` → existing "That game was not found…" error.
  - else, `NotLinkedError` → today's path (bot token, public; NotFound → same error). Other `UserFacingError` from
    `user_token` (invalid/unreadable link) also falls back to today's path, so a broken link never blocks public games.
- `game_choices_visible(interaction, current)` (`apbot/autocomplete.py`): public `cache.games` plus the caller's private
  games, deduped by id, ordered by `game_choices`; private choices get the suffix `SEPARATOR + t("Private", lang)` with
  the name clipped to `CHOICE_NAME - len(suffix)`. Personal list: `await bot.my_games.games(id, token, quick=True)`;
  on `NotLinkedError`, `UserFacingError`, `ManagerError`, `TypeError` → log.debug and use `bot.my_games.cached(id)` or
  nothing (public choices only). Never raises. Used by `/game`, `/wake`, `/start`, `/stop`, `/restart`.
- `/wake`, `/start`, `/stop`, `/restart` with a private game picked (3B): no code change in `_control`. It already
  checks the organizer role (start/stop/restart), resolves the caller's token before deferring (unlinked → the existing
  "not linked" error, as today), defers ephemerally, runs the action with the caller's token via `call_as` and replies
  ephemerally with `game_state_embed`. The manager decides whether the caller may act (its refusal is shown as today);
  `NotFound` → the existing "That game was not found…" error. So a private game's name/state only ever appears in the
  caller's ephemeral reply.

## Security and user data
- No new secret. Tokens: only via `Access`; never logged (log only `exc.detail` / exception class as today).
- Privacy rule (non-negotiable): data from a user token is only sent in ephemeral messages; public replies keep using
  only the bot token's data. `/game` decides public vs private **before** deferring (a deferred public response cannot
  turn ephemeral). Autocomplete suggestions are only seen by the person typing.
- Error handler caveat: `on_app_command_error` deletes the original response when `response.is_done()`. In `/games` the
  public list is already sent, so errors from the private part must be caught inside the command and answered with an
  ephemeral followup — never raised, or the public list would be deleted.
- `GameSummary`/`GameDetail` whitelist unchanged → no password, notes, webhook or spectator links.
- Personal data: per-person game lists held in memory for ≤ 60 s; cleared on `/link` and `/unlink`. Not persisted, so
  `/mydata` needs no change (README notes that nothing new is stored).
- Admin tokens see every game; their list is still private to them (ephemeral), nothing else changes.

## UI
- Reused: `_game_line`, `_list_embed`, `ACCENT`, `SEPARATOR`, `error_embed`, `open_in_manager_view`, `game_state_embed`.
- `/games`: public list exactly as today; then, if the caller has private games, an ephemeral followup embed
  `private_games_embed(games, lang)`: title `t("Private games you can see", lang)`, lines via `_game_line`, cap
  `LIST_LINES` + "…and N more" via `_list_embed`. No private games → no followup. The title is the marking.
- Suggestions: private games carry the text suffix "· Private" (localized). Text label, no emoji/icon (ui.md).
- Error states (private part of `/games` only): link broken → the existing message from `user_message` (e.g. "Run /link
  again") ephemerally; manager unreachable/timeout → `t("Your private games could not be loaded right now. Try again
  later.", lang)` ephemerally.

## Steps
1. `client.py`: add `quick` to `list_games` as in Interfaces.
2. Create `apbot/services/my_games.py` (`MyGames`, `private_games`) modelled on `SignupDirectory`.
3. `bot.py`: `self.my_games = MyGames(self.manager)`.
4. `linking.py`: call `self.bot.my_games.forget(interaction.user.id)` right after `links.save(...)` in `on_submit` and
   next to `user_sync.forget` in `unlink`.
5. `embeds.py`: add `private_games_embed(games: Sequence[GameSummary], lang: str) -> discord.Embed`.
6. `games.py` `/games`:
   - Before anything else, resolve the personal token without failing: helper `_personal_token(interaction)` returns
     `(token, None)` on success, `(None, None)` on `NotLinkedError`, `(None, exc)` on any other `UserFacingError`.
   - Send the public reply exactly as today (defer only when the cache is not loaded).
   - If a `UserFacingError` was kept: `interaction.followup.send(embed=error_embed(user_message(exc, lang)),
     ephemeral=True)` and stop.
   - If a token exists: inside `try`, `mine = await access.call_as(interaction, token, lambda tok: my_games.games(id, tok))`;
     `except UserFacingError` → ephemeral `error_embed(user_message(...))`; `except (ManagerError, TypeError)` → log.debug
     and ephemeral "could not be loaded" embed. Else `extra = listed_games(private_games(mine, self.bot.cache.games))`;
     if `extra`, `interaction.followup.send(embed=private_games_embed(extra, lang), ephemeral=True)`.
7. `games.py` `/game`: implement the three branches from Interfaces.
8. `autocomplete.py`: add `game_choices_visible`.
9. `games.py`: set `@app_commands.autocomplete(game=game_choices_visible)` on `/game`, `/wake`, `/start`, `/stop`,
   `/restart`; update the import. Leave `_control` unchanged. Remove `game_choices_public` if grep shows no other user.
10. i18n: add DE/FR/NL for `"Private games you can see"`, `"Private"` and
    `"Your private games could not be loaded right now. Try again later."`. `pytest tests/test_i18n.py` enforces
    completeness.
11. README: `/games` and `/game` rows say that linked users additionally see the private games their manager account
    can see, shown only to them; rewrite the sentence under the table ("These commands show what the bot's own manager
    account may see…") accordingly; keep "never a room password". In the game-control table, say the `game:`
    suggestions of `/wake`, `/start`, `/stop`, `/restart` include your private games. Keep the bot-account section
    (~line 141) true: public lists still only show visibility "all".
12. CHANGELOG `[Unreleased]` → `### Added`: "`/games` and `/game` also show linked users the private games their own
    manager account can see, and the game suggestions of `/game`, `/wake`, `/start`, `/stop` and `/restart` include
    them; only that person sees them."
13. Tests (last step, see below), `ruff check .`, `pytest`.

## Tests
- `MyGames`: second call within 60 s does not call the manager; after TTL it does; `quick=True` passes through;
  `forget` drops the entry; a `ManagerError` caches nothing; `cached` returns None when absent.
- `private_games`: only ids missing from the public list; order kept; empty when all public.
- `list_games(quick=True)` uses `QUICK_TIMEOUT` and no retry; default unchanged.
- `/games` unlinked → exactly today: one public message, manager not called with any user token.
- `/games` linked, no private games → one public message, no followup.
- `/games` linked with a private game → the private game appears only in an ephemeral followup titled "Private games
  you can see"; no non-ephemeral send contains its name.
- `/games` linked, token refused (`TokenInvalid`) → link marked invalid, public list sent and not deleted, ephemeral
  "link again" message.
- `/games` linked, unreadable token (plan 002) → public list sent, ephemeral "cannot be read" message, link kept.
- `/games` linked, manager timeout on the personal call → public list sent, ephemeral "could not be loaded" message.
- `private_games_embed`: title, lines, 25-line cap with "…and N more", localized.
- `/game` public game id → public reply via bot token (as today), even for a linked caller.
- `/game` private id, linked → ephemeral defer, user token used, embeds + button, ephemeral reply.
- `/game` private id, unlinked → bot token, NotFound → existing error.
- `/game` private id, broken link → falls back to the bot-token path.
- `game_choices_visible`: unlinked → only public games (same result as before); linked → private games included with
  "· Private" suffix in the caller's language and clipped to 100 chars; manager error → cached or public choices only,
  no exception; uses `quick=True`.
- `/game`, `/wake`, `/start`, `/stop`, `/restart` each have `game_choices_visible` as the `game` autocomplete.
- `/wake` with a private game id (not in `bot.cache.games`), linked → the action is called with the caller's token,
  deferred and answered ephemerally with the game state; manager `NotFound` → existing "not found" error; unlinked →
  the existing "not linked" error before any defer.
- `/start` with a private game id → organizer-role check still applies first; then as `/wake`.
- `/link` and `/unlink` clear the person's `MyGames` entry.
- No `GameSummary` built from a user token has a password-like attribute (existing whitelist test still passes).

## Decisions for the programmer
Resolved 2026-11-11: 1B, 2A, 3B.

1. **How `/games` keeps private games private** — A: whole reply ephemeral for linked callers; B: public list as today
   plus an ephemeral followup with only the private games; C: a `private:` option. → **B**.
2. **Where the caller's game list comes from** — A: new `MyGames` cache, 60 s per Discord id, quick timeout for
   suggestions; B: reuse `UserSync`'s 2-minute snapshot; C: no cache. → **A**.
3. **Suggestions of `/wake`, `/start`, `/stop`, `/restart`** — A: unchanged; B: switch to `game_choices_visible`. → **B**.

## Risks / follow-ups
- Staleness: a game the user just joined or that just turned private shows up/moves within 60 s (MyGames) / 60 s
  (bot cache). A game just made public may briefly appear in the private followup or carry "· Private" in suggestions —
  safe direction.
- The ephemeral followup of `/games` arrives after the public reply; if the personal call is slow (≤ 10 s) the user sees
  it later. Acceptable.
- Every `/games` or suggestion by a linked user costs at most one manager call per minute per person (TTL bound).
