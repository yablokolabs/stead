/**
 * Microphone capture and reply playback.
 *
 * Two browser facts shape this file. Chrome and Firefox record WebM/Opus while
 * Safari — every iOS browser, and the home-screen PWA — records MP4, so the
 * container is negotiated rather than assumed. And iOS refuses to play audio
 * that was not started by a user gesture, which a reply arriving after a
 * network round trip never is.
 */

/** Best first. Safari supports only the mp4 entry; Chrome and Firefox the rest. */
export const RECORDING_MIME_CANDIDATES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/mp4',
  'audio/ogg;codecs=opus',
] as const;

export function pickRecordingMime(isSupported: (type: string) => boolean): string | null {
  for (const candidate of RECORDING_MIME_CANDIDATES) {
    if (isSupported(candidate)) return candidate;
  }
  return null;
}

export class MicrophoneDenied extends Error {
  constructor() {
    super('microphone denied');
    this.name = 'MicrophoneDenied';
  }
}

export class RecordingUnsupported extends Error {
  constructor() {
    super('recording unsupported');
    this.name = 'RecordingUnsupported';
  }
}

export class VoiceRecorder {
  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];

  get active(): boolean {
    return this.recorder !== null;
  }

  async start(): Promise<void> {
    if (typeof MediaRecorder === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      throw new RecordingUnsupported();
    }

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (cause) {
      // NotAllowedError is a refusal; NotFoundError is a device with no mic.
      if (cause instanceof DOMException && cause.name === 'NotAllowedError') {
        throw new MicrophoneDenied();
      }
      throw new RecordingUnsupported();
    }

    // Safari ignores an unsupported mimeType option rather than throwing, so
    // the container that actually came out is read back from the recorder.
    const mime = pickRecordingMime((type) => MediaRecorder.isTypeSupported(type));
    this.recorder = mime
      ? new MediaRecorder(this.stream, { mimeType: mime })
      : new MediaRecorder(this.stream);

    this.chunks = [];
    this.recorder.ondataavailable = (event) => {
      if (event.data.size > 0) this.chunks.push(event.data);
    };
    this.recorder.start();
  }

  async stop(): Promise<Blob> {
    const recorder = this.recorder;
    if (!recorder) throw new RecordingUnsupported();

    const finished = new Promise<Blob>((resolve) => {
      recorder.onstop = () => {
        resolve(new Blob(this.chunks, { type: recorder.mimeType || 'audio/webm' }));
      };
    });

    recorder.stop();
    const blob = await finished;
    this.release();
    return blob;
  }

  /** Abandon the recording and, importantly, drop the microphone indicator. */
  cancel(): void {
    if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop();
    this.release();
  }

  private release(): void {
    for (const track of this.stream?.getTracks() ?? []) track.stop();
    this.stream = null;
    this.recorder = null;
    this.chunks = [];
  }
}

/** A valid, zero-sample WAV. Cheapest thing that counts as playing something. */
const SILENCE =
  'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAgLsAAAB3AQACABAAZGF0YQAAAAA=';

let player: HTMLAudioElement | null = null;
let playing: string | null = null;

function canSpeak(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window;
}

/**
 * Spend the user's tap on unlocking both ways of making sound.
 *
 * iOS permits neither `play()` nor `speak()` outside a gesture, and the reply
 * arrives long after this handler returns. Playing silence and speaking a
 * space during the tap leaves both engines allowed for the rest of the session.
 */
export function primePlayback(): void {
  player ??= new Audio();
  player.src = SILENCE;
  void player.play().catch(() => {
    // A browser that refuses even this still shows the reply as text.
  });

  if (canSpeak()) {
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(' '));
  }
}

/**
 * Rank a voice. Higher is better; below zero means unusable.
 *
 * Device voice quality varies enormously, and the first `en-GB` entry in the
 * list is usually the oldest and worst — a 1990s formant synthesiser on some
 * systems. The good ones are identifiable: "Natural" or "Neural" in the name
 * (Edge's are excellent), and network voices, which are almost always better
 * than the ones shipped in the OS.
 *
 * `ARCHITECTURE.md` argues Stead serves a British household and should sound
 * like it, so `en-GB` outranks everything else.
 */
/**
 * `SpeechSynthesisVoice` has no gender field, so this is name matching and
 * therefore imperfect. These are the British female voices the major
 * platforms actually ship — Apple (Kate, Serena), Chrome (Google UK English
 * Female), Edge and Windows (Sonia, Libby, Maisie, Hazel) — and the male
 * names that must not outrank them.
 *
 * `\bmale\b` deliberately does not match "Female" — no word boundary between
 * "Fe" and "male" — so the literal word in "Google UK English Female" lands
 * in the female list, not the male one.
 */
const BRITISH_MALE = /\b(daniel|arthur|oliver|george|ryan|thomas|brian|alfie|elliot|male)\b/i;
const FEMALE = /\b(female|kate|serena|sonia|libby|hazel|martha|maisie|amy|emma|fiona|susan|karen)\b/i;

/**
 * Scored in tiers, and the order of the tiers is the point.
 *
 * The household asked for a female voice, so `ARCHITECTURE.md` now names
 * `en-GB-SoniaNeural` — British and female. The argument it makes at length
 * is about the ACCENT: Stead serves a British household. So locale dominates
 * absolutely, and gender only decides between voices that are already
 * British: female first, then unrecognised names, then known male ones.
 * An earlier version scored these on one scale, which let an American male
 * outrank a British female — exactly the substitution this ordering argues
 * against.
 */
export function scoreVoice(voice: SpeechSynthesisVoice): number {
  let score: number;
  if (voice.lang === 'en-GB') score = 1000;
  else if (voice.lang.startsWith('en')) score = 0;
  else return -1;

  if (FEMALE.test(voice.name)) score += 100;
  else if (BRITISH_MALE.test(voice.name)) score -= 100;

  if (/natural|neural/i.test(voice.name)) score += 30;
  if (!voice.localService) score += 20;
  if (/google/i.test(voice.name)) score += 10;
  // Genuinely poor renderings, whatever the accent.
  if (/compact|eloquence/i.test(voice.name)) score -= 25;
  return score;
}

export function pickVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | undefined {
  const usable = voices.filter((v) => scoreVoice(v) >= 0);
  if (usable.length === 0) return undefined;
  return usable.reduce((best, v) => (scoreVoice(v) > scoreVoice(best) ? v : best));
}

/**
 * Speak a reply with the device's own synthesiser.
 *
 * This replaced server-side text-to-speech: it removed about three seconds
 * from every spoken turn and a base64 payload a third larger than the audio,
 * and it starts talking immediately rather than after a whole file arrives.
 */
export function speak(text: string): void {
  if (!canSpeak() || text.trim().length === 0) return;

  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = 'en-GB';
  const voice = pickVoice(window.speechSynthesis.getVoices());
  if (voice) utterance.voice = voice;
  window.speechSynthesis.speak(utterance);
}

/** Stop mid-sentence — for when the user starts talking again. */
export function stopSpeaking(): void {
  if (canSpeak()) window.speechSynthesis.cancel();
}

/** Only used if the agent ever sends audio itself; the browser speaks now. */
export function playReply(audio: Blob): void {
  player ??= new Audio();
  if (playing) URL.revokeObjectURL(playing);
  playing = URL.createObjectURL(audio);
  player.src = playing;
  void player.play().catch(() => {
    // Autoplay refused. The transcript and reply are already on screen.
  });
}
