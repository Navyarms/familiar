import { useEffect, useRef, useState } from 'react';

type Msg = { id: string; role: 'user' | 'assistant' | 'system' | 'tool'; text: string; stats?: Record<string, number>; streaming?: boolean; tool?: ToolCard };
type ToolCard = { request_id: string; tool: string; args: Record<string, unknown>; description: string; risk?: string; status: 'confirm' | 'running' | 'ok' | 'failed' | 'denied'; summary?: string };

export function App() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [state, setState] = useState<string>('booting');
  const [detail, setDetail] = useState<string>('');
  const [persona, setPersona] = useState<string>('Wisp');
  const [input, setInput] = useState('');
  const [progress, setProgress] = useState<{ item: string; done: number; total: number; odone: number; ototal: number } | null>(null);
  const [needsDownload, setNeedsDownload] = useState(false);
  const [mic, setMic] = useState<{ mode: string; level: number; reason: string }>({ mode: 'off', level: 0, reason: '' });
  const [partial, setPartial] = useState('');
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
        case 'transcript.partial':
          setPartial(m.text);
          break;
        case 'transcript.final':
          setPartial('');
          setMsgs((xs) => [...xs, { id: Math.random().toString(36).slice(2), role: 'user', text: m.text }]);
          break;
        case 'mic.state':
          setMic({ mode: m.mode, level: m.level ?? 0, reason: m.reason ?? '' });
          if (m.mode !== 'listening' && m.mode !== 'processing') setPartial('');
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
            if (!m.text && !m.interrupted && i < 0) return xs; // tool-only turn: the cards already tell the story
            return i >= 0 ? [...xs.slice(0, i), done, ...xs.slice(i + 1)] : [...xs, done];
          });
          turnRef.current = null;
          break;
        case 'tool.request_confirm':
          turnRef.current = null; // tokens after the card start a fresh bubble
          setMsgs((xs) => [...xs, { id: 'tool-' + m.request_id, role: 'tool', text: '', tool: { request_id: m.request_id, tool: m.tool, args: m.args, description: m.description, risk: m.risk, status: 'confirm' } }]);
          break;
        case 'tool.start':
          turnRef.current = null;
          setMsgs((xs) => upsertCard(xs, { request_id: m.request_id, tool: m.tool, args: m.args, description: m.description, status: 'running' }));
          break;
        case 'tool.result':
          turnRef.current = null;
          setMsgs((xs) => {
            const prev = xs.find((x) => x.id === 'tool-' + m.request_id)?.tool;
            const status: ToolCard['status'] = m.ok ? 'ok' : /declined|not allowed|turned off/.test(m.summary) ? 'denied' : 'failed';
            return upsertCard(xs, { request_id: m.request_id, tool: m.tool, args: prev?.args ?? {}, description: prev?.description ?? m.tool, risk: prev?.risk, status, summary: m.summary });
          });
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
  const busy = state === 'thinking' || state === 'speaking' || state === 'executing' || state === 'needs_confirm';
  const ready = ['idle', 'thinking', 'speaking', 'listening', 'executing', 'needs_confirm'].includes(state);
  const listening = mic.mode === 'listening';
  const micAvailable = mic.mode !== 'off';
  const toggleTalk = () => window.familiar.brain.send({ type: listening ? 'ptt.stop' : 'ptt.start' });

  return (
    <div className="panel">
      <div className="panel-head">
        <span className={`dot ${state}`} />
        <strong>{persona}</strong>
        <span className="muted grow" style={{ fontSize: 12 }}>{listening ? 'listening…' : mic.mode === 'processing' ? 'transcribing…' : labelFor(state, detail, mic.mode)}</span>
        {busy && <button onClick={() => window.familiar.brain.send({ type: 'interrupt' })}>Stop</button>}
        <button title="Settings" onClick={() => window.familiar.openSettings()}>⚙</button>
        <button title="Hide (Esc)" onClick={() => window.familiar.panel.hide()}>✕</button>
      </div>
      <div className="transcript" ref={listRef}>
        {msgs.length === 0 && ready && (
          <div className="msg system">
            {mic.mode === 'wake' ? `Say “hey ${persona}” or press Ctrl+Shift+M to talk, or type below.` : `Type to ${persona} below.`} Ctrl+Shift+Space toggles this panel.
          </div>
        )}
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
        {partial && <div className="msg user" style={{ opacity: 0.6, fontStyle: 'italic' }}>{partial}…</div>}
        {msgs.map((m) => m.role === 'tool' && m.tool ? <ToolCardView key={m.id} card={m.tool} /> : (
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
        {micAvailable && (
          <button className={listening ? 'primary' : ''} title={listening ? 'Stop listening' : 'Talk (Ctrl+Shift+M)'} onClick={toggleTalk}
                  style={listening ? { boxShadow: `0 0 0 ${Math.round(2 + mic.level * 10)}px rgba(60,200,130,.35)` } : undefined}>
            {listening ? '■' : '🎤'}
          </button>
        )}
        <button className="primary" disabled={!ready || !input.trim()} onClick={send}>Send</button>
      </div>
    </div>
  );
}

function upsertCard(xs: Msg[], card: ToolCard): Msg[] {
  const id = 'tool-' + card.request_id;
  const i = xs.findIndex((x) => x.id === id);
  const msg: Msg = { id, role: 'tool', text: '', tool: card };
  return i >= 0 ? [...xs.slice(0, i), msg, ...xs.slice(i + 1)] : [...xs, msg];
}

function ToolCardView({ card }: { card: ToolCard }) {
  const icon = { confirm: '?', running: '…', ok: '✓', failed: '✗', denied: '–' }[card.status];
  const argText = Object.entries(card.args || {}).map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join(' · ');
  return (
    <div className={`tool-card ${card.status}`}>
      <div className="row">
        <span className="tool-icon">{icon}</span>
        <span className="grow">
          <strong>{card.description}</strong>
          {argText && <span className="muted" style={{ marginLeft: 6, fontSize: 11 }}>{argText.slice(0, 120)}</span>}
        </span>
        {card.risk && card.status === 'confirm' && <span className={`risk ${card.risk}`}>{card.risk}</span>}
      </div>
      {card.status === 'confirm' && (
        <div className="row" style={{ marginTop: 8 }}>
          <span className="muted grow" style={{ fontSize: 12 }}>Allow this? You can also say yes or no.</span>
          <button onClick={() => window.familiar.brain.send({ type: 'tool.deny', request_id: card.request_id })}>Deny</button>
          <button className="primary" onClick={() => window.familiar.brain.send({ type: 'tool.confirm', request_id: card.request_id })}>Allow</button>
        </div>
      )}
      {card.summary && card.status !== 'confirm' && <div className="muted" style={{ marginTop: 4, fontSize: 12 }}>{card.summary}</div>}
    </div>
  );
}

function labelFor(state: string, detail: string, micMode: string): string {
  switch (state) {
    case 'executing': return detail ? `working: ${detail}` : 'working…';
    case 'needs_confirm': return 'waiting for your OK';
    case 'idle': return micMode === 'wake' ? 'say the wake word or type' : 'ready';
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
