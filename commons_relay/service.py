"""Small JSON-line boundary for the Logos Core module. No model is started here."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import stat
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from .codec import Rejected,parse,canonical,unb64
from .engine import Engine,Policy
from .signing import Ed25519,protected_directory

class Service:
    def __init__(self,profile:Path,bridge=None):
        self.bridge=bridge;self.vault=None;self.store=None;self.adapter=None
        self.adapter_guard=threading.RLock()
        self.planner=None
        self.messaging=None;self.mailbox=None;self.message_adapter=None;self.wallet_adapter=None;self.controller=None;self.meta_adapter=None;self.agent_protocol=None;self.agent_adapter=None;self.program_adapter=None;self.external_adapter=None
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
        from .skills import default_registry
        from .external_skills import ExternalSkills
        self.extensions=ExternalSkills(self.root)
        registry=default_registry().extended(self.extensions.skills())
        self.engine=Engine(self.root/'ledger',config['agent_id'],public,self.crypto,policy=Policy(**config['policy']),registry=registry)
        self.engine.quote_provider=self.quote_for_skill
    def quote_for_skill(self,name,args,base):
        if name!='agent.task':return base
        from .skills import Quote
        from .a2a_types import PAYMENT_EXTENSION
        from .codec import amount
        if not isinstance(args,dict) or set(args)!={'agent_address','skill','params'}:raise Rejected('INVALID_AGENT_TASK_ARGUMENTS')
        card=self.get_agent_protocol().verified_card(args['agent_address'])
        if args['skill'] not in {s['id'] for s in card['skills']}:raise Rejected('REMOTE_SKILL_NOT_ADVERTISED')
        payment=next((x for x in card.get('capabilities',{}).get('extensions',[]) if x.get('uri')==PAYMENT_EXTENSION),None)
        if not isinstance(payment,dict) or not isinstance(payment.get('params'),dict) or not isinstance(payment['params'].get('prices'),dict):raise Rejected('REMOTE_PRICE_NOT_ADVERTISED')
        if args['skill'] not in payment['params']['prices']:raise Rejected('REMOTE_PRICE_NOT_ADVERTISED')
        price=amount(payment['params']['prices'][args['skill']])
        return Quote('LEZ-testnet',price,True)
    def close(self):
        if self.planner:self.planner.close()
        if self.controller:self.controller.stop()
        if self.agent_protocol:self.agent_protocol.close()
        if self.messaging:self.messaging.stop()
        if self.mailbox:self.mailbox.close()
        if self.vault:self.vault.close()
        self.engine.close()
    def storage_adapter(self):
        with self.adapter_guard:return self._storage_adapter()
    def _storage_adapter(self):
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
    def get_messaging(self):
        with self.adapter_guard:
            if self.messaging:return self.messaging
            self._storage_adapter()
            from .messaging import Mailbox,Contact,MessagingRuntime
            from .messaging_adapter import MessagingAdapter
            self.mailbox=Mailbox(self.root/'mailbox',self.vault,self.engine.agent)
            peers=self.root/'contacts.json'
            if peers.is_file():
                if peers.is_symlink() or peers.stat().st_size>200000:raise Rejected('INVALID_CONTACTS_FILE')
                values=parse(peers.read_bytes())
                if not isinstance(values,list) or len(values)>256:raise Rejected('INVALID_CONTACTS_LIST')
                for peer in values:self.mailbox.add_contact(Contact.from_public(peer))
            self.messaging=MessagingRuntime(self.bridge,self.mailbox)
            self.message_adapter=MessagingAdapter(self.messaging)
            return self.messaging
    def get_wallet(self):
        with self.adapter_guard:
            if self.wallet_adapter is None:
                from .wallet_adapter import WalletAdapter
                if self.bridge is None:raise Rejected('NATIVE_BRIDGE_NOT_CONNECTED')
                self.wallet_adapter=WalletAdapter(self.root,self.bridge,self.engine)
            return self.wallet_adapter
    def get_agent_protocol(self):
        with self.adapter_guard:
            if self.agent_protocol is None:
                from .a2a_protocol import AgentProtocol
                self.agent_protocol=AgentProtocol(self)
                self.get_controller().protocol=self.agent_protocol
            return self.agent_protocol
    def adapter_for(self,skill):
        if self.extensions.has(skill):
            with self.adapter_guard:
                if self.external_adapter is None:
                    from .external_skills import ExternalAdapter
                    self.external_adapter=ExternalAdapter(self.root,self.extensions,self.engine)
                return self.external_adapter
        if skill in ['agent.discover','agent.task','agent.subscribe','agent.cancel']:
            with self.adapter_guard:
                if self.agent_adapter is None:
                    from .a2a_adapter import A2AAdapter
                    self.agent_adapter=A2AAdapter(self)
                return self.agent_adapter
        if skill in ['meta.skills','meta.status','meta.configure','agent.card']:
            with self.adapter_guard:
                if self.meta_adapter is None:
                    from .meta_adapter import MetaAdapter
                    self.meta_adapter=MetaAdapter(self)
                return self.meta_adapter
        if skill in ['storage.upload','storage.download','storage.list']:return self.storage_adapter()
        if skill in ['storage.share','messaging.send','messaging.join','messaging.create_group']:
            runtime=self.get_messaging();runtime.start()
            return self.message_adapter
        if skill in ['wallet.balance','wallet.history','wallet.send','program.query']:return self.get_wallet()
        if skill in ['program.call','program.deploy']:
            with self.adapter_guard:
                if self.program_adapter is None:
                    from .program_adapter import ProgramAdapter
                    self.program_adapter=ProgramAdapter(self.root,self.get_wallet(),self.engine)
                return self.program_adapter
        raise Rejected('SKILL_ADAPTER_NOT_CONNECTED')
    def get_controller(self):
        with self.adapter_guard:
            if self.controller is None:
                from .controller import Controller
                path=self.root/'owner-channel.json'
                if path.is_symlink() or not path.is_file() or path.stat().st_size>4000:raise Rejected('OWNER_CHANNEL_REQUIRED')
                config=parse(path.read_bytes())
                if not isinstance(config,dict) or set(config)!={'address'}:raise Rejected('INVALID_OWNER_CHANNEL')
                with self.engine.tx() as db:override=db.execute("SELECT value FROM metadata WHERE key='runtime:owner_address'").fetchone()
                self.controller=Controller(self,json.loads(override[0]) if override else config['address'])
            return self.controller
    def get_planner(self):
        with self.adapter_guard:
            if self.planner is None:
                from .planner import Planner
                self.planner=Planner(self,clock=self.engine.clock)
            return self.planner
    def handle(self,request:dict)->dict:
        if not isinstance(request,dict) or set(request)!={'id','method','params'}:raise Rejected('INVALID_SERVICE_REQUEST')
        if not isinstance(request['id'],str) or len(request['id'])>80:raise Rejected('INVALID_REQUEST_ID')
        method=request['method'];params=request['params']
        if not isinstance(params,dict):raise Rejected('INVALID_PARAMETERS')
        if method in ('owner.snapshot', 'owner.skills') and set(params) == {'offset'}:
            from . import owner_views
            view = owner_views.snapshot if method == 'owner.snapshot' else owner_views.skills_page
            return view(self.engine, params['offset'])
        if method == 'owner.skill' and set(params) == {'name'}:
            from .owner_views import skill_details
            return skill_details(self.engine, params['name'])
        if method == 'owner.task' and set(params) == {'task_id'}:
            from .owner_views import task_details
            return task_details(self.engine, params['task_id'])
        if method == 'planner.status' and not params:
            return self.get_planner().status()
        if method == 'planner.history' and set(params) == {'offset'}:
            return self.get_planner().history(params['offset'])
        if method == 'planner.goal' and set(params) == {'goal_id'}:
            return self.get_planner().view(params['goal_id'])
        if method == 'planner.start' and set(params) == {'envelope'}:
            return self.get_planner().start(params['envelope'])
        if method == 'planner.cancel' and set(params) == {'goal_id','envelope'}:
            return self.get_planner().cancel(params['goal_id'],params['envelope'])
        if method=='status' and not params:
            from .status_views import local_status
            return local_status(self)
        if method=='storage.connect_local' and set(params)=={'peer_id','addresses'}:
            self.storage_adapter().store.initialize()
            result=self.bridge.call('storage.connect-local',params)
            return {'connected':True,'result':result}
        if method=='agent.start' and not params:
            protocol=self.get_agent_protocol();self.get_controller().start();protocol.discover(protocol.config['discovery_topic']);return {'started':True,'card':protocol.card()}
        if method=='agent.cards' and not params:
            return {'agents':self.get_agent_protocol().cards()}
        if method=='controller.start' and not params:
            controller=self.get_controller();controller.start();return {'started':True,'owner_channel':controller.owner_address}
        if method=='controller.status' and not params:
            controller=self.get_controller();return {'started':controller.thread is not None,'active_task':controller.active,'last_error':controller.last_error}
        if method=='schedule' and set(params)=={'task_id'}:
            controller=self.get_controller();controller.start();return controller.schedule(params['task_id'])
        if method=='owner.send' and set(params)=={'recipient','envelope'}:
            runtime=self.get_messaging();runtime.start()
            return runtime.mailbox.enqueue(params['recipient'],'owner-command',params['envelope'],ttl=86400)
        if method=='cancel' and set(params)=={'envelope'}:
            from .signing import verify_envelope
            body=verify_envelope(params['envelope'],self.engine.owner_key,self.engine.crypto)
            if set(body)!={'domain','agent_id','task_id','expires_at'} or body['domain']!='commons/relay/cancel/v1' or body['agent_id']!=self.engine.agent or type(body['expires_at'])is not int or not int(self.engine.clock())<body['expires_at']<=int(self.engine.clock())+self.engine.policy.approval_ttl:raise Rejected('INVALID_CANCELLATION')
            return self.engine.cancel(body['task_id'],self.engine.owner)
        if method=='messaging.reload_contacts' and not params:
            runtime=self.get_messaging()
            from .messaging import Contact
            path=self.root/'contacts.json'
            if path.is_symlink() or path.stat().st_size>200000:raise Rejected('INVALID_CONTACTS_FILE')
            contacts=parse(path.read_bytes())
            if not isinstance(contacts,list) or len(contacts)>256:raise Rejected('INVALID_CONTACTS_LIST')
            for contact in contacts:runtime.mailbox.add_contact(Contact.from_public(contact))
            return {'contacts':len(contacts)}
        if method=='messaging.contact' and not params:
            return self.get_messaging().mailbox.contact().public()
        if method=='messaging.start' and not params:
            runtime=self.get_messaging();runtime.start()
            return {'started':True,'address':runtime.mailbox.address,'node':self.bridge.call('delivery.info',{})}
        if method == 'owner.inbox' and set(params) == {'after'}:
            from .owner_views import inbox_page
            return inbox_page(self.get_messaging().mailbox, params['after'])
        if method=='messaging.messages' and set(params)=={'after'}:
            runtime=self.get_messaging();return {'messages':runtime.mailbox.messages(params['after'])}
        if method=='messaging.pump' and not params:
            runtime=self.get_messaging();received=runtime.pump();return {'received':len(received),'last_error':runtime.last_error}
        if method == 'transport.status' and not params:
            runtime = self.get_messaging()
            runtime.initialize()
            health = self.bridge.call('delivery.health', {})
            return {'transport_health': health, 'last_delivery_error': runtime.last_error}
        if method=='diagnostics' and not params:
            if self.bridge is None:raise Rejected('NATIVE_BRIDGE_NOT_CONNECTED')
            return {'modules':self.bridge.call('modules.probe',{}),'storage_version':self.bridge.call('storage.version',{})}
        if method=='run' and set(params)=={'task_id'}:
            task=self.engine.get(params['task_id'])
            adapter=self.adapter_for(task['skill'])
            return self.engine.execute(task['id'],adapter)
        if method=='reconcile' and set(params)=={'task_id'}:
            task=self.engine.get(params['task_id'])
            return self.engine.reconcile(params['task_id'],self.adapter_for(task['skill']))
        if method=='task' and set(params)=={'task_id'}:
            return self.engine.get(params['task_id'])
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
    # These are ordinary local I/O workers, not AI agents. Bounded concurrent
    # dispatch keeps status and approvals responsive while an adapter waits.
    slots=threading.BoundedSemaphore(8)
    def dispatch(request):
        request_id=request.get('id','') if isinstance(request,dict) else ''
        if not isinstance(request_id,str) or len(request_id)>80:request_id=''
        try:
            try:reply={'id':request_id,'success':True,'result':service.handle(request)}
            except Rejected as e:reply={'id':request_id,'success':False,'error':str(e)}
            except Exception:reply={'id':request_id,'success':False,'error':'SERVICE_OPERATION_FAILED'}
            try:
                canonical(reply)
            except Rejected:
                reply={'id':request_id,'success':False,'error':'RESPONSE_TOO_LARGE_OR_INVALID'}
            wire.write(reply)
        finally:slots.release()
    try:
        with ThreadPoolExecutor(max_workers=4,thread_name_prefix='relay-local-io') as workers:
            while True:
                request=wire.next_command()
                if request is None:break
                if not slots.acquire(blocking=False):
                    rid=request.get('id','') if isinstance(request,dict) else ''
                    wire.write({'id':rid if isinstance(rid,str) and len(rid)<=80 else '', 'success':False,'error':'LOCAL_WORK_QUEUE_FULL'})
                    continue
                workers.submit(dispatch,request)
    finally:service.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--native-bridge',action='store_true')
    args=parser.parse_args()
    if args.native_bridge:serve_native(args.profile)
    else:serve(args.profile)
if __name__=='__main__':main()
