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

### Linking and game control

Link your manager account once and the bot acts as you: the manager decides what you may do, the bot never gives you
more. These replies are private (only you see them).

| Command | Who | What |
|---|---|---|
| `/link` | everyone | Opens a form for your manager API token. Create one in the manager under *API tokens* with access *Read and change* and name it "Discord bot". The token is checked, stored encrypted and never shown again. |
| `/unlink` | linked users | Forgets your token and settings after a confirmation. Revoke the token in the manager as well: the bot cannot do that. |
| `/mydata` | everyone | Everything the bot stores about you. |
| `/wake game:` | linked users | Wakes a sleeping game (players of the game, and managers). |
| `/start`, `/stop`, `/restart game:` | linked users, organizer role if set | Starts, stops or restarts a game, as far as the manager allows you. The manager's answer is shown as it is, for example "This game opens at its planned time". |

### Server settings (`/setup`)

For people with the *Manage Server* permission, and for the admin role if the server has set one. Replies are private.

| Command | What |
|---|---|
| `/setup show` | The settings of this server and a check of the manager. |
| `/setup manager` | Checks the manager now: reachable, version, the bot's token and account, web address. |
| `/setup channel kind: channel:` | Choose the games, sign-ups or overview channel. The bot checks it has the permissions it will need there and lists any that are missing. |
| `/setup role kind: role:`, `/setup role_clear kind:` | The organizer role (limits `/start`, `/stop`, `/restart`; without one, everyone linked may try) and the admin role (may use `/setup` without *Manage Server*). |
| `/setup language language:` | The language of the bot's posts in this server. |
| `/setup timezone timezone:` | The time zone for dates typed in this server. |

The channel settings are saved now and used by later versions; nothing is posted in them yet.

## Setup

### 1. A Discord application

1. Open the [Discord developer portal](https://discord.com/developers/applications), create an application and add a
   *Bot* to it. Copy the bot token (it goes into `DISCORD_TOKEN`, see below).
2. Leave all *Privileged Gateway Intents* off. The bot needs the `guilds` intent only, and it reads no messages.
3. Invite it to your server with this link (replace `YOUR_APPLICATION_ID` with the application id from the portal):

   `https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&scope=bot+applications.commands`

   The commands in this version need no channel permissions. `/setup channel` tells you which permissions the bot needs
   in a channel you pick.

### 2. The bot's account on the manager

1. In the manager, create a user for the bot (role **user**, not organizer or administrator) and sign in as it.
   With a plain user, `/games` and `/signups` can only ever show games whose visibility is "all"; private games stay
   private. The bot warns at startup if the account is more than a plain user.
2. Under *API tokens*, create a token named "Discord bot" with access **Read**. Copy it (it starts with `apm_`) into
   `MANAGER_API_TOKEN`.

### 3. Run it

Copy [`.env.example`](.env.example) to `.env` next to `docker-compose.yml`, fill in the two tokens and the
encryption key, then:

```sh
docker compose up -d
```

Make the encryption key once with `docker run --rm ghcr.io/niklaskodev/archipelagodiscordbot python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
(or the same `python -c` with a local Python that has `cryptography`). Keep it safe: without it, nobody's stored token can be read and everyone has to run `/link` again.

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
| `BOT_ENCRYPTION_KEY` | yes | | Encrypts the manager tokens people link. One or more Fernet keys separated by commas; the first encrypts, all decrypt. To replace a key, put the new one in front and keep the old one until everyone has used a linked command once (the bot then re-encrypts their token). Secret. |
| `DATA_DIR` | no | `/data` | Where the bot keeps its files. Mount a volume here. |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. |
| `DISCORD_DEV_GUILD_ID` | no | | Only for development: register the commands in this one server instead of globally (it is instant; global commands can take a while to appear). |
| `PUID`, `PGID`, `UMASK`, `TZ` | no | `99`, `100`, `022` | Container only: the user and group that own `/data` (Unraid's defaults), file mode mask and time zone. |

Logs never contain tokens: anything that looks like a manager token, a Discord token or an encryption key is replaced
by `[redacted]`.

## Privacy

What the bot stores, in `DATA_DIR/bot.sqlite3`:

- For each person who ran `/link`: their Discord user id, their manager user id and username, their manager API token
  (encrypted with `BOT_ENCRYPTION_KEY`, plus its last four characters so `/mydata` can tell tokens apart), their
  language, and when they linked. No messages, no Discord names.
- For each server that ran `/setup`: its settings (language, time zone, channels and roles).

`/mydata` shows a person everything stored about them. `/unlink` deletes it. When the bot leaves a server, that
server's settings are deleted. The bot cannot revoke a token in the manager: after `/unlink`, do that under *API tokens*.
A token pasted into a normal channel by mistake cannot be read by the bot (it reads no messages), but other people in
the channel can see it: revoke it in the manager.

A linked administrator's write token lets the bot act with administrator rights for that person. Prefer a dedicated
token that expires.

The file `heartbeat` in `DATA_DIR` is read by the container's health check.

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
