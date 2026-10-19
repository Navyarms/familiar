import { useEffect, useMemo, useState } from 'react';

/**
 * Settings window. Tabs: Persona · Voice · Hotkeys & mic · Models · Privacy & memory · Tools.
 * Brain-side state comes through settings.get/settings.update and the persona/voice/device messages;
 * hotkeys, startup and file dialogs are shell-side (IPC).
 */
export function App() {
  const [snap, setSnap] = useState<any>(null);
  const [info, setInfo] = useState<any>(null);
  const [hello, setHello] = useState<any>(null);
  const [state, setState] = useState<any>(null);
  const [status, setStatus] = useState<any>(null);
  const [progress, setProgress] = useState<any>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [tools, setTools] = useState<any[]>([]);
  const [memory, setMemory] = useState<{ items: any[]; counts: Record<string, number>; query: string } | null>(null);
  const [memQuery, setMemQuery] = useState('');
  const [settings, setSettings] = useState<any>(null);
  const [personas, setPersonas] = useState<any[]>([]);
  const [detail, setDetail] = useState<{ id: string; prompt: string } | null>(null);
  const [voices, setVoices] = useState<{ voices: string[]; current: string } | null>(null);
  const [devices, setDevices] = useState<{ devices: any[]; current: any } | null>(null);
  const [hotkeys, setHotkeys] = useState<{ summon: string; talk: string; kill: string } | null>(null);
  const [startup, setStartup] = useState<boolean | null>(null);
  const [avatarMode, setAvatarModeState] = useState<'always' | 'summoned'>('always');
  const [userName, setUserName] = useState('');
  const [userSpoken, setUserSpoken] = useState('');
  useEffect(() => { if (settings) { setUserName(settings.user_name ?? ''); setUserSpoken(settings.user_name_spoken ?? ''); } }, [settings?.user_name, settings?.user_name_spoken]);
  const setAvatarMode = async (m: 'always' | 'summoned') => { setAvatarModeState(m); await window.familiar.shell.setAvatarMode(m); };
  const [tab, setTab] = useState<'general' | 'voice' | 'input' | 'models' | 'privacy' | 'tools'>('general');

  const send = (m: any) => window.familiar.brain.send(m);
  const update = (patch: Record<string, unknown>) => send({ type: 'settings.update', settings: patch });

  useEffect(() => {
    let detailId: string | null = null;
    window.familiar.snapshot().then((s: any) => {
      setSnap(s); setInfo(s.lastModelsInfo); setHello(s.lastHello); setState(s.lastState); setStatus(s.brain); setHotkeys(s.hotkeys);
    });
    window.familiar.shell.getStartup().then(setStartup);
    window.familiar.shell.getAvatarMode().then(setAvatarModeState);
    const off = window.familiar.brain.onMessage((m: any) => {
      if (m.type === 'models.info') setInfo(m);
      if (m.type === 'hello') { setHello(m); send({ type: 'personas.list' }); send({ type: 'voices.list' }); }
      if (m.type === 'state') setState(m);
      if (m.type === 'model.download_progress') setProgress(m);
      if (m.type === 'model.ready') { setProgress(null); send({ type: 'voices.list' }); send({ type: 'devices.list' }); }
      if (m.type === 'error') setErrors((xs) => [...xs.slice(-4), `${m.code}: ${m.message}`]);
      if (m.type === 'tools.info') setTools(m.tools);
      if (m.type === 'memory.items') setMemory({ items: m.items, counts: m.counts, query: m.query ?? '' });
      if (m.type === 'settings.items') setSettings(m.settings);
      if (m.type === 'personas.items') { setPersonas(m.personas); if (m.prompt != null && detailId) setDetail({ id: detailId, prompt: m.prompt }); }
      if (m.type === 'voices.items') setVoices({ voices: m.voices, current: m.current });
      if (m.type === 'devices.items') setDevices({ devices: m.devices, current: m.current });
    });
    (window as any).__setDetailId = (id: string | null) => { detailId = id; };
    const offS = window.familiar.brain.onStatus(setStatus);
    send({ type: 'settings.get' }); send({ type: 'personas.list' }); send({ type: 'voices.list' }); send({ type: 'devices.list' });
    send({ type: 'memory.list', query: '', limit: 100 });
    return () => { off(); offS(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openEditor = (id: string) => { (window as any).__setDetailId(id); setDetail({ id, prompt: '' }); send({ type: 'personas.list', detail_id: id }); };
  const hw = info?.hardware;
  const missing = info?.required?.filter((r: any) => !r.present) ?? [];
  const totalMissing = missing.reduce((a: number, r: any) => a + r.bytes_total, 0);
  const active = personas.find((p) => p.active);

  return (
    <div className="settings">
      <div className="row" style={{ marginBottom: 12 }}>
        <h1 style={{ margin: 0 }} className="grow">Familiar</h1>
        <span className={`dot ${state?.state ?? ''}`} />
        <span className="muted" style={{ fontSize: 12 }}>{status?.phase ?? '…'} · {state?.state ?? '…'}{state?.detail ? ` — ${state.detail}` : ''}</span>
      </div>
      <nav className="tabs">
        {(['general', 'voice', 'input', 'models', 'privacy', 'tools'] as const).map((t) => (
          <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>{{ general: 'Persona', voice: 'Voice', input: 'Hotkeys & mic', models: 'Models', privacy: 'Privacy & memory', tools: 'Tools' }[t]}</button>
        ))}
      </nav>

      {tab === 'general' && (
        <>
          <h2>You</h2>
          <div className="card">
            <dl className="kv">
              <dt>Call me</dt>
              <dd className="row">
                <input className="grow" placeholder="your name (optional)" value={userName} onChange={(e) => setUserName(e.target.value)} onBlur={() => update({ user_name: userName.trim() || null })} onKeyDown={(e) => { if (e.key === 'Enter') update({ user_name: userName.trim() || null }); }} />
              </dd>
              <dt>Say it as</dt>
              <dd className="row">
                <input className="grow" placeholder="only if it sounds wrong: spell it the way it sounds, e.g. “Dillon”" value={userSpoken} onChange={(e) => setUserSpoken(e.target.value)} onBlur={() => update({ user_name_spoken: userSpoken.trim() || null })} onKeyDown={(e) => { if (e.key === 'Enter') update({ user_name_spoken: userSpoken.trim() || null }); }} />
                <button disabled={!userName.trim()} onClick={() => { update({ user_name: userName.trim() || null, user_name_spoken: userSpoken.trim() || null }); send({ type: 'voice.preview', voice: voices?.current ?? active?.voice ?? 'af_heart', speed: 1.0, text: `Hi ${userName.trim()}, good to see you.` }); }}>▶ Hear it</button>
              </dd>
            </dl>
            <p className="muted" style={{ marginBottom: 0 }}>{active?.name ?? 'Wisp'} will use your name now and then. The spelling only changes how it is pronounced, never how it is written.</p>
          </div>
          <h2>Persona</h2>
          <div className="card">
            {personas.length === 0 ? <span className="muted">waiting for the brain…</span> : (
              <table className="items">
                <tbody>
                  {personas.map((p) => (
                    <tr key={p.id}>
                      <td><strong>{p.name}</strong> <span className="muted">· “{p.wake_phrase}” · {p.voice}{p.has_avatar ? '' : ' · orb (no avatar.vrm)'}{p.user_pack ? '' : ' · built in'}</span></td>
                      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                        {p.active ? <span className="ok">active</span> : <button onClick={() => update({ persona_id: p.id })}>Use</button>}{' '}
                        <button onClick={() => openEditor(p.id)}>Edit</button>{' '}
                        {!p.user_pack && <button onClick={() => { const n = prompt('Name for your copy:', p.name + ' (mine)'); if (n) send({ type: 'persona.duplicate', persona_id: p.id, new_id: n, new_name: n }); }}>Duplicate</button>}
                        {p.user_pack && !p.active && <button onClick={() => { if (confirm(`Delete the persona “${p.name}”?`)) send({ type: 'persona.delete', persona_id: p.id }); }}>Delete</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="row" style={{ marginTop: 10 }}>
              <span className="muted grow">Packs live in <code>%LOCALAPPDATA%\Familiar\personas</code>. Import a .zip or folder with persona.json, prompt.md, expressions.json and optionally avatar.vrm.</span>
              <button onClick={async () => { const p = await window.familiar.shell.pickFile({ title: 'Import persona pack', filters: [{ name: 'Persona pack', extensions: ['zip', 'json'] }] }); if (p) send({ type: 'persona.import', path: p.toLowerCase().endsWith('persona.json') ? p.slice(0, -'persona.json'.length) : p }); }}>Import…</button>
              <button onClick={() => window.familiar.shell.openPath('personas')}>Open folder</button>
            </div>
          </div>
          {detail && <PersonaEditor detail={detail} personas={personas} voices={voices} onClose={() => { setDetail(null); (window as any).__setDetailId(null); }} />}
          <h2>Avatar</h2>
          <div className="card">
            <dl className="kv">
              <dt>Current</dt><dd>{hello?.persona?.avatar_path ? <code>{hello.persona.avatar_path}</code> : 'built-in orb'}</dd>
              <dt>Override</dt>
              <dd className="row">
                <code className="grow" style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{settings?.avatar_override || '(none: uses the pack’s avatar.vrm)'}</code>
                <button onClick={async () => { const p = await window.familiar.shell.pickFile({ title: 'Choose a VRM', filters: [{ name: 'VRM model', extensions: ['vrm'] }] }); if (p) update({ avatar_override: p }); }}>Choose VRM…</button>
                {settings?.avatar_override && <button onClick={() => update({ avatar_override: null })}>Clear</button>}
              </dd>
            </dl>
            <p className="muted">Any VRM 0.x or 1.0 you have the rights to use (VRoid Studio exports work). Lip sync and expressions map automatically.</p>
            <h3 style={{ margin: '8px 0 4px' }}>On screen</h3>
            <label className="row"><input type="radio" name="avatarMode" checked={avatarMode === 'always'} onChange={() => setAvatarMode('always')} /> Always</label>
            <label className="row"><input type="radio" name="avatarMode" checked={avatarMode === 'summoned'} onChange={() => setAvatarMode('summoned')} /> Only when summoned: appears for the hotkey, wake word, replies and reminders, and hides a few seconds after going quiet</label>
          </div>
        </>
      )}

      {tab === 'voice' && (
        <>
          <h2>Voice</h2>
          <div className="card">
            {!voices || voices.voices.length === 0 ? <span className="muted">waiting for the voice engine…</span> : <VoicePicker voices={voices} active={active} />}
          </div>
        </>
      )}

      {tab === 'input' && (
        <>
          <h2>Hotkeys</h2>
          <div className="card">
            {hotkeys && (['summon', 'talk', 'kill'] as const).map((k) => (
              <div className="row" key={k} style={{ marginBottom: 8 }}>
                <span style={{ width: 160 }}>{{ summon: 'Open chat panel', talk: 'Talk (push to talk)', kill: 'Kill switch' }[k]}</span>
                <HotkeyField value={hotkeys[k]} onChange={async (v) => { const res = await window.familiar.shell.setHotkey(k, v); if (res.ok) setHotkeys({ ...hotkeys, [k]: v }); else alert(res.error); }} />
              </div>
            ))}
            <p className="muted" style={{ margin: '6px 0 0' }}>Electron accelerator names, e.g. <code>CommandOrControl+Shift+Space</code>, <code>Alt+F1</code>.</p>
          </div>
          <h2>Startup</h2>
          <div className="card row">
            <label className="grow"><input type="checkbox" checked={!!startup} onChange={async (e) => { await window.familiar.shell.setStartup(e.target.checked); setStartup(e.target.checked); }} /> Start Familiar when I sign in to Windows</label>
          </div>
          <h2>Microphone</h2>
          <div className="card">
            {settings && (
              <dl className="kv">
                <dt>Voice input</dt><dd><label><input type="checkbox" checked={!!settings.voice_enabled} onChange={(e) => update({ voice_enabled: e.target.checked })} /> enabled</label></dd>
                <dt>Device</dt>
                <dd>
                  <select value={settings.mic_device ?? ''} onChange={(e) => update({ mic_device: e.target.value === '' ? null : (isNaN(+e.target.value) ? e.target.value : +e.target.value) })}>
                    <option value="">System default</option>
                    {(devices?.devices ?? []).map((d) => <option key={d.index} value={d.index}>{d.name} ({d.hostapi}){d.default ? ' · default' : ''}</option>)}
                  </select>
                </dd>
                <dt>Wake word</dt><dd><label><input type="checkbox" checked={!!settings.wake_word} onChange={(e) => update({ wake_word: e.target.checked })} /> listen for “{active?.wake_phrase ?? 'hey wisp'}”</label></dd>
                <dt>Interrupt on any speech</dt><dd><label><input type="checkbox" checked={!!settings.barge_in_vad} onChange={(e) => update({ barge_in_vad: e.target.checked })} /> for headsets (speakers feed Wisp’s own voice back in)</label></dd>
                <dt>Wake sensitivity</dt><dd className="row"><input type="range" min={0.1} max={0.5} step={0.05} value={settings.kws_threshold ?? 0.25} onChange={(e) => update({ kws_threshold: +e.target.value })} /><span className="muted">{settings.kws_threshold} (lower = easier to trigger)</span></dd>
              </dl>
            )}
          </div>
        </>
      )}

      {tab === 'models' && (
        <>
          <h2>Hardware and models</h2>
          <div className="card">
            {hw ? (
              <dl className="kv">
                <dt>Tier</dt>
                <dd className="row">
                  <strong>{info.tier}</strong> <span className="muted">— {info.tier_reason}</span>
                  <select value={settings?.tier_override ?? ''} onChange={(e) => update({ tier_override: e.target.value || null })} style={{ marginLeft: 'auto' }}>
                    <option value="">auto</option>
                    {['T0', 'T1', 'T2', 'T3', 'T4'].map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </dd>
                <dt>CPU</dt><dd>{hw.cpu} · {hw.physical_cores} cores{hw.avx512 ? ' · AVX-512' : hw.avx2 ? ' · AVX2' : ''}</dd>
                <dt>RAM</dt><dd>{hw.ram_gb} GB</dd>
                <dt>GPU</dt><dd>{hw.gpu_name ? `${hw.gpu_name}${hw.vram_gb ? ` · ${hw.vram_gb} GB` : ''}` : 'none detected'}</dd>
                <dt>Custom chat model</dt>
                <dd className="row">
                  <code className="grow" style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{settings?.custom_chat_gguf || '(tier default)'}</code>
                  <button onClick={async () => { const p = await window.familiar.shell.pickFile({ title: 'Choose a GGUF', filters: [{ name: 'GGUF', extensions: ['gguf'] }] }); if (p) update({ custom_chat_gguf: p }); }}>Choose GGUF…</button>
                  {settings?.custom_chat_gguf && <button onClick={() => update({ custom_chat_gguf: null, custom_mmproj_gguf: null })}>Clear</button>}
                </dd>
                <dt>Context</dt><dd className="row"><input type="number" min={2048} max={32768} step={1024} value={settings?.llm_ctx ?? 8192} onChange={(e) => update({ llm_ctx: +e.target.value })} style={{ width: 100 }} /><span className="muted">tokens (tool schemas use ~4.8k)</span></dd>
              </dl>
            ) : <span className="muted">waiting for the brain…</span>}
            <p className="muted">Tier and model changes take effect after “Restart brain” in the tray menu.</p>
            {info && (
              <>
                <table className="items" style={{ marginTop: 12 }}>
                  <tbody>
                    {info.required.map((r: any) => (
                      <tr key={r.name}><td>{r.present ? <span className="ok">●</span> : <span className="warn">○</span>} {r.name}</td><td>{fmtBytes(r.bytes_total)}</td></tr>
                    ))}
                  </tbody>
                </table>
                {missing.length > 0 && !progress && (
                  <div className="row" style={{ marginTop: 12 }}>
                    <span className="grow muted">{missing.length} file(s), {fmtBytes(totalMissing)} to download. Stored in %LOCALAPPDATA%\Familiar.</span>
                    <button className="primary" onClick={() => send({ type: 'models.download' })}>Download</button>
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
        </>
      )}

      {tab === 'privacy' && (
        <>
          <h2>Privacy</h2>
          <div className="card">
            {settings && (
              <dl className="kv">
                <dt>Web access</dt><dd><label><input type="checkbox" checked={!!settings.web_enabled} onChange={(e) => update({ web_enabled: e.target.checked })} /> allow web search, page fetch and weather (the only network use besides model downloads)</label></dd>
                <dt>Memory</dt><dd><label><input type="checkbox" checked={!!settings.memory_enabled} onChange={(e) => update({ memory_enabled: e.target.checked })} /> remember facts and summarize conversations locally</label></dd>
                <dt>Interference guard</dt><dd><label><input type="checkbox" checked={!!settings.interference_guard} onChange={(e) => update({ interference_guard: e.target.checked })} /> pause automation while I’m using the mouse or keyboard</label></dd>
                <dt>Progress narration</dt><dd><label><input type="checkbox" checked={!!settings.narrate_tools} onChange={(e) => update({ narrate_tools: e.target.checked })} /> say what it is doing during multi-step actions</label></dd>
              </dl>
            )}
          </div>
          <h2>Memory</h2>
          <div className="card">
            <p className="muted" style={{ marginTop: 0 }}>
              Facts Wisp keeps about you and short summaries of past conversations. Stored only in <code>%LOCALAPPDATA%\Familiar\memory.sqlite</code>.
              {memory && <> · {memory.counts.facts ?? 0} facts, {memory.counts.episodes ?? 0} episodes, {memory.counts.turns ?? 0} turns waiting to be summarized.</>}
            </p>
            <div className="row" style={{ marginBottom: 10 }}>
              <input className="grow" placeholder="search memory…" value={memQuery} onChange={(e) => setMemQuery(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Enter') send({ type: 'memory.list', query: memQuery, limit: 100 }); }} />
              <button onClick={() => send({ type: 'memory.list', query: memQuery, limit: 100 })}>Search</button>
              <button onClick={() => { setMemQuery(''); send({ type: 'memory.list', query: '', limit: 100 }); }}>All</button>
              <button onClick={() => { if (confirm('Delete everything Wisp remembers? This cannot be undone.')) send({ type: 'memory.delete', all: true }); }}>Forget all</button>
            </div>
            {!memory ? <span className="muted">waiting for the brain…</span> : memory.items.length === 0 ? <span className="muted">nothing remembered yet{memory.query ? ` for “${memory.query}”` : ''}.</span> : (
              <table className="items">
                <tbody>
                  {memory.items.map((it) => (
                    <tr key={`${it.kind}-${it.id}`}>
                      <td style={{ width: 70 }}><span className={`risk ${it.kind === 'fact' ? 'low' : 'medium'}`}>{it.kind}</span></td>
                      <td>{it.text}<div className="muted" style={{ fontSize: 11 }}>{it.created?.slice(0, 16).replace('T', ' ')}{it.source ? ` · ${it.source}` : ''}{it.score != null ? ` · ${(it.score * 100).toFixed(0)}%` : ''}</div></td>
                      <td style={{ width: 70 }}><button onClick={() => send({ type: 'memory.delete', kind: it.kind, id: it.id })}>Delete</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}

      {tab === 'tools' && (
        <>
          <h2>Tool permissions</h2>
          <div className="card">
            <p className="muted" style={{ marginTop: 0 }}>
              <strong>auto</strong> runs without asking, <strong>confirm</strong> shows a card (and asks aloud) first, <strong>blocked</strong> hides the tool from Wisp.
              Kill switch <code>{hotkeys?.kill}</code> stops any running action and releases held keys.
            </p>
            {tools.length === 0 ? <span className="muted">waiting for the brain…</span> : (
              <table className="perm-table"><tbody>{groupBy(tools).map(([group, items]) => <GroupRows key={group} group={group} items={items} all={tools} />)}</tbody></table>
            )}
          </div>
        </>
      )}

      {errors.length > 0 && (
        <>
          <h2>Recent errors</h2>
          <div className="card">{errors.map((e, i) => <div key={i} className="err">{e}</div>)}</div>
        </>
      )}

      <h2>About</h2>
      <div className="card muted">
        Familiar {snap?.version} · everything runs on this PC; nothing leaves it except web searches you ask for and the one-time model downloads.
        Data folder: <code>%LOCALAPPDATA%\Familiar</code> <button style={{ marginLeft: 6 }} onClick={() => window.familiar.shell.openPath('home')}>Open</button>
        {' '}· Logs: <button onClick={() => window.familiar.shell.openPath('logs')}>Open</button>
      </div>
    </div>
  );
}

function PersonaEditor({ detail, personas, voices, onClose }: { detail: { id: string; prompt: string }; personas: any[]; voices: { voices: string[]; current: string } | null; onClose: () => void }) {
  const p = personas.find((x) => x.id === detail.id);
  const [prompt, setPrompt] = useState(detail.prompt);
  const [name, setName] = useState(p?.name ?? '');
  const [wake, setWake] = useState(p?.wake_phrase ?? '');
  const [voice, setVoice] = useState(p?.voice ?? 'af_heart');
  useEffect(() => { setPrompt(detail.prompt); }, [detail.prompt]);
  useEffect(() => { if (p) { setName(p.name); setWake(p.wake_phrase); setVoice(p.voice); } }, [p?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!p) return null;
  const editable = p.user_pack;
  return (
    <div className="card" style={{ marginTop: 10 }}>
      <div className="row"><strong className="grow">Edit {p.name}</strong>{!editable && <span className="warn">built-in pack: duplicate it to make changes</span>}<button onClick={onClose}>Close</button></div>
      <dl className="kv" style={{ marginTop: 10 }}>
        <dt>Name</dt><dd><input value={name} disabled={!editable} onChange={(e) => setName(e.target.value)} /></dd>
        <dt>Wake phrase</dt><dd><input value={wake} disabled={!editable} onChange={(e) => setWake(e.target.value)} placeholder="hey wisp" /></dd>
        <dt>Voice</dt><dd><select value={voice} disabled={!editable} onChange={(e) => setVoice(e.target.value)}>{(voices?.voices?.length ? voices.voices : [voice]).map((v) => <option key={v} value={v}>{v}</option>)}</select>{' '}<button onClick={() => window.familiar.brain.send({ type: 'voice.preview', voice, speed: 1.0 })}>▶ Preview</button></dd>
        <dt>System prompt</dt><dd><textarea value={prompt} disabled={!editable} onChange={(e) => setPrompt(e.target.value)} rows={12} style={{ width: '100%', fontFamily: 'Consolas, monospace', fontSize: 12 }} /></dd>
      </dl>
      {editable && <div className="row" style={{ justifyContent: 'flex-end' }}><button className="primary" onClick={() => { window.familiar.brain.send({ type: 'persona.update', persona_id: p.id, prompt, voice: { voice }, wake_phrase: wake, name }); onClose(); }}>Save</button></div>}
    </div>
  );
}

function VoicePicker({ voices, active }: { voices: { voices: string[]; current: string }; active: any }) {
  const [voice, setVoice] = useState(voices.current);
  const [speed, setSpeed] = useState(1.0);
  const [text, setText] = useState('');
  const groups = useMemo(() => {
    const g: Record<string, string[]> = {};
    const names: Record<string, string> = { a: 'American', b: 'British', e: 'Spanish', f: 'French', h: 'Hindi', i: 'Italian', j: 'Japanese', p: 'Portuguese', z: 'Chinese' };
    for (const v of voices.voices) { const key = names[v[0]] ?? 'Other'; (g[key] ??= []).push(v); }
    return g;
  }, [voices.voices]);
  return (
    <>
      <div className="row" style={{ flexWrap: 'wrap', gap: 6, alignItems: 'flex-start' }}>
        {Object.entries(groups).map(([k, vs]) => (
          <div key={k} style={{ minWidth: 150 }}>
            <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.05em', margin: '4px 0' }}>{k}</div>
            {vs.map((v) => <div key={v}><label><input type="radio" name="voice" checked={voice === v} onChange={() => setVoice(v)} /> {v} <span className="muted">{v[1] === 'f' ? '♀' : '♂'}</span></label></div>)}
          </div>
        ))}
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <span>Speed</span><input type="range" min={0.7} max={1.3} step={0.05} value={speed} onChange={(e) => setSpeed(+e.target.value)} /><span className="muted">{speed.toFixed(2)}×</span>
        <input className="grow" placeholder="custom preview text (optional)" value={text} onChange={(e) => setText(e.target.value)} />
        <button onClick={() => window.familiar.brain.send({ type: 'voice.preview', voice, speed, text: text || null })}>▶ Preview</button>
        {active?.user_pack
          ? <button className="primary" onClick={() => window.familiar.brain.send({ type: 'persona.update', persona_id: active.id, voice: { voice, speed } })}>Use for {active.name}</button>
          : <span className="muted">Duplicate the built-in persona (Persona tab) to change its voice.</span>}
      </div>
    </>
  );
}

function HotkeyField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <span className="row">
      <input value={v} onChange={(e) => setV(e.target.value)} style={{ width: 260 }} />
      {v !== value && <button className="primary" onClick={() => onChange(v)}>Apply</button>}
    </span>
  );
}

function groupBy(tools: any[]): [string, any[]][] {
  const m = new Map<string, any[]>();
  for (const t of tools) m.set(t.group, [...(m.get(t.group) ?? []), t]);
  return [...m.entries()];
}

function overrides(all: any[], name: string, permission: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const t of all) if (t.permission !== t.default_permission) out[t.name] = t.permission;
  const def = all.find((t) => t.name === name)?.default_permission;
  if (permission === def) delete out[name];
  else out[name] = permission;
  return out;
}

function GroupRows({ group, items, all }: { group: string; items: any[]; all: any[] }) {
  return (
    <>
      <tr><td colSpan={3} style={{ paddingTop: 10, color: 'var(--fg-dim)', textTransform: 'uppercase', fontSize: 11, letterSpacing: '.06em' }}>{group}</td></tr>
      {items.map((t) => (
        <tr key={t.name}>
          <td style={{ width: 150 }}><code>{t.name}</code></td>
          <td className="muted">{t.description}</td>
          <td style={{ width: 110, textAlign: 'right' }}>
            <select value={t.permission} className={t.permission !== t.default_permission ? 'changed' : ''}
                    onChange={(e) => window.familiar.brain.send({ type: 'settings.update', settings: { tool_permissions: overrides(all, t.name, e.target.value) } })}>
              <option value="auto">auto</option><option value="confirm">confirm</option><option value="blocked">blocked</option>
            </select>
          </td>
        </tr>
      ))}
    </>
  );
}

function fmtBytes(n: number): string {
  if (!n) return '0';
  if (n > 1e9) return (n / 1e9).toFixed(2) + ' GB';
  if (n > 1e6) return (n / 1e6).toFixed(0) + ' MB';
  return (n / 1e3).toFixed(0) + ' kB';
}
