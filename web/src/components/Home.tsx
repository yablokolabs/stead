import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useAuth } from '../auth/AuthProvider';
import { askStead, speakToStead, SteadApiError, type SteadReply } from '../lib/api';
import {
  MicrophoneDenied,
  playReply,
  primePlayback,
  speak,
  stopSpeaking,
  VoiceRecorder,
} from '../lib/recorder';

type Phase = 'idle' | 'recording' | 'thinking';

interface Turn {
  transcript?: string;
  reply: string;
}

function greeting(now = new Date()): string {
  const hour = now.getHours();
  if (hour < 12) return 'Good morning.';
  if (hour < 18) return 'Good afternoon.';
  return 'Good evening.';
}

export function Home({ apiUrl }: { apiUrl: string }) {
  const { email, signOut, getAccessToken } = useAuth();
  const [message, setMessage] = useState('');
  const [turn, setTurn] = useState<Turn | null>(null);
  const [error, setError] = useState<{ text: string; expired: boolean } | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');

  const recorderRef = useRef<VoiceRecorder | null>(null);
  const recorder = (recorderRef.current ??= new VoiceRecorder());

  // Leaving the screen mid-recording must drop the microphone, or the browser
  // goes on showing a household it is being listened to.
  useEffect(
    () => () => {
      recorder.cancel();
      stopSpeaking();
    },
    [recorder],
  );

  function report(cause: unknown) {
    const failure = cause instanceof SteadApiError ? cause : new SteadApiError('unknown', 0);
    setError({ text: failure.userMessage, expired: failure.code === 'unauthorized' });
  }

  async function accessToken(): Promise<string> {
    const token = await getAccessToken();
    if (!token) throw new SteadApiError('unauthorized', 401);
    return token;
  }

  /** Returns whether the turn succeeded, so a failed message is not lost. */
  async function runTurn(send: () => Promise<SteadReply>): Promise<boolean> {
    setPhase('thinking');
    setError(null);
    setTurn(null);
    try {
      const result = await send();
      setTurn({ transcript: result.transcript, reply: result.reply });
      if (result.audio) playReply(result.audio);
      else speak(result.reply);
      return true;
    } catch (cause) {
      report(cause);
      return false;
    } finally {
      setPhase('idle');
    }
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const text = message.trim();
    if (!text || phase !== 'idle') return;

    const sent = await runTurn(async () =>
      askStead({ apiUrl, accessToken: await accessToken(), message: text }),
    );
    if (sent) setMessage('');
  }

  async function onTalk() {
    if (phase === 'recording') {
      const recording = await recorder.stop();
      if (recording.size === 0) {
        setPhase('idle');
        return;
      }
      await runTurn(async () =>
        speakToStead({ apiUrl, accessToken: await accessToken(), recording }),
      );
      return;
    }

    if (phase !== 'idle') return;

    // Must happen inside the tap: iOS only grants playback from a gesture, and
    // the reply arrives long after this handler returns.
    stopSpeaking();
    primePlayback();
    setError(null);
    try {
      await recorder.start();
      setPhase('recording');
    } catch (cause) {
      setError({
        text:
          cause instanceof MicrophoneDenied
            ? 'Stead needs microphone access to listen. Allow it in your browser settings and try again.'
            : 'This browser cannot record audio. Type instead.',
        expired: false,
      });
    }
  }

  const busy = phase === 'thinking';

  return (
    <main className="screen">
      <header className="stack stack--tight">
        <h1 className="wordmark">Stead</h1>
        <p className="tagline">{greeting()}</p>
      </header>

      <button
        type="button"
        className={`button ${phase === 'recording' ? 'button--recording' : 'button--ghost'}`}
        onClick={() => void onTalk()}
        disabled={busy}
        aria-pressed={phase === 'recording'}
      >
        {phase === 'recording' ? 'Stop and send' : 'Talk to Stead'}
      </button>

      {phase === 'recording' && (
        <p className="divider" role="status">
          Listening…
        </p>
      )}

      <p className="divider">or</p>

      <form className="stack" onSubmit={onSubmit}>
        <label className="field">
          <span>Ask Stead…</span>
          <textarea
            name="message"
            rows={3}
            maxLength={4000}
            placeholder="What's happening tomorrow?"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
        </label>

        <button
          type="submit"
          className="button button--primary"
          disabled={phase !== 'idle' || message.trim().length === 0}
        >
          {busy ? 'Asking Stead…' : 'Send'}
        </button>
      </form>

      {error && (
        <div className="notice notice--error" role="alert">
          <p>{error.text}</p>
          {error.expired && (
            <button type="button" className="button button--link" onClick={() => void signOut()}>
              Sign in again
            </button>
          )}
        </div>
      )}

      {turn && (
        <section className="reply" aria-live="polite">
          {turn.transcript && <p className="footnote">You said: {turn.transcript}</p>}
          <p>{turn.reply}</p>
        </section>
      )}

      <footer className="footer">
        <p className="footnote">
          Signed in as
          <br />
          <strong>{email}</strong>
        </p>
        <button type="button" className="button button--ghost" onClick={() => void signOut()}>
          Sign out
        </button>
      </footer>
    </main>
  );
}
