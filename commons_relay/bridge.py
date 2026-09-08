"""Multiplexed JSON-line link to the enclosing Logos Core plugin.

The reader has no network access or skill dispatch. It only routes bounded
responses; the service remains the single task executor.
"""
from __future__ import annotations
import queue
import secrets
import threading
from typing import BinaryIO
from .codec import Rejected,canonical,parse

ACTIONS=frozenset(['modules.probe','storage.version','storage.init','storage.start',
                    'storage.upload','storage.download','storage.manifests','storage.publish-card','storage.connect-local',
 'delivery.init','delivery.start','delivery.info','delivery.subscribe','delivery.unsubscribe','delivery.send','delivery.events','delivery.publish-card','wallet.init','wallet.invoke'])

class Wire:
    def __init__(self,source:BinaryIO,sink:BinaryIO):
        self.source=source;self.sink=sink
        self.writing=threading.Lock();self.guard=threading.Lock()
        self.pending={};self.commands=queue.Queue(maxsize=32)
        self.closed=threading.Event();self.thread=None
    def write(self,value:dict):
        raw=canonical(value)+b'\n'
        with self.writing:
            if self.closed.is_set():raise Rejected('CORE_LINK_CLOSED')
            self.sink.write(raw);self.sink.flush()
    def start(self):
        if self.thread:raise Rejected('CORE_READER_ALREADY_STARTED')
        self.thread=threading.Thread(target=self._read,name='logos-ipc-reader',daemon=True);self.thread.start()
    def _read(self):
        try:
            while True:
                raw=self.source.readline(65538)
                if not raw:break
                if len(raw)>65536 or not raw.endswith(b'\n'):raise Rejected('INVALID_MESSAGE_FRAME')
                value=parse(raw)
                if isinstance(value,dict) and value.get('kind')=='bridge_response':
                    request_id=value.get('id')
                    with self.guard:target=self.pending.get(request_id) if isinstance(request_id,str) else None
                    if target:
                        try:target.put_nowait(value)
                        except queue.Full:pass
                else:
                    try:self.commands.put_nowait(value)
                    except queue.Full:raise Rejected('SERVICE_QUEUE_FULL')
        except Exception:
            pass
        finally:
            self.closed.set()
            with self.guard:targets=list(self.pending.values())
            for q in targets:
                try:q.put_nowait({'success':False,'error':'CORE_LINK_CLOSED'})
                except queue.Full:pass
    def next_command(self):
        while True:
            try:return self.commands.get(timeout=.2)
            except queue.Empty:
                if self.closed.is_set():return None
    def call(self,action:str,params:dict,timeout=100):
        if action not in ACTIONS or not isinstance(params,dict):raise Rejected('BRIDGE_ACTION_NOT_ALLOWED')
        if not 0<timeout<=(7200 if action=='wallet.invoke' else 120):raise Rejected('INVALID_BRIDGE_TIMEOUT')
        request_id='bridge-'+secrets.token_hex(16);target=queue.Queue(maxsize=1)
        with self.guard:
            if len(self.pending)>=8:raise Rejected('TOO_MANY_BRIDGE_CALLS')
            self.pending[request_id]=target
        try:
            self.write({'kind':'bridge_request','id':request_id,'action':action,'params':params})
            try:response=target.get(timeout=timeout)
            except queue.Empty:raise Rejected('CORE_OPERATION_TIMEOUT') from None
            if response.get('success') is not True:
                # Only native static machine codes, never raw library messages.
                code=response.get('error','CORE_OPERATION_FAILED')
                if not isinstance(code,str) or len(code)>80 or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789' for c in code):code='CORE_OPERATION_FAILED'
                raise Rejected(code)
            if response.get('id')!=request_id:raise Rejected('CORE_REPLY_ID_MISMATCH')
            return response.get('result')
        finally:
            with self.guard:self.pending.pop(request_id,None)

class LogosStorage:
    def __init__(self,wire:Wire):
        self.wire=wire;self.started=False;self.initialize_guard=threading.RLock()
    def initialize(self):
        # Discovery publication and the controller worker can ask for Storage
        # concurrently during startup. Serialise the two-step node bootstrap;
        # calling storage.init twice on the same Logos module can race its
        # LevelDB repository lock and crash the upstream module.
        with self.initialize_guard:
            if self.started:return
            self.wire.call('storage.init',{})
            self.wire.call('storage.start',{})
            self.started=True
    def upload(self,path,operation_id):
        self.initialize()
        result=self.wire.call('storage.upload',{'path':str(path)})
        if not isinstance(result,dict) or not isinstance(result.get('address'),str):raise Rejected('STORAGE_RECEIPT_MISSING')
        return result['address']
    def download(self,address,path,maximum_bytes):
        self.initialize()
        self.wire.call('storage.download',{'address':address,'path':str(path)})
        if path.stat().st_size>maximum_bytes:raise Rejected('STORAGE_DOWNLOAD_LIMIT')
