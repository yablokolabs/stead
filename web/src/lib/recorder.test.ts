import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  pickRecordingMime,
  pickVoice,
  RECORDING_MIME_CANDIDATES,
  speak,
  stopSpeaking,
} from './recorder';

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

describe('pickVoice', () => {
  const v = (name: string, lang: string, localService = true) =>
    ({ name, lang, localService }) as SpeechSynthesisVoice;

  it('returns nothing when the device has no English voice', () => {
    expect(pickVoice([v('Amélie', 'fr-FR'), v('Yuna', 'ko-KR')])).toBeUndefined();
  });

  it('prefers British over other English', () => {
    const pick = pickVoice([v('Samantha', 'en-US'), v('Serena', 'en-GB')]);
    expect(pick?.name).toBe('Serena');
  });

  it('prefers a natural voice among equals', () => {
    const pick = pickVoice([
      v('Serena', 'en-GB'),
      v('Microsoft Sonia Online (Natural) - English (United Kingdom)', 'en-GB', false),
    ]);
    expect(pick?.name).toContain('Sonia');
  });

  it('prefers a network voice over a local one of the same locale', () => {
    const pick = pickVoice([v('Local Brit', 'en-GB'), v('Cloud Brit', 'en-GB', false)]);
    expect(pick?.name).toBe('Cloud Brit');
  });

  /**
   * The household asked for a female voice, so the picker prefers female
   * first and British second — ARCHITECTURE.md names en-GB-SoniaNeural, but
   * macOS ships no female en-GB voice at all, so a female voice of any
   * English locale must beat a male British one. There is no gender field on
   * a voice, so this is name matching.
   */
  describe('female first, British among females, as the household asked for', () => {
    it.each([
      ['Apple', ['Kate', 'Daniel'], 'Kate'],
      ['Chrome', ['Google UK English Female', 'Google UK English Male'], 'Google UK English Female'],
      ['Edge', ['Microsoft Sonia', 'Microsoft Ryan'], 'Microsoft Sonia'],
      ['Windows', ['Hazel', 'George'], 'Hazel'],
    ])('picks the female British voice on %s', (_platform, names, expected) => {
      expect(pickVoice(names.map((n) => v(n, 'en-GB')))?.name).toBe(expected);
    });

    /**
     * The female-name list cannot be exhaustive. When a name is simply not
     * recognised, a known female name still has to win — otherwise the pick
     * is whichever the device happened to list first.
     */
    it('prefers a known female name over an unrecognised one', () => {
      const pick = pickVoice([v('Aurelie', 'en-GB'), v('Kate', 'en-GB')]);
      expect(pick?.name).toBe('Kate');
    });

    /**
     * An unrecognised name still beats a known male one: it might be female
     * (Aurelie is), and the male list must not win by default.
     */
    it('prefers an unrecognised name over a known male one', () => {
      const pick = pickVoice([v('Aurelie', 'en-GB'), v('Arthur', 'en-GB')]);
      expect(pick?.name).toBe('Aurelie');
    });

    it('does not mistake "Female" for a male voice', () => {
      const pick = pickVoice([v('Google UK English Female', 'en-GB'), v('Arthur', 'en-GB')]);
      expect(pick?.name).toBe('Google UK English Female');
    });

    it('still prefers British female over American male', () => {
      const pick = pickVoice([v('Serena', 'en-GB'), v('Daniel', 'en-US')]);
      expect(pick?.name).toBe('Serena');
    });

    /** macOS ships no female en-GB voice — its British voices are male. */
    it('prefers an American female over the male British voice macOS ships', () => {
      const pick = pickVoice([v('Daniel', 'en-GB'), v('Samantha', 'en-US')]);
      expect(pick?.name).toBe('Samantha');
    });

    it('prefers a female of any English locale over a male British voice', () => {
      const pick = pickVoice([v('Oliver', 'en-GB'), v('Victoria', 'en-US')]);
      expect(pick?.name).toBe('Victoria');
    });

    /** Better a male British voice than the device's default. */
    it('falls back to a male British voice when nothing female exists', () => {
      const pick = pickVoice([v('Daniel', 'en-GB'), v('Alex', 'en-US')]);
      expect(pick?.name).toBe('Daniel');
    });

    it('takes a female voice rather than none when that is all there is', () => {
      expect(pickVoice([v('Serena', 'en-GB')])?.name).toBe('Serena');
    });

    it('avoids the compact renderings whatever the gender', () => {
      const pick = pickVoice([v('Kate (Compact)', 'en-GB'), v('Sonia', 'en-GB')]);
      expect(pick?.name).toBe('Sonia');
    });
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
    // The picked voice's own locale drives the utterance; WebKit ignores an
    // assigned voice whose language disagrees with the utterance's.
    expect(spoken[0]!.lang).toBe('en-US');
  });

  it('still speaks when the device offers no voices at all', () => {
    voices = [];
    speak('hello');
    expect(spoken).toHaveLength(1);
    expect(spoken[0]!.voice).toBeNull();
  });

  /** Chrome returns an empty list until `voiceschanged` fires after load. */
  it('waits for voices to load instead of falling back to the default', () => {
    const listeners: { onVoicesChanged: (() => void) | null } = { onVoicesChanged: null };
    vi.stubGlobal('speechSynthesis', {
      cancel,
      speak: (u: FakeUtterance) => void spoken.push(u),
      getVoices: () => voices,
      addEventListener: (_event: string, handler: () => void) => {
        listeners.onVoicesChanged = handler;
      },
    });
    voices = [];

    speak('hello');
    expect(spoken).toHaveLength(0); // nothing yet — waiting for the list

    voices = [{ name: 'Serena', lang: 'en-GB', localService: true } as SpeechSynthesisVoice];
    listeners.onVoicesChanged?.();
    expect(spoken).toHaveLength(1);
    expect(spoken[0]!.voice).toMatchObject({ lang: 'en-GB', name: 'Serena' });
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
