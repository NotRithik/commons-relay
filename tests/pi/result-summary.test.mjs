import test from 'node:test';
import assert from 'node:assert/strict';
import {completedResultSummary} from '../../adapters/pi/relay-tools.mjs';
test('exact counts come from the returned list, not a model estimate',()=>{
 const skills=Array.from({length:21},(_,i)=>({id:'example.'+i}));
 const s=completedResultSummary({artifacts:[{name:'meta.skills result',parts:[{data:{skills}}]}]});
 assert.equal(s.capability_lists[0].skill_count,21);assert.equal(s.capability_lists[0].skill_ids.length,21);
});
test('empty skill lists are explicitly counted as zero',()=>assert.equal(completedResultSummary({skills:[]}).capability_lists[0].skill_count,0));
test('omitted and malformed data are not turned into claimed counts',()=>{
 for(const r of [null,{omitted:true},{skills:'21'},{skills:[{}]},{artifacts:[{parts:[{data:{skills:[null]}}]}]}])assert.equal(completedResultSummary(r),null);
});
test('the original receipt is not rewritten',()=>{
 const r={skills:[{id:'custom.tool',input_schema:{type:'object'}}]},before=JSON.stringify(r);completedResultSummary(r);assert.equal(JSON.stringify(r),before);
});
