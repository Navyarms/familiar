import { app, BrowserWindow, dialog, globalShortcut, ipcMain, Menu, nativeImage, net, protocol, shell, Tray } from 'electron';
import { join } from 'path';
import { pathToFileURL } from 'url';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs';
import { BrainSupervisor } from './brain';
import { createOverlay, createPanel, createSettings } from './windows';
import { setupUpdater } from './updater';
import { IPC, type AvatarMode, type ShellSnapshot } from '../shared/ipc';

const DEFAULT_HOTKEYS = { summon: 'CommandOrControl+Shift+Space', talk: 'CommandOrControl+Shift+M', kill: 'CommandOrControl+Alt+Escape' };
const DATA_HOME = join(process.env['LOCALAPPDATA'] ?? app.getPath('userData'), 'Familiar');
const SHELL_SETTINGS = join(DATA_HOME, 'shell.json');
type ShellSettings = { hotkeys: typeof DEFAULT_HOTKEYS; avatarMode: AvatarMode };
function loadShellSettings(): ShellSettings {
  try {
    const j = JSON.parse(readFileSync(SHELL_SETTINGS, 'utf-8'));
    return { hotkeys: { ...DEFAULT_HOTKEYS, ...(j.hotkeys ?? {}) }, avatarMode: j.avatarMode === 'summoned' ? 'summoned' : 'always' };
  } catch {
    return { hotkeys: { ...DEFAULT_HOTKEYS }, avatarMode: 'always' };
  }
}
function saveShellSettings(s: ShellSettings): void {
  try {
    mkdirSync(DATA_HOME, { recursive: true });
    writeFileSync(SHELL_SETTINGS, JSON.stringify(s, null, 2));
  } catch (e) {
    console.error('could not save shell settings', e);
  }
}
const shellSettings = loadShellSettings();
const HOTKEYS = shellSettings.hotkeys;
let micMode: string = 'off'; // mirrors the brain's mic.state so the talk hotkey can toggle
const isDev = !app.isPackaged;

let overlay: BrowserWindow | null = null;
let panel: BrowserWindow | null = null;
let settings: BrowserWindow | null = null;
let tray: Tray | null = null;
const brain = new BrainSupervisor(isDev);
let updates: ReturnType<typeof setupUpdater> | null = null;
if (process.env['FAMILIAR_CDP']) app.commandLine.appendSwitch('remote-debugging-port', process.env['FAMILIAR_CDP']);

// Avatar presence. In 'summoned' mode the overlay stays hidden until something happens (summon hotkey, wake
// word, a turn, speech or a reminder) and hides again a few seconds after everything is quiet.
const AVATAR_LINGER_MS = 6000;
let brainState = 'booting';
let speaking = false;
let hideTimer: NodeJS.Timeout | null = null;
function avatarBusy(): boolean {
  return (panel?.isVisible() ?? false) || speaking || micMode === 'listening' || micMode === 'processing' ||
    ['listening', 'thinking', 'speaking', 'executing', 'needs_confirm'].includes(brainState);
}
function showAvatar(): void {
  if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
  if (overlay && !overlay.isDestroyed() && !overlay.isVisible()) overlay.showInactive();
}
function settleAvatar(): void {
  if (shellSettings.avatarMode !== 'summoned' || avatarBusy()) return showAvatar();
  if (hideTimer) return;
  hideTimer = setTimeout(() => {
    hideTimer = null;
    if (shellSettings.avatarMode === 'summoned' && !avatarBusy() && overlay && !overlay.isDestroyed()) overlay.hide();
  }, AVATAR_LINGER_MS);
}
function setAvatarMode(mode: AvatarMode): void {
  shellSettings.avatarMode = mode;
  saveShellSettings(shellSettings);
  settleAvatar();
  rebuildTray();
}

function rebuildTray(): void {
  tray?.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open chat', click: showPanel, accelerator: HOTKEYS.summon },
    { label: 'Talk (push to talk)', click: toggleTalk, accelerator: HOTKEYS.talk },
    { label: 'Avatar always on screen', type: 'checkbox', checked: shellSettings.avatarMode === 'always', click: (item) => setAvatarMode(item.checked ? 'always' : 'summoned') },
    { label: 'Settings…', click: openSettings },
    { type: 'separator' },
    { label: 'Stop speaking', click: () => brain.send({ type: 'interrupt' }) },
    { label: 'Restart brain', click: () => { brain.stop(); setTimeout(() => brain.start(), 500); } },
    { label: 'Check for updates…', click: async () => { const r = await updates?.check(); tray?.displayBalloon?.({ title: 'Familiar', content: String(r ?? '') }); } },
    { type: 'separator' },
    { label: 'Quit Familiar', click: () => app.quit() },
  ]));
}

if (!app.requestSingleInstanceLock()) app.quit();
app.on('second-instance', () => showPanel());

// familiar://avatar/current.vrm serves the active persona's model to the overlay. Renderers cannot fetch
// file:// URLs (dev server origin is http://localhost), and this keeps the renderer free of absolute paths.
protocol.registerSchemesAsPrivileged([{ scheme: 'familiar', privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, bypassCSP: true, stream: true } }]);
let avatarPath: string | null = null;

function showPanel(): void {
  if (!panel || !overlay) return;
  (panel as any).__placeNear();
  showAvatar();
  panel.show();
  panel.focus();
  panel.webContents.send(IPC.panelShown);
  brain.send({ type: 'summon' });
}

function togglePanel(): void {
  if (panel?.isVisible()) panel.hide();
  else showPanel();
}

/** Talk hotkey: start capturing right away (no wake word); press again to stop early. */
function toggleTalk(): void {
  if (micMode === 'listening') brain.send({ type: 'ptt.stop' });
  else { showAvatar(); brain.send({ type: 'ptt.start' }); }
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
  if (shellSettings.avatarMode === 'summoned') overlay.hide();
  panel.on('show', showAvatar);
  panel.on('hide', settleAvatar);

  updates = setupUpdater((s) => console.log('[updater]', s));
  tray = new Tray(trayIcon());
  tray.setToolTip('Familiar');
  rebuildTray();
  tray.on('click', togglePanel);

  const actions = { summon: togglePanel, talk: toggleTalk, kill: () => brain.send({ type: 'kill_switch' }) } as const;
  const registerHotkeys = () => {
    globalShortcut.unregisterAll();
    for (const k of Object.keys(actions) as (keyof typeof actions)[]) {
      if (!globalShortcut.register(HOTKEYS[k], actions[k])) console.warn(`hotkey ${k} (${HOTKEYS[k]}) could not be registered`);
    }
  };
  registerHotkeys();
  ipcMain.handle(IPC.setHotkey, (_e, name: keyof typeof actions, accel: string) => {
    if (!(name in actions)) return { ok: false, error: 'unknown hotkey' };
    const old = HOTKEYS[name];
    globalShortcut.unregister(old);
    try {
      if (!globalShortcut.register(accel, actions[name])) throw new Error('that combination is taken or invalid');
    } catch (e) {
      globalShortcut.register(old, actions[name]);
      return { ok: false, error: String((e as Error).message ?? e) };
    }
    HOTKEYS[name] = accel;
    saveShellSettings(shellSettings);
    return { ok: true };
  });
  ipcMain.handle(IPC.getStartup, () => app.getLoginItemSettings().openAtLogin);
  ipcMain.handle(IPC.setStartup, (_e, enabled: boolean) => app.setLoginItemSettings({ openAtLogin: enabled, args: ['--hidden'] }));
  ipcMain.handle(IPC.getAvatarMode, () => shellSettings.avatarMode);
  ipcMain.handle(IPC.setAvatarMode, (_e, mode: AvatarMode) => setAvatarMode(mode === 'summoned' ? 'summoned' : 'always'));
  ipcMain.handle(IPC.pickFile, async (_e, options: { title?: string; filters?: Electron.FileFilter[] }) => {
    const r = await dialog.showOpenDialog({ title: options?.title, filters: options?.filters, properties: ['openFile'] });
    return r.canceled ? null : r.filePaths[0];
  });
  ipcMain.on(IPC.openPath, (_e, which: 'home' | 'personas' | 'logs') => {
    const p = which === 'home' ? DATA_HOME : join(DATA_HOME, which);
    mkdirSync(p, { recursive: true });
    void shell.openPath(p);
  });

  // --- IPC ---------------------------------------------------------------------
  ipcMain.on(IPC.brainSend, (_e, msg) => brain.send(msg));
  ipcMain.on(IPC.overlayHit, (_e, over: boolean) => (overlay as any)?.__setThrough(!over));
  ipcMain.on(IPC.overlayDrag, (_e, { dx, dy }) => {
    if (!overlay) return;
    const [x, y] = overlay.getPosition();
    overlay.setPosition(x + dx, y + dy);
  });
  ipcMain.on(IPC.overlayPlaybackState, (_e, state) => {
    speaking = state === 'speaking';
    settleAvatar();
    brain.send({ type: 'playback', speaking }); // barge-in + speaking state live in the brain
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
    avatarMode: shellSettings.avatarMode,
    version: app.getVersion(),
  }));

  // First run: when the brain reports missing models, open settings so the user can start the download.
  brain.on('message', (msg: any) => {
    if (msg.type === 'mic.state') { micMode = msg.mode; settleAvatar(); }
    if (msg.type === 'state') { brainState = msg.state; settleAvatar(); }
    if (msg.type === 'hello') {
      avatarPath = msg.persona?.avatar_path ?? null;
      if (isDev) net.fetch('familiar://avatar/current.vrm').then((r) => console.log(`[avatar] main self-test ${r.status}`)).catch((e) => console.log(`[avatar] main self-test failed: ${e}`));
    }
    if (msg.type === 'models.info' && msg.required?.some((r: any) => !r.present)) openSettings();
    if (msg.type === 'error' && msg.fatal) openSettings();
  });

  brain.start();
  if (isDev && !process.argv.includes('--hidden')) setTimeout(showPanel, 1500);
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  brain.stop();
});
app.on('window-all-closed', () => {
  /* tray app: keep running */
});
void join; // keep import for future asset paths
