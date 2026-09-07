"""Small JSON-line boundary for the Logos Core module. No model is started here."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import stat
import sys
from .codec import Rejected,parse,canonical,unb64
from .engine import Engine,Policy
from .signing import Ed25519,protected_directory

class Service:
    def __init__(self,profile:Path,bridge=None):
        self.bridge=bridge;self.vault=None;self.store=None;self.adapter=None
        self.root=protected_directory(profile)
        settings=self.root/'settings.json'
        if settings.is_symlink() or not settings.is_file() or stat.S_IMODE(settings.stat().st_mode)&0o077:
            raise Rejected('SETTINGS_REQUIRE_OWNER_ONLY_PERMISSIONS')
        config=parse(settings.read_bytes())
        if set(config)!={'schema_version','agent_id','owner_public_key','policy','network'} or config['schema_version']!=1 or config['network']!='testnet':
            raise Rejected('INVALID_TESTNET_CONFIGURATION')
        public=unb64(config['owner_public_key'],32)
        if len(public)!=32:raise Rejected('INVALID_OWNER_PUBLIC_KEY')
        self.crypto=Ed25519(self.root/'crypto')
        self.engine=Engine(self.root/'ledger',config['agent_id'],public,self.crypto,policy=Policy(**config['policy']))
    def close(self):
        if self.vault:self.vault.close()
        self.engine.close()
    def storage_adapter(self):
        if self.bridge is None:raise Rejected('NATIVE_BRIDGE_NOT_CONNECTED')
        if self.adapter is None:
            from .file_crypto import Sodium
            from .vault import Vault
            from .bridge import LogosStorage
            from .storage_adapter import StorageAdapter
            inputs=protected_directory(self.root/'inputs');outputs=protected_directory(self.root/'outputs')
            library=os.environ.get('COMMONS_RELAY_SODIUM_LIBRARY')
            self.vault=Vault(self.root/'vault',inputs,outputs,Sodium(library))
            self.store=LogosStorage(self.bridge);self.adapter=StorageAdapter(self.vault,self.store)
        return self.adapter
    def handle(self,request:dict)->dict:
        if not isinstance(request,dict) or set(request)!={'id','method','params'}:raise Rejected('INVALID_SERVICE_REQUEST')
        if not isinstance(request['id'],str) or len(request['id'])>80:raise Rejected('INVALID_REQUEST_ID')
        method=request['method'];params=request['params']
        if not isinstance(params,dict):raise Rejected('INVALID_PARAMETERS')
        if method=='status' and not params:
            result=self.engine.overview()
            result['transport']='Logos Core local IPC'
            result['adapters_connected']=['storage-vault'] if self.adapter else []
            result['wallet_funded']=False
            result['inference_enabled']=False
            return result
        if method=='diagnostics' and not params:
            if self.bridge is None:raise Rejected('NATIVE_BRIDGE_NOT_CONNECTED')
            return {'modules':self.bridge.call('modules.probe',{}),'storage_version':self.bridge.call('storage.version',{})}
        if method=='run' and set(params)=={'task_id'}:
            task=self.engine.get(params['task_id'])
            if task['skill'] not in ['storage.upload','storage.download','storage.list']:raise Rejected('SKILL_ADAPTER_NOT_CONNECTED')
            adapter=self.storage_adapter()
            self.store.initialize()
            return self.engine.execute(task['id'],adapter)
        if method=='reconcile' and set(params)=={'task_id'}:
            return self.engine.reconcile(params['task_id'],self.storage_adapter())
        if method=='skills' and not params:return {'skills':self.engine.registry.describe()}
        if method=='submit' and set(params)=={'envelope','public_key'}:
            return self.engine.submit(params['envelope'],unb64(params['public_key'],32))
        if method=='approve' and set(params)=={'envelope'}:return self.engine.approve(params['envelope'])
        if method=='grant' and set(params)=={'envelope'}:return self.engine.register_grant(params['envelope'])
        if method=='revoke' and set(params)=={'envelope'}:return self.engine.revoke_grant(params['envelope'])
        if method=='recover' and not params:return {'reconciliation_required':self.engine.recover_stale()}
        raise Rejected('UNSUPPORTED_SERVICE_METHOD')

def serve(profile:Path,source=None,sink=None):
    service=Service(profile);source=source or sys.stdin.buffer;sink=sink or sys.stdout.buffer
    try:
        for _ in range(1000000):
            data=source.readline(65538)
            if not data:return
            if len(data)>65536 or not data.endswith(b'\n'):
                sink.write(b'{"id":"","success":false,"error":"INVALID_MESSAGE_FRAME"}\n');sink.flush();return
            request=None
            try:
                request=parse(data);result=service.handle(request)
                response={'id':request['id'],'success':True,'result':result}
            except Rejected as e:
                rid=request.get('id','') if isinstance(request,dict) else ''
                response={'id':rid if isinstance(rid,str) and len(rid)<80 else '', 'success':False,'error':str(e)}
            except Exception:
                response={'id':'','success':False,'error':'SERVICE_OPERATION_FAILED'}
            sink.write(canonical(response)+b'\n');sink.flush()
    finally:service.close()

def serve_native(profile:Path,source=None,sink=None):
    from .bridge import Wire
    wire=Wire(source or sys.stdin.buffer,sink or sys.stdout.buffer)
    service=Service(profile,wire);wire.start()
    try:
        while True:
            request=wire.next_command()
            if request is None:return
            request_id=request.get('id','') if isinstance(request,dict) else ''
            if not isinstance(request_id,str) or len(request_id)>80:request_id=''
            try:reply={'id':request_id,'success':True,'result':service.handle(request)}
            except Rejected as e:reply={'id':request_id,'success':False,'error':str(e)}
            except Exception:reply={'id':request_id,'success':False,'error':'SERVICE_OPERATION_FAILED'}
            wire.write(reply)
    finally:service.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--native-bridge',action='store_true')
    args=parser.parse_args()
    if args.native_bridge:serve_native(args.profile)
    else:serve(args.profile)
if __name__=='__main__':main()
