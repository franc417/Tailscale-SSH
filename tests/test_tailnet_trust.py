"""Trust through Tailscale: a phone signed in to the same Tailscale account as this computer needs no pairing token."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mesh_input as mi  # noqa: E402
from test_mesh_input import connect, needs_openssl, running  # noqa: E402

ME = 1234
STATUS = {"BackendState": "Running", "Self": {"UserID": ME}, "User": {str(ME): {"LoginName": "franc@example.com"}}}


def whois(user=ME, name="pixel-7.tail1234.ts.net.", tags=None, login="franc@example.com"):
    return {"Node": {"Name": name, "Tags": tags, "User": user}, "UserProfile": {"ID": user, "LoginName": login}}


class Done:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode = stdout, returncode


class FakeTailscale:
    def __init__(self, status=STATUS, answers=None, installed=True):
        self.status, self.answers, self.installed, self.calls = status, answers or {}, installed, []

    def which(self, name):
        return "/usr/bin/tailscale" if self.installed and name == "tailscale" else None

    def runner(self, cmd, **kwargs):
        self.calls.append(cmd[1:])
        if cmd[1] == "status":
            return Done(json.dumps(self.status)) if self.status is not None else Done("", 1)
        if cmd[1] == "whois":
            answer = self.answers.get(cmd[-1])
            return Done(json.dumps(answer)) if answer is not None else Done("", 1)
        raise AssertionError(cmd)

    def trust(self, **kwargs):
        return mi.TailnetTrust(runner=self.runner, which=self.which, **kwargs)


# ------------------------------------------------------------------ addresses and names

def test_only_tailscale_addresses_count():
    assert mi.is_tailnet_address("100.64.0.1") and mi.is_tailnet_address("100.67.163.21") and mi.is_tailnet_address("100.127.255.254")
    assert mi.is_tailnet_address("fd7a:115c:a1e0::1") and mi.is_tailnet_address("::ffff:100.67.163.21")
    for other in ("192.168.100.4", "10.0.0.2", "127.0.0.1", "100.128.0.1", "100.63.255.255", "8.8.8.8", "not an address", ""):
        assert not mi.is_tailnet_address(other), other


def test_a_device_name_loses_its_tailnet_suffix():
    assert mi.short_device_name("pixel-7.tail1234.ts.net.") == "pixel-7" and mi.short_device_name("pixel") == "pixel" and mi.short_device_name("") == ""


# ------------------------------------------------------------------ the decision

def test_a_device_of_the_same_account_is_trusted():
    ts = FakeTailscale(answers={"100.64.0.9": whois()})
    trusted, device, why = ts.trust().check("100.64.0.9")
    assert trusted and device == "pixel-7" and "franc@example.com" in why


def test_a_device_of_another_account_is_not_trusted():
    ts = FakeTailscale(answers={"100.64.0.9": whois(user=999, login="friend@example.com")})
    trusted, device, why = ts.trust().check("100.64.0.9")
    assert not trusted and device == "pixel-7" and "friend@example.com" in why


def test_a_tagged_device_such_as_a_server_is_not_trusted_even_with_the_same_owner_id():
    ts = FakeTailscale(answers={"100.64.0.9": whois(tags=["tag:server"])})
    trusted, _device, why = ts.trust().check("100.64.0.9")
    assert not trusted and "tagged" in why


def test_an_address_from_the_local_network_is_never_asked_about_and_never_trusted():
    ts = FakeTailscale(answers={"192.168.100.4": whois()})
    trusted, _d, why = ts.trust().check("192.168.100.4")
    assert not trusted and "not a Tailscale address" in why and ts.calls == []


def test_without_tailscale_nobody_is_trusted_and_the_reason_says_so():
    for fake in (FakeTailscale(installed=False), FakeTailscale(status=None), FakeTailscale(status={"BackendState": "Stopped"})):
        trusted, _d, why = fake.trust().check("100.64.0.9")
        assert not trusted and "not running" in why


def test_an_address_tailscale_does_not_know_is_not_trusted():
    trusted, _d, why = FakeTailscale().trust().check("100.64.0.9")
    assert not trusted and "does not know" in why


def test_answers_are_cached_for_a_short_while_and_asked_again_afterwards():
    ts = FakeTailscale(answers={"100.64.0.9": whois()})
    now = [0.0]
    trust = ts.trust(clock=lambda: now[0], ttl=30.0)
    trust.check("100.64.0.9")
    trust.check("100.64.0.9")
    assert [c[0] for c in ts.calls].count("whois") == 1
    now[0] = 31.0
    trust.check("100.64.0.9")
    assert [c[0] for c in ts.calls].count("whois") == 2 and [c[0] for c in ts.calls].count("status") == 1


def test_a_revoked_device_is_refused_at_once_even_when_the_answer_was_cached(tmp_path):
    ts = FakeTailscale(answers={"100.64.0.9": whois()})
    trust = ts.trust(directory=str(tmp_path))
    assert trust.check("100.64.0.9")[0]
    (tmp_path / mi.DENIED_DEVICES_FILE).write_text("# lost phone\nPixel-7\n")
    trusted, _d, why = trust.check("100.64.0.9")
    assert not trusted and "revoked" in why


# ------------------------------------------------------------------ the handshake

class AlwaysTrust:
    def __init__(self, trusted=True, device="pixel-7"):
        self.trusted, self.device, self.asked = trusted, device, []

    def check(self, address):
        self.asked.append(address)
        return self.trusted, self.device, f"{self.device} is signed in to Tailscale as franc@example.com"


@needs_openssl
def test_a_trusted_phone_gets_in_without_a_token_and_is_told_so(tmp_path):
    async def go():
        async with running(tmp_path, tailnet=AlwaysTrust()) as (port, inj, token, _fp):
            c = await connect(port)
            await c.send(mi.msg_hello(b"", "pixel"))
            msg_type, payload = await c.recv()
            assert msg_type == mi.T_HELLO_OK and payload[1] & mi.FEATURE_TAILNET_TRUST
            await c.send(mi.msg_move(2, 3))
            await asyncio.sleep(0.15)
            assert inj.events[0] == ("move", 2, 3)
    asyncio.run(go())


@needs_openssl
def test_an_untrusted_phone_without_a_token_is_refused_with_a_reason_a_person_can_act_on(tmp_path):
    async def go():
        async with running(tmp_path, tailnet=AlwaysTrust(trusted=False)) as (port, _inj, _token, _fp):
            c = await connect(port)
            await c.send(mi.msg_hello(b"", "pixel"))
            msg_type, payload = await c.recv()
            assert msg_type == mi.T_ERROR and payload[0] == mi.E_BAD_TOKEN and b"Tailscale" in payload
    asyncio.run(go())


@needs_openssl
def test_the_token_still_works_and_does_not_claim_tailscale_trust(tmp_path):
    async def go():
        async with running(tmp_path, tailnet=AlwaysTrust(trusted=False)) as (port, _inj, token, _fp):
            c = await connect(port)
            await c.send(mi.msg_hello(token, "pixel"))
            msg_type, payload = await c.recv()
            assert msg_type == mi.T_HELLO_OK and not payload[1] & mi.FEATURE_TAILNET_TRUST
    asyncio.run(go())


@needs_openssl
def test_a_phone_with_a_stale_token_but_the_right_account_still_gets_in(tmp_path):
    async def go():
        async with running(tmp_path, tailnet=AlwaysTrust()) as (port, _inj, _token, _fp):
            c = await connect(port)
            await c.send(mi.msg_hello(b"x" * 32, "pixel"))
            msg_type, payload = await c.recv()
            assert msg_type == mi.T_HELLO_OK and payload[1] & mi.FEATURE_TAILNET_TRUST
    asyncio.run(go())


@needs_openssl
def test_without_the_feature_a_phone_with_no_token_is_refused_as_before(tmp_path):
    async def go():
        async with running(tmp_path) as (port, _inj, _token, _fp):
            c = await connect(port)
            await c.send(mi.msg_hello(b"", "pixel"))
            msg_type, payload = await c.recv()
            assert msg_type == mi.T_ERROR and payload[0] == mi.E_BAD_TOKEN
    asyncio.run(go())
