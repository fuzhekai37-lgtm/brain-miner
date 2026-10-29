import math
import re

OPERATORS = {'rank','zscore','ts_mean','ts_sum','ts_delta','ts_delay','ts_rank',
             'ts_zscore','ts_std_dev','ts_decay_linear','add','subtract','multiply',
             'divide','abs','log','sqrt','power','reverse','group_neutralize','group_rank'}
GROUPS = {'market','sector','industry','subindustry'}


def validate(expression, fields):
    if not isinstance(expression, str) or not 1 <= len(expression) <= 500:
        return False
    if not re.fullmatch(r'[A-Za-z0-9_.,()\s+\-]+', expression):
        return False
    depth = 0
    for char in expression:
        depth += (char == '(') - (char == ')')
        if depth < 0:
            return False
    if depth:
        return False
    calls = set(re.findall(r'\b([A-Za-z_]\w*)\s*\(', expression))
    if not calls.issubset(OPERATORS):
        return False
    tokens = set(re.findall(r'\b[A-Za-z_]\w*\b', expression))
    return tokens.issubset(set(fields) | OPERATORS | GROUPS) and bool(tokens & set(fields))


def metrics(result):
    source = result.get('is') or {}
    return {k:source.get(k) for k in ('sharpe','fitness','turnover')}


def passed(result):
    m = metrics(result)
    if any(not isinstance(v,(float,int)) or not math.isfinite(v) for v in m.values()):
        return False
    return 1.25 < m['sharpe'] <= 5 and m['fitness'] > 1 and .01 < m['turnover'] < .7


def variants(expression, result, stage):
    """Explicit parent, inherited settings, at most two enhancement stages."""
    if stage >= 2:
        return []
    m = metrics(result)
    if not isinstance(m['sharpe'], (int,float)) or not math.isfinite(m['sharpe']) or abs(m['sharpe']) > 5:
        return []
    if m['sharpe'] < -1:
        return [f'reverse({expression})']
    if m['sharpe'] < .6:
        return []
    candidates = [f'ts_mean({expression}, {w})' for w in (5,20,60)]
    candidates += [f'rank({expression})', f'zscore({expression})', f'group_neutralize({expression}, industry)']
    return [x for x in candidates if len(x) <= 500]


def prune_pnl(records, threshold=.8):
    """Optional offline pruning; expects dated daily RETURNS, not cumulative PnL.
    Missing/short histories remain explicitly unknown; no claim of independence.
    """
    kept, removed, unknown = [], [], []
    for candidate in sorted(records, key=lambda x:x['sharpe'], reverse=True):
        series = candidate.get('returns', {})
        values = list(series.values())
        if (len(values) < 30 or
                not all(isinstance(x, (int, float)) and math.isfinite(x) for x in values) or
                len(set(values)) < 2):
            unknown.append(candidate); continue
        reject = False
        comparable = not kept
        for previous in kept:
            dates = sorted(set(series) & set(previous['returns']))
            if len(dates) < 30:
                continue
            a, b = [series[d] for d in dates], [previous['returns'][d] for d in dates]
            if not all(isinstance(x,(int,float)) and math.isfinite(x) for x in a+b):
                continue
            ma,mb = sum(a)/len(a),sum(b)/len(b)
            va,vb = sum((x-ma)**2 for x in a),sum((x-mb)**2 for x in b)
            if not va or not vb:
                continue
            comparable = True
            corr = sum((x-ma)*(y-mb) for x,y in zip(a,b))/math.sqrt(va*vb)
            if abs(corr) >= threshold:
                reject = True; break
        if reject: removed.append(candidate)
        elif comparable: kept.append(candidate)
        else: unknown.append(candidate)
    return {'kept':kept, 'removed':removed, 'unknown':unknown}
