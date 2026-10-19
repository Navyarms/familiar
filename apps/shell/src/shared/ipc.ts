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
  pickFile: 'shell:pickFile', // invoke(options) -> path | null
  openPath: 'shell:openPath', // (which: 'home'|'personas'|'logs')
  setHotkey: 'shell:setHotkey', // invoke(name, accelerator) -> {ok, error?}
  getStartup: 'shell:getStartup', // invoke -> boolean
  setStartup: 'shell:setStartup', // invoke(boolean)
  getAvatarMode: 'shell:getAvatarMode', // invoke -> AvatarMode
  setAvatarMode: 'shell:setAvatarMode', // invoke(AvatarMode)
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

/** 'always': the avatar stays on screen. 'summoned': it appears when summoned, listening, working or speaking, and hides again when quiet. */
export type AvatarMode = 'always' | 'summoned';

export type ShellSnapshot = {
  brain: BrainStatus;
  lastHello?: unknown;
  lastModelsInfo?: unknown;
  lastState?: unknown;
  hotkeys: { summon: string; talk: string; kill: string };
  avatarMode: AvatarMode;
  version: string;
};

export type OverlayCursor = { x: number; y: number };
