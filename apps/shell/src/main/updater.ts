/**
 * Auto-update through GitHub Releases (electron-updater). Only active in packaged builds. Checks once at startup
 * and every 6 hours; downloads in the background and installs on quit. The tray menu can also trigger a check.
 */
import { app, Notification } from 'electron';

let updater: typeof import('electron-updater').autoUpdater | null = null;

export function setupUpdater(onStatus: (s: string) => void): { check: () => Promise<string> } {
  if (!app.isPackaged) return { check: async () => 'updates are disabled in development' };
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    updater = (require('electron-updater') as typeof import('electron-updater')).autoUpdater;
  } catch (e) {
    console.warn('electron-updater unavailable', e);
    return { check: async () => 'updater unavailable' };
  }
  const u = updater;
  u.autoDownload = true;
  u.autoInstallOnAppQuit = true;
  u.on('checking-for-update', () => onStatus('checking for updates'));
  u.on('update-available', (i) => onStatus(`downloading update ${i.version}`));
  u.on('update-not-available', () => onStatus('up to date'));
  u.on('error', (e) => onStatus(`update check failed: ${e.message}`));
  u.on('update-downloaded', (i) => {
    onStatus(`update ${i.version} ready; installs when Familiar quits`);
    new Notification({ title: 'Familiar update ready', body: `Version ${i.version} will install the next time you quit Familiar.` }).show();
  });
  const check = async () => {
    try {
      const r = await u.checkForUpdates();
      return r?.updateInfo?.version ? `latest: ${r.updateInfo.version}` : 'up to date';
    } catch (e) {
      return `update check failed: ${(e as Error).message}`;
    }
  };
  setTimeout(() => void check(), 20_000);
  setInterval(() => void check(), 6 * 60 * 60 * 1000);
  return { check };
}
