import { app, BrowserWindow, globalShortcut, ipcMain, Menu, nativeImage, net, protocol, Tray } from 'electron';
import { join } from 'path';
import { pathToFileURL } from 'url';
import { existsSync } from 'fs';
import { BrainSupervisor } from './brain';
import { createOverlay, createPanel, createSettings } from './windows';
import { IPC, type ShellSnapshot } from '../shared/ipc';

const HOTKEYS = { summon: 'CommandOrControl+Shift+Space', kill: 'CommandOrControl+Alt+Escape' };
const isDev = !app.isPackaged;

let overlay: BrowserWindow | null = null;
let panel: BrowserWindow | null = null;
let settings: BrowserWindow | null = null;
let tray: Tray | null = null;
const brain = new BrainSupervisor(isDev);
if (process.env['FAMILIAR_CDP']) app.commandLine.appendSwitch('remote-debugging-port', process.env['FAMILIAR_CDP']);

if (!app.requestSingleInstanceLock()) app.quit();
app.on('second-instance', () => showPanel());

// familiar://avatar/current.vrm serves the active persona's model to the overlay. Renderers cannot fetch
// file:// URLs (dev server origin is http://localhost), and this keeps the renderer free of absolute paths.
protocol.registerSchemesAsPrivileged([{ scheme: 'familiar', privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, bypassCSP: true, stream: true } }]);
let avatarPath: string | null = null;

function showPanel(): void {
  if (!panel || !overlay) return;
  (panel as any).__placeNear();
  panel.show();
  panel.focus();
  panel.webContents.send(IPC.panelShown);
  brain.send({ type: 'summon' });
}

function togglePanel(): void {
  if (panel?.isVisible()) panel.hide();
  else showPanel();
}

function openSettings(): void {
  if (settings && !settings.isDestroyed()) return settings.focus();
  settings = createSettings();
  settings.on('closed', () => (settings = null));
}

function trayIcon() {
  // 16x16 soft orb drawn in code so the shell needs no asset pipeline yet
  const size = 16;
  const buf = Buffer.alloc(size * size * 4);
  for (let y = 0; y < size; y++)
    for (let x = 0; x < size; x++) {
      const dx = x - 7.5, dy = y - 7.5;
      const d = Math.sqrt(dx * dx + dy * dy) / 7.5;
      const a = d > 1 ? 0 : Math.round(255 * (1 - d * d * 0.6));
      const i = (y * size + x) * 4;
      buf[i] = 120; buf[i + 1] = 200; buf[i + 2] = 255; buf[i + 3] = a; // BGRA on Windows
    }
  return nativeImage.createFromBitmap(buf, { width: size, height: size });
}

app.whenReady().then(() => {
  app.setAppUserModelId('dev.familiar.app');
  protocol.handle('familiar', async (req) => {
    const u = new URL(req.url);
    const cors = { 'Access-Control-Allow-Origin': '*' };
    if (u.host === 'avatar' && avatarPath && existsSync(avatarPath)) {
      const res = await net.fetch(pathToFileURL(avatarPath).toString());
      console.log(`[avatar] serving ${avatarPath} (${res.status})`);
      return new Response(res.body, { status: res.status, headers: { ...cors, 'Content-Type': 'model/gltf-binary' } });
    }
    console.log(`[avatar] 404 for ${req.url} (avatarPath=${avatarPath})`);
    return new Response('not found', { status: 404, headers: cors });
  });
  overlay = createOverlay();
  panel = createPanel(overlay);

  tray = new Tray(trayIcon());
  tray.setToolTip('Familiar');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Talk to Wisp', click: showPanel, accelerator: HOTKEYS.summon },
    { label: 'Settings…', click: openSettings },
    { type: 'separator' },
    { label: 'Stop speaking', click: () => brain.send({ type: 'interrupt' }) },
    { label: 'Restart brain', click: () => { brain.stop(); setTimeout(() => brain.start(), 500); } },
    { type: 'separator' },
    { label: 'Quit Familiar', click: () => app.quit() },
  ]));
  tray.on('click', togglePanel);

  globalShortcut.register(HOTKEYS.summon, togglePanel);
  globalShortcut.register(HOTKEYS.kill, () => brain.send({ type: 'kill_switch' }));

  // --- IPC ---------------------------------------------------------------------
  ipcMain.on(IPC.brainSend, (_e, msg) => brain.send(msg));
  ipcMain.on(IPC.overlayHit, (_e, over: boolean) => (overlay as any)?.__setThrough(!over));
  ipcMain.on(IPC.overlayDrag, (_e, { dx, dy }) => {
    if (!overlay) return;
    const [x, y] = overlay.getPosition();
    overlay.setPosition(x + dx, y + dy);
  });
  ipcMain.on(IPC.overlayPlaybackState, (_e, state) => {
    for (const w of BrowserWindow.getAllWindows()) w.webContents.send(IPC.brainMessage, { type: 'shell.playback', state });
  });
  ipcMain.on(IPC.panelHide, () => panel?.hide());
  ipcMain.on(IPC.openSettings, openSettings);
  ipcMain.handle(IPC.getSnapshot, (): ShellSnapshot => ({
    brain: brain.status,
    lastHello: brain.lastHello,
    lastModelsInfo: brain.lastModelsInfo,
    lastState: brain.lastState,
    hotkeys: HOTKEYS,
    version: app.getVersion(),
  }));

  // First run: when the brain reports missing models, open settings so the user can start the download.
  brain.on('message', (msg: any) => {
    if (msg.type === 'hello') {
      avatarPath = msg.persona?.avatar_path ?? null;
      if (isDev) net.fetch('familiar://avatar/current.vrm').then((r) => console.log(`[avatar] main self-test ${r.status}`)).catch((e) => console.log(`[avatar] main self-test failed: ${e}`));
    }
    if (msg.type === 'models.info' && msg.required?.some((r: any) => !r.present)) openSettings();
    if (msg.type === 'error' && msg.fatal) openSettings();
  });

  brain.start();
  if (isDev) setTimeout(showPanel, 1500);
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  brain.stop();
});
app.on('window-all-closed', () => {
  /* tray app: keep running */
});
void join; // keep import for future asset paths
