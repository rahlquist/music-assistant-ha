# Verification table: music-assistant-ha

Environment: Python 3.14.7 (project venv on yogaman) and 3.14.9 (venv on hermesvm01), `music-assistant-client` 1.8.0, Music Assistant server 2.10.9 (schema 65).

Legend:
- **Unit**: covered by `tests/test_mactl.py` with a mocked client.
- **Live (read-only)**: run against the real server, no state change.
- **Live (state)**: state-changing commands executed against a real player with owner consent, 2026-11-10, target Apollo CAST-1 (`up020e194d`), non-destructive plan with per-step restore.

| Capability | CLI command(s) | Unit | Live | Notes |
|---|---|---|---|---|
| Auth and connection | all | yes | read-only: yes | Valid token connects. Bad token exits 3. Unreachable exits 3 after 30 s. Re-verified 2026-11-10 with the temporary token. |
| Config validation | all | yes | n/a | Missing vars exit 2 without connecting. Token never printed. Web-UI URL fragments now stripped (live bug fix, +2 unit tests). |
| Player listing | `players` | yes | read-only: yes | 5 players on the test server; availability is dynamic (Apollo flipped offline->online mid-session). |
| Player resolution | `--player`, `MASS_DEFAULT_PLAYER`, cache, auto-detect | yes | yes | Ambiguous set refuses to guess (exit 1). `MASS_DEFAULT_PLAYER=up020e194d` set; `status` with no flag resolves Apollo. |
| Status / now playing | `status` | yes | yes | `--json` exposes volume/muted/shuffle/repeat/current_index — enough to assert restores. |
| Play/pause, stop, next, previous | `play-pause` `stop` `next` `previous` | yes | state: yes | All four verified live. Pause state-reporting lags on the CAST device (~60 s), accepted by owner as device behavior. `stop` does NOT clear the queue on this server. |
| Volume set | `volume-set` | yes | state: yes | 50->30->50, each step asserted. |
| Mute / unmute | `mute`, `unmute` | yes | state: yes | Asserted on/off, baseline restored. |
| Shuffle, repeat | `shuffle`, `repeat` | yes | state: yes | on->assert->off; all->assert->off. Baseline restored. |
| Queue list | `queue-list` | yes | state: yes | Indexes/ids usable; added item located by queue_item_id. |
| Queue add / remove / move | `queue-add` `queue-remove` `queue-move` | yes | state: yes | Added `library://track/661` (205->206), moved by id, removed (205). |
| Queue clear / play-index | `queue-clear` `queue-play-index` | yes | state: partial | `queue-clear` verified (during rebuild, after the original queue was lost). `queue-play-index` not fired. |
| Play URI | `play` | yes | state: yes | Replace-mode `play` verified live (queue=1, playing). |
| Search | `search` | yes | read-only: yes | Multi-provider results; `--json` returns uri + media_type. |
| Search and play | `search-play` | yes | state: yes | Verified end-to-end (replaced queue, started top result). |
| Recently played | `recent` | yes | read-only: yes | Server keeps a rolling play history (~60 entries). |
| Library sync | `sync` | yes | state: yes | Accepted as background job. |
| Favorites | `favorite-add` `favorite-remove` | yes | state: yes | Roundtrip on `library://track/661` accepted. |
| Group / ungroup | `group` `ungroup` | yes | state: yes (device limit) | Accepted (rc 0) but never applied within 40 s polling on Apollo/SHIELD — these endpoints don't support MA-side grouping. Provider limitation, not a CLI bug. |
| Transfer queue | `transfer` | yes | state: yes — **incident** | Transferring the EMPTY SHIELD queue to Apollo **cleared Apollo's 205-item queue**. MA transfer replaces the target queue with the source; an empty source is destructive, not a no-op. Server offers no queue undo (checked live api-docs: no history/snapshot/restore command). Warning now documented in SKILL.md. |
| Announcement | `announce` | yes | state: yes | `--text` with `--volume 10` accepted on schema 65 (handoff item 5 closed: no schema-101 gate on the command itself). Audio path accepted; human audibility confirmation pending. |
| TTS engine list | `announce-engines` | no | read-only: yes | Needs schema 101; server is 65; clean refusal, exit 1. |
| JSON / quiet / exit codes | `--json` `--quiet` 0/1/2/3 | yes | yes | Ambiguous-player exits 1 (operation-failed class), pinned by tests; left as-is per owner. |

## Suite result, 2026-11-10

Suite 1 (conservative, 205-item queue present): 18/22 — see rows above; transfer incident documented.

Suite 2 (owner-approved destructive round, after the queue loss): **7/8 passed**.
- `play URI` replace-mode: verified (queue=1, playing). `stop`: verified (does NOT clear the queue on this server).
- `search-play`: verified end-to-end (replaced queue, started top result).
- `announce --url`: verified end-to-end with self-hosted 3 s clip — accepted, interrupted playback, and **restored** it (same track, same index). Owner heard the earlier `--text` announcement; audibility confirmed by human.
- `queue-clear`: verified as side effect during rebuild.
- `group`/`ungroup`: **accepted (rc 0) but never applied** — 40 s of polling shows empty `group_members`/`synced_to` on both endpoints. Provider/device limitation of these players, not a CLI bug.
- Owner confirmed: CAST pause state-lag from suite 1 is expected device behavior; closed.

Queue rebuild: original 205-item queue was a **Spotify-side radio queue** (all item URIs were `spotify--…://track/…`, built in the Spotify app) — it cannot be reconstructed via MA (no history endpoint). Best-effort standee loaded: "Today's Hard Rock Radio" (`library://playlist/68`) = 181 items, idle. Exact-original recovery: re-play the radio on the device from Spotify.

## Python 3.14.7 vs 3.14.9

The code is written against the 3.14 standard library and uses no features newer than 3.14.0.
The 3.14.8 and 3.14.9 release notes list no language-level incompatible changes. The items noted
are the bundled pip version, the installer, PGP signature removal, and a macOS Tk build change.
None of those affect this code. The code also avoids `return`, `break`, or `continue` inside
`finally` blocks (PEP 765, already in 3.14.0).

**3.14.9: verified 2026-11-10.** All 36 unit tests pass on Python 3.14.9 with
`music-assistant-client` 1.8.0 (hermesvm01 venv `~/.venvs/ma-ha`), same as on 3.14.7.

## Hermes lint and load (verified 2026-11-10)

- Skill linter (`tools/skill_linter.py`): ran clean. Fixed from its findings:
  description trimmed to 59 chars (repo hardline is 60; the longer design-spec text moved into
  `## When to Use`, added to satisfy the linter's trigger-section rule). Accepted advisory
  warnings: `README.md` and `.gitignore` flagged as scaffolding, but the design spec mandates
  them at the repo root, which doubles as the skill directory.
- `MASS_DEFAULT_PLAYER` must carry `optional: true` in the frontmatter. `required_for: optional`
  is display-only: Hermes readiness (`tools/skills_tool_setup.py`) treats any entry without the
  `optional` key as required and would block setup on it. Verified via `skill_view`: loaded
  `available`, optional entry never listed missing.
- Live bug found during load testing: a pasted web-UI URL (`http://host:8095/#/settings`) was
  used verbatim and broke the WebSocket connect. `normalize_url` now strips query and fragment
  (plus trailing `/api` and slashes), with unit tests. The bad value still lives in Bitwarden SM;
  the client now tolerates it, but the stored secret should be cleaned at token rotation.

## Not verified

- `queue-play-index` (not fired).
- `group`/`ungroup` functional effect (commands pass; these devices don't group — other player types untested).
- `announce-engines` happy path (needs server at API schema 101).
