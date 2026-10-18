// Spike 1: transparent, frameless, always-on-top Electron window rendering a VRM.
// Click-through everywhere except over the avatar.
//
// Hit testing: the main process polls the cursor (screen.getCursorScreenPoint) at ~60 Hz,
// converts to window coords and asks the renderer whether that pixel has alpha. The renderer
// answers with 'hit'; we toggle setIgnoreMouseEvents only when the answer changes.
// (Relying on setIgnoreMouseEvents({forward:true}) to keep mousemove flowing proved
// unreliable with synthetic cursor moves, so polling is the robust path.)
const { app, BrowserWindow, ipcMain, screen } = require('electron');
const path = require('path');

let win;
let clickThrough = null; // tri-state so we only call the OS when it changes

function createWindow() {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize;
  const W = 420, H = 640;
  win = new BrowserWindow({
    width: W, height: H,
    x: width - W - 20, y: height - H - 20,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    resizable: false,
    hasShadow: false,
    skipTaskbar: true,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      sandbox: false,
      backgroundThrottling: false, // keep rendering when unfocused
    },
  });
  win.setAlwaysOnTop(true, 'screen-saver'); // above fullscreen-ish windows too
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.loadFile('index.html');

  const setThrough = (wantThrough) => {
    if (wantThrough !== clickThrough) {
      clickThrough = wantThrough;
      win.setIgnoreMouseEvents(wantThrough, { forward: true });
    }
  };
  setThrough(true);

  // 60 Hz cursor poll -> renderer hit test
  setInterval(() => {
    if (!win || win.isDestroyed()) return;
    const c = screen.getCursorScreenPoint();
    const b = win.getBounds();
    const inside = c.x >= b.x && c.x < b.x + b.width && c.y >= b.y && c.y < b.y + b.height;
    if (!inside) { setThrough(true); return; }
    win.webContents.send('cursor', { x: c.x - b.x, y: c.y - b.y });
  }, 16);

  ipcMain.on('hit', (_e, overAvatar) => setThrough(!overAvatar));
  ipcMain.on('fps', (_e, fps) => process.stdout.write(`fps=${fps}\n`));
  ipcMain.on('drag', (_e, { dx, dy }) => {
    const [x, y] = win.getPosition();
    win.setPosition(x + dx, y + dy);
  });
  ipcMain.on('quit', () => app.quit());
}

app.whenReady().then(createWindow);
app.on('window-all-closed', () => app.quit());
