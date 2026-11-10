# music-assistant-ha

A Hermes Agent skill that controls a [Music Assistant](https://www.music-assistant.io/) server:
playback, volume, queues, library search, favorites, multi-room grouping and transfer, and announcements.

The integration point is `scripts/mactl.py`, a CLI that wraps the official
[`music-assistant-client`](https://pypi.org/project/music-assistant-client/) library.
`SKILL.md` contains the agent-facing instructions and command reference.

## Requirements

- Python 3.14 (developed and tested on 3.14.7). Standard library plus the client library only.
- A Music Assistant server with API schema 46 or newer. Tested against server 2.10.9 (schema 65).
- A long-lived access token from **Settings → Security** in MA.

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`requirements.txt` is intentionally unpinned. Tested with `music-assistant-client` 1.8.0.

## Configure

| Variable | Required | Purpose |
|---|---|---|
| `MASS_SERVER_URL` | yes | Server base URL, e.g. `http://<ma-host>:8095`. A trailing `/api` is accepted. |
| `MASS_ACCESS_TOKEN` | yes | Long-lived token. Never printed or logged. |
| `MASS_DEFAULT_PLAYER` | no | Player id to use when `--player` is not given. If unset, the player is auto-detected and cached. |

Find player ids with `mactl players`. Do not hardcode them.

## Use

```sh
.venv/bin/python scripts/mactl.py players
.venv/bin/python scripts/mactl.py search "Blue in Green" --type track
.venv/bin/python scripts/mactl.py search-play "Miles Davis" --type artist
.venv/bin/python scripts/mactl.py --json status
```

Add `--json` for machine-readable output and `--quiet` to hide confirmations. Errors are always shown.
Exit codes: `0` success, `1` operation failed, `2` configuration error, `3` connection or auth failure.

Full command list: `.venv/bin/python scripts/mactl.py --help`, or see `SKILL.md`.

## Tests

```sh
.venv/bin/python -m pytest -q                      # unit tests, mocked client, no network
MASS_LIVE_TESTS=1 MASS_SERVER_URL=... MASS_ACCESS_TOKEN=... \
  .venv/bin/python tests/live_readonly_check.py    # read-only checks against a real server
```

The live check makes no changes to playback, volume, queues, groups, or favorites.
`docs/VERIFICATION.md` lists what has been verified and how.

## Layout

```
SKILL.md                  agent instructions (Hermes skill definition)
scripts/mactl.py          CLI: the single integration point
tests/test_mactl.py       unit tests (mocked client)
tests/live_readonly_check.py  read-only live checks
docs/FINDINGS.md          research notes and clean-room attestation
docs/VERIFICATION.md      per-capability verification table
requirements.txt          client library dependency
LICENSE                   MIT
```

## Security notes

- The token is read only from `MASS_ACCESS_TOKEN`. Keep it out of the repository. `.gitignore`
  excludes `.env` and token files.
- The CLI talks only to the configured MA server. It makes no other network calls.
- Use an `https://` URL if the server is reachable over a network you do not fully trust.

## License

MIT. See `LICENSE`.
