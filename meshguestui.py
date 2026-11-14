#!/usr/bin/env python3
"""meshguestui - what the guest's browser loads to watch and control a shared screen. Plain HTML, CSS and one script; no libraries,
nothing from any other site. Served by meshshare's gateway."""

SCREEN_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Shared screen</title><link rel="stylesheet" href="/_mesh/screen.css"></head>
<body>
<div id="stage"><canvas id="screen" tabindex="0"></canvas></div>
<div id="start"><div><h1>Mesh</h1><p id="note">The owner is sharing their screen with you.</p><button id="go">Start</button>
<p class="dim">Sound and the keyboard and mouse start when you click Start.</p></div></div>
<div id="bar" hidden><span id="stat"></span><button id="fs" type="button">Full screen</button><button id="snd" type="button">Sound on</button><button id="cur" type="button">My cursor</button><button id="leave" type="button">Leave</button><button id="close" type="button">Close</button></div>
<div id="over" hidden><div><h1>Mesh</h1><p id="why"></p><button id="again" type="button" hidden>Reconnect</button></div></div>
<script src="/_mesh/screen.js"></script>
</body></html>
"""

SCREEN_CSS = r"""html,body{margin:0;height:100%;background:#000;color:#fff;font-family:ui-monospace,Menlo,Consolas,monospace;overflow:hidden}
#stage{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:#000}
canvas{display:block;background:#000;outline:none;cursor:none;touch-action:none}
#start,#over{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;text-align:center;background:#000;z-index:5}
#over[hidden],#start[hidden],#bar[hidden]{display:none}
h1{font-size:1.1rem;letter-spacing:.3em;text-transform:uppercase;margin:0 0 1.5rem}
p{margin:.6rem 1rem}.dim{color:#888;font-size:.85rem}
button{font:inherit;color:#fff;background:#000;border:1px solid #fff;padding:.45rem .9rem;margin:0 .25rem;cursor:pointer}
button:hover{background:#fff;color:#000}
#go{font-size:1.2rem;padding:.7rem 2.2rem;margin-top:.8rem}
#bar{position:fixed;top:0;left:50%;transform:translateX(-50%);z-index:4;padding:.35rem .6rem;background:rgba(0,0,0,.82);border:1px solid #444;border-top:0;
     opacity:.18;transition:opacity .2s;white-space:nowrap;max-width:100vw;overflow-x:auto}
#bar:hover,#bar:focus-within{opacity:1}
#stat{color:#aaa;font-size:.8rem;margin-right:.6rem}
"""

SCREEN_JS = r"""(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var canvas = $('screen'), ctx = canvas.getContext('2d', {alpha: false});
  var st = {ws: null, vdec: null, adec: null, actx: null, gain: null, nextAudio: 0, sps: null, pps: null, configured: false, needKey: true,
            latest: null, raf: 0, started: false, ended: false, info: {}, shown: false, muted: false,
            frames: 0, bytes: 0, dropped: 0, audioChunks: 0, errors: [], fps: 0, kbps: 0, rtt: 0, tick: performance.now(), tf: 0, tb: 0};
  window.__mesh = st;   // read by the automatic tests, harmless otherwise

  function note(text) { st.errors.push(text); if (st.errors.length > 20) { st.errors.shift(); } }
  function hex(b) { return ('0' + b.toString(16)).slice(-2); }
  function send(o) { if (st.ws && st.ws.readyState === 1) { st.ws.send(JSON.stringify(o)); } }

  // ---- H.264: Annex B in, length-prefixed (AVCC) out for WebCodecs
  function trim(u8, a, b) { while (b > a && u8[b - 1] === 0) { b--; } return u8.subarray(a, b); }
  function nals(u8) {
    var out = [], i = 0, n = u8.length, start = -1;
    while (i + 3 <= n) {
      if (u8[i + 2] > 1) { i += 3; continue; }
      if (u8[i] === 0 && u8[i + 1] === 0 && u8[i + 2] === 1) {
        if (start >= 0) { out.push(trim(u8, start, i)); }
        start = i + 3; i += 3;
      } else { i++; }
    }
    if (start >= 0) { out.push(trim(u8, start, n)); }
    return out;
  }
  function toAvcc(list) {
    var size = 0, p = 0, k;
    for (k = 0; k < list.length; k++) { size += 4 + list[k].length; }
    var out = new Uint8Array(size), dv = new DataView(out.buffer);
    for (k = 0; k < list.length; k++) { dv.setUint32(p, list[k].length); out.set(list[k], p + 4); p += 4 + list[k].length; }
    return out;
  }
  function avcC(sps, pps) {
    var out = new Uint8Array(11 + sps.length + pps.length), p = 0;
    out[p++] = 1; out[p++] = sps[1]; out[p++] = sps[2]; out[p++] = sps[3]; out[p++] = 0xFF; out[p++] = 0xE1;
    out[p++] = sps.length >> 8; out[p++] = sps.length & 255; out.set(sps, p); p += sps.length;
    out[p++] = 1; out[p++] = pps.length >> 8; out[p++] = pps.length & 255; out.set(pps, p);
    return out;
  }
  function same(a, b) { if (!a || !b || a.length !== b.length) { return false; } for (var i = 0; i < a.length; i++) { if (a[i] !== b[i]) { return false; } } return true; }

  function makeDecoder() {
    if (st.vdec) { try { st.vdec.close(); } catch (e) { /* already closed */ } }
    st.vdec = new VideoDecoder({output: onFrame, error: function (e) { note('video: ' + e.message); st.configured = false; st.needKey = true; st.sps = null; setTimeout(makeDecoder, 0); }});
    st.configured = false; st.needKey = true;
  }
  function handleVideo(buf) {
    var u8 = new Uint8Array(buf), key = (u8[1] & 1) === 1, pts = new DataView(buf).getUint32(2);
    var list = nals(u8.subarray(6)), body = [], sps = null, pps = null, i, t;
    for (i = 0; i < list.length; i++) {
      t = list[i][0] & 31;
      if (t === 7) { sps = list[i]; } else if (t === 8) { pps = list[i]; } else if (t === 1 || t === 5) { body.push(list[i]); }
    }
    if (sps && pps && (!st.configured || !same(sps, st.sps) || !same(pps, st.pps))) {
      st.sps = sps.slice(); st.pps = pps.slice();
      try {
        st.vdec.configure({codec: 'avc1.' + hex(sps[1]) + hex(sps[2]) + hex(sps[3]), description: avcC(sps, pps), optimizeForLatency: true});
        st.configured = true; st.needKey = true;
      } catch (e) { note('configure: ' + e.message); return; }
    }
    if (!st.configured || !body.length) { return; }
    if (st.needKey && !key) { return; }
    if (st.vdec.decodeQueueSize > 10 && !key) { st.needKey = true; st.dropped++; return; }
    st.needKey = false;
    st.bytes += buf.byteLength;
    try {
      st.vdec.decode(new EncodedVideoChunk({type: key ? 'key' : 'delta', timestamp: pts * 1000, data: toAvcc(body)}));
    } catch (e) { note('decode: ' + e.message); st.needKey = true; }
  }

  // ---- drawing
  function fit() {
    var scale = Math.min(window.innerWidth / canvas.width, window.innerHeight / canvas.height);
    canvas.style.width = Math.floor(canvas.width * scale) + 'px';
    canvas.style.height = Math.floor(canvas.height * scale) + 'px';
  }
  function onFrame(frame) {
    if (st.latest) { st.latest.close(); }
    st.latest = frame;
    if (!st.raf) { st.raf = requestAnimationFrame(draw); }
  }
  function draw() {
    st.raf = 0;
    var f = st.latest;
    if (!f) { return; }
    st.latest = null;
    if (canvas.width !== f.displayWidth || canvas.height !== f.displayHeight) { canvas.width = f.displayWidth; canvas.height = f.displayHeight; fit(); }
    ctx.drawImage(f, 0, 0);
    f.close();
    st.frames++;
    if (!st.shown) { st.shown = true; $('start').hidden = true; $('bar').hidden = false; canvas.focus(); }
  }
  window.addEventListener('resize', fit);

  // ---- sound: Opus packets -> AudioContext, kept close to live
  function handleAudio(buf) {
    if (!st.actx || !window.AudioDecoder) { return; }
    if (!st.adec) {
      st.adec = new AudioDecoder({output: onAudio, error: function (e) { note('audio: ' + e.message); st.adec = null; }});
      st.adec.configure({codec: 'opus', sampleRate: 48000, numberOfChannels: 2});
    }
    if (st.adec.decodeQueueSize > 20) { return; }
    try {
      st.adec.decode(new EncodedAudioChunk({type: 'key', timestamp: new DataView(buf).getUint32(2) * 1000, data: new Uint8Array(buf, 6)}));
    } catch (e) { note('audio decode: ' + e.message); }
  }
  function onAudio(data) {
    st.audioChunks++;
    var c = st.actx, ch = data.numberOfChannels, n = data.numberOfFrames, buf = c.createBuffer(ch, n, data.sampleRate), k, tmp;
    for (k = 0; k < ch; k++) { tmp = new Float32Array(n); data.copyTo(tmp, {planeIndex: k, format: 'f32-planar'}); buf.copyToChannel(tmp, k); }
    data.close();
    var src = c.createBufferSource(), now = c.currentTime, t = Math.max(now + 0.05, st.nextAudio);
    if (t - now > 0.4) { t = now + 0.05; }
    src.buffer = buf; src.connect(st.gain); src.start(t); st.nextAudio = t + buf.duration;
  }

  // ---- messages from the computer
  function control(m) {
    if (m.t === 'info') {
      st.info = m;
      if (m.width && m.height) { $('stat').title = m.width + 'x' + m.height; }
      if (m.paused) { status('Paused by the owner'); }
    } else if (m.t === 'end') { finish(m.why || 'The owner ended this.'); }
    else if (m.t === 'pong') { st.rtt = Math.round(performance.now() - m.ts); }
  }
  function status(text) { $('stat').textContent = text; }
  function finish(why) {
    st.ended = true;
    try { if (st.ws) { st.ws.close(); } } catch (e) { /* closing */ }
    try { if (st.vdec) { st.vdec.close(); } if (st.adec) { st.adec.close(); } if (st.actx) { st.actx.close(); } } catch (e) { /* closing */ }
    if (document.fullscreenElement) { document.exitFullscreen(); }
    $('bar').hidden = true; $('start').hidden = true; $('why').textContent = why; $('again').hidden = true; $('over').hidden = false;
  }
  function connect() {
    var ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/_mesh/ws');
    ws.binaryType = 'arraybuffer';
    st.ws = ws;
    ws.onmessage = function (ev) {
      if (typeof ev.data === 'string') { try { control(JSON.parse(ev.data)); } catch (e) { note('control: ' + e.message); } return; }
      var kind = new Uint8Array(ev.data, 0, 1)[0];
      if (kind === 1) { handleVideo(ev.data); } else if (kind === 2) { handleAudio(ev.data); }
    };
    ws.onclose = function () {
      if (st.ended) { return; }
      fetch('/_mesh/status', {credentials: 'same-origin', cache: 'no-store'}).then(function (r) { return r.json(); }).then(function (s) {
        if (s.state === 'active') { st.ended = false; $('why').textContent = 'The connection dropped.'; $('again').hidden = false; $('over').hidden = false; $('bar').hidden = true; }
        else { finish(s.message || 'This share ended.'); }
      }).catch(function () { $('why').textContent = 'Cannot reach the computer.'; $('again').hidden = false; $('over').hidden = false; });
    };
  }

  // ---- the guest's keyboard and mouse
  function clamp(v) { return Math.max(0, Math.min(65535, Math.round(v))); }
  function pos(e) { var r = canvas.getBoundingClientRect(); return {x: clamp((e.clientX - r.left) / r.width * 65535), y: clamp((e.clientY - r.top) / r.height * 65535)}; }
  var lastMove = null, moveTimer = 0;
  function flushMove() { moveTimer = 0; if (lastMove) { send({t: 'm', x: lastMove.x, y: lastMove.y}); lastMove = null; } }
  function btn(b) { return b === 0 ? 1 : b === 1 ? 2 : b === 2 ? 3 : b === 3 ? 4 : 5; }
  canvas.addEventListener('pointermove', function (e) { lastMove = pos(e); if (!moveTimer) { moveTimer = setTimeout(flushMove, 8); } });
  canvas.addEventListener('pointerdown', function (e) {
    lastMove = pos(e); flushMove();
    try { canvas.setPointerCapture(e.pointerId); } catch (x) { /* not capturable */ }
    send({t: 'b', b: btn(e.button), d: 1}); canvas.focus(); e.preventDefault();
  });
  canvas.addEventListener('pointerup', function (e) { lastMove = pos(e); flushMove(); send({t: 'b', b: btn(e.button), d: 0}); e.preventDefault(); });
  canvas.addEventListener('contextmenu', function (e) { e.preventDefault(); });
  canvas.addEventListener('wheel', function (e) {
    var k = e.deltaMode === 1 ? 1 / 3 : e.deltaMode === 2 ? 10 : 1 / 100;
    send({t: 'w', v: -e.deltaY * k, h: e.deltaX * k}); e.preventDefault();
  }, {passive: false});
  function key(e, down) {
    if (!st.started || st.ended || !st.shown || !e.code || e.code === 'Unidentified') { return; }
    if (e.repeat && down) { e.preventDefault(); return; }
    send({t: 'k', c: e.code, d: down ? 1 : 0}); e.preventDefault();
  }
  window.addEventListener('keydown', function (e) { key(e, true); }, true);
  window.addEventListener('keyup', function (e) { key(e, false); }, true);
  window.addEventListener('blur', function () { send({t: 'r'}); });
  document.addEventListener('visibilitychange', function () { if (document.hidden) { send({t: 'r'}); } });

  // ---- buttons
  function start() {
    if (!window.isSecureContext || !window.VideoDecoder || !window.WebSocket) {
      $('note').textContent = 'This browser cannot show the shared screen. Open the link in a recent Chrome, Edge, Firefox or Safari.';
      $('go').hidden = true; return;
    }
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (AC && window.AudioDecoder) { st.actx = new AC({latencyHint: 'interactive', sampleRate: 48000}); st.gain = st.actx.createGain(); st.gain.connect(st.actx.destination); }
      else { $('snd').hidden = true; }
    } catch (e) { note('audio context: ' + e.message); $('snd').hidden = true; }
    st.started = true; $('note').textContent = 'Connecting...'; $('go').hidden = true;
    makeDecoder(); connect();
  }
  $('go').addEventListener('click', start);
  $('again').addEventListener('click', function () { $('over').hidden = true; st.ended = false; makeDecoder(); connect(); });
  $('fs').addEventListener('click', function () {
    var root = document.documentElement;
    if (document.fullscreenElement) { document.exitFullscreen(); return; }
    var p = root.requestFullscreen ? root.requestFullscreen() : null;
    Promise.resolve(p).then(function () { if (navigator.keyboard && navigator.keyboard.lock) { return navigator.keyboard.lock(); } }).catch(function (e) { note('fullscreen: ' + e.message); });
  });
  document.addEventListener('fullscreenchange', function () { if (!document.fullscreenElement && navigator.keyboard && navigator.keyboard.unlock) { navigator.keyboard.unlock(); } fit(); });
  $('snd').addEventListener('click', function () {
    st.muted = !st.muted;
    if (st.gain) { st.gain.gain.value = st.muted ? 0 : 1; }
    if (st.actx && st.actx.state === 'suspended') { st.actx.resume(); }
    $('snd').textContent = st.muted ? 'Sound off' : 'Sound on';
  });
  $('cur').addEventListener('click', function () { canvas.style.cursor = canvas.style.cursor === 'default' ? 'none' : 'default'; });
  $('leave').addEventListener('click', function () {
    fetch('/_mesh/leave', {method: 'POST', credentials: 'same-origin'}).catch(function () {}).then(function () { finish('You left. Ask the owner for a new link if you need to come back.'); });
  });
  $('close').addEventListener('click', function () {
    fetch('/_mesh/close', {method: 'POST', credentials: 'same-origin'}).catch(function () {}).then(function () { finish('You closed this share. The link no longer works.'); });
  });

  // ---- numbers in the bar, and a ping to measure the round trip
  setInterval(function () {
    var now = performance.now(), dt = (now - st.tick) / 1000;
    if (dt <= 0) { return; }
    st.fps = Math.round((st.frames - st.tf) / dt); st.kbps = Math.round((st.bytes - st.tb) * 8 / 1000 / dt);
    st.tf = st.frames; st.tb = st.bytes; st.tick = now;
    if (st.shown && !(st.info && st.info.paused)) { status(st.fps + ' fps  ' + st.kbps + ' kbps  ' + st.rtt + ' ms'); }
    send({t: 'p', ts: now});
  }, 1000);
  if (!window.isSecureContext || !window.VideoDecoder) { $('note').textContent = 'This browser cannot show the shared screen. Open the link in a recent Chrome, Edge, Firefox or Safari.'; $('go').hidden = true; }
})();
"""


def screen_csp(host: str) -> str:
    return ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
            f"connect-src 'self' wss://{host} ws://{host}; media-src blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
