"""A2A 1.0 data model and detached Ed25519 Agent Card signatures.

The transport binding is Logos Messaging. Data fields and lifecycle states follow
lf.a2a.v1 (A2A release v1.0.1), not the earlier 0.3 wire shape.
"""
from __future__ import annotations
import json
import re
from datetime import datetime,timezone
from .codec import Rejected,b64,unb64,canonical,identifier,amount
from .signing import key_id

PAYMENT_EXTENSION='https://github.com/NotRithik/commons-relay/blob/main/docs/a2a-payment-v1.md'
BINDING_EXTENSION='https://github.com/NotRithik/commons-relay/blob/main/docs/a2a-logos-binding-v1.md'
VERSION='1.0'
STATES={
    'submitted':'TASK_STATE_SUBMITTED','working':'TASK_STATE_WORKING','unknown':'TASK_STATE_WORKING',
    'completed':'TASK_STATE_COMPLETED','failed':'TASK_STATE_FAILED','canceled':'TASK_STATE_CANCELED',
    'input-required':'TASK_STATE_INPUT_REQUIRED','rejected':'TASK_STATE_REJECTED','auth-required':'TASK_STATE_AUTH_REQUIRED'}
TERMINAL={'completed','failed','canceled','rejected'}

def timestamp(seconds):return datetime.fromtimestamp(seconds,timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')

def jcs(value)->bytes:
    """RFC8785-compatible subset: strings, safe integers, booleans and objects.
    Floating point is deliberately not accepted in our Agent Cards. UTF-16 key
    sorting and unescaped Unicode match ECMAScript canonical JSON serialization.
    """
    canonical(value)
    def encode(v):
        if v is None:return 'null'
        if type(v)is bool:return 'true' if v else 'false'
        if type(v)is int:
            if abs(v)>2**53-1:raise Rejected('CARD_INTEGER_NOT_INTEROPERABLE')
            return str(v)
        if isinstance(v,str):return json.dumps(v,ensure_ascii=False,separators=(',',':'))
        if isinstance(v,list):return '['+','.join(encode(x) for x in v)+']'
        if isinstance(v,dict):return '{'+','.join(encode(k)+':'+encode(v[k]) for k in sorted(v,key=lambda k:k.encode('utf-16be')))+'}'
        raise Rejected('CARD_VALUE_NOT_INTEROPERABLE')
    return encode(value).encode('utf8')

def sign_card(card:dict,private,public:bytes,crypto)->dict:
    if 'signatures' in card:raise Rejected('CARD_ALREADY_SIGNED')
    header=b64(jcs({'alg':'EdDSA','kid':key_id(public)}));payload=b64(jcs(card))
    signature=crypto.sign(private,(header+'.'+payload).encode('ascii'))
    return {**card,'signatures':[{'protected':header,'signature':b64(signature)}]}

def verify_card(card:dict,public:bytes,crypto)->dict:
    if not isinstance(card,dict) or not isinstance(card.get('signatures'),list) or len(card['signatures'])!=1:raise Rejected('CARD_SIGNATURE_REQUIRED')
    value={k:v for k,v in card.items() if k!='signatures'};sig=card['signatures'][0]
    if not isinstance(sig,dict) or set(sig)!={'protected','signature'}:raise Rejected('INVALID_CARD_JWS')
    try:header=json.loads(unb64(sig['protected'],1000))
    except Exception:raise Rejected('INVALID_CARD_JWS') from None
    if header!={'alg':'EdDSA','kid':key_id(public)}:raise Rejected('CARD_SIGNER_MISMATCH')
    data=(sig['protected']+'.'+b64(jcs(value))).encode('ascii')
    if not crypto.verify(public,data,unb64(sig['signature'],64)):raise Rejected('CARD_SIGNATURE_INVALID')
    validate_card(value)
    binding=next(e['params'] for e in value['capabilities']['extensions'] if e['uri']==BINDING_EXTENSION)
    if unb64(binding['signingPublicKey'],32)!=public:raise Rejected('CARD_SIGNER_MISMATCH')
    return value

def validate_card(card:dict):
    canonical(card)
    required={'name','description','supportedInterfaces','version','capabilities','defaultInputModes','defaultOutputModes','skills'}
    allowed=required|{'provider','documentationUrl','securitySchemes','securityRequirements','iconUrl','signatures'}
    if not isinstance(card,dict) or not required<=set(card) or set(card)-allowed:raise Rejected('INVALID_A2A_CARD_FIELDS')
    for field in ['name','description','version']:
        if not isinstance(card[field],str) or not 1<=len(card[field])<=2000:raise Rejected('INVALID_A2A_CARD_TEXT')
    interfaces=card['supportedInterfaces']
    if not isinstance(interfaces,list) or not 1<=len(interfaces)<=4:raise Rejected('A2A_INTERFACE_REQUIRED')
    logos=[]
    for item in interfaces:
        if not isinstance(item,dict) or not {'url','protocolBinding','protocolVersion'}<=set(item) or item['protocolVersion']!=VERSION:raise Rejected('A2A_PROTOCOL_VERSION_MISMATCH')
        if not isinstance(item['protocolBinding'],str) or not isinstance(item['url'],str) or not 1<=len(item['url'])<=2000:raise Rejected('INVALID_A2A_INTERFACE')
        if item['protocolBinding']=='LOGOS-MESSAGING':
            if not item['url'].startswith('logos://'):raise Rejected('UNSUPPORTED_A2A_BINDING')
            identifier(item['url'][8:]);logos.append(item['url'][8:])
    if len(logos)!=1:raise Rejected('UNSUPPORTED_A2A_BINDING')
    capabilities=card['capabilities']
    if not isinstance(capabilities,dict):raise Rejected('INVALID_A2A_CAPABILITIES')
    for flag in ('streaming','pushNotifications','extendedAgentCard'):
        if flag in capabilities and type(capabilities[flag])is not bool:raise Rejected('INVALID_A2A_CAPABILITIES')
    def modes(value):
        if not isinstance(value,list) or not 1<=len(value)<=16 or any(not isinstance(m,str) or not 1<=len(m)<=100 for m in value):raise Rejected('INVALID_A2A_MODES')
    for field in ['defaultInputModes','defaultOutputModes']:modes(card[field])
    if not isinstance(card['skills'],list) or len(card['skills'])>100:raise Rejected('INVALID_A2A_SKILLS')
    ids=set()
    for skill in card['skills']:
        if not isinstance(skill,dict) or not {'id','name','description','tags'}<=set(skill):raise Rejected('INVALID_A2A_SKILL')
        name=identifier(skill['id'])
        if name in ids or not isinstance(skill['tags'],list) or len(skill['tags'])>32 or any(not isinstance(tag,str) or len(tag)>100 for tag in skill['tags']):raise Rejected('INVALID_A2A_SKILL')
        for field in ('name','description'):
            if not isinstance(skill[field],str) or not 1<=len(skill[field])<=2000:raise Rejected('INVALID_A2A_SKILL')
        for field in ('inputModes','outputModes'):
            if field in skill:modes(skill[field])
        ids.add(name)
    extensions=capabilities.get('extensions',[])
    if not isinstance(extensions,list) or len(extensions)>16:raise Rejected('INVALID_A2A_EXTENSIONS')
    known={}
    for extension in extensions:
        if not isinstance(extension,dict) or not isinstance(extension.get('uri'),str) or not 1<=len(extension['uri'])<=2000:raise Rejected('INVALID_A2A_EXTENSION')
        if extension['uri'] in known:raise Rejected('DUPLICATE_A2A_EXTENSION')
        if 'required' in extension and type(extension['required'])is not bool:raise Rejected('INVALID_A2A_EXTENSION')
        if 'params' in extension and not isinstance(extension['params'],dict):raise Rejected('INVALID_A2A_EXTENSION')
        known[extension['uri']]=extension.get('params',{})
    binding=known.get(BINDING_EXTENSION)
    if not isinstance(binding,dict) or binding.get('address')!=logos[0]:raise Rejected('CARD_ADDRESS_MISMATCH')
    for name in ('signingPublicKey','encryptionPublicKey'):
        if len(unb64(binding.get(name),32))!=32:raise Rejected('INVALID_PUBLIC_SERVICE_KEYS')
    topic=binding.get('discoveryTopic')
    if not isinstance(topic,str) or not 1<=len(topic)<=128:raise Rejected('INVALID_DISCOVERY_TOPIC')
    root=binding.get('rootNpk')
    if root is not None and (not isinstance(root,str) or not re.fullmatch('[0-9a-f]{64}',root)):raise Rejected('INVALID_CARD_ROOT_HINT')
    payment=known.get(PAYMENT_EXTENSION)
    if payment is not None:
        if payment.get('asset')!='LEZ-testnet' or payment.get('network')!='https://testnet.lez.logos.co/':raise Rejected('PAYMENT_WRONG_NETWORK_OR_ASSET')
        modes=payment.get('paymentModes',['private'])
        if not isinstance(modes,list) or not modes or any(not isinstance(mode,str) or mode not in ('private','public') for mode in modes) or len(set(modes))!=len(modes):raise Rejected('INVALID_PAYMENT_MODES')
        prices=payment.get('prices');inputs=payment.get('inputSchemas');outputs=payment.get('outputSchemas')
        if not isinstance(prices,dict) or set(prices)!=ids or not isinstance(inputs,dict) or set(inputs)!=ids:raise Rejected('INVALID_SERVICE_SCHEMAS')
        if outputs is not None and (not isinstance(outputs,dict) or set(outputs)!=ids):raise Rejected('INVALID_SERVICE_SCHEMAS')
        from .schema import check_public_schema
        for name in ids:
            amount(prices[name]);check_public_schema(inputs[name])
            if outputs is not None:check_public_schema(outputs[name])


def task_document(row:dict,now:int)->dict:
    state=row['state']
    if state not in STATES:raise Rejected('INVALID_A2A_STATE')
    task={'id':row['id'],'contextId':row['context_id'],'status':{'state':STATES[state],'timestamp':timestamp(row['updated'])},
          'metadata':{BINDING_EXTENSION:{'sequence':str(row['sequence'])}}}
    if row.get('quote'):
        quote=json.loads(row['quote']) if isinstance(row['quote'],str) else row['quote']
        task['metadata'][PAYMENT_EXTENSION]={'quote':quote,'paymentState':row.get('payment_state','unpaid')}
        if row.get('payment_hash'):task['metadata'][PAYMENT_EXTENSION]['paymentTransaction']=row['payment_hash']
        if row.get('refund_hash'):task['metadata'][PAYMENT_EXTENSION]['refundTransaction']=row['refund_hash']
        if row.get('refund_proof'):task['metadata'][PAYMENT_EXTENSION]['refundProof']=json.loads(row['refund_proof'])
    if row.get('result') is not None:
        result=json.loads(row['result']) if isinstance(row['result'],str) else row['result']
        task['artifacts']=[{'artifactId':'result','name':row['skill']+' result','parts':[{'data':result,'mediaType':'application/json'}]}]
    if row.get('error'):
        task['status']['message']={'messageId':'status-'+row['id']+'-'+str(row['sequence']),'contextId':row['context_id'],'taskId':row['id'],'role':'ROLE_AGENT','parts':[{'text':row['error']}]}
    return task
