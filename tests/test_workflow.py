import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

from brain_miner.config import load_env
from brain_miner.store import Store
from brain_miner.research import validate, variants, passed, prune_pnl
from brain_miner.clients import Brain, LLM, RemoteError
from brain_miner.cli import run


class Tests(unittest.TestCase):
    def test_dotenv_preserves_process(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'BRAIN_EMAIL':'process'}, clear=True):
            p=Path(d)/'.env';p.write_text('BRAIN_EMAIL=file\nBRAIN_PASSWORD="a#b=c"\n')
            load_env(p)
            self.assertEqual(os.environ['BRAIN_EMAIL'],'process')
            self.assertEqual(os.environ['BRAIN_PASSWORD'],'a#b=c')

    def test_identity_and_raw_result(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)/'test.db')
            a=s.add('rank(close)',{'decay':0},'scope')
            self.assertIsNone(s.add('rank(close)',{'decay':0},'scope'))
            b=s.add('rank(close)',{'decay':5},'scope',parent=a,stage=1)
            self.assertNotEqual(a,b)
            self.assertIsNone(s.claim('another scope'))
            job=s.claim('scope');self.assertEqual(job['id'],a)
            raw={'id':'alpha','is':{'sharpe':2},'checks':[{'name':'example'}]}
            s.finish(a,raw)
            self.assertEqual(json.loads(s.rows()[0]['result']),raw)
            self.assertEqual(s.rows()[1]['parent'],a)
            s.close()

    def test_claim_is_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'test.db';s=Store(path);s.add('rank(close)',{},'scope');s.close()
            results=[]
            def claim():
                local=Store(path)
                try: results.append(local.claim('scope'))
                finally: local.close()
            ts=[threading.Thread(target=claim) for _ in range(4)]
            for t in ts:t.start()
            for t in ts:t.join()
            self.assertEqual(sum(r is not None for r in results),1)

    def test_feedback_context_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(Path(d) / 'test.db')
            try:
                s.note('options result', 'options')
                s.note('price result', 'price')
                self.assertEqual(s.latest_note('options'), 'options result')
                self.assertEqual(s.latest_note('new context'), '')
            finally:
                s.close()

    def test_validation(self):
        self.assertTrue(validate('ts_mean(subtract(close, open), 5)',{'close','open'}))
        for x in ['rank(secret_field)','rank(close); import os','rank(close))','unknown(close)']:
            self.assertFalse(validate(x,{'close'}))

    def test_filter_and_stages(self):
        self.assertTrue(passed({'is':{'sharpe':2,'fitness':1.2,'turnover':.1}}))
        for value in [float('nan'),100,None]:
            self.assertFalse(passed({'is':{'sharpe':value,'fitness':2,'turnover':.1}}))
        self.assertEqual(variants('close',{'is':{'sharpe':-2}},0),['reverse(close)'])
        self.assertEqual(variants('close',{'is':{'sharpe':2}},2),[])

    def test_pruning_unknown_and_duplicate(self):
        series={str(i):i*.001 for i in range(40)}
        result=prune_pnl([{'id':'a','sharpe':3,'returns':series},
                          {'id':'b','sharpe':2,'returns':series},
                          {'id':'c','sharpe':1,'returns':{}}])
        self.assertEqual([r['id'] for r in result['kept']],['a'])
        self.assertEqual([r['id'] for r in result['removed']],['b'])
        self.assertEqual([r['id'] for r in result['unknown']],['c'])

    def test_pruning_rejects_invalid_anchor(self):
        for history in [{'day': 1}, {str(i): 1 for i in range(40)},
                        {str(i): float('nan') for i in range(40)}]:
            result = prune_pnl([{'id':'a', 'sharpe':3, 'returns':history}])
            self.assertEqual(result['kept'], [])
            self.assertEqual(len(result['unknown']), 1)

    @patch.dict(os.environ, {'BRAIN_EMAIL':'test','BRAIN_PASSWORD':'test'})
    def test_authentication_and_pagination(self):
        client = Brain()
        client.session = Mock()
        client.session.post.return_value = Mock(status_code=401, headers={'Location':'/authentication/persona'})
        with self.assertRaisesRegex(RemoteError, 'interactive'):
            client.login()
        client.session.post.return_value = Mock(status_code=201)
        client.login()
        config = {'instrumentType':'EQUITY', 'region':'USA', 'delay':1, 'universe':'TOP3000'}
        with patch.object(client, 'get', side_effect=[{'results':[{'id':'a'}], 'count':2},
                                                     {'results':[{'id':'b'}], 'count':2}]) as get:
            self.assertEqual(len(client.fields(config, 'dataset')), 2)
            self.assertEqual(get.call_args_list[1].args[1]['offset'], 1)

    @patch.dict(os.environ, {'BRAIN_EMAIL':'test','BRAIN_PASSWORD':'test'})
    def test_simulation_poll_and_origin(self):
        client=Brain();client.session=Mock()
        client.session.post.return_value=Mock(ok=True,headers={'Location':'/simulations/1'})
        client.session.get.side_effect=[Mock(ok=True,headers={},json=lambda:{'alpha':'abc'}),
                                       Mock(ok=True,json=lambda:{'id':'abc','is':{'sharpe':2}})]
        self.assertEqual(client.simulate('rank(close)',{})['id'],'abc')
        client.session.post.return_value=Mock(ok=True,headers={'Location':'https://evil.example/path'})
        with self.assertRaises(RemoteError):client.simulate('rank(close)',{})

    @patch.dict(os.environ, {'LLM_BASE_URL':'https://example.com/v1','LLM_API_KEY':'test','LLM_MODEL':'test'})
    def test_llm_schema(self):
        client=LLM()
        with patch.object(client,'ask',return_value='```json\n["rank(close)"]\n```'):
            self.assertEqual(client.candidates(['close'],'','',1),['rank(close)'])
        with patch.object(client,'ask',return_value='{"expression":"close"}'):
            with self.assertRaises(RemoteError):client.candidates(['close'],'','',1)

    @patch.dict(os.environ, dict(BRAIN_EMAIL='test',BRAIN_PASSWORD='test',LLM_BASE_URL='https://example.com/v1',LLM_API_KEY='test',LLM_MODEL='test'))
    def test_bounded_workflow_and_inheritance(self):
        with tempfile.TemporaryDirectory() as d:
            data=Path(d);config={'instrumentType':'EQUITY','region':'USA','delay':1,'universe':'TOP3000','decay':7}
            (data/'fields.json').write_text(json.dumps({'scope':{k:config[k] for k in ('instrumentType','region','delay','universe')},'digest':'test','fields':[{'id':'close','type':'MATRIX'}]}))
            args=SimpleNamespace(max_simulations=2,max_llm_calls=1,workers=1,idea='test',enhance=True,reflect=False)
            with patch('brain_miner.cli.DATA',data), patch('brain_miner.cli.LLM') as llm, patch('brain_miner.cli.simulate_job') as sim:
                llm.return_value.candidates.return_value=['rank(close)']
                sim.return_value=({'id':'test','is':{'sharpe':2,'fitness':1.5,'turnover':.1}},None)
                run(args,config)
                self.assertEqual(sim.call_count,2)
            s=Store(data/'research.db');rows=s.rows();s.close()
            self.assertTrue(any(r['parent'] for r in rows))
            self.assertTrue(all(json.loads(r['settings'])['decay']==7 for r in rows))


if __name__=='__main__':unittest.main()
