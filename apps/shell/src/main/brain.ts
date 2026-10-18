/**
 * Brain supervisor: spawns the Python brain (PyInstaller exe in production, `uv run` in dev), reads
 * `READY <port>`, connects the WebSocket with a random token, restarts on crash with backoff, and
 * fans brain messages out to every renderer.
 */
import { app, BrowserWindow } from 'electron';
import { spawn, type ChildProcess } from 'child_process';
import { randomBytes } from 'crypto';
import { existsSync } from 'fs';
import { join, resolve } from 'path';
import { EventEmitter } from 'events';
import type { BrainStatus } from '../shared/ipc';
import { IPC } from '../shared/ipc';

export class BrainSupervisor extends EventEmitter {
  private proc: ChildProcess | null = null;
  private ws: WebSocket | null = null;
  private token = randomBytes(24).toString('hex');
  private stopping = false;
  private backoffMs = 1000;
  status: BrainStatus = { phase: 'starting', restarts: 0 };
  lastHello: unknown;
  lastModelsInfo: unknown;
  lastState: unknown;
  private outbox: string[] = [];

  constructor(private readonly dev: boolean) {
    super();
  }

  /** Where the brain lives: <resources>/brain/familiar-brain.exe when packaged, else `uv run` from the repo. */
  private command(): { cmd: string; args: string[]; cwd: string } {
    const packaged = join(process.resourcesPath ?? '', 'brain', 'familiar-brain.exe');
    if (app.isPackaged && existsSync(packaged)) {
      return { cmd: packaged, args: ['--token', this.token], cwd: join(process.resourcesPath, 'brain') };
    }
    const repo = resolve(__dirname, '..', '..', '..', '..'); // apps/shell/out/main -> repo root
    const brainDir = join(repo, 'brain');
    const exe = join(brainDir, 'dist', 'familiar-brain', 'familiar-brain.exe');
    if (existsSync(exe) && !this.dev) return { cmd: exe, args: ['--token', this.token], cwd: brainDir };
    return { cmd: 'uv', args: ['run', '--project', brainDir, 'familiar-brain', '--token', this.token, ...(this.dev ? ['--dev'] : [])], cwd: brainDir };
  }

  start(): void {
    this.stopping = false;
    const { cmd, args, cwd } = this.command();
    console.log(`[brain] spawn ${cmd} ${args.filter((a) => a !== this.token).join(' ')}`);
    this.proc = spawn(cmd, args, { cwd, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true, shell: process.platform === 'win32' && cmd === 'uv' });
    this.setStatus({ phase: 'starting', pid: this.proc.pid });
    let buf = '';
    this.proc.stdout?.on('data', (d) => {
      buf += d.toString();
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        const m = /^READY (\d+)$/.exec(line);
        if (m) this.connect(+m[1]);
        else if (line) console.log('[brain]', line);
      }
    });
    this.proc.stderr?.on('data', (d) => process.stderr.write(`[brain] ${d}`));
    this.proc.on('exit', (code, signal) => {
      console.log(`[brain] exited code=${code} signal=${signal}`);
      this.ws?.close();
      this.ws = null;
      this.proc = null;
      if (this.stopping) return;
      this.setStatus({ phase: 'restarting', restarts: this.status.restarts + 1, lastError: `exited with code ${code}` });
      setTimeout(() => this.start(), this.backoffMs);
      this.backoffMs = Math.min(this.backoffMs * 2, 30_000);
    });
  }

  private connect(port: number, attempt = 0): void {
    const url = `ws://127.0.0.1:${port}/ws?token=${this.token}`;
    const ws = new WebSocket(url);
    let opened = false;
    ws.onopen = () => {
      opened = true;
      console.log(`[brain] websocket connected on ${port}`);
      this.backoffMs = 1000;
      this.setStatus({ phase: 'connected', port });
      for (const m of this.outbox.splice(0)) ws.send(m);
    };
    ws.onmessage = (e) => {
      let msg: any;
      try {
        msg = JSON.parse(String(e.data));
      } catch {
        return;
      }
      if (msg.type === 'hello') this.lastHello = msg;
      if (msg.type === 'models.info') this.lastModelsInfo = msg;
      if (msg.type === 'state') this.lastState = msg;
      this.emit('message', msg);
      for (const w of BrowserWindow.getAllWindows()) if (!w.isDestroyed()) w.webContents.send(IPC.brainMessage, msg);
    };
    ws.onclose = (e) => {
      if (this.ws === ws) this.ws = null;
      console.log(`[brain] websocket closed ${e.code}`);
      // Never opened: the brain may still be binding its socket, or we lost a race. Retry briefly.
      if (!opened && !this.stopping && this.proc && attempt < 30) setTimeout(() => this.connect(port, attempt + 1), 300);
      else if (opened && !this.stopping && this.proc && e.code !== 4401) setTimeout(() => this.connect(port, 0), 1000);
    };
    ws.onerror = () => {
      /* onclose follows */
    };
    this.ws = ws;
  }

  send(msg: unknown): void {
    const data = JSON.stringify(msg);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(data);
    else if (this.outbox.length < 20) this.outbox.push(data);
  }

  stop(): void {
    this.stopping = true;
    this.ws?.close();
    this.ws = null;
    if (this.proc) {
      try {
        this.proc.stdin?.end(); // the brain exits on stdin EOF
      } catch {
        /* ignore */
      }
      const p = this.proc;
      setTimeout(() => {
        if (p.exitCode === null) p.kill();
      }, 3000);
    }
  }

  private setStatus(patch: Partial<BrainStatus>): void {
    this.status = { ...this.status, ...patch };
    this.emit('status', this.status);
    for (const w of BrowserWindow.getAllWindows()) if (!w.isDestroyed()) w.webContents.send(IPC.brainStatus, this.status);
  }
}
