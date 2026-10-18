import { useEffect, useRef, useState } from 'react';

type Msg = { id: string; role: 'user' | 'assistant' | 'system'; text: string; stats?: Record<string, number>; streaming?: boolean };

export function App() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [state, setState] = useState<string>('booting');
  const [detail, setDetail] = useState<string>('');
  const [persona, setPersona] = useState<string>('Wisp');
  const [input, setInput] = useState('');
  const [progress, setProgress] = useState<{ item: string; done: number; total: number; odone: number; ototal: number } | null>(null);
  const [needsDownload, setNeedsDownload] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const turnRef = useRef<string | null>(null);

  useEffect(() => {
    const off = window.familiar.brain.onMessage((m: any) => {
      switch (m.type) {
        case 'hello':
          if (m.persona?.name) setPersona(m.persona.name);
          break;
        case 'state':
          setState(m.state);
          setDetail(m.detail ?? '');
          break;
        case 'models.info':
          setNeedsDownload(m.required?.some((r: any) => !r.present));
          break;
        case 'model.download_progress':
          setProgress({ item: m.item, done: m.bytes_done, total: m.bytes_total, odone: m.overall_done, ototal: m.overall_total });
          break;
        case 'model.ready':
          setProgress(null);
          setNeedsDownload(false);
          break;
        case 'transcript.final':
          setMsgs((xs) => [...xs, { id: Math.random().toString(36).slice(2), role: 'user', text: m.text }]);
          break;
        case 'reply.token':
          setMsgs((xs) => {
            if (turnRef.current === m.turn_id) {
              const last = xs[xs.length - 1];
              return [...xs.slice(0, -1), { ...last, text: last.text + m.text }];
            }
            turnRef.current = m.turn_id;
            return [...xs, { id: m.turn_id, role: 'assistant', text: m.text, streaming: true }];
          });
          break;
        case 'reply.done':
          setMsgs((xs) => {
            const i = xs.findIndex((x) => x.id === m.turn_id);
            const done: Msg = { id: m.turn_id, role: 'assistant', text: m.text || (m.interrupted ? '(stopped)' : ''), stats: m.stats };
            return i >= 0 ? [...xs.slice(0, i), done, ...xs.slice(i + 1)] : [...xs, done];
          });
          turnRef.current = null;
          break;
        case 'error':
          setMsgs((xs) => [...xs, { id: Math.random().toString(36).slice(2), role: 'system', text: `${m.code}: ${m.message}` }]);
          break;
      }
    });
    const offShown = window.familiar.panel.onShown(() => setTimeout(() => inputRef.current?.focus(), 50));
    window.familiar.snapshot().then((s: any) => {
      if (s.lastHello?.persona?.name) setPersona(s.lastHello.persona.name);
      if (s.lastState?.state) setState(s.lastState.state);
      if (s.lastModelsInfo) setNeedsDownload(s.lastModelsInfo.required?.some((r: any) => !r.present));
    });
    return () => {
      off();
      offShown();
    };
  }, []);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [msgs, progress]);

  const send = () => {
    const text = input.trim();
    if (!text) return;
    window.familiar.brain.send({ type: 'text.send', text, speak: true });
    setInput('');
  };
  const busy = state === 'thinking' || state === 'speaking';
  const ready = ['idle', 'thinking', 'speaking', 'listening'].includes(state);

  return (
    <div className="panel">
      <div className="panel-head">
        <span className={`dot ${state}`} />
        <strong>{persona}</strong>
        <span className="muted grow" style={{ fontSize: 12 }}>{labelFor(state, detail)}</span>
        {busy && <button onClick={() => window.familiar.brain.send({ type: 'interrupt' })}>Stop</button>}
        <button title="Settings" onClick={() => window.familiar.openSettings()}>⚙</button>
        <button title="Hide (Esc)" onClick={() => window.familiar.panel.hide()}>✕</button>
      </div>
      <div className="transcript" ref={listRef}>
        {msgs.length === 0 && ready && <div className="msg system">Say something to {persona}. Ctrl+Shift+Space toggles this panel.</div>}
        {needsDownload && !progress && (
          <div className="msg system">
            First run: {persona} needs to download its models.{' '}
            <button className="primary" onClick={() => window.familiar.brain.send({ type: 'models.download' })}>Download now</button>
            <div style={{ marginTop: 6 }}><button onClick={() => window.familiar.openSettings()}>Details…</button></div>
          </div>
        )}
        {progress && (
          <div className="msg system" style={{ width: '90%' }}>
            Downloading {progress.item} · {fmtBytes(progress.odone)} / {fmtBytes(progress.ototal)}
            <div className="progress" style={{ marginTop: 6 }}><div style={{ width: `${(100 * progress.odone) / Math.max(1, progress.ototal)}%` }} /></div>
          </div>
        )}
        {msgs.map((m) => (
          <div key={m.id} className={`msg ${m.role}`}>
            {m.text}
            {m.stats && m.stats.ttft_s != null && (
              <span className="stats">
                first token {m.stats.ttft_s.toFixed(2)}s · {m.stats.tok_s?.toFixed(1)} tok/s
                {m.stats.first_audio_s != null && <> · first audio {m.stats.first_audio_s.toFixed(2)}s</>}
              </span>
            )}
          </div>
        ))}
      </div>
      <div className="composer">
        <textarea
          ref={inputRef}
          value={input}
          placeholder={ready ? `Message ${persona}…` : 'Starting up…'}
          disabled={!ready}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            } else if (e.key === 'Escape') {
              if (busy) window.familiar.brain.send({ type: 'interrupt' });
              else window.familiar.panel.hide();
            }
          }}
        />
        <button className="primary" disabled={!ready || !input.trim()} onClick={send}>Send</button>
      </div>
    </div>
  );
}

function labelFor(state: string, detail: string): string {
  switch (state) {
    case 'idle': return 'listening for text';
    case 'thinking': return 'thinking…';
    case 'speaking': return 'speaking';
    case 'booting': return detail || 'starting…';
    case 'downloading': return detail || 'downloading models';
    case 'error': return detail || 'error';
    default: return state;
  }
}

function fmtBytes(n: number): string {
  if (n > 1e9) return (n / 1e9).toFixed(2) + ' GB';
  if (n > 1e6) return (n / 1e6).toFixed(0) + ' MB';
  return (n / 1e3).toFixed(0) + ' kB';
}
