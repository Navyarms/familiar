import { contextBridge, ipcRenderer } from 'electron';
import { IPC, type AvatarMode, type BrainStatus, type OverlayCursor, type ShellSnapshot } from '../shared/ipc';

type Unsub = () => void;
const on = <T,>(channel: string, cb: (payload: T) => void): Unsub => {
  const h = (_e: Electron.IpcRendererEvent, p: T) => cb(p);
  ipcRenderer.on(channel, h);
  return () => ipcRenderer.removeListener(channel, h);
};

const api = {
  brain: {
    send: (msg: unknown) => ipcRenderer.send(IPC.brainSend, msg),
    onMessage: (cb: (msg: any) => void) => on<any>(IPC.brainMessage, cb),
    onStatus: (cb: (s: BrainStatus) => void) => on<BrainStatus>(IPC.brainStatus, cb),
  },
  overlay: {
    hit: (over: boolean) => ipcRenderer.send(IPC.overlayHit, over),
    drag: (dx: number, dy: number) => ipcRenderer.send(IPC.overlayDrag, { dx, dy }),
    onCursor: (cb: (c: OverlayCursor) => void) => on<OverlayCursor>(IPC.overlayCursor, cb),
    playback: (state: 'speaking' | 'idle') => ipcRenderer.send(IPC.overlayPlaybackState, state),
  },
  panel: {
    hide: () => ipcRenderer.send(IPC.panelHide),
    onShown: (cb: () => void) => on<void>(IPC.panelShown, cb),
  },
  openSettings: () => ipcRenderer.send(IPC.openSettings),
  snapshot: (): Promise<ShellSnapshot> => ipcRenderer.invoke(IPC.getSnapshot),
  shell: {
    pickFile: (options: { title?: string; filters?: { name: string; extensions: string[] }[] }): Promise<string | null> => ipcRenderer.invoke(IPC.pickFile, options),
    openPath: (which: 'home' | 'personas' | 'logs') => ipcRenderer.send(IPC.openPath, which),
    setHotkey: (name: 'summon' | 'talk' | 'kill', accelerator: string): Promise<{ ok: boolean; error?: string }> => ipcRenderer.invoke(IPC.setHotkey, name, accelerator),
    getStartup: (): Promise<boolean> => ipcRenderer.invoke(IPC.getStartup),
    setStartup: (enabled: boolean): Promise<void> => ipcRenderer.invoke(IPC.setStartup, enabled),
    getAvatarMode: (): Promise<AvatarMode> => ipcRenderer.invoke(IPC.getAvatarMode),
    setAvatarMode: (mode: AvatarMode): Promise<void> => ipcRenderer.invoke(IPC.setAvatarMode, mode),
  },
};

contextBridge.exposeInMainWorld('familiar', api);
export type FamiliarApi = typeof api;
