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
| `/mydata` | everyone | Everything the bot stores about you, the alerts you turned on included. |
| `/alerts` | linked users | Choose which direct messages the bot sends you (see below). All are off until you pick them. |
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
| `/setup feed game:` | Choose which kinds of activity the thread of a game shows. |
| `/setup events enabled:` | Turn Discord events for game starts and sign-up deadlines on or off. Needs the *Manage Events* permission to turn on. |

The sign-ups channel receives the deadline reminders of open sign-up rounds (see below).

### Live: threads, direct messages, overview and events

The bot reads each game's activity feed from the manager every few seconds while its server is up.

- **Threads and roles.** With a games channel set (`/setup channel kind:games`), every game the bot account sees gets a
  public thread named after it and a role "AP game name" that goes to the linked players of the game. The thread shows
  goals, releases, collects, hints, chat, server messages, status changes and slot hand-overs; items, joins and leaves
  are off until you tick them with `/setup feed`. When a game comes online the role is pinged in its thread. When a game
  is finished the thread gets a summary, is archived and locked, and the role is deleted. Only games that are visible to
  everyone get a thread; a private game never appears in a server.
- **Direct messages (`/alerts`).** Linked people can ask for a message when an item arrives for one of their slots, when
  an item they were hinted is found, when a player is stuck and waits for items in their world, when one of their games
  comes online, and when a slot is offered to them (with buttons to take it over or decline). This works for private
  games too, because the bot reads those with the player's own token. Someone who has closed their DMs is left alone
  for a day. If the manager stops accepting a person's token, they get one message about it.
- **Overview.** With an overview channel set, one pinned message lists the games and open sign-up rounds and is edited
  when something changes. The bot's status shows how many games are online.
- **Events.** With events turned on, the planned start of a game and the deadline of an open sign-up round become Discord
  events.

If the manager also posts to a Discord webhook (game online, stuck, hand-over), turn that off for the channels the bot
posts in, or every message appears twice.

### Player tools

For people who played in a game and linked their account. Replies are private. Every command takes `game:` and, if you
have more than one slot in that game, `slot:`; the manager only lets you act for your own slots. Giving hints, saying
things and releasing need a token with access *Read and change*.

| Command | What |
|---|---|
| `/tracker`, `/hints` | What your slot has received and checked, hint points, whether release and collect are possible, and your open hints. Pick a hint from the list to mark it as priority, no priority or avoid. |
| `/hint item:`, `/hint_location location:` | Asks the game's server for a hint, as you. Names are suggested once the first suggestion has loaded them. |
| `/remaining`, `/say text:` | Asks the server what you still have to find, or says something in the game's chat as your slot. |
| `/release`, `/collect` | Releases the remaining items of your world, or collects what other worlds hold for you, after a confirmation and only when the game's rules allow it now. |
| `/status status: note:` | Tells the others whether you are playing, stuck or away (or clears it). |
| `/claim game: slot:` | Claims a free slot in a game that allows it. |
| `/handover offer`, `/handover cancel` | Offers one of your slots to a Discord member who linked their account, to a manager username, or to anyone; or takes the offer back. |

These commands start a text client connection for you on the manager, so the game's server may announce that you joined
and left. That is why joins and leaves are off in game threads by default.

`/say !release` and `/say !collect` go straight to the game's server and skip the confirmation that `/release` and
`/collect` ask for.

### Sign-ups

Sign-up rounds are run by the manager; these commands let you use them from Discord. Replies are private. `/signups`
(see above) lists the open rounds for everyone; the commands below act as you, with your linked token. Joining and
withdrawing need a token with access *Read and change*. The round is picked from a list of the open ones.

| Command | Who | What |
|---|---|---|
| `/signup show round:` | linked users | The round's state, deadline, rules and your own entries with the problems the manager found. The organizer of the round also sees the latest test and what the generator said about each entry. Other players' entries are only counted. |
| `/signup join round: file:` | linked users | Attach a `.yaml` or `.yml` file (at most 1 MiB, UTF-8). The manager checks it first and the result is shown; when it passes, the file is saved in your manager library under its file name and entered. A file with problems is neither saved nor entered. |
| `/signup join round: config:` | linked users | Enters one of the configs you already saved in the manager. It was checked when you saved it, so it is not checked again. Give either a file or a config. |
| `/signup withdraw round: entry:` | linked users | Takes one of your entries out of a round, after a confirmation. Only while the round takes entries. |
| `/signup open name: deadline: slots_per_user: description:` | organizers | Opens a round. The deadline is `YYYY-MM-DD HH:MM` on the clock of the server's time zone (`/setup timezone`); leave it empty for a round without a deadline. Slots per player is 1 to 20. |
| `/signup close round:` | organizers | Closes the sign-ups of a round, after a confirmation. |
| `/signup test round:` | organizers | Starts a test generation of the entries without keeping the result, and shows the outcome here when it is over (the bot looks every 10 seconds, for up to 10 minutes). |

`/signup open`, `close` and `test` need the organizer role when the server has set one (`/setup role`), and the manager
must see you as an organizer or administrator; `close` and `test` only work for rounds you opened (administrators: all).
Mystery rounds cannot be joined from Discord (the manager asks for a player name there); use the manager.

**Reminders.** With a sign-ups channel set (`/setup channel kind:signups`), the bot posts there 24 hours and 1 hour
before an open round with a deadline closes, once for each deadline (a moved deadline gets its reminders again). If both
are due at once, only the 1 hour reminder is posted. The manager can also post its own reminder to a webhook: turn that
off for the channel you set here, or it appears twice.

## Setup

### 1. A Discord application

1. Open the [Discord developer portal](https://discord.com/developers/applications), create an application and add a
   *Bot* to it. Copy the bot token (it goes into `DISCORD_TOKEN`, see below).
2. Leave all *Privileged Gateway Intents* off. The bot needs the `guilds` intent only, and it reads no messages.
3. Invite it to your server with this link (replace `YOUR_APPLICATION_ID` with the application id from the portal):

   `https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&scope=bot+applications.commands&permissions=335275977728`

   The link asks for what the live features need: View Channels, Send Messages, Send Messages in Threads, Create Public
   Threads, Manage Threads, Embed Links, Read Message History, Manage Messages (to pin the overview), Manage Roles (game
   roles) and Manage Events (scheduled events). The commands alone need none of them. `/setup channel` tells you which
   permissions the bot lacks in a channel you pick.

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

#### Unraid

1. Add the template in the Unraid terminal (or web terminal), until the bot is in Community Applications:

   ```sh
   curl -fsSL -o /boot/config/plugins/dockerMan/templates-user/ap-discord-bot.xml      https://raw.githubusercontent.com/NiklaskoDev/ArchipelagoDiscordBot/main/unraid/ap-discord-bot.xml
   ```

   `-f` keeps an error page from being saved as the template. Run the same command again to update it.
2. Open *Docker -> Add Container*, pick **ap-discord-bot** from the template list, fill in the two tokens, the
   encryption key (the `docker run --rm ...` one-liner above also works in the Unraid terminal) and the manager
   address, then *Apply*.
3. Unraid has no compose network, so the bot needs a way to reach the manager. Either:
   - **Custom network (recommended).** Run `docker network create archipelago` once, set *Network Type* of both the
     manager and the bot container to `archipelago`, and use `MANAGER_URL=http://ArchipelagoManager:8080` (the manager
     template's container name). Turn on *Settings -> Docker -> Preserve user defined networks* (Advanced view) so the
     network survives a reboot.
   - **Unraid's address.** Use `MANAGER_URL=http://<Unraid IP>:<manager web UI port>`. The API token then crosses your
     network unencrypted, as the `http://` note above says.
4. The bot runs as `nobody:users` (99:100) like the manager, and keeps its data in `/mnt/user/appdata/ap-discord-bot`.

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
- For each person: the alerts they turned on with `/alerts`.
- For each server that ran `/setup`: its settings (language, time zone, channels, roles, events on or off), the thread
  and role the bot made for each game, the overview message and the Discord events it made.
- For each game: how far its activity feed was read.
- For each open sign-up round: which deadline reminders were already posted (server, round, deadline, lead time).

`/mydata` shows a person everything stored about them. `/unlink` deletes it, takes the person out of the game roles
and forgets their alerts. When the bot leaves a server, that server's data is deleted. The bot cannot revoke a token in the manager: after `/unlink`, do that under *API tokens*.
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

## License

[MIT](LICENSE)
