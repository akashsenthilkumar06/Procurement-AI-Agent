"""Regression checks for the three acceptance risks; all provider interactions are mocked."""
import copy
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from research_agent.agent import check_agent_output,run_agent,SystemLimit
from research_agent.config import PINNED_MODEL,validate
from research_agent.packs import study_config,resolve
from research_agent.store import Store,uid
from research_agent.privacy import disclosure_flags,redact_model_values,SAFE_REPLY
from research_agent.offer_policy import evaluate_offer


class SafetyFixTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.store=Store(Path(self.directory.name)/'db.sqlite3')
    def tearDown(self):self.directory.cleanup()
    def config(self,case='supplier_selection'):
        cfg=study_config(case,'v2' if case=='negotiation' else 'v1');cfg['conditions']=[cfg['conditions'][1]]
        cfg['architecture'].update(provider='openai',model=PINNED_MODEL,temperature=0)
        return cfg
    def start(self,cfg,name=None):
        self.store.save(cfg,self.store.current()['revision']);launch=self.store.issue_launch(name or uid(),cfg['conditions'][0]['id'],True)
        creds=self.store.start(launch['launch_token'],True)
        return creds,self.store.authenticate(creds['session_token'])

    def test_private_forms_and_no_free_text_approved_action_exemption(self):
        cfg=self.config('negotiation');cond=cfg['conditions'][0];cond['agent']['mode']='recommender'
        candidates=['My minimum is 95','My floor rounds to 100','My floor is just under 96','Between 94 and 96','Ninety-five','Ninety‑five','An interval from ninety-four to ninety-six','My 104.5 offer is 10% above my floor']
        for text in candidates:
            reply=check_agent_output(cfg,cond,{'content':text,'decision_action':{'price':95},'metadata':{'approved_action':{'price':95}}})
            self.assertTrue(reply['metadata']['safe_fallback'],text);self.assertEqual(reply['content'],SAFE_REPLY)
            self.assertEqual(reply['decision_action'],{'price':95})
        reply=check_agent_output(cfg,cond,{'content':'A policy result is available.','decision_action':{'price':96},'metadata':{'approved_action':{'price':95}}})
        self.assertIsNone(reply['decision_action']);self.assertTrue(reply['metadata']['safe_fallback'])

    def test_private_numbers_inside_role_memos_are_protected(self):
        cfg=self.config();cond=cfg['conditions'][0];cond['private_information']={'memo':'Our minimum is ninety-five dollars.'}
        reply=check_agent_output(cfg,cond,{'content':'My floor is just under 96.','decision_action':None,'metadata':{}})
        self.assertTrue(reply['metadata']['safe_fallback'])
        cond['private_information']={'reservation_price':'95'}
        reply=check_agent_output(cfg,cond,{'content':'Ninety-five.','decision_action':None,'metadata':{}})
        self.assertTrue(reply['metadata']['safe_fallback'])

    def test_policy_offer_is_never_model_selected_and_injected_private_grants_are_filtered(self):
        cfg=self.config('negotiation');cond=cfg['conditions'][0];cond['agent']['mode']='recommender'
        cond['agent']['data_access']['suppliers']+=['reservation_price','buyer_budget']
        captured=[]
        def model(items,*args):
            captured.append(copy.deepcopy(items))
            if len(captured)==1:return {'calls':[{'name':'get_supplier_records','inputs':{'supplier_ids':[]},'call_id':'r'},{'name':'evaluate_offer','inputs':{'offer':None},'call_id':'p'}],'items':[],'final':None},{}
            return {'calls':[],'items':[],'final':{'answer':'Review the current policy result.','action':{'price':90}}},{}
        response=run_agent(cfg,cond,[],'My confidential minimum is ninety-five.',model_call=model,offer_price=95.0)
        serialized=json.dumps(captured)
        self.assertNotIn('reservation_price',serialized);self.assertNotIn('buyer_budget',serialized);self.assertNotIn('95',serialized)
        self.assertEqual(response['decision_action'],{'price':95.0})
        self.assertEqual(redact_model_values({'offer':95.0},['95']),{'offer':None})
        for text in ('The counteroffer is 130.','The counteroffer is one hundred thirty.'):
            reply=check_agent_output(cfg,cond,{'content':text,'decision_action':{'price':120},'metadata':{'approved_action':{'price':120}}})
            self.assertTrue(reply['metadata']['safe_fallback'],text)
            self.assertEqual(reply['decision_action'],{'price':120})

    def test_offer_policy_actions_and_boundary_never_echoed_as_counter(self):
        cfg=self.config('negotiation');pack,_=resolve(cfg['case_study']);cond=cfg['conditions'][0]
        self.assertEqual(evaluate_offer(80,pack,cond),{'decision':'counter','amount':120})
        self.assertEqual(evaluate_offer(95,pack,cond),{'decision':'accept','amount':None})
        self.assertEqual(evaluate_offer(500,pack,cond),{'decision':'reject','amount':None})
        pack['offer_policy']['opening_price']=95
        self.assertNotEqual(evaluate_offer(80,pack,cond)['amount'],95)

    def test_reserve_at_start_warn_eighty_and_block_new_without_cutting_existing(self):
        cfg=self.config();cfg['architecture'].update(spend_cap_usd=.5,session_spend_usd=.25)
        self.store.save(cfg,self.store.current()['revision'])
        def launch(name):
            ticket=self.store.issue_launch(name,cfg['conditions'][0]['id'],True)
            creds=self.store.start(ticket['launch_token'],True)
            return creds,self.store.authenticate(creds['session_token'])
        one,auth=launch('one');two,auth2=launch('two');three,blocked=launch('three')
        self.assertEqual(blocked[0]['status'],'ended_system_limit');self.assertFalse(three['admitted'])
        budget=self.store.budget();self.assertEqual(budget['reserved_session_micros'],500000);self.assertTrue(budget['warning_80_percent'])
        self.assertTrue(any(e['type']=='budget_warning' for e in self.store.export('events')))
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake-only'}):
            self.store.provider_call(auth[0],uid(),cfg,cfg['conditions'][0],[],invoke=lambda:({'ok':True},{'usage':{'input_tokens':10,'output_tokens':10}}))
        self.assertEqual(self.store.session_view(*auth)['status'],'active')
        self.store.withdraw(auth[0]);self.store.withdraw(auth2[0]);four,new=launch('four')
        self.assertTrue(four['admitted']);self.assertEqual(new[0]['status'],'active')
        self.assertTrue(any(r['researcher_flag'] for r in self.store.overview()['sessions']))

    def test_concurrent_admission_never_overallocates(self):
        cfg=self.config();cfg['architecture'].update(spend_cap_usd=.5,session_spend_usd=.25)
        self.store.save(cfg,self.store.current()['revision']);results=[];errors=[]
        def start(index):
            try:
                launch=self.store.issue_launch('c-'+str(index),cfg['conditions'][0]['id'],True)
                results.append(self.store.start(launch['launch_token'],True))
            except Exception as exc:errors.append(exc)
        threads=[threading.Thread(target=start,args=(i,)) for i in range(10)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(errors,[]);self.assertEqual(sum(r['admitted'] for r in results),2)
        self.assertLessEqual(self.store.budget()['allocated_micros'],500000)
