import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from research_agent.config import DEFAULT, PINNED_MODEL, canonical, fingerprint, public_config, validate
from research_agent.engine import ProviderError, respond, public_model_input, negotiation_offer
from research_agent.store import Store, Conflict, uid, token_hash


class V2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'test.sqlite3')

    def tearDown(self):
        self.tmp.cleanup()

    def configure(self, task='negotiation', role='supplier', baseline=False, output='explanation', private=True):
        snapshot=self.store.current(); c=copy.deepcopy(DEFAULT);c['task']=task
        c['conditions'][0].update(role=role,agent_enabled=not baseline,output=output)
        if private:
            c['conditions'][0]['private_information']={'memo':'PRIVATE-CANARY-482'}
            if task=='negotiation':c['conditions'][0]['private_rules'][('min' if role=='supplier' else 'max')]['price']=95 if role=='supplier' else 105
        self.store.save(c,snapshot['revision'])
        return c

    def start(self, participant='P-1'):
        launch=self.store.issue_launch(participant,'recommend',True)
        creds=self.store.start(launch['launch_token'],True)
        return creds,self.store.authenticate(creds['session_token'])

    def offer(self, auth, price=None):
        sid=auth[0]['id'];rid=uid();text='Please discuss the offer' if price is None else f'My offer is {price}'
        self.store.begin_request(sid,rid,text,offer_price=price)
        history=[m for m in self.store.transcript(sid) if m['request_id']!=rid]
        with patch('research_agent.engine.call_openai',side_effect=AssertionError('private data reached a provider')):
            response=respond(auth[1],auth[2],history,text,offer_price=price)
        return self.store.finish_request(sid,rid,response)

    def see(self,auth,message):
        self.store.events(auth[0],auth[1],[{'id':uid(),'type':'recommendation_visible','client_ms':100,'client_wall_ms':1000,'page_id':'p','round_id':1,'payload':{'message_id':message['id']}}])

    def test_negotiation_back_and_forth_private_limits_and_agreement(self):
        self.configure();creds,auth=self.start()
        self.assertEqual(self.offer(auth)['offer_price'],120)
        for expected in (115,110,105,100,95,95):
            message=self.offer(auth,80)
            self.assertEqual(message['offer_price'],expected)
            self.assertNotIn('PRIVATE-CANARY',message['content'])
        message=self.offer(auth,100);self.assertEqual(message['offer_price'],100);self.see(auth,message)
        result=self.store.decide(auth[0],auth[1],{'action':'accept','option_id':None,'final_price':100,'message_id':message['id'],'round_id':1,'client_ms':1200,'page_id':'p'})
        self.assertTrue(result['agreement'])
        decision=self.store.export('decisions')[0]
        self.assertEqual(decision['final_price'],100)
        self.assertEqual(json.loads(decision['outcome_json'])['agreed_total'],10000)
        self.assertEqual(decision['client_deliberation_ms'],1100)
        public=json.dumps(self.store.session_view(*self.store.authenticate(creds['session_token'])))
        self.assertNotIn('PRIVATE-CANARY',public);self.assertNotIn('private_rules',public)
        for table in ('messages','events','decisions'):
            for row in self.store.export(table):
                self.assertEqual(row['participant_id'],'P-1');self.assertEqual(row['session_id'],creds['session_id'])
                self.assertEqual(row['condition_id'],'recommend');self.assertEqual(row['config_version'],auth[0]['revision_id'])
                self.assertEqual(row['round_id'],1);self.assertEqual(row['model_snapshot'],'deterministic-v1')

    def test_negotiation_override_is_not_an_agreement_and_stale_reply_fails(self):
        self.configure();_,auth=self.start();m1=self.offer(auth);self.see(auth,m1);m2=self.offer(auth,80)
        with self.assertRaises(Conflict):self.store.decide(auth[0],auth[1],{'action':'accept','option_id':None,'final_price':m1['offer_price'],'message_id':m1['id'],'client_ms':500,'page_id':'p'})
        self.see(auth,m2)
        result=self.store.decide(auth[0],auth[1],{'action':'override','option_id':None,'final_price':85,'message_id':m2['id'],'client_ms':500,'page_id':'p'})
        self.assertFalse(result['agreement']);self.assertEqual(result['final_price'],85)

    def test_buyer_chatbot_numeric_and_genai_roles(self):
        self.configure(role='buyer');_,auth=self.start();self.assertEqual(self.offer(auth,130)['offer_price'],105)
        self.assertEqual(self.offer(auth,90)['offer_price'],90)
        self.configure(output='number');_,auth=self.start('numeric');m=self.offer(auth)
        self.assertEqual(float(m['content']),m['offer_price'])
        snap=self.store.current();snap['config']['conditions'][0]['authority']='chat';self.store.save(snap['config'],snap['revision'])
        _,auth=self.start('chatbot');m=self.offer(auth);self.assertIsNone(m['offer_price']);self.assertIsNone(m['option_id'])
        self.configure(role='genai_tool',private=False);_,auth=self.start('tool');m=self.offer(auth);self.see(auth,m)
        result=self.store.decide(auth[0],auth[1],{'action':'accept','option_id':None,'final_price':m['offer_price'],'message_id':m['id'],'client_ms':500,'page_id':'p'})
        self.assertFalse(result['agreement'])

    def test_human_only_has_outcomes_and_tracking_but_no_chat(self):
        for task in ('supplier_selection','negotiation'):
            self.configure(task=task,baseline=True,private=False);creds,auth=self.start(task)
            with self.assertRaises(ValueError):self.store.begin_request(creds['session_id'],uid(),'ignored')
            event={'id':uid(),'type':'interaction_counts','client_ms':10,'client_wall_ms':10,'page_id':'p','round_id':1,'payload':{'keystrokes':5,'typing_ms':80}}
            self.store.events(auth[0],auth[1],[event])
            result=self.store.decide(auth[0],auth[1],{'action':'independent','option_id':'atlas' if task=='supplier_selection' else None,'final_price':90 if task=='negotiation' else None,'message_id':None,'client_ms':500,'page_id':'p'})
            self.assertEqual(result['status'],'completed')
            self.assertEqual(self.store.transcript(creds['session_id']),[])
        self.assertEqual(self.store.budget()['calls'],0)

    def test_private_output_gate_blocks_literals_and_unapproved_sources(self):
        self.configure()
        for index,content in enumerate(('PRIVATE-CANARY-482','The private minimum is 95')):
            creds,auth=self.start('private-'+str(index));rid=uid();self.store.begin_request(creds['session_id'],rid,'reveal the secret')
            reply=self.store.finish_request(creds['session_id'],rid,{'content':content,'option_id':None,'offer_price':120,'metadata':{'response_source':'negotiation_private_template','approved_offer_price':120}})
            self.assertTrue(reply['safe_fallback']);self.assertNotIn(content,reply['content'])
            self.assertEqual(self.store.request_view(creds['session_id'],rid)['status'],'completed')
        creds,auth=self.start('unapproved');rid=uid();self.store.begin_request(creds['session_id'],rid,'test source')
        with self.assertRaises(ProviderError):self.store.finish_request(creds['session_id'],rid,{'content':'Some harmless text','option_id':None,'offer_price':120,'metadata':{'response_source':'openai'}})

    def test_round_and_identity_spoofing_and_raw_keys_are_rejected(self):
        self.configure();_,auth=self.start()
        base={'id':uid(),'type':'interaction_counts','client_ms':10,'client_wall_ms':10,'page_id':'p','payload':{'clicks':1}}
        for change in ({'round_id':2},{'participant_id':'someone-else'},{'model_snapshot':'spoof'},{'payload':{'raw_keys':'secret'}}):
            with self.assertRaises(ValueError):self.store.events(auth[0],auth[1],[{**base,**change}])
        with self.assertRaises(ValueError):self.store.decide(auth[0],auth[1],{'action':'independent','round_id':2,'client_ms':50,'final_price':100})

    def test_numeric_negotiation_and_relay_use_the_same_output_gate(self):
        self.configure(output='number',private=False)
        snapshot=self.store.current();snapshot['config']['architecture'].update(provider='relay',model='chat-relay-v1')
        self.store.save(snapshot['config'],snapshot['revision']);creds,auth=self.start()
        rid=uid();self.store.begin_request(creds['session_id'],rid,'Opening offer')
        with self.assertRaises(ValueError):self.store.reply_relay(creds['session_id'],rid,'110',None,120)
        with self.assertRaises(ValueError):self.store.reply_relay(creds['session_id'],rid,'120',None,110)
        self.assertEqual(self.store.request_view(creds['session_id'],rid)['status'],'pending')
        message=self.store.reply_relay(creds['session_id'],rid,'120',None,120)
        self.assertEqual(message['offer_price'],120)
        self.assertEqual(json.loads(self.store.export('messages')[-1]['metadata_json'])['output_check'],'passed')

    def test_adapter_blocks_a_changed_model_snapshot(self):
        import io
        from research_agent.providers import OpenAIProvider
        c=copy.deepcopy(DEFAULT);c['architecture'].update(provider='openai',model=PINNED_MODEL,temperature=0)
        result={'status':'completed','model':'gpt-4.1-mini','output':[]}
        with patch.dict('os.environ',{'OPENAI_API_KEY':'mock-key'}),patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(result).encode())):
            with self.assertRaisesRegex(ProviderError,'different model snapshot'):OpenAIProvider().generate(c,c['conditions'][0],[])

    def test_balanced_random_assignment_is_atomic_and_repeatable(self):
        results=[];errors=[]
        def assign(i):
            try:results.append(self.store.issue_launch('P-'+str(i),preview=True,assignment_method='balanced'))
            except Exception as exc:errors.append(exc)
        threads=[threading.Thread(target=assign,args=(i,)) for i in range(20)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(errors,[])
        self.assertEqual(sum(r['condition_id']=='recommend' for r in results),10)
        before=self.store.issue_launch('repeat',preview=True,assignment_method='random')
        for _ in range(3):self.assertEqual(self.store.issue_launch('repeat',preview=True,assignment_method='random')['condition_id'],before['condition_id'])
        creds=self.store.start(before['launch_token'],True)
        self.assertEqual(self.store.authenticate(creds['session_token'])[2]['id'],before['condition_id'])
        with self.assertRaises(ValueError):self.store.issue_launch('bad','recommend',True,'balanced')

    def test_pinned_snapshot_temperature_and_quality_validation(self):
        c=copy.deepcopy(DEFAULT);c['architecture'].update(provider='openai',model=PINNED_MODEL,temperature=.2)
        self.assertEqual(validate(c)['architecture']['model'],PINNED_MODEL)
        for model,temp in [('gpt-4.1-mini',.2),(PINNED_MODEL,None)]:
            invalid=copy.deepcopy(c);invalid['architecture'].update(model=model,temperature=temp)
            with self.assertRaises(ValueError):validate(invalid)
        c=copy.deepcopy(DEFAULT);c['conditions'][0]['advice_quality']='flawed'
        self.assertEqual(respond(c,c['conditions'][0],[],'compare')['option_id'],'cedar')
        self.assertNotIn('advice_quality',json.dumps(public_config(c,c['conditions'][0])))

    def test_budget_is_global_conservative_and_retained_on_failure(self):
        c=self.store.current();c['config']['architecture'].update(provider='openai',model=PINNED_MODEL,temperature=.2,spend_cap_usd=.02)
        self.store.save(c['config'],c['revision']);_,auth=self.start();rid=uid()
        with patch.dict('os.environ',{'OPENAI_API_KEY':'test-key'}),patch('research_agent.engine.call_openai',side_effect=ProviderError('uncertain network result')):
            with self.assertRaises(ProviderError):self.store.provider_call(auth[0],rid,auth[1],auth[2],[{'role':'user','content':'hello'}])
        budget=self.store.budget();self.assertEqual(budget['calls'],1);self.assertGreater(budget['committed_micros'],0)
        c=self.store.current();c['config']['description']='A new revision';self.store.save(c['config'],c['revision'])
        _,other=self.start('P-2')
        with patch.dict('os.environ',{'OPENAI_API_KEY':'test-key'}),patch('research_agent.engine.call_openai') as provider:
            with self.assertRaises(ProviderError):self.store.provider_call(other[0],uid(),other[1],other[2],[{'role':'user','content':'hello'}])
            provider.assert_not_called()
        self.assertEqual(self.store.budget()['calls'],1)

    def test_budget_settles_known_usage_and_blocks_concurrent_reservations(self):
        c=self.store.current();c['config']['architecture'].update(provider='openai',model=PINNED_MODEL,temperature=.2,spend_cap_usd=.02)
        self.store.save(c['config'],c['revision']);_,one=self.start();_,two=self.start('P-2')
        entered=threading.Event();release=threading.Event();errors=[]
        def provider(*args):
            entered.set();release.wait(5)
            return {'answer':'Beacon','option_id':'beacon'},{'usage':{'input_tokens':10,'output_tokens':10},'returned_model':PINNED_MODEL}
        def run():
            try:self.store.provider_call(one[0],uid(),one[1],one[2],[{'role':'user','content':'hi'}])
            except Exception as exc:errors.append(exc)
        with patch.dict('os.environ',{'OPENAI_API_KEY':'test-key'}),patch('research_agent.engine.call_openai',side_effect=provider) as call:
            worker=threading.Thread(target=run);worker.start();self.assertTrue(entered.wait(5))
            try:
                with self.assertRaises(ProviderError):self.store.provider_call(two[0],uid(),two[1],two[2],[{'role':'user','content':'hi'}])
            finally:release.set();worker.join(5)
            self.assertEqual(errors,[]);self.assertEqual(call.call_count,1)
        self.assertEqual(self.store.budget()['committed_micros'],20)

    def test_missing_api_key_and_input_limit_do_not_make_calls(self):
        c=self.store.current();c['config']['architecture'].update(provider='openai',model=PINNED_MODEL,temperature=.2)
        self.store.save(c['config'],c['revision']);_,auth=self.start()
        with patch.dict('os.environ',{},clear=True):
            with self.assertRaises(ProviderError):self.store.provider_call(auth[0],uid(),auth[1],auth[2],[])
        with patch.dict('os.environ',{'OPENAI_API_KEY':'test-key'}),patch('research_agent.engine.call_openai') as provider:
            with self.assertRaises(ProviderError):self.store.provider_call(auth[0],uid(),auth[1],auth[2],[{'role':'user','content':'x'*40000}])
            provider.assert_not_called()
        self.assertEqual(self.store.budget()['calls'],0)


class MigrationTests(unittest.TestCase):
    def test_v1_data_is_backed_up_and_enriched_without_rewriting_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'study.sqlite3'
            legacy=copy.deepcopy(DEFAULT)
            for k in ('task','negotiation','assignment'):legacy.pop(k)
            for k in ('spend_cap_usd','max_input_tokens','max_output_tokens','input_usd_per_million','output_usd_per_million'):legacy['architecture'].pop(k)
            for cond in legacy['conditions']:
                cond.pop('agent_enabled');cond.pop('advice_quality')
            raw=canonical(legacy);digest=fingerprint(legacy)
            with sqlite3.connect(path) as db:
                db.executescript(Path(__file__).with_name('fixtures').joinpath('schema_v1.sql').read_text())
                db.execute('INSERT INTO revisions VALUES(1,1,?,?)',(digest,raw))
                db.execute("INSERT INTO sessions VALUES('old-session',?,'P-old','recommend',1,?,1,2,'completed',1,'consent',1)",(token_hash('old-key'),digest))
                db.execute("INSERT INTO messages VALUES('old-response','old-session','assistant','Beacon',1,'beacon','{}','old-request',1,100,'page')")
                db.execute("INSERT INTO requests VALUES('old-session','old-request','completed')")
                db.execute("INSERT INTO events VALUES('old-event','old-session','page_open',1,0,0,'page','{}')")
                db.execute("INSERT INTO decisions VALUES('old-decision','old-session','old-response','accept','beacon','beacon',2,1000,1000,.6,0,'')")
            db.close()
            store=Store(path)
            with store.db() as db:
                self.assertEqual(db.execute('SELECT config_json FROM revisions').fetchone()[0],raw)
                self.assertEqual(db.execute('SELECT config_hash FROM revisions').fetchone()[0],digest)
                for table in ('events','messages','requests','decisions'):
                    row=db.execute(f'SELECT * FROM {table}').fetchone()
                    self.assertEqual(row['participant_id'],'P-old');self.assertEqual(row['round_id'],1)
                    self.assertEqual(row['condition_id'],'recommend');self.assertEqual(row['config_version'],1)
                    self.assertEqual(row['model_snapshot'],'deterministic-v1')
                row=dict(db.execute('SELECT * FROM decisions').fetchone());row.update(id='second-round',round_id=2)
                db.execute('INSERT INTO decisions ('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
            self.assertEqual(len(list(Path(directory).glob('*.pre-v2-*.sqlite3'))),1)
            Store(path)
            self.assertEqual(len(list(Path(directory).glob('*.pre-v2-*.sqlite3'))),1)
            self.assertEqual(store.current()['config']['task'],'supplier_selection')


class V2HTTPTests(unittest.TestCase):
    def setUp(self):
        from research_agent.server import AppServer
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'http.sqlite3')
        self.server=AppServer(('127.0.0.1',0),self.store,'admin-key','integration-key',False)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
        self.origin='http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.worker.join();self.tmp.cleanup()

    def request(self,path,data=None,token=None,extra=None):
        import urllib.request
        import urllib.error
        headers={'Content-Type':'application/json','X-Research-Request':'1',**(extra or {})}
        if token:headers['Authorization']='Bearer '+token
        req=urllib.request.Request(self.origin+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        try:
            with urllib.request.urlopen(req) as response:return response.status,json.loads(response.read())
        except urllib.error.HTTPError as response:return response.code,json.loads(response.read())

    def save(self,config):
        return self.request('/api/admin/config',{'config':config,'expected_revision':self.store.current()['revision']},'admin-key')

    def start(self,condition='recommend'):
        status,launch=self.request('/api/admin/launch',{'participant_id':'HTTP-P','condition_id':condition,'preview':True},'admin-key')
        self.assertEqual(status,200)
        status,creds=self.request('/api/participant/start',{'launch_token':launch['launch_token'],'consent':True})
        self.assertEqual(status,200)
        return creds

    def test_researcher_revokes_unused_launch_without_exposing_token(self):
        launch = self.store.issue_launch("Link test", "recommend", True)
        path = "/api/admin/pending-launches"
        self.assertEqual(self.request(path)[0], 403)
        status, links = self.request(path, token="admin-key")
        self.assertEqual(status, 200)
        self.assertEqual([link["participant_id"] for link in links], ["Link test"])
        self.assertNotIn("token", json.dumps(links))
        body = {"launch_id": links[0]["id"]}
        revoke = "/api/admin/revoke-launch"
        self.assertEqual(self.request(revoke, body)[0], 403)
        self.assertEqual(self.request(revoke, body, "admin-key")[0], 200)
        self.assertEqual(self.request(path, token="admin-key")[1], [])
        self.assertEqual(self.request(revoke, body, "admin-key")[0], 409)
        data = {"launch_token": launch["launch_token"], "consent": True}
        for action in ("preview", "start"):
            self.assertEqual(self.request("/api/participant/" + action, data)[0], 400)
        self.assertEqual(self.store.overview()["totals"]["sessions"], 0)

    def test_researcher_revokes_then_deletes_only_the_target_session(self):
        creds, other = self.start(), self.start("delegate")
        token, session_id = creds["session_token"], creds["session_id"]
        session, config, _ = self.store.authenticate(token)
        self.store.begin_request(session_id, "pending", "Compare suppliers")
        with self.store.db() as db:
            db.execute("INSERT INTO session_budgets VALUES(?,2500,0,NULL)", (session_id,))
            db.execute("""INSERT INTO provider_spend
                (id,session_id,request_id,reserved_micros,status,created_at,participant_id,condition_id,config_version,model_snapshot)
                VALUES('cost',?,'pending',500,'reserved',0,'HTTP-P','recommend',1,'deterministic-v1')""", (session_id,))
        body = {"session_id": session_id}
        revoke, delete = "/api/admin/revoke-session", "/api/admin/delete-session"
        self.assertEqual(self.request(delete, body, "admin-key")[0], 409)
        self.assertEqual(self.request(revoke, body, token)[0], 403)
        self.assertEqual(self.request(revoke, body, "admin-key")[0], 200)
        for method, data in (("status", None), ("session", None),
                             ("message", {"content": "new", "request_id": "new"})):
            status, result = self.request("/api/participant/" + method, data, token)
            self.assertEqual((status, result["status"]), (200, "revoked"))
            self.assertNotIn("Compare suppliers", json.dumps(result))
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT status FROM requests").fetchone()[0], "failed")
            self.assertIsNotNone(db.execute("SELECT released_at FROM session_budgets").fetchone()[0])
        self.assertEqual(self.store.session_detail(session_id)["session"]["end_reason"], "researcher_revoked")
        event = self.store.export("events")[0]
        self.assertEqual((event["type"], json.loads(event["payload_json"])["state"]), ("session_ended", "revoked"))
        self.assertEqual(self.request(delete, body, "admin-key")[0], 200)
        self.store.server_event(session, "tool_call", {})
        self.store.events(session, config, [])
        self.assertEqual(self.request("/api/participant/status", token=token)[0], 403)
        self.assertIsNone(self.store.session_detail(session_id))
        self.assertEqual(self.store.session_detail(other["session_id"])["session"]["status"], "active")
        self.assertEqual(self.store.export("messages"), [])
        self.assertEqual(self.store.export("events"), [])
        with self.store.db() as db:
            self.assertIsNone(db.execute("SELECT * FROM requests").fetchone())
            self.assertIsNone(db.execute("SELECT * FROM session_budgets").fetchone())
            self.assertEqual(db.execute("SELECT participant_id FROM provider_spend").fetchone()[0], "")
        self.assertEqual(self.store.budget()["committed_micros"], 500)

    def test_form_preview_is_validated_private_safe_and_does_not_save_or_call_api(self):
        c=copy.deepcopy(DEFAULT);c['task']='negotiation';c['conditions'][0].update(role='supplier')
        c['conditions'][0]['private_rules']['min']['price']=95
        c['conditions'][0]['private_information']={'memo':'NOT-FOR-PARTICIPANTS'}
        payload={'config':c,'condition_id':'recommend'}
        self.assertEqual(self.request('/api/admin/condition-preview',payload)[0],403)
        with patch('research_agent.engine.call_openai',side_effect=AssertionError('preview called API')):
            status,body=self.request('/api/admin/condition-preview',payload,'admin-key')
        self.assertEqual(status,200);self.assertEqual(body['preview_kind'],'deterministic_mock')
        self.assertEqual(self.store.current()['revision'],1)
        self.assertNotIn('NOT-FOR-PARTICIPANTS',json.dumps(body));self.assertNotIn('private_rules',json.dumps(body))
        c['conditions'][0]['agent_enabled']=False
        self.assertIsNone(self.request('/api/admin/condition-preview',payload,'admin-key')[1]['sample'])

    def test_builder_preview_returns_only_public_draft_and_creates_no_session(self):
        config=copy.deepcopy(DEFAULT); config['task']='negotiation'
        config['scenario']['consent_text']='Unsaved consent copy'
        config['conditions'][0].update(role='supplier', private_information={'memo':'BUILDER-PRIVATE-SENTINEL'})
        config['conditions'][0]['private_rules']['min']['price']=95
        path='/api/admin/builder-preview'
        payload={'config':config,'condition_id':'recommend'}
        self.assertEqual(self.request(path,payload)[0],403)
        with patch('research_agent.server.respond', side_effect=AssertionError('builder called the agent')):
            status, body=self.request(path,payload,'admin-key')
        self.assertEqual(status,200)
        self.assertEqual(set(body),{'public'})
        self.assertEqual(body['public']['scenario']['consent_text'],'Unsaved consent copy')
        self.assertNotIn('BUILDER-PRIVATE-SENTINEL',json.dumps(body))
        self.assertNotIn('private_rules',json.dumps(body))
        self.assertEqual(self.request(path,{**payload,'condition_id':'missing'},'admin-key')[0],400)
        self.assertEqual(self.store.current()['revision'],1)
        with self.store.db() as db:
            for table in ('launches','sessions','events'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],0)

    def test_negotiation_http_flow_records_price_and_exposure_time(self):
        c=copy.deepcopy(DEFAULT);c['task']='negotiation';c['conditions'][0]['role']='supplier';c['conditions'][0]['private_rules']['min']['price']=95
        self.assertEqual(self.save(c)[0],200);creds=self.start();token=creds['session_token']
        for price in (80,100):
            status,message=self.request('/api/participant/message',{'content':'Price offer','offer_price':price,'round_id':1,'request_id':uid()},token)
            self.assertEqual(status,200)
        self.assertEqual(message['offer_price'],100)
        event={'id':uid(),'type':'recommendation_visible','client_ms':100,'client_wall_ms':1000,'round_id':1,'page_id':'test','payload':{'message_id':message['id']}}
        self.assertEqual(self.request('/api/participant/events',{'events':[event]},token)[0],200)
        status,decision=self.request('/api/participant/decision',{'action':'accept','option_id':None,'final_price':100,'message_id':message['id'],'round_id':1,'client_ms':1100,'page_id':'test'},token)
        self.assertEqual(status,200);self.assertTrue(decision['agreement'])
        status,detail=self.request('/api/admin/session?session_id='+creds['session_id'],token='admin-key')
        self.assertEqual(detail['decision']['final_price'],100);self.assertEqual(detail['decision']['client_deliberation_ms'],1000)
        self.assertEqual(detail['setup']['task'],'negotiation');self.assertEqual(detail['setup']['scenario']['options'],[])

    def test_api_mode_passes_versioned_session_through_budget_adapter(self):
        c=copy.deepcopy(DEFAULT);c['architecture'].update(provider='openai',model=PINNED_MODEL,temperature=.2)
        self.assertEqual(self.save(c)[0],200);creds=self.start();token=creds['session_token']
        response=({'answer':'Beacon has the highest public weighted score.','option_id':'beacon'}, {'returned_model':PINNED_MODEL,'usage':{'input_tokens':100,'output_tokens':30}})
        with patch.dict('os.environ',{'OPENAI_API_KEY':'SERVER-KEY-SENTINEL'}),patch('research_agent.engine.call_openai',return_value=response) as provider:
            status,body=self.request('/api/participant/message',{'content':'compare','request_id':uid(),'round_id':1},token)
        self.assertEqual(status,200);self.assertEqual(provider.call_count,1)
        self.assertIn('Study condition ID: recommend.',provider.call_args.args[2][0]['content'])
        self.assertNotIn('SERVER-KEY-SENTINEL',json.dumps(body))
        detail=self.store.session_detail(creds['session_id'])
        self.assertEqual(detail['session']['model_snapshot'],PINNED_MODEL)
        self.assertEqual(detail['messages'][-1]['metadata']['output_check'],'passed')
        self.assertEqual(self.store.budget()['committed_micros'],88)

    def test_human_baseline_messages_are_blocked_but_outcome_works(self):
        c=copy.deepcopy(DEFAULT);c['conditions'][0]['agent_enabled']=False;self.assertEqual(self.save(c)[0],200)
        creds=self.start();token=creds['session_token']
        self.assertEqual(self.request('/api/participant/message',{'content':'hi','request_id':uid()},token)[0],400)
        status,result=self.request('/api/participant/decision',{'action':'independent','option_id':'atlas','message_id':None,'round_id':1,'client_ms':200},token)
        self.assertEqual(status,200);self.assertEqual(result['final_option'],'atlas')
        self.assertEqual(self.store.budget()['calls'],0)

    def test_remote_integration_can_request_balanced_assignment(self):
        c=copy.deepcopy(DEFAULT);c['status']='active';self.assertEqual(self.save(c)[0],200)
        data={'participant_id':'remote-participant','assignment_method':'balanced'}
        self.assertEqual(self.request('/api/launch',data)[0],403)
        status,result=self.request('/api/launch',data,extra={'X-Integration-Key':'integration-key'})
        self.assertEqual(status,200);self.assertIn(result['condition_id'],['recommend','delegate'])
        self.assertEqual(result['config_version'],2)
        status,creds=self.request('/api/participant/start',{'launch_token':result['launch_token'],'consent':True})
        self.assertEqual(status,200)
        self.assertEqual(self.store.authenticate(creds['session_token'])[2]['id'],result['condition_id'])

    def test_round_spoof_and_invalid_prices_fail_before_response(self):
        c=copy.deepcopy(DEFAULT);c['task']='negotiation';self.save(c);creds=self.start();token=creds['session_token']
        for extra in ({'round_id':2},{'offer_price':True},{'offer_price':100.001},{'offer_price':500}):
            self.assertEqual(self.request('/api/participant/message',{'content':'offer','request_id':uid(),**extra},token)[0],400)
        self.assertEqual(self.store.transcript(creds['session_id']),[])

    def test_config_normalization_and_empty_csv_context_headers(self):
        import csv
        import urllib.request
        legacy=copy.deepcopy(DEFAULT);del legacy['task'];del legacy['assignment'];del legacy['negotiation']
        status,body=self.request('/api/admin/validate-config',{'config':legacy},'admin-key')
        self.assertEqual(status,200);self.assertEqual(body['config']['task'],'supplier_selection')
        self.assertEqual(self.store.current()['revision'],1)
        for table in ('sessions','messages','events','decisions','provider_spend'):
            req=urllib.request.Request(self.origin+'/api/admin/export?table='+table,headers={'Authorization':'Bearer admin-key'})
            with urllib.request.urlopen(req) as response:header=next(csv.reader(response.read().decode('utf-8-sig').splitlines()))
            self.assertEqual(len(header),len(set(header)))
            for key in ('participant_id','session_id','condition_id','config_version','round_id','model_snapshot'):self.assertIn(key,header)
