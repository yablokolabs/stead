/**
 * `Stead Web` — the n8n workflow the Cloudflare Worker calls.
 *
 * THIS FILE IS THE ONLY COPY OUTSIDE n8n CLOUD. The workflow was built through
 * n8n's MCP interface, which stores it in the account and nowhere else. If that
 * account is lost, closed, or the instance is rebuilt, this file is what
 * recreates it. Keep it in step with the live workflow.
 *
 * Recreate with the n8n MCP tools:
 *   validate_workflow  → create_workflow_from_code → publish_workflow
 *
 * Two things this file cannot carry, both by design:
 *   - the Header Auth credential's value (a secret)
 *   - the OpenAI credential
 * Both are recreated by hand; see docs/WEB_MVP_RUNBOOK.md.
 *
 * Every non-obvious parameter here was learned by something breaking in
 * production. Do not "tidy" one without reading the comment above it.
 */
import {
  workflow,
  node,
  trigger,
  newCredential,
  ifElse,
  languageModel,
  memory,
  tool,
  expr,
  nodeJson,
  sticky,
} from '@n8n/workflow-sdk';

/** Identical to both agents in `Stead Telegram`. Keep the three in step. */
/**
 * An EXPRESSION, not a plain string — note the leading `=`.
 *
 * Asked "what day is today", Stead replied that it could not access the web to
 * look up the date. The model has no clock; n8n does, and nothing was passing
 * it. `$now` closes that. Identical across all three agents, here and in
 * Stead Telegram.
 */
const STEAD_PROMPT =
  "=You are Stead, an AI household manager for busy families.\n\nRIGHT NOW\n\nIt is {{ $now.setZone('Europe/London').toFormat('cccc d LLLL yyyy, HH:mm') }} in the household's timezone, Europe/London. You know this. Never say you cannot tell what day or time it is, and never call the date something you would have to look up. Work out days of the week, tomorrow, next Tuesday and how long until something from it.\n\nHOW YOU SPEAK — READ THIS FIRST\n\nYour reply is spoken aloud by a synthetic voice. Write only what a person would actually say.\n\n- Never use bullet points, numbered lists, headings, asterisks, or markdown of any kind. Not once.\n- Never include a URL, a link, or a citation of any kind.\n- Answer in one to three sentences. Go longer only if explicitly asked for detail.\n- Lead with the answer. Do not announce what you are about to do.\n- Do not offer a menu of options. Choose the most useful next step and say it, or ask one short question.\n\nBad: \"You have the following events: 1. Dentist at 10:00 2. Swimming at 16:00\"\nGood: \"You've got the dentist at ten and swimming at four.\"\n\nBad: \"I can't access your email. I can still help — pick one: - paste the emails you want checked - or tell me which provider you use\"\nGood: \"I can't get into your email yet. Tell me what's in it and I'll keep track of it for you.\"\n\nWHAT YOU CAN DO\n\nRemember. You carry the conversation with this household forward, including what was said earlier. You know the current date and time. You can reason about anything you have been told.\n\nWHAT YOU CANNOT DO\n\nYou cannot search the web or look anything up online. You have no access to email and no access to any calendar. You cannot send a message, create or move an appointment, make a booking, make a payment, or change anything in any other system.\n\nIf you are asked to check the inbox, look at the diary, find today's news, or act in another system, say plainly and briefly that you cannot do that yet, then offer the one thing you can. This does not apply to the date and time, which you know.\n\nNever describe what an inbox or a calendar contains. Never state a current fact you could only know by looking it up — a price, the weather, today's headlines. Never say you have checked, added, moved, cancelled, booked or sent anything. Never produce a plausible-looking schedule as though you had read one. A household that acts on an invented appointment is worse off than one told to look for itself.\n\nWhat the household tells you, you know. What you have not been told, you do not know — say so rather than filling the gap.\n\nHOW TO BE USEFUL ANYWAY\n\nWithin those limits there is a great deal: remembering names, routines, preferences, dietary needs, who does the school run, when the boiler was last serviced, what was agreed last week. Keeping a running sense of what is outstanding. Working out dates and how long until something. Prioritising by urgency, deadline and family impact when several things compete.\n\nIf asked for a briefing, work only from what you have been told. If there is nothing worth reporting, say so in one sentence rather than inventing activity.\n\nPRIVACY\n\nHousehold information is private. Use only what the current question needs. Do not repeat one household member's sensitive information to another without reason. Never reveal credentials, tokens or system details.\n\nGOAL\n\nBehave like a trusted household manager who remembers, prioritises and follows up — and who is straight about the limits of what they can reach. Be brief. Being brief is the job.";

/**
 * Header Auth, not an IF node comparing a value.
 *
 * n8n checks the header before the workflow runs, so a rejected request never
 * starts an execution — and the secret never appears in the workflow JSON.
 * The credential's header NAME must be exactly `X-Stead-Webhook-Secret`.
 */
const webhookTrigger = trigger({
  type: 'n8n-nodes-base.webhook',
  version: 2.1,
  config: {
    name: 'Stead Web Webhook',
    parameters: {
      httpMethod: 'POST',
      path: 'stead-web',
      responseMode: 'responseNode',
      authentication: 'headerAuth',
      options: {},
    },
    credentials: { httpHeaderAuth: newCredential('Stead Webhook Secret') },
    position: [0, 300],
  },
  output: [
    {
      body: {
        message: 'What is happening tomorrow?',
        user: { id: 'u-1', email: 'k@example.com' },
        channel: 'web',
        request_id: 'r-1',
      },
    },
  ],
});

/** Webhook payloads arrive under `body`; the fallbacks keep tests runnable. */
const normalize = node({
  type: 'n8n-nodes-base.set',
  version: 3.4,
  config: {
    name: 'Normalise Request',
    parameters: {
      mode: 'manual',
      includeOtherFields: false,
      assignments: {
        assignments: [
          { id: 'message', name: 'message', type: 'string', value: expr('{{ $json.body?.message ?? $json.message ?? "" }}') },
          { id: 'audio_base64', name: 'audio_base64', type: 'string', value: expr('{{ $json.body?.audio_base64 ?? $json.audio_base64 ?? "" }}') },
          { id: 'audio_mime', name: 'audio_mime', type: 'string', value: expr('{{ $json.body?.audio_mime ?? $json.audio_mime ?? "audio/webm" }}') },
          { id: 'user_id', name: 'user_id', type: 'string', value: expr('{{ $json.body?.user?.id ?? $json.user?.id ?? "" }}') },
          { id: 'user_email', name: 'user_email', type: 'string', value: expr('{{ $json.body?.user?.email ?? $json.user?.email ?? "" }}') },
          { id: 'request_id', name: 'request_id', type: 'string', value: expr('{{ $json.body?.request_id ?? $json.request_id ?? "" }}') },
        ],
      },
      options: {},
    },
    position: [220, 300],
  },
  output: [{ message: 'What is happening tomorrow?', audio_base64: '', audio_mime: 'audio/webm', user_id: 'u-1', user_email: 'k@example.com', request_id: 'r-1' }],
});

const isAudio = ifElse({
  version: 2.2,
  config: {
    name: 'Spoken?',
    parameters: {
      conditions: {
        options: { caseSensitive: true, leftValue: '', typeValidation: 'loose', version: 2 },
        conditions: [
          { id: 'has-audio', leftValue: expr('{{ $json.audio_base64 }}'), operator: { type: 'string', operation: 'notEmpty', singleValue: true } },
        ],
        combinator: 'and',
      },
      options: {},
    },
    position: [440, 300],
  },
});

/**
 * The filename extension is load-bearing.
 *
 * OpenAI infers the audio container from the FILENAME, not from the MIME type.
 * Naming this "voice-note" with no extension made Whisper reject every real
 * browser recording with "Invalid file format" — while listing webm as
 * supported. Chrome sends webm, Safari and iOS send mp4; both must be labelled.
 */
const decodeAudio = node({
  type: 'n8n-nodes-base.convertToFile',
  version: 1.1,
  config: {
    name: 'Decode Audio',
    parameters: {
      operation: 'toBinary',
      sourceProperty: 'audio_base64',
      options: {
        fileName: expr(
          "{{ 'voice-note.' + ({'audio/webm':'webm','audio/ogg':'ogg','audio/mp4':'mp4','audio/mpeg':'mp3','audio/mp3':'mp3','audio/wav':'wav','audio/x-m4a':'m4a','audio/m4a':'m4a','audio/flac':'flac'}[$json.audio_mime] || 'webm') }}",
        ),
        mimeType: expr('{{ $json.audio_mime }}'),
      },
    },
    position: [660, 180],
  },
  output: [{ audio_base64: 'GkXfo59', audio_mime: 'audio/webm' }],
});

/**
 * Sarvam, not OpenAI.
 *
 * OpenAI's transcription died twice — n8n's free credits ran out, then a real
 * key on an unfunded account returned insufficient_quota. Sarvam is the vendor
 * the Telegram preview already uses for speech, and it is faster here: 1,182 ms
 * against Whisper's 1,851 ms on the same clip.
 *
 * It is also more honest. Given a sine tone with no speech in it, Whisper
 * hallucinated the word "You"; Sarvam returned an empty transcript, which is
 * what `Heard Anything?` below exists to handle.
 *
 * The field is `transcript`, not Whisper's `text`. Its English is en-IN — see
 * ARCHITECTURE.md on why Stead has no British-accented recogniser.
 *
 * Auth is a Header Auth credential named `api-subscription-key`; the value
 * cannot live here.
 */
const transcribe = node({
  type: 'n8n-nodes-base.httpRequest',
  version: 4.2,
  config: {
    name: 'Transcribe Voice Note',
    parameters: {
      method: 'POST',
      url: 'https://api.sarvam.ai/speech-to-text',
      authentication: 'genericCredentialType',
      genericAuthType: 'httpHeaderAuth',
      sendBody: true,
      contentType: 'multipart-form-data',
      bodyParameters: {
        parameters: [
          { parameterType: 'formBinaryData', name: 'file', inputDataFieldName: 'data' },
          { name: 'model', value: 'saarika:v2.5' },
          { name: 'language_code', value: 'en-IN' },
        ],
      },
      options: { timeout: 60000, response: { response: { responseFormat: 'json' } } },
    },
    credentials: { httpHeaderAuth: newCredential('Sarvam') },
    position: [880, 180],
  },
  output: [{ request_id: '20260817_x', transcript: 'What is happening tomorrow?', language_code: 'en-IN' }],
});

/**
 * An empty transcript must never reach the agent.
 *
 * Silence, a muted microphone, or tapping record and saying nothing all produce
 * one, and the agent answered with an empty string. Same principle as never
 * letting it describe an inbox it cannot read: say what happened.
 */
const heardAnything = ifElse({
  version: 2.2,
  config: {
    name: 'Heard Anything?',
    parameters: {
      conditions: {
        options: { caseSensitive: true, leftValue: '', typeValidation: 'loose', version: 2 },
        conditions: [
          { id: 'heard-something', leftValue: expr('{{ $json.transcript }}'), operator: { type: 'string', operation: 'notEmpty', singleValue: true } },
        ],
        combinator: 'and',
      },
      options: {},
    },
    position: [1000, 180],
  },
});

const heardNothing = node({
  type: 'n8n-nodes-base.set',
  version: 3.4,
  config: {
    name: 'Heard Nothing',
    parameters: {
      mode: 'manual',
      includeOtherFields: false,
      assignments: {
        assignments: [
          { id: 'reply', name: 'reply', type: 'string', value: "I didn't catch that. Try again?" },
          { id: 'transcript', name: 'transcript', type: 'string', value: '' },
        ],
      },
      options: {},
    },
    position: [1200, 40],
  },
  output: [{ reply: "I didn't catch that. Try again?", transcript: '' }],
});

const voicePrompt = node({
  type: 'n8n-nodes-base.set',
  version: 3.4,
  config: {
    name: 'Prompt From Speech',
    parameters: {
      mode: 'manual',
      includeOtherFields: false,
      assignments: { assignments: [{ id: 'prompt', name: 'prompt', type: 'string', value: expr('{{ $json.transcript }}') }] },
      options: {},
    },
    position: [1100, 180],
  },
  output: [{ prompt: 'What is happening tomorrow?' }],
});

const textPrompt = node({
  type: 'n8n-nodes-base.set',
  version: 3.4,
  config: {
    name: 'Prompt From Text',
    parameters: {
      mode: 'manual',
      includeOtherFields: false,
      assignments: { assignments: [{ id: 'prompt', name: 'prompt', type: 'string', value: expr('{{ $json.message }}') }] },
      options: {},
    },
    position: [660, 420],
  },
  output: [{ prompt: 'What is happening tomorrow?' }],
});

/**
 * Google Gemini, not OpenAI.
 *
 * The OpenAI path died twice: n8n's free AI credits ran out, and a real key on
 * an unfunded account returns `insufficient_quota`. Gemini is what the Python
 * side of Stead already uses, and it is faster here — a typed turn measured
 * 0.8-1.4 s against ~2.2 s on gpt-5-mini.
 *
 * It has NO built-in web search. The system prompt above must not claim it
 * does: an agent told it can look things up when it cannot is the same fault
 * as one told it can read email.
 */
const steadModel = languageModel({
  type: '@n8n/n8n-nodes-langchain.lmChatGoogleGemini',
  version: 1.1,
  config: {
    name: 'Stead Web Model',
    parameters: {
      modelName: 'models/gemini-3.1-flash-lite',
      options: { temperature: 0.4, maxOutputTokens: 2048 },
    },
    credentials: { googlePalmApi: newCredential('Google Gemini') },
    position: [1320, 480],
  },
});

/**
 * Keyed on the VERIFIED Supabase user id — the identity boundary reaching its
 * destination. `Stead Telegram` keys on a chat id; do not copy that here.
 * Pointing this at anything the browser can influence lets one household read
 * another. `nodeJson` rather than `$json` because a subnode has no main input.
 */
const steadMemory = memory({
  type: '@n8n/n8n-nodes-langchain.memoryBufferWindow',
  version: 1.4,
  config: {
    name: 'Stead Web Memory',
    parameters: { sessionIdType: 'customKey', sessionKey: nodeJson(normalize, 'user_id'), contextWindowLength: 20 },
    position: [1460, 480],
  },
});

const steadAgent = node({
  type: '@n8n/n8n-nodes-langchain.agent',
  version: 3.1,
  config: {
    name: 'Stead Web Agent',
    parameters: {
      promptType: 'define',
      text: expr('{{ $json.prompt }}'),
      options: { systemMessage: STEAD_PROMPT, maxIterations: 10, enableStreaming: false },
    },
    subnodes: { model: steadModel, memory: steadMemory, tools: [searchTheWeb] },
    position: [1320, 300],
  },
  output: [{ output: 'Nothing in the diary tomorrow.' }],
});

/** Whether to include a transcript. Re-reads the request, since after the
 *  merge `$json` is the agent's output rather than the original fields. */
const wasSpoken = ifElse({
  version: 2.2,
  config: {
    name: 'Spoken Turn?',
    parameters: {
      conditions: {
        options: { caseSensitive: true, leftValue: '', typeValidation: 'loose', version: 2 },
        conditions: [
          { id: 'came-from-speech', leftValue: nodeJson(normalize, 'audio_base64'), operator: { type: 'string', operation: 'notEmpty', singleValue: true } },
        ],
        combinator: 'and',
      },
      options: {},
    },
    position: [1540, 300],
  },
});

/**
 * No server-side text-to-speech.
 *
 * `tts-1` cost about 3s per spoken turn plus a base64 payload a third larger
 * than the audio itself, and it could not start playing until the whole file
 * had arrived. The browser's own speechSynthesis is instant, free, and honours
 * `lang: en-GB` — which gets closer to the British voice ARCHITECTURE.md argues
 * for than `alloy` ever did. The voice branch now returns text only.
 */
const voiceReply = node({
  type: 'n8n-nodes-base.set',
  version: 3.4,
  config: {
    name: 'Voice Reply',
    parameters: {
      mode: 'manual',
      includeOtherFields: false,
      assignments: {
        assignments: [
          { id: 'reply', name: 'reply', type: 'string', value: expr('{{ $json.output }}') },
          { id: 'transcript', name: 'transcript', type: 'string', value: expr('{{ $(\'Transcribe Voice Note\').item.json.text }}') },
        ],
      },
      options: {},
    },
    position: [1760, 180],
  },
  output: [{ reply: 'Nothing in the diary tomorrow.', transcript: 'What is happening tomorrow?' }],
});

/** `firstIncomingItem`, not `allEntries` — the latter is not a valid option
 *  value and silently degrades to "must be an expression". */
const respondSpoken = node({
  type: 'n8n-nodes-base.respondToWebhook',
  version: 1.1,
  config: { name: 'Respond With Voice', parameters: { respondWith: 'firstIncomingItem', options: {} }, position: [2200, 180] },
  output: [{ reply: 'Nothing in the diary tomorrow.', transcript: 'What is happening tomorrow?' }],
});

const textReply = node({
  type: 'n8n-nodes-base.set',
  version: 3.4,
  config: {
    name: 'Text Reply',
    parameters: {
      mode: 'manual',
      includeOtherFields: false,
      assignments: { assignments: [{ id: 'reply', name: 'reply', type: 'string', value: expr('{{ $json.output }}') }] },
      options: {},
    },
    position: [1760, 420],
  },
  output: [{ reply: 'Nothing in the diary tomorrow.' }],
});

const respondText = node({
  type: 'n8n-nodes-base.respondToWebhook',
  version: 1.1,
  config: { name: 'Respond With Text', parameters: { respondWith: 'firstIncomingItem', options: {} }, position: [1980, 420] },
  output: [{ reply: 'Nothing in the diary tomorrow.' }],
});

/**
 * Search, and the two traps that cost a broken deployment.
 *
 * `toolHttpRequest` lists `httpBearerAuth` among its `genericAuthType` values
 * and REJECTS IT AT RUNTIME — "The type httpBearerAuth is not supported". The
 * credential must be Header Auth carrying `Authorization: Bearer fc-…`.
 *
 * And a misconfigured tool fails at CONFIG time, which fails the whole run: the
 * agent resolves its tools before doing anything, so typed questions that would
 * never have searched returned empty bodies until this was reverted. Attach a
 * tool, prove it harmless, then let the prompt advertise it — in that order.
 *
 * Household queries now reach a vendor account. SECURITY.md carries what that
 * costs and why the prompt's "search the world, never the household" rule is
 * not a boundary in the sense the rest of that document means.
 */
const searchTheWeb = tool({
  type: '@n8n/n8n-nodes-langchain.toolHttpRequest',
  version: 1.1,
  config: {
    name: 'Search The Web',
    parameters: {
      toolDescription:
        'Search the public web for something happening in the world right now — news, weather, opening times, prices, travel. Returns a handful of results, each with a title, a URL and a short snippet. Use it only when the answer depends on current information the household has not told you. Never search for anything about this household; that is private.',
      method: 'POST',
      url: 'https://api.firecrawl.dev/v2/search',
      authentication: 'genericCredentialType',
      genericAuthType: 'httpHeaderAuth',
      sendBody: true,
      specifyBody: 'json',
      jsonBody: '{"query": "{query}", "limit": 5}',
      placeholderDefinitions: {
        values: [
          {
            name: 'query',
            description:
              'What to search for, phrased as you would type it into a search engine. Never include household names, addresses, health details or anything private.',
            type: 'string',
          },
        ],
      },
      optimizeResponse: true,
      responseType: 'json',
      // Firecrawl v2 returns { success, data: { web: [...] } }.
      dataField: 'data.web',
    },
    credentials: { httpHeaderAuth: newCredential('Firecrawl') },
    position: [1180, 620],
  },
});

const identityNote = sticky(
  '## Identity comes from the Cloudflare Worker, not from here\n\nThe webhook is protected by Header Auth: the Worker sends X-Stead-Webhook-Secret and n8n refuses anything else. Trust user.id only because that check passed.\n\nMemory is keyed on the verified Supabase user id, NOT on a chat id. Changing that key to anything the browser can influence would let one household read another.',
  [webhookTrigger, normalize],
  { color: 3 },
);

export default workflow('stead-web', 'Stead Web')
  .add(webhookTrigger)
  .to(normalize)
  .to(
    isAudio
      .onTrue(
        decodeAudio.to(transcribe).to(
          heardAnything.onTrue(voicePrompt.to(steadAgent)).onFalse(heardNothing.to(respondSpoken)),
        ),
      )
      .onFalse(textPrompt.to(steadAgent)),
  )
  .add(steadAgent)
  .to(wasSpoken.onTrue(voiceReply.to(respondSpoken)).onFalse(textReply.to(respondText)))
  .add(identityNote);
