import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { pickRecordingMime, RECORDING_MIME_CANDIDATES, speak, stopSpeaking } from './recorder';

describe('pickRecordingMime', () => {
  const supporting =
    (...types: string[]) =>
    (type: string) =>
      types.includes(type);

  it('prefers WebM/Opus where it exists, as on Chrome and Firefox', () => {
    const pick = pickRecordingMime(supporting('audio/webm;codecs=opus', 'audio/webm'));
    expect(pick).toBe('audio/webm;codecs=opus');
  });

  it('falls back to MP4 on Safari, which is every browser on iOS', () => {
    expect(pickRecordingMime(supporting('audio/mp4'))).toBe('audio/mp4');
  });

  it('returns null when nothing matches, leaving the recorder its own default', () => {
    expect(pickRecordingMime(() => false)).toBeNull();
  });

  /**
   * The gateway refuses any container outside its allowlist, so a candidate
   * added here that the Worker does not accept would fail only at runtime,
   * on whichever browser happened to pick it.
   */
  it('only offers containers the gateway accepts', () => {
    const accepted = ['audio/webm', 'audio/ogg', 'audio/mp4', 'audio/mpeg', 'audio/wav'];
    for (const candidate of RECORDING_MIME_CANDIDATES) {
      expect(accepted).toContain(candidate.split(';')[0]);
    }
  });
});

describe('speaking the reply', () => {
  class FakeUtterance {
    lang = '';
    voice: SpeechSynthesisVoice | null = null;
    constructor(public text: string) {}
  }

  const voice = (lang: string) => ({ lang, name: `voice-${lang}` }) as SpeechSynthesisVoice;

  let spoken: FakeUtterance[];
  let cancel: ReturnType<typeof vi.fn>;
  let voices: SpeechSynthesisVoice[];

  beforeEach(() => {
    spoken = [];
    voices = [voice('en-US'), voice('en-GB'), voice('fr-FR')];
    cancel = vi.fn();
    vi.stubGlobal('SpeechSynthesisUtterance', FakeUtterance);
    vi.stubGlobal('speechSynthesis', {
      cancel,
      speak: (u: FakeUtterance) => void spoken.push(u),
      getVoices: () => voices,
    });
  });

  afterEach(() => vi.unstubAllGlobals());

  it('speaks the reply', () => {
    speak('Nothing in the diary tomorrow.');
    expect(spoken).toHaveLength(1);
    expect(spoken[0]!.text).toBe('Nothing in the diary tomorrow.');
  });

  /** ARCHITECTURE.md argues Stead should sound British; the device can oblige. */
  it('asks for a British voice, and picks one when the device has it', () => {
    speak('hello');
    expect(spoken[0]!.lang).toBe('en-GB');
    expect(spoken[0]!.voice).toMatchObject({ lang: 'en-GB' });
  });

  it('settles for any English voice when there is no British one', () => {
    voices = [voice('en-US'), voice('fr-FR')];
    speak('hello');
    expect(spoken[0]!.voice).toMatchObject({ lang: 'en-US' });
    expect(spoken[0]!.lang).toBe('en-GB');
  });

  it('still speaks when the device offers no voices at all', () => {
    voices = [];
    speak('hello');
    expect(spoken).toHaveLength(1);
    expect(spoken[0]!.voice).toBeNull();
  });

  it('cuts off whatever it was saying before starting again', () => {
    speak('first');
    speak('second');
    expect(cancel).toHaveBeenCalledTimes(2);
  });

  it('says nothing when there is nothing to say', () => {
    speak('   ');
    expect(spoken).toHaveLength(0);
  });

  it('stops on request, for when the user starts talking again', () => {
    stopSpeaking();
    expect(cancel).toHaveBeenCalled();
  });

  it('is silent rather than broken on a browser without speech synthesis', () => {
    vi.unstubAllGlobals();
    vi.stubGlobal('SpeechSynthesisUtterance', undefined);
    expect(() => speak('hello')).not.toThrow();
    expect(() => stopSpeaking()).not.toThrow();
  });
});
