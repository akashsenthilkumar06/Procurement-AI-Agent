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
from research_agent.offer_policy import evaluate_offer, chat_offer, chat_acceptance


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
        cond['agent']['allowed_tools'].append('get_supplier_records')
        cond['agent']['data_access']['suppliers']=['reservation_price','buyer_budget']
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

    def test_chat_prices_are_distinguished_from_market_numbers_and_conditions(self):
        cases = [
            ("I am willing to go for $60 per unit", 60),
            ("I'm willing to pay $105 per chip.", 105),
            ("We are prepared to go with USD 110.", 110),
            ("I'd offer $107.50 per unit.", 107.5),
            ("I'd much rather come to about $80 a unit.", 80),
            ("$120 a unit", 120),
            ("Chip stocks are down 20%. I can offer $105 per unit.", 105),
            ("Your $120 quote is high. Would you accept USD 110 per chip?", 110),
            ("My unit-price offer is USD 90.00.", 90),
            ("How about $107.50?", 107.5),
            ("Let's agree at 110.", 110),
            ("$105 per unit", 105),
            ("Stocks fell 20% and demand fell 10%.", None),
            ("I can pay 10% more.", None),
            ("I can offer 100 chips.", None),
            ("If demand recovers, I can pay $110.", None),
            ("Previously I could pay $120.", None),
            ("I cannot pay $120.", None),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(chat_offer(text, 'USD'), expected)
        for text in ("I can offer $100 to $110.", "I can pay $100; how about $105?",
                     "I can offer EUR 105.", "I can pay $10,500 total."):
            with self.subTest(text=text), self.assertRaises(ValueError):
                chat_offer(text, 'USD')

    def test_chat_acceptance_requires_unconditional_agreement(self):
        for text in ('Ok I accept', 'Okay, I accept.', 'I accept your offer!', 'I agree to the price',
                     'Deal', 'Agreed.', "Let's do it"):
            with self.subTest(text=text):
                self.assertEqual(chat_acceptance(text, 'USD'), {'price':None})
        for text in ('I accept $120', 'I accept your price at USD 120', 'I accept $120 a unit.'):
            with self.subTest(text=text):
                self.assertEqual(chat_acceptance(text, 'USD'), {'price':120})
        for text in ('I do not accept', "I don't accept $120", 'I accept if delivery is free',
                     'Would you accept $120?', 'I accept?', 'I accepted your last price',
                     'I accept $120 or $110', 'I agree that the price is too high'):
            with self.subTest(text=text):
                self.assertIsNone(chat_acceptance(text, 'USD'))
        with self.assertRaises(ValueError):
            chat_acceptance('I accept EUR 120', 'USD')

    def test_public_market_arguments_reach_the_negotiation_model(self):
        cfg = self.config('negotiation')
        cond = cfg['conditions'][0]
        cond['agent']['mode'] = 'recommender'
        text = "Semiconductor stocks fell 20%, so our demand may weaken. I can offer $105 per chip."
        captured = []
        def model(items, definitions, schema, remaining, step):
            captured.append(copy.deepcopy(items))
            if step == 0:
                return {'calls':[{'name':'evaluate_offer','inputs':{'offer':None},'call_id':'price'}],
                        'items':[], 'final':None}, {}
            return {'calls':[], 'items':[], 'final':{'answer':'I understand the demand concern and accept your offer.', 'action':None}}, {}
        reply = run_agent(cfg, cond, [], text, model_call=model, offer_price=105)
        self.assertEqual(captured[0][-1]['content'], text)
        self.assertEqual(reply['decision_action'], {'price':105})
        self.assertFalse(reply['metadata'].get('safe_fallback'))

    def test_negotiation_rewrites_a_blocked_reply_without_sending_it_back(self):
        cfg = self.config('negotiation')
        cond = cfg['conditions'][0]
        cond['agent']['mode'] = 'recommender'
        blocked = "Your $60 offer is below my minimum, so I can offer $120."
        captured = []
        def model(items, definitions, schema, remaining, step):
            captured.append(copy.deepcopy(items))
            if step == 0:
                return {'calls':[{'name':'evaluate_offer','inputs':{'offer':None},'call_id':'price'}],
                        'items':[], 'final':None}, {}
            if step == 1:
                return {'calls':[], 'items':[], 'final':{'answer':blocked, 'action':None}}, {}
            self.assertEqual(definitions, [])
            self.assertNotIn(blocked, json.dumps(items))
            return {'calls':[], 'items':[], 'final':{'answer':'My current offer is $120 per unit.', 'action':{'price':60}}}, {}
        reply = run_agent(cfg, cond, [], 'I can offer $60 per unit.', model_call=model, offer_price=60)
        self.assertEqual(len(captured), 3)
        self.assertEqual(reply['content'], 'My current offer is $120 per unit.')
        self.assertEqual(reply['decision_action'], {'price':120})
        self.assertFalse(reply['metadata']['safe_fallback'])
        self.assertTrue(reply['metadata']['response_repaired'])
        self.assertIn('private_numeric_inference', reply['metadata']['repaired_output_flags'])

    def test_failed_negotiation_rewrites_use_a_checked_offer_response(self):
        cfg = self.config('negotiation')
        cond = cfg['conditions'][0]
        cond['agent']['mode'] = 'recommender'
        for offer, answer in ((60, 'My confidential minimum is 95.'), (95, 'My confidential minimum is 95.'), (60, SAFE_REPLY)):
            with self.subTest(offer=offer, answer=answer):
                def model(items, definitions, schema, remaining, step):
                    if step == 0:
                        return {'calls':[{'name':'evaluate_offer','inputs':{'offer':None},'call_id':'price'}],
                                'items':[], 'final':None}, {}
                    return {'calls':[], 'items':[], 'final':{'answer':answer, 'action':None}}, {}
                reply = run_agent(cfg, cond, [], 'Discuss my offer.', model_call=model, offer_price=offer)
                reply = check_agent_output(cfg, cond, reply)
                self.assertNotEqual(reply['content'], SAFE_REPLY)
                self.assertNotIn('95', reply['content'])
                self.assertEqual(reply['decision_action'], {'price':95 if offer == 95 else 120})
                self.assertEqual(reply['metadata']['fallback_type'], 'policy_response')
                self.assertTrue(reply['metadata']['safe_fallback'])

    def test_negotiation_can_reference_submitted_participant_offers(self):
        cfg = self.config('negotiation')
        cond = cfg['conditions'][0]
        cond['agent']['mode'] = 'recommender'
        cases = [
            ([], 90, "I cannot accept your $90 offer. My counteroffer is $120 per unit.", False),
            ([{'role': 'user', 'content': 'My offer was 90.', 'offer_price': 90}], None,
             "You previously offered $90. My offer is $120 per unit.", False),
            ([], 90, "My counteroffer is $130 per unit.", True),
            ([], 95, "Your $95 offer is accepted.", True),
            ([{'role': 'user', 'content': 'The price is 90.', 'offer_price': None}], None,
             "You offered $90. My counteroffer is $120 per unit.", True),
        ]
        for history, offer, answer, blocked in cases:
            with self.subTest(answer=answer):
                def model(items, definitions, schema, remaining, step):
                    if step == 0:
                        return {'calls': [{'name': 'evaluate_offer', 'inputs': {'offer': None}, 'call_id': 'price'}],
                                'items': [], 'final': None}, {}
                    return {'calls': [], 'items': [], 'final': {'answer': answer, 'action': None}}, {}
                response = run_agent(cfg, cond, history, 'Discuss the current offer.', model_call=model, offer_price=offer)
                # Persistence checks the result again before storing and delivering it.
                response = check_agent_output(cfg, cond, response)
                self.assertEqual(bool(response['metadata'].get('safe_fallback')), blocked)
                if not blocked:
                    self.assertEqual(response['content'], answer)
                    self.assertEqual(response['decision_action'], {'price': 120})

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
