"""File-loaded case studies, pinned by content hash in each saved study configuration."""
import copy
import hashlib
import json
from pathlib import Path
from .repository import open_repository, SLUG
from .contracts import check, at

PACK_ROOT=Path(__file__).resolve().parent.parent/'case_studies'


def load_pack(pack_id,version):
    if not isinstance(pack_id,str) or not isinstance(version,str) or not SLUG.fullmatch(pack_id) or not SLUG.fullmatch(version):raise ValueError('Invalid case-study identifier')
    raw=(PACK_ROOT/pack_id/version/'pack.json').read_bytes();pack=json.loads(raw)
    if pack['id']!=pack_id or pack['version']!=version:raise ValueError('Case-study identity mismatch')
    for key in ('title','scenario','dataset','allowed_tools','default_role','private_information','decision','scoring','demo_plan','data_access'):
        if key not in pack:raise ValueError('Missing pack field: '+key)
    schema=pack['decision']['schema']
    if schema.get('type')!='object' or schema.get('additionalProperties') is not False or set(schema['required'])!=set(schema['properties']):raise ValueError('Decision schema must be a closed object with required fields')
    if not 1<=len(schema['properties'])<=12:raise ValueError('Use 1–12 decision fields')
    for key,field in schema['properties'].items():
        if not SLUG.fullmatch(key) or field['type'] not in ('string','number','integer'):raise ValueError('Decision fields must be flat strings or numbers')
    if pack['scoring']['kind'] not in (('ozer_payoffs',) if pack_id=='ozer_game' else ('lookup','target','range')):raise ValueError('Unknown scoring template')
    if pack['scoring']['action_field'] not in schema['properties']:raise ValueError('Scoring field is missing')
    from .tools import REGISTRY
    for name in pack['allowed_tools']:
        if name not in REGISTRY:raise ValueError('Unknown tool in pack')
    for step in pack['demo_plan']:
        if step['tool'] not in pack['allowed_tools']:raise ValueError('Demo plan uses a disallowed tool')
        check(step['inputs'],REGISTRY[step['tool']].input_schema,'demo tool input')
    pack['_hash']=hashlib.sha256(raw).hexdigest()
    return pack


def resolve(reference):
    pack=load_pack(reference['id'],reference['version'])
    repo=open_repository(pack['dataset']['id'],pack['dataset']['version'])
    if reference.get('dataset_version') and reference['dataset_version']!=repo.version:raise ValueError('Dataset version mismatch')
    if reference.get('pack_hash') and reference['pack_hash']!=pack['_hash']:raise ValueError('Case-study files changed; publish a new pack version')
    if reference.get('dataset_hash') and reference['dataset_hash']!=repo.digest:raise ValueError('Dataset files changed; publish a new dataset version')
    return pack,repo


def pin(reference):
    pack,repo=resolve(reference)
    return {'id':pack['id'],'version':pack['version'],'pack_hash':pack['_hash'],'dataset_version':repo.version,'dataset_hash':repo.digest}


def catalog():
    packs=[load_pack(path.parent.parent.name,path.parent.name) for path in sorted(PACK_ROOT.glob('*/*/pack.json'))]
    superseded={(p['id'],v) for p in packs for v in p.get('supersedes',[])}
    return [p for p in packs if (p['id'],p['version']) not in superseded]


def study_config(pack_id,version):
    from .config import DEFAULT,validate
    pack=load_pack(pack_id,version);config=copy.deepcopy(DEFAULT)
    config.update(name=pack['title'],description=pack['scenario']['instructions'],case_study=pin({'id':pack_id,'version':version}))
    config['task']=pack.get('legacy_task','supplier_selection')
    if pack_id == 'ozer_game':
        from .ozer import configure
        configure(config, pack)
        return validate(config)
    conditions=[]
    for mode in ('analytical','chatbot','recommender','autonomous','human_only'):
        cond=copy.deepcopy(DEFAULT['conditions'][0])
        cond.update(id=mode,label=mode.replace('_',' ').title(),role=pack['default_role'],authority='chat' if mode in ('analytical','chatbot') else 'recommendation',
            agent_enabled=mode!='human_only',private_information=pack['private_information'],objective=pack['scenario']['instructions'])
        if mode=='analytical':cond['output']='number'
        cond['agent']={'mode':'analytical' if mode=='human_only' else mode,'allowed_tools':pack['allowed_tools'],
            'data_access':pack['data_access'],'max_tool_calls':8,'tool_timeout_seconds':3,'turn_timeout_seconds':30}
        conditions.append(cond)
    config['conditions']=conditions
    if pack.get('negotiation'):config['negotiation'].update(pack['negotiation'])
    return validate(config)


def demo_config(demo_id, use_openai):
    from .config import DEFAULT, PINNED_MODEL, live_supplier_preview, validate

    if demo_id == "supplier_selection":
        config = copy.deepcopy(DEFAULT)
        condition = config["conditions"][0]
        condition["label"] = "Supplier advisor"
        config.update(name="Supplier advisor", description="Compare suppliers using your cost and delivery priorities.",
                      conditions=[condition])
        if use_openai:
            config["architecture"]["session_spend_usd"] = 0.10
            return live_supplier_preview(config, condition["id"])
        return validate(config)
    if demo_id != "demand_forecasting":
        raise ValueError("Choose an available demo agent.")
    config = study_config(demo_id, "v1")
    condition = next(group for group in config["conditions"] if group["id"] == "analytical")
    condition.update(label="Demand forecaster", prompt=(
        "Call run_forecast for component_a with periods=1, window=3, and method=moving_average. "
        "Return the resulting forecast in units as a single number."
    ))
    config.update(name=condition["label"], conditions=[condition])
    if use_openai:
        config["architecture"].update(provider="openai", model=PINNED_MODEL, temperature=0, session_spend_usd=0.10)
    return validate(config)


def public_pack(config,condition):
    pack,repo=resolve(config['case_study'])
    return {'id':pack['id'],'version':pack['version'],'title':pack['title'],'dataset_version':repo.version,
        **({'game':pack['game']} if pack['id']=='ozer_game' else {}),
        'scenario':pack['scenario'],'decision':pack['decision'], 'mode':condition['agent']['mode'],
        'tables':{name:repo.records(name,'participant',pack.get('participant_access',{})) for name in pack.get('participant_access',{})}}


def score_action(config,condition,action,execute=None):
    from .tools import execute_inline
    pack,repo=resolve(config['case_study']);check(action,pack['decision']['schema'],'decision')
    spec=pack['scoring'];value=action[spec['action_field']]
    if spec['kind']=='range':
        result=(value-spec['min'])/(spec['max']-spec['min'])
        if spec['direction']=='min':result=1-result
        return round(result,8),{'metric':spec['metric'],'value':value}
    result=execute(spec['tool'],spec['inputs']) if execute else execute_inline(spec['tool'],spec['inputs'],repo,condition['role'],condition['agent']['data_access'])
    if spec['kind']=='target':
        target=at(result,spec['target']);error=abs(value-target)
        return round(1/(1+error),8),{'metric':spec['metric'],'absolute_error':error,'reference':target}
    rows=at(result,spec['rows']);row=next((r for r in rows if r[spec['key']]==value),None)
    if row is None:raise ValueError('Decision does not identify an eligible outcome')
    metric=row[spec['value']];values=[r[spec['value']] for r in rows];low,high=min(values),max(values)
    score=1 if low==high else (metric-low)/(high-low)
    if spec['direction']=='min' and low!=high:score=1-score
    return round(score,8),{'metric':spec['metric'],'value':metric}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Validate file-based case studies and deterministic smoke responses')
    parser.add_argument('--id');parser.add_argument('--version',default='v1');args=parser.parse_args()
    selected=[load_pack(args.id,args.version)] if args.id else catalog()
    from .agent import run_agent
    for candidate in selected:
        if candidate['id']=='ozer_game':
            config=study_config(candidate['id'],candidate['version'])
            print(candidate['id']+'@'+candidate['version']+' validated; dataset '+config['case_study']['dataset_version'])
            continue
        config=study_config(candidate['id'],candidate['version']);condition=next(c for c in config['conditions'] if c['id']=='recommender')
        response=run_agent(config,condition,[],'Validate pack');score_action(config,condition,response['decision_action'])
        print(candidate['id']+'@'+candidate['version']+' validated; dataset '+config['case_study']['dataset_version'])
