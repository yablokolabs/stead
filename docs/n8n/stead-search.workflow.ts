/**
 * `Stead Search` — web search for the Stead agent, called as a sub-workflow.
 *
 * THIS FILE IS THE ONLY COPY OUTSIDE n8n CLOUD, as with stead-web.workflow.ts.
 *
 * It exists because `@n8n/n8n-nodes-langchain.toolHttpRequest` DOES NOT WORK on
 * this n8n build. Every call fails with
 *
 *   The node "@n8n/n8n-nodes-langchain.toolHttpRequest" has a "supplyData"
 *   method but no "execute" method
 *
 * at typeVersion 1 and 1.1 alike, with correct ai_tool wiring and an attached
 * credential. An ordinary `n8n-nodes-base.httpRequest` works perfectly — so the
 * request lives here, and `Stead Web` reaches it through `toolWorkflow`.
 *
 * Recreate with: validate_workflow → create_workflow_from_code →
 * publish_workflow, then attach the `Firecrawl` Header Auth credential by hand,
 * because credential auto-assignment skips HTTP Request nodes.
 */
import { workflow, node, trigger, newCredential } from '@n8n/workflow-sdk';

const searchTrigger = trigger({
  type: 'n8n-nodes-base.executeWorkflowTrigger',
  version: 1.2,
  config: {
    name: 'Search Trigger',
    parameters: {
      inputSource: 'workflowInputs',
      workflowInputs: { values: [{ name: 'query', type: 'string' }] },
    },
    position: [0, 0],
  },
  output: [{ query: 'weather in London today' }],
});

/**
 * Firecrawl, because there is no alternative rather than because it is right.
 * SECURITY.md records what it costs: household queries now reach a vendor
 * account, reversing the reason Firecrawl was adopted here as a page reader.
 */
const firecrawl = node({
  type: 'n8n-nodes-base.httpRequest',
  version: 4.2,
  config: {
    name: 'Firecrawl Search',
    parameters: {
      method: 'POST',
      url: 'https://api.firecrawl.dev/v2/search',
      authentication: 'genericCredentialType',
      genericAuthType: 'httpHeaderAuth',
      sendBody: true,
      contentType: 'json',
      specifyBody: 'json',
      jsonBody: '={{ JSON.stringify({ query: $json.query, limit: 5 }) }}',
      options: { timeout: 30000, response: { response: { responseFormat: 'json' } } },
    },
    credentials: { httpHeaderAuth: newCredential('Firecrawl') },
    position: [220, 0],
  },
  output: [{ success: true, data: { web: [{ title: 'London - BBC Weather', description: '…', url: 'https://bbc.co.uk/weather' }] } }],
});

/**
 * Not cosmetic. Firecrawl's `description` is scraped markdown, not a snippet:
 * a single weather search returned several thousand tokens of embedded links,
 * images and table markup. Two consequences, both fixed here rather than asked
 * for in the prompt:
 *
 *   the agent was receiving URLs, which it can then read aloud one character
 *   at a time — the exact failure web/src/lib/text.ts exists to catch
 *
 *   one search cost a large share of the context window
 *
 * Watch the escaping. A previous version double-escaped these patterns, every
 * match failed, snippets came back empty, and Stead answered "I could not find
 * it" for everything — a silent regression that looked like a search outage.
 */
const shape = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Shape Results',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: [
        "const body = $input.first().json || {};",
        "const web = (body.data && body.data.web) || [];",
        "",
        "function clean(text) {",
        "  var s = String(text || '');",
        "  s = s.replace(/!\\[[^\\]]*\\]\\([^)]*\\)/g, ' ');",
        "  s = s.replace(/\\[([^\\]]*)\\]\\([^)]*\\)/g, '$1');",
        "  s = s.replace(/https?:\\/\\/\\S+/g, ' ');",
        "  s = s.replace(/[#*_`>|]+/g, ' ');",
        "  s = s.replace(/\\s+/g, ' ');",
        "  return s.trim().slice(0, 400);",
        "}",
        "",
        "var results = [];",
        "for (var i = 0; i < web.length && results.length < 5; i++) {",
        "  var title = clean(web[i].title);",
        "  var snippet = clean(web[i].description || web[i].snippet);",
        "  if (title || snippet) results.push({ title: title, snippet: snippet });",
        "}",
        "",
        "if (results.length === 0) {",
        "  return [{ json: { found: false, note: 'No results. Say you could not find it; do not guess.' } }];",
        "}",
        "return [{ json: { found: true, results: results } }];",
      ].join('\n'),
    },
    position: [440, 0],
  },
  output: [{ found: true, results: [{ title: 'London - BBC Weather', snippet: 'Light rain and a gentle breeze High25° Low15°' }] }],
});

export default workflow('stead-search', 'Stead Search')
  .add(searchTrigger)
  .to(firecrawl)
  .to(shape);
