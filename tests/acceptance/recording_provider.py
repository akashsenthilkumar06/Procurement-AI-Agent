"""Records the exact HTTP JSON produced by the existing provider adapter, without a network call."""
import copy
import io
import json
from unittest.mock import patch
from research_agent.providers import _agent_step_http


class RecordingMockProvider:
    def __init__(self):
        self.records=[];self.script=[];self.label={};self.counter=0

    def arm(self,steps,label=None):
        self.script=copy.deepcopy(steps);self.label=copy.deepcopy(label or {})

    def transport(self,request,**kwargs):
        body=json.loads(request.data)
        self.records.append({'label':copy.deepcopy(self.label),'request':copy.deepcopy(body),'timeout':kwargs.get('timeout')})
        if not self.script:raise AssertionError('Recording mock script exhausted')
        item=self.script.pop(0);self.counter+=1
        output=[]
        for i,call in enumerate(item.get('calls',[])):
            output.append({'type':'function_call','id':f'fc_{self.counter}_{i}','call_id':f'call_{self.counter}_{i}','name':call['name'],'arguments':json.dumps(call['inputs'])})
        if 'final' in item:output.append({'type':'message','id':f'msg_{self.counter}','role':'assistant','status':'completed','content':[{'type':'output_text','text':json.dumps(item['final'])}]})
        result={'id':f'resp_mock_{self.counter}','status':'completed','model':body['model'],'output':output,'usage':{'input_tokens':100,'output_tokens':20}}
        return io.BytesIO(json.dumps(result).encode())

    def __call__(self,config,condition,items,definitions,schema,timeout):
        # Only the killable process boundary is bypassed. Request building/response parsing are real.
        with patch('research_agent.providers.urllib.request.urlopen',side_effect=self.transport):
            return _agent_step_http(config,condition,items,definitions,schema,timeout)


def script_for(pack,answer='Analysis of the permitted data is complete.',action=None):
    return [{'calls':[{'name':step['tool'],'inputs':step['inputs']}]} for step in pack['demo_plan']]+[{'final':{'answer':answer,'action':action}}]
