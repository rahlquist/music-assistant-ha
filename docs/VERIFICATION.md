# Verification table: music-assistant-ha

Environment: Python 3.14.7 (project venv), `music-assistant-client` 1.8.0, Music Assistant server 2.10.9 (schema 65).

Legend:
- **Unit**: covered by `tests/test_mactl.py` with a mocked client.
- **Live (read-only)**: run against the real server, no state change.
- **Not live-tested**: would change playback, volume, queue, group, or favorites on real devices. Not run without your go-ahead.

| Capability | CLI command(s) | Unit | Live (read-only) | Notes |
|---|---|---|---|---|
| Auth and connection | all | yes | yes | Valid token connects. Bad token exits 3 with message. Unreachable host exits 3 after 30s. |
| Config validation | all | yes | n/a | Missing vars exit 2 without connecting. Token never printed. |
| Player listing | `players` | yes | **yes** | 5 players returned on the test server. |
| Player resolution | `--player`, `MASS_DEFAULT_PLAYER`, cache, auto-detect | yes | partial | With several available players, auto-detect correctly refuses to guess. |
| Status / now playing | `status` | yes | **yes** (explicit `--player`) | |
| Play/pause, pause, stop, next, previous | `play-pause` … `previous` | yes | not live-tested | Maps to `player_queues` methods. |
| Volume set | `volume-set` | yes (incl. range check) | not live-tested | Range 0–100 enforced before any call. |
| Mute / unmute | `mute`, `unmute` | yes | not live-tested | |
| Shuffle, repeat | `shuffle`, `repeat` | no | not live-tested | Enum values checked against client: `off`/`one`/`all`. |
| Queue list | `queue-list` | no | **yes** | Index is `sort_index`. Live fix: earlier `index` field was always 0. |
| Queue clear / add / remove / move / play-index | `queue-*` | yes (args) | not live-tested | Remove accepts index or queue_item_id. |
| Play URI | `play` | yes | not live-tested | `QueueOption` values checked against client enum. |
| Search | `search` | yes (grouping) | **yes** | Track search returns results from multiple providers. |
| Search and play top result | `search-play` | yes | not live-tested | Refuses with no playback when there are no results. |
| Recently played | `recent` | no | **yes** | |
| Library sync | `sync` | no | not live-tested | Starts a background job on the server. |
| Favorites add / remove | `favorite-add`, `favorite-remove` | yes (enum) | not live-tested | |
| Group / ungroup | `group`, `ungroup` | yes (validation) | not live-tested | Both player ids validated first. |
| Transfer queue | `transfer` | yes (validation) | not live-tested | |
| Announcement (TTS or URL) | `announce` | yes (kwargs mapping) | not live-tested | Client maps to `players/cmd/play_announcement`. Server docs: interrupts and restores playback. |
| TTS engine list | `announce-engines` | no | **yes** (error path) | Needs API schema 101. This server is schema 65, so it reports a clear "update the server" message. |
| JSON and quiet output modes | `--json`, `--quiet` | yes | partial | Errors are still shown in quiet mode. |
| Exit codes | 0 / 1 / 2 / 3 | yes | yes (3 for bad token and unreachable) | |

## Python 3.14.7 vs 3.14.9

The code is written against the 3.14 standard library and uses no features newer than 3.14.0.
The 3.14.8 and 3.14.9 release notes list no language-level incompatible changes. The items noted
are the bundled pip version, the installer, PGP signature removal, and a macOS Tk build change.
None of those affect this code. The code also avoids `return`, `break`, or `continue` inside
`finally` blocks (PEP 765, already in 3.14.0). Re-run `pytest` on 3.14.9 before relying on it.

## Not verified

- Any audible behavior: announcement interruption and restoration, grouping, and transfer.
- Queue commands with real content on a real player.
- Hermes skill linter: not available on this machine.
