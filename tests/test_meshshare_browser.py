"""meshshare in a real browser (Chromium through Playwright). Skipped where Playwright or Chromium is not installed (CI)."""

import http.server
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import meshshare as ms  # noqa: E402

sync_api = pytest.importorskip("playwright.sync_api")


class Page(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        port = self.server.server_address[1]
        pages = {"/": f'<h1>Student project</h1><a id="go" href="http://localhost:{port}/page2.php">next</a>',
                 "/page2.php": "<h1>Second page</h1>"}
        body = pages.get(self.path, "missing").encode()
        self.send_response(200 if self.path in pages else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def browser():
    try:
        pw = sync_api.sync_playwright().start()
        b = pw.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no Chromium here: {exc}")
    yield b
    b.close()
    pw.stop()


def test_guest_opens_the_link_owner_approves_then_revokes(browser, tmp_path):
    app = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    app.daemon_threads = True
    threading.Thread(target=app.serve_forever, daemon=True).start()
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    environ = {"XDG_RUNTIME_DIR": str(runtime)}
    service = ms.ShareService(str(tmp_path / "cfg"), ms.NoTunnel(), say=lambda s: None, environ=environ)
    info = service.open_web(app.server_address[1], ttl=600)
    try:
        link = info["link"]
        secret = link.split("#", 1)[1]
        page = browser.new_page()
        page.goto(link)
        page.wait_for_selector("#code:not(:empty)", timeout=5000)
        code = page.inner_text("#code")
        assert len(code) == 4 and code.isdigit()
        assert secret not in page.url and "#" not in page.url                    # the secret left the address bar
        assert "Waiting for the owner" in page.inner_text("#msg")
        # the owner checks the code and approves from another terminal
        listed = ms.control_request(str(tmp_path / "cfg"), {"cmd": "list"}, environ)
        assert listed["shares"][0]["visitor"]["code"] == code
        assert ms.control_request(str(tmp_path / "cfg"), {"cmd": "approve", "code": code}, environ)["ok"]
        page.wait_for_selector("h1:has-text('Student project')", timeout=8000)    # the page moved on to the shared app by itself
        assert page.url.endswith("/") and "/_mesh/" not in page.url
        link_target = page.get_attribute("#go", "href")
        assert link_target.startswith(f"http://127.0.0.1:{service.gateway.port}") and ":%d" % app.server_address[1] not in link_target
        page.click("#go")
        page.wait_for_selector("h1:has-text('Second page')", timeout=5000)
        cookies = page.context.cookies()
        guest = [c for c in cookies if c["name"] == ms.COOKIE][0]
        assert guest["httpOnly"] and guest["sameSite"] == "Strict"
        # a second browser with the same link is refused while the first is in
        other = browser.new_context().new_page()
        other.goto(link)
        other.wait_for_function("document.getElementById('msg').textContent.includes('Someone else')", timeout=5000)
        # the owner revokes: the first browser is out at once, and the link is dead for everyone
        assert ms.control_request(str(tmp_path / "cfg"), {"cmd": "revoke"}, environ)["ok"]
        page.goto(f"http://127.0.0.1:{service.gateway.port}/", wait_until="domcontentloaded")
        assert "not active" in page.inner_text("main")
    finally:
        service.close()
        app.shutdown()
        app.server_close()
