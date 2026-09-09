/** Request one exact owner-reviewed action. This tool never submits or runs it. */
import { Type } from 'typebox';
import { canonical } from './canonical.mjs';
export function createPermissionTool({skills, state, request}) {
  if (!Array.isArray(skills) || !skills.length || skills.length>32 || typeof request!=='function') throw new Error('INVALID_PERMISSION_TOOL_CONFIG');
  const catalog=new Map(skills.map(row=>[row.id,row]));
  if (catalog.size!==skills.length || catalog.has('meta.configure')) throw new Error('INVALID_PERMISSION_TOOL_CONFIG');
  return {
    name:'request_action_permission',label:'Request your permission',
    description:'Ask the owner to approve exactly one action that this conversation is not allowed to run. Explain why it is needed. The action will NOT run until the owner reviews its exact inputs and any spending. After requesting permission, stop. Available action specifications: '+canonical(skills.map(row=>({skill:row.id,description:row.description,arguments:row.input_schema}))),
    parameters:Type.Object({
      skill:Type.Union([...catalog.keys()].map(name=>Type.Literal(name))),
      arguments_json:Type.String({minLength:2,maxLength:4500,description:'Exact action arguments as one JSON object, following the selected action specification.'}),
      reason:Type.String({minLength:1,maxLength:800,description:'Plain explanation of why this action is needed and what it changes or sends.'})
    },{additionalProperties:false}),
    async execute(_id,parameters,signal) {
      if (signal?.aborted) throw new Error('ABORTED');
      if (state.waiting) throw new Error('OWNER_INPUT_REQUIRED');
      if (!catalog.has(parameters.skill) || typeof parameters.arguments_json!=='string' || typeof parameters.reason!=='string' || !parameters.reason.trim()) throw new Error('INVALID_PERMISSION_REQUEST');
      const args=JSON.parse(parameters.arguments_json);canonical(args);
      if (!args || typeof args!=='object' || Array.isArray(args) || Buffer.byteLength(canonical(args))>4500) throw new Error('INVALID_PERMISSION_ARGUMENTS');
      const result=await request({method:'request_permission',params:{skill:parameters.skill,arguments:args,reason:parameters.reason}},signal);
      if (!result || result.permission_required!==true || result.request?.skill!==parameters.skill
          || !canonical(result.request.arguments).equals(canonical(args)) || result.decision!=='pending') throw new Error('INVALID_PERMISSION_REPLY');
      state.waiting=true;state.permission=result.request;
      return {content:[{type:'text',text:'Permission requested. No action has been executed. Wait for the owner to review it.'}],details:result};
    }
  };
}
