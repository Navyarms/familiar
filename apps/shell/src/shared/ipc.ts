/** IPC channel names and the shape of what the preload exposes to renderers. */

export const IPC = {
  // renderer -> main
  brainSend: 'brain:send', // forward a ShellMessage to the brain
  overlayHit: 'overlay:hit', // boolean: cursor is over avatar pixels
  overlayDrag: 'overlay:drag',
  overlayPlaybackState: 'overlay:playback', // 'speaking' | 'idle'
  panelHide: 'panel:hide',
  openSettings: 'settings:open',
  getSnapshot: 'shell:snapshot', // invoke -> ShellSnapshot
  // main -> renderer
  brainMessage: 'brain:message', // any BrainMessage (JSON)
  brainStatus: 'brain:status', // BrainStatus
  overlayCursor: 'overlay:cursor', // {x,y} window-relative
  panelShown: 'panel:shown',
} as const;

export type BrainStatus = {
  phase: 'starting' | 'connected' | 'restarting' | 'crashed';
  pid?: number;
  port?: number;
  restarts: number;
  lastError?: string;
};

export type ShellSnapshot = {
  brain: BrainStatus;
  lastHello?: unknown;
  lastModelsInfo?: unknown;
  lastState?: unknown;
  hotkeys: { summon: string; talk: string; kill: string };
  version: string;
};

export type OverlayCursor = { x: number; y: number };
