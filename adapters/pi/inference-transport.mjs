/** Explicit owner-configured Responses / Chat Completions transport.
 * No endpoint discovery, provider fallback, redirects, or automatic retry.
 * The API credential belongs only to this immutable endpoint configuration.
 */
import { createAssistantMessageEventStream } from '@earendil-works/pi-ai/utils/event-stream';
import { canonical } from './canonical.mjs';
import { responsePayload } from './openai-responses.mjs';

export function inferenceEndpoint(value) {
  if (typeof value !== 'string' || !value.length || value.length > 500
      || /[^\x21-\x7e]|[\\%?#]/.test(value)) throw new Error('INVALID_INFERENCE_ENDPOINT');
  let url;
  try { url = new URL(value); } catch { throw new Error('INVALID_INFERENCE_ENDPOINT'); }
  const match = value.match(/^(https?):\/\/([^/]+)(\/.*)?$/i);
  if (!match || url.username || url.password || !['http:', 'https:'].includes(url.protocol)
      || match[2].endsWith(':') || !/^[A-Za-z0-9._/-]*$/.test(match[3] || '')
      || (match[3] || '').split('/').some(x => ['.', '..'].includes(x)))
    throw new Error('INVALID_INFERENCE_ENDPOINT');
  const host = url.hostname.toLowerCase();
  if (!/^(?:[A-Za-z0-9.-]+|\[[0-9a-f:]+\])$/.test(host) || host.startsWith('.') || host.endsWith('.') || host.includes('..'))
    throw new Error('INVALID_INFERENCE_ENDPOINT');
  // WHATWG URL normalizes unusual numeric IP spellings. Reject these aliases,
  // rather than granting loopback HTTP access to a different raw host spelling.
  const rawHost = match[2].replace(/:\d+$/, '').toLowerCase();
  if (rawHost !== host) throw new Error('INVALID_INFERENCE_ENDPOINT');
  if (url.protocol === 'http:' && !['localhost', '127.0.0.1', '[::1]'].includes(host))
    throw new Error('INFERENCE_HTTPS_REQUIRED');
  const path = (match[3] || '').replace(/\/+$/, '');
  if (path.endsWith('/responses') || path.endsWith('/chat/completions'))
    throw new Error('INFERENCE_BASE_URL_REQUIRED');
  const portMatch = match[2].match(/:(\d+)$/);
  if (portMatch && !(Number(portMatch[1]) >= 1 && Number(portMatch[1]) <= 65535))
    throw new Error('INVALID_INFERENCE_ENDPOINT');
  const scheme = match[1].toLowerCase();
  return scheme + '://' + host + (portMatch && Number(portMatch[1]) !== (scheme === 'https' ? 443 : 80) ? ':' + Number(portMatch[1]) : '') + path;
}
function plain(content) {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) throw new Error('INVALID_MESSAGE_CONTENT');
  return content.map(item => {
    if (item.type !== 'text' || typeof item.text !== 'string') throw new Error('TEXT_ONLY_INFERENCE');
    return item.text;
  }).join('\n');
}
export function chatPayload(context, model, maximum, official = false) {
  if (!context || !Array.isArray(context.messages) || context.messages.length > 100)
    throw new Error('INVALID_PLANNER_CONTEXT');
  const messages = [];
  if (context.systemPrompt) messages.push({ role: 'system', content: String(context.systemPrompt) });
  for (const item of context.messages) {
    if (item.role === 'user') messages.push({ role: 'user', content: plain(item.content) });
    else if (item.role === 'assistant') {
      if (!Array.isArray(item.content)) throw new Error('INVALID_MESSAGE_CONTENT');
      const content = item.content.filter(x => x.type === 'text').map(x => x.text).join('\n');
      const tool_calls = item.content.filter(x => x.type === 'toolCall').map(x => ({
        id: x.id, type: 'function', function: { name: x.name, arguments: JSON.stringify(x.arguments) }
      }));
      const next = { role: 'assistant', content: content || null };
      if (tool_calls.length) next.tool_calls = tool_calls;
      messages.push(next);
    } else if (item.role === 'toolResult') messages.push({ role: 'tool', tool_call_id: item.toolCallId, content: plain(item.content) });
    else throw new Error('UNSUPPORTED_PLANNER_MESSAGE_ROLE');
  }
  const payload = { model, messages, stream: false, [official ? 'max_completion_tokens' : 'max_tokens']: maximum };
  if (context.tools?.length) {
    payload.tools = context.tools.map(tool => ({ type: 'function', function: {
      name: tool.name, description: tool.description, parameters: tool.parameters
    }}));
    payload.tool_choice = 'auto'; payload.parallel_tool_calls = false;
  }
  return payload;
}
async function boundedJSON(response, maximum = 1000000) {
  const declared = response.headers?.get?.('content-length');
  if (declared && Number(declared) > maximum) throw new Error('API_RESPONSE_LIMIT');
  let text;
  if (response.body?.getReader) {
    const reader = response.body.getReader(); const chunks = []; let total = 0;
    try {
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        total += value.byteLength;
        if (total > maximum) { await reader.cancel(); throw new Error('API_RESPONSE_LIMIT'); }
        chunks.push(Buffer.from(value));
      }
      text = Buffer.concat(chunks).toString('utf8');
    } finally { reader.releaseLock(); }
  } else {
    text = await response.text();
    if (Buffer.byteLength(text) > maximum) throw new Error('API_RESPONSE_LIMIT');
  }
  return JSON.parse(text);
}
function toolCall(id, name, raw) {
  if (typeof id !== 'string' || !id.length || id.length > 256
      || typeof name !== 'string' || !/^[A-Za-z0-9_-]{1,100}$/.test(name) || typeof raw !== 'string' || raw.length > 60000)
    throw new Error('INVALID_MODEL_TOOL_CALL');
  const args = JSON.parse(raw); canonical(args);
  return { type: 'toolCall', id, name, arguments: args };
}
export function decodeInference(data, api) {
  if (!data || typeof data !== 'object') throw new Error('INVALID_MODEL_RESPONSE');
  const content = []; let usage; let reason;
  if (api === 'responses') {
    if (!['completed', 'incomplete'].includes(data.status) || !Array.isArray(data.output) || data.output.length > 100)
      throw new Error('INVALID_MODEL_RESPONSE');
    for (const item of data.output) {
      if (item.type === 'message') {
        if (!Array.isArray(item.content) || item.content.length > 100) throw new Error('INVALID_MODEL_RESPONSE');
        for (const part of item.content) {
          if (part.type === 'output_text' && typeof part.text === 'string') content.push({ type: 'text', text: part.text });
          else if (part.type === 'refusal' && typeof part.refusal === 'string') content.push({ type: 'text', text: part.refusal });
        }
      }
      if (item.type === 'function_call') content.push(toolCall(item.call_id, item.name, item.arguments));
    }
    usage = data.usage; reason = data.status === 'incomplete' ? 'length' : 'stop';
  } else {
    if (!Array.isArray(data.choices) || data.choices.length !== 1) throw new Error('INVALID_MODEL_RESPONSE');
    const choice = data.choices[0]; const message = choice.message;
    if (!message || message.role !== 'assistant' || !['stop','length','tool_calls','content_filter'].includes(choice.finish_reason))
      throw new Error('INVALID_MODEL_RESPONSE');
    if (typeof message.content === 'string' && message.content) content.push({ type: 'text', text: message.content });
    if (message.tool_calls !== undefined) {
      if (!Array.isArray(message.tool_calls) || message.tool_calls.length > 32) throw new Error('INVALID_MODEL_TOOL_CALL');
      for (const call of message.tool_calls) {
        if (call.type !== 'function') throw new Error('INVALID_MODEL_TOOL_CALL');
        content.push(toolCall(call.id, call.function?.name, call.function?.arguments));
      }
    }
    usage = { input_tokens: data.usage?.prompt_tokens, output_tokens: data.usage?.completion_tokens };
    reason = choice.finish_reason === 'length' ? 'length' : 'stop';
  }
  if (!usage || !Number.isSafeInteger(usage.input_tokens) || usage.input_tokens < 0
      || !Number.isSafeInteger(usage.output_tokens) || usage.output_tokens < 0)
    throw new Error('API_USAGE_MISSING');
  if (!content.length) throw new Error('MODEL_RESPONSE_EMPTY');
  if (reason !== 'length' && content.some(x => x.type === 'toolCall')) reason = 'toolUse';
  return { content, usage, reason };
}
export function createInferenceStream({ modelId, endpoint, api, apiKey = '', budget,
  maxOutputTokens = 1536, maxRequests = 4, fetchImpl = globalThis.fetch, onStatus = () => {} }) {
  endpoint = inferenceEndpoint(endpoint);
  if (!['responses','chat-completions'].includes(api) || typeof modelId !== 'string'
      || !/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$/.test(modelId) || !budget
      || typeof apiKey !== 'string' || apiKey.length > 4096 || /[^\x21-\x7e]/.test(apiKey))
    throw new Error('EXPLICIT_INFERENCE_CONFIG_REQUIRED');
  if (!Number.isInteger(maxOutputTokens) || maxOutputTokens < 128 || maxOutputTokens > 8192
      || !Number.isInteger(maxRequests) || maxRequests < 1 || maxRequests > 20)
    throw new Error('INVALID_REQUEST_LIMIT');
  const url = endpoint + (api === 'responses' ? '/responses' : '/chat/completions');
  const provider = new URL(endpoint).host; const piAPI = api === 'responses' ? 'openai-responses' : 'openai-completions';
  let count = 0;
  return (_model, context, options = {}) => {
    const stream = createAssistantMessageEventStream();
    (async () => {
      let held = null;
      try {
        if (++count > maxRequests) throw new Error('PLANNER_REQUEST_LIMIT');
        if (options.signal?.aborted) throw new Error('PLANNER_ABORTED');
        const official = endpoint === 'https://api.openai.com/v1';
        const payload = api === 'responses' ? responsePayload(context, modelId, maxOutputTokens)
          : chatPayload(context, modelId, maxOutputTokens, official);
        if (api === 'responses' && !official) delete payload.reasoning;
        const body = JSON.stringify(payload); const bytes = Buffer.byteLength(body);
        if (bytes > 100000) throw new Error('PLANNER_CONTEXT_LIMIT');
        held = budget.reserve(bytes, maxOutputTokens);
        onStatus({ event: 'model_request', model: modelId, endpoint, api, budget: budget.summary() });
        const headers = { 'Content-Type': 'application/json' };
        if (apiKey) headers.Authorization = 'Bearer ' + apiKey;
        const response = await fetchImpl(url, { method: 'POST', headers, body, redirect: 'error',
          signal: options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(90000)]) : AbortSignal.timeout(90000) });
        if (!response.ok) throw new Error('INFERENCE_HTTP_' + response.status);
        const data = await boundedJSON(response);
        const parsed = decodeInference(data, api);
        const charged = budget.settle(held, parsed.usage, data.id); held = null;
        const input = parsed.usage.input_tokens, output = parsed.usage.output_tokens;
        const message = { role: 'assistant', content: parsed.content, api: piAPI, provider, model: modelId,
          usage: { input, output, cacheRead: 0, cacheWrite: 0, totalTokens: input + output,
            cost: { input: input * budget.inputPrice / 1e6, output: output * budget.outputPrice / 1e6,
              cacheRead: 0, cacheWrite: 0, total: charged / 1e6 } },
          stopReason: parsed.reason, timestamp: Date.now() };
        onStatus({ event: 'model_response', model: modelId, endpoint, api, budget: budget.summary() });
        stream.push({ type: 'start', partial: message }); stream.push({ type: 'done', reason: parsed.reason, message });
      } catch (failure) {
        const code = typeof failure?.message === 'string' && /^[A-Z_0-9]{1,100}$/.test(failure.message)
          ? failure.message : 'MODEL_TRANSPORT_FAILED';
        const message = { role: 'assistant', content: [], api: piAPI, provider, model: modelId,
          usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
            cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
          stopReason: 'error', errorMessage: code, timestamp: Date.now() };
        onStatus({ event: 'model_error', code, reservation_held: held !== null, budget: budget.summary() });
        stream.push({ type: 'error', reason: 'error', error: message });
      }
    })();
    return stream;
  };
}
