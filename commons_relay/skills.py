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
    output_schema:dict|None=None
    optional_keys:tuple[str,...]=()
    def validate(self,args:dict)->Quote:
        canonical(args)
        if not isinstance(args,dict) or not set(self.keys)-set(self.optional_keys)<=set(args)<=set(self.keys):raise Rejected('INVALID_SKILL_ARGUMENTS')
        if self.input_schema:validate(args,self.input_schema)
        return self.quote(args)

class Registry:
    def __init__(self,skills:list[Skill]):
        self._skills={}
        for skill in skills:
            identifier(skill.name)
            if skill.name in self._skills:raise Rejected('DUPLICATE_SKILL')
            if skill.input_schema:check_schema(skill.input_schema)
            if skill.output_schema:check_schema(skill.output_schema)
            self._skills[skill.name]=skill
    def get(self,name:str)->Skill:
        try:return self._skills[name]
        except (KeyError,TypeError):raise Rejected('UNKNOWN_SKILL') from None
    def describe(self)->list[dict]:
        rows=[{'id':s.name,'description':s.description,'argument_names':list(s.keys),'public':s.public,'input_schema':s.input_schema or {'type':'object','properties':{k:{} for k in s.keys},'required':list(s.keys),'additionalProperties':False},'output_schema':s.output_schema or {'type':'object'}} for s in self._skills.values()]
        locations={'storage.upload':('path','inputs'),'storage.download':('path','outputs'),'program.deploy':('binary_path','inputs')}
        for row in rows:
            if row['id'] not in locations:continue
            key,folder=locations[row['id']]
            schema=row['input_schema'];properties=dict(schema.get('properties',{}))
            if key not in properties:continue
            properties[key]={**properties[key], 'title':'File name inside '+folder,
                'description':'Relative to the agent '+folder+' folder. Use filename.txt, not '+folder+'/filename.txt. Do not use an absolute path. Existing output files are never overwritten.'}
            row['input_schema']={**schema,'properties':properties}
        return rows
    def extended(self,skills:list[Skill])->"Registry":
        # Return a new registry rather than mutating the authority table that an
        # already-running engine may be using.
        return Registry([*self._skills.values(),*skills])

def zero(_):return Quote('LEZ-testnet',0)

def transfer(args):
    identifier(args['recipient'])
    return Quote('LEZ-testnet',amount(args['amount'],positive=True),args.get('payment_mode','private')=='private')

def program_call(args):
    identifier(args['program_id']);identifier(args['instruction']);canonical(args['params'])
    # Program effects are arbitrary. An owner-approved maximum and a trusted
    # adapter-enforced balance delta are required; never auto-send from a guess.
    return Quote('LEZ-testnet',amount(args['maximum_spend']),False)

def default_registry()->Registry:
    specs=[
      ('storage.upload','Encrypt and store a file',('path','label'),zero),
      ('storage.download','Download and decrypt a stored file',('address','path'),zero),
      ('storage.list','List files this agent has stored',(),zero),
      ('storage.share','Share a stored file with someone',('address','recipient'),zero),
      ('messaging.send','Send an encrypted Logos message',('recipient','message'),zero),
      ('messaging.inbox','View recent messages from other agents',(),zero),
      ('messaging.join','Join a Logos group',('group_id',),zero),
      ('messaging.create_group','Create a Logos group',('members',),zero),
      ('wallet.balance','Check this agent’s balance',(),zero),
      ('wallet.send','Send testnet tokens within your limits',('recipient','amount'),transfer),
      ('wallet.history','View recent wallet activity',(),zero),
      ('wallet.public_account','View the public receiving address and public balance',(),zero),
      ('wallet.initialize_public','Enable a public receiving account with owner approval',(),lambda a:Quote('LEZ-testnet',0,False)),
      ('program.query','Read a program’s current on-chain state',('program_id','params'),zero),
      ('program.call','Run an approved action on a LEZ program',('program_id','instruction','params'),lambda a:Quote('LEZ-testnet',0,False)),
      ('program.deploy','Publish a compiled program to LEZ testnet',('binary_path',),lambda a:Quote('LEZ-testnet',0,False)),
      ('agent.card','View this agent’s public capabilities',(),zero),
      ('agent.discover','Find other agents on a named topic',('topic',),zero),
      ('agent.ping','Check whether another agent is online',('agent_address',),zero),
      ('agent.task','Ask another agent to do a task at its listed price',('agent_address','skill','params'),zero),
      ('agent.subscribe','Follow another agent’s task',('agent_address','task_id'),zero),
      ('agent.cancel','Ask another agent to stop a task',('agent_address','task_id'),zero),
      ('meta.skills','View this agent’s tools',(),zero),
      ('meta.status','View this agent’s current status',(),zero),
      ('meta.configure','Change an agent setting with owner approval',('key','value'),zero),
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
            elif key=='value':field={'type':'string','minLength':1,'maxLength':200,'description':'The new setting value.'}
            elif key=='key':field={'type':'string','enum':['name','owner_address','spending_limit','period_limit','hard_maximum','approval_ttl'],'description':'Which setting to change.'}
            elif key=='task_id':field={'type':'string','minLength':1,'maxLength':180,'description':'The other agent’s task, or the task you started here.'}
            elif key=='cursor':field={'type':'integer','minimum':0,'maximum':2147483647}
            elif key=='message':field={'type':'string','minLength':1,'maxLength':16000}
            elif key=='instruction':field={'type':'string','pattern':'^[0-9a-f]*$','maxLength':131072,'description':'Hex-encoded little-endian u32 instruction words from the target program ABI.'}
            elif key in ['path','binary_path']:field={'type':'string','minLength':1,'maxLength':1000,'description':'Relative to the explicitly configured file root.'}
            elif key=='label':field={'type':'string','minLength':1,'maxLength':200}
            else:field={'type':'string','minLength':1,'maxLength':180}
            properties[key]=field
        if name=='storage.upload':
            properties['path']['description']="File name relative to this agent computer's inputs folder, for example note.txt or documents/note.txt. Do not prefix inputs/ and do not supply a laptop path unless the agent runs there."
        elif name=='storage.download':
            properties['path']['description']="New file name relative to this agent computer's outputs folder, for example restored-note.txt. Do not prefix outputs/. Existing files are not overwritten."
        if name=='program.query':
            properties['program_id']={'type':'string','pattern':'^[0-9a-f]{64}$','minLength':64,'maxLength':64,
                'description':'Actual LEZ program image ID in lowercase hex, never an operation name or placeholder.'}
            properties['params']={'type':'object','properties':{'account':{'type':'string','pattern':'^[0-9a-f]{64}$','minLength':64,'maxLength':64}},
                'required':['account'],'additionalProperties':False}
        schema={'type':'object','properties':properties,'required':list(keys),'additionalProperties':False}
        optional=()
        if name in ('wallet.send','agent.task'):
            optional=('payment_mode',);keys=(*keys,'payment_mode')
            properties['payment_mode']={'type':'string','enum':['private','public'],
                'description':'Private is the default. Public avoids private proving but exposes sender, receiver and amount on-chain. Public mode needs an exact owner review.'}
        definitions.append(Skill(name,description,keys,quote,name in ['agent.card','meta.skills'],schema,optional_keys=optional))
    return Registry(definitions)
