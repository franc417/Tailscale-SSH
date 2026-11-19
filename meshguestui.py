#!/usr/bin/env python3
"""meshguestui - what the guest's browser loads to watch and control a shared screen. Plain HTML, CSS and one script; no libraries,
nothing from any other site. Served by meshshare's gateway."""

SCREEN_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow"><title>Shared screen</title><link rel="stylesheet" href="/_mesh/screen.css"></head>
<body>
<div id="stage"><canvas id="screen" tabindex="0"></canvas><div id="lc" hidden></div></div>
<div id="start"><div><h1>Mesh</h1><p id="note">The owner is sharing their screen with you.</p><button id="go">Start</button>
<p class="dim">Sound and the keyboard and mouse start when you click Start.</p></div></div>
<div id="bar" hidden><span id="stat"></span><button id="fs" type="button">Full screen</button><button id="fit" type="button">Fit</button><button id="snd" type="button">Sound on</button><button id="cur" type="button">Hide my cursor</button><button id="leave" type="button">Leave</button><button id="close" type="button">Close</button></div>
<div id="mirror" hidden></div>
<button id="menu" type="button" hidden>Menu</button><button id="kbtog" type="button" hidden>Keyboard</button><div id="kb" hidden></div>
<div id="over" hidden><div><h1>Mesh</h1><p id="why"></p><button id="again" type="button" hidden>Reconnect</button></div></div>
<script src="/_mesh/screen.js"></script>
</body></html>
"""

SCREEN_CSS = r"""html,body{margin:0;height:100%;background:#000;color:#fff;font-family:ui-monospace,Menlo,Consolas,monospace;overflow:hidden}
#stage{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:#000}
canvas{display:block;background:#000;outline:none;touch-action:none;cursor:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24'%3E%3Cpath d='M3 2v17l4.5-4 3 7 3-1.3-3-6.7h6z' fill='%235ab0ff' stroke='%23000' stroke-width='1.5' stroke-linejoin='round'/%3E%3C/svg%3E") 3 2,default}
canvas.hidecursor{cursor:none}
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
#mirror{position:fixed;bottom:.6rem;left:50%;transform:translateX(-50%);z-index:4;max-width:90vw;padding:.5rem .9rem;background:rgba(0,0,0,.85);border:1px solid #666;color:#ccc;font-size:.8rem;text-align:center}
#stage{overflow:hidden}
#lc{position:absolute;left:0;top:0;width:24px;height:24px;margin:-2px 0 0 -3px;pointer-events:none;z-index:3;background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24'%3E%3Cpath d='M3 2v17l4.5-4 3 7 3-1.3-3-6.7h6z' fill='%235ab0ff' stroke='%23000' stroke-width='1.5' stroke-linejoin='round'/%3E%3C/svg%3E") no-repeat}
#lc[hidden]{display:none}
#menu,#kbtog{position:fixed;z-index:7;opacity:.7;padding:.55rem .9rem}
#menu{top:.4rem;right:.4rem}
#kbtog{bottom:.5rem;right:.5rem}
#menu[hidden],#kbtog[hidden]{display:none}
#bar.touch{display:none;top:2.9rem;left:.4rem;right:.4rem;transform:none;opacity:1;white-space:normal;max-width:none}
#bar.touch.open{display:block}
#bar.touch button{padding:.6rem .8rem;margin:.2rem}
#kb{position:fixed;left:0;right:0;bottom:0;z-index:6;padding:4px;background:rgba(8,8,8,.94);border-top:1px solid #555;touch-action:none;user-select:none;-webkit-user-select:none}
#kb[hidden]{display:none}
#kb .row{display:flex;gap:3px;margin-bottom:3px}
#kb button{flex:1 1 0;min-width:0;height:clamp(30px,9.5vh,46px);margin:0;padding:0;font-size:clamp(.7rem,2.6vh,1rem);border:1px solid #666;background:#111;color:#fff;touch-action:none}
#kb button:hover{background:#111;color:#fff}
#kb button:active{background:#333}
#kb button.on{background:#5ab0ff;color:#000}
#kb button.lock{background:#fff;color:#000}
"""

SCREEN_JS = r"""(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var canvas = $('screen'), ctx = canvas.getContext('2d', {alpha: false, desynchronized: true});
  var st = {ws: null, vdec: null, adec: null, actx: null, gain: null, nextAudio: 0, sps: null, pps: null, configured: false, needKey: true,
            latest: null, raf: 0, started: false, ended: false, info: {}, shown: false, muted: false,
            frames: 0, bytes: 0, dropped: 0, audioChunks: 0, errors: [], fps: 0, kbps: 0, rtt: 0, tick: performance.now(), tf: 0, tb: 0,
            mode: 'fit', minOff: null, late: 0, mirror: false, lastView: '', viewTimer: 0};
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
    var off = performance.now() - pts;
    if (st.minOff === null || off < st.minOff) { st.minOff = off; }
    st.late = st.late * 0.9 + (off - st.minOff) * 0.1;
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
  function stageSize() { var s = $('stage'); return [s.clientWidth || window.innerWidth, s.clientHeight || window.innerHeight]; }
  function fit() { fitPicture(); placeCursor(); }
  function fitPicture() {
    if (!canvas.width || !canvas.height) { return; }
    var sz = stageSize(), sw = sz[0], sh = sz[1], scale;
    if (st.mode === 'stretch') { canvas.style.width = sw + 'px'; canvas.style.height = sh + 'px'; return; }
    scale = st.mode === 'fill' ? Math.max(sw / canvas.width, sh / canvas.height) : Math.min(sw / canvas.width, sh / canvas.height);
    canvas.style.width = Math.round(canvas.width * scale) + 'px';
    canvas.style.height = Math.round(canvas.height * scale) + 'px';
  }
  // tell the computer how big this window really is (in screen pixels), so it sends a picture of that size and shape
  function sendView(now) {
    var sz = stageSize(), dpr = window.devicePixelRatio || 1, w = Math.round(sz[0] * dpr), h = Math.round(sz[1] * dpr), key = w + 'x' + h;
    if (key === st.lastView) { return; }
    var go = function () { st.lastView = key; send({t: 'v', w: w, h: h}); };
    if (now) { go(); return; }
    clearTimeout(st.viewTimer); st.viewTimer = setTimeout(go, 700);
  }
  function refit() { fit(); if (st.started && st.ws && st.ws.readyState === 1) { sendView(false); } }
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
    if (!st.shown) { st.shown = true; $('start').hidden = true; $('bar').hidden = false; canvas.focus(); revealTouchUi(); }
  }
  window.addEventListener('resize', refit);
  if (window.ResizeObserver) { new ResizeObserver(refit).observe($('stage')); }

  // ---- sound: Opus packets -> AudioContext, kept close to live
  function handleAudio(buf) {
    if (!st.actx || !window.AudioDecoder || st.muted) { return; }
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
      if (m.mirror && !st.mirror) {
        st.mirror = true; st.muted = true;
        if (st.gain) { st.gain.gain.value = 0; }
        $('snd').textContent = 'Sound off';
        $('mirror').textContent = 'You are watching this computer on itself, so you see a tunnel of screens and it works twice as hard. Sound is off to stop an echo. For a real test open the link on another computer. (click to hide)';
        $('mirror').hidden = false;
      }
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
    st.lastView = '';
    ws.onopen = function () { sendView(true); };
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

  // @touchpad-begin
  // ---- touch: a laptop touchpad on a phone. One finger moves the pointer (relative, a little faster when the finger is), a tap clicks, a
  // two-finger tap right-clicks, two fingers dragging scroll, and a tap followed at once by a touch that stays down drags (or double-clicks).
  function makeTouchpad(o) {
    var SLOP = 8, TAP_MS = 280, AGAIN_MS = 300;
    var fingers = {}, count = 0, most = 0, t0 = 0, moved = false, scrolled = false, dragging = false, lastTap = -1e9;
    function far(f) { var dx = f.x - f.x0, dy = f.y - f.y0; return dx * dx + dy * dy > SLOP * SLOP; }
    return {
      down: function (id, x, y) {
        var now = o.now();
        if (count === 0) {
          t0 = now; most = 0; moved = false; scrolled = false;
          if (now - lastTap < AGAIN_MS) { dragging = true; o.button(0, true); }
          lastTap = -1e9;
        }
        fingers[id] = {x: x, y: y, x0: x, y0: y, t: now};
        count++; most = Math.max(most, count);
      },
      move: function (id, x, y) {
        var f = fingers[id];
        if (!f) { return; }
        var now = o.now(), dx = x - f.x, dy = y - f.y, dt = Math.max(1, now - f.t);
        f.x = x; f.y = y; f.t = now;
        if (!moved) { if (!far(f)) { return; } moved = true; }
        if (count === 1) {
          if (scrolled) { return; }
          var gain = 1.3 + Math.min(2.2, Math.sqrt(dx * dx + dy * dy) / dt * 1.1);
          o.move(dx * gain, dy * gain);
        } else { scrolled = true; o.scroll(-dx / count, -dy / count); }
      },
      up: function (id) {
        if (!fingers[id]) { return; }
        delete fingers[id]; count--;
        if (count > 0) { return; }
        var now = o.now(), b = most >= 3 ? 1 : most === 2 ? 2 : 0;
        if (dragging) { dragging = false; o.button(0, false); return; }
        if (!moved && !scrolled && now - t0 < TAP_MS) {
          o.button(b, true); o.button(b, false);
          if (most === 1) { lastTap = now; }
        }
      },
      cancel: function () { if (dragging) { o.button(0, false); } fingers = {}; count = 0; dragging = false; moved = false; scrolled = false; }
    };
  }
  // @touchpad-end
  // @keys-begin
  // ---- the on-screen keyboard's keys: physical key names, the same ones a real keyboard sends
  function K(code, label, shift, flex, kind) { return {code: code, label: label, shift: shift || label, flex: flex || 1, kind: kind || ''}; }
  function letters(s) { return s.split('').map(function (c) { return K('Key' + c.toUpperCase(), c, c.toUpperCase()); }); }
  var KB_LAYERS = {
    main: [
      [K('Backquote', '`', '~'), K('Digit1', '1', '!'), K('Digit2', '2', '@'), K('Digit3', '3', '#'), K('Digit4', '4', '$'), K('Digit5', '5', '%'),
       K('Digit6', '6', '^'), K('Digit7', '7', '&'), K('Digit8', '8', '*'), K('Digit9', '9', '('), K('Digit0', '0', ')'), K('Minus', '-', '_'),
       K('Equal', '=', '+'), K('Backspace', 'Bksp', 'Bksp', 1.8)],
      [K('Tab', 'Tab', 'Tab', 1.5)].concat(letters('qwertyuiop'), [K('BracketLeft', '[', '{'), K('BracketRight', ']', '}'), K('Backslash', '\\', '|', 1.2)]),
      [K('Escape', 'Esc', 'Esc', 1.3)].concat(letters('asdfghjkl'), [K('Semicolon', ';', ':'), K('Quote', "'", '"'), K('Enter', 'Enter', 'Enter', 2)]),
      [K('ShiftLeft', 'Shift', 'Shift', 2.2, 'mod')].concat(letters('zxcvbnm'), [K('Comma', ',', '<'), K('Period', '.', '>'), K('Slash', '/', '?'), K('ArrowUp', '\u2191', '\u2191', 1.2)]),
      [K('ControlLeft', 'Ctrl', 'Ctrl', 1.5, 'mod'), K('MetaLeft', 'Win', 'Win', 1.3, 'mod'), K('AltLeft', 'Alt', 'Alt', 1.3, 'mod'), K('Space', 'Space', 'Space', 6),
       K('ArrowLeft', '\u2190', '\u2190'), K('ArrowDown', '\u2193', '\u2193'), K('ArrowRight', '\u2192', '\u2192'), K('Fn', 'Fn', 'Fn', 1.3, 'layer'), K('Hide', 'Hide', 'Hide', 1.3, 'hide')]
    ],
    fn: [
      [K('Escape', 'Esc', 'Esc', 1.3)].concat([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map(function (n) { return K('F' + n, 'F' + n); })),
      [K('Insert', 'Ins'), K('Delete', 'Del'), K('Home', 'Home'), K('End', 'End'), K('PageUp', 'PgUp'), K('PageDown', 'PgDn'), K('PrintScreen', 'PrtSc'), K('ContextMenu', 'Menu')],
      [K('ArrowLeft', '\u2190'), K('ArrowDown', '\u2193'), K('ArrowUp', '\u2191'), K('ArrowRight', '\u2192'), K('CapsLock', 'Caps', 'Caps', 1.4), K('ControlLeft', 'Ctrl', 'Ctrl', 1.4, 'mod'),
       K('AltLeft', 'Alt', 'Alt', 1.4, 'mod'), K('ShiftLeft', 'Shift', 'Shift', 1.4, 'mod'), K('Main', 'ABC', 'ABC', 1.4, 'layer')]
    ]
  };
  // @keys-end

  // ---- the guest's keyboard and mouse
  function clamp(v) { return Math.max(0, Math.min(65535, Math.round(v))); }
  function pos(e) { var r = canvas.getBoundingClientRect(); return {x: clamp((e.clientX - r.left) / r.width * 65535), y: clamp((e.clientY - r.top) / r.height * 65535)}; }
  var lastMove = null, moveTimer = 0, lastSent = 0;
  function flushMove() { moveTimer = 0; if (lastMove) { send({t: 'm', x: lastMove.x, y: lastMove.y}); lastMove = null; lastSent = performance.now(); } }
  function btn(b) { return b === 0 ? 1 : b === 1 ? 2 : b === 2 ? 3 : b === 3 ? 4 : 5; }
  function queueMove() {      // the first move goes out at once; the ones right behind it are merged into one every 8 ms
    var wait = 8 - (performance.now() - lastSent);
    if (wait <= 0) { flushMove(); } else if (!moveTimer) { moveTimer = setTimeout(flushMove, wait); }
  }
  canvas.addEventListener('pointermove', function (e) {
    if (e.pointerType === 'touch') { touch('move', e); return; }
    lastMove = pos(e); queueMove();
  });
  canvas.addEventListener('pointerdown', function (e) {
    if (e.pointerType === 'touch') { touch('down', e); return; }
    lastMove = pos(e); flushMove();
    try { canvas.setPointerCapture(e.pointerId); } catch (x) { /* not capturable */ }
    send({t: 'b', b: btn(e.button), d: 1}); canvas.focus(); e.preventDefault();
  });
  canvas.addEventListener('pointerup', function (e) {
    if (e.pointerType === 'touch') { touch('up', e); return; }
    lastMove = pos(e); flushMove(); send({t: 'b', b: btn(e.button), d: 0}); e.preventDefault();
  });
  canvas.addEventListener('pointercancel', function (e) { if (e.pointerType === 'touch') { tp.cancel(); } });
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
  window.addEventListener('blur', function () { send({t: 'r'}); kbReset(); });
  document.addEventListener('visibilitychange', function () { if (document.hidden) { send({t: 'r'}); kbReset(); } });

  // ---- touch screens (phones, tablets): the touchpad above, an own pointer drawn at once, an on-screen keyboard, full screen in landscape
  var android = /Android/i.test(navigator.userAgent), touchUi = false, touchSeen = false, vp = {x: 0.5, y: 0.5};
  function placeCursor() {      // the guest's own pointer: drawn here, so it follows the finger at once instead of after the picture's round trip
    var lc = $('lc'), r = canvas.getBoundingClientRect(), s = $('stage').getBoundingClientRect();
    lc.style.transform = 'translate(' + Math.round(r.left - s.left + vp.x * r.width) + 'px,' + Math.round(r.top - s.top + vp.y * r.height) + 'px)';
    lc.hidden = !(touchSeen && st.shown && !canvas.classList.contains('hidecursor'));
  }
  function virtualMove() { lastMove = {x: clamp(vp.x * 65535), y: clamp(vp.y * 65535)}; }
  var tp = makeTouchpad({
    now: function () { return performance.now(); },
    move: function (dx, dy) {
      var r = canvas.getBoundingClientRect();
      vp.x = Math.max(0, Math.min(1, vp.x + dx / r.width)); vp.y = Math.max(0, Math.min(1, vp.y + dy / r.height));
      placeCursor(); virtualMove(); queueMove();
    },
    button: function (b, down) { virtualMove(); flushMove(); send({t: 'b', b: btn(b), d: down ? 1 : 0}); },
    scroll: function (sx, sy) { send({t: 'w', v: -sy / 45, h: sx / 45}); }
  });
  function touch(kind, e) {
    if (!touchSeen) { enableTouchUi(); }
    if (kind === 'down') { try { canvas.setPointerCapture(e.pointerId); } catch (x) { /* not capturable */ } tp.down(e.pointerId, e.clientX, e.clientY); canvas.focus(); }
    else if (kind === 'move') { tp.move(e.pointerId, e.clientX, e.clientY); }
    else { tp.up(e.pointerId); }
    e.preventDefault();
  }
  function enableTouchUi() { touchSeen = true; touchUi = true; $('bar').classList.add('touch'); if (st.shown) { revealTouchUi(); } }
  function revealTouchUi() { if (touchUi) { $('menu').hidden = false; $('kbtog').hidden = kb.open; } placeCursor(); }

  var kb = {layer: 'main', mods: {}, open: false};      // mods: key name -> 1 (next key only) or 2 (until tapped again)
  function kbSend(code, down) { send({t: 'k', c: code, d: down ? 1 : 0}); }
  function kbReset() { kb.mods = {}; if (kb.open) { kbRender(); } }
  function kbRelease() {
    var changed = false;
    Object.keys(kb.mods).forEach(function (c) { if (kb.mods[c] === 1) { kbSend(c, false); kb.mods[c] = 0; changed = true; } });
    if (changed) { kbRender(); }
  }
  function kbBind(b, k) {
    if (k.kind === 'layer') { b.addEventListener('click', function () { kb.layer = k.code === 'Fn' ? 'fn' : 'main'; kbRender(); }); return; }
    if (k.kind === 'hide') { b.addEventListener('click', function () { kbShow(false); }); return; }
    if (k.kind === 'mod') {
      b.addEventListener('click', function () {
        var s = ((kb.mods[k.code] || 0) + 1) % 3;
        if (s === 1) { kbSend(k.code, true); } else if (s === 0) { kbSend(k.code, false); }
        kb.mods[k.code] = s; kbRender();
      });
      return;
    }
    var down = false;
    b.addEventListener('pointerdown', function (e) { down = true; try { b.setPointerCapture(e.pointerId); } catch (x) { /* not capturable */ } kbSend(k.code, true); e.preventDefault(); });
    var up = function (e) { if (!down) { return; } down = false; kbSend(k.code, false); kbRelease(); e.preventDefault(); };
    b.addEventListener('pointerup', up); b.addEventListener('pointercancel', up);
  }
  function kbRender() {
    var box = $('kb'), shift = (kb.mods.ShiftLeft || 0) > 0;
    box.textContent = '';
    KB_LAYERS[kb.layer].forEach(function (keys) {
      var row = document.createElement('div'); row.className = 'row';
      keys.forEach(function (k) {
        var b = document.createElement('button'); b.type = 'button'; b.style.flex = k.flex + ' 1 0'; b.textContent = shift ? k.shift : k.label;
        if (k.kind === 'mod' && kb.mods[k.code]) { b.className = kb.mods[k.code] === 2 ? 'lock' : 'on'; }
        kbBind(b, k); row.appendChild(b);
      });
      box.appendChild(row);
    });
  }
  function kbShow(on) {
    if (!on) { Object.keys(kb.mods).forEach(function (c) { if (kb.mods[c]) { kbSend(c, false); } }); kb.mods = {}; }
    kb.open = on; $('kb').hidden = !on; $('kbtog').hidden = on || !touchUi;
    if (on) { kb.layer = 'main'; kbRender(); }
  }
  $('kbtog').addEventListener('click', function () { kbShow(true); });
  $('menu').addEventListener('click', function () { $('bar').classList.toggle('open'); });

  function lockLandscape() {
    if (screen.orientation && screen.orientation.lock) { return screen.orientation.lock('landscape').catch(function (e) { note('orientation: ' + e.message); }); }
  }
  function goFull() {      // an Android phone: the whole screen and landscape, like the Mesh app (fullscreen only works from a tap, and the lock only inside it)
    var root = document.documentElement;
    if (document.fullscreenElement || !root.requestFullscreen) { return lockLandscape(); }
    return Promise.resolve(root.requestFullscreen({navigationUI: 'hide'})).then(lockLandscape).catch(function (e) { note('fullscreen: ' + e.message); });
  }
  if (window.matchMedia && matchMedia('(pointer: coarse)').matches) { enableTouchUi(); }

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
    if (android) { goFull(); }
    makeDecoder(); connect();
  }
  $('go').addEventListener('click', start);
  $('again').addEventListener('click', function () { $('over').hidden = true; st.ended = false; makeDecoder(); connect(); });
  $('fs').addEventListener('click', function () {
    var root = document.documentElement;
    if (document.fullscreenElement) { document.exitFullscreen(); return; }
    var p = root.requestFullscreen ? root.requestFullscreen() : null;
    Promise.resolve(p).then(function () { if (touchUi) { lockLandscape(); } if (navigator.keyboard && navigator.keyboard.lock) { return navigator.keyboard.lock(); } }).catch(function (e) { note('fullscreen: ' + e.message); });
  });
  document.addEventListener('fullscreenchange', function () { if (!document.fullscreenElement && navigator.keyboard && navigator.keyboard.unlock) { navigator.keyboard.unlock(); } fit(); });
  $('snd').addEventListener('click', function () {
    st.muted = !st.muted;
    st.nextAudio = 0;
    if (st.gain) { st.gain.gain.value = st.muted ? 0 : 1; }
    if (st.actx && st.actx.state === 'suspended') { st.actx.resume(); }
    $('snd').textContent = st.muted ? 'Sound off' : 'Sound on';
  });
  var modes = ['fit', 'fill', 'stretch'], names = {fit: 'Fit', fill: 'Fill', stretch: 'Stretch'};
  try { var saved = localStorage.getItem('mesh-fit'); if (saved && names[saved]) { st.mode = saved; } } catch (e) { /* storage may be blocked */ }
  $('fit').textContent = names[st.mode];
  $('fit').addEventListener('click', function () {
    st.mode = modes[(modes.indexOf(st.mode) + 1) % modes.length];
    $('fit').textContent = names[st.mode];
    try { localStorage.setItem('mesh-fit', st.mode); } catch (e) { /* storage may be blocked */ }
    fit();
  });
  $('mirror').addEventListener('click', function () { $('mirror').hidden = true; });
  function showCursor(on) {      // the guest's own pointer, drawn by their computer: it follows the hand at once instead of after the picture's round trip
    canvas.classList.toggle('hidecursor', !on);
    $('cur').textContent = on ? 'Hide my cursor' : 'Show my cursor';
    try { localStorage.setItem('mesh-cursor', on ? '1' : '0'); } catch (e) { /* storage may be blocked */ }
    placeCursor();
  }
  try { if (localStorage.getItem('mesh-cursor') === '0') { showCursor(false); } } catch (e) { /* storage may be blocked */ }
  $('cur').addEventListener('click', function () { showCursor(canvas.classList.contains('hidecursor')); });
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
    if (st.shown && !(st.info && st.info.paused)) { status((st.info && st.info.width ? st.info.width + 'x' + st.info.height + (st.info.quality ? ' ' + st.info.quality : '') + '  ' : '') + st.fps + ' fps  ' + st.kbps + ' kbps  ' + st.rtt + ' ms  late ' + Math.round(st.late) + ' ms'); }
    send({t: 'p', ts: now});
  }, 1000);
  if (!window.isSecureContext || !window.VideoDecoder) { $('note').textContent = 'This browser cannot show the shared screen. Open the link in a recent Chrome, Edge, Firefox or Safari.'; $('go').hidden = true; }
})();
"""


def screen_csp(host: str) -> str:
    return ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
            f"connect-src 'self' wss://{host} ws://{host}; media-src blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
