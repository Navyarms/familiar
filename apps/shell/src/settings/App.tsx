import { useEffect, useState } from 'react';

/** Phase 1 settings: status, hardware tier, first-run model download, persona summary, hotkeys. */
export function App() {
  const [snap, setSnap] = useState<any>(null);
  const [info, setInfo] = useState<any>(null);
  const [hello, setHello] = useState<any>(null);
  const [state, setState] = useState<any>(null);
  const [status, setStatus] = useState<any>(null);
  const [progress, setProgress] = useState<any>(null);
  const [errors, setErrors] = useState<string[]>([]);

  useEffect(() => {
    window.familiar.snapshot().then((s: any) => {
      setSnap(s);
      setInfo(s.lastModelsInfo);
      setHello(s.lastHello);
      setState(s.lastState);
      setStatus(s.brain);
    });
    const off = window.familiar.brain.onMessage((m: any) => {
      if (m.type === 'models.info') setInfo(m);
      if (m.type === 'hello') setHello(m);
      if (m.type === 'state') setState(m);
      if (m.type === 'model.download_progress') setProgress(m);
      if (m.type === 'model.ready') setProgress(null);
      if (m.type === 'error') setErrors((xs) => [...xs.slice(-4), `${m.code}: ${m.message}`]);
    });
    const offS = window.familiar.brain.onStatus(setStatus);
    return () => {
      off();
      offS();
    };
  }, []);

  const missing = info?.required?.filter((r: any) => !r.present) ?? [];
  const totalMissing = missing.reduce((a: number, r: any) => a + r.bytes_total, 0);
  const hw = info?.hardware;

  return (
    <div className="settings">
      <h1>Familiar</h1>
      <div className="card">
        <dl className="kv">
          <dt>Brain</dt><dd>{status?.phase ?? '…'}{status?.port ? ` · port ${status.port}` : ''}{status?.restarts ? ` · restarts ${status.restarts}` : ''}</dd>
          <dt>State</dt><dd>{state?.state ?? '…'}{state?.detail ? ` — ${state.detail}` : ''}</dd>
          <dt>Persona</dt><dd>{hello?.persona ? `${hello.persona.name} · wake phrase “${hello.persona.wake_phrase}” · voice ${hello.persona.voice}` : '…'}</dd>
          <dt>Avatar</dt><dd>{hello?.persona?.avatar_path ? <code>{hello.persona.avatar_path}</code> : 'built-in orb (drop an avatar.vrm into the persona folder)'}</dd>
          <dt>Hotkeys</dt><dd>summon <code>{snap?.hotkeys.summon}</code> · kill switch <code>{snap?.hotkeys.kill}</code></dd>
        </dl>
      </div>

      <h2>Hardware and models</h2>
      <div className="card">
        {hw ? (
          <dl className="kv">
            <dt>Tier</dt><dd><strong>{info.tier}</strong> <span className="muted">— {info.tier_reason}</span></dd>
            <dt>CPU</dt><dd>{hw.cpu} · {hw.physical_cores} cores{hw.avx512 ? ' · AVX-512' : hw.avx2 ? ' · AVX2' : ''}</dd>
            <dt>RAM</dt><dd>{hw.ram_gb} GB</dd>
            <dt>GPU</dt><dd>{hw.gpu_name ? `${hw.gpu_name}${hw.vram_gb ? ` · ${hw.vram_gb} GB` : ''}` : 'none detected'}</dd>
          </dl>
        ) : <span className="muted">waiting for the brain…</span>}
        {info && (
          <>
            <table className="items" style={{ marginTop: 12 }}>
              <tbody>
                {info.required.map((r: any) => (
                  <tr key={r.name}>
                    <td>{r.present ? <span className="ok">●</span> : <span className="warn">○</span>} {r.name}</td>
                    <td>{fmtBytes(r.bytes_total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {missing.length > 0 && !progress && (
              <div className="row" style={{ marginTop: 12 }}>
                <span className="grow muted">{missing.length} file(s), {fmtBytes(totalMissing)} to download from Hugging Face and GitHub. Stored in %LOCALAPPDATA%\Familiar\models.</span>
                <button className="primary" onClick={() => window.familiar.brain.send({ type: 'models.download' })}>Download</button>
              </div>
            )}
            {progress && (
              <div style={{ marginTop: 12 }}>
                <div className="row"><span className="grow">{progress.item}</span><span className="muted">{fmtBytes(progress.bytes_done)} / {fmtBytes(progress.bytes_total)} · {fmtBytes(progress.speed_bps)}/s</span></div>
                <div className="progress" style={{ marginTop: 6 }}><div style={{ width: `${(100 * progress.overall_done) / Math.max(1, progress.overall_total)}%` }} /></div>
              </div>
            )}
          </>
        )}
      </div>

      {errors.length > 0 && (
        <>
          <h2>Recent errors</h2>
          <div className="card">{errors.map((e, i) => <div key={i} className="err">{e}</div>)}</div>
        </>
      )}

      <h2>About</h2>
      <div className="card muted">
        Familiar {snap?.version} · everything runs on this PC, nothing leaves it. Persona packs live in <code>%LOCALAPPDATA%\Familiar\personas</code>.
        Edit <code>%LOCALAPPDATA%\Familiar\settings.json</code> for tier override, custom GGUF, threads (UI for these comes in Phase 6).
      </div>
    </div>
  );
}

function fmtBytes(n: number): string {
  if (!n) return '0';
  if (n > 1e9) return (n / 1e9).toFixed(2) + ' GB';
  if (n > 1e6) return (n / 1e6).toFixed(0) + ' MB';
  return (n / 1e3).toFixed(0) + ' kB';
}
