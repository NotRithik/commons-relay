import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { TokenBudget } from '../../adapters/pi/openai-responses.mjs';
import { inferenceEndpoint, chatPayload, decodeInference, createInferenceStream } from '../../adapters/pi/inference-transport.mjs';
const context = { messages: [{ role: 'user', content: 'synthetic test' }], systemPrompt: 'Fixture only.', tools: [] };
function fixture(fn) {
  const dir = mkdtempSync(join(tmpdir(), 'relay-inference-'));
  const budget = new TokenBudget({ path: join(dir, 'budget.json'), maximumUsd: .1, inputUsdPerMillion: .2, outputUsdPerMillion: 1.2 });
  return Promise.resolve().then(() => fn(budget, dir)).finally(() => rmSync(dir, { recursive: true, force: true }));
}
const response = { id: 'synthetic-response', status: 'completed', usage: { input_tokens: 12, output_tokens: 3 },
  output: [{ type: 'message', content: [{ type: 'output_text', text: 'Synthetic fixture reply.' }] }] };
const chat = { id: 'synthetic-chat', choices: [{ message: { role: 'assistant', content: 'Synthetic chat reply.' }, finish_reason: 'stop' }],
  usage: { prompt_tokens: 12, completion_tokens: 3 } };
function fake(value) { return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } }); }

test('only explicit canonical remote HTTPS or exact loopback HTTP endpoints are accepted', () => {
  for (const value of ['https://inference.example/v1', 'http://127.0.0.1:11434/v1', 'http://localhost:8080', 'http://[::1]:8000/v1'])
    assert.equal(inferenceEndpoint(value), value);
  assert.equal(inferenceEndpoint('https://API.example/v1/'), 'https://api.example/v1');
  for (const value of ['http://example.test/v1', 'http://127.1/v1', 'http://0x7f000001/v1', 'http://2130706433/v1',
    'http://localhost.attacker.test/v1', 'https://user:key@example.test/v1', 'https://example.test/a/../v1',
    'https://example.test/%2e/v1', 'https://example.test/v1?key=x', 'https://example.test/v1#part',
    'https://example.test:0/v1', 'https://example.test:/v1', 'https://example.test/v1/responses', 'file:///tmp/test'])
    assert.throws(() => inferenceEndpoint(value));
});
test('custom Responses endpoint receives only its configured key and no automatic fallback', () => fixture(async budget => {
  let observed;
  const stream = createInferenceStream({ endpoint: 'https://model.example/v1', api: 'responses', modelId: 'vendor/model:version',
    apiKey: 'synthetic-provider-token', budget, fetchImpl: async (url, options) => { observed = { url, options }; return fake(response); } });
  const result = await stream({}, context).result();
  assert.equal(result.stopReason, 'stop'); assert.equal(observed.url, 'https://model.example/v1/responses');
  assert.equal(observed.options.headers.Authorization, 'Bearer synthetic-provider-token');
  assert.equal(observed.options.redirect, 'error');
  assert.equal(JSON.parse(observed.options.body).model, 'vendor/model:version');
  assert.equal(JSON.parse(observed.options.body).reasoning, undefined);
  assert.equal(budget.summary().completed_requests, 1);
}));
test('anonymous local Chat Completions never forwards the existing OpenAI key', () => fixture(async budget => {
  let observed;
  const stream = createInferenceStream({ endpoint: 'http://127.0.0.1:11434/v1', api: 'chat-completions', modelId: 'local-model:latest',
    apiKey: '', budget, fetchImpl: async (url, options) => { observed = { url, options }; return fake(chat); } });
  const result = await stream({}, context).result();
  assert.equal(result.content[0].text, 'Synthetic chat reply.');
  assert.equal(observed.url, 'http://127.0.0.1:11434/v1/chat/completions');
  assert.equal(observed.options.headers.Authorization, undefined);
  const payload = JSON.parse(observed.options.body);
  assert.equal(payload.max_tokens, 1536); assert.equal(payload.max_output_tokens, undefined);
  assert.equal(payload.messages[0].role, 'system'); assert.equal(result.usage.input, 12);
}));
test('official Chat endpoint uses max_completion_tokens', () => {
  const payload = chatPayload(context, 'gpt-test', 1024, true);
  assert.equal(payload.max_completion_tokens, 1024); assert.equal(payload.max_tokens, undefined);
});
test('chat function calls and tool results use matching tool_call_id', () => {
  const ctx = { ...context, messages: [...context.messages,
    { role: 'assistant', content: [{ type: 'toolCall', id: 'call-1', name: 'relay_storage_list', arguments: {} }] },
    { role: 'toolResult', toolCallId: 'call-1', content: [{ type: 'text', text: '{"files":[]}' }] }] };
  const body = chatPayload(ctx, 'custom-model', 1000);
  assert.equal(body.messages[2].tool_calls[0].id, 'call-1');
  assert.equal(body.messages[3].tool_call_id, 'call-1');
  const value = decodeInference({ ...chat, choices: [{ finish_reason: 'tool_calls', message: { role: 'assistant', content: null,
    tool_calls: [{ id: 'call-2', type: 'function', function: { name: 'relay_storage_list', arguments: '{}' } }] } }] }, 'chat-completions');
  assert.equal(value.reason, 'toolUse'); assert.deepEqual(value.content[0].arguments, {});
});
test('network uncertainty keeps reservation and does not retry', () => fixture(async budget => {
  let calls = 0;
  const stream = createInferenceStream({ endpoint: 'https://model.example/v1', api: 'responses', modelId: 'custom', budget,
    fetchImpl: async () => { ++calls; throw new Error('synthetic failure'); } });
  const result = await stream({}, context).result();
  assert.equal(calls, 1); assert.equal(result.stopReason, 'error');
  assert.ok(budget.summary().reserved_for_unconfirmed_requests_usd > 0);
}));
test('missing usage cannot erase a potentially charged request', () => fixture(async budget => {
  const stream = createInferenceStream({ endpoint: 'https://model.example/v1', api: 'chat-completions', modelId: 'custom', budget,
    fetchImpl: async () => fake({ ...chat, usage: undefined }) });
  const result = await stream({}, context).result();
  assert.equal(result.errorMessage, 'API_USAGE_MISSING'); assert.equal(budget.summary().completed_requests, 0);
  assert.ok(budget.summary().reserved_for_unconfirmed_requests_usd > 0);
}));
test('HTTP errors are sanitized and never include provider error bodies or credentials', () => fixture(async budget => {
  const stream = createInferenceStream({ endpoint: 'https://model.example/v1', api: 'responses', modelId: 'custom', apiKey: 'synthetic-secret', budget,
    fetchImpl: async () => new Response('synthetic-secret echoed upstream', { status: 401 }) });
  const result = await stream({}, context).result();
  assert.equal(result.errorMessage, 'INFERENCE_HTTP_401'); assert.ok(!JSON.stringify(result).includes('synthetic-secret'));
}));
test('oversized streamed replies are canceled at the bound', () => fixture(async budget => {
  let canceled = false;
  const body = new ReadableStream({ pull(controller) { controller.enqueue(new Uint8Array(300000)); }, cancel() { canceled = true; } });
  const stream = createInferenceStream({ endpoint: 'https://model.example/v1', api: 'responses', modelId: 'custom', budget,
    fetchImpl: async () => new Response(body) });
  const result = await stream({}, context).result();
  assert.equal(result.errorMessage, 'API_RESPONSE_LIMIT'); assert.equal(canceled, true);
}));
test('malformed function arguments do not become tool requests', () => {
  const data = { ...response, output: [{ type: 'function_call', call_id: 'c1', name: 'relay_storage_list', arguments: 'not-json' }] };
  assert.throws(() => decodeInference(data, 'responses'));
});
test('the goal worker reads frozen snapshots and verifies credential digest', () => {
  const source = readFileSync(new URL('../../adapters/pi/goal-worker.mjs', import.meta.url), 'utf8');
  assert.match(source, /config\.credential_sha256/); assert.match(source, /createInferenceStream/);
  assert.doesNotMatch(source, /createOpenAIStream/);
});


test('shared reviewed URL fixture agrees with the Python validator contract', () => {
  const data = JSON.parse(readFileSync(new URL('../fixtures/inference-endpoint-cases.json', import.meta.url), 'utf8'));
  for (const item of data.base_url_cases) {
    if (item.valid) assert.equal(inferenceEndpoint(item.input), item.canonical, item.id);
    else assert.throws(() => inferenceEndpoint(item.input), undefined, item.id);
  }
});
