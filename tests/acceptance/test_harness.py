"""Keyless checks of the acceptance harness controls; these do not make live calls."""
import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing,redirect_stdout
from unittest.mock import patch

from acceptance import run as harness
from acceptance.recording_provider import RecordingMockProvider,script_for
from research_agent import providers
from research_agent.packs import load_pack
from research_agent.store import Store


class HarnessControlTests(unittest.TestCase):
    def test_recording_mock_captures_real_adapter_wire_and_is_deep_copied(self):
        config=harness.api_config();cond=config['conditions'][0]
        mock=RecordingMockProvider();mock.arm(script_for(load_pack('supplier_selection','v1'),action=None))
        items=[{'role':'system','content':'Original context'}]
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake-only'}):result,metadata=mock(config,cond,items,[],{},1)
        items[0]['content']='changed after request'
        self.assertEqual(mock.records[0]['request']['input'][0]['content'],'Original context')
        self.assertEqual(mock.records[0]['request']['temperature'],0)
        self.assertEqual(result['calls'][0]['name'],'get_supplier_records')
        self.assertNotIn('fake-only',json.dumps(mock.records))

    def test_input_scanner_marks_unauthorized_private_field_and_public_coincidence(self):
        records=[{'label':{'case':'supplier_selection','role':'buyer'},'request':{'input':[{'type':'function_call_output','output':json.dumps({'records':[{'reservation_price':95}],'on_hand':100})}]}}]
        occurrences=harness.scan_inputs(records)
        self.assertTrue(any(o['classification']=='FORBIDDEN_PRIVATE_FIELD' for o in occurrences))
        self.assertTrue(any(o['classification']=='public_dataset_numeric_coincidence' for o in occurrences))
        records[0]['label']={'case':'negotiation','role':'supplier'}
        self.assertTrue(any(o['classification']=='FORBIDDEN_PRIVATE_FIELD' for o in harness.scan_inputs(records)))

    def test_unexplained_secret_literal_in_system_context_fails_scanner(self):
        records=[{'label':{'case':'supplier_selection','role':'buyer'},'request':{'input':[{'role':'system','content':'The confidential floor is 95'}]}}]
        self.assertTrue(any(o['classification']=='FORBIDDEN_PRIVATE_VALUE' for o in harness.scan_inputs(records)))

    def test_live_recorder_calls_existing_worker_with_timeout_and_never_captures_key(self):
        config=harness.api_config();calls=[]
        with tempfile.TemporaryDirectory() as directory:
            recorder=harness.LiveRecorder(Path(directory))
            def worker(command,**kwargs):
                args=json.loads(kwargs['input']);calls.append(command)
                Path(args['capture_path']).write_text(json.dumps({'request':{'model':config['architecture']['model'],'input':args['items']}}))
                return type('Completed',(),{'returncode':0,'stdout':json.dumps({'response':{'calls':[],'items':[],'final':{'answer':'Done','action':None}},'metadata':{'usage':{'input_tokens':10,'output_tokens':10}}})})()
            with patch('subprocess.run',side_effect=worker):response,metadata=recorder(config,config['conditions'][0],[{'role':'user','content':'Hi'}],[],{},1)
            self.assertTrue(calls[0][-1].endswith('provider_worker.py'))
            self.assertEqual(recorder.records[0]['request']['input'][0]['content'],'Hi')

    def test_live_determinism_budget_abort_never_dispatches_provider_and_is_not_pass(self):
        saved=copy.deepcopy(harness.RESULTS);harness.RESULTS.clear()
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'OPENAI_API_KEY':'fake','SERVER_SPEND_CAP_USD':'0.000001'}),patch.object(harness.Handler,'log_message',lambda *args:None):
            fixture=harness.Fixture(Path(directory)/'budget.sqlite3');recorder=harness.LiveRecorder(Path(directory))
            try:
                with patch('research_agent.providers.agent_step',side_effect=AssertionError('No API call allowed')):
                    with self.assertRaises(harness.SpendAbort):harness.live_determinism(fixture,recorder,Path(directory),.000001)
                self.assertEqual(fixture.store.budget()['calls'],0)
                self.assertEqual(harness.RESULTS[-1]['status'],'NOT RUN')
                self.assertEqual(recorder.records,[])
            finally:fixture.close();harness.RESULTS[:]=saved

    def test_live_command_cap_abort_exits_three_and_marks_both_tests_not_run(self):
        saved=copy.deepcopy(harness.RESULTS);harness.RESULTS.clear()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'docs').mkdir();here=root/'tests/acceptance';here.mkdir(parents=True)
            try:
                with patch.object(harness,'ROOT',root),patch.object(harness,'HERE',here),patch.dict(os.environ,{'OPENAI_API_KEY':'fake-only','RESEARCH_AGENT_LIVE_TESTS':'1'}),patch('sys.argv',['run.py','live','--max-spend','0.000001']),patch('subprocess.run',side_effect=AssertionError('Cap must stop all provider dispatch')),redirect_stdout(io.StringIO()) as stdout:
                    code=harness.main()
                self.assertEqual(code,3);self.assertIn('ABORTED_SPEND_CAP',stdout.getvalue())
                self.assertEqual([r['status'] for r in harness.RESULTS],['NOT RUN','NOT RUN'])
                with closing(sqlite3.connect(here/'artifacts/live/budget.sqlite3')) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM provider_spend').fetchone()[0],0)
            finally:harness.RESULTS[:]=saved

    def test_temperature_migration_backfills_pinned_config_without_changing_its_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db.sqlite3';store=Store(path);config=harness.api_config();config['architecture']['temperature']=.25
            store.save(config,store.current()['revision']);launch=store.issue_launch('temp-test','acceptance',True);creds=store.start(launch['launch_token'],True)
            session,cfg,condition=store.authenticate(creds['session_token']);store.server_event(session,'tool_call',{'test':True})
            with store.db() as db:
                raw=db.execute('SELECT config_json FROM revisions WHERE id=?',(session['config_version'],)).fetchone()[0]
                for table in ('sessions','launches','messages','requests','events','decisions','provider_spend'):db.execute(f'ALTER TABLE {table} DROP COLUMN temperature')
                db.execute('PRAGMA user_version=3')
            reopened=Store(path)
            self.assertTrue(list(Path(directory).glob('*.pre-audit-*.sqlite3')))
            self.assertEqual(reopened.export('events')[0]['temperature'],.25)
            self.assertEqual(reopened.export('sessions')[0]['temperature'],.25)
            with reopened.db() as db:self.assertEqual(db.execute('SELECT config_json FROM revisions WHERE id=?',(session['config_version'],)).fetchone()[0],raw)
