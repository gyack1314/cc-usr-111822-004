"""对比已批准扩容方案与新方案,并把差异归因到证据版本变化或方案本身。

归因方法:同一已批准方案在 before/after 两个知识时点各算一次,差值即证据
(预测、电价等版本变化)的影响;同一 after 证据下新方案与已批准方案的差值
即方案本身的影响。缺失证据不补正:任一侧待核的指标差值为 None。
"""
from .evidence import at
from .scenario import evaluate

KINDS = ('load', 'pool', 'price', 'sla', 'contract', 'facility', 'route', 'plan')
METRICS = ('requests', 'served', 'breached', 'commitment_breached',
           'energy_cost', 'transfer_cost', 'plan_cost', 'cost', 'revenue', 'margin')
RANGE_KEYS = ('served', 'breached', 'cost', 'revenue', 'margin')


def compare(db, approved, candidate, before, after):
    """返回两方案在证据版本变化前后的成本、服务能力与不确定范围差异及归因。"""
    approved_before = evaluate(db, approved, before)
    approved_after = evaluate(db, approved, after)
    candidate_after = evaluate(db, candidate, after)
    return {
        'plans': {'approved': approved, 'candidate': candidate},
        'known_at': {'before': before, 'after': after},
        'evidence_changes': _evidence_changes(db, before, after),
        'approved_before': _summary(approved_before),
        'approved_after': _summary(approved_after),
        'candidate_after': _summary(candidate_after),
        'effects': {
            'evidence': _effect(approved_before, approved_after),
            'plan': _effect(approved_after, candidate_after),
            'total': _effect(approved_before, candidate_after),
        },
    }


def _summary(result):
    return {key: result[key] for key in ('windows', 'totals', 'uncertainty', 'complete', 'pending')}


def _effect(x, y):
    return {'delta': {key: _sub(x['totals'][key], y['totals'][key]) for key in METRICS},
            'uncertainty_width': {key: _sub(_width(x['uncertainty'][key]), _width(y['uncertainty'][key]))
                                  for key in RANGE_KEYS}}


def _sub(a, b):
    return None if a is None or b is None else b - a


def _width(bounds):
    return _sub(bounds[0], bounds[1])


def _evidence_changes(db, before, after):
    """列出两个知识时点之间实际参与核算的资料版本变化,定位差异来源。"""
    changes = []
    for kind in KINDS:
        old = {d['id']: d for d in at(db, kind, before)}
        new = {d['id']: d for d in at(db, kind, after)}
        for ident in sorted(set(old) | set(new)):
            o, n = old.get(ident), new.get(ident)
            if (o and o['digest']) != (n and n['digest']):
                changes.append({'kind': kind, 'id': ident,
                                'before': o and o['revision'], 'after': n and n['revision']})
    return changes
