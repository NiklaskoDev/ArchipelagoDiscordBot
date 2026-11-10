# Archipelago Discord bot

A Discord bot for [Archipelago](https://archipelago.gg) multiworld games. It sits next to an
[Archipelago Manager](https://github.com/NiklaskoDev/ArchipelagoManagerDocker) (version 1.3.0 or newer) and uses only
the manager's REST API: nothing in the manager is changed.

It ships as a Docker image for amd64 and arm64 and speaks English, German, French and Dutch (it follows each user's
Discord language).

## What it does in this version

| Command | Who | What |
|---|---|---|
| `/games` | everyone | The games that are not finished: state, number of worlds, players and goals. Running games first. |
| `/game game:` | everyone | One game in detail: state, address, Archipelago version, planned start, progress, goals, every slot with its world, owner, checks and status, and a button that opens the game in the manager. |
| `/signups` | everyone | The sign-up rounds that take entries, soonest deadline first. |

These commands show what the bot's own manager account may see (see below), never more, and never a room password.
Replies are public, except error messages, which only the person who ran the command sees.

## Setup

### 1. A Discord application

1. Open the [Discord developer portal](https://discord.com/developers/applications), create an application and add a
   *Bot* to it. Copy the bot token (it goes into `DISCORD_TOKEN`, see below).
2. Leave all *Privileged Gateway Intents* off. The bot needs the `guilds` intent only, and it reads no messages.
3. Invite it to your server with this link (replace `YOUR_APPLICATION_ID` with the application id from the portal):

   `https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&scope=bot+applications.commands`

   The commands in this version need no channel permissions. Later versions that post in channels will list what they
   need here.

### 2. The bot's account on the manager

1. In the manager, create a user for the bot (role **user**, not organizer or administrator) and sign in as it.
   With a plain user, `/games` and `/signups` can only ever show games whose visibility is "all"; private games stay
   private. The bot warns at startup if the account is more than a plain user.
2. Under *API tokens*, create a token named "Discord bot" with access **Read**. Copy it (it starts with `apm_`) into
   `MANAGER_API_TOKEN`.

### 3. Run it

Copy [`.env.example`](.env.example) to `.env` next to `docker-compose.yml`, fill in the two tokens, then:

```sh
docker compose up -d
```

[`docker-compose.yml`](docker-compose.yml) starts the manager and the bot together. If you already run the manager,
keep only the `ap-discord-bot` service and point `MANAGER_URL` at it. To build the image yourself, uncomment
`build: .` and run `docker compose up -d --build`.

`MANAGER_URL` may use `http://` on a private Docker network such as the one above. Anywhere else, use `https://`: the
API token travels in a request header. TLS certificates are always verified.

The bot keeps running when the manager is down and retries every 30 seconds. It stops with exit code 2 when the manager
refuses `MANAGER_API_TOKEN`.

## Settings

All settings are environment variables. Secrets belong in `.env` (git-ignored), never in the compose file.

| Name | Required | Default | Notes |
|---|---|---|---|
| `DISCORD_TOKEN` | yes | | The bot token of your Discord application. Secret. |
| `MANAGER_URL` | yes | | The manager's address without a path, e.g. `http://archipelago-manager:8080`. |
| `MANAGER_API_TOKEN` | yes | | The bot account's manager API token (`apm_...`). Secret. |
| `DATA_DIR` | no | `/data` | Where the bot keeps its files. Mount a volume here. |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. |
| `DISCORD_DEV_GUILD_ID` | no | | Only for development: register the commands in this one server instead of globally (it is instant; global commands can take a while to appear). |
| `PUID`, `PGID`, `UMASK`, `TZ` | no | `99`, `100`, `022` | Container only: the user and group that own `/data` (Unraid's defaults), file mode mask and time zone. |

Logs never contain tokens: anything that looks like a manager token, a Discord token or an encryption key is replaced
by `[redacted]`.

## Privacy

This version stores no personal data. It keeps one file in `DATA_DIR`, `heartbeat`, which the container's health check
reads. Versions that add account linking and alerts will state here what they store and how to export or delete it.

## Development

Python 3.12.

```sh
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # Windows: .venv\Scripts\pip
.venv/bin/ruff check .
.venv/bin/pytest
docker build -t ap-discord-bot .
```

To run the bot from source, set the variables above (and `DISCORD_DEV_GUILD_ID` to your test server) and run
`python -m apbot`. `CHANGELOG.md` lists what changed in each version.
