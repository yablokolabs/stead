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
  expr,
  nodeJson,
  sticky,
} from '@n8n/workflow-sdk';

/** Identical to both agents in `Stead Telegram`. Keep the three in step. */
const STEAD_PROMPT =
  'You are Stead, an AI household manager for busy families.\n\nHOW YOU SPEAK — READ THIS FIRST\n\nYour reply is spoken aloud by a synthetic voice. Write only what a person would actually say.\n\n- Never use bullet points, numbered lists, headings, asterisks, or markdown of any kind. Not once.\n- Answer in one to three sentences. Go longer only if explicitly asked for detail.\n- Lead with the answer. Do not announce what you are about to do.\n- Do not offer a menu of options. Choose the most useful next step and say it, or ask one short question.\n- Do not read out URLs unless asked.\n\nBad: "You have the following events: 1. Dentist at 10:00 2. Swimming at 16:00"\nGood: "You\'ve got the dentist at ten and swimming at four."\n\nBad: "I can\'t access your email. I can still help — pick one: - paste the emails you want checked - or tell me which provider you use and I\'ll give you step-by-step instructions"\nGood: "I can\'t get into your email yet. Tell me what\'s in it and I\'ll keep track of it for you."\n\nWHAT YOU CAN DO\n\nTwo things, and only these:\n\n- Remember. You carry the conversation with this household forward, including what was said earlier.\n- Search the web. You can look up public information.\n\nWHAT YOU CANNOT DO\n\nYou have no access to email and no access to any calendar. You cannot send a message, create or move an appointment, make a booking, make a payment, or change anything in any other system.\n\nIf you are asked to check the inbox, read a message, look at the diary, put something in a calendar, or act in another system, say plainly and briefly that you cannot do that yet, then offer the one thing you can.\n\nNever describe what an inbox or a calendar contains. Never say you have checked, added, moved, cancelled, booked or sent anything. Never produce a plausible-looking schedule as though you had read one. A household that acts on an invented appointment is worse off than one told to look for itself.\n\nWhat the household tells you, you know. What you have not been told, you do not know — say so rather than filling the gap.\n\nHOW TO BE USEFUL ANYWAY\n\nWithin those limits there is a great deal: remembering names, routines, preferences, dietary needs, who does the school run, when the boiler was last serviced, what was agreed last week. Keeping a running sense of what is outstanding. Prioritising by urgency, deadline and family impact when several things compete. Looking something up when a fact would settle the question.\n\nIf asked for a briefing, work only from what you have been told and what you can look up, and be clear which is which. If there is nothing worth reporting, say so in one sentence rather than inventing activity.\n\nPRIVACY\n\nHousehold information is private. Use only what the current question needs. Do not repeat one household member\'s sensitive information to another without reason. Never reveal credentials, tokens or system details. A web search leaves the household — never put personal details into a query.\n\nGOAL\n\nBehave like a trusted household manager who remembers, prioritises and follows up — and who is straight about the limits of what they can reach. Be brief. Being brief is the job.';

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
 * The prompt is not decoration.
 *
 * Whisper transcribed "Hey Stead" as "He's dead", and the agent replied by
 * offering emergency numbers. Priming it with the name and some household
 * vocabulary fixes that class of error. `language` pins English, which also
 * skips detection.
 */
const transcribe = node({
  type: '@n8n/n8n-nodes-langchain.openAi',
  version: 2.3,
  config: {
    name: 'Transcribe Voice Note',
    parameters: {
      resource: 'audio',
      operation: 'transcribe',
      binaryPropertyName: 'data',
      options: {
        language: 'en',
        prompt:
          "Hey Stead. Stead, what's on tomorrow? Stead is the household assistant being spoken to. Household topics: the school run, the dentist, the boiler service, swimming, shopping.",
      },
    },
    credentials: { openAiApi: newCredential('OpenAI') },
    position: [880, 180],
  },
  output: [{ text: 'What is happening tomorrow?' }],
});

const voicePrompt = node({
  type: 'n8n-nodes-base.set',
  version: 3.4,
  config: {
    name: 'Prompt From Speech',
    parameters: {
      mode: 'manual',
      includeOtherFields: false,
      assignments: { assignments: [{ id: 'prompt', name: 'prompt', type: 'string', value: expr('{{ $json.text }}') }] },
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
 * `builtInTools` is silently ignored unless `responsesApiEnabled` is also true.
 * Setting one without the other yields a prompt promising web search and an
 * agent that has none.
 *
 * `searchContextSize` is the single biggest latency lever in the whole path.
 * Measured on the same question: `high` cost the agent node 15,914 ms, `low`
 * cost 7,269 ms. The search runs provider-side inside OpenAI's Responses API —
 * `tool_calls.requested` stays 0 — so n8n's `maxIterations` does nothing here.
 */
const steadModel = languageModel({
  type: '@n8n/n8n-nodes-langchain.lmChatOpenAi',
  version: 1.3,
  config: {
    name: 'Stead Web Model',
    parameters: {
      model: { __rl: true, mode: 'list', value: 'gpt-5-mini' },
      responsesApiEnabled: true,
      builtInTools: { webSearch: { searchContextSize: 'low' } },
      options: {},
    },
    credentials: { openAiApi: newCredential('OpenAI') },
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
    subnodes: { model: steadModel, memory: steadMemory },
    position: [1320, 300],
  },
  output: [{ output: 'Nothing in the diary tomorrow.' }],
});

/** Re-reads the request, since after the merge `$json` is the agent's output. */
const wasSpoken = ifElse({
  version: 2.2,
  config: {
    name: 'Reply Aloud?',
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

/** Labels its output `audio/mp3`, which is not the registered MP3 type. The
 *  gateway's allowlist must contain that exact string or replies are dropped. */
const speak = node({
  type: '@n8n/n8n-nodes-langchain.openAi',
  version: 2.3,
  config: {
    name: 'Generate Speech',
    parameters: {
      resource: 'audio',
      operation: 'generate',
      model: 'tts-1',
      input: expr('{{ $json.output }}'),
      voice: 'alloy',
      options: { response_format: 'mp3', binaryPropertyOutput: 'data' },
    },
    credentials: { openAiApi: newCredential('OpenAI') },
    position: [1760, 180],
  },
  output: [{ output: 'Nothing in the diary tomorrow.' }],
});

/**
 * `binary.data.data` is NOT the payload when n8n stores binary on the
 * filesystem — it is the storage backend id, and this instance returned the
 * literal string "filesystem-v2" to the browser. Always use the helper.
 */
const encodeReply = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Encode Spoken Reply',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode:
        "const item = $input.first();\nconst meta = item.binary && item.binary.data ? item.binary.data : null;\n\n// binary.data.data is NOT the payload when n8n stores binary on the\n// filesystem — it is the storage backend id. Always go through the helper.\nconst buffer = await this.helpers.getBinaryDataBuffer(0, 'data');\n\nreturn [{ json: {\n  reply: $(\"Stead Web Agent\").first().json.output,\n  transcript: $(\"Transcribe Voice Note\").first().json.text,\n  audio_base64: buffer.toString('base64'),\n  audio_mime: (meta && meta.mimeType) || 'audio/mpeg'\n} }];",
    },
    position: [1980, 180],
  },
  output: [{ reply: 'Nothing in the diary tomorrow.', transcript: 'What is happening tomorrow?', audio_base64: 'SUQzB', audio_mime: 'audio/mp3' }],
});

/** `firstIncomingItem`, not `allEntries` — the latter is not a valid option
 *  value and silently degrades to "must be an expression". */
const respondSpoken = node({
  type: 'n8n-nodes-base.respondToWebhook',
  version: 1.1,
  config: { name: 'Respond With Voice', parameters: { respondWith: 'firstIncomingItem', options: {} }, position: [2200, 180] },
  output: [{ reply: 'Nothing in the diary tomorrow.', transcript: 'What is happening tomorrow?', audio_base64: 'SUQzB', audio_mime: 'audio/mp3' }],
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

const identityNote = sticky(
  '## Identity comes from the Cloudflare Worker, not from here\n\nThe webhook is protected by Header Auth: the Worker sends X-Stead-Webhook-Secret and n8n refuses anything else. Trust user.id only because that check passed.\n\nMemory is keyed on the verified Supabase user id, NOT on a chat id. Changing that key to anything the browser can influence would let one household read another.',
  [webhookTrigger, normalize],
  { color: 3 },
);

export default workflow('stead-web', 'Stead Web')
  .add(webhookTrigger)
  .to(normalize)
  .to(isAudio.onTrue(decodeAudio.to(transcribe).to(voicePrompt).to(steadAgent)).onFalse(textPrompt.to(steadAgent)))
  .add(steadAgent)
  .to(wasSpoken.onTrue(speak.to(encodeReply).to(respondSpoken)).onFalse(textReply.to(respondText)))
  .add(identityNote);
