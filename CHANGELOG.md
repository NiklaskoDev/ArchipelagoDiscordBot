# Changelog

Versions follow `{major}.{minor}.{hotfix}[-prN]` — see `docs/ai/versioning.md`.
Sections per version: Added, Changed, Fixed, Removed, Security.

## [Unreleased]
### Added
- Discord bot with the commands `/games`, `/game` and `/signups`, in English, German, French and Dutch.
- Docker image for amd64 and arm64, with an example `docker-compose.yml` that runs it next to the Archipelago Manager.
- Account linking with `/link`, `/unlink` and `/mydata` (manager tokens are stored encrypted), server settings with `/setup`, and `/wake`, `/start`, `/stop`, `/restart`. New required setting `BOT_ENCRYPTION_KEY`.

### Fixed
- A `MANAGER_URL` with a malformed port is refused at startup with a clear message instead of crashing later.
- A very long game address in `/game` is cut to fit.

## [0.1.0] - YYYY-MM-DD
### Added
- Initial version.
