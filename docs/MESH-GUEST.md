# Mesh Guest: show your computer in someone else's browser

Use it when you need to put a screen or an app in front of a person and their computer has nothing installed (a lecturer, a
friend's laptop). You send a link; they open it in any browser; you allow them in; when you are done you revoke it and the
link is dead.

## Step 1 (today): share a web app: `meshd share web PORT`

```
meshd share web 80                 # XAMPP / Apache; phpMyAdmin and your PHP projects are under it
meshd share web 8080 --ttl 20m     # shorter; default 30 minutes, at most 8 hours
meshd share web 80 --read-only     # they can look and click links, not submit forms
meshd share list                   # what is open, who is connected, the code of anyone waiting
meshd share approve [CODE]         # let the waiting visitor in (shows who, asks first)
meshd share revoke                 # end it now; the public address is closed
```

It runs in the terminal where you start it (from the phone, run it in a Termux/`mesh` session on the computer). It prints a link
and a QR code, then shows who opens it. Ctrl+C, closing that terminal, the TTL running out, or `meshd share revoke` from any
terminal all end it the same way.

**What the other person sees:** they open the link, get a black page with a 4 digit code and "waiting for the owner". You see
the same code in your terminal (their browser and system too). You allow them, their page switches to your web app by itself.
Links in your pages that point to `http://localhost/...` (common in student projects) are rewritten so they still work for them.

### One-time Tailscale setup for Funnel

Funnel is off until you approve it once (double opt-in, by Tailscale's design):

1. `meshd share web 80` the first time prints a link starting `https://login.tailscale.com/...`. Open it and approve.
2. If it asks for HTTPS certificates: Tailscale admin console, DNS page, turn on **MagicDNS** and **HTTPS Certificates**.
3. Run the command again. The first time the new `*.ts.net` name can need a few minutes to spread: open the link on your phone
   (mobile data) before giving it to anyone.

Funnel limits (Tailscale's, not ours): public ports 443, 8443 or 10000 only (`--https-port`), TCP/TLS only, and a bandwidth
limit they do not publish. TLS ends on your computer; Tailscale's relay cannot read the traffic. The public address exists only
while a share is open.

## How the link is protected

| Risk | What stops it |
|---|---|
| Someone guesses or finds the link | 256-bit random secret; only its hash is kept; wrong tries are rate-limited per address (8 per 10 min) and the share closes itself after 24 |
| The link leaks into logs or chat previews | the secret is in the `#` part (a browser never sends it); the page erases it from the address bar; a link preview cannot use it up |
| The wrong person opens it first | nothing opens until **you** approve; the same 4 digit code is on both screens |
| Two people at once | one visitor at a time |
| It stays open by mistake | TTL (default 30 min), idle timeout (15 min), request timeout (5 min); the public address is closed when the share ends |
| After you revoke | the session is deleted, open connections (even a page that is still loading) are cut, the link answers "ended", Funnel is turned off |
| Their browser steals the session | cookie is `HttpOnly`, `Secure`, `SameSite=Strict`, random 256 bits, and is never passed on to your web app |
| Your app is reachable without the gate | only the gate answers on the public port; everything else needs the session |
| Forged addresses | the visitor's address comes from the tunnel hop only; any `X-Forwarded-*` the visitor sends is replaced |

Not protected: **the other person's computer.** Anything you type there can be seen by that computer. Do not type passwords on it.
Never share a web app that has real data and no password (XAMPP's phpMyAdmin often logs in as `root` without one): use
`--read-only`, or set a password first.

## What is and is not tested

Tested by the automatic tests: all the rules above (approval, one visitor, leave, close, TTL, idle, lockout, revoke cutting a page
that is streaming), the gateway in front of a real web app (redirects, cookies, big and chunked pages, headers, uploads, view-only),
the Funnel commands against a fake `tailscale` (including the approval link and an old CLI without `--yes`), the control socket,
and the whole flow in a real Chromium (`tests/test_meshshare_browser.py`, needs Playwright, skipped in CI).

**Not tested yet:** the real Funnel on your tailnet (nobody has run it end to end), a real PHP/XAMPP app, other browsers.
Known limits: WebSocket pages and uploads sent without a length are not supported through the share yet; text pages are sent
uncompressed (so their links can be rewritten).

## Next steps

- **Phone app button** on the home screen: create the link, copy/share it, approve, see who is in, Revoke. The app will use the
  same control socket `meshd share` uses (`share.sock`, owner-only), so no new rules are needed.
- **Guest screen (the whole desktop in the browser, with sound, using the guest's keyboard and mouse):** the same gate and
  link, with a screen stream instead of a web app (design in the chat that started this; build after the steps above work).
