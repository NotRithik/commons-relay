import test from 'node:test';
import assert from 'node:assert/strict';
import {observeTaskCompletion} from '../../adapters/pi/relay-tools.mjs';
const task=(state='working',extra={})=>({id:'a'.repeat(32),skill:'agent.task',state,maximum_spend:'0',asset:'LEZ-testnet',result:{},...extra});
function fixture(states,extra={}){
 let clock=0,reads=0;
 const transport={get:async(id)=>{assert.equal(id,'a'.repeat(32));reads++;return task(states.shift()||'working');},submit:()=>{throw Error('MUST_NOT_SUBMIT');},run:()=>{throw Error('MUST_NOT_RUN');}};
 const options={waitMs:9000,pollIntervalMs:3000,now:()=>clock,pause:async(ms)=>{clock+=ms;},...extra};
 return {transport,options,count:()=>reads};
}
test('a slow free peer completes without a second task or execution',async()=>{
 const f=fixture(['working','completed']);const r=await observeTaskCompletion(task(),'agent.task',f.transport,undefined,f.options);
 assert.equal(r.state,'completed');assert.equal(f.count(),2);
});
test('the same task remains pending when the read window expires',async()=>{
 const f=fixture([]);const r=await observeTaskCompletion(task(),'agent.task',f.transport,undefined,f.options);
 assert.equal(r.state,'working');assert.equal(f.count(),3);
});
test('approval and nonzero-spend tasks never enter the read loop',async()=>{
 for(const t of [task('input-required'),task('unknown',{maximum_spend:'3'}),task('working',{maximum_spend:'3'}),task('completed')]){
  const f=fixture([]);const r=await observeTaskCompletion(t,'agent.task',f.transport,undefined,f.options);assert.equal(r,t);assert.equal(f.count(),0);
 }
});
test('owner cancellation aborts observation without another operation',async()=>{
 const controller=new AbortController(),f=fixture([]);controller.abort();
 await assert.rejects(observeTaskCompletion(task(),'agent.task',f.transport,controller.signal,f.options),/ABORTED/);assert.equal(f.count(),0);
});
test('an unrelated reply cannot replace the selected task',async()=>{
 const f=fixture([]);f.transport.get=async()=>task('completed',{id:'b'.repeat(32)});
 await assert.rejects(observeTaskCompletion(task(),'agent.task',f.transport,undefined,f.options),/RELAY_TASK_ID_CHANGED/);
});
test('a changed spending ceiling is rejected during observation',async()=>{
 const f=fixture([]);f.transport.get=async()=>task('completed',{maximum_spend:'9'});
 await assert.rejects(observeTaskCompletion(task(),'agent.task',f.transport,undefined,f.options),/RELAY_TASK_SPEND_CHANGED/);
});
test('observation stops immediately when an approval is required',async()=>{
 const f=fixture(['input-required','completed']);const r=await observeTaskCompletion(task(),'agent.task',f.transport,undefined,f.options);
 assert.equal(r.state,'input-required');assert.equal(f.count(),1);
});
test('ordinary adapters have no implicit completion polling',async()=>{
 const f=fixture([]);await observeTaskCompletion(task(),'agent.task',f.transport);assert.equal(f.count(),0);
});

test('zero-spend reconciliation may be observed without repeating its side effect',async()=>{
 const f=fixture(['completed']);const r=await observeTaskCompletion(task('unknown'),'agent.task',f.transport,undefined,f.options);
 assert.equal(r.state,'completed');assert.equal(f.count(),1);
});
