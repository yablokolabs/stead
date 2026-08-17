import { describe, expect, it } from 'vitest';
import { pickRecordingMime, RECORDING_MIME_CANDIDATES } from './recorder';

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
