/** Product acceptance test, not a coding subagent. Uses only synthetic file tools. */
import { readFileSync,writeFileSync,statSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname,join } from 'node:path';
import { envelopeSigner } from './canonical.mjs';
import { signedTransport,createPiRelayAgent } from './relay-tools.mjs';
import { coreRequester } from './core-client.mjs';
import { TokenBudget,createOpenAIStream } from './openai-responses.mjs';

const configPath=process.argv[2];
if(!configPath)throw new Error('EXPLICIT_TEST_CONFIG_REQUIRED');
const config=JSON.parse(readFileSync(configPath,'utf8'));
if(config.modelId!=='gpt-5.6-luna'||config.maximumUsd!==1||config.syntheticOnly!==true)throw new Error('TEST_CONFIG_NOT_AUTHORIZED');
if(statSync(config.envFile).size>20000)throw new Error('INVALID_KEY_FILE');
let key;
for(const line of readFileSync(config.envFile,'utf8').split(/\r?\n/)){
  const match=/^(?:export\s+)?(?:API_KEY|OPENAI_API_KEY)\s*=\s*(["']?)(sk-[^\s"']+)\1\s*$/.exec(line.trim());
  if(match)key=match[2];
}
if(!key)throw new Error('API_KEY_NOT_FOUND');
const request=coreRequester(config.core);
const registry=await request({method:'skills',params:{}});
const signer=envelopeSigner(readFileSync(config.delegateKey));
if(signer.keyId!==config.delegateKeyId)throw new Error('DELEGATE_KEY_MISMATCH');
const transport=signedTransport({agentId:config.agentId,grantId:config.grantId,signer,request,ttl:300});
const budget=new TokenBudget({path:config.budgetPath,maximumUsd:config.maximumUsd,inputUsdPerMillion:.2,outputUsdPerMillion:1.2});
const events=[];
const stream=createOpenAIStream({modelId:config.modelId,apiKey:key,budget,maxOutputTokens:3072,maxRequests:6,
  onStatus:value=>{events.push(value);console.log(JSON.stringify(value));}});
key=undefined;
const model={id:config.modelId,name:'GPT-5.6 Luna',api:'openai-responses',provider:'openai',baseUrl:'https://api.openai.com/v1',reasoning:false,input:['text'],cost:{input:.2,output:1.2,cacheRead:.02,cacheWrite:.2},contextWindow:1050000,maxTokens:3072};
const tasks=[];
const harness=createPiRelayAgent({goal:config.goal,model,streamFn:stream,skills:registry.skills,allowedSkills:config.allowedSkills,transport,maxSteps:8,maxTurns:6,
  onTask:task=>{tasks.push(task);console.log(JSON.stringify({event:'verified_task',task}));}});
const result=await harness.run(AbortSignal.timeout(240000));
const completed=tasks.filter(t=>t.state==='completed');
const expected=new Set(['storage.upload','storage.download','storage.list']);
for(const task of completed)expected.delete(task.skill);
const report={passed:expected.size===0&&!result.waiting,model:config.modelId,provider:'OpenAI official API',
  harness:'actual Pi Agent loop',synthetic_input_only:true,hosted_tools_enabled:false,store:false,
  budget:budget.summary(),completed_tasks:completed,missing_skills:[...expected],state:result,timestamp:new Date().toISOString()};
writeFileSync(config.reportPath,JSON.stringify(report,null,2)+'\n',{mode:0o600});
console.log(JSON.stringify({event:'acceptance_result',...report}));
if(!report.passed)process.exitCode=1;
