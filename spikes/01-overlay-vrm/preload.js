const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('overlay', {
  hit: (overAvatar) => ipcRenderer.send('hit', overAvatar),
  fps: (fps) => ipcRenderer.send('fps', fps),
  drag: (dx, dy) => ipcRenderer.send('drag', { dx, dy }),
  quit: () => ipcRenderer.send('quit'),
  onCursor: (cb) => ipcRenderer.on('cursor', (_e, p) => cb(p)),
});
