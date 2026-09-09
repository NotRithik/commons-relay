import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { canonical } from '../../adapters/pi/canonical.mjs';
import { validateInferenceSnapshot, INFERENCE_FIELDS } from '../../adapters/pi/inference-configuration.mjs';
const fixture = JSON.parse(readFileSync(new URL('../fixtures/inference-snapshot.json', import.meta.url), 'utf8'));
function changedHash(value) { const result = { ...value }; delete result.configuration_hash; return createHash('sha256').update(canonical(result)).digest('hex'); }

test('Python-created snapshot digest independently recomputes identically in Node', () => {
  assert.equal(changedHash(fixture), fixture.configuration_hash);
  const value = validateInferenceSnapshot(fixture, fixture.configuration_hash);
  assert.equal(value.model, 'vendor/fixture:1'); assert.equal(Object.isFrozen(value), true);
});
test('matching copied hash does not authorize changed snapshot fields', () => {
  const changes = { model: 'other-model', endpoint: 'https://other.example/v1', api: 'chat-completions',
    max_output_tokens: 4096, input_micro_per_million: 0, output_micro_per_million: 0,
    credential_sha256: 'b'.repeat(64), api_key_file: '/tmp/other-key', runner_sha256: '1'.repeat(64),
    budget_micro_usd: 2000000, node: '/tmp/other-node', runner: '/tmp/other-runner',
    budget_file: '/tmp/other-budget', credential_mode: 'keep' };
  assert.deepEqual(Object.keys(changes).sort(), [...INFERENCE_FIELDS].sort());
  for (const [key, value] of Object.entries(changes)) {
    assert.throws(() => validateInferenceSnapshot({ ...fixture, [key]: value }, fixture.configuration_hash),
      /CHANGED_REVIEW_AGAIN/, key);
  }
});
test('attacker recomputing the copied snapshot hash still cannot change the owner-reviewed hash', () => {
  const changed = { ...fixture, endpoint: 'https://other.example/v1', credential_sha256: 'c'.repeat(64) };
  changed.configuration_hash = changedHash(changed);
  assert.throws(() => validateInferenceSnapshot(changed, fixture.configuration_hash), /CHANGED_REVIEW_AGAIN/);
});
test('a model request always needs a reviewed configuration hash', () => {
  for (const hash of [undefined, '', 'x', 'A'.repeat(64)]) {
    assert.throws(() => validateInferenceSnapshot(fixture, hash), /INVALID_INFERENCE_REVIEW/);
  }
});
test('unknown or omitted snapshot fields are not implicitly trusted', () => {
  const missing = { ...fixture }; delete missing.credential_sha256;
  assert.throws(() => validateInferenceSnapshot(missing, fixture.configuration_hash));
  assert.throws(() => validateInferenceSnapshot({ ...fixture, fallback_endpoint: 'https://other.example' }, fixture.configuration_hash));
});
test('a properly reviewed anonymous local configuration never contains a credential path', () => {
  const local = { ...fixture, endpoint: 'http://127.0.0.1:11434/v1', model: 'local:latest', api: 'chat-completions',
    credential_mode: 'none', api_key_file: '', credential_sha256: '' };
  local.configuration_hash = changedHash(local);
  assert.equal(validateInferenceSnapshot(local, local.configuration_hash).api_key_file, '');
  const invalid = { ...local, api_key_file: '/tmp/old-key', credential_sha256: 'a'.repeat(64) };
  invalid.configuration_hash = changedHash(invalid);
  assert.throws(() => validateInferenceSnapshot(invalid, invalid.configuration_hash), /BINDING_MISMATCH/);
});
