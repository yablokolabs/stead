import { describe, expect, it } from 'vitest';
import { speakableText } from './text';

describe('speakableText', () => {
  /** The reply that started this: read aloud it became "w w w dot axios dot com". */
  it('removes a web-search citation entirely', () => {
    const reply =
      'Top AI stories right now: the White House has finalized a framework. ' +
      '([axios.com](https://www.axios.com/2026/08/03/white-house-finalizes-ai-framework?utm_source=openai))';

    const spoken = speakableText(reply);

    expect(spoken).toBe(
      'Top AI stories right now: the White House has finalized a framework.',
    );
    expect(spoken).not.toContain('http');
    expect(spoken).not.toContain('www');
    expect(spoken).not.toContain('axios');
  });

  it('removes several citations from one reply', () => {
    const reply =
      'One thing happened. ([a.com](https://a.com/x)) Another thing did too. ([b.org](https://b.org/y?utm_source=openai))';
    expect(speakableText(reply)).toBe('One thing happened. Another thing did too.');
  });

  it('keeps the label of a link that is part of the sentence', () => {
    expect(speakableText('See [the guidance](https://gov.uk/thing) for details.')).toBe(
      'See the guidance for details.',
    );
  });

  it('strips a bare URL wherever it appears', () => {
    expect(speakableText('Book at https://dentist.example.com/appointments now.')).toBe(
      'Book at now.',
    );
    expect(speakableText('Try www.example.co.uk today.')).toBe('Try today.');
  });

  /** Hyphens in a URL slug are what was being read out as "dash dash dash". */
  it('leaves no hyphenated slug behind', () => {
    const spoken = speakableText('News. ([x](https://x.com/white-house-finalizes-ai-framework))');
    expect(spoken).not.toContain('-');
  });

  it('strips markdown the prompt asked the model not to use', () => {
    const reply = '## Tomorrow\n\n- **Dentist** at ten\n- *Swimming* at four\n\nUse `the car`.';
    const spoken = speakableText(reply);
    expect(spoken).not.toMatch(/[*_`#]/);
    expect(spoken).toContain('Dentist at ten');
    expect(spoken).toContain('Swimming at four');
    expect(spoken).toContain('Use the car.');
  });

  it('leaves an ordinary reply completely alone', () => {
    const reply = "You've got the dentist at ten and swimming at four.";
    expect(speakableText(reply)).toBe(reply);
  });

  it('does not mangle punctuation or apostrophes', () => {
    const reply = "I can't get into your email yet — tell me what's in it.";
    expect(speakableText(reply)).toBe(reply);
  });

  it('survives an empty or whitespace-only reply', () => {
    expect(speakableText('')).toBe('');
    expect(speakableText('   \n  ')).toBe('');
  });
});
