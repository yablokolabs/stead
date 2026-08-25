/**
 * Make a model's reply fit to be spoken.
 *
 * The system prompt asks for no markdown and no URLs. That is not enough, and
 * cannot be: web search injects citations into the answer after the model has
 * written it, so a reply arrives looking like
 *
 *   Top AI stories right now: … ([axios.com](https://www.axios.com/2026/08/03/
 *   white-house-finalizes-ai-framework?utm_source=openai))
 *
 * which a speech synthesiser reads as "h t t p s colon slash slash w w w dot
 * axios dot com slash two thousand twenty six slash…", and every hyphen in the
 * slug as "dash". Stripping it here is structural rather than advisory, so it
 * holds however the model behaves.
 *
 * Used for the displayed text too: without a markdown renderer, the raw
 * syntax is just as wrong on screen as it is in the ear.
 */
export function speakableText(reply: string): string {
  return (
    reply
      // A whole citation parenthetical — "([axios.com](https://…))" — is not
      // part of the sentence and should vanish, label and all.
      .replace(/\(\s*\[[^\]]*\]\([^)]*\)\s*\)/g, '')
      // Any remaining markdown link keeps its label and loses its target.
      .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
      // Bare URLs, with or without a scheme.
      .replace(/\bhttps?:\/\/\S+/gi, '')
      .replace(/\bwww\.\S+/gi, '')
      // Markdown emphasis, headings, code ticks, quote markers.
      .replace(/[*_`#>]+/g, ' ')
      // List bullets at the start of a line.
      .replace(/^[ \t]*[-–—•]\s+/gm, '')
      // Parens left empty by the removals above.
      .replace(/\(\s*[,.;:]*\s*\)/g, '')
      // Tidy the wreckage.
      .replace(/[ \t]{2,}/g, ' ')
      .replace(/[ \t]+([,.;:!?])/g, '$1')
      .replace(/\n{3,}/g, '\n\n')
      .split('\n')
      .map((line) => line.trim())
      .join('\n')
      .trim()
  );
}
