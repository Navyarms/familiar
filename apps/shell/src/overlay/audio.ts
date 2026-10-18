/**
 * Plays `reply.audio` sentences back to back through WebAudio and exposes the current mouth
 * amplitude + viseme for the avatar. Scheduling on the AudioContext clock keeps lip sync exact.
 */
export type AudioSentence = {
  turn_id: string;
  sentence_id: number;
  sample_rate: number;
  pcm16_b64: string;
  duration_s: number;
  envelope_hz: number;
  envelope: number[];
  visemes: { t: number; shape: string }[];
};

type Scheduled = AudioSentence & { startAt: number; endAt: number };

export class SpeechPlayer {
  private ctx = new AudioContext();
  private gain = this.ctx.createGain();
  private queue: Scheduled[] = [];
  private nextStart = 0;
  private sources = new Set<AudioBufferSourceNode>();
  private currentTurn: string | null = null;
  onSentenceStart: ((s: AudioSentence) => void) | null = null;
  onSpeakingChange: ((speaking: boolean) => void) | null = null;
  private speaking = false;

  constructor() {
    this.gain.connect(this.ctx.destination);
  }

  enqueue(s: AudioSentence): void {
    if (this.currentTurn !== s.turn_id) {
      this.stop();
      this.currentTurn = s.turn_id;
    }
    void this.ctx.resume();
    const bytes = Uint8Array.from(atob(s.pcm16_b64), (c) => c.charCodeAt(0));
    const i16 = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2);
    const buf = this.ctx.createBuffer(1, i16.length, s.sample_rate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < i16.length; i++) ch[i] = i16[i] / 32768;
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.gain);
    const now = this.ctx.currentTime;
    const startAt = Math.max(now + 0.03, this.nextStart);
    const endAt = startAt + buf.duration;
    this.nextStart = endAt + 0.12; // small breath between sentences
    src.start(startAt);
    this.sources.add(src);
    const sched: Scheduled = { ...s, startAt, endAt };
    this.queue.push(sched);
    src.onended = () => {
      this.sources.delete(src);
      this.queue = this.queue.filter((q) => q !== sched);
      this.updateSpeaking();
    };
    this.updateSpeaking();
  }

  stop(): void {
    for (const s of this.sources) {
      try {
        s.stop();
      } catch {
        /* already stopped */
      }
    }
    this.sources.clear();
    this.queue = [];
    this.nextStart = 0;
    this.updateSpeaking();
  }

  /** Current mouth state sampled on the audio clock. */
  sample(): { amp: number; shape: string; sentence: Scheduled | null } {
    const t = this.ctx.currentTime;
    const cur = this.queue.find((q) => t >= q.startAt && t < q.endAt) ?? null;
    if (!cur) return { amp: 0, shape: 'sil', sentence: null };
    if (!(cur as any).__started) {
      (cur as any).__started = true;
      this.onSentenceStart?.(cur);
    }
    const rel = t - cur.startAt;
    const fi = rel * cur.envelope_hz;
    const i = Math.floor(fi);
    const a0 = cur.envelope[i] ?? 0, a1 = cur.envelope[i + 1] ?? a0;
    const amp = a0 + (a1 - a0) * (fi - i);
    let shape = 'aa';
    for (const v of cur.visemes) {
      if (v.t <= rel) shape = v.shape;
      else break;
    }
    return { amp, shape, sentence: cur };
  }

  private updateSpeaking(): void {
    const s = this.queue.length > 0;
    if (s !== this.speaking) {
      this.speaking = s;
      this.onSpeakingChange?.(s);
    }
  }
}
