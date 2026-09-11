"""Bounded CLI transport to an already running local Logos Core session.

This client receives no wallet secrets. It submits signed command envelopes and
polls the corresponding bounded reply slot, rather than racing a subscription.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import time
from .codec import canonical,parse,Rejected

class CoreClient:
    def __init__(self,executable:Path,session:Path,*,timeout=120,environment=None):
        self.executable=executable.resolve(strict=True);self.session=session.resolve(strict=True)
        if not self.executable.is_file() or not os.access(self.executable,os.X_OK):raise Rejected('LOGOSCTL_NOT_EXECUTABLE')
        if not self.session.is_dir() or session.is_symlink():raise Rejected('INVALID_CORE_SESSION')
        if not 1<=timeout<=7200:raise Rejected('INVALID_CORE_TIMEOUT')
        self.timeout=timeout
        self.env={k:v for k,v in (environment or os.environ).items() if k in ['HOME','PATH','TMPDIR','DYLD_LIBRARY_PATH','LD_LIBRARY_PATH','LOGOSCTL_CONFIG_DIR']}
    def invoke(self,method:str,*arguments:str):
        if method not in ['configure','request','reply','runtimeState','moduleProbe']:raise Rejected('CORE_CLIENT_METHOD_DENIED')
        cmd=[str(self.executable),'--config-dir',str(self.session),'--json','call','commons_relay_module',method,*arguments]
        try:
            output=subprocess.run(cmd,cwd=self.session,env=self.env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=25,check=False)
        except subprocess.TimeoutExpired:raise Rejected('CORE_CALL_TIMEOUT') from None
        if len(output.stdout)>131072:raise Rejected('CORE_RESPONSE_TOO_LARGE')
        try:value=json.loads(output.stdout)
        except ValueError:raise Rejected('INVALID_CORE_CLI_RESPONSE') from None
        # The official CLI can exit zero while returning status=error.
        if output.returncode or not isinstance(value,dict) or value.get('status')!='ok' or value.get('module')!='commons_relay_module':
            raise Rejected('CORE_CALL_REJECTED')
        return value.get('result')
    def request(self,method:str,params:dict):
        if not isinstance(method,str) or not isinstance(params,dict):raise Rejected('INVALID_CONTROL_REQUEST')
        request={'method':method,'params':params}
        request_id=self.invoke('request','str:'+canonical(request).decode())
        if isinstance(request_id,str) and request_id in {'METHOD_NOT_ALLOWED','RUNTIME_NOT_CONFIGURED','REQUEST_LIMIT','INVALID_REQUEST_JSON'}:
            raise Rejected(request_id)
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-f0-9-]{36}',request_id):raise Rejected('CORE_REQUEST_NOT_ACCEPTED')
        deadline=time.monotonic()+self.timeout
        while time.monotonic()<deadline:
            raw=self.invoke('reply',request_id)
            try:reply=parse(raw.encode())
            except Exception:raise Rejected('INVALID_CORE_REPLY') from None
            if not isinstance(reply,dict) or reply.get('id')!=request_id:raise Rejected('CORE_RESPONSE_ID_MISMATCH')
            if reply.get('pending')is True:time.sleep(.15);continue
            if reply.get('expired')is True:raise Rejected('CORE_REPLY_EXPIRED')
            if reply.get('success')is not True:
                code=reply.get('error','CORE_REQUEST_FAILED')
                if not isinstance(code,str) or not re.fullmatch('[A-Z_0-9]{1,100}',code):code='CORE_REQUEST_FAILED'
                raise Rejected(code)
            return reply.get('result')
        raise Rejected('CORE_OPERATION_TIMEOUT')
