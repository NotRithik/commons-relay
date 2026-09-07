"""Bounded deterministic messages for this binding; not a claim of full RFC8785."""
from __future__ import annotations
import base64
import hashlib
import json
import re
from typing import Any

MAX_MESSAGE = 65536
MAX_AMOUNT = 2**128-1

class Rejected(ValueError):
    """Static error code only; never include untrusted payloads or secrets."""

def amount(value: Any, *, positive: bool = False) -> int:
    if not isinstance(value,str) or not re.fullmatch(r'0|[1-9][0-9]{0,38}',value):
        raise Rejected('INVALID_BASE_UNIT_AMOUNT')
    n=int(value)
    if n>MAX_AMOUNT or (positive and n==0):raise Rejected('AMOUNT_OUT_OF_RANGE')
    return n

def identifier(value: Any) -> str:
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.:@/-]{1,160}',value):
        raise Rejected('INVALID_IDENTIFIER')
    return value

def check_shape(value: Any, depth: int = 0) -> None:
    if depth>16:raise Rejected('MESSAGE_TOO_DEEP')
    if value is None or type(value) in (bool,int):
        if type(value) is int and abs(value)>MAX_AMOUNT:raise Rejected('INTEGER_OUT_OF_RANGE')
        return
    if isinstance(value,str):
        if len(value)>32768 or any(0xD800<=ord(c)<=0xDFFF for c in value):raise Rejected('INVALID_STRING')
        return
    if isinstance(value,list):
        if len(value)>256:raise Rejected('TOO_MANY_ITEMS')
        for item in value:check_shape(item,depth+1)
        return
    if isinstance(value,dict):
        if len(value)>128 or any(not isinstance(k,str) for k in value):raise Rejected('INVALID_OBJECT')
        for k,v in value.items():check_shape(k,depth+1);check_shape(v,depth+1)
        return
    raise Rejected('UNSUPPORTED_JSON_TYPE')

def canonical(value: Any) -> bytes:
    check_shape(value)
    data=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
    if len(data)>MAX_MESSAGE:raise Rejected('MESSAGE_TOO_LARGE')
    return data

def parse(data: bytes) -> Any:
    if not isinstance(data,bytes) or len(data)>MAX_MESSAGE:raise Rejected('MESSAGE_TOO_LARGE')
    def pairs(values):
        d={}
        for k,v in values:
            if k in d:raise Rejected('DUPLICATE_JSON_KEY')
            d[k]=v
        return d
    try:
        result=json.loads(data.decode('utf-8'),object_pairs_hook=pairs,
                          parse_constant=lambda _:(_ for _ in ()).throw(Rejected('NONFINITE_JSON')))
        check_shape(result);return result
    except (UnicodeError,json.JSONDecodeError,RecursionError) as exc:
        raise Rejected('INVALID_JSON') from None

def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()

def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip('=')

def unb64(text: str, maximum: int = MAX_MESSAGE) -> bytes:
    if not isinstance(text,str) or len(text)>maximum*2 or not re.fullmatch(r'[A-Za-z0-9_-]*',text):
        raise Rejected('INVALID_BASE64URL')
    try:data=base64.b64decode(text+'='*((-len(text))%4),altchars=b'-_',validate=True)
    except ValueError:raise Rejected('INVALID_BASE64URL') from None
    if len(data)>maximum or b64(data)!=text:raise Rejected('NONCANONICAL_BASE64URL')
    return data
