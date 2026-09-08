/** Optional, explicitly configured OpenAI Responses transport for Pi.
 * No hosted tools, no remote shell, no automatic retries and no saved responses.
 */
import { readFileSync, writeFileSync, renameSync, openSync, closeSync, unlinkSync, existsSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { createAssistantMessageEventStream } from '@earendil-works/pi-ai/utils/event-stream';
import { canonical } from './canonical.mjs';

export class TokenBudget {
  constructor({path,maximumUsd,inputUsdPerMillion,outputUsdPerMillion}) {
    if(!Number.isFinite(maximumUsd)||maximumUsd<=0||maximumUsd>15)throw new Error('INVALID_TEST_BUDGET');
    for(const x of [inputUsdPerMillion,outputUsdPerMillion])if(!Number.isFinite(x)||x<0||x>1000)throw new Error('INVALID_TOKEN_PRICE');
    this.path=path;this.maximum=Math.floor(maximumUsd*1e6);this.inputPrice=inputUsdPerMillion;this.outputPrice=outputUsdPerMillion;
    if(!existsSync(path))this.save({version:1,maximum_micro_usd:this.maximum,settled_micro_usd:0,reservations:{},requests:[]});
    const state=this.read();if(state.maximum_micro_usd!==this.maximum)throw new Error('BUDGET_LIMIT_CHANGED');
  }
  read(){const state=JSON.parse(readFileSync(this.path,'utf8'));if(state.version!==1||!Number.isSafeInteger(state.settled_micro_usd)||state.settled_micro_usd<0)throw new Error('INVALID_BUDGET_LEDGER');return state;}
  save(state){const temp=this.path+'.'+randomUUID()+'.tmp';writeFileSync(temp,JSON.stringify(state,null,2)+'\n',{flag:'wx',mode:0o600});renameSync(temp,this.path);}
  locked(fn){const lock=this.path+'.lock';let fd;try{fd=openSync(lock,'wx',0o600);}catch{throw new Error('BUDGET_LEDGER_BUSY');}try{const state=this.read();const result=fn(state);this.save(state);return result;}finally{closeSync(fd);unlinkSync(lock);}}
  reserve(payloadBytes,maxOutput){
    // One input token per UTF-8 byte plus ample protocol overhead is deliberately
    // conservative for text-only requests; never discount cached tokens here.
    const inputBound=payloadBytes+4096;
    const upper=Math.ceil(inputBound*this.inputPrice+maxOutput*this.outputPrice);
    return this.locked(state=>{
      const held=Object.values(state.reservations).reduce((a,b)=>a+b,0);
      if(state.settled_micro_usd+held+upper>state.maximum_micro_usd)throw new Error('TEST_BUDGET_EXHAUSTED');
      if(state.requests.length+Object.keys(state.reservations).length>=100)throw new Error('TEST_REQUEST_LIMIT');
      const id=randomUUID();state.reservations[id]=upper;return id;
    });
  }
  settle(id,usage,responseId){
    const input=usage?.input_tokens,output=usage?.output_tokens;
    if(!Number.isSafeInteger(input)||input<0||!Number.isSafeInteger(output)||output<0)throw new Error('API_USAGE_MISSING');
    return this.locked(state=>{
      const held=state.reservations[id];if(!Number.isSafeInteger(held))throw new Error('BUDGET_RESERVATION_MISSING');
      const charged=Math.ceil(input*this.inputPrice+output*this.outputPrice);
      delete state.reservations[id];state.settled_micro_usd+=charged;
      state.requests.push({reservation:id,response_id:String(responseId||'').slice(0,100),input_tokens:input,output_tokens:output,estimated_micro_usd:charged});
      if(charged>held)state.unexpected_usage=true;
      return charged;
    });
  }
  summary(){const s=this.read();return {limit_usd:s.maximum_micro_usd/1e6,estimated_spend_usd:s.settled_micro_usd/1e6,reserved_for_unconfirmed_requests_usd:Object.values(s.reservations).reduce((a,b)=>a+b,0)/1e6,completed_requests:s.requests.length};}
}
function textParts(content){if(typeof content==='string')return content;if(!Array.isArray(content))throw new Error('INVALID_MESSAGE_CONTENT');return content.map(x=>{if(x.type!=='text'||typeof x.text!=='string')throw new Error('TEXT_ONLY_TEST_TRANSPORT');return x.text;}).join('\n');}
/** Provider strict generation supports a subset of JSON Schema. Unknown
 * keywords keep their original schema but opt out of provider strict mode.
 * The Relay permission engine still enforces the complete original schema.
 */
export function supportsStrictSchema(schema, depth=0) {
  if (!schema || typeof schema !== 'object' || Array.isArray(schema) || depth > 16) return false;
  const common=['type','description','title','enum'];
  const keywords={
    object:['properties','required','additionalProperties'],
    array:['items','minItems','maxItems'],
    string:['minLength','maxLength','pattern','format'],
    integer:['minimum','maximum','exclusiveMinimum','exclusiveMaximum','multipleOf'],
    number:['minimum','maximum','exclusiveMinimum','exclusiveMaximum','multipleOf'],
    boolean:[], null:[]
  };
  if (!Object.hasOwn(keywords, schema.type)) return false;
  const allowed=new Set([...common,...keywords[schema.type]]);
  if (Object.keys(schema).some(key=>!allowed.has(key))) return false;
  if (schema.type === 'object') {
    const props=schema.properties;
    if (!props || typeof props!=='object' || Array.isArray(props)) return false;
    const keys=Object.keys(props);
    if (schema.additionalProperties !== false || !Array.isArray(schema.required)
        || new Set(schema.required).size !== keys.length || schema.required.length !== keys.length
        || keys.some(key=>!schema.required.includes(key))) return false;
    return keys.every(key=>supportsStrictSchema(props[key],depth+1));
  }
  if (schema.type === 'array') return supportsStrictSchema(schema.items,depth+1);
  return true;
}
export function responsePayload(context,model,maxOutput,reasoning='none') {
  if(!context || !Array.isArray(context.messages)||context.messages.length>100)throw new Error('INVALID_PLANNER_CONTEXT');
  const input=[];
  for(const message of context.messages){
    if(message.role==='user')input.push({role:'user',content:textParts(message.content)});
    else if(message.role==='assistant'){
      const text=message.content.filter(x=>x.type==='text').map(x=>x.text).join('\n');
      if(text)input.push({role:'assistant',content:text});
      for(const call of message.content.filter(x=>x.type==='toolCall'))input.push({type:'function_call',call_id:call.id,name:call.name,arguments:JSON.stringify(call.arguments)});
    }else if(message.role==='toolResult')input.push({type:'function_call_output',call_id:message.toolCallId,output:textParts(message.content)});
    else throw new Error('UNSUPPORTED_PLANNER_MESSAGE_ROLE');
  }
  const tools=(context.tools||[]).map(tool=>({type:'function',name:tool.name,description:tool.description,parameters:tool.parameters,strict:supportsStrictSchema(tool.parameters)}));
  return {model,input,instructions:context.systemPrompt||'',tools,tool_choice:'auto',parallel_tool_calls:false,store:false,stream:false,max_output_tokens:maxOutput,reasoning:{effort:reasoning}};
}
export function createOpenAIStream({modelId,apiKey,budget,maxOutputTokens=3072,maxRequests=6,fetchImpl=globalThis.fetch,onStatus=()=>{}}) {
  if(!/^gpt-[a-zA-Z0-9.-]{1,80}$/.test(modelId)||typeof apiKey!=='string'||!apiKey.startsWith('sk-')||!budget)throw new Error('EXPLICIT_OPENAI_CONFIG_REQUIRED');
  if(!Number.isInteger(maxOutputTokens)||maxOutputTokens<128||maxOutputTokens>8192||!Number.isInteger(maxRequests)||maxRequests<1||maxRequests>20)throw new Error('INVALID_REQUEST_LIMIT');
  let count=0;
  return (_model,context,options={})=>{
    const stream=createAssistantMessageEventStream();
    (async()=>{
      let held=null;
      try{
        if(++count>maxRequests)throw new Error('PLANNER_REQUEST_LIMIT');
        if(options.signal?.aborted)throw new Error('PLANNER_ABORTED');
        const payload=responsePayload(context,modelId,maxOutputTokens);
        const body=JSON.stringify(payload);const bytes=Buffer.byteLength(body);
        if(bytes>100000)throw new Error('PLANNER_CONTEXT_LIMIT');
        held=budget.reserve(bytes,maxOutputTokens);
        onStatus({event:'model_request',number:count,model:modelId,budget:budget.summary()});
        const response=await fetchImpl('https://api.openai.com/v1/responses',{method:'POST',redirect:'error',
          headers:{'Authorization':'Bearer '+apiKey,'Content-Type':'application/json'},body,
          signal:options.signal?AbortSignal.any([options.signal,AbortSignal.timeout(90000)]):AbortSignal.timeout(90000)});
        if(!response.ok)throw new Error('OPENAI_HTTP_'+response.status);
        const raw=await response.text();if(Buffer.byteLength(raw)>1000000)throw new Error('API_RESPONSE_LIMIT');
        const data=JSON.parse(raw);const charged=budget.settle(held,data.usage,data.id);held=null;
        const content=[];
        for(const item of data.output||[]){
          if(item.type==='message')for(const part of item.content||[])if(part.type==='output_text')content.push({type:'text',text:part.text});
          if(item.type==='function_call'){
            const args=JSON.parse(item.arguments);canonical(args);
            content.push({type:'toolCall',id:item.call_id,name:item.name,arguments:args});
          }
        }
        const stopReason=data.status==='incomplete'?'length':content.some(x=>x.type==='toolCall')?'toolUse':'stop';
        const input=data.usage.input_tokens,output=data.usage.output_tokens;
        const message={role:'assistant',content,api:'openai-responses',provider:'openai',model:modelId,
          usage:{input,output,cacheRead:0,cacheWrite:0,totalTokens:input+output,cost:{input:input*budget.inputPrice/1e6,output:output*budget.outputPrice/1e6,cacheRead:0,cacheWrite:0,total:charged/1e6}},
          stopReason,timestamp:Date.now()};
        onStatus({event:'model_response',number:count,model:modelId,input_tokens:input,output_tokens:output,budget:budget.summary()});
        stream.push({type:'start',partial:message});stream.push({type:'done',reason:stopReason,message});
      }catch(error){
        const code=typeof error?.message==='string'&&/^[A-Z_0-9]{1,100}$/.test(error.message)?error.message:'MODEL_TRANSPORT_FAILED';
        // A timeout may have been billed. Its reservation remains held.
        const message={role:'assistant',content:[],api:'openai-responses',provider:'openai',model:modelId,
          usage:{input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}},
          stopReason:'error',errorMessage:code,timestamp:Date.now()};
        onStatus({event:'model_error',code,reservation_held:held!==null,budget:budget.summary()});
        stream.push({type:'error',reason:'error',error:message});
      }
    })();return stream;
  };
}
