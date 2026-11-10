# 002 — Keep links when a stored token cannot be decrypted

Status: approved
Type: fix
Version impact: hotfix — restores intended behaviour, no API/config/schema change. 0.1.0 still has a placeholder date in `CHANGELOG.md`; if it is unreleased this simply rides in `[Unreleased]`, otherwise it is 0.1.1.

Hotfix gate fails: the new user-facing text needs entries in `apbot/i18n/de.py`, `fr.py`, `nl.py` (`tests/test_i18n.py:43` requires every scanned text in every catalog), so 5 source files instead of at most 3. That is why this is a normal plan.

## Goal
A mistyped, lost or wrongly rotated `BOT_ENCRYPTION_KEY` must never destroy stored links. On `TokenUndecryptable` the row stays untouched (status `active`, `token_ciphertext` unchanged). The person gets an ephemeral "cannot be read, /link again" message, background services skip them, and one warning per process start gives admins the count of unreadable links. A real 401 (`TokenInvalid`) still marks the link invalid, as it does today. A later `/link` overwrites the row, as it does today (`LinkStore.save`).

Out of scope: changing `LinkStore.mark_invalid`, the 401 path, `TokenCipher`, or any key-validation-at-startup check.

## Cause
- `apbot/access.py:57-60`: `stored_token` catches `TokenUndecryptable` and calls `on_token_invalid` → `LinkStore.mark_invalid` (`apbot/db/links.py:73-79`) sets `status='invalid', token_ciphertext=NULL`.
- `apbot/services/user_sync.py:155-158`: `sync_user` treats `None` from `stored_token` as "link gone" and calls `_drop` (game roles removed).
- `apbot/services/feeds.py:88` and `apbot/services/alerts.py:252` call `stored_token`, so they wipe the link through the same path. `feeds.py:115` (`token_refused`) is reached only on a real `TokenInvalid`, so it stays as it is.

## Reuse
- `Access.stored_token` / `Access.user_token` keep their signatures. Callers that only need "token or None" (feeds, alerts) need no change.
- `UserFacingError` + i18n catalogs for the new message. `_link_again()` stays for the 401 and invalid-link cases.
- New: one in-memory set on `Access` (`_unreadable`) so callers can tell "cannot be decrypted" apart from "not active" without changing the return type. Nothing like it exists yet.

## Code layout
- `apbot/access.py`: stop marking invalid on decrypt failure, remember unreadable ids, add `is_unreadable`, add a new message for `user_token`.
- `apbot/services/user_sync.py`: skip unreadable people in `sync_user`, and log one count warning in `run_once`.
- `apbot/i18n/de.py`, `apbot/i18n/fr.py`, `apbot/i18n/nl.py`: translation of the new message.
- `CHANGELOG.md`: line under `[Unreleased]` → `Fixed`.
- No change: `apbot/services/feeds.py`, `apbot/services/alerts.py`, `apbot/db/links.py`, `apbot/crypto.py`.

## Data
None. No schema change. With this fix, `user_links` is no longer written when decryption fails.

## Interfaces
`apbot/access.py`, class `Access`:
- `__init__`: add `self._unreadable: set[int] = set()`.
- `stored_token(link) -> str | None` (same signature):
  - On `TokenUndecryptable`: `self._unreadable.add(link.discord_user_id)`, then `log.debug("A stored manager token cannot be decrypted with the configured keys")` (no id, no token), then `return None`. Remove the `log.error` and the `self.on_token_invalid(...)` call.
  - On success, before returning: `self._unreadable.discard(link.discord_user_id)`.
  - Update the docstring: None when the link is not active or cannot be decrypted. A decrypt failure leaves the link as it is.
- New `is_unreadable(self, discord_id: int) -> bool`: `return discord_id in self._unreadable`.
- `user_token`: when `token is None`, raise `_link_unreadable()` if `self.is_unreadable(discord_id)`, otherwise `_link_again()` (as today).
- New module function `_link_unreadable() -> UserFacingError` with the English key `"Your link to the manager cannot be read any more. Run /link again"`. The coder may adjust the wording but must keep "/link".
- `on_token_invalid`, `run`, `call_as` do not change. A 401 still calls `mark_invalid`.

`apbot/services/user_sync.py`, class `UserSync`:
- `__init__`: add `self._unreadable_reported = False`.
- `sync_user`: when `token is None`:
  - if `self._access.is_unreadable(discord_id)`, return. Do not `_drop`, keep `_states[discord_id]`, call no listener.
  - otherwise `_drop`, as today.
- `run_once`: after the per-link loop, if `not self._unreadable_reported`:
  - `count = sum(1 for link in links if self._access.is_unreadable(link.discord_user_id))`
  - if `count`: `log.warning("%d linked people have a stored manager token the bot cannot decrypt; BOT_ENCRYPTION_KEY may be wrong. Their links are kept", count)`
  - set `self._unreadable_reported = True`. This happens after the first full pass even when `count == 0`: the key can only change on a restart, so the first pass already shows the full picture.
- `token_refused` and `_drop` stay as they are. They now run only for a real 401 or for links that are really gone.

## Security and user data
- No token, ciphertext or Discord id is ever logged. The warning gives only a count. Tests check that `USER_TOKEN` does not appear in `caplog.text`.
- Unreadable ciphertexts are kept on purpose, so they can be decrypted again once the correct key is back.

## UI
One new ephemeral error text, shown through the existing `UserFacingError` handler. No new components.

## Steps
1. Regression tests first (see Tests). Change `tests/test_access.py::test_a_token_that_cannot_be_decrypted_marks_the_link_invalid` and `tests/test_user_sync.py::test_a_token_that_cannot_be_decrypted_tells_the_listener_too` to the new behaviour. Run them and confirm they fail against the current code.
2. `apbot/access.py` changes as in Interfaces.
3. `apbot/services/user_sync.py` changes as in Interfaces.
4. Add the new key to `apbot/i18n/de.py`, `fr.py`, `nl.py` (`MESSAGES`), next to the existing "no longer works" entry (line ~290).
5. Add the remaining tests. Run `pytest -q` and `ruff check .`.
6. Add to `CHANGELOG.md` `[Unreleased]` → `Fixed`: "A wrong or missing encryption key no longer erases linked accounts; affected people are asked to /link again and admins see one warning."

## Tests
`tests/test_access.py`:
- Rewrite line 55 test as `test_a_token_that_cannot_be_decrypted_keeps_the_link_and_asks_to_link_again`. Link under a foreign key, then `user_token` raises `UserFacingError` with the new message, `links.get(1001).status == "active"`, `token_ciphertext` is byte-equal to the stored one, and `USER_TOKEN not in caplog.text`.
- `test_the_link_works_again_once_the_right_key_is_back`: link under key A, then `Access` with key B fails, then a fresh `Access` with key A returns `USER_TOKEN`. This is the reported symptom.
- `test_linking_again_after_an_unreadable_token_works`: unreadable first, then `link_user` with `bot.cipher` (overwrite), then `user_token` returns the token and `is_unreadable(1001)` is False.
- The existing line 101 test (401 marks invalid and clears ciphertext) must still pass unchanged.

`tests/test_user_sync.py`:
- Rewrite line 225 test as `test_a_token_that_cannot_be_decrypted_is_skipped_and_the_link_kept`. After the key change, `sync_user` emits no new memberships (last entry still the old set, no `frozenset()`), `on_token_stopped` is not called, `games_of(1001)` is still the earlier state, and the link is active with the same ciphertext.
- `test_unreadable_links_are_counted_in_one_warning_per_start`: 2 unreadable links + 1 good one. `run_once` twice leaves exactly one WARNING record from `apbot.services.user_sync` containing "2", and `USER_TOKEN not in caplog.text`.
- `test_no_warning_when_every_link_is_readable`: `run_once` logs no WARNING.

`tests/test_feeds.py`:
- `test_a_participant_whose_token_cannot_be_decrypted_is_skipped_for_the_next_one`: the feed is read with the second participant's token, and the first link is untouched (active, same ciphertext).
- `test_an_unreadable_only_reader_reads_nothing_and_keeps_the_link`: `poll` makes no activity call, there is no `token_refused` (no DM, roles unchanged), and the link stays active.

`tests/test_alerts.py`:
- `_tracker_hints` (through the public path that uses it) with an unreadable token returns nothing, and the link stays active.

`tests/test_i18n.py`: the existing catalog-completeness tests cover the new key.

## Affected (one level out)
| Part | Covered by |
|---|---|
| `Access.stored_token` / `is_unreadable` | `tests/test_access.py` (new/rewritten cases) |
| `Access.user_token` callers: `apbot/cogs/common.py:63`, `apbot/cogs/games.py:114`, `apbot/cogs/signups.py:122,160,189,236,264,299`, `apbot/ui/dynamic.py:63,94,137`, `apbot/autocomplete.py:91,117,153,185,203` | Signature and exception type unchanged, only the message text differs: `tests/test_access.py` plus the existing cog/autocomplete suites |
| `UserSync.sync_user` / `run_once` | `tests/test_user_sync.py` (new/rewritten cases) |
| `FeedPoller._reader` (`apbot/services/feeds.py:88`) | add `tests/test_feeds.py` cases above |
| `Alerts._tracker_hints` (`apbot/services/alerts.py:252`) | add `tests/test_alerts.py` case above |
| `tests/test_bot_live.py:98-116` (monkeypatches `stored_token`) | existing test, must still pass |
| `user_links` table (no longer written on decrypt failure) | assertions on `status` / `token_ciphertext` in the tests above |
| i18n catalogs de/fr/nl | `tests/test_i18n.py` |

## Decisions for the programmer
- Background notice per person. Your instruction allows "at most one DM / log line". Options:
  - (a) Log only: background paths do not DM. The person sees the ephemeral message at their next command, and admins get the single count warning.
  - (b) Also send one DM per person per process start, through a new `on_token_unreadable` listener plus a new DM text in 4 languages. This adds code in `alerts.py`/`bot.py` and DMs every linked person after a mistyped key, even if it is fixed a minute later.
  - Recommendation: (a). The cause is an admin mistake that a restart with the right key fully repairs, and a DM telling everyone to re-link would be misleading in that case. The plan above implements (a). Choosing (b) needs an amendment.

**Resolved 2026-11-10:** the programmer approved the plan with (a), no DMs from background paths.

## Risks / follow-ups
- People whose ciphertext really is lost (key gone for good) stay `active` with an unreadable token indefinitely. They are skipped every 2 minutes, which costs nothing, and they are told on their next command. A later admin command to purge unreadable links could be a follow-up, but it is not part of this fix.
- Game roles of unreadable people are frozen, neither added nor removed, until they re-link or the key is fixed. This is intended per the decision.
