# Phase 1 Findings: music-assistant-ha

Status: research only. No CLI written. Pending review by the project owner.

## Environment

| Check | Result |
|---|---|
| Python | 3.14.7 (spec and handoff target 3.14.9). Not installed or changed; flagged for owner decision. |
| MA server reachable | VERIFIED. `GET /api-docs` returns 200. |
| MA server version | VERIFIED. `GET /info`: server_version 2.10.9, schema_version 65, min_supported_schema 28, status running, onboard_done true. |
| Token | NOT VERIFIED. Reading `~/.hermes/.env` was blocked by the permission system during this session, so no authenticated call was made. See open questions. |

## Section 3.2 items

### 1. Enumerate the API surface (players, queues, library search, favorites, groups, announcements, sync)
**Status: PARTIAL / UNVERIFIED.**
- VERIFIED: the server's OpenAPI 3.0 spec (`/api-docs/openapi.json`, API 2.10.9) exposes one command endpoint, `POST /api` with body `{"command": "<name>", "args": {...}}` and bearer auth. Other paths: `/auth/login`, `/auth/providers`, `/setup`, `/info`.
- VERIFIED from the spec examples only: `players/all`, `players/cmd/play`.
- UNVERIFIED: the full command list. The spec defers to a "Commands Reference" page, which returned 404 when fetched. Command names for queues, search, favorites, groups, sync, and announcements must come from server source or a live call.

### 2. Transport per capability (REST JSON-RPC vs WebSocket) and exact client call
**Status: PARTIAL.**
- VERIFIED: `POST /api` exists in the 2.10.9 spec and is documented as the command endpoint.
- VERIFIED: the server source has a WebSocket endpoint (`/ws`) implemented in `music_assistant/controllers/webserver/websocket_client.py` and routes registered in `controller.py`. The official client is built on this.
- UNVERIFIED: which transport is reliable for search and play-by-URI on 2.10.9. The spec's reliability requirement needs a live test with a token.
- Client method names for player control, queues, and library were NOT found in the client README. The README names the controllers `client.players`, `client.player_queues`, `client.music`, and `client.auth`, but no method names.

### 3. Announcement semantics
**Status: VERIFIED (docs), parameters UNVERIFIED.**
Source: music-assistant.io announcements page.
- Interrupt and restore: active playback is paused and restored afterward. Players without native support are stopped, volume is adjusted, the announcement plays, and the previous state is restored.
- Power: players are powered on before the announcement and powered off afterward if they were off before.
- Volume: announcement volume is set per player. Groups only have a pre-announcement sound setting.
- Native overlay: Sonos S2 and ESPHome-based Sendspin devices duck the music instead of pausing it.
- TTS vs audio file: the TTS engine is one server-wide setting (Settings → System → Players). The docs do not say how a per-call audio URL is chosen versus TTS.
- Groups: native Cast/Sync/Universal groups play to all children, and children already playing are not resumed. Sync groups are treated as one target.
- UNVERIFIED: per-call parameter names and the exact command for announcements.

### 4. Grouping, transfer, favorites semantics
**Status: UNVERIFIED.** The source and docs needed for this were not retrieved in this session.

### 5. Client authentication and connection lifecycle
**Status: VERIFIED (client README).**
- Package: `music-assistant-client` (PyPI). Version not stated on the page.
- Construction: `MusicAssistantClient(server_url, aiohttp_session=None, token=None, ssl_context=None)`, used as an async context manager.
- Lifecycle: `await client.start_listening()`, then `client.subscribe(callback)` for events. No explicit `connect()` or `disconnect()` is documented; the context manager handles the connection.
- Auth: schema 28 and later require a token. Recommended path: `login_with_token(server_url, username, password, token_name=...)`. Errors: `AuthenticationRequired`, `AuthenticationFailed`, `LoginFailed` from `music_assistant_models.errors`.
- Min server version and Python version for the client: not stated.

### 6. Verify the connection details against the live server
**Status: PARTIAL.**
- VERIFIED: base URL `http://192.168.8.36:8095` is reachable. `/api-docs` and `/info` respond.
- VERIFIED: the `/api` path exists in the spec.
- NOT VERIFIED: authenticated `/api` calls (`players/all` etc.). The token could not be read this session.

## Transport decision (provisional)
- Preferred: the official client (`music-assistant-client`) over WebSocket, per the MA maintainers' guidance (discussion 3802, cited in the design doc) and because the client handles auth and lifecycle.
- Fallback: `POST /api` JSON-RPC for operations the client does not expose.
- Final decision requires one live authenticated call to compare search and play-by-URI on both transports. This is blocked until the token is available.

## Clean-room attestation
Sources consulted:
1. Live MA server at `http://192.168.8.36:8095`: `/api-docs`, `/api-docs/openapi.json`, `/info`.
2. `https://www.music-assistant.io/integration/announcements`
3. `https://github.com/music-assistant/client` (README)
4. `https://github.com/music-assistant/server/tree/dev/music_assistant/controllers/webserver` (file listing)
5. `https://raw.githubusercontent.com/music-assistant/client/main/README.md`

Not consulted:
- No Music Assistant skill, community integration, or skill registry or hub was searched, cloned, or read.
- `https://www.music-assistant.io/api/commands` returned 404 and was not used.

## Open questions for the owner
1. **Token:** reading `~/.hermes/.env` was blocked by the permission system during this run. Should the token be supplied another way (for example, an env var passed to the session, or a permission rule that allows the read), or should live auth checks wait?
2. **Python:** the machine has 3.14.7; the spec says 3.14.9. Proceed with 3.14.7, or install 3.14.9 first?
3. **Project path:** the spec says `~/music-assistant-ha`; the handoff says `/home/rahlquist/projects/music-assistant-ha`. This repo uses the handoff path. Confirm.
4. **Commands reference:** the spec's command list is not in the repo. Can you share the MA Commands Reference page, or approve authenticated discovery against `/api` once the token is available?
5. **Client version:** pin `music-assistant-client` to a version matching server 2.10.9?
6. **Hermes linter:** is it available on this machine? Not checked yet.
