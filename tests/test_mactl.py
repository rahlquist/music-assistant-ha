"""Unit tests for scripts/mactl.py using a mocked Music Assistant client.

No network access. Run with:  .venv/bin/python -m pytest -q
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import mactl  # noqa: E402
from music_assistant_models.enums import MediaType, PlaybackState, QueueOption  # noqa: E402

TOKEN = "TEST-TOKEN-do-not-leak-0123456789"
URL = "http://ma.example:8095"


def make_player(pid, name, state=PlaybackState.IDLE, available=True, volume=40):
    return SimpleNamespace(
        player_id=pid,
        name=name,
        playback_state=state,
        available=available,
        volume_level=volume,
        volume_muted=False,
        powered=True,
        synced_to=None,
        group_members=[],
    )


def make_client(players=(), queues=None):
    by_id = {p.player_id: p for p in players}
    client = MagicMock()
    client.players.players = list(players)
    client.players.get.side_effect = lambda pid: by_id.get(pid)
    client.players.volume_set = AsyncMock()
    client.players.volume_mute = AsyncMock()
    client.players.group = AsyncMock()
    client.players.ungroup = AsyncMock()
    client.players.play_announcement = AsyncMock()
    client.players.tts_engines = AsyncMock(return_value=[{"name": "tts.a", "id": "tts.a"}])
    client.player_queues.get.side_effect = lambda qid: (queues or {}).get(qid)
    for name in ("play_pause", "pause", "stop", "next", "previous", "clear", "shuffle",
                 "repeat", "play_media", "delete_item", "move_item", "play_index",
                 "transfer", "get_queue_items"):
        setattr(client.player_queues, name, AsyncMock(return_value=[]))
    client.music.search = AsyncMock()
    client.music.recently_played = AsyncMock(return_value=[])
    client.music.start_sync = AsyncMock()
    client.music.add_item_to_favorites = AsyncMock()
    client.music.remove_item_from_favorites = AsyncMock()
    return client


def ctx(tmp_path, default_player=None):
    cfg = mactl.Config(url=URL, token=TOKEN, default_player=default_player)
    return mactl.Context(cfg=cfg, cache_path=tmp_path / "cache" / "player.json")


def ns(**kwargs):
    base = {"player": None}
    base.update(kwargs)
    return argparse.Namespace(**base)


def run_handler(name, client, context, args):
    return asyncio.run(mactl.HANDLERS[name](client, context, args))


# ---------------------------------------------------------------- config


def test_normalize_url_strips_api_suffix_and_slash():
    assert mactl.normalize_url("http://h:8095/api/") == "http://h:8095"
    assert mactl.normalize_url("  https://h.example/ ") == "https://h.example"
    # pasted web-UI addresses must not poison the API base URL
    assert mactl.normalize_url("http://h:8095/#/settings") == "http://h:8095"
    assert mactl.normalize_url("https://h:8095/api/?x=1#/players") == "https://h:8095"


def test_normalize_url_rejects_bare_host():
    with pytest.raises(mactl.ConfigError, match="http"):
        mactl.normalize_url("192.0.2.10:8095")


def test_load_config_reports_missing_names_not_values():
    with pytest.raises(mactl.ConfigError) as exc:
        mactl.load_config({"MASS_SERVER_URL": URL})
    message = str(exc.value)
    assert "MASS_ACCESS_TOKEN" in message
    assert "MASS_SERVER_URL" not in message.split(":", 1)[1]


def test_load_config_reads_optional_default_player():
    cfg = mactl.load_config({"MASS_SERVER_URL": URL, "MASS_ACCESS_TOKEN": TOKEN,
                             "MASS_DEFAULT_PLAYER": " abc "})
    assert cfg == mactl.Config(url=URL, token=TOKEN, default_player="abc")


# ---------------------------------------------------------------- player resolution


def test_explicit_player_wins(tmp_path):
    client = make_client([make_player("a", "A"), make_player("b", "B")])
    pid = asyncio.run(mactl.resolve_player(client, ctx(tmp_path, "a").cfg, "b", tmp_path / "c.json"))
    assert pid == "b"


def test_unknown_explicit_player_is_a_command_failure(tmp_path):
    client = make_client([make_player("a", "A")])
    with pytest.raises(mactl.CommandFailure, match="no player"):
        asyncio.run(mactl.resolve_player(client, ctx(tmp_path).cfg, "zzz", tmp_path / "c.json"))


def test_configured_default_player_is_used(tmp_path):
    client = make_client([make_player("a", "A"), make_player("b", "B", PlaybackState.PLAYING)])
    pid = asyncio.run(mactl.resolve_player(client, ctx(tmp_path, "a").cfg, None, tmp_path / "c.json"))
    assert pid == "a"


def test_autodetect_picks_single_playing_player_and_caches(tmp_path):
    cache = tmp_path / "c.json"
    client = make_client([make_player("a", "A"), make_player("b", "B", PlaybackState.PLAYING)])
    cfg = ctx(tmp_path).cfg
    assert asyncio.run(mactl.resolve_player(client, cfg, None, cache)) == "b"
    assert json.loads(cache.read_text()) == {"player_id": "b"}
    # The cached id is reused without re-detection.
    other = make_client([make_player("b", "B"), make_player("c", "C")])
    assert asyncio.run(mactl.resolve_player(other, cfg, None, cache)) == "b"


def test_autodetect_ambiguous_asks_for_explicit_choice(tmp_path):
    client = make_client([make_player("a", "A"), make_player("b", "B")])
    with pytest.raises(mactl.CommandFailure, match="ambiguous"):
        asyncio.run(mactl.resolve_player(client, ctx(tmp_path).cfg, None, tmp_path / "c.json"))


def test_autodetect_ignores_unavailable_players(tmp_path):
    client = make_client([make_player("a", "A", available=False), make_player("b", "B")])
    assert asyncio.run(mactl.resolve_player(client, ctx(tmp_path).cfg, None, tmp_path / "c.json")) == "b"


def test_corrupt_cache_is_ignored(tmp_path):
    cache = tmp_path / "c.json"
    cache.write_text("{not json")
    assert mactl.read_cached_player(cache) is None


# ---------------------------------------------------------------- command handlers


def test_transport_calls_queue_method(tmp_path):
    client = make_client([make_player("a", "A")])
    run_handler("pause", client, ctx(tmp_path, "a"), ns())
    client.player_queues.pause.assert_awaited_once_with("a")
    run_handler("play-pause", client, ctx(tmp_path, "a"), ns())
    client.player_queues.play_pause.assert_awaited_once_with("a")


def test_volume_out_of_range_rejected_before_any_call(tmp_path):
    client = make_client([make_player("a", "A")])
    with pytest.raises(mactl.CommandFailure, match="between 0 and 100"):
        run_handler("volume-set", client, ctx(tmp_path, "a"), ns(level=101))
    client.players.volume_set.assert_not_called()


def test_volume_set_valid(tmp_path):
    client = make_client([make_player("a", "A")])
    result = run_handler("volume-set", client, ctx(tmp_path, "a"), ns(level=30))
    client.players.volume_set.assert_awaited_once_with("a", 30)
    assert result.confirmation and result.data["volume"] == 30


def test_mute_and_unmute_use_action(tmp_path):
    client = make_client([make_player("a", "A")])
    run_handler("mute", client, ctx(tmp_path, "a"), ns(action="mute"))
    client.players.volume_mute.assert_awaited_with("a", True)
    run_handler("unmute", client, ctx(tmp_path, "a"), ns(action="unmute"))
    client.players.volume_mute.assert_awaited_with("a", False)


def test_queue_add_uses_add_option_without_playing(tmp_path):
    client = make_client([make_player("a", "A")])
    run_handler("queue-add", client, ctx(tmp_path, "a"), ns(uri="library://track/1"))
    client.player_queues.play_media.assert_awaited_once_with(
        "a", "library://track/1", option=QueueOption.ADD)


def test_queue_remove_numeric_index_is_int(tmp_path):
    client = make_client([make_player("a", "A")])
    run_handler("queue-remove", client, ctx(tmp_path, "a"), ns(item="3"))
    client.player_queues.delete_item.assert_awaited_once_with("a", 3)


def test_queue_remove_string_id_stays_string(tmp_path):
    client = make_client([make_player("a", "A")])
    run_handler("queue-remove", client, ctx(tmp_path, "a"), ns(item="abc-1"))
    client.player_queues.delete_item.assert_awaited_once_with("a", "abc-1")


def test_queue_move_passes_shift(tmp_path):
    client = make_client([make_player("a", "A")])
    run_handler("queue-move", client, ctx(tmp_path, "a"), ns(queue_item_id="q9", shift=-2))
    client.player_queues.move_item.assert_awaited_once_with("a", "q9", pos_shift=-2)


def test_play_default_option_is_play(tmp_path):
    client = make_client([make_player("a", "A")])
    run_handler("play", client, ctx(tmp_path, "a"), ns(uri="u://x", option="play"))
    client.player_queues.play_media.assert_awaited_once_with("a", "u://x", option=QueueOption.PLAY)


def test_search_defaults_to_tracks_and_groups_results(tmp_path):
    track = SimpleNamespace(name="Blue", uri="lib://track/1", media_type="track", artists=[])
    client = make_client([])
    client.music.search.return_value = SimpleNamespace(
        tracks=[track], albums=[], artists=[], playlists=[], radio=[])
    result = run_handler("search", client, ctx(tmp_path), ns(query="blue", type=None, limit=5,
                                                              library_only=False))
    client.music.search.assert_awaited_once()
    assert client.music.search.await_args.kwargs["media_types"] == [MediaType.TRACK]
    assert result.data["results"]["track"][0]["uri"] == "lib://track/1"


def test_search_play_plays_top_result(tmp_path):
    top = SimpleNamespace(name="Blue", uri="lib://track/1")
    client = make_client([make_player("a", "A")])
    client.music.search.return_value = SimpleNamespace(tracks=[top], albums=[], artists=[],
                                                       playlists=[], radio=[])
    run_handler("search-play", client, ctx(tmp_path, "a"), ns(query="blue", type="track",
                                                              library_only=False))
    client.player_queues.play_media.assert_awaited_once_with("a", "lib://track/1",
                                                             option=QueueOption.PLAY)


def test_search_play_no_results_fails_without_playing(tmp_path):
    client = make_client([make_player("a", "A")])
    client.music.search.return_value = SimpleNamespace(tracks=[], albums=[], artists=[],
                                                       playlists=[], radio=[])
    with pytest.raises(mactl.CommandFailure, match="no track"):
        run_handler("search-play", client, ctx(tmp_path, "a"), ns(query="zzz", type="track",
                                                                  library_only=False))
    client.player_queues.play_media.assert_not_called()


def test_announce_text_sends_message_not_url(tmp_path):
    client = make_client([make_player("a", "A")])
    run_handler("announce", client, ctx(tmp_path, "a"),
                ns(text="dinner is ready", url=None, tts_engine=None, language="en-US", volume=60))
    kwargs = client.players.play_announcement.await_args.kwargs
    assert kwargs["message"] == "dinner is ready"
    assert kwargs["url"] is None
    assert kwargs["volume_level"] == 60
    assert kwargs["language"] == "en-US"


def test_announce_url_path(tmp_path):
    client = make_client([make_player("a", "A")])
    run_handler("announce", client, ctx(tmp_path, "a"),
                ns(text=None, url="https://x/y.mp3", tts_engine=None, language=None, volume=None))
    kwargs = client.players.play_announcement.await_args.kwargs
    assert kwargs["url"] == "https://x/y.mp3"
    assert kwargs["message"] is None


def test_group_and_transfer_validate_both_players(tmp_path):
    client = make_client([make_player("a", "A")])
    with pytest.raises(mactl.CommandFailure):
        run_handler("group", client, ctx(tmp_path), ns(player_id="a", target="missing"))
    client.players.group.assert_not_called()
    with pytest.raises(mactl.CommandFailure):
        run_handler("transfer", client, ctx(tmp_path), ns(source="a", target="missing",
                                                          auto_play=None))
    client.player_queues.transfer.assert_not_called()


def test_favorite_remove_uses_media_type_enum(tmp_path):
    client = make_client([])
    run_handler("favorite-remove", client, ctx(tmp_path), ns(media_type="track", item_id="77"))
    client.music.remove_item_from_favorites.assert_awaited_once_with(MediaType.TRACK, 77)


def test_status_handles_missing_queue(tmp_path):
    client = make_client([make_player("a", "A", PlaybackState.PLAYING)])
    result = run_handler("status", client, ctx(tmp_path, "a"), ns())
    assert result.data["now_playing"] is None
    assert result.data["queue"]["items"] == 0


# ---------------------------------------------------------------- main() and output modes


class _FakeConnect:
    def __init__(self, client):
        self.client = client

    def __call__(self, cfg, timeout=mactl.CONNECT_TIMEOUT_SECONDS):
        @asynccontextmanager
        async def cm():
            yield self.client

        return cm()


def _run_main(monkeypatch, capsys, argv, env, client=None, connect=None):
    if connect is None:
        connect = _FakeConnect(client or make_client([make_player("a", "A")]))
    monkeypatch.setattr(mactl, "connected", connect)
    code = mactl.main(argv, env)
    out, err = capsys.readouterr()
    return code, out, err


GOOD_ENV = {"MASS_SERVER_URL": URL, "MASS_ACCESS_TOKEN": TOKEN, "MASS_DEFAULT_PLAYER": "a",
            "XDG_CACHE_HOME": "/nonexistent-cache-for-tests"}


def test_main_missing_config_exits_2_without_connecting(monkeypatch, capsys):
    called = []

    def boom(cfg, timeout=0):
        called.append(cfg)
        raise AssertionError("must not connect")

    code, out, err = _run_main(monkeypatch, capsys, ["status"], {}, connect=boom)
    assert code == mactl.EXIT_CONFIG
    assert "MASS_ACCESS_TOKEN" in err and TOKEN not in err
    assert called == []


def test_main_connection_failure_exits_3(monkeypatch, capsys):
    @asynccontextmanager
    async def fail(cfg, timeout=0):
        raise mactl.ConnectionFailure("timed out")
        yield  # pragma: no cover

    code, out, err = _run_main(monkeypatch, capsys, ["status"], GOOD_ENV, connect=lambda c, timeout=0: fail(c))
    assert code == mactl.EXIT_CONNECTION
    assert "timed out" in err


def test_main_json_success(monkeypatch, capsys):
    client = make_client([make_player("a", "A", PlaybackState.PLAYING)])
    code, out, err = _run_main(monkeypatch, capsys, ["--json", "players"], GOOD_ENV, client=client)
    assert code == mactl.EXIT_OK
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["players"][0]["player_id"] == "a"


def test_main_json_flag_accepted_after_subcommand(monkeypatch, capsys):
    client = make_client([make_player("a", "A")])
    code, out, _ = _run_main(monkeypatch, capsys, ["players", "--json"], GOOD_ENV, client=client)
    assert code == mactl.EXIT_OK
    assert json.loads(out)["ok"] is True


def test_main_quiet_hides_confirmation_but_not_errors(monkeypatch, capsys):
    client = make_client([make_player("a", "A")])
    code, out, err = _run_main(monkeypatch, capsys, ["--quiet", "volume-set", "50"], GOOD_ENV,
                               client=client)
    assert code == mactl.EXIT_OK
    assert out == ""

    code, out, err = _run_main(monkeypatch, capsys, ["--quiet", "volume-set", "500"], GOOD_ENV,
                               client=client)
    assert code == mactl.EXIT_FAILED
    assert "between 0 and 100" in err


def test_main_json_error_goes_to_stdout_with_exit_code(monkeypatch, capsys):
    client = make_client([make_player("a", "A")])
    code, out, _ = _run_main(monkeypatch, capsys, ["--json", "volume-set", "999"], GOOD_ENV,
                             client=client)
    payload = json.loads(out)
    assert code == mactl.EXIT_FAILED
    assert payload["ok"] is False and payload["exit_code"] == 1


def test_token_never_appears_in_output(monkeypatch, capsys):
    client = make_client([make_player("a", "A")])
    for argv in (["players"], ["--json", "status"], ["volume-set", "999"]):
        _, out, err = _run_main(monkeypatch, capsys, argv, GOOD_ENV, client=client)
        assert TOKEN not in out and TOKEN not in err


def test_parser_accepts_every_documented_command():
    parser = mactl.build_parser()
    samples = {
        "players": [], "status": [], "play-pause": [], "pause": [], "stop": [], "next": [],
        "previous": [], "volume-set": ["40"], "mute": [], "unmute": [], "shuffle": ["on"],
        "repeat": ["all"], "queue-list": [], "queue-clear": [], "queue-add": ["u://a"],
        "queue-remove": ["2"], "queue-move": ["q1", "1"], "queue-play-index": ["0"],
        "play": ["u://a"], "search": ["q"], "search-play": ["q"], "recent": [], "sync": [],
        "favorite-add": ["u://a"], "favorite-remove": ["track", "1"], "group": ["a", "b"],
        "ungroup": ["a"], "transfer": ["a", "b"], "announce": ["--text", "hi"],
        "announce-engines": [],
    }
    assert set(samples) == set(mactl.HANDLERS)
    for command, extra in samples.items():
        args = parser.parse_args([command, *extra])
        assert args.command == command
