# 003 — Unraid Docker template, installed with curl

Status: approved
Type: feature
Version impact: minor — new install path, no code, config or schema change. 0.1.0 is unreleased (`CHANGELOG.md` still has `YYYY-MM-DD`), so it rides in `[Unreleased]`.

## Goal
Unraid users install the bot from a template: one `curl` command in the Unraid terminal puts
`unraid/ap-discord-bot.xml` into `/boot/config/plugins/dockerMan/templates-user/`, then *Docker → Add Container* offers it
with every setting of the bot, secrets masked, and Unraid's defaults (99:100, appdata).
Out of scope: listing in Community Applications, a CA `ca_profile.xml`, any change to the bot's code or settings, a
compose file for Unraid.

## Reuse
- Manager template `ArchipelagoManagerDocker/unraid/archipelago-manager.xml` → same structure, element order, attribute
  set and description tone (`Container version="2"`, `<Config ... Type/Display/Required/Mask>`).
- Manager README "Unraid" section → same numbered steps; `wget -O` becomes `curl -fsSL -o`.
- `.env.example`, `apbot/config.py`, `docker/entrypoint.sh` → the list of variables and their defaults/wording; the README
  Settings table → descriptions.
- New `unraid/` folder: nothing existing holds platform templates; the path matches the manager repo so both
  `TemplateURL`s look alike.

## Code layout
- `unraid/ap-discord-bot.xml` — new, the Unraid template.
- `unraid/icon.png` — new, only if decision D1 = (a).
- `tests/test_unraid_template.py` — new, keeps template, code, `.env.example` and README in sync.
- `README.md` — new "Unraid" subsection under "Setup → 3. Run it".
- `CHANGELOG.md` — one line under `[Unreleased] → Added`.
- `.dockerignore` — add `unraid` (template and icon do not belong in the image).

## Data
None.

## Interfaces
Public data format (new, stable from now on because users' Unraid boxes fetch it):
`https://raw.githubusercontent.com/NiklaskoDev/ArchipelagoDiscordBot/main/unraid/ap-discord-bot.xml`

### Template content (`unraid/ap-discord-bot.xml`)
Header elements, in the manager's order:
- `Name` `ap-discord-bot` (matches `container_name` in `docker-compose.yml`)
- `Repository` `ghcr.io/niklaskodev/archipelagodiscordbot:latest`
- `Registry` `https://github.com/NiklaskoDev/ArchipelagoDiscordBot/pkgs/container/archipelagodiscordbot`
- `Network` `bridge`, `<MyIP/>`, `Shell` `sh`, `Privileged` `false`
- `Support` `https://github.com/NiklaskoDev/ArchipelagoDiscordBot/issues`, `Project` `https://github.com/NiklaskoDev/ArchipelagoDiscordBot`
- `Overview`: 3–6 lines (`&#xD;` line breaks like the manager): what the bot does (games, sign-ups, live threads, player
  tools from Discord, using the Archipelago Manager), that it needs a running manager, and one sentence on reaching it
  (see MANAGER_URL below).
- `Category` `Tools: GameServers:`
- No `WebUI` (the bot has none): `<WebUI/>`
- `TemplateURL` the raw URL above; `Icon` per D1 (empty `<Icon/>` for (c)).
- `<ExtraParams/>`, `<PostArgs/>`, `<CPUset/>`, `<DateInstalled/>`, `<DonateText/>`, `<DonateLink/>`, `<Requires/>`.
  `Requires`: one line "A running Archipelago Manager (ghcr.io/niklaskodev/archipelagomanagerdocker) and a Discord
  application" — fill `<Requires>` with it instead of leaving it empty.

`Config` entries, in this order (all `Type="Variable"`, `Mode=""` unless stated):

| Name | Target | Default / value | Display | Required | Mask |
|---|---|---|---|---|---|
| Data (appdata) | `/data` (`Type="Path"`, `Mode="rw"`) | `/mnt/user/appdata/ap-discord-bot` | always | true | false |
| Discord bot token | `DISCORD_TOKEN` | empty | always | true | true |
| Manager address | `MANAGER_URL` | empty | always | true | false |
| Manager API token | `MANAGER_API_TOKEN` | empty | always | true | true |
| Encryption key | `BOT_ENCRYPTION_KEY` | empty | always | true | true |
| Log level | `LOG_LEVEL` | `INFO` | advanced | false | false |
| Development server id | `DISCORD_DEV_GUILD_ID` | empty | advanced | false | false |
| Data folder in the container | `DATA_DIR` | `/data` | advanced | false | false |
| PUID | `PUID` | `99` | advanced | true | false |
| PGID | `PGID` | `100` | advanced | true | false |
| UMASK | `UMASK` | `022` | advanced | false | false |
| Timezone | `TZ` | empty | advanced | false | false |

Descriptions (coder words them, one or two sentences each, from `.env.example`/README):
- Data: "SQLite database and heartbeat. Back this folder up together with the encryption key."
- DISCORD_TOKEN / MANAGER_API_TOKEN: where to get them (README Setup 1 and 2); API token is a plain user's, access Read, starts with `apm_`.
- MANAGER_URL: "Address of the manager without a path, as this container sees it: `http://ArchipelagoManager:8080` when
  both containers are on the same custom Docker network, otherwise `http://<Unraid IP>:8080`. See the README." (order of
  the two follows D2).
- BOT_ENCRYPTION_KEY: Fernet key(s), comma-separated, first encrypts; the `docker run --rm ghcr.io/niklaskodev/archipelagodiscordbot python -c ...` one-liner from the README; losing it means everyone links again.
- DATA_DIR: "Leave at /data; it must match the container path of 'Data (appdata)'."
- PUID/PGID/UMASK: copy the manager's wording ("99 is Unraid's 'nobody'", "100 is Unraid's 'users'").
- DISCORD_DEV_GUILD_ID: development only, registers commands in this one server.

Secrets: every masked entry has an empty element body and `Default=""` (self-closing `<Config .../>`).

## Security and user data
- Masked in the template: `DISCORD_TOKEN`, `MANAGER_API_TOKEN`, `BOT_ENCRYPTION_KEY`. No value or default in the repo;
  the test enforces it.
- `MANAGER_URL` over `http://<Unraid IP>:8080` sends the API token in clear over the LAN; the README already says
  "http only on a private Docker network, anywhere else https" — the README text must stay consistent with that (D2).

## UI
None (no Discord UI change). README text only.

## Steps
1. If D1 = (a): copy the manager's `unraid/icon.png` byte for byte to `unraid/icon.png`.
2. Write `unraid/ap-discord-bot.xml` per *Interfaces*, UTF-8, `<?xml version="1.0"?>` header like the manager's.
3. Add `unraid` as its own line to `.dockerignore`.
4. README: under "### 3. Run it", after the existing compose paragraphs and before "## Settings", add `#### Unraid`
   (rename nothing else). Content, numbered like the manager's:
   1. Add the template (Unraid terminal / web terminal), until it is in Community Applications:
      ```sh
      curl -fsSL -o /boot/config/plugins/dockerMan/templates-user/ap-discord-bot.xml \
        https://raw.githubusercontent.com/NiklaskoDev/ArchipelagoDiscordBot/main/unraid/ap-discord-bot.xml
      ```
      One sentence: `-f` keeps an error page from being saved as the template. Run the same command again to update it.
   2. *Docker → Add Container*, pick **ap-discord-bot** from the template list, fill in the two tokens, the encryption key
      (the `docker run --rm ...` one-liner above also works in the Unraid terminal) and the manager address, *Apply*.
   3. Reaching the manager — Unraid has no compose network. Two ways (order and wording per D2):
      - Custom network: `docker network create archipelago` once; set *Network Type* of both the manager and the bot
        container to `archipelago`; `MANAGER_URL=http://ArchipelagoManager:8080` (the manager template's container
        name). Mention: enable *Settings → Docker → Preserve user defined networks* (Advanced view) so the network
        survives a reboot.
      - Unraid's address: `MANAGER_URL=http://<Unraid IP>:<manager web UI port>`, with the note from line 162 that the
        token then crosses the LAN unencrypted.
   4. One line: runs as `nobody:users` (99:100) like the manager; the appdata folder is `/mnt/user/appdata/ap-discord-bot`.
5. `CHANGELOG.md` `[Unreleased] → Added`: "Unraid template (`unraid/ap-discord-bot.xml`), installed with one `curl`
   command (see the README)."
6. Write `tests/test_unraid_template.py` (below). Run `pytest` and `ruff check .`.

## Tests
`tests/test_unraid_template.py`, stdlib only (`xml.etree.ElementTree`, `re`, `pathlib`); paths from
`Path(__file__).resolve().parent.parent`. Helpers:
- `template_configs()` → `{Target: Element}` for every `<Config>`.
- `variables_read()` → union of
  - `apbot/config.py`: regex `(?:env\.get\(|_required\(env,\s*)"([A-Z][A-Z0-9_]*)"`,
  - `docker/entrypoint.sh`: regex `\$\{([A-Z][A-Z0-9_]*):-`,
  - `.env.example`: regex `^#?\s*([A-Z][A-Z0-9_]*)=` per line (`re.M`).

Cases:
- Scan sanity: `variables_read()` ⊇ {DISCORD_TOKEN, MANAGER_URL, MANAGER_API_TOKEN, BOT_ENCRYPTION_KEY, DATA_DIR, LOG_LEVEL, DISCORD_DEV_GUILD_ID, PUID, PGID, UMASK, TZ} → passes (catches a broken regex).
- Sync: set of `Target`s with `Type="Variable"` == `variables_read()` → a new env var without a template entry fails, and so does a stale template entry.
- Masked: `Mask="true"` exactly for {DISCORD_TOKEN, MANAGER_API_TOKEN, BOT_ENCRYPTION_KEY}; all others `"false"`.
- No secret in the repo: masked entries have `Default=""` and no text.
- Required: `Required="true"` for the four required settings plus PUID/PGID and the `/data` path; `"false"` for the rest.
- Defaults: PUID `99`, PGID `100`, UMASK `022`, LOG_LEVEL `INFO`, DATA_DIR `/data`, each in both `Default` and text.
- Data path: one `Type="Path"` entry, `Target="/data"`, `Mode="rw"`, default `/mnt/user/appdata/ap-discord-bot`.
- Header: `Repository` == `ghcr.io/niklaskodev/archipelagodiscordbot:latest`; `TemplateURL` == raw URL on `main`.
- README in sync: `README.md` contains `curl -fsSL -o /boot/config/plugins/dockerMan/templates-user/ap-discord-bot.xml` and the exact `TemplateURL` value.
- Icon (D1 = a): `Icon` == `https://raw.githubusercontent.com/NiklaskoDev/ArchipelagoDiscordBot/main/unraid/icon.png` and `unraid/icon.png` exists. (D1 = b: `Icon` == the manager's raw icon URL. D1 = c: `Icon` empty.)
- File parses as XML (implicit in every helper).

## Decisions for the programmer
- D1 Icon — (a) copy the manager's `icon.png` into `unraid/icon.png` here and point at this repo's raw URL; (b) point
  `Icon` at the manager repo's raw `icon.png`, no file added; (c) no icon (Unraid shows its generic one).
  Recommendation: (a) — self-contained, survives changes in the other repo; same look as the manager, which fits a
  companion app. Same owner, so no licence question.
- D2 How the README tells the bot to reach the manager — (a) custom Docker network first, Unraid IP:port as the
  alternative with the "token travels unencrypted on the LAN" note; (b) Unraid IP:port first (simplest), custom network
  as the more private option; (c) custom network only. Recommendation: (a) — matches the existing README rule that
  `http://` is only for a private Docker network, while keeping the simple path available.
- D3 Public file name/location — `unraid/ap-discord-bot.xml` (matches `container_name`) vs. `unraid/archipelago-discord-bot.xml`
  (matches the repo name). Once users have fetched it the URL is a public interface; changing it later breaks their
  update command. Recommendation: `unraid/ap-discord-bot.xml`.

**Resolved 2026-11-10:** the programmer approved the plan with every recommended option: D1 (a) copy the manager's icon to `unraid/icon.png` · D2 (a) custom Docker network first, IP:port as the alternative with the unencrypted-LAN note · D3 file name `unraid/ap-discord-bot.xml`.

## Risks / follow-ups
- The GHCR image `ghcr.io/niklaskodev/archipelagodiscordbot:latest` only exists after the first release:
  `release.yml` publishes on `v*` tags only, and `:latest` only for non-`-prN` tags. Until then *Apply* fails with
  "manifest unknown"; the README step should not be advertised before 0.1.0 is released (or say so in one line).
- After the first push GHCR packages are private by default; the package must be set to public, or Unraid cannot pull
  it without login. Check once after the first release.
- The template on `main` changes for every user who re-runs the curl command, also before a release that matches it;
  a template entry for a variable added on `main` may reach users whose `:latest` image does not read it yet (harmless:
  unknown env vars are ignored).
- Unraid's default `bridge` network has no container-name DNS; `http://ArchipelagoManager:8080` works only on the custom
  network, and only if the manager container kept its template name.
