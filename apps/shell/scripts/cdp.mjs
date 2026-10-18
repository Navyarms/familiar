// Dev helper: evaluate JS inside a running Familiar window via Chrome DevTools Protocol.
// Start the app with FAMILIAR_CDP=9222, then: node scripts/cdp.mjs <panel|overlay|settings> "<js expression>"
const [, , which = 'panel', expr = 'document.title'] = process.argv;
const port = process.env.FAMILIAR_CDP || '9222';
const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
const titles = { panel: /^Familiar$/, overlay: /overlay/i, settings: /settings/i };
const t = targets.find((x) => x.type === 'page' && titles[which].test(x.title));
if (!t) {
  console.error('no such page; targets:', targets.map((x) => `${x.type}:${x.title}`));
  process.exit(1);
}
const ws = new WebSocket(t.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
ws.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: { expression: expr, awaitPromise: true, returnByValue: true } }));
const res = await new Promise((r) => (ws.onmessage = (e) => r(JSON.parse(e.data))));
console.log(JSON.stringify(res.result?.result?.value ?? res.result ?? res, null, 1));
ws.close();
