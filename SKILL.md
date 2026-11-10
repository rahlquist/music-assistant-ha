---
name: music-assistant-ha
description: "Control a Music Assistant server: playback, volume, queues, library search, favorites, multi-room grouping/transfer, and announcements. Use when the user wants to play/pause/skip music, adjust volume, search their library, manage the queue, group or transfer players, or make an announcement."
version: 1.0.0
author: rahlquist
license: MIT
platforms: [linux, macos, windows]
required_environment_variables:
  - name: MASS_SERVER_URL
    prompt: Music Assistant server base URL
    help: "http://<ma-host>:8095 (a trailing /api is also accepted)"
    required_for: all operations
  - name: MASS_ACCESS_TOKEN
    prompt: Music Assistant long-lived access token
    help: "Music Assistant Settings -> Security -> create a long-lived token"
    required_for: all operations
  - name: MASS_DEFAULT_PLAYER
    prompt: Default player id (optional)
    help: "Run `mactl players` to list ids. If unset, the player is auto-detected and cached."
    required_for: optional
metadata:
  hermes:
    tags: [Media, Music, Music-Assistant, Smart-Home]
    related_skills: []
---

# music-assistant-ha

Control a Music Assistant (MA) server through `scripts/mactl.py`. Always use the CLI.
Do not call the MA API directly unless the direct-access section below applies.

## Setup

1. Install the client library once, in the Python that runs the CLI:
   `pip install -r requirements.txt` (installs `music-assistant-client`).
2. Set the environment variables:
   - `MASS_SERVER_URL`: the server base URL, for example `http://<ma-host>:8095`.
   - `MASS_ACCESS_TOKEN`: a long-lived token. In MA, go to **Settings → Security** and create one.
   - `MASS_DEFAULT_PLAYER` (optional): a player id from `mactl players`.
3. Check the connection: `python scripts/mactl.py players`.

Pitfalls:
- Use the API base URL, not the web-UI URL. The UI is at `http://<ma-host>:8095/`. The
  CLI accepts `http://<ma-host>:8095/api` and strips the suffix, but the bare host is what the client needs.
- The URL must start with `http://` or `https://`.
- Do not share or paste the token. It is never printed by the CLI.
- Player ids are not stable across installs. Discover them with `players`; do not hardcode them.

## Command reference

Run as `python scripts/mactl.py [global flags] <command> [args] [--player ID]`.

Global flags, accepted before or after the command:
- `--json`: machine-readable output. Success prints `{"ok": true, ...}`. Failure prints `{"ok": false, "error": ..., "exit_code": ...}` on stdout.
- `--quiet`: hides confirmation text such as "playing". Data and errors still print.
- `--player ID`: target player for this call. Overrides `MASS_DEFAULT_PLAYER`.

| Area | Command | Meaning |
|---|---|---|
| Status | `players` | list players: id, name, state, volume, availability |
| Status | `status` | player state, volume, queue summary, now playing |
| Playback | `play-pause`, `pause`, `stop`, `next`, `previous` | transport on the target player's queue |
| Volume | `volume-set N` | volume 0–100 |
| Volume | `mute`, `unmute` | mute or unmute the target player |
| Queue | `shuffle on\|off`, `repeat off\|all\|one` | queue modes |
| Queue | `queue-list [--limit N]` | list items with index and queue_item_id |
| Queue | `queue-clear` | empty the queue |
| Queue | `queue-add URI` | add media to the end of the queue without starting playback |
| Queue | `queue-remove ITEM` | remove by index (number) or queue_item_id |
| Queue | `queue-move QUEUE_ITEM_ID SHIFT` | move by a relative shift (negative = earlier) |
| Queue | `queue-play-index INDEX` | start playing a queue index |
| Play | `play URI [--option play\|replace\|next\|replace_next\|add]` | play a media URI (default `play`) |
| Search | `search QUERY [--type track\|album\|artist\|playlist\|radio] [--limit N] [--library-only]` | search; repeat `--type` for several |
| Search | `search-play QUERY [--type T] [--library-only]` | search and play the top result |
| Library | `recent [--limit N]` | recently played items |
| Library | `sync [--media-type T] [--provider ID]` | start a library sync |
| Favorites | `favorite-add URI` | add to favorites |
| Favorites | `favorite-remove MEDIA_TYPE ITEM_ID` | remove a favorite by media type and library item id |
| Multi-room | `group PLAYER TARGET` | join PLAYER to TARGET's group |
| Multi-room | `ungroup PLAYER` | remove PLAYER from its group |
| Multi-room | `transfer SOURCE TARGET [--auto-play\|--no-auto-play]` | move the queue from SOURCE to TARGET |
| Announce | `announce --text "..." [--tts-engine E] [--language L] [--volume N]` | speak text on a player |
| Announce | `announce --url URL [--volume N]` | play an audio URL as an announcement |
| Announce | `announce-engines` | list TTS engines (needs a newer server, API schema 101) |

Notes:
- In this server, a player's queue id equals its player id. Queue commands use the target player.
- `announce` interrupts active playback and restores it afterward, and it powers on
  players that are off, then powers them off again. Volume is set per player.
- `group` and `transfer` take two player ids. Both are validated before the call.

## Voice-command examples (Hermes gadget)

| Spoken request | Command |
|---|---|
| "pause the music" | `pause` |
| "play some Miles Davis" | `search-play "Miles Davis" --type artist` |
| "play Blue in Green" | `search-play "Blue in Green" --type track` |
| "turn it down to 30" | `volume-set 30` |
| "skip this" | `next` |
| "shuffle on" | `shuffle on` |
| "what's playing?" | `status` |
| "add this to the queue" | `queue-add URI` (only if the URI is known) |
| "move the music to the kitchen" | `transfer <current-player-id> <kitchen-player-id>` |
| "say dinner is ready in the living room" | `announce --player <id> --text "Dinner is ready"` |

## Direct API access (fallback only)

Use this only for an operation the CLI does not cover.

- Endpoint: `POST <MASS_SERVER_URL>/api`, JSON body `{"command": "<name>", "args": {...}}`.
- Header: `Authorization: Bearer <token>`. Read the token from the environment. Never print it.
- Examples from the server's own API docs: `players/all`, `players/cmd/play`.
- The full command list is at https://www.music-assistant.io/api and the live spec at `<MASS_SERVER_URL>/api-docs`.

## Troubleshooting

- `missing required environment variable(s)`: set the named variable (see Setup).
- `authentication failed`: the token is wrong or revoked. Create a new one in Settings → Security.
- `timed out ... connecting`: check the URL, the network, and that MA is running.
- `several players are available and the target is ambiguous`: pass `--player ID` or set
  `MASS_DEFAULT_PLAYER`. Run `players` to see ids.
- `Command not available ... api schema 101`: the server is older than the feature. Update MA.
- `no track found`: try `--library-only` off, a different query, or `search` to inspect results.
- `queue-remove` or `queue-move` with a wrong index: run `queue-list` first and use its indexes and ids.
