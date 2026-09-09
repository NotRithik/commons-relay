import test from 'node:test';
import assert from 'node:assert/strict';
import {createPermissionTool} from '../../adapters/pi/permission-tool.mjs';
import {createPiRelayAgent} from '../../adapters/pi/relay-tools.mjs';
import {createAssistantMessageEventStream} from '../../adapters/pi/node_modules/@earendil-works/pi-ai/dist/utils/event-stream.js';
const action={id:'messaging.send',description:'Send an encrypted message',input_schema:{type:'object',properties:{recipient:{type:'string'},message:{type:'string'}},required:['recipient','message'],additionalProperties:false}};
const read={id:'meta.status',description:'Read status',input_schema:{type:'object',properties:{},required:[],additionalProperties:false}};
const args={recipient:'fixture-peer',message:'Synthetic permission test.'};
const proposal={skill:action.id,arguments_json:JSON.stringify(args),reason:'Send your requested test message to the configured peer.'};
function fixture(reply) {
 const state={waiting:false};const calls=[];
 const request=async command=>{calls.push(command);return reply || {permission_required:true,request:{...command.params,intent_hash:'a'.repeat(64)},decision:'pending',task_id:null};};
 return {state,calls,request,tool:createPermissionTool({skills:[action],state,request})};
}
test('permission tool asks without submitting or running an action',async()=>{
 const f=fixture();const result=await f.tool.execute('one',proposal);
 assert.deepEqual(f.calls,[{method:'request_permission',params:{skill:action.id,arguments:args,reason:proposal.reason}}]);
 assert.equal(f.state.waiting,true);assert.deepEqual(f.state.permission.arguments,args);
 assert.match(result.content[0].text,/No action has been executed/);
});
test('permission tool stops subsequent operations while waiting',async()=>{
 const f=fixture();await f.tool.execute('one',proposal);await assert.rejects(f.tool.execute('two',proposal),/OWNER_INPUT_REQUIRED/);assert.equal(f.calls.length,1);
});
test('cancelled permission proposal makes no request',async()=>{
 const f=fixture();const abort=new AbortController();abort.abort();await assert.rejects(f.tool.execute('one',proposal,abort.signal),/ABORTED/);assert.equal(f.calls.length,0);
});
test('unknown actions and malformed argument objects never reach parent',async()=>{
 for(const p of [{...proposal,skill:'meta.configure'},{...proposal,arguments_json:'[]'},{...proposal,arguments_json:'null'},{...proposal,arguments_json:'{'}]) {
  const f=fixture();await assert.rejects(f.tool.execute('one',p));assert.equal(f.calls.length,0);
 }
});
test('an altered parent reply is not represented as the requested action',async()=>{
 const f=fixture({permission_required:true,request:{skill:action.id,arguments:{...args,recipient:'different-peer'}},decision:'pending'});
 await assert.rejects(f.tool.execute('one',proposal),/INVALID_PERMISSION_REPLY/);assert.equal(f.state.waiting,false);
});
test('policy configuration cannot be exposed as a requestable action',()=>{
 assert.throws(()=>createPermissionTool({skills:[{...action,id:'meta.configure'}],state:{},request:async()=>{}}),/INVALID_PERMISSION_TOOL_CONFIG/);
});
test('actual Pi loop requests permission and stops before another model turn or task',async()=>{
 let turns=0,submits=0,runs=0;const f=fixture();
 const model={id:'fixture',name:'Fixture',api:'fixture',provider:'fixture',baseUrl:'',reasoning:false,input:['text'],cost:{input:0,output:0,cacheRead:0,cacheWrite:0},contextWindow:8000,maxTokens:1000};
 const streamFn=()=>{turns++;const stream=createAssistantMessageEventStream();const message={role:'assistant',content:[{type:'toolCall',id:'permission-call',name:'request_action_permission',arguments:proposal}],api:'fixture',provider:'fixture',model:'fixture',usage:{input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}},stopReason:'toolUse',timestamp:1000};queueMicrotask(()=>{stream.push({type:'start',partial:message});stream.push({type:'done',reason:'toolUse',message});});return stream;};
 const h=createPiRelayAgent({goal:'Request a test message.',model,streamFn,skills:[read],allowedSkills:[read.id],permissionSkills:[action],requestPermission:f.request,transport:{submit:async()=>{submits++;throw new Error('UNEXPECTED');},run:async()=>{runs++;throw new Error('UNEXPECTED');}}});
 const result=await h.run();assert.equal(result.waiting,true);assert.equal(turns,1);assert.equal(submits,0);assert.equal(runs,0);assert.equal(f.calls.length,1);assert.equal(h.state.permission.skill,action.id);
});
