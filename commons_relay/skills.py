"""Immutable skill registry: pricing/permissions come from trusted code, not a model."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
from .codec import amount, identifier, canonical, Rejected

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
    def validate(self,args:dict)->Quote:
        canonical(args)
        if set(args)!=set(self.keys):raise Rejected('INVALID_SKILL_ARGUMENTS')
        return self.quote(args)

class Registry:
    def __init__(self,skills:list[Skill]):
        self._skills={}
        for skill in skills:
            identifier(skill.name)
            if skill.name in self._skills:raise Rejected('DUPLICATE_SKILL')
            self._skills[skill.name]=skill
    def get(self,name:str)->Skill:
        try:return self._skills[name]
        except (KeyError,TypeError):raise Rejected('UNKNOWN_SKILL') from None
    def describe(self)->list[dict]:
        return [{'id':s.name,'description':s.description,'argument_names':list(s.keys),'public':s.public} for s in self._skills.values()]

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
      ('program.call','Execute an owner-approved program instruction',('program_id','instruction','params','maximum_spend'),program_call),
      ('program.deploy','Deploy a compiled LEZ testnet binary',('path','maximum_spend'),lambda a:Quote('LEZ-testnet',amount(a['maximum_spend']),False)),
      ('agent.card','Read the signed Agent Card',(),zero),
      ('agent.discover','Read registered Agent Cards',('topic',),zero),
      ('agent.task','Request a task from a known peer',('agent_address','skill','params','maximum_spend'),lambda a:Quote('LEZ-testnet',amount(a['maximum_spend']),False)),
      ('agent.subscribe','Resume task status events',('agent_address','task_id','cursor'),zero),
      ('agent.cancel','Request cancellation of a remote task',('agent_address','task_id'),zero),
      ('meta.skills','Read registered skills',(),zero),
      ('meta.status','Read this agent status',(),zero),
    ]
    return Registry([Skill(n,d,k,q,n in ['agent.card','meta.skills']) for n,d,k,q in specs])
