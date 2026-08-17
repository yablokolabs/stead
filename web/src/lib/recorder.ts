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

/**
 * Spend the user's tap on unlocking playback.
 *
 * iOS only permits `play()` inside a gesture. Playing silence on the same
 * element during the tap that starts recording leaves that element allowed to
 * play again later, which is when the reply actually arrives.
 */
export function primePlayback(): void {
  player ??= new Audio();
  player.src = SILENCE;
  void player.play().catch(() => {
    // A browser that refuses even this still shows the reply as text.
  });
}

export function playReply(audio: Blob): void {
  player ??= new Audio();
  if (playing) URL.revokeObjectURL(playing);
  playing = URL.createObjectURL(audio);
  player.src = playing;
  void player.play().catch(() => {
    // Autoplay refused. The transcript and reply are already on screen.
  });
}
