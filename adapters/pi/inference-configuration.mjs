/** Recompute the exact owner-reviewed configuration before a provider call.
 * This validates a snapshot, not mutable operator files. A matching copied hash
 * alone is not evidence that the fields beneath it have remained unchanged.
 */
import { createHash, timingSafeEqual } from 'node:crypto';
import { canonical } from './canonical.mjs';
import { inferenceEndpoint } from './inference-transport.mjs';

export const INFERENCE_FIELDS = Object.freeze([
  'node', 'runner', 'model', 'api_key_file', 'budget_file', 'budget_micro_usd',
  'input_micro_per_million', 'output_micro_per_million', 'endpoint', 'api',
  'max_output_tokens', 'credential_mode', 'credential_sha256', 'runner_sha256'
]);
const HASH = /^[a-f0-9]{64}$/;
export function validateInferenceSnapshot(config, reviewedHash) {
  if (!config || typeof config !== 'object' || Array.isArray(config)
      || !HASH.test(reviewedHash || '') || !HASH.test(config.configuration_hash || '')
      || Object.keys(config).length !== INFERENCE_FIELDS.length + 1
      || !INFERENCE_FIELDS.every(key => Object.hasOwn(config, key)))
    throw new Error('INVALID_INFERENCE_REVIEW');
  const values = {};
  for (const key of INFERENCE_FIELDS) values[key] = config[key];
  const computed = createHash('sha256').update(canonical(values)).digest('hex');
  if (!timingSafeEqual(Buffer.from(computed), Buffer.from(reviewedHash))
      || !timingSafeEqual(Buffer.from(computed), Buffer.from(config.configuration_hash)))
    throw new Error('INFERENCE_SETTINGS_CHANGED_REVIEW_AGAIN');
  if (inferenceEndpoint(config.endpoint) !== config.endpoint
      || !['responses', 'chat-completions'].includes(config.api)
      || typeof config.model !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$/.test(config.model)
      || !['keep', 'replace', 'none'].includes(config.credential_mode)
      || !HASH.test(config.runner_sha256))
    throw new Error('INVALID_INFERENCE_CONFIG');
  for (const key of ['node', 'runner', 'budget_file']) {
    if (typeof config[key] !== 'string' || !config[key].startsWith('/') || /[\x00-\x1f]/.test(config[key]))
      throw new Error('INVALID_INFERENCE_CONFIG');
  }
  if (typeof config.api_key_file !== 'string'
      || (config.api_key_file !== '' && (!config.api_key_file.startsWith('/') || /[\x00-\x1f]/.test(config.api_key_file)))
      || (config.api_key_file ? !HASH.test(config.credential_sha256) : config.credential_sha256 !== '')
      || (config.credential_mode === 'none' && config.api_key_file !== ''))
    throw new Error('INFERENCE_CREDENTIAL_BINDING_MISMATCH');
  for (const key of ['budget_micro_usd', 'input_micro_per_million', 'output_micro_per_million', 'max_output_tokens']) {
    if (!Number.isSafeInteger(config[key])) throw new Error('INVALID_INFERENCE_CONFIG');
  }
  if (config.budget_micro_usd < 1 || config.budget_micro_usd > 15000000
      || config.input_micro_per_million < 0 || config.input_micro_per_million > 1000000000
      || config.output_micro_per_million < 0 || config.output_micro_per_million > 1000000000
      || config.max_output_tokens < 128 || config.max_output_tokens > 8192)
    throw new Error('INVALID_INFERENCE_CONFIG');
  return Object.freeze({ ...config });
}
