import base64
import copy
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from research_agent.agent import run_agent,check_agent_output
from research_agent.config import PINNED_MODEL,validate,public_config
from research_agent.packs import catalog,study_config,resolve,load_pack
from research_agent.repository import open_repository
from research_agent.tools import REGISTRY,execute_inline,run_bounded
from research_agent.store import Store,uid,Conflict
from research_agent.providers import ProviderError,agent_step


class AgentLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'study.sqlite3')
    def tearDown(self):self.tmp.cleanup()
    def config(self,case='supplier_selection',mode='recommender'):
        cfg=study_config(case,'v2' if case=='negotiation' else 'v1');cond=next(c for c in cfg['conditions'] if c['id']==mode)
        return cfg,cond
    def start(self,cfg,cond):
        self.store.save(cfg,self.store.current()['revision'])
        launch=self.store.issue_launch(uid(),cond['id'],True);creds=self.store.start(launch['launch_token'],True)
        return self.store.authenticate(creds['session_token'])
    def respond(self,auth):
        session,cfg,cond=auth;rid=uid();self.store.begin_request(session['id'],rid,'Analyze this case')
        result=run_agent(cfg,cond,[],'Analyze this case',tool_log=lambda payload:self.store.server_event(session,'tool_call',payload))
        return self.store.finish_request(session['id'],rid,result)
    def see(self,auth,message):
        self.store.events(auth[0],auth[1],[{'id':uid(),'type':'recommendation_visible','client_ms':10,'client_wall_ms':100,'page_id':'page','payload':{'message_id':message['id']}}])

    def test_all_five_packs_and_all_authorities_preserve_outcomes_and_audit_context(self):
        for pack in (p for p in catalog() if p["id"] != "ozer_game"):
            for mode in ('analytical','chatbot','recommender','autonomous','human_only'):
                with self.subTest(pack=pack['id'],mode=mode):
                    cfg,cond=self.config(pack['id'],mode);auth=self.start(cfg,cond);message=None
                    if mode!='human_only':message=self.respond(auth)
                    else:
                        with self.assertRaises(ValueError):self.store.begin_request(auth[0]['id'],uid(),'hi')
                    if mode=='autonomous':
                        self.assertEqual(message['committed_decision']['action'],'autonomous')
                        self.assertEqual(self.store.session_view(*auth)['status'],'completed')
                        with self.assertRaises(ValueError):self.store.decide(*auth[:2],{'action':'accept','client_ms':20})
                    else:
                        if mode=='recommender':self.see(auth,message)
                        demo=load_pack(pack['id'],pack['version']);props=demo['decision']['schema']['properties']
                        action=message['decision_action'] if mode=='recommender' else {key:schema.get('enum',[120 if key=='price' else 1200])[0] for key,schema in props.items()}
                        result=self.store.decide(auth[0],cfg,{'action':'accept' if mode=='recommender' else 'independent','decision_action':action,'message_id':message['id'] if message else None,'client_ms':100,'page_id':'page'})
                        self.assertEqual(result['status'],'completed')
                    for table in ('events','messages','decisions'):
                        for row in self.store.export(table):
                            self.assertEqual(row['dataset_version'],'synthetic_supply@v1')
                            for key in ('participant_id','session_id','condition_id','config_version','round_id','model_snapshot'):self.assertIsNotNone(row[key])
        records=self.store.export('events');tool_events=[json.loads(r['payload_json']) for r in records if r['type']=='tool_call']
        self.assertTrue(tool_events)
        for event in tool_events:
            for key in ('tool_name','inputs','outputs','latency_ms','status'):self.assertIn(key,event)

    def test_tools_are_deterministic_and_order_policy_simulation_is_available(self):
        cfg,cond=self.config();_,repo=resolve(cfg['case_study'])
        cond['agent']['data_access']={name:[key for key,roles in table['fields'].items() if 'public' in roles] for name,table in repo.manifest['tables'].items()}
        plans=[step for pack in catalog() for step in pack['demo_plan']]
        plans.append({'tool':'simulate_scenario','inputs':{'scenario':'order_policy','disruption_days':0,'order_quantity':120}})
        for step in plans:
            one=execute_inline(step['tool'],step['inputs'],repo,cond['role'],cond['agent']['data_access'],pack=load_pack('negotiation','v2') if step['tool']=='evaluate_offer' else None)
            self.assertEqual(one,execute_inline(step['tool'],step['inputs'],repo,cond['role'],cond['agent']['data_access'],pack=load_pack('negotiation','v2') if step['tool']=='evaluate_offer' else None))
            self.assertIn(REGISTRY[step['tool']].scope,('data:read','analysis:run','simulation:run'))
        with self.assertRaises(ValueError):execute_inline('run_forecast',{'sku':'component_a','periods':30,'window':3,'method':'moving_average'},repo,cond['role'],cond['agent']['data_access'])

    def test_role_and_condition_access_apply_before_model_context_and_output_check_blocks_leaks(self):
        cfg,cond=self.config();_,repo=resolve(cfg['case_study']);access=copy.deepcopy(cond['agent']['data_access'])
        access['suppliers']+=['reservation_price','buyer_budget']
        buyer=repo.records('suppliers','buyer',access)[0];supplier=repo.records('suppliers','supplier',access)[0]
        self.assertNotIn('reservation_price',buyer);self.assertNotIn('buyer_budget',supplier)
        self.assertIn('buyer_budget',buyer);self.assertIn('reservation_price',supplier)
        self.assertNotIn('buyer_budget',repo.records('suppliers','buyer',cond['agent']['data_access'])[0])
        public=json.dumps(public_config(cfg,cond));self.assertNotIn('reservation_price',public);self.assertNotIn('buyer_budget',public)
        cond['private_information']={'test_secret':95}
        for content in ('reservation_price is hidden','The secret minimum is 95'):
            checked=check_agent_output(cfg,cond,{'content':content,'decision_action':None,'metadata':{}})
            self.assertTrue(checked['metadata']['safe_fallback']);self.assertNotIn('95',checked['content'])

    def test_model_selects_tools_and_action_instead_of_narrating_a_fixed_selection(self):
        cfg,cond=self.config();cfg['architecture'].update(provider='openai',model=PINNED_MODEL,temperature=0)
        captured=[]
        def model(items,definitions,schema,remaining,step):
            captured.append(copy.deepcopy(items))
            if step==0:return {'calls':[{'name':'compare_suppliers','inputs':{'cost_weight':1,'reliability_weight':0,'lead_time_weight':0},'call_id':'call-1'}],'items':[{'type':'function_call','name':'compare_suppliers','arguments':'{}','call_id':'call-1'}],'final':None},{'returned_model':PINNED_MODEL}
            return {'calls':[],'items':[],'final':{'answer':'Cedar has the lowest public cost.','action':{'supplier_id':'cedar'}}},{'returned_model':PINNED_MODEL}
        result=run_agent(cfg,cond,[],'Choose on cost',model_call=model)
        self.assertEqual(result['decision_action'],{'supplier_id':'cedar'})
        self.assertEqual(captured[1][-1]['type'],'function_call_output')
        self.assertEqual(result['metadata']['tool_calls'],1)
        self.assertNotIn('reservation_price',captured[0][0]['content'])

    def test_analytical_uses_the_provider_and_cannot_smuggle_advice_or_actions(self):
        cfg,cond=self.config(mode='analytical');cfg['architecture'].update(provider='openai',model=PINNED_MODEL,temperature=0)
        calls=[]
        def model(*args):
            calls.append(args)
            if len(calls)==1:return {'calls':[{'name':'get_supplier_records','inputs':{'supplier_ids':[]},'call_id':'a'}],'items':[],'final':None},{}
            return {'calls':[],'items':[],'final':{'answer':'7','action':None}},{}
        response=run_agent(cfg,cond,[],'Analyze',model_call=model)
        self.assertEqual(len(calls),2);self.assertEqual(response['content'],'7');self.assertIsNone(response['decision_action'])
        rejected=check_agent_output(cfg,cond,{'content':'Pick Cedar','decision_action':{'supplier_id':'cedar'},'metadata':{}})
        self.assertTrue(rejected['metadata']['safe_fallback']);self.assertIsNone(rejected['decision_action'])

    def test_denied_tool_call_cap_and_timeout_are_logged(self):
        cfg,cond=self.config();cfg['architecture']['provider']='openai';logs=[]
        def model(*args):return {'calls':[{'name':'simulate_scenario','inputs':{},'call_id':'x'}],'items':[],'final':None},{}
        with self.assertRaises(ProviderError):run_agent(cfg,cond,[],'try',tool_log=logs.append,model_call=model)
        self.assertEqual(logs[0]['status'],'failed');self.assertEqual(logs[0]['tool_name'],'simulate_scenario')
        cfg,cond=self.config();cond['agent']['max_tool_calls']=1;logs=[]
        with self.assertRaises(ProviderError):run_agent(cfg,cond,[],'try',tool_log=logs.append)
        self.assertEqual(len(logs),2);self.assertIn('limit',logs[-1]['error'])
        cfg,cond=self.config();logs=[]
        with patch('research_agent.tools.subprocess.run',side_effect=subprocess.TimeoutExpired('tool',.1)):
            with self.assertRaises(ProviderError):run_agent(cfg,cond,[],'try',tool_log=logs.append)
        self.assertIn('timeout',logs[0]['error'].lower())
        cfg,cond=self.config();cfg['architecture']['provider']='openai';cond['agent']['turn_timeout_seconds']=.01
        def slow(*args):time.sleep(.03);return {'calls':[],'items':[],'final':{'answer':'Done','action':None}},{}
        with self.assertRaises(ProviderError):run_agent(cfg,cond,[],'try',model_call=slow)

    def test_failed_output_does_not_commit_and_recommender_requires_explicit_approval(self):
        cfg,cond=self.config(mode='autonomous');auth=self.start(cfg,cond);rid=uid();self.store.begin_request(auth[0]['id'],rid,'try')
        result=run_agent(cfg,cond,[],'try');result['content']='reservation_price is 95'
        reply=self.store.finish_request(auth[0]['id'],rid,result)
        self.assertTrue(reply['safe_fallback']);self.assertEqual(self.store.export('decisions'),[]);self.assertEqual(len(self.store.transcript(auth[0]['id'])),2)
        cfg,cond=self.config();auth=self.start(cfg,cond);message=self.respond(auth)
        payload={'action':'accept','decision_action':message['decision_action'],'message_id':message['id'],'client_ms':100,'page_id':'page'}
        with self.assertRaises(ValueError):self.store.decide(auth[0],cfg,payload)
        self.see(auth,message)
        with self.assertRaises(ValueError):self.store.decide(auth[0],cfg,{**payload,'action':'override'})
        self.store.decide(auth[0],cfg,{**payload,'action':'reject','decision_action':None})
        with self.assertRaises(Conflict):self.store.decide(auth[0],cfg,payload)

    def test_versions_fail_closed_and_new_case_requires_only_files(self):
        cfg,cond=self.config();bad=copy.deepcopy(cfg);bad['case_study']['dataset_hash']='changed'
        with self.assertRaises(ValueError):validate(bad)
        bad=copy.deepcopy(cfg);bad['case_study']['pack_hash']='changed'
        with self.assertRaises(ValueError):validate(bad)
        bad=copy.deepcopy(cfg);bad['conditions'][0]['agent']['allowed_tools']=['unknown']
        with self.assertRaises(ValueError):validate(bad)
        from research_agent import packs
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'new_case'/'v1';path.mkdir(parents=True)
            pack=load_pack('supplier_selection','v1');pack.pop('_hash');pack['id']='new_case';pack['title']='A file-only case'
            (path/'pack.json').write_text(json.dumps(pack))
            with patch.object(packs,'PACK_ROOT',Path(directory)):
                new=study_config('new_case','v1')
                response=run_agent(new,new['conditions'][2],[],'Analyze')
                self.assertEqual(response['decision_action'],{'supplier_id':'beacon'})

    def test_provider_protocol_and_hard_timeout(self):
        import io
        from research_agent.providers import _agent_step_http
        cfg,cond=self.config();cfg['architecture'].update(provider='openai',model=PINNED_MODEL,temperature=0)
        output={'status':'completed','model':PINNED_MODEL,'usage':{'input_tokens':20,'output_tokens':10},'output':[{'type':'function_call','name':'get_supplier_records','arguments':'{"supplier_ids":[]}','call_id':'tool-1'}]}
        with patch.dict(os.environ,{'OPENAI_API_KEY':'MOCK'}),patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(output).encode())) as post:
            result,meta=_agent_step_http(cfg,cond,[],[],{'type':'object','properties':{},'required':[],'additionalProperties':False},1)
            body=json.loads(post.call_args.args[0].data)
            self.assertEqual(result['calls'][0]['call_id'],'tool-1');self.assertFalse(body['store']);self.assertFalse(body['parallel_tool_calls'])
        with patch('subprocess.run',side_effect=subprocess.TimeoutExpired('api',.1)):
            with self.assertRaisesRegex(ProviderError,'timeout'):agent_step(cfg,cond,[],[],{},.1)

    def test_numeric_forecast_and_secret_task_policy(self):
        cfg,cond=self.config('demand_forecasting');cond['output']='number'
        self.assertEqual(float(run_agent(cfg,cond,[],'Analyze')['content']),1200)
        cfg,cond=self.config('negotiation')
        self.assertTrue(check_agent_output(cfg,cond,{'content':'Price 80','decision_action':{'price':80},'metadata':{}})['metadata']['safe_fallback'])
        self.assertTrue(check_agent_output(cfg,cond,{'content':'Price 120.005','decision_action':{'price':120.005},'metadata':{}})['metadata']['safe_fallback'])

    def test_negotiation_model_never_receives_reservation_and_only_policy_selects_amount(self):
        cfg,cond=self.config('negotiation');cfg['architecture']['provider']='openai';captured=[]
        def model(items,*args):
            captured.append(copy.deepcopy(items))
            if len(captured)==1:return {'calls':[{'name':'get_supplier_records','inputs':{'supplier_ids':['beacon']},'call_id':'n1'},{'name':'evaluate_offer','inputs':{'offer':None},'call_id':'n2'}],'items':[],'final':None},{}
            return {'calls':[],'items':[],'final':{'answer':'The current policy offer is available.','action':{'price':110}}},{}
        response=run_agent(cfg,cond,[],'Negotiate',model_call=model)
        self.assertNotIn('reservation_price',json.dumps(captured));self.assertNotIn('95',json.dumps(captured))
        self.assertEqual(response['decision_action'],{'price':120})



class AgentHTTPTests(unittest.TestCase):
    def setUp(self):
        from research_agent.server import AppServer
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'db.sqlite3')
        self.server=AppServer(('127.0.0.1',0),self.store,'admin',local_mode=True);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
    def request(self,path,data=None,token='admin'):
        import urllib.request,urllib.error
        req=urllib.request.Request(self.origin+path,data=json.dumps(data).encode() if data is not None else None,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','X-Research-Request':'1'})
        try:
            with urllib.request.urlopen(req) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as response:return response.code,json.load(response)
    def launch(self,cfg,mode):
        self.store.save(cfg,self.store.current()['revision']);launch=self.store.issue_launch(uid(),mode,True)
        return self.request('/api/participant/start',{'launch_token':launch['launch_token'],'consent':True})[1]
    def test_autonomous_http_commit_is_atomic_and_reloads_with_result(self):
        cfg=study_config('disruption_response','v1');creds=self.launch(cfg,'autonomous');token=creds['session_token']
        code,message=self.request('/api/participant/message',{'request_id':uid(),'content':'Analyze','round_id':1},token)
        self.assertEqual(code,200,message);self.assertEqual(message['committed_decision']['action'],'autonomous')
        code,view=self.request('/api/participant/session',token=token)
        self.assertEqual(view['status'],'completed');self.assertIsNotNone(view['decision']['outcome_json'])
        detail=self.request('/api/admin/session?session_id='+creds['session_id'])[1]
        self.assertEqual(detail['session']['dataset_version'],'synthetic_supply@v1')
        self.assertTrue(any(e['type']=='tool_call' for e in detail['activity']['timeline']))
        self.assertEqual(len(self.store.export('decisions')),1)
        code,_=self.request('/api/participant/events',{'events':[{'id':uid(),'type':'tool_call','client_ms':0,'client_wall_ms':0,'page_id':'x','payload':{}}]},token)
        self.assertEqual(code,400)
    def test_api_loop_charges_each_step_and_passes_filtered_tool_output(self):
        cfg=study_config('supplier_selection','v1');cfg['architecture'].update(provider='openai',model=PINNED_MODEL,temperature=0)
        creds=self.launch(cfg,'recommender');steps=[]
        def model(config,condition,items,definitions,schema,timeout):
            steps.append(copy.deepcopy(items));meta={'usage':{'input_tokens':100,'output_tokens':10},'returned_model':PINNED_MODEL}
            if len(steps)==1:return {'calls':[{'name':'get_supplier_records','inputs':{'supplier_ids':[]},'call_id':'c1'}],'items':[{'type':'function_call','name':'get_supplier_records','arguments':'{"supplier_ids":[]}','call_id':'c1'}],'final':None},meta
            return {'calls':[],'items':[],'final':{'answer':'Compare the public attributes.','action':{'supplier_id':'atlas'}}},meta
        with patch.dict(os.environ,{'OPENAI_API_KEY':'TEST-ONLY'}),patch('research_agent.providers.agent_step',side_effect=model):
            code,response=self.request('/api/participant/message',{'request_id':uid(),'content':'Analyze'},creds['session_token'])
        self.assertEqual(code,200,response);self.assertEqual(self.store.budget()['calls'],2)
        self.assertEqual(self.store.budget()['committed_micros'],112)
        self.assertNotIn('reservation_price',json.dumps(steps));self.assertNotIn('buyer_budget',json.dumps(steps))
        self.assertEqual(self.store.export('decisions'),[])
    def test_save_slots_preview_and_activate_only_the_selected_revision(self):
        current = self.store.current()
        active_config = copy.deepcopy(current["config"])
        active_config["status"] = "active"
        current = self.store.save(active_config, current["revision"])
        code, slots = self.request("/api/admin/setup-slots")
        self.assertEqual(code, 200)
        self.assertEqual(len(slots), 1)
        old_link = self.store.issue_launch("old-participant", active_config["conditions"][0]["id"])

        config = study_config("ozer_game", "v1")
        config["conditions"][0]["prompt"] = "Use only your observation."
        config["telemetry"]["enabled"] = False
        code, slot = self.request("/api/admin/setup-slots", {"label": "Ozer pilot", "config": config})
        self.assertEqual(code, 200, slot)
        self.assertEqual(self.store.current(), current)
        code, loaded = self.request(f"/api/admin/setup-slots?id={slot['id']}")
        self.assertEqual(code, 200, loaded)
        self.assertEqual(loaded["config"], config)
        self.assertEqual(self.request("/api/admin/setup-slots", token="unauthorized")[0], 403)
        self.assertEqual(self.request("/api/admin/setup-slots", {"label": "ozer pilot", "config": config})[0], 409)

        preview_request = {"slot_id": slot["id"], "expected_slot_revision": slot["revision"],
                           "participant_id": "preview-ozer", "condition_id": "human_retailer"}
        self.assertEqual(self.request("/api/admin/launch", preview_request)[0], 403)
        code, launch = self.request("/api/admin/launch", {**preview_request, "preview": True})
        self.assertEqual(code, 200, launch)
        self.assertEqual(launch["config_version"], slot["revision"])

        next_config = copy.deepcopy(config)
        next_config["conditions"][0]["prompt"] = "An edited counterpart prompt."
        code, edited = self.request("/api/admin/setup-slots", {
            "id": slot["id"], "expected_revision": slot["revision"], "label": "Ozer pilot v2", "config": next_config,
        })
        self.assertEqual(code, 200, edited)
        self.assertEqual(self.request("/api/admin/launch", {**preview_request, "preview": True})[0], 409)
        credentials = self.store.start(launch["launch_token"], True)
        session, pinned_config, _ = self.store.authenticate(credentials["session_token"])
        self.assertEqual(session["config_version"], slot["revision"])
        self.assertEqual(pinned_config, config)
        old_session = self.store.start(old_link["launch_token"], True)
        self.assertEqual(self.store.authenticate(old_session["session_token"])[1], active_config)

        activation = {"id": slot["id"], "expected_revision": edited["revision"],
                      "expected_study_revision": current["revision"]}
        self.assertEqual(self.request("/api/admin/use-setup-slot", {**activation, "expected_revision": slot["revision"]})[0], 409)
        code, study = self.request("/api/admin/use-setup-slot", activation)
        self.assertEqual(code, 200, study)
        self.assertEqual(study["config"], next_config)
        self.assertEqual(study["source_slot_id"], slot["id"])
        self.assertEqual(self.store.current(), study)
        self.assertEqual(self.request("/api/admin/use-setup-slot", activation)[0], 409)
        self.assertEqual(self.store.authenticate(credentials["session_token"])[1], config)

    def test_delete_setup_keeps_pinned_sessions_and_protects_active_setup(self):
        current = self.store.current()
        code, saved = self.request("/api/admin/setup-slots", {"label": "Delete me", "config": current["config"]})
        self.assertEqual(code, 200, saved)
        request = {"id": saved["id"], "expected_revision": saved["revision"]}
        path = "/api/admin/delete-setup-slot"
        self.assertEqual(self.request(path, request, token="unauthorized")[0], 403)
        self.assertEqual(self.request(path, {**request, "expected_revision": -1})[0], 409)
        active = self.store.setup_slot(current["source_slot_id"])
        self.assertEqual(self.request(path, {"id": active["id"], "expected_revision": active["revision"]})[0], 409)
        launch = self.store.issue_launch("existing-preview", current["config"]["conditions"][0]["id"],
                                         preview=True, slot_id=saved["id"], expected_slot_revision=saved["revision"])
        self.assertEqual(self.request(path, request), (200, {"ok": True}))
        self.assertEqual(self.store.current(), current)
        self.assertEqual([slot["id"] for slot in self.store.setup_slots()], [active["id"]])
        credentials = self.store.start(launch["launch_token"], True)
        session, _, _ = self.store.authenticate(credentials["session_token"])
        self.assertEqual(session["config_version"], saved["revision"])
        code, replacement = self.request("/api/admin/setup-slots", {"label": "Delete me", "config": current["config"]})
        self.assertEqual(code, 200, replacement)
        self.assertNotEqual(replacement["id"], saved["id"])

    def test_overview_demos_are_pinned_previews_and_use_the_server_provider(self):
        current = self.store.current()
        slots = self.store.setup_slots()
        path = "/api/admin/launch"
        demos = {"supplier_selection": "recommend", "demand_forecasting": "analytical"}
        for demo_id, condition_id in demos.items():
            request = {"participant_id": uid(), "demo_agent": demo_id, "preview": True}
            self.assertEqual(self.request(path, {**request, "preview": False})[0], 400)
            with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
                code, launch = self.request(path, request)
                self.assertEqual(code, 200, launch)
                self.assertEqual(self.request(path, request)[1]["revision"], launch["revision"])
                credentials = self.store.start(launch["launch_token"], True)
                session, config, condition = self.store.authenticate(credentials["session_token"])
                self.assertTrue(session["preview"])
                self.assertEqual(config["architecture"]["provider"], "demo")
                self.assertEqual(condition["id"], condition_id)
                code, reply = self.request("/api/participant/message",
                                           {"request_id": uid(), "content": "Analyze the case"}, credentials["session_token"])
                self.assertEqual(code, 200, reply)
                self.assertEqual(reply["option_id"] is not None, demo_id == "supplier_selection")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}):
                code, live = self.request(path, request)
                self.assertEqual(code, 200, live)
                self.assertNotEqual(live["revision"], launch["revision"])
                credentials = self.store.start(live["launch_token"], True)
                session, config, _ = self.store.authenticate(credentials["session_token"])
                self.assertEqual(config["architecture"]["provider"], "openai")
                self.assertEqual(session["model_snapshot"], PINNED_MODEL)
        self.assertEqual(self.store.current(), current)
        self.assertEqual(self.store.setup_slots(), slots)

    def test_live_supplier_preview_uses_the_question_history_and_saved_supplier_table(self):
        study = self.store.current()
        config = study["config"]
        config["scenario"]["options"][0]["label"] = "Custom Atlas"
        slot = self.store.save_setup_slot("Supplier preview", config)
        path = "/api/admin/launch"
        request = {"participant_id": uid(), "condition_id": "recommend", "preview": True, "live_preview": True,
                   "slot_id": slot["id"], "expected_slot_revision": slot["revision"]}
        first = "Which supplier is cheapest?"
        follow_up = "What if I need it within 15 days?"
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual(self.request(path, request)[0], 400)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}):
            self.assertEqual(self.request(path, {**request, "preview": False})[0], 400)
            code, launch = self.request(path, request)
            self.assertEqual(code, 200, launch)
            self.assertNotEqual(launch["revision"], slot["revision"])
            credentials = self.store.start(launch["launch_token"], True)
            token = credentials["session_token"]
            _, pinned, group = self.store.authenticate(token)
            self.assertEqual(pinned["scenario"], config["scenario"])
            self.assertEqual(pinned["architecture"]["provider"], "openai")
            self.assertEqual(group["selection"], "model")
            metadata = {"usage": {"input_tokens": 300, "output_tokens": 30}, "returned_model": PINNED_MODEL}
            answers = [({"answer": "Cedar Industrial at $86.", "option_id": "cedar"}, metadata),
                       ({"answer": "Custom Atlas at $92 in 14 days.", "option_id": "atlas"}, metadata)]
            with patch("research_agent.engine.call_openai", side_effect=answers) as provider:
                for question, supplier in [(first, "cedar"), (follow_up, "atlas")]:
                    code, reply = self.request("/api/participant/message", {"request_id": uid(), "content": question}, token)
                    self.assertEqual(code, 200, reply)
                    self.assertEqual(reply["option_id"], supplier)
                messages = provider.call_args.args[2]
                self.assertIn("Custom Atlas", messages[0]["content"])
                self.assertEqual(messages[1]["content"], first)
                self.assertEqual(messages[-1]["content"], follow_up)
            code, fixed = self.request(path, {**request, "live_preview": False})
            self.assertEqual(code, 200, fixed)
            self.assertEqual(fixed["revision"], slot["revision"])
        self.assertEqual(self.store.setup_slot(slot["id"]), slot)

    def test_setup_assistant_from_document_builds_reviewable_draft(self):
        current = self.store.current()
        old_slots = self.store.setup_slots()
        brief = "Compare an AI supplier and an AI retailer in Ozer's Game."
        attachment = {"name": "protocol.txt", "data": base64.b64encode(brief.encode()).decode()}
        plan = {
            "case": "ozer_game@v1", "name": "Forecast study", "description": "Compare the two roles.",
            "assignment": "balanced", "memory": "session", "telemetry": False,
            "conditions": [
                {"id": "human_supplier", "label": "Supplier", "objective": "Set production.", "prompt": "Report a forecast."},
            ],
            "review": ["Confirm assignment."],
        }
        response = {
            "status": "completed", "model": PINNED_MODEL,
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(plan)}]}],
        }
        path = "/api/admin/setup-assistant"
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual(self.request(path, {"description": brief})[0], 502)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "mock-only"}), patch(
            "research_agent.setup_assistant.request_openai", return_value=response
        ) as provider:
            self.assertEqual(self.request(path, {"document": attachment}, token="unauthorized")[0], 403)
            code, result = self.request(path, {"document": attachment})
            self.assertEqual(code, 200, result)
            provider.assert_called_once()
        config = result["config"]
        self.assertEqual(config["status"], "draft")
        self.assertEqual(config["case_study"]["id"], "ozer_game")
        self.assertEqual(config["architecture"]["provider"], "demo")
        self.assertEqual(config["architecture"]["memory"], "none")
        self.assertFalse(config["telemetry"]["enabled"])
        self.assertEqual(config["conditions"][0]["prompt"], "Report a forecast.")
        self.assertEqual([condition["id"] for condition in config["conditions"]], ["human_supplier", "human_retailer"])
        self.assertEqual(config["conditions"][1]["prompt"], study_config("ozer_game", "v1")["conditions"][1]["prompt"])
        self.assertEqual(result["review"][0], "Confirm assignment.")
        self.assertIn("default instructions were kept", result["review"][1])
        body = provider.call_args.args[0]
        self.assertEqual(body["input"][-1], {"role": "user", "content": brief})
        self.assertFalse(body["store"])
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertEqual(self.store.setup_slots(), old_slots)
        self.assertEqual(self.store.current(), current)
        self.assertEqual(self.store.budget()["committed_micros"], 120)
        history_path = "/api/admin/setup-generations"
        self.assertEqual(self.request(history_path, token="unauthorized")[0], 403)
        code, history = self.request(history_path)
        self.assertEqual(code, 200)
        self.assertEqual([entry["title"] for entry in history], ["Forecast study"])
        self.assertEqual(self.request(f"{history_path}?id={history[0]['id']}"), (200, result))
        self.assertEqual(Store(self.store.path).setup_generation(history[0]["id"]), result)
        self.assertNotIn(brief, json.dumps(result))

    def test_setup_assistant_flags_unsupported_cases_and_respects_spending_limit(self):
        plan = {"case": "unsupported", "review": ["No matching workflow."]}
        response = {
            "status": "completed", "model": PINNED_MODEL,
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(plan)}]}],
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "mock-only"}), patch(
            "research_agent.setup_assistant.request_openai", return_value=response
        ) as provider:
            path = "/api/admin/setup-assistant"
            code, result = self.request(path, {"description": "Build a new game."})
            self.assertEqual(code, 200, result)
            self.assertIsNone(result["config"])
            with patch.dict(os.environ, {"SERVER_SPEND_CAP_USD": "0.000001"}):
                self.assertEqual(self.request(path, {"description": "Build another draft."})[0], 400)
            provider.assert_called_once()

    def test_session_assistant_queries_local_records_and_keeps_previews_separate(self):
        config = study_config("supplier_selection", "v1")
        config.update(status="active", name="Supplier pilot")
        self.store.save(config, self.store.current()["revision"])
        for code, preview in (("buyer-open", False), ("buyer-stopped", False), ("preview-only", True)):
            link = self.store.issue_launch(code, "recommender", preview, "external")
            session = self.store.start(link["launch_token"], True)
            if code == "buyer-stopped":
                self.assertEqual(self.request("/api/participant/withdraw", {}, session["session_token"])[0], 200)
        query = {"view": "list", "scope": "study", "status": "not_completed",
                 "condition": "recommender", "study": "Supplier pilot", "participant": ""}
        path = "/api/admin/session-assistant"
        self.assertEqual(self.request(path, {"query": query}, token="unauthorized")[0], 403)
        status, result = self.request(path, {"query": query})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["total"], 2)
        self.assertEqual({r["participant_id"]: r["status"] for r in result["rows"]},
                         {"buyer-open": "active", "buyer-stopped": "withdrawn"})
        self.assertTrue(all("token_hash" not in row for row in result["rows"]))
        response = {
            "status": "completed", "model": PINNED_MODEL,
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "output": [{"type": "message", "content": [{"type": "output_text",
                "text": json.dumps({**query, "view": "count", "scope": "preview"})}]}],
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "mock-only"}), patch(
            "research_agent.session_assistant.request_openai", return_value=response
        ) as provider:
            code, answer = self.request(path, {"question": "How many previews?", "previous": query})
        self.assertEqual(code, 200, answer)
        self.assertEqual(answer["total"], 1)
        self.assertEqual(answer["rows"], [])
        self.assertNotIn("preview-only", json.dumps(provider.call_args.args[0]))
        self.assertNotIn("buyer-open", json.dumps(provider.call_args.args[0]))
        self.assertEqual(self.store.budget()["committed_micros"], 120)
        self.assertEqual(self.request(path, {"query": {**query, "view": "DROP TABLE sessions"}})[0], 400)

    def test_catalog_and_draft_preview_do_not_launch_or_save(self):
        self.assertIn('ozer_game', {p['id'] for p in self.request('/api/admin/case-studies')[1]})
        code,body=self.request('/api/admin/case-study-config',{'id':'demand_forecasting','version':'v1'})
        self.assertEqual(code,200);self.assertEqual(len(body['config']['conditions']),5)
        code,preview=self.request('/api/admin/condition-preview',{'config':body['config'],'condition_id':'recommender'})
        self.assertEqual(code,200,preview);self.assertEqual(self.store.current()['revision'],1)
        self.assertEqual(preview['sample']['decision_action'],{'units':1200})

    def test_spend_cap_stops_the_next_model_step_and_keeps_failed_turn_auditable(self):
        cfg=study_config('supplier_selection','v1');cfg['architecture'].update(provider='openai',model=PINNED_MODEL,temperature=0,spend_cap_usd=.01474)
        creds=self.launch(cfg,'recommender')
        def model(*args):return {'calls':[{'name':'get_supplier_records','inputs':{'supplier_ids':[]},'call_id':'c1'}],'items':[],'final':None},{'usage':{'input_tokens':100,'output_tokens':10},'returned_model':PINNED_MODEL}
        with patch.dict(os.environ,{'OPENAI_API_KEY':'TEST'}),patch('research_agent.providers.agent_step',side_effect=model) as call:
            code,response=self.request('/api/participant/message',{'request_id':uid(),'content':'Analyze'},creds['session_token'])
        self.assertEqual(code,200,response);self.assertEqual(response['status'],'ended_system_limit');self.assertIn('spend',response['system_message'])
        self.assertEqual(call.call_count,1);self.assertEqual(self.store.export('decisions'),[])
        self.assertTrue(any(e['type']=='tool_call' for e in self.store.export('events')))
