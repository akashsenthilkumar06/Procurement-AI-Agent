"""Deterministic, versioned tools. No network, files chosen by the model, or external writes."""
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from .contracts import check
from .repository import open_repository


def obj(properties):return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
def arr(items):return {'type':'array','items':items,'maxItems':10000}
STR={'type':'string','maxLength':100};NUM={'type':'number'}
IDS=arr(STR)


@dataclass(frozen=True)
class Tool:
    name: str
    input_schema: dict
    output_schema: dict
    scope: str
    description: str


REGISTRY={}
def register(name,inputs,outputs,scope,description):
    REGISTRY[name]=Tool(name,obj(inputs),obj(outputs),scope,description)


register('get_supplier_records',{'supplier_ids':IDS},{'records':arr({'type':'object'})},'data:read','Read role-filtered supplier records. Empty IDs reads all permitted records.')
register('get_demand_history',{'sku':STR,'limit':{'type':'integer','minimum':1,'maximum':120}}, {'history':arr(obj({'period':STR,'sku':STR,'units':NUM}))},'data:read','Read historical synthetic demand, ordered by period.')
register('run_forecast',{'sku':STR,'periods':{'type':'integer','minimum':1,'maximum':12},'window':{'type':'integer','minimum':2,'maximum':24},'method':{'type':'string','enum':['moving_average','linear_trend']}},
    {'forecast':arr(obj({'step':{'type':'integer'},'units':NUM})),'method':STR,'history_count':{'type':'integer'}},'analysis:run','Deterministic moving-average or least-squares trend forecast. Units are rounded to two decimals.')
register('assess_supplier_risk',{'supplier_ids':IDS},{'risks':arr(obj({'supplier_id':STR,'risk':NUM})),'lowest_risk_supplier':STR},'analysis:run','Risk = 70% late-delivery rate plus 30% public disruption probability. Lower is better.')
register('compare_suppliers',{'cost_weight':NUM,'reliability_weight':NUM,'lead_time_weight':NUM},
    {'scores':arr(obj({'supplier_id':STR,'score':NUM})),'best_supplier':STR},'analysis:run','Normalize public cost, reliability and lead time across the dataset, then calculate weighted utility.')
register('simulate_scenario',{'scenario':{'type':'string','enum':['disruption','order_policy']},'disruption_days':{'type':'integer','minimum':0,'maximum':120},'order_quantity':{'type':'integer','minimum':0,'maximum':100000}},
    {'policies':arr(obj({'policy':STR,'cost':NUM,'shortage_units':NUM})),'best_policy':STR,'daily_demand':NUM},'simulation:run','Compare wait, expedite and alternate supply policies with deterministic inventory and shortage costs. No real order is made.')

register('evaluate_offer',{'offer':{'type':['number','null']}},{'decision':{'type':'string','enum':['accept','counter','reject']},'amount':{'type':['number','null']}},'analysis:run','Evaluate the current participant offer using the server policy. Use offer=null for the current offer or opening request; only the server chooses counter amounts.')

MODE_SCOPES={mode:{'data:read','analysis:run','simulation:run'} for mode in ('analytical','chatbot','recommender','autonomous')}
MODE_SCOPES['recommender']=MODE_SCOPES['recommender']|{'decision:propose'}
MODE_SCOPES['autonomous']=MODE_SCOPES['autonomous']|{'decision:commit'}


def execute_inline(name,inputs,repo,role,access,pack=None,condition=None):
    if name not in REGISTRY:raise ValueError('Unknown tool')
    check(inputs,REGISTRY[name].input_schema,'tool input')
    def suppliers():
        rows=repo.records('suppliers',role,access)
        return [r for r in rows if not inputs.get('supplier_ids') or r['id'] in inputs['supplier_ids']]
    if name=='evaluate_offer':
        from .offer_policy import evaluate_offer
        if not pack or 'offer_policy' not in pack:raise ValueError('A server offer policy is required')
        result=evaluate_offer(inputs['offer'],pack,condition or {})
    elif name=='get_supplier_records':result={'records':suppliers()}
    elif name in ('get_demand_history','run_forecast'):
        rows=sorted([r for r in repo.records('demand',role,access) if r['sku']==inputs['sku']],key=lambda r:r['period'])
        if not rows:raise ValueError('No demand history for SKU')
        if name=='get_demand_history':result={'history':rows[-inputs['limit']:]}
        else:
            values=[r['units'] for r in rows[-inputs['window']:]]
            if len(values)<inputs['window']:raise ValueError('Insufficient history for forecast window')
            avg=sum(values)/len(values);slope=0
            if inputs['method']=='linear_trend':
                middle=(len(values)-1)/2;slope=sum((i-middle)*(v-avg) for i,v in enumerate(values))/sum((i-middle)**2 for i in range(len(values)))
            result={'method':inputs['method'],'history_count':len(rows),'forecast':[{'step':i,'units':round(max(0,avg+slope*((len(values)-1)/2+i)),2)} for i in range(1,inputs['periods']+1)]}
    elif name=='assess_supplier_risk':
        risks=[{'supplier_id':r['id'],'risk':round(.7*(1-r['reliability']/100)+.3*r['disruption_probability'],8)} for r in suppliers()]
        if not risks:raise ValueError('No suppliers match the requested IDs')
        result={'risks':risks,'lowest_risk_supplier':min(risks,key=lambda r:(r['risk'],r['supplier_id']))['supplier_id']}
    elif name=='compare_suppliers':
        rows=suppliers();weights={k:inputs[k+'_weight'] for k in ('cost','reliability','lead_time')}
        if not rows or min(weights.values())<0 or sum(weights.values())<=0:raise ValueError('Invalid comparison weights or empty records')
        scores=[]
        for row in rows:
            score=0
            for key,weight in weights.items():
                low=min(r[key] for r in rows);high=max(r[key] for r in rows)
                normalized=1 if low==high else (row[key]-low)/(high-low)
                if key!='reliability' and low!=high:normalized=1-normalized
                score+=normalized*weight/sum(weights.values())
            scores.append({'supplier_id':row['id'],'score':round(score,8)})
        result={'scores':scores,'best_supplier':min(scores,key=lambda r:(-r['score'],r['supplier_id']))['supplier_id']}
    elif name=='simulate_scenario':
        demand=repo.records('demand',role,access);inventory=repo.records('inventory',role,access)[0]
        if not demand:raise ValueError('Demand history is unavailable')
        daily=sum(r['units'] for r in sorted(demand,key=lambda r:r['period'])[-3:])/3/30
        days=inputs['disruption_days'] if inputs['scenario']=='disruption' else inventory['lead_days']
        available=inventory['on_hand']+inputs['order_quantity'];policies=[]
        for policy,delay,premium in [('wait',days,0),('expedite',min(days,2),inventory['expedite_fee']),('alternate',min(days,5),inventory['alternate_fee'])]:
            shortage=max(0,daily*delay-available)
            cost=shortage*inventory['shortage_penalty']+premium+inputs['order_quantity']*inventory['unit_cost']+max(0,available-daily*delay)*inventory['holding_cost']
            policies.append({'policy':policy,'cost':round(cost,2),'shortage_units':round(shortage,2)})
        result={'policies':policies,'best_policy':min(policies,key=lambda r:(r['cost'],r['policy']))['policy'],'daily_demand':round(daily,8)}
    else:raise ValueError('Unknown tool')
    check(result,REGISTRY[name].output_schema,'tool output')
    return result


def run_bounded(name,inputs,reference,condition,timeout):
    # Separate process gives a real killable timeout; worker has no capability to mutate study state.
    from .packs import PACK_ROOT
    request={'name':name,'inputs':inputs,'reference':reference,'role':condition['role'],'access':condition['agent']['data_access'],'pack_root':str(PACK_ROOT),'policy_condition':{'role':condition['role'],'private_information':condition.get('private_information',{}),'private_rules':condition.get('private_rules',{})}}
    try:
        completed=subprocess.run([sys.executable,'-m','research_agent.tools'],input=json.dumps(request),text=True,capture_output=True,timeout=timeout,
            cwd=str(__import__('pathlib').Path(__file__).resolve().parent.parent))
    except subprocess.TimeoutExpired:raise TimeoutError('Tool timeout') from None
    if completed.returncode:raise ValueError('Tool execution failed')
    result=json.loads(completed.stdout)
    if 'error' in result:raise ValueError(result['error'])
    check(result,REGISTRY[name].output_schema,'tool output')
    return result


if __name__=='__main__':
    from . import packs
    from pathlib import Path
    try:
        req=json.load(sys.stdin);packs.PACK_ROOT=Path(req['pack_root']);pack,repository=packs.resolve(req['reference'])
        if pack.get('legacy_task')=='negotiation':req['access']={table:[key for key in fields if 'public' in repository.manifest['tables'][table]['fields'][key]] for table,fields in req['access'].items()}
        print(json.dumps(execute_inline(req['name'],req['inputs'],repository,req['role'],req['access'],pack=pack,condition=req.get('policy_condition',{})),allow_nan=False))
    except (ValueError,KeyError,PermissionError,TypeError):print(json.dumps({'error':'Tool inputs or data permissions are invalid'}))
