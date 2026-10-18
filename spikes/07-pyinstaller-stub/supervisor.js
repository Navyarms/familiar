// Spike 7: Electron main process that spawns the PyInstaller-built brain stub, waits for
// READY <port>, connects over WebSocket with a token, measures round-trip, then forces a
// crash and verifies the restart path. Prints a JSON summary and exits (headless: no window).
const { app } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const crypto = require('crypto');
const http = require('http');

const EXE = path.join(__dirname, 'dist', 'brain_stub', 'brain_stub.exe');
const results = { exe: EXE, starts: [], crash_detected_ms: null, restart_ready_ms: null, ws_rtt_ms: null, orphan_check: null };
let brain = null, port = null, token = crypto.randomBytes(16).toString('hex'), restarts = 0, intentionalCrash = false;
let resolveReady = null;

function startBrain() {
  const t0 = Date.now();
  brain = spawn(EXE, ['--token', token], { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  brain.stdout.on('data', (d) => {
    const m = /READY (\d+)/.exec(d.toString());
    if (m) { port = +m[1]; results.starts.push({ pid: brain.pid, port, ready_ms: Date.now() - t0 }); resolveReady && resolveReady(); }
  });
  brain.stderr.on('data', (d) => process.stderr.write('[brain] ' + d));
  brain.on('exit', (code) => {
    process.stdout.write(`brain exited code=${code} intentional=${intentionalCrash}\n`);
    if (intentionalCrash) { results.crash_detected_ms = Date.now() - crashT0; intentionalCrash = false; restarts++; const r0 = Date.now(); onReady().then(() => { results.restart_ready_ms = Date.now() - r0; }); startBrain(); }
  });
  return onReady();
}
function onReady() { return new Promise((res) => { resolveReady = res; }); }
function getOnce(p, method = 'GET') {
  return new Promise((res, rej) => {
    const req = http.request({ host: '127.0.0.1', port, path: p, method }, (r) => { let b = ''; r.on('data', (c) => b += c); r.on('end', () => res(JSON.parse(b))); });
    req.on('error', rej); req.end();
  });
}
async function get(p, method = 'GET', tries = 20) { // retry briefly: the brain may still be binding
  for (let i = 0; ; i++) {
    try { return await getOnce(p, method); } catch (e) { if (i >= tries) throw e; await new Promise((r) => setTimeout(r, 100)); }
  }
}
let crashT0 = 0;
app.whenReady().then(async () => {
  try {
    await startBrain();
    results.health = await get('/health');
    // WebSocket round trip (Electron main has global WebSocket since v28+)
    const ws = new WebSocket(`ws://127.0.0.1:${port}/ws?token=${token}`);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    const first = await new Promise((res) => { ws.onmessage = (e) => res(JSON.parse(e.data)); });
    results.ws_first_message = first;
    const t = performance.now();
    ws.send(JSON.stringify({ type: 'ping', t }));
    const pong = await new Promise((res) => { ws.onmessage = (e) => res(JSON.parse(e.data)); });
    results.ws_rtt_ms = +(performance.now() - t).toFixed(2);
    // wrong-token connection must be refused
    const bad = new WebSocket(`ws://127.0.0.1:${port}/ws?token=nope`);
    results.bad_token_refused = await new Promise((res) => { bad.onclose = (e) => res(e.code === 4401 || e.code === 1006); bad.onopen = () => res(false); });
    ws.close();
    // crash + restart
    intentionalCrash = true; crashT0 = Date.now();
    await get('/crash', 'POST').catch(() => {});
    await new Promise((r) => setTimeout(r, 4000));
    results.health_after_restart = await get('/health');
    results.restarts = restarts;
    // orphan check: close stdin -> brain must exit on its own
    const pid = brain.pid; brain.stdin.end();
    await new Promise((r) => setTimeout(r, 1500));
    results.orphan_check = { pid, exited_after_stdin_close: brain.exitCode !== null };
  } catch (e) { results.error = String(e); }
  process.stdout.write('RESULT ' + JSON.stringify(results) + '\n');
  if (brain && brain.exitCode === null) brain.kill();
  app.quit();
});
