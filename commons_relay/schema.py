"""The strict JSON-schema subset accepted by installed Relay skill descriptors.

Planners get the same schema, but this validation occurs again in the trusted
engine. Unsupported schema keywords fail installation instead of being ignored.
"""
from __future__ import annotations
import re
from .codec import Rejected,canonical

KEYWORDS=frozenset(['type','properties','required','additionalProperties','items','minItems','maxItems',
                    'uniqueItems','minLength','maxLength','pattern','minimum','maximum','enum','description','title'])

def check_schema(schema:dict,depth=0)->None:
    if depth>16 or not isinstance(schema,dict) or set(schema)-KEYWORDS:raise Rejected('UNSUPPORTED_SKILL_SCHEMA')
    for name,maximum in [('title',200),('description',4000)]:
        if name in schema and (not isinstance(schema[name],str) or len(schema[name])>maximum):raise Rejected('INVALID_SCHEMA_ANNOTATION')
    kind=schema.get('type')
    if kind not in [None,'object','array','string','integer','boolean','null']:raise Rejected('UNSUPPORTED_SKILL_SCHEMA_TYPE')
    if 'pattern' in schema:
        if not isinstance(schema['pattern'],str) or len(schema['pattern'])>250:raise Rejected('INVALID_SCHEMA_PATTERN')
        try:re.compile(schema['pattern'])
        except re.error:raise Rejected('INVALID_SCHEMA_PATTERN') from None
    if kind=='object':
        properties=schema.get('properties',{})
        if not isinstance(properties,dict) or len(properties)>128:raise Rejected('INVALID_SCHEMA_PROPERTIES')
        required=schema.get('required',[])
        if not isinstance(required,list) or any(not isinstance(x,str) or x not in properties for x in required) or len(set(required))!=len(required):raise Rejected('INVALID_SCHEMA_REQUIRED')
        if 'additionalProperties' in schema and type(schema['additionalProperties'])is not bool:raise Rejected('INVALID_SCHEMA_ADDITIONAL_PROPERTIES')
        for key,value in properties.items():
            if not isinstance(key,str):raise Rejected('INVALID_SCHEMA_PROPERTY_NAME')
            check_schema(value,depth+1)
    elif kind=='array':
        if 'items' not in schema:raise Rejected('ARRAY_SCHEMA_NEEDS_ITEMS')
        check_schema(schema['items'],depth+1)
    for key in ['minItems','maxItems','minLength','maxLength','minimum','maximum']:
        if key in schema and type(schema[key])is not int:raise Rejected('INVALID_SCHEMA_LIMIT')
    if 'enum' in schema:
        if not isinstance(schema['enum'],list) or not 1<=len(schema['enum'])<=100:raise Rejected('INVALID_SCHEMA_ENUM')
        canonical(schema['enum'])

def validate(value,schema:dict,depth=0)->None:
    if depth>16:raise Rejected('SKILL_ARGUMENTS_TOO_DEEP')
    if 'enum' in schema and all(canonical(value)!=canonical(x) for x in schema['enum']):raise Rejected('SKILL_ENUM_VALUE_REJECTED')
    kind=schema.get('type')
    if kind is None:canonical(value);return
    if kind=='object':
        if not isinstance(value,dict):raise Rejected('SKILL_OBJECT_REQUIRED')
        props=schema.get('properties',{})
        if any(k not in value for k in schema.get('required',[])):raise Rejected('SKILL_ARGUMENT_MISSING')
        if schema.get('additionalProperties',True) is False and set(value)-set(props):raise Rejected('SKILL_EXTRA_ARGUMENT')
        for key,item in value.items():
            if key in props:validate(item,props[key],depth+1)
            else:canonical(item)
    elif kind=='array':
        if not isinstance(value,list):raise Rejected('SKILL_ARRAY_REQUIRED')
        if not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',256):raise Rejected('SKILL_ARRAY_SIZE')
        if schema.get('uniqueItems') and len({canonical(x) for x in value})!=len(value):raise Rejected('SKILL_DUPLICATE_ARRAY_ITEM')
        for item in value:validate(item,schema['items'],depth+1)
    elif kind=='string':
        if not isinstance(value,str):raise Rejected('SKILL_STRING_REQUIRED')
        if not schema.get('minLength',0)<=len(value)<=schema.get('maxLength',32768):raise Rejected('SKILL_STRING_SIZE')
        if 'pattern' in schema and re.search(schema['pattern'],value) is None:raise Rejected('SKILL_STRING_FORMAT')
    elif kind=='integer':
        if type(value)is not int:raise Rejected('SKILL_INTEGER_REQUIRED')
        if value<schema.get('minimum',-(2**128-1)) or value>schema.get('maximum',2**128-1):raise Rejected('SKILL_INTEGER_RANGE')
    elif kind=='boolean':
        if type(value)is not bool:raise Rejected('SKILL_BOOLEAN_REQUIRED')
    elif kind=='null':
        if value is not None:raise Rejected('SKILL_NULL_REQUIRED')


def check_public_schema(schema:dict)->None:
    """Accept the documented schema subset without untrusted regex programs.

    Public providers may use enum and size/range constraints. Patterns are
    restricted to an anchored character class with one repetition, plus the
    decimal-amount pattern used by Relay. Nested repeats, lookarounds and back
    references cannot run in a client's controller from an advertisement.
    """
    check_schema(schema)
    def walk(node):
        pattern=node.get('pattern')
        if pattern is not None:
            simple=re.fullmatch(r'\^\[([A-Za-z0-9_./:@\\-]+)\](?:[+*?]|\{[0-9]{1,5}(?:,[0-9]{0,5})?\})\$',pattern)
            if pattern!='^(0|[1-9][0-9]{0,38})$' and simple is None:
                raise Rejected('UNSUPPORTED_PUBLIC_SCHEMA_PATTERN')
        for lower,upper in [('minLength','maxLength'),('minItems','maxItems'),('minimum','maximum')]:
            if lower in node and upper in node and node[lower]>node[upper]:raise Rejected('INVALID_SCHEMA_LIMIT')
            if lower!='minimum' and any(node.get(k,0)<0 for k in (lower,upper)):raise Rejected('INVALID_SCHEMA_LIMIT')
        if 'uniqueItems' in node and type(node['uniqueItems'])is not bool:raise Rejected('INVALID_SCHEMA_UNIQUENESS')
        if node.get('type')=='object':
            for child in node.get('properties',{}).values():walk(child)
        elif node.get('type')=='array':walk(node['items'])
    walk(schema)
