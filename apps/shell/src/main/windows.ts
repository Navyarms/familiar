import { BrowserWindow, screen, shell } from 'electron';
import { join } from 'path';
import { IPC } from '../shared/ipc';

const preload = join(__dirname, '../preload/index.js');
const isDev = !!process.env['ELECTRON_RENDERER_URL'];

function load(win: BrowserWindow, page: 'overlay' | 'panel' | 'settings'): void {
  if (isDev) win.loadURL(`${process.env['ELECTRON_RENDERER_URL']}/${page}/index.html`);
  else win.loadFile(join(__dirname, `../renderer/${page}/index.html`));
}

export const OVERLAY_W = 400;
export const OVERLAY_H = 600;

export function createOverlay(): BrowserWindow {
  const { workArea } = screen.getPrimaryDisplay();
  const win = new BrowserWindow({
    width: OVERLAY_W,
    height: OVERLAY_H,
    x: workArea.x + workArea.width - OVERLAY_W - 16,
    y: workArea.y + workArea.height - OVERLAY_H - 8,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    resizable: false,
    hasShadow: false,
    skipTaskbar: true,
    focusable: false,
    backgroundColor: '#00000000',
    title: 'Familiar overlay',
    webPreferences: { preload, contextIsolation: true, sandbox: false, backgroundThrottling: false },
  });
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.setIgnoreMouseEvents(true, { forward: true });
  load(win, 'overlay');

  // Hit testing: poll the cursor (renderer mouse events do not arrive while click-through is on).
  let clickThrough = true;
  const setThrough = (v: boolean) => {
    if (v === clickThrough) return;
    clickThrough = v;
    win.setIgnoreMouseEvents(v, { forward: true });
  };
  const timer = setInterval(() => {
    if (win.isDestroyed()) return clearInterval(timer);
    const c = screen.getCursorScreenPoint();
    const b = win.getBounds();
    const inside = c.x >= b.x && c.x < b.x + b.width && c.y >= b.y && c.y < b.y + b.height;
    if (!inside) return setThrough(true);
    win.webContents.send(IPC.overlayCursor, { x: c.x - b.x, y: c.y - b.y });
  }, 16);
  (win as any).__setThrough = setThrough;
  return win;
}

export const PANEL_W = 420;
export const PANEL_H = 520;

export function createPanel(overlay: BrowserWindow): BrowserWindow {
  const win = new BrowserWindow({
    width: PANEL_W,
    height: PANEL_H,
    show: false,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: true,
    skipTaskbar: true,
    hasShadow: true,
    backgroundColor: '#00000000',
    title: 'Familiar',
    webPreferences: { preload, contextIsolation: true, sandbox: false },
  });
  win.setAlwaysOnTop(true, 'screen-saver');
  load(win, 'panel');
  win.on('blur', () => {
    // auto-hide when the user clicks elsewhere, unless pinned (renderer can call panel.hide itself)
    if (!win.isDestroyed() && !(win as any).__pinned) win.hide();
  });
  (win as any).__placeNear = () => {
    const b = overlay.getBounds();
    const { workArea } = screen.getDisplayMatching(b);
    let x = b.x - PANEL_W - 8;
    if (x < workArea.x) x = b.x + b.width + 8;
    const y = Math.max(workArea.y, Math.min(b.y + b.height - PANEL_H, workArea.y + workArea.height - PANEL_H));
    win.setPosition(Math.round(x), Math.round(y));
  };
  return win;
}

export function createSettings(): BrowserWindow {
  const win = new BrowserWindow({
    width: 760,
    height: 620,
    title: 'Familiar settings',
    autoHideMenuBar: true,
    backgroundColor: '#14151a',
    webPreferences: { preload, contextIsolation: true, sandbox: false },
  });
  load(win, 'settings');
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
  return win;
}
