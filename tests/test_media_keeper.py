"""Leaving the desktop on purpose keeps the music playing: MPRIS players that were playing and turn Paused get Play pressed."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mesh_input as mi  # noqa: E402
from test_mesh_input import connect, needs_openssl, running  # noqa: E402

NAMES = "(['org.freedesktop.DBus', ':1.5', 'org.mpris.MediaPlayer2.firefox.instance_1_2', 'org.mpris.MediaPlayer2.spotify', 'org.gnome.Shell'],)"


class Done:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode = stdout, returncode


class FakeBus:
    """A session bus with two media players. `script[name]` is the list of statuses that player reports on successive questions."""

    def __init__(self, script):
        self.script = {name: list(statuses) for name, statuses in script.items()}
        self.played, self.now, self.gdbus = [], 0.0, True

    def which(self, name):
        return "/usr/bin/gdbus" if self.gdbus and name == "gdbus" else None

    def runner(self, cmd, **kwargs):
        assert cmd[:3] == ["gdbus", "call", "--session"]
        args = cmd[3:]
        dest = args[args.index("--dest") + 1]
        method = args[args.index("--method") + 1]
        if method.endswith("ListNames"):
            return Done(NAMES)
        if method.endswith("Properties.Get"):
            queue = self.script[dest]
            status = queue.pop(0) if len(queue) > 1 else queue[0]
            return Done(f"(<'{status}'>,)")
        if method.endswith("Player.Play"):
            self.played.append(dest)
            return Done("()")
        raise AssertionError(cmd)

    def keeper(self):
        return mi.MediaKeeper(runner=self.runner, which=self.which, sleep=self._sleep, clock=lambda: self.now)

    def _sleep(self, seconds):
        self.now += seconds


FIREFOX, SPOTIFY = "org.mpris.MediaPlayer2.firefox.instance_1_2", "org.mpris.MediaPlayer2.spotify"


def test_the_players_on_the_bus_are_found_by_their_mpris_names():
    assert FakeBus({}).keeper().players() == [FIREFOX, SPOTIFY]


def test_only_players_that_are_playing_count():
    bus = FakeBus({FIREFOX: ["Playing"], SPOTIFY: ["Paused"]})
    assert bus.keeper().playing() == [FIREFOX]


def test_a_playing_song_that_turns_paused_is_resumed_once():
    bus = FakeBus({FIREFOX: ["Playing", "Paused", "Paused"], SPOTIFY: ["Paused"]})
    assert bus.keeper().keep_playing(wait=5.0) == [FIREFOX]
    assert bus.played == [FIREFOX]


def test_a_song_that_keeps_playing_is_left_alone():
    bus = FakeBus({FIREFOX: ["Playing"], SPOTIFY: ["Paused"]})
    assert bus.keeper().keep_playing(wait=2.0) == [] and bus.played == []


def test_a_player_that_was_already_paused_by_a_person_is_never_resumed():
    bus = FakeBus({FIREFOX: ["Paused"], SPOTIFY: ["Paused"]})
    assert bus.keeper().keep_playing(wait=2.0) == [] and bus.played == []


def test_nothing_playing_returns_at_once_without_waiting():
    bus = FakeBus({FIREFOX: ["Paused"], SPOTIFY: ["Stopped"]})
    bus.keeper().keep_playing(wait=5.0)
    assert bus.now == 0.0


def test_waiting_stops_when_everything_has_been_resumed():
    bus = FakeBus({FIREFOX: ["Playing", "Paused"], SPOTIFY: ["Playing", "Paused"]})
    assert sorted(bus.keeper().keep_playing(wait=30.0)) == [FIREFOX, SPOTIFY]
    assert bus.now < 5.0


def test_without_gdbus_nothing_happens_and_nothing_breaks():
    bus = FakeBus({FIREFOX: ["Playing"]})
    bus.gdbus = False
    assert bus.keeper().keep_playing(wait=1.0) == [] and bus.played == []


class LeaveKeeper:
    def __init__(self):
        self.calls = []

    def keep_playing(self, wait=5.0, poll=0.4):
        self.calls.append(wait)
        return ["org.mpris.MediaPlayer2.spotify"]


@needs_openssl
def test_a_leave_message_makes_the_agent_keep_the_music_going(tmp_path):
    keeper = LeaveKeeper()

    async def go():
        async with running(tmp_path, media=keeper) as (port, _inj, token, _fp):
            c = await connect(port)
            await c.send(mi.msg_hello(token, "pixel"))
            await c.recv()
            await c.send(mi.msg_leave())
            for _ in range(100):
                if keeper.calls:
                    break
                await asyncio.sleep(0.05)
            await c.send(mi.msg_ping(1))                     # the channel is still fine afterwards
            msg_type, _payload = await c.recv()
            assert msg_type == mi.T_PONG
    asyncio.run(go())
    assert keeper.calls == [5.0]


@needs_openssl
def test_closing_without_leave_does_not_touch_the_music(tmp_path):
    keeper = LeaveKeeper()

    async def go():
        async with running(tmp_path, media=keeper) as (port, _inj, token, _fp):
            c = await connect(port)
            await c.send(mi.msg_hello(token, "pixel"))
            await c.recv()
            await c.send(mi.msg_bye())
            await asyncio.sleep(0.3)
    asyncio.run(go())
    assert keeper.calls == []


@needs_openssl
def test_an_agent_without_the_feature_ignores_a_leave_message(tmp_path):
    async def go():
        async with running(tmp_path) as (port, _inj, token, _fp):
            c = await connect(port)
            await c.send(mi.msg_hello(token, "pixel"))
            await c.recv()
            await c.send(mi.msg_leave())
            await c.send(mi.msg_ping(7))
            msg_type, _payload = await c.recv()
            assert msg_type == mi.T_PONG
    asyncio.run(go())
