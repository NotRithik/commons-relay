/** Exercise the actual QML functions against their current native contract.
 * These are deterministic component tests, not a substitute for Basecamp UI QA.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
const qml = readFileSync(fileURLToPath(new URL('../native/ui/src/qml/Main.qml', import.meta.url)), 'utf8');
const rep = readFileSync(fileURLToPath(new URL('../native/ui/src/commons_relay_owner_ui.rep', import.meta.url)), 'utf8');
const functions = [...qml.matchAll(/^    function [^\n]+\{[\s\S]*?^    \}/gm)].map(match => match[0]).join('\n');
const plain = value => JSON.parse(JSON.stringify(value));
function fixture(properties = {}, values = {}) {
  const calls = [];
  const schema = { type: 'object', properties, required: Object.keys(properties), additionalProperties: false };
  const state = {
    Theme: { palette: { success: 'success', warning: 'warning', error: 'error', textSecondary: 'secondary' } },
    skills: [{ id: 'fixture.skill', description: 'Fixture skill' }],
    skillDetails: { id: 'fixture.skill', input_schema: schema },
    skillPicker: { currentIndex: 0, currentValue: 'fixture.skill' }, fields: [], formValues: values,
    summary: { policy: { approval_ttl: 600 } }, task: {}, reviewed: {}, reviewKind: '', viewError: '',
    planner: { enabled: true }, modelConsent: true, selectedLabel: 'Storage', reviewedChat: {},
    pendingDraft: '', pendingDraftAgent: '', chatInput: { text: 'List my files.', forceActiveFocus() {} },
    allowChatActions: { checked: false }, chatSpend: { text: '0' },
    backend: {
      selectedProfile: 'storage', selectedAgent: 'agent-storage', remoteReady: true, chatBusy: false,
      submitTask: (...args) => { calls.push(['submit', ...args]); return {}; },
      approveTask: (...args) => { calls.push(['approve', ...args]); return {}; },
      cancelTask: (...args) => { calls.push(['cancel', ...args]); return {}; },
      startConversation: (...args) => { calls.push(['chat', ...args]); return {}; },
    },
    reviewDialog: { open: () => calls.push(['review']) },
    chatPermissionDialog: { open: () => calls.push(['chat-review']) },
    logos: { watch() {} },
  };
  state.root = state;
  vm.createContext(state);
  new vm.Script(functions, { filename: 'Main.qml:actual-functions' }).runInContext(state);
  return { state, calls };
}

test('zero-input skills require the matching authenticated schema', () => {
  const { state } = fixture();
  assert.deepEqual(plain(state.collectForm()), {});
  state.skillDetails.id = 'stale.skill';
  assert.throws(() => state.collectForm(), /verified fields/);
});
test('no task can be formed without a selected registered skill', () => {
  const { state } = fixture(); state.skillPicker.currentIndex = -1;
  assert.throws(() => state.collectForm(), /verified fields/);
});
test('new schemas clear old field values', () => {
  const { state } = fixture({ amount: { type: 'string' } }, { amount: '90' });
  state.renderFieldValues();
  assert.equal(state.formValues.amount, '');
  assert.equal(state.fields[0].name, 'amount');
});
test('large token amounts remain exact decimal strings', () => {
  const amount = '123456789012345678901234567890';
  const { state } = fixture({ amount: { type: 'string', pattern: '^(0|[1-9][0-9]{0,38})$', maxLength: 39 } }, { amount });
  assert.equal(state.collectForm().amount, amount);
});
test('amounts reject exponents, fractions, signs and leading zeroes', () => {
  const { state } = fixture({ amount: { type: 'string', pattern: '^(0|[1-9][0-9]{0,38})$' } });
  for (const amount of ['1e4', '-1', '1.25', '', '01']) {
    state.formValues = { amount }; assert.throws(() => state.collectForm());
  }
});
test('member lists accept normal newline or comma separated text', () => {
  const { state } = fixture({ members: { type: 'array', items: { type: 'string' }, minItems: 1 } }, { members: 'alice\n bob,carol' });
  assert.deepEqual(plain(state.collectForm().members), ['alice', 'bob', 'carol']);
});
test('lists enforce uniqueness, size and item type', () => {
  const { state } = fixture({ members: { type: 'array', items: { type: 'string' }, minItems: 1, maxItems: 2, uniqueItems: true } });
  for (const members of ['alice,alice', 'alice,bob,carol', '[]', '[42]']) {
    state.formValues = { members }; assert.throws(() => state.collectForm());
  }
});
test('structured objects reject malformed JSON and wrong types', () => {
  const { state } = fixture({ params: { type: 'object' } }, { params: '{"count":2}' });
  assert.deepEqual(plain(state.collectForm().params), { count: 2 });
  for (const params of ['{', '[]', 'null', '42']) {
    state.formValues = { params }; assert.throws(() => state.collectForm());
  }
});
test('nested numeric values cannot silently round or use fractions', () => {
  const { state } = fixture({ params: { type: 'object' } });
  for (const params of ['{"amount":9007199254740993}', '{"nested":{"x":1.25}}', '{"v":[1e999]}']) {
    state.formValues = { params }; assert.throws(() => state.collectForm());
  }
  state.formValues = { params: '{"amount":"9007199254740993","count":4}' };
  assert.equal(state.collectForm().params.count, 4);
});
test('prototype-shaped structured fields are rejected', () => {
  const { state } = fixture({ params: { type: 'object' } }, { params: '{"__proto__":{"x":1}}' });
  assert.throws(() => state.collectForm(), /unsupported field/);
});
test('integer controls enforce canonical safe integer ranges', () => {
  const { state } = fixture({ count: { type: 'integer', minimum: 1, maximum: 4 } }, { count: '4' });
  assert.equal(state.collectForm().count, 4);
  for (const count of ['0', '5', '2.5', '01', '9007199254740993']) {
    state.formValues = { count }; assert.throws(() => state.collectForm());
  }
});
test('required strings and length limits are enforced', () => {
  const { state } = fixture({ label: { type: 'string', minLength: 1, maxLength: 4 } });
  for (const label of ['', 'longer']) { state.formValues = { label }; assert.throws(() => state.collectForm()); }
  state.formValues = { label: 'four' }; assert.equal(state.collectForm().label, 'four');
});
test('optional values can remain absent', () => {
  const { state } = fixture({ optional: { type: 'string' } });
  state.skillDetails.input_schema.required = [];
  assert.deepEqual(plain(state.collectForm()), {});
});
test('review does not perform the task and freezes the shown arguments', () => {
  const { state, calls } = fixture({ recipient: { type: 'string' } }, { recipient: 'alice' });
  state.reviewTask(); state.formValues.recipient = 'changed-after-review';
  assert.deepEqual(calls, [['review']]);
  assert.equal(state.reviewed.arguments.recipient, 'alice');
});
test('confirmation sends the reviewed values and the configured expiry', () => {
  const { state, calls } = fixture({ recipient: { type: 'string' } }, { recipient: 'alice' });
  state.summary.policy.approval_ttl = 300;
  state.reviewTask(); state.formValues.recipient = 'bob'; state.confirmReview();
  assert.deepEqual(calls[1], ['submit', 'fixture.skill', '{"recipient":"alice"}', 300]);
  assert.equal(state.reviewKind, '');
});
test('switching agent disarms the manual task review', () => {
  const { state, calls } = fixture(); state.reviewTask(); state.backend.selectedProfile = 'messaging';
  state.confirmReview(); assert.deepEqual(calls, [['review']]);
  assert.match(state.viewError, /agent changed/);
});
test('approval requires complete task arguments', () => {
  const { state, calls } = fixture(); state.task = { id: 'task-1', arguments_complete: false };
  state.reviewAction('approve'); assert.equal(calls.length, 0);
});
test('approval forwards the exact reviewed ID, hash and policy', () => {
  const { state, calls } = fixture();
  state.task = { id: 'task-1', arguments_complete: true, intent_hash: 'a'.repeat(64), policy_version: 4, arguments: { amount: '6' } };
  state.reviewAction('approve'); state.task.arguments.amount = '7';
  assert.equal(state.reviewed.arguments.amount, '6');
  state.confirmReview(); assert.deepEqual(calls[1], ['approve', 'task-1', 'a'.repeat(64), 4]);
});
test('uncertain and approval-required tasks are never labelled completed', () => {
  const { state } = fixture();
  assert.match(state.stateLabel('unknown'), /Checking/);
  assert.equal(state.stateLabel('input-required'), 'Needs your approval');
  assert.equal(state.taskColor('unknown'), 'warning');
});
test('chat refuses to share data before model consent', () => {
  const { state, calls } = fixture(); state.modelConsent = false; state.sendChat();
  assert.equal(calls.length, 0); assert.match(state.viewError, /data-sharing/);
});
test('read-only chat cannot inherit a hidden token limit', () => {
  const { state, calls } = fixture(); state.chatSpend.text = '50'; state.sendChat();
  assert.deepEqual(calls[0], ['chat', 'List my files.', false, '0']);
});
test('actions use a frozen review rather than later edits', () => {
  const { state, calls } = fixture(); state.allowChatActions.checked = true; state.chatSpend.text = '3';
  state.sendChat(); state.chatInput.text = 'Different request'; state.chatSpend.text = '90'; state.submitReviewedChat();
  assert.deepEqual(calls, [['chat-review'], ['chat', 'List my files.', true, '3']]);
});
test('chat review cannot migrate to another agent or survive consent removal', () => {
  for (const change of [state => { state.backend.selectedAgent = 'other'; }, state => { state.modelConsent = false; }]) {
    const { state, calls } = fixture(); state.allowChatActions.checked = true; state.sendChat();
    change(state); state.submitReviewedChat(); assert.deepEqual(calls, [['chat-review']]);
  }
});
test('chat validates amount spelling before review', () => {
  for (const amount of ['1.5', '1e3', '-1', '01']) {
    const { state, calls } = fixture(); state.allowChatActions.checked = true; state.chatSpend.text = amount;
    state.sendChat(); assert.equal(calls.length, 0);
  }
});
test('disconnected agents and missing model configuration cannot start chat', () => {
  for (const change of [state => { state.backend.remoteReady = false; }, state => { state.planner.enabled = false; }]) {
    const { state, calls } = fixture(); change(state); state.sendChat(); assert.equal(calls.length, 0);
  }
});
test('QML backend references all exist in the native remote contract', () => {
  const names = new Set([...rep.matchAll(/(?:SLOT|PROP)\(\w+\s+(\w+)/g)].map(match => match[1]));
  for (const match of qml.matchAll(/(?:root\.)?backend\.(\w+)/g)) assert.ok(names.has(match[1]), match[1]);
});
test('root bindings and function names are coherent', () => {
  const propertyNames = [...qml.matchAll(/(?:readonly\s+)?property\s+\w+\s+(\w+)\s*:/g)].map(match => match[1]);
  const functionNames = [...qml.matchAll(/^    function\s+(\w+)\(/gm)].map(match => match[1]);
  assert.equal(new Set(functionNames).size, functionNames.length, 'No duplicate root functions');
  const names = new Set([...propertyNames, ...functionNames, 'width', 'height', 'grabToImage']);
  for (const match of qml.matchAll(/root\.(\w+)/g)) assert.ok(names.has(match[1]), match[1]);
});
test('untrusted text stays plain and ordinary workflows require no pasted signed JSON', () => {
  assert.ok(qml.includes('textFormat: Text.PlainText'));
  assert.ok(qml.includes('textFormat: TextEdit.PlainText'));
  assert.ok(!qml.includes('Paste signed'));
  assert.ok(qml.includes('Chat'));
});

test('filtered manual tools resolve by signed skill ID, not the original list index', () => {
  const { state } = fixture();
  state.skills.unshift({ id: 'unrelated.skill', description: 'Unrelated' });
  assert.deepEqual(plain(state.collectForm()), {});
});

test('assistive text changes update the form, not only keyboard textEdited events', () => {
  const field=qml.match(/Field \{\n\s+visible: \["object", "array", "any"\][\s\S]*?\n\s+\}/);
  assert.ok(field,'Dynamic scalar field exists');
  assert.ok(field[0].includes('onTextChanged:'));
  assert.ok(!field[0].includes('onTextEdited:'));
});

// Accessibility setters emit checkedChanged, not the pointer-only toggled signal.
test('model consent follows the visible checkbox for keyboard and accessibility actions', () => {
  assert.ok(qml.includes('onCheckedChanged: if (root.modelConsent !== checked) root.modelConsent = checked'));
  assert.ok(!qml.includes('onToggled: root.modelConsent = checked'));
  assert.ok(qml.includes('root.modelConsent = false'));
});

test('finishing a conversation resets the next message to read-only and zero spend',()=>{
  assert.ok(qml.includes('function onChatBusyChanged()'));
  const handler=qml.slice(qml.indexOf('function onChatBusyChanged()'),qml.indexOf('function onConversationJsonChanged()'));
  assert.ok(handler.includes('!root.backend.chatBusy'));
  assert.ok(handler.includes('allowChatActions.checked = false'));
  assert.ok(handler.includes('chatSpend.text = "0"'));
});
