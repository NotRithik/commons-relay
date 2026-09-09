"""Immutable skill registry: pricing/permissions come from trusted code, not a model."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
from .codec import amount, identifier, canonical, Rejected
from .schema import check_schema, validate

@dataclass(frozen=True)
class Quote:
    asset:str
    maximum:int
    autonomous:bool=True
    def __post_init__(self):
        identifier(self.asset)
        amount(str(self.maximum))
        if type(self.maximum) is not int or type(self.autonomous) is not bool:raise Rejected('INVALID_QUOTE')

@dataclass(frozen=True)
class Skill:
    name:str
    description:str
    keys:tuple[str,...]
    quote:Callable[[dict],Quote]
    public:bool=False
    input_schema:dict|None=None
    def validate(self,args:dict)->Quote:
        canonical(args)
        if not isinstance(args,dict) or set(args)!=set(self.keys):raise Rejected('INVALID_SKILL_ARGUMENTS')
        if self.input_schema:validate(args,self.input_schema)
        return self.quote(args)

class Registry:
    def __init__(self,skills:list[Skill]):
        self._skills={}
        for skill in skills:
            identifier(skill.name)
            if skill.name in self._skills:raise Rejected('DUPLICATE_SKILL')
            if skill.input_schema:check_schema(skill.input_schema)
            self._skills[skill.name]=skill
    def get(self,name:str)->Skill:
        try:return self._skills[name]
        except (KeyError,TypeError):raise Rejected('UNKNOWN_SKILL') from None
    def describe(self)->list[dict]:
        return [{'id':s.name,'description':s.description,'argument_names':list(s.keys),'public':s.public,'input_schema':s.input_schema or {'type':'object','properties':{k:{} for k in s.keys},'required':list(s.keys),'additionalProperties':False}} for s in self._skills.values()]
    def extended(self,skills:list[Skill])->"Registry":
        # Return a new registry rather than mutating the authority table that an
        # already-running engine may be using.
        return Registry([*self._skills.values(),*skills])

def zero(_):return Quote('LEZ-testnet',0)

def transfer(args):
    identifier(args['recipient'])
    return Quote('LEZ-testnet',amount(args['amount'],positive=True))

def program_call(args):
    identifier(args['program_id']);identifier(args['instruction']);canonical(args['params'])
    # Program effects are arbitrary. An owner-approved maximum and a trusted
    # adapter-enforced balance delta are required; never auto-send from a guess.
    return Quote('LEZ-testnet',amount(args['maximum_spend']),False)

def default_registry()->Registry:
    specs=[
      ('storage.upload','Encrypt and store a file',('path','label'),zero),
      ('storage.download','Retrieve and decrypt a stored file',('address','path'),zero),
      ('storage.list','List owned file references',(),zero),
      ('storage.share','Share a file key with a configured identity',('address','recipient'),zero),
      ('messaging.send','Send an encrypted Logos message',('recipient','message'),zero),
      ('messaging.join','Join a configured Logos group',('group_id',),zero),
      ('messaging.create_group','Create a Logos group',('members',),zero),
      ('wallet.balance','Read the agent wallet balance',(),zero),
      ('wallet.send','Transfer testnet tokens within owner policy',('recipient','amount'),transfer),
      ('wallet.history','Read the agent transaction history',(),zero),
      ('program.query','Read a program state',('program_id','params'),zero),
      ('program.call','Execute an owner-approved public LEZ program instruction',('program_id','instruction','params'),lambda a:Quote('LEZ-testnet',0,False)),
      ('program.deploy','Deploy a compiled LEZ testnet binary',('binary_path',),lambda a:Quote('LEZ-testnet',0,False)),
      ('agent.card','Read the signed Agent Card',(),zero),
      ('agent.discover','Read registered Agent Cards',('topic',),zero),
      ('agent.task','Request a task from a known peer at its signed advertised price',('agent_address','skill','params'),zero),
      ('agent.subscribe','Resume task status events',('agent_address','task_id'),zero),
      ('agent.cancel','Request cancellation of a remote task',('agent_address','task_id'),zero),
      ('meta.skills','Read registered skills',(),zero),
      ('meta.status','Read this agent status',(),zero),
      ('meta.configure','Apply an owner-signed runtime configuration',('key','value'),zero),
    ]
    # This data is exported to planners, cards and UI forms. The trusted Python
    # validators and adapters still enforce the operation before side effects.
    definitions=[]
    for name,description,keys,quote in specs:
        properties={}
        for key in keys:
            if key in ['amount','maximum_spend']:
                field={'type':'string','pattern':'^(0|[1-9][0-9]{0,38})$','maxLength':39,'description':'Integer token base units as a decimal string.'}
            elif key=='members':
                field={'type':'array','items':{'type':'string','minLength':1,'maxLength':160},'minItems':1,'maxItems':32,'uniqueItems':True}
            elif key=='params':field={'type':'object'}
            elif key=='value':field={}
            elif key=='cursor':field={'type':'integer','minimum':0,'maximum':2147483647}
            elif key=='message':field={'type':'string','minLength':1,'maxLength':16000}
            elif key=='instruction':field={'type':'string','pattern':'^[0-9a-f]*$','maxLength':131072,'description':'Hex-encoded little-endian u32 instruction words from the target program ABI.'}
            elif key in ['path','binary_path']:field={'type':'string','minLength':1,'maxLength':1000,'description':'Relative to the explicitly configured file root.'}
            elif key=='label':field={'type':'string','minLength':1,'maxLength':200}
            else:field={'type':'string','minLength':1,'maxLength':180}
            properties[key]=field
        if name=='program.query':
            properties['program_id']={'type':'string','pattern':'^[0-9a-f]{64}$','minLength':64,'maxLength':64,
                'description':'Actual LEZ program image ID in lowercase hex, never an operation name or placeholder.'}
            properties['params']={'type':'object','properties':{'account':{'type':'string','pattern':'^[0-9a-f]{64}$','minLength':64,'maxLength':64}},
                'required':['account'],'additionalProperties':False}
        schema={'type':'object','properties':properties,'required':list(keys),'additionalProperties':False}
        definitions.append(Skill(name,description,keys,quote,name in ['agent.card','meta.skills'],schema))
    return Registry(definitions)
