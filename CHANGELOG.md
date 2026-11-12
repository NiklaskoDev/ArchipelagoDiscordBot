# Changelog

Versions follow `{major}.{minor}.{hotfix}[-prN]` — see `docs/ai/versioning.md`.
Sections per version: Added, Changed, Fixed, Removed, Security.

## [Unreleased]

## [0.3.0] - 2026-11-12
### Added
- `/friends` lists your friends and requests with buttons to accept, decline, take back or remove; `/friend add` asks a member or a manager user; a new alert sends a direct message with Accept and Decline for each new friend request. A manager without the friends feature gets a clear message. The bot's database gets a new migration (004, applied at startup).
- `/generate` generates a game from your configs and follows it; `/game_archive` moves a game to the archive and back; `/game_delete` deletes one. All act as you, after a confirmation for archive and delete.
- `/generate` also offers the configs your friends share with you; the friend gets a direct message if they turned the new alert "A friend puts me in a game they generate" on. `/config share` shares one of your configs with everyone (after a confirmation), all your friends or chosen friends, or stops sharing it.

### Fixed
- `/friends`: when a list is longer than the menu (25 people), a note now says so and points to the manager for the rest; the buttons under a chosen person answer a removed or invalid link privately instead of failing after "thinking".
- `/generate`: a Cancel pressed while a progress update is on its way no longer gets its greyed-out button undone, and a generation the manager no longer shows ends with a short private notice and a link to the manager instead of a generic error.

## [0.2.0] - 2026-11-11
### Added
- `/games` and `/game` also show linked users the private games their own manager account can see, and the game suggestions of `/game`, `/wake`, `/start`, `/stop` and `/restart` include them; only that person sees them.

## [0.1.0] - 2026-11-10
### Added
- Discord bot with the commands `/games`, `/game` and `/signups`, in English, German, French and Dutch.
- Docker image for amd64 and arm64, with an example `docker-compose.yml` that runs it next to the Archipelago Manager.
- Account linking with `/link`, `/unlink` and `/mydata` (manager tokens are stored encrypted), server settings with `/setup`, and `/wake`, `/start`, `/stop`, `/restart`. New required setting `BOT_ENCRYPTION_KEY`.
- Live features: a thread and a role per game with the game's activity (`/setup feed` picks the kinds), opt-in direct messages with `/alerts` (items, hints, stuck players, game online, offered slots with take-over buttons), a pinned overview message and bot status, and Discord events for game starts and sign-up deadlines (`/setup events`). `/mydata` lists the alerts that are on; `/unlink` removes the person from the game roles.
- Player tools: `/tracker` and `/hints` (with buttons to mark a hint), `/hint`, `/hint_location`, `/remaining`, `/say`, `/release`, `/collect`, `/status`, `/claim` and `/handover offer|cancel`, in all four languages.
- Sign-ups from Discord: `/signup show|join|withdraw` (attach a YAML file, which the manager checks before it is saved and entered, or enter a saved config), `/signup open|close|test` for organizers (the deadline is read in the server's time zone; a test reports its result in the reply), and deadline reminders 24 hours and 1 hour before a round closes in the sign-ups channel, in all four languages. The bot's database gets a new table (migration 003, applied at startup).
- MIT license.
- Unraid template (`unraid/ap-discord-bot.xml`), installed with one `curl` command (see the README).

### Fixed
- One scheduled event that Discord refuses no longer stops the other events of the server from syncing.
- Deleting the organizer or admin role in Discord no longer leaves it configured, which locked everyone out of `/start`, `/stop` and `/restart`.
- `/wake`, `/start`, `/stop` and `/restart` look up the person's stored token once instead of twice.
- A `MANAGER_URL` with a malformed port is refused at startup with a clear message instead of crashing later.
- A very long game address in `/game` is cut to fit.
- `/handover offer` with a blank username offers the slot to anyone, as it says, instead of to a user with an empty name.
- A wrong or missing encryption key no longer erases linked accounts; affected people are asked to /link again and admins see one warning.
- The container's start-up message about fixing data folder ownership no longer mixes into the output of the command it runs.
