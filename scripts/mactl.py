#!/usr/bin/env python3
"""mactl: command-line control for a Music Assistant (MA) server.

The single integration point for the music-assistant-ha skill. It wraps the
official ``music-assistant-client`` library. Each invocation opens one
connection, runs one command, and disconnects.

Configuration (environment variables):
  MASS_SERVER_URL       Server base URL, e.g. http://<ma-host>:8095 (a trailing /api is accepted)
  MASS_ACCESS_TOKEN     Long-lived access token (MA Settings -> Security). Never printed.
  MASS_DEFAULT_PLAYER   Optional player id. If unset, the player is auto-detected and cached.

Exit codes: 0 success, 1 operation failed, 2 configuration or usage error,
3 connection or authentication failure.

Target Python: 3.14 (developed on 3.14.7). Standard library only, apart from the client.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from music_assistant_client import MusicAssistantClient
from music_assistant_client.exceptions import MusicAssistantClientException
from music_assistant_models.enums import MediaType, PlaybackState, QueueOption, RepeatMode
from music_assistant_models.errors import (
    AuthenticationFailed,
    AuthenticationRequired,
    InvalidToken,
    MusicAssistantError,
)

ENV_URL = "MASS_SERVER_URL"
ENV_TOKEN = "MASS_ACCESS_TOKEN"
ENV_DEFAULT_PLAYER = "MASS_DEFAULT_PLAYER"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2
EXIT_CONNECTION = 3

CONNECT_TIMEOUT_SECONDS = 30.0
SEARCH_MEDIA_TYPES = ("track", "album", "artist", "playlist", "radio")
# Maps a media type to the attribute holding its results in SearchResults.
SEARCH_RESULT_ATTRS = {
    "track": "tracks",
    "album": "albums",
    "artist": "artists",
    "playlist": "playlists",
    "radio": "radio",
}
VOLUME_RANGE = (0, 100)


class ConfigError(Exception):
    """Missing or invalid configuration (exit 2)."""


class ConnectionFailure(Exception):
    """Server unreachable, timed out, or authentication rejected (exit 3)."""


class CommandFailure(Exception):
    """The command could not be carried out (exit 1)."""


@dataclass(frozen=True)
class Config:
    url: str
    token: str
    default_player: str | None


@dataclass(frozen=True)
class Result:
    """Outcome of a command.

    ``confirmation`` marks a plain "done" message. ``--quiet`` hides those and
    keeps data output. Errors are never hidden.
    """

    data: dict[str, Any]
    human: str
    confirmation: bool = False


def normalize_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if url.endswith("/api"):
        url = url[: -len("/api")]
    if not url.startswith(("http://", "https://")):
        raise ConfigError(
            f"{ENV_URL} must start with http:// or https:// (got {url!r}). "
            "Example: http://<ma-host>:8095"
        )
    return url


def load_config(env: Mapping[str, str]) -> Config:
    raw_url = env.get(ENV_URL, "").strip()
    token = env.get(ENV_TOKEN, "").strip()
    missing = [name for name, value in ((ENV_URL, raw_url), (ENV_TOKEN, token)) if not value]
    if missing:
        raise ConfigError(
            "missing required environment variable(s): "
            + ", ".join(missing)
            + ". Set them as described in SKILL.md (token: MA Settings -> Security)."
        )
    default_player = env.get(ENV_DEFAULT_PLAYER, "").strip() or None
    return Config(url=normalize_url(raw_url), token=token, default_player=default_player)


def cache_file_path(env: Mapping[str, str]) -> Path:
    base = env.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "music-assistant-ha" / "player.json"


def read_cached_player(path: Path) -> str | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("player_id")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and value else None


def write_cached_player(path: Path, player_id: str) -> None:
    # Cache is best effort: a failure here must not break the command.
    with suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"player_id": player_id}), encoding="utf-8")


@asynccontextmanager
async def connected(cfg: Config, timeout: float = CONNECT_TIMEOUT_SECONDS) -> AsyncIterator[Any]:
    """Open a client connection and yield it once the initial state is loaded."""
    client = MusicAssistantClient(cfg.url, None, token=cfg.token)
    ready = asyncio.Event()
    listener = asyncio.create_task(client.start_listening(init_ready=ready))
    waiter = asyncio.create_task(ready.wait())
    try:
        done, _ = await asyncio.wait(
            {listener, waiter}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if not ready.is_set():
            if listener in done and listener.exception() is not None:
                raise _map_connect_error(listener.exception())  # type: ignore[arg-type]
            raise ConnectionFailure(
                f"timed out after {timeout:.0f}s connecting to {cfg.url}. "
                "Check MASS_SERVER_URL and that the server is reachable."
            )
        yield client
    finally:
        waiter.cancel()
        listener.cancel()
        with suppress(BaseException):
            await listener
        with suppress(Exception):
            await client.disconnect()


def _map_connect_error(exc: BaseException) -> ConnectionFailure:
    if isinstance(exc, (AuthenticationRequired, AuthenticationFailed, InvalidToken)):
        return ConnectionFailure(
            "authentication failed: MASS_ACCESS_TOKEN was rejected. "
            "Create a new long-lived token in MA Settings -> Security."
        )
    if isinstance(exc, MusicAssistantError):
        return ConnectionFailure(f"server error while connecting: {exc}")
    if isinstance(exc, OSError):
        return ConnectionFailure(
            f"could not reach the server ({type(exc).__name__}). "
            "Check MASS_SERVER_URL and network access."
        )
    return ConnectionFailure(f"unexpected error while connecting: {type(exc).__name__}")


# ---------------------------------------------------------------------------
# Player and queue resolution
# ---------------------------------------------------------------------------


def _find_player(client: Any, player_id: str) -> Any:
    player = client.players.get(player_id)
    if player is None:
        raise CommandFailure(f"no player with id {player_id!r}. Run `players` to list ids.")
    return player


async def resolve_player(
    client: Any, cfg: Config, explicit: str | None, cache_path: Path
) -> str:
    """Return the player id to act on.

    Order: --player flag, then MASS_DEFAULT_PLAYER, then the cached player, then
    auto-detection (one playing player, or the only available player). Auto-detected
    ids are cached so later commands skip detection.
    """
    if explicit:
        return _find_player(client, explicit).player_id
    if cfg.default_player:
        return _find_player(client, cfg.default_player).player_id

    cached = read_cached_player(cache_path)
    if cached:
        player = client.players.get(cached)
        if player is not None and player.available:
            return player.player_id

    available = [p for p in client.players.players if p.available]
    if not available:
        raise CommandFailure("no available players on the server. Run `players` to inspect.")
    playing = [p for p in available if p.playback_state == PlaybackState.PLAYING]
    if len(playing) == 1:
        chosen = playing[0]
    elif len(available) == 1:
        chosen = available[0]
    else:
        raise CommandFailure(
            "several players are available and the target is ambiguous. "
            "Pass --player <id>, or set MASS_DEFAULT_PLAYER. Run `players` to list ids."
        )
    write_cached_player(cache_path, chosen.player_id)
    return chosen.player_id


# ---------------------------------------------------------------------------
# Command handlers. Each takes (client, cfg, args) and returns a Result.
# In this server, a player's queue id is the same as its player id.
# ---------------------------------------------------------------------------

Handler = Callable[[Any, "Context", argparse.Namespace], Awaitable[Result]]


@dataclass(frozen=True)
class Context:
    cfg: Config
    cache_path: Path


def _player_summary(player: Any) -> dict[str, Any]:
    return {
        "player_id": player.player_id,
        "name": player.name,
        "state": str(player.playback_state),
        "available": player.available,
        "volume": player.volume_level,
        "muted": player.volume_muted,
        "powered": player.powered,
        "synced_to": player.synced_to,
        "group_members": list(player.group_members or []),
    }


def _media_summary(item: Any) -> dict[str, Any]:
    artists = getattr(item, "artists", None) or []
    return {
        "name": getattr(item, "name", None),
        "media_type": str(getattr(item, "media_type", "") or ""),
        "uri": getattr(item, "uri", None),
        "artists": [getattr(a, "name", str(a)) for a in artists],
    }


async def _target_player_id(client: Any, ctx: Context, args: argparse.Namespace) -> str:
    return await resolve_player(client, ctx.cfg, getattr(args, "player", None), ctx.cache_path)


async def cmd_players(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    rows = [_player_summary(p) for p in client.players.players]
    lines = [
        f"{r['player_id']}  {r['name']}  {r['state']}  "
        f"vol={r['volume'] if r['volume'] is not None else '-'}"
        f"{'' if r['available'] else '  (unavailable)'}"
        for r in rows
    ]
    return Result({"players": rows}, "\n".join(lines) or "no players")


async def cmd_status(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    pid = await _target_player_id(client, ctx, args)
    player = _find_player(client, pid)
    queue = client.player_queues.get(pid)
    current = getattr(queue, "current_item", None) if queue else None
    now_playing = None
    if current is not None:
        now_playing = {
            "name": current.name,
            "uri": getattr(current.media_item, "uri", None),
        }
    data = {
        "player": _player_summary(player),
        "queue": {
            "queue_id": pid,
            "state": str(queue.state) if queue else None,
            "items": queue.items if queue else 0,
            "shuffle": queue.shuffle_enabled if queue else None,
            "repeat": str(queue.repeat_mode) if queue else None,
            "current_index": queue.current_index if queue else None,
        },
        "now_playing": now_playing,
    }
    human = (
        f"{player.name}: {data['queue']['state'] or player.playback_state}"
        f" | now playing: {now_playing['name'] if now_playing else 'nothing'}"
        f" | vol {player.volume_level} | queue {data['queue']['items']} items"
    )
    return Result(data, human)


def _transport(method_name: str, past: str) -> Handler:
    async def handler(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
        pid = await _target_player_id(client, ctx, args)
        await getattr(client.player_queues, method_name)(pid)
        return Result({"ok": True, "action": past, "player_id": pid}, f"{past} ({pid})", True)

    return handler


async def cmd_volume_set(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    low, high = VOLUME_RANGE
    if not low <= args.level <= high:
        raise CommandFailure(f"volume must be between {low} and {high}, got {args.level}")
    pid = await _target_player_id(client, ctx, args)
    await client.players.volume_set(pid, args.level)
    return Result({"ok": True, "action": "volume_set", "volume": args.level, "player_id": pid},
                  f"volume set to {args.level}", True)


async def cmd_mute(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    muted = args.action == "mute"
    pid = await _target_player_id(client, ctx, args)
    await client.players.volume_mute(pid, muted)
    return Result({"ok": True, "action": args.action, "player_id": pid},
                  "muted" if muted else "unmuted", True)


async def cmd_shuffle(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    enabled = args.state == "on"
    pid = await _target_player_id(client, ctx, args)
    await client.player_queues.shuffle(pid, enabled)
    return Result({"ok": True, "action": "shuffle", "enabled": enabled, "player_id": pid},
                  f"shuffle {'on' if enabled else 'off'}", True)


async def cmd_repeat(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    mode = RepeatMode(args.mode)
    pid = await _target_player_id(client, ctx, args)
    await client.player_queues.repeat(pid, mode)
    return Result({"ok": True, "action": "repeat", "mode": str(mode), "player_id": pid},
                  f"repeat {mode}", True)


async def cmd_queue_list(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    pid = await _target_player_id(client, ctx, args)
    items = await client.player_queues.get_queue_items(pid, limit=args.limit)
    rows = [
        {
            "index": item.sort_index,
            "queue_item_id": item.queue_item_id,
            "name": item.name,
            "uri": getattr(item.media_item, "uri", None),
        }
        for item in items
    ]
    lines = [f"{r['index']:>4}  {r['queue_item_id']}  {r['name']}" for r in rows]
    return Result({"queue_id": pid, "items": rows}, "\n".join(lines) or "queue is empty")


async def cmd_queue_clear(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    pid = await _target_player_id(client, ctx, args)
    await client.player_queues.clear(pid)
    return Result({"ok": True, "action": "queue_clear", "player_id": pid}, "queue cleared", True)


async def cmd_queue_add(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    pid = await _target_player_id(client, ctx, args)
    await client.player_queues.play_media(pid, args.uri, option=QueueOption.ADD)
    return Result({"ok": True, "action": "queue_add", "uri": args.uri, "player_id": pid},
                  "added to queue", True)


async def cmd_queue_remove(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    pid = await _target_player_id(client, ctx, args)
    target: int | str = int(args.item) if args.item.isdigit() else args.item
    await client.player_queues.delete_item(pid, target)
    return Result({"ok": True, "action": "queue_remove", "item": args.item, "player_id": pid},
                  f"removed queue item {args.item}", True)


async def cmd_queue_move(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    pid = await _target_player_id(client, ctx, args)
    await client.player_queues.move_item(pid, args.queue_item_id, pos_shift=args.shift)
    return Result({"ok": True, "action": "queue_move", "queue_item_id": args.queue_item_id,
                   "shift": args.shift, "player_id": pid},
                  f"moved queue item by {args.shift}", True)


async def cmd_queue_play_index(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    pid = await _target_player_id(client, ctx, args)
    await client.player_queues.play_index(pid, args.index)
    return Result({"ok": True, "action": "queue_play_index", "index": args.index, "player_id": pid},
                  f"playing queue index {args.index}", True)


async def cmd_play(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    pid = await _target_player_id(client, ctx, args)
    option = QueueOption(args.option)
    await client.player_queues.play_media(pid, args.uri, option=option)
    return Result({"ok": True, "action": "play", "uri": args.uri, "option": str(option),
                   "player_id": pid}, f"playing {args.uri}", True)


async def cmd_search(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    kinds = args.type or ["track"]
    results = await client.music.search(
        args.query,
        media_types=[MediaType(t) for t in kinds],
        limit=args.limit,
        library_only=args.library_only,
    )
    groups: dict[str, list[dict[str, Any]]] = {}
    for kind in kinds:
        bucket = getattr(results, SEARCH_RESULT_ATTRS[kind], None) or []
        groups[kind] = [_media_summary(item) for item in bucket]
    lines: list[str] = []
    for kind, rows in groups.items():
        lines.append(f"{kind}s:")
        lines.extend(f"  {r['name']}  [{', '.join(r['artists'])}]  {r['uri']}" for r in rows)
    return Result({"results": groups}, "\n".join(lines) or "no results")


async def cmd_search_play(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    results = await client.music.search(
        args.query, media_types=[MediaType(args.type)], limit=1, library_only=args.library_only
    )
    bucket = getattr(results, SEARCH_RESULT_ATTRS[args.type], None) or []
    if not bucket:
        raise CommandFailure(f"no {args.type} found for {args.query!r}")
    top = bucket[0]
    uri = getattr(top, "uri", None)
    if not uri:
        raise CommandFailure("top search result has no uri; cannot play it")
    pid = await _target_player_id(client, ctx, args)
    await client.player_queues.play_media(pid, uri, option=QueueOption.PLAY)
    return Result({"ok": True, "action": "search_play", "uri": uri, "name": top.name,
                   "player_id": pid}, f"playing {top.name}", True)


async def cmd_recent(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    items = await client.music.recently_played(limit=args.limit)
    rows = [_media_summary(i) for i in items]
    lines = [f"{r['name']}  {r['media_type']}  {r['uri']}" for r in rows]
    return Result({"items": rows}, "\n".join(lines) or "nothing played recently")


async def cmd_sync(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    media_types = [MediaType(t) for t in args.media_type] if args.media_type else None
    await client.music.start_sync(media_types=media_types, providers=args.provider or None)
    return Result({"ok": True, "action": "sync"}, "library sync started", True)


async def cmd_favorite_add(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    await client.music.add_item_to_favorites(args.uri)
    return Result({"ok": True, "action": "favorite_add", "uri": args.uri}, "added to favorites", True)


async def cmd_favorite_remove(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    item_id: int | str = int(args.item_id) if args.item_id.isdigit() else args.item_id
    await client.music.remove_item_from_favorites(MediaType(args.media_type), item_id)
    return Result({"ok": True, "action": "favorite_remove", "media_type": args.media_type,
                   "item_id": args.item_id}, "removed from favorites", True)


async def cmd_group(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    _find_player(client, args.player_id)
    _find_player(client, args.target)
    await client.players.group(args.player_id, args.target)
    return Result({"ok": True, "action": "group", "player_id": args.player_id,
                   "target": args.target}, f"grouped {args.player_id} into {args.target}", True)


async def cmd_ungroup(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    _find_player(client, args.player_id)
    await client.players.ungroup(args.player_id)
    return Result({"ok": True, "action": "ungroup", "player_id": args.player_id},
                  f"ungrouped {args.player_id}", True)


async def cmd_transfer(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    _find_player(client, args.source)
    _find_player(client, args.target)
    await client.player_queues.transfer(args.source, args.target, auto_play=args.auto_play)
    return Result({"ok": True, "action": "transfer", "source": args.source,
                   "target": args.target}, f"transferred playback to {args.target}", True)


async def cmd_announce(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    if args.volume is not None and not VOLUME_RANGE[0] <= args.volume <= VOLUME_RANGE[1]:
        raise CommandFailure("announcement volume must be between 0 and 100")
    pid = await _target_player_id(client, ctx, args)
    await client.players.play_announcement(
        pid,
        url=args.url,
        message=args.text,
        volume_level=args.volume,
        tts_engine=args.tts_engine,
        language=args.language,
    )
    return Result({"ok": True, "action": "announce", "player_id": pid,
                   "kind": "url" if args.url else "tts"}, "announcement played", True)


async def cmd_announce_engines(client: Any, ctx: Context, args: argparse.Namespace) -> Result:
    engines = await client.players.tts_engines()
    return Result({"engines": engines}, "\n".join(str(e) for e in engines) or "no TTS engines")


HANDLERS: dict[str, Handler] = {
    "players": cmd_players,
    "status": cmd_status,
    "play-pause": _transport("play_pause", "play_pause"),
    "pause": _transport("pause", "pause"),
    "stop": _transport("stop", "stop"),
    "next": _transport("next", "next"),
    "previous": _transport("previous", "previous"),
    "volume-set": cmd_volume_set,
    "mute": cmd_mute,
    "unmute": cmd_mute,
    "shuffle": cmd_shuffle,
    "repeat": cmd_repeat,
    "queue-list": cmd_queue_list,
    "queue-clear": cmd_queue_clear,
    "queue-add": cmd_queue_add,
    "queue-remove": cmd_queue_remove,
    "queue-move": cmd_queue_move,
    "queue-play-index": cmd_queue_play_index,
    "play": cmd_play,
    "search": cmd_search,
    "search-play": cmd_search_play,
    "recent": cmd_recent,
    "sync": cmd_sync,
    "favorite-add": cmd_favorite_add,
    "favorite-remove": cmd_favorite_remove,
    "group": cmd_group,
    "ungroup": cmd_ungroup,
    "transfer": cmd_transfer,
    "announce": cmd_announce,
    "announce-engines": cmd_announce_engines,
}


# ---------------------------------------------------------------------------
# Argument parsing and entry point
# ---------------------------------------------------------------------------


class _SubParser(argparse.ArgumentParser):
    """Subcommand parser that also accepts the global flags after the subcommand.

    Defaults are suppressed so a flag given before the subcommand is not reset.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                          help="machine-readable JSON output")
        self.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS,
                          help="suppress confirmation text (errors are still shown)")
        self.add_argument("--player", default=argparse.SUPPRESS,
                          help="target player id (overrides MASS_DEFAULT_PLAYER)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mactl",
        description="Control a Music Assistant server. Configure with MASS_SERVER_URL and MASS_ACCESS_TOKEN.",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable JSON output")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress confirmation text (errors are still shown)")
    parser.set_defaults(player=None)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_SubParser)

    sub.add_parser("players", help="list players")
    sub.add_parser("status", help="player and now-playing status")
    for name, help_text in (
        ("play-pause", "toggle play/pause"),
        ("pause", "pause playback"),
        ("stop", "stop playback"),
        ("next", "next track"),
        ("previous", "previous track"),
    ):
        sub.add_parser(name, help=help_text)

    p = sub.add_parser("volume-set", help="set volume 0-100")
    p.add_argument("level", type=int)
    sub.add_parser("mute", help="mute the player").set_defaults(action="mute")
    sub.add_parser("unmute", help="unmute the player").set_defaults(action="unmute")

    p = sub.add_parser("shuffle", help="shuffle on/off")
    p.add_argument("state", choices=["on", "off"])
    p = sub.add_parser("repeat", help="repeat mode")
    p.add_argument("mode", choices=["off", "all", "one"])

    sub.add_parser("queue-list", help="list queue items").add_argument(
        "--limit", type=int, default=50)
    sub.add_parser("queue-clear", help="clear the queue")
    p = sub.add_parser("queue-add", help="add a media URI to the queue without starting playback")
    p.add_argument("uri")
    p = sub.add_parser("queue-remove", help="remove a queue item by index or queue_item_id")
    p.add_argument("item")
    p = sub.add_parser("queue-move", help="move a queue item by a relative shift")
    p.add_argument("queue_item_id")
    p.add_argument("shift", type=int, help="positive moves later, negative moves earlier")
    p = sub.add_parser("queue-play-index", help="play the queue item at an index")
    p.add_argument("index")

    p = sub.add_parser("play", help="play a media URI directly")
    p.add_argument("uri")
    p.add_argument("--option", default="play",
                   choices=["play", "replace", "next", "replace_next", "add"])

    p = sub.add_parser("search", help="search the library and providers")
    p.add_argument("query")
    p.add_argument("--type", action="append", choices=SEARCH_MEDIA_TYPES,
                   help="repeatable; default track")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--library-only", action="store_true")
    p = sub.add_parser("search-play", help="search and play the top result")
    p.add_argument("query")
    p.add_argument("--type", default="track", choices=SEARCH_MEDIA_TYPES)
    p.add_argument("--library-only", action="store_true")

    sub.add_parser("recent", help="recently played items").add_argument("--limit", type=int, default=10)
    p = sub.add_parser("sync", help="start a library sync")
    p.add_argument("--media-type", action="append", choices=SEARCH_MEDIA_TYPES)
    p.add_argument("--provider", action="append", help="provider instance id; repeatable")

    p = sub.add_parser("favorite-add", help="add a media URI to favorites")
    p.add_argument("uri")
    p = sub.add_parser("favorite-remove", help="remove a favorite by media type and library item id")
    p.add_argument("media_type", choices=SEARCH_MEDIA_TYPES)
    p.add_argument("item_id")

    p = sub.add_parser("group", help="join a player to a target player/sync group")
    p.add_argument("player_id")
    p.add_argument("target")
    p = sub.add_parser("ungroup", help="remove a player from its group")
    p.add_argument("player_id")
    p = sub.add_parser("transfer", help="transfer the queue from one player to another")
    p.add_argument("source")
    p.add_argument("target")
    p.add_argument("--auto-play", dest="auto_play", action=argparse.BooleanOptionalAction,
                   default=None, help="start playback on the target (server default if omitted)")

    p = sub.add_parser("announce", help="speak text or play an audio URL as an announcement")
    group_arg = p.add_mutually_exclusive_group(required=True)
    group_arg.add_argument("--text", help="text to speak via TTS")
    group_arg.add_argument("--url", help="audio URL to play")
    p.add_argument("--tts-engine", dest="tts_engine")
    p.add_argument("--language", help="language code, e.g. en-US")
    p.add_argument("--volume", type=int, help="announcement volume 0-100")
    sub.add_parser("announce-engines", help="list available TTS engines")
    return parser


class Printer:
    def __init__(self, *, json_mode: bool, quiet: bool) -> None:
        self.json_mode = json_mode
        self.quiet = quiet

    def result(self, result: Result) -> None:
        if self.json_mode:
            print(json.dumps({"ok": True, **result.data}, default=str))
            return
        if result.confirmation and self.quiet:
            return
        print(result.human)

    def error(self, message: str, code: int) -> None:
        if self.json_mode:
            print(json.dumps({"ok": False, "error": message, "exit_code": code}))
        else:
            print(f"error: {message}", file=sys.stderr)


async def run(args: argparse.Namespace, env: Mapping[str, str]) -> Result:
    cfg = load_config(env)
    ctx = Context(cfg=cfg, cache_path=cache_file_path(env))
    handler = HANDLERS[args.command]
    async with connected(cfg) as client:
        return await handler(client, ctx, args)


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    printer = Printer(json_mode=args.json, quiet=args.quiet)
    environ = os.environ if env is None else env
    try:
        result = asyncio.run(run(args, environ))
    except ConfigError as exc:
        printer.error(str(exc), EXIT_CONFIG)
        return EXIT_CONFIG
    except ConnectionFailure as exc:
        printer.error(str(exc), EXIT_CONNECTION)
        return EXIT_CONNECTION
    except (CommandFailure, MusicAssistantError, MusicAssistantClientException, ValueError) as exc:
        printer.error(str(exc), EXIT_FAILED)
        return EXIT_FAILED
    except KeyboardInterrupt:
        printer.error("interrupted", EXIT_FAILED)
        return EXIT_FAILED
    except Exception as exc:  # last resort: report without a traceback
        printer.error(f"unexpected {type(exc).__name__}: {exc}", EXIT_FAILED)
        return EXIT_FAILED
    printer.result(result)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
