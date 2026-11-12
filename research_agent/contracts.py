"""Small, fail-closed JSON Schema subset shared by tools and file-based decisions."""
import math


def check(value, schema, path='value'):
    kind=schema.get('type')
    kinds=kind if isinstance(kind,list) else [kind]
    valid={'object':isinstance(value,dict),'array':isinstance(value,list),'string':isinstance(value,str),
           'number':isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value),
           'integer':type(value) is int,'boolean':type(value) is bool,'null':value is None}
    if not any(valid.get(k,False) for k in kinds):raise ValueError(f'{path}: invalid type')
    if 'enum' in schema and value not in schema['enum']:raise ValueError(f'{path}: unknown value')
    if isinstance(value,dict):
        props=schema.get('properties',{})
        if set(schema.get('required',[]))-set(value):raise ValueError(f'{path}: missing fields')
        if schema.get('additionalProperties') is False and set(value)-set(props):raise ValueError(f'{path}: unexpected fields')
        for key,item in value.items():
            if key in props:check(item,props[key],path+'.'+key)
    elif isinstance(value,list):
        if not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',1000):raise ValueError(f'{path}: invalid length')
        for item in value:check(item,schema['items'],path+'[]')
    elif isinstance(value,str):
        if not schema.get('minLength',0)<=len(value)<=schema.get('maxLength',16000):raise ValueError(f'{path}: invalid length')
    elif isinstance(value,(int,float)) and not isinstance(value,bool):
        if not schema.get('minimum',-math.inf)<=value<=schema.get('maximum',math.inf):raise ValueError(f'{path}: outside range')
        if 'multipleOf' in schema and abs(value/schema['multipleOf']-round(value/schema['multipleOf']))>1e-6:raise ValueError(f'{path}: invalid precision')
    return value


def at(value,path):
    for key in path.split('.'):
        value=value[int(key)] if isinstance(value,list) else value[key]
    return value
