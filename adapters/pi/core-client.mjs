/** Local Logos CLI transport; it never starts a model, server or shell. */
import { spawn } from 'node:child_process';
import { realpathSync,statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
const script=fileURLToPath(new URL('../../scripts/control.py',import.meta.url));
export function coreRequester({python,logosctl,session,timeoutSeconds=120}) {
  for(const p of [python,logosctl,session])realpathSync(p);
  if(!Number.isInteger(timeoutSeconds)||timeoutSeconds<1||timeoutSeconds>7200)throw new Error('INVALID_CORE_TIMEOUT');
  const env={};for(const key of ['HOME','PATH','TMPDIR','LD_LIBRARY_PATH','DYLD_LIBRARY_PATH'])if(process.env[key])env[key]=process.env[key];
  return (command,signal)=>new Promise((resolve,reject)=>{
    if(signal?.aborted){reject(new Error('CORE_REQUEST_ABORTED'));return;}
    const params=JSON.stringify(command.params);
    if(params.length>60000||typeof command.method!=='string'){reject(new Error('INVALID_CORE_COMMAND'));return;}
    const child=spawn(python,['-I',script,'--logosctl',logosctl,'--session',session,'--timeout',String(timeoutSeconds),'--method',command.method,'--params',params],{env,stdio:['ignore','pipe','pipe'],shell:false});
    let output='',errorBytes=0,done=false;
    const abort=()=>child.kill('SIGTERM');signal?.addEventListener('abort',abort,{once:true});
    const timer=setTimeout(abort,(timeoutSeconds+30)*1000);
    child.stdout.on('data',chunk=>{output+=chunk.toString();if(output.length>131072)child.kill('SIGTERM');});
    child.stderr.on('data',chunk=>{errorBytes+=chunk.length;if(errorBytes>100000)child.kill('SIGTERM');});
    child.on('error',()=>finish(new Error('CORE_PROCESS_FAILED')));
    child.on('close',code=>{
      try {
        if(code!==0)throw new Error('CORE_COMMAND_REJECTED');
        const reply=JSON.parse(output);if(reply.success!==true)throw new Error('CORE_COMMAND_REJECTED');
        finish(null,reply.result);
      }catch {finish(new Error('CORE_COMMAND_REJECTED'));}
    });
    function finish(error,result){if(done)return;done=true;clearTimeout(timer);signal?.removeEventListener('abort',abort);if(error)reject(error);else resolve(result);}
  });
}
