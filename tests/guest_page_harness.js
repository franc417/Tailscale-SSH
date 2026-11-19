const vm = require('vm');
const code = require('fs').readFileSync(process.argv[2], 'utf8');
let now = 0;
const sent = [], fullscreenCalls = [], lockCalls = [], sockets = [];
function makeEl(id) {
  const listeners = {}, cls = new Set();
  const el = {
    id, hidden: false, style: {}, children: [], _text: '', className: '', type: '', clientWidth: 1000, clientHeight: 500, offsetWidth: 900, offsetHeight: 280, width: 0, height: 0,
    classList: { add: c => cls.add(c), remove: c => cls.delete(c), contains: c => cls.has(c),
                 toggle: (c, f) => { const on = f === undefined ? !cls.has(c) : !!f; if (on) cls.add(c); else cls.delete(c); return on; } },
    addEventListener(n, f) { (listeners[n] = listeners[n] || []).push(f); },
    fire(n, ev) { (listeners[n] || []).forEach(f => f(Object.assign({preventDefault() {}, stopPropagation() {}}, ev))); },
    appendChild(c) { el.children.push(c); return c; },
    focus() {}, setPointerCapture() {},
    getBoundingClientRect() { return {left: 0, top: 0, width: 1000, height: 500}; },
    getContext() { return {drawImage() {}}; },
  };
  Object.defineProperty(el, 'textContent', { get() { return el._text; }, set(v) { el._text = v; if (v === '') { el.children = []; } } });
  return el;
}
const els = {};
const document = {
  getElementById(id) { return els[id] || (els[id] = makeEl(id)); },
  createElement(tag) { return makeEl(tag); },
  addEventListener() {}, hidden: false, fullscreenElement: null,
  documentElement: { requestFullscreen(opts) { fullscreenCalls.push(opts); return Promise.resolve(); } },
};
class FakeSocket { constructor(u) { this.url = u; this.readyState = 1; sockets.push(this); } send(s) { sent.push(JSON.parse(s)); } close() {} }
class FakeDecoder { constructor(o) {} configure() {} decode() {} close() {} reset() {} get decodeQueueSize() { return 0; } }
const win = { addEventListener() {}, innerWidth: 1000, innerHeight: 500, devicePixelRatio: 1, isSecureContext: true };
const ctx = {
  window: win, document, console, setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {}, requestAnimationFrame: () => 1, cancelAnimationFrame() {},
  performance: { now: () => now },
  navigator: { userAgent: 'Mozilla/5.0 (Linux; Android 14; Pixel) Chrome/120 Mobile', keyboard: undefined },
  screen: { orientation: { lock(o) { lockCalls.push(o); return Promise.resolve(); } } },
  localStorage: { getItem() { return null; }, setItem() {} },
  matchMedia: () => ({ matches: true }), ResizeObserver: class { observe() {} },
  VideoDecoder: FakeDecoder, EncodedVideoChunk: class {}, WebSocket: FakeSocket, AudioDecoder: undefined, AudioContext: undefined,
  location: { protocol: 'http:', host: 'x.test', pathname: '/', hash: '', search: '' }, URL, Uint8Array, DataView, JSON, Math, Promise, Object, String, Number,
};
win.VideoDecoder = FakeDecoder; win.WebSocket = FakeSocket; win.matchMedia = ctx.matchMedia;
vm.createContext(ctx);
vm.runInContext(code, ctx);
const st = win.__mesh;
const out = {};
out.touchUiOnLoad = els.bar.classList.contains('touch');
st.ws = new FakeSocket('ws://x'); sockets.length = 0; st.started = true; st.shown = true;
const canvas = els.screen;
function touch(kind, id, x, y, t) { now = t; canvas.fire('pointer' + kind, {pointerType: 'touch', pointerId: id, clientX: x, clientY: y}); }
// 1 tap
sent.length = 0; touch('down', 1, 500, 250, 0); touch('up', 1, 500, 250, 80);
out.tap = sent.slice();
// 2 slide to the right
sent.length = 0; touch('down', 1, 400, 200, 1000); touch('move', 1, 440, 200, 1016); touch('move', 1, 480, 200, 1032); touch('up', 1, 480, 200, 1200); now = 1300;
out.slide = sent.slice();
out.lcShown = els.lc.hidden === false;
// 3 two fingers up
sent.length = 0; touch('down', 1, 300, 300, 3000); touch('down', 2, 360, 300, 3000); touch('move', 1, 300, 240, 3016); touch('move', 2, 360, 240, 3016); touch('up', 1, 300, 240, 3300); touch('up', 2, 360, 240, 3300);
out.scroll = sent.slice();
// 4 keyboard
els.kbtog.fire('click');
out.kbOpen = els.kb.hidden === false; out.rows = els.kb.children.filter(r => r.className === 'row').length;
const bar = els.kb.children.find(r => r.className === 'bar'), grip = bar.children[0];
out.kbStart = {left: els.kb.style.left, top: els.kb.style.top, transform: els.kb.style.transform};
const find = (label) => { for (const row of els.kb.children) { for (const b of row.children) { if (b.textContent === label) { return b; } } } return null; };
sent.length = 0;
const a = find('a'); a.fire('pointerdown', {pointerId: 5}); a.fire('pointerup', {pointerId: 5});
out.typeA = sent.slice();
sent.length = 0;
find('Shift').fire('click');
out.shiftOn = sent.slice(); out.upper = find('A') !== null;
const A = find('A'); A.fire('pointerdown', {pointerId: 6}); A.fire('pointerup', {pointerId: 6});
out.typeShiftA = sent.slice(); out.lowerAgain = find('a') !== null;
sent.length = 0; find('Fn').fire('click'); out.fnHasF5 = find('F5') !== null; const f5 = find('F5'); f5.fire('pointerdown', {pointerId: 7}); f5.fire('pointerup', {pointerId: 7});
out.f5 = sent.slice();
find('ABC').fire('click');
// the floating keyboard: bigger, smaller, dragged
find('A+').fire('click'); out.kbBigger = els.kb.style.transform;
find('A\u2212').fire('click'); find('A\u2212').fire('click'); out.kbSmaller = els.kb.style.transform;
{ const g = els.kb.children.find(r => r.className === 'bar').children[0];
  g.fire('pointerdown', {pointerId: 8, clientX: 500, clientY: 300}); g.fire('pointermove', {pointerId: 8, clientX: 300, clientY: 100}); g.fire('pointerup', {pointerId: 8, clientX: 300, clientY: 100});
  out.kbDragged = {left: els.kb.style.left, top: els.kb.style.top}; }
find('Hide').fire('click'); out.kbClosed = els.kb.hidden === true;
// 5 menu
els.menu.fire('click'); out.menuOpens = els.bar.classList.contains('open');
// 6 a mouse still works absolutely
sent.length = 0; canvas.fire('pointermove', {pointerType: 'mouse', pointerId: 9, clientX: 250, clientY: 125}); out.mouse = sent.slice();
// 7 Start on Android goes full screen and landscape
els.go.fire('click');
setTimeout(() => { out.fullscreen = fullscreenCalls.slice(); out.lock = lockCalls.slice(); out.errors = st.errors.slice(); console.log(JSON.stringify(out)); }, 20);
