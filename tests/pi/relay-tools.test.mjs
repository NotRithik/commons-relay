import assert from 'node:assert/strict';
import { test } from 'node:test';
import { generateKeyPairSync } from 'node:crypto';
import { createRelayTools,createPiRelayAgent,signedTransport,toolNameFor } from '../../adapters/pi/relay-tools.mjs';
import { envelopeSigner } from '../../adapters/pi/canonical.mjs';
import { createAssistantMessageEventStream } from '../../adapters/pi/node_modules/@earendil-works/pi-ai/dist/utils/event-stream.js';

const skill={id:'storage.upload',description:'Store a file',input_schema:{type:'object',properties:{path:{type:'string'},label:{type:'string'}},required:['path','label'],additionalProperties:false}};
const args={path:'synthetic.txt',label:'fixture'};
const task=(state='submitted',which=skill.id)=>({id:'a'.repeat(32),skill:which,state,maximum_spend:'0',asset:'LEZ-testnet',result:{address:'synthetic-receipt'}});
function fixture(mode='completed') {
  let submitted=0,executed=0;
  const transport={async submit(){submitted++;return task(mode==='input-required'?'input-required':'submitted');},async run(){executed++;return task(mode);}};
  const view=createRelayTools({skills:[skill],allowedSkills:[skill.id],transport});
  return {...view,transport,counts:()=>({submitted,executed})};
}
test('registered names map to provider-safe tool names without collisions',()=>{
  assert.match(toolNameFor('storage.upload'),/^[A-Za-z0-9_-]{1,64}$/);assert.notEqual(toolNameFor('x.y'),toolNameFor('x_y'));
});
test('tool proposal is executed only after engine accepts',async()=>{const f=fixture();const result=await f.tools[0].execute('call1',args);assert.equal(result.details.state,'completed');assert.deepEqual(f.counts(),{submitted:1,executed:1});});
test('above-threshold result stops before any effect',async()=>{const f=fixture('input-required');const r=await f.tools[0].execute('call1',args);assert.equal(r.terminate,true);assert.deepEqual(f.counts(),{submitted:1,executed:0});});
test('unknown result blocks subsequent automatic steps',async()=>{const f=fixture('unknown');await f.tools[0].execute('call1',args);await assert.rejects(f.tools[0].execute('call2',args),/WAITING/);assert.equal(f.state.steps,1);});
test('same tool-call id has one effect',async()=>{const f=fixture();await Promise.all([f.tools[0].execute('call1',args),f.tools[0].execute('call1',args)]);assert.deepEqual(f.counts(),{submitted:1,executed:1});});
test('reused id with different arguments rejected',async()=>{const f=fixture();await f.tools[0].execute('call1',args);await assert.rejects(f.tools[0].execute('call1',{...args,label:'changed'}),/REUSED/);});
test('only grant-authorized registry skills exposed',()=>{const f=fixture();const extra={...skill,id:'wallet.send'};assert.equal(createRelayTools({skills:[skill,extra],allowedSkills:[skill.id],transport:f.transport}).tools.length,1);});
test('unknown skill in grant rejected',()=>{assert.throws(()=>createRelayTools({skills:[skill],allowedSkills:['shell.exec'],transport:{}}),/UNKNOWN_SKILL/);});
test('duplicate registry ids rejected',()=>{assert.throws(()=>createRelayTools({skills:[skill,skill],allowedSkills:[skill.id],transport:{}}),/DUPLICATE/);});
test('strict schema required',()=>{assert.throws(()=>createRelayTools({skills:[{...skill,input_schema:{type:'object'}}],allowedSkills:[skill.id],transport:{}}),/SCHEMA/);});
test('step cap stops extra proposals',async()=>{const f=fixture();const g=createRelayTools({skills:[skill],allowedSkills:[skill.id],transport:f.transport,maxSteps:1});await g.tools[0].execute('one',args);await assert.rejects(g.tools[0].execute('two',args),/STEP_LIMIT/);});
test('abort before execution does not submit',async()=>{const f=fixture();const abort=new AbortController();abort.abort();await assert.rejects(f.tools[0].execute('one',args,abort.signal),/ABORTED/);assert.deepEqual(f.counts(),{submitted:0,executed:0});});
test('forged completion for another skill rejected',async()=>{const t={async submit(){return task('completed','wallet.send');}};const f=createRelayTools({skills:[skill],allowedSkills:[skill.id],transport:t});await assert.rejects(f.tools[0].execute('one',args),/INVALID_RELAY/);});
test('tool output excludes secret-bearing request fields',async()=>{const f=fixture();f.transport.run=async()=>({...task('completed'),arguments:{private_key:'never return'},private_key:'never return'});const r=await f.tools[0].execute('one',args);assert.equal(JSON.stringify(r).includes('private_key'),false);});
test('new arbitrary typed skill needs no planner changes',async()=>{
  const custom={id:'weather.read',description:'Read a local observation',input_schema:{type:'object',properties:{station:{type:'string'}},required:['station'],additionalProperties:false}};
  const transport={async submit(){return task('completed',custom.id);}};
  const f=createRelayTools({skills:[custom],allowedSkills:[custom.id],transport});assert.equal((await f.tools[0].execute('x',{station:'fixture'})).details.skill,'weather.read');
});
test('signed transport binds agent, grant, call id and parameters',async()=>{
  const signer=envelopeSigner(generateKeyPairSync('ed25519').privateKey);const requests=[];
  const t=signedTransport({agentId:'test-agent',grantId:'test-goal',signer,clock:()=>1000,request:async x=>{requests.push(x);return task();}});
  await t.submit(skill.id,args,'call1');await t.submit(skill.id,args,'call1');
  const body=requests[0].params.envelope.body;
  assert.equal(body.grant_id,'test-goal');assert.equal(body.agent_id,'test-agent');assert.equal(body.expires_at,1120);assert.deepEqual(body.arguments,args);
  assert.equal(body.request_id,requests[1].params.envelope.body.request_id);assert.equal(requests[0].params.public_key,signer.publicKey);
});

const model={id:'fixture',name:'Unit fixture, no LLM',api:'fixture',provider:'fixture',baseUrl:'',reasoning:false,input:['text'],cost:{input:0,output:0,cacheRead:0,cacheWrite:0},contextWindow:8000,maxTokens:1000};
const usage={input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
function scriptedStream(messages) {
  let index=0;
  const fn=()=>{
    const stream=createAssistantMessageEventStream();
    const content=messages[index++] || [{type:'text',text:'fixture complete'}];
    const message={role:'assistant',content,api:'fixture',provider:'fixture',model:'fixture',usage,stopReason:content.some(x=>x.type==='toolCall')?'toolUse':'stop',timestamp:1000};
    queueMicrotask(()=>{stream.push({type:'start',partial:message});stream.push({type:'done',reason:message.stopReason,message});});
    return stream;
  };
  return {fn,count:()=>index};
}
test('real Pi agent loop calls the typed tool without a model API',async()=>{
  const f=fixture();const stream=scriptedStream([[{type:'toolCall',id:'first',name:toolNameFor(skill.id),arguments:args}]]);
  const harness=createPiRelayAgent({goal:'Use the registered file capability.',model,streamFn:stream.fn,skills:[skill],allowedSkills:[skill.id],transport:f.transport});
  const events=[];harness.agent.subscribe(e=>events.push(e.type));const r=await harness.run();
  assert.equal(r.steps,1);assert.equal(r.waiting,false);assert.deepEqual(f.counts(),{submitted:1,executed:1});assert.ok(events.includes('tool_execution_end'));assert.equal(stream.count(),2);
});
test('real Pi stops for owner input without inventing approval',async()=>{
  const f=fixture('input-required');const stream=scriptedStream([[{type:'toolCall',id:'first',name:toolNameFor(skill.id),arguments:args}]]);
  const h=createPiRelayAgent({goal:'A bounded test goal',model,streamFn:stream.fn,skills:[skill],allowedSkills:[skill.id],transport:f.transport});
  const r=await h.run();assert.equal(r.waiting,true);assert.equal(stream.count(),1);assert.equal(f.counts().executed,0);
});
test('real Pi schema validation rejects malformed tool arguments before Relay',async()=>{
  const f=fixture();const stream=scriptedStream([[{type:'toolCall',id:'first',name:toolNameFor(skill.id),arguments:{path:17}}]]);
  const h=createPiRelayAgent({goal:'Schema test',model,streamFn:stream.fn,skills:[skill],allowedSkills:[skill.id],transport:f.transport});
  await h.run();assert.deepEqual(f.counts(),{submitted:0,executed:0});
});
test('no implicit provider or model selection',()=>{assert.throws(()=>createPiRelayAgent({goal:'test',skills:[skill],allowedSkills:[skill.id],transport:{}}),/EXPLICIT/);});
