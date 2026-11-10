# Changelog

Versions follow `{major}.{minor}.{hotfix}[-prN]` — see `docs/ai/versioning.md`.
Sections per version: Added, Changed, Fixed, Removed, Security.

## [Unreleased]
### Added
- Discord bot with the commands `/games`, `/game` and `/signups`, in English, German, French and Dutch.
- Docker image for amd64 and arm64, with an example `docker-compose.yml` that runs it next to the Archipelago Manager.
- Account linking with `/link`, `/unlink` and `/mydata` (manager tokens are stored encrypted), server settings with `/setup`, and `/wake`, `/start`, `/stop`, `/restart`. New required setting `BOT_ENCRYPTION_KEY`.
- Live features: a thread and a role per game with the game's activity (`/setup feed` picks the kinds), opt-in direct messages with `/alerts` (items, hints, stuck players, game online, offered slots with take-over buttons), a pinned overview message and bot status, and Discord events for game starts and sign-up deadlines (`/setup events`). `/mydata` lists the alerts that are on; `/unlink` removes the person from the game roles.
- Player tools: `/tracker` and `/hints` (with buttons to mark a hint), `/hint`, `/hint_location`, `/remaining`, `/say`, `/release`, `/collect`, `/status`, `/claim` and `/handover offer|cancel`, in all four languages.
- Sign-ups from Discord: `/signup show|join|withdraw` (attach a YAML file, which the manager checks before it is saved and entered, or enter a saved config), `/signup open|close|test` for organizers (the deadline is read in the server's time zone; a test reports its result in the reply), and deadline reminders 24 hours and 1 hour before a round closes in the sign-ups channel, in all four languages. The bot's database gets a new table (migration 003, applied at startup).

### Fixed
- One scheduled event that Discord refuses no longer stops the other events of the server from syncing.
- Deleting the organizer or admin role in Discord no longer leaves it configured, which locked everyone out of `/start`, `/stop` and `/restart`.
- `/wake`, `/start`, `/stop` and `/restart` look up the person's stored token once instead of twice.
- A `MANAGER_URL` with a malformed port is refused at startup with a clear message instead of crashing later.
- A very long game address in `/game` is cut to fit.
- `/handover offer` with a blank username offers the slot to anyone, as it says, instead of to a user with an empty name.

## [0.1.0] - YYYY-MM-DD
### Added
- Initial version.
