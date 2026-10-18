import { Avatar } from './avatar';
import { SpeechPlayer } from './audio';

const badge = document.getElementById('badge')!;
const avatar = new Avatar(document.body);
const player = new SpeechPlayer();
if (import.meta.env.DEV) (window as any).__familiar = { avatar, player }; // for scripts/cdp.mjs
let cursor: { x: number; y: number } | null = null;
let overAvatar = false, lastSent: boolean | null = null;
let pendingExpressions = new Map<number, string>(); // sentence_id -> tag (applied when that sentence starts)
let loaded = false;

const micEl = document.getElementById('mic')!;
let micMode = 'off';
function setMic(mode: string, level: number, reason: string): void {
  if (mode !== micMode) {
    if (mode === 'listening') chime(reason === 'wake' ? 'wake' : 'start');
    else if (micMode === 'listening' && mode === 'processing') chime('end');
    micMode = mode;
    micEl.className = mode;
    if (mode === 'listening') avatar.state = 'listening';
    else if (avatar.state === 'listening') avatar.state = 'idle';
  }
  if (mode === 'listening') micEl.style.boxShadow = `0 0 0 ${Math.round(2 + level * 14)}px rgba(60, 200, 130, ${0.15 + level * 0.35})`;
  else micEl.style.boxShadow = 'none';
}

/** Short, soft earcons so the user knows when the mic opened/closed without looking. */
const earcon = new AudioContext();
function chime(kind: 'wake' | 'start' | 'end'): void {
  void earcon.resume();
  const t = earcon.currentTime;
  const notes = kind === 'end' ? [[660, 0], [440, 0.09]] : kind === 'wake' ? [[523, 0], [784, 0.1]] : [[587, 0]];
  for (const [f, dt] of notes) {
    const o = earcon.createOscillator(), g = earcon.createGain();
    o.type = 'sine'; o.frequency.value = f;
    g.gain.setValueAtTime(0, t + dt); g.gain.linearRampToValueAtTime(0.12, t + dt + 0.01); g.gain.exponentialRampToValueAtTime(0.0001, t + dt + 0.16);
    o.connect(g).connect(earcon.destination); o.start(t + dt); o.stop(t + dt + 0.18);
  }
}

function showBadge(text: string, ms = 2500): void {
  badge.textContent = text;
  badge.classList.add('show');
  if (ms > 0) setTimeout(() => badge.classList.remove('show'), ms);
}

player.onSentenceStart = (s) => {
  const tag = pendingExpressions.get(s.sentence_id);
  if (tag) {
    avatar.setExpression(tag);
    pendingExpressions.delete(s.sentence_id);
  }
};
player.onSpeakingChange = (speaking) => {
  avatar.state = speaking ? 'speaking' : avatar.state === 'speaking' ? 'idle' : avatar.state;
  window.familiar.overlay.playback(speaking ? 'speaking' : 'idle');
};

async function applyHello(msg: any): Promise<void> {
  const p = msg.persona;
  if (!p) return;
  avatar.setExpressionMap(p.expressions ?? {});
  const kind = await avatar.load(p.avatar_path ?? null);
  loaded = true;
  showBadge(`${p.name} · ${kind}`);
}

window.familiar.brain.onMessage((msg: any) => {
  switch (msg.type) {
    case 'hello':
      void applyHello(msg);
      break;
    case 'state':
      if (msg.state === 'thinking' || msg.state === 'listening') avatar.state = msg.state;
      else if (msg.state === 'executing') { avatar.state = 'thinking'; if (msg.detail) showBadge(msg.detail, 3000); }
      else if (msg.state === 'needs_confirm') { avatar.setExpression('surprised'); if (msg.detail) showBadge(`OK to ${msg.detail}?`, 6000); }
      else if (msg.state === 'idle' && avatar.state !== 'speaking' && micMode !== 'listening') avatar.state = 'idle';
      else if (msg.state === 'booting' || msg.state === 'downloading') {
        avatar.state = 'booting';
        if (msg.detail) showBadge(msg.detail, 4000);
      }
      break;
    case 'mic.state':
      setMic(msg.mode, msg.level ?? 0, msg.reason ?? '');
      break;
    case 'expression':
      if (msg.sentence_id == null) avatar.setExpression(msg.tag);
      else pendingExpressions.set(msg.sentence_id, msg.tag);
      break;
    case 'reply.audio':
      player.enqueue(msg);
      break;
    case 'reply.done':
      if (msg.interrupted) player.stop();
      break;
    case 'model.ready':
      showBadge('ready');
      break;
    case 'error':
      showBadge(`error: ${msg.message}`.slice(0, 80), 6000);
      break;
  }
});
window.familiar.brain.onStatus((s) => {
  if (s.phase === 'restarting') showBadge('brain restarting…', 4000);
});
window.familiar.overlay.onCursor((c) => (cursor = c));

// drag to move (events only arrive while not click-through)
let dragging = false, last = { x: 0, y: 0 };
window.addEventListener('mousedown', (e) => {
  if (overAvatar) {
    dragging = true;
    last = { x: e.screenX, y: e.screenY };
  }
});
window.addEventListener('mouseup', () => (dragging = false));
window.addEventListener('mousemove', (e) => {
  if (dragging) {
    window.familiar.overlay.drag(e.screenX - last.x, e.screenY - last.y);
    last = { x: e.screenX, y: e.screenY };
  }
});
window.addEventListener('dblclick', () => window.familiar.brain.send({ type: micMode === 'listening' ? 'ptt.stop' : 'ptt.start' }));
window.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  window.familiar.openSettings();
});

function frame(): void {
  requestAnimationFrame(frame);
  const m = player.sample();
  avatar.setMouth(m.amp, m.shape);
  avatar.update();
  if (cursor && loaded) {
    overAvatar = avatar.alphaAt(cursor.x, cursor.y) > 12;
    if (overAvatar !== lastSent) {
      lastSent = overAvatar;
      window.familiar.overlay.hit(overAvatar);
    }
  }
}
frame();
window.familiar.snapshot().then((snap) => {
  if (snap.lastHello) void applyHello(snap.lastHello);
  else void avatar.load(null).then(() => (loaded = true));
});
