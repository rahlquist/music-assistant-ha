# Findings: music-assistant-ha

Status: Phase 1 research complete. Phase 2 implemented (see `docs/VERIFICATION.md`).

## Environment

| Check | Result |
|---|---|
| Python | 3.14.7 (project venv). Release notes for 3.14.8 and 3.14.9 list no language-level breaking changes; see VERIFICATION.md. |
| MA server | Reachable. `/info`: server 2.10.9, schema 65, min supported schema 28. |
| Token | Verified against `players/all` (HTTP 200). Token stored in the project `.env` (git-ignored, never committed). |

## Section 3.2 items

### 1. API surface
- VERIFIED: the server's OpenAPI spec (`/api-docs/openapi.json`) exposes `POST /api` with `{"command", "args"}` and bearer auth.
- VERIFIED from the installed client source (`music_assistant_client` 1.8.0): method names for players, player queues, music (search, favorites, recently played, sync), and announcements. The CLI uses these.
- VERIFIED live: `players/all`, `music/search`, `music/recently_played`, `player_queues/get_queue_items`, `players/tts_engines` (rejected on schema 65, as expected).
- Command reference pointers: https://www.music-assistant.io/api (the Commands page with curl examples) and the live `/api-docs`.

### 2. Transport
- VERIFIED: the official client connects over WebSocket (`/ws`), and `POST /api` also exists.
- Decision: use the official client over WebSocket for every capability. The client handles auth, schema checks, and events. The raw `POST /api` path is documented only as a fallback. The client's schema guards (`require_schema`) already protect older-server cases.
- VERIFIED live: the client path works for every read-only operation tested.

### 3. Announcement semantics
- VERIFIED (MA docs): interrupt and restore playback, power on before and off after, per-player volume, TTS engine set server-wide.
- VERIFIED (client source): parameters are `url`, `message`, `volume_level`, `tts_engine`, `language`, `pre_announce_url`. The client requires schema 46 for message and 47 for language.
- NOT VERIFIED audibly: the CLI does not play announcements during testing, to avoid sound on real devices.

### 4. Grouping, transfer, favorites
- VERIFIED (client source): `players.group(player_id, target_player)`, `players.ungroup(player_id)`, `player_queues.transfer(source_queue_id, target_queue_id, auto_play)`, `music.add_item_to_favorites(item)`, `music.remove_item_from_favorites(media_type, library_item_id)`.
- VERIFIED live: queue id equals player id on this server.
- NOT VERIFIED audibly: group and transfer behavior on real players.

### 5. Client auth and lifecycle
- VERIFIED (client source and README): `MusicAssistantClient(url, None, token=...)`. Use `start_listening(init_ready=event)` for initial state. `start_listening` runs until cancelled, so the CLI waits on the event. `disconnect()` on exit.
- VERIFIED live: connect, initial state load, and clean disconnect.
- Error classes: `AuthenticationFailed` and `InvalidToken` (mapped to exit 3). `InvalidServerVersion` (mapped to exit 1 with a clear message).

### 6. Connection details
- VERIFIED: base URL `http://192.168.8.36:8095` reachable. The client takes the base URL and adds the path itself. The `/api` suffix in the spec is accepted and stripped by the CLI.

## Transport decision (final)
Official client over WebSocket. Reason: maintainers' stated preference (discussion 3802), built-in auth and schema checks, and it works live.

## Clean-room attestation
Sources consulted:
1. Live MA server at the configured address: `/api-docs`, `/api-docs/openapi.json`, `/info`, and `players/all` plus read-only search and queue calls.
2. https://www.music-assistant.io/integration/announcements
3. https://github.com/music-assistant/client (README)
4. https://github.com/music-assistant/server (controller directory listing)
5. Installed `music-assistant-client` 1.8.0 source (PyPI)
6. Python release pages: python.org 3.14.8 and 3.14.9
7. Command reference pointer from the owner: https://www.music-assistant.io/api

Not consulted: no Music Assistant skill, community integration, or skill registry or hub was searched, cloned, or read.

## Open questions (resolved or pending)
- Token location: resolved. Token is in the project `.env`, git-ignored.
- Python version: 3.14.7 used per owner decision.
- Client pin: unpinned per owner decision. Tested with 1.8.0.
- Hermes linter: not available. Owner will have Hermes debug.
- Default player: auto-detect refuses to guess with several available players. Owner should set `MASS_DEFAULT_PLAYER`.
- Token exposure: the token was printed once in this session's tool output while checking the file. Recommend revoking it and creating a new one in Settings → Security, then updating `.env`.
