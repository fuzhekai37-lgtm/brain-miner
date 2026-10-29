import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys

from .clients import Brain, LLM
from .config import load_env, require, settings
from .research import metrics, passed, validate, variants, prune_pnl
from .store import Store

DATA = Path('data')


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def simulate_job(job):
    brain = Brain()
    try:
        brain.login()
        return brain.simulate(job['expression'], json.loads(job['settings'])), None
    except Exception as exc:
        # Remote response bodies and request objects never enter logs.
        from .clients import RemoteError
        return None, str(exc) if isinstance(exc, RemoteError) else type(exc).__name__
    finally:
        brain.close()


def run(args, config):
    cache = json.loads((DATA/'fields.json').read_text(encoding='utf-8'))
    scope = {k:config[k] for k in ('instrumentType','region','delay','universe')}
    if cache['scope'] != scope:
        raise ValueError('Field cache scope differs from settings. Run sync-fields again.')
    fields = [x['id'] for x in cache['fields'] if x.get('type') == 'MATRIX']
    if not fields:
        raise ValueError('No MATRIX fields accessible in the cache')
    require(['BRAIN_EMAIL','BRAIN_PASSWORD','LLM_BASE_URL','LLM_API_KEY','LLM_MODEL'])
    context = hashlib.sha256(json.dumps([config, cache['digest'], args.idea], sort_keys=True).encode()).hexdigest()
    llm = LLM()
    store = Store(DATA/'research.db')
    consumed = 0
    generation_calls = 0
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            while consumed < args.max_simulations:
                jobs = []
                for _ in range(min(args.workers, args.max_simulations-consumed)):
                    job = store.claim(context)
                    if job: jobs.append(job)
                if not jobs:
                    if generation_calls >= args.max_llm_calls:
                        break
                    generation_calls += 1
                    expressions = llm.candidates(fields, args.idea, store.latest_note(context), min(20,args.max_simulations-consumed))
                    added = 0
                    for expr in expressions:
                        if validate(expr, fields):
                            added += bool(store.add(expr, config, context))
                    print(f'Generated {len(expressions)}; admitted {added}')
                    if not added:
                        continue
                    continue
                consumed += len(jobs)  # Failed attempts also consume this run's budget.
                for job, (result, error) in zip(jobs, pool.map(simulate_job, jobs)):
                    store.finish(job['id'], result, error)
                    if error:
                        print(f'{job["id"][:10]} failed: {error}')
                        continue
                    print(f'{job["id"][:10]} {metrics(result)} local_pass={passed(result)}')
                    if args.enhance:
                        for expr in variants(job['expression'], result, job['stage']):
                            if validate(expr, fields):
                                store.add(expr, json.loads(job['settings']), context, 'enhancement', job['id'], job['stage']+1)
                if args.reflect and generation_calls < args.max_llm_calls:
                    evidence = [{'expression':r['expression'], 'result':metrics(json.loads(r['result'])) if r['result'] else None,
                                 'error':r['error']} for r in store.rows() if r['context']==context and r['status'] in ('done','failed')][-10:]
                    generation_calls += 1
                    note = llm.ask('Summarize research observations in under 200 words. Distinguish hypotheses from verified results; do not claim causal improvement.', json.dumps(evidence))
                    store.note(note[:4000], context)
        print(f'Finished: {consumed} simulation attempts, {generation_calls} LLM calls. Results: data/research.db')
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description='Brain miner: bounded, reproducible Alpha research')
    parser.add_argument('--settings', default='settings.json')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='Offline configuration checks; no credentials printed')
    sync = sub.add_parser('sync-fields', help='Authenticate and download your accessible field metadata')
    sync.add_argument('--dataset', required=True)
    mine = sub.add_parser('run', help='Consumes LLM and BRAIN quota; bounded by explicit budgets')
    mine.add_argument('--max-simulations', type=int, default=10)
    mine.add_argument('--max-llm-calls', type=int, default=3)
    mine.add_argument('--workers', type=int, default=1)
    mine.add_argument('--idea', default='Explore simple, economically interpretable stock selection signals')
    mine.add_argument('--enhance', action='store_true')
    mine.add_argument('--reflect', action='store_true')
    sub.add_parser('status')
    sub.add_parser('export', help='Write local result snapshot, including raw platform results')
    prune = sub.add_parser('prune', help='Offline correlation pruning of user-supplied dated daily returns')
    prune.add_argument('input')
    args = parser.parse_args()
    try:
        load_env()
        config = settings(args.settings)
        if args.command == 'doctor':
            require(['BRAIN_EMAIL','BRAIN_PASSWORD','LLM_BASE_URL','LLM_API_KEY','LLM_MODEL'])
            LLM()
            print('Configuration OK. Offline check only: authentication, permissions and model compatibility are not verified.')
        elif args.command == 'sync-fields':
            brain = Brain()
            try:
                brain.login()
                rows = brain.fields(config, args.dataset)
            finally:
                brain.close()
            if not rows:
                raise ValueError('No accessible fields returned; check dataset and account permissions')
            clean = [{k:r.get(k) for k in ('id','type','description')} for r in rows]
            digest = hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest()
            save(DATA/'fields.json', {'scope':{k:config[k] for k in ('instrumentType','region','delay','universe')},
                                     'dataset':args.dataset, 'digest':digest, 'fields':clean})
            print(f'Saved {len(clean)} fields to data/fields.json')
        elif args.command == 'run':
            if not 1 <= args.workers <= 3 or not 1 <= args.max_simulations <= 10000 or not 1 <= args.max_llm_calls <= 1000:
                raise ValueError('workers: 1–3; simulations: 1–10000; LLM calls: 1–1000')
            run(args,config)
        elif args.command == 'prune':
            output = prune_pnl(json.loads(Path(args.input).read_text(encoding='utf-8')))
            save(DATA/'pruned.json', output)
            print({k:len(v) for k,v in output.items()})
        else:
            store = Store(DATA/'research.db')
            try:
                rows = store.rows()
                if args.command == 'export':
                    save(DATA/'results.json', rows)
                    print('Exported to data/results.json (private research data; ignored by Git)')
                else:
                    from collections import Counter
                    print(dict(Counter(r['status'] for r in rows)))
                    print('running means recorded state, not verified live worker activity')
            finally:
                store.close()
    except KeyboardInterrupt:
        print('Interrupted. Inspect running jobs on BRAIN before resubmitting.', file=sys.stderr)
        return 130
    except Exception as exc:
        from .clients import RemoteError
        print(str(exc) if isinstance(exc,(ValueError,RemoteError,FileNotFoundError)) else type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
