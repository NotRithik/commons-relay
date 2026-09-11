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
    planner: { enabled: true, configuration_hash: "a".repeat(64), model: 'gpt-5.6-luna' }, selectedLabel: 'Storage', reviewedChat: {},
    pendingDraft: '', pendingDraftAgent: '', chatInput: { text: 'List my files.', forceActiveFocus() {} },
    allowChatActions: { checked: false }, chatSpend: { text: '0' },
    conversation: [], nowSeconds: 1_700_000_000, remoteHealth: { connection_state: 'online', last_reply_age_ms: 1000, round_trip_ms: 2000 },
    backend: {
      selectedProfile: 'storage', selectedAgent: 'agent-storage', remoteReady: true, chatBusy: false, activeGoalId: '',
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
test('chat requires a known model configuration before Send', () => {
  const { state, calls } = fixture(); state.planner.configuration_hash = ''; state.sendChat();
  assert.equal(calls.length, 0); assert.match(state.viewError, /model settings/);
});
test('read-only chat cannot inherit a hidden token limit', () => {
  const { state, calls } = fixture(); state.chatSpend.text = '50'; state.sendChat();
  assert.deepEqual(calls[0], ['chat', 'List my files.', false, '0', 'a'.repeat(64)]);
});
test('actions use a frozen review rather than later edits', () => {
  const { state, calls } = fixture(); state.allowChatActions.checked = true; state.chatSpend.text = '3';
  state.sendChat(); state.chatInput.text = 'Different request'; state.chatSpend.text = '90'; state.submitReviewedChat();
  assert.deepEqual(calls, [['chat-review'], ['chat', 'List my files.', true, '3', 'a'.repeat(64)]]);
});
test('chat review cannot migrate to another agent or model', () => {
  for (const change of [state => { state.backend.selectedAgent = 'other'; }, state => { state.planner.configuration_hash = 'b'.repeat(64); }]) {
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
test('Send is model-bound without a redundant data-sharing checkbox', () => {
  assert.ok(!qml.includes('id: consentBox'));
  assert.ok(qml.includes('inference_hash: root.planner.configuration_hash'));
  assert.ok(qml.includes('reviewed.inference_hash !== root.planner.configuration_hash'));
  assert.ok(qml.includes('root.planner.endpoint'));
});

test('finishing a conversation resets the next message to read-only and zero spend',()=>{
  assert.ok(qml.includes('function onChatBusyChanged()'));
  const handler=qml.slice(qml.indexOf('function onChatBusyChanged()'),qml.indexOf('function onConversationJsonChanged()'));
  assert.ok(handler.includes('!root.backend.chatBusy'));
  assert.ok(handler.includes('allowChatActions.checked = false'));
  assert.ok(handler.includes('chatSpend.text = "0"'));
});

test('paid service summary uses the complete receipt, not authorization maximum', () => {
  const { state } = fixture(); const hash='a'.repeat(64);
  const receipt={state:'completed',skill:'agent.task',maximum_spend:'9',result_complete:true,result_preview:JSON.stringify({paid_amount:'3',provider:'peer',payment_transaction:hash,artifacts:[{}]})};
  assert.match(state.resultSummary(receipt),/Paid 3 testnet units/); assert.doesNotMatch(state.resultSummary(receipt),/Paid 9/);
  assert.ok(state.resultSummary(receipt).includes(hash));
});
test('incomplete and unsuccessful receipts cannot produce a success summary', () => {
  const { state } = fixture();
  for(const taskState of ['submitted','working','unknown','input-required','failed','rejected','canceled'])
    assert.equal(state.resultSummary({state:taskState,skill:'wallet.balance',result_complete:true,result_preview:'{"balance":"50"}'}),'');
  assert.equal(state.resultSummary({state:'completed',result_complete:false,result_preview:'{"files":[]}'}),'');
});
test('a public program account is not mislabeled as the agent wallet', () => {
  const { state } = fixture();
  const text=state.resultSummary({state:'completed',skill:'program.query',result_complete:true,result_preview:'{"balance":"0","block":42}'});
  assert.match(text,/Program state/); assert.doesNotMatch(text,/Recorded wallet balance/);
});
test('large wallet values stay exact and recorded block is explicit', () => {
  const { state } = fixture(); const balance='123456789012345678901234567890';
  const text=state.resultSummary({state:'completed',skill:'wallet.balance',result_complete:true,result_preview:JSON.stringify({balance,block:604})});
  assert.ok(text.includes(balance)); assert.match(text,/Recorded at block 604/);
});
test('file receipt wording requires the recorded download authentication', () => {
  const { state } = fixture();
  const base={state:'completed',skill:'storage.download',result_complete:true};
  assert.match(state.resultSummary({...base,result_preview:'{"authenticated":true,"bytes":49,"path":"restored.txt"}'}),/Retrieved and authenticated: 49 bytes/);
  assert.doesNotMatch(state.resultSummary({...base,result_preview:'{"authenticated":false,"bytes":49,"path":"restored.txt"}'}),/Retrieved and authenticated/);
});
test('a nonzero paid result missing its transaction hash is not summarized as paid', () => {
  const { state } = fixture();
  const text=state.resultSummary({state:'completed',skill:'agent.task',result_complete:true,result_preview:'{"paid_amount":"3","provider":"peer","artifacts":[]}'});
  assert.doesNotMatch(text,/Paid 3/); assert.match(text,/needs inspection/);
});
test('technical details never hide approval arguments', () => {
  assert.match(qml,/\(root\.task\.state !== "completed" \|\| detailsDialog\.showDetails\) && root\.task\.arguments_complete/);
  assert.match(qml,/onOpened: showDetails = false/);
});


test('offline agents keep the draft editor enabled while sending remains gated', () => {
  const editor = qml.slice(qml.indexOf('id: chatInput'), qml.indexOf('id: chatInput') + 1600);
  assert.match(editor, /enabled: true/);
  assert.match(qml, /Write a draft here\. Connect an agent before sending\./);
  const { state, calls } = fixture(); state.backend.remoteReady = false;
  state.sendChat(); assert.equal(calls.filter(x => x[0] === 'chat').length, 0);
});
test('an in-flight model turn is not labeled as a dropped agent', () => {
  assert.match(qml, /This reply is still running\. Press Stop to cancel it/);
  assert.match(qml, /Busy — waiting on the model/);
  assert.match(qml, /Health ping is stale because this message still has the agent/);
  const { state } = fixture();
  state.backend.chatBusy = true;
  state.backend.remoteReady = false;
  state.remoteHealth = { connection_state: 'unresponsive', last_reply_age_ms: 126000, round_trip_ms: 2000 };
  state.conversation = [{ id: 'goal-1', state: 'thinking', created: 1_699_999_880, updated: 1_699_999_880 }];
  state.backend.activeGoalId = 'goal-1';
  state.nowSeconds = 1_700_000_000;
  assert.equal(state.agentHealthTitle(), 'Busy — waiting on the model');
  assert.match(state.agentHealthDetail(), /Health ping is stale/);
  assert.match(state.composerPlaceholder(), /still running/);
  assert.equal(state.sendButtonLabel(), 'Waiting on model');
  assert.match(state.footerStatus(), /Waiting on gpt-5\.6-luna/);
  assert.match(state.chatWaitBody(state.conversation[0]), /No tools have been used yet/);
  assert.doesNotMatch(state.agentHealthTitle(), /Agent not responding/);
  assert.doesNotMatch(state.composerPlaceholder(), /Connect an agent before sending/);
});
test('provider notice follows configured provider instead of assuming OpenAI', () => {
  assert.match(qml, /root\.providerLabel/);
  assert.doesNotMatch(qml, /Send this conversation and requested tool results to OpenAI/);
});

function inferenceFixture() {
  const f=fixture();const s=f.state;
  s.planner.configuration_hash='a'.repeat(64);
  s.inferenceDialog={profileAtOpen:'storage',hashAtOpen:'a'.repeat(64),errorText:'',saveAttempted:false,close:()=>f.calls.push(['close-settings'])};
  s.inferenceEndpoint={text:'https://api.example.com/v1'};s.inferenceModel={text:'vendor/model'};
  s.inferenceApi={currentIndex:0};s.inferenceCredential={currentIndex:1};s.inferenceKey={text:'synthetic-fixture-key'};
  s.inferenceOutput={text:'1536'};s.inferenceInputPrice={text:'0.25'};s.inferenceOutputPrice={text:'1.20'};
  s.inferenceReview={checked:true};s.backend.configureInference=(...args)=>{f.calls.push(['configure-inference',...args]);return {};};
  return f;
}
test('inference save keeps the editor open for asynchronous results and clears the key',()=>{
  const {state,calls}=inferenceFixture();state.saveInferenceSettings();
  assert.equal(calls.length,1);assert.equal(calls[0][0],'configure-inference');
  assert.equal(state.inferenceDialog.saveAttempted,true);assert.equal(state.inferenceKey.text,'');
  assert.equal(state.inferenceReview.checked,false);
  assert.equal(calls.some(x=>x[0]==='close-settings'),false);
});
test('stale inference review stays open without signing another setting',()=>{
  const {state,calls}=inferenceFixture();state.planner.configuration_hash='b'.repeat(64);state.saveInferenceSettings();
  assert.equal(calls.length,0);assert.match(state.inferenceDialog.errorText,/reload/);
});

function actionReviewFixture() {
  const f=fixture();const s=f.state;
  const request={goal_id:'chat-fixture',skill:'messaging.send',arguments:{recipient:'fixture-peer',message:'Synthetic only'},reason:'Send the requested test message',description:'Send message',maximum_spend:'0',asset:'LEZ-testnet',policy_version:1,expires_at:2000000000,intent_hash:'b'.repeat(64)};
  s.permissionGoalRequested='chat-fixture';s.permissionProfile='storage';s.permissionAgent='agent-storage';s.permissionReview={};s.pending=false;
  s.actionRequestReviewed={checked:false};s.modelActionDialog={open:()=>f.calls.push(['action-dialog']),close:()=>f.calls.push(['close-action'])};
  s.backend.permissionReviewJson=JSON.stringify({id:'chat-fixture',agent_id:'agent-storage',permission:{request,decision:'pending',task_id:null}});
  s.backend.decideConversationPermission=(...args)=>{f.calls.push(['permission-decision',...args]);return {};};
  return f;
}
test('model action review opens only for the requested agent and goal',()=>{
  const {state,calls}=actionReviewFixture();state.receivePermissionReview();
  assert.deepEqual(calls,[['action-dialog']]);assert.equal(state.permissionReview.arguments.recipient,'fixture-peer');
  assert.equal(state.actionRequestReviewed.checked,false);
});
test('unreviewed model action cannot be approved',()=>{
  const {state,calls}=actionReviewFixture();state.receivePermissionReview();state.confirmRequestedAction(true);
  assert.equal(calls.filter(x=>x[0]==='permission-decision').length,0);
});
test('approval signs only the displayed action hash, not a broader conversation',()=>{
  const {state,calls}=actionReviewFixture();state.receivePermissionReview();state.actionRequestReviewed.checked=true;state.confirmRequestedAction(true);
  assert.deepEqual(calls[1],['permission-decision','chat-fixture','b'.repeat(64),true]);
});
test('decline needs no approval checkbox and passes no action arguments',()=>{
  const {state,calls}=actionReviewFixture();state.receivePermissionReview();state.confirmRequestedAction(false);
  assert.deepEqual(calls[1],['permission-decision','chat-fixture','b'.repeat(64),false]);
});
test('switching agents invalidates an open model-action review',()=>{
  const {state,calls}=actionReviewFixture();state.receivePermissionReview();state.actionRequestReviewed.checked=true;state.backend.selectedProfile='other';state.confirmRequestedAction(true);
  assert.equal(calls.filter(x=>x[0]==='permission-decision').length,0);
});
test('a stale action response cannot open the decision dialog',()=>{
  const {state,calls}=actionReviewFixture();state.permissionGoalRequested='different-goal';state.receivePermissionReview();assert.equal(calls.length,0);
});
test('transfer and message implications are described separately',()=>{
  const {state}=fixture();assert.match(state.actionImplications('wallet.send'),/Transfers testnet tokens/);assert.match(state.actionImplications('messaging.send'),/recipient/);
});
