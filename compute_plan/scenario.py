"""同一窗口内核对负载、时限、容量、电价与合同收入,给出可审计的情景结果。

证据规则:缺失的计量或收入证据记为待核并把相关金额置为 None,
不用估值补齐,因此缺失证据不会被补成正利润。
"""
from .evidence import at

LEVELS = ('low', 'expected', 'high')
_EPS = 1e-9


def evaluate(db, plan, known_at):
    """评估扩容方案:expected 结果加 low/high 请求区间下的不确定范围。"""
    runs = {level: _run(db, plan, known_at, level) for level in LEVELS}
    result = runs['expected']
    result['uncertainty'] = {
        key: [runs['low']['totals'][key], runs['high']['totals'][key]]
        for key in ('requests', 'served', 'breached', 'commitment_breached', 'cost', 'revenue', 'margin')
    }
    return result


def _run(db, plan, known_at, level):
    loads = _load_rows(db, known_at, level)
    pools = _by_region_window(at(db, 'pool', known_at))
    sla = _slas(db, known_at)
    prices = _prices(db, known_at)
    contracts = _contracts(db, known_at)
    facilities = _facilities(db, known_at)
    routes = sorted(at(db, 'route', known_at),
                    key=lambda d: (d['payload']['cost_per_request'], d['payload']['to_region']))
    for doc in routes:
        if doc['payload']['cost_per_request'] < 0:
            raise ValueError('调度单价不得为负: ' + doc['id'])
    additions = _plan_additions(db, plan, known_at)
    pending = []
    names = {row['window'] for row in loads} | {w for _, w in pools} | {w for _, w in additions}
    windows = [_window(window, loads, pools, additions, sla, prices, contracts, facilities, routes, pending)
               for window in sorted(names)]
    return {'plan': plan, 'known_at': known_at, 'level': level,
            'windows': windows, 'totals': _totals(windows), 'complete': not pending, 'pending': pending}


def _load_rows(db, known_at, level):
    rows = []
    for doc in at(db, 'load', known_at):
        p = doc['payload']
        base = p['requests']
        low = p.get('requests_low', base)
        high = p.get('requests_high', base)
        if not 0 <= low <= base <= high:
            raise ValueError('请求量区间无效: ' + doc['id'])
        rows.append({'window': p['window'], 'region': p['region'], 'class': p['class'],
                     'requests': {'low': low, 'expected': base, 'high': high}[level],
                     'source': doc['digest']})
    return rows


def _by_region_window(docs):
    return {(d['payload']['region'], d['payload']['window']): d for d in docs}


def _slas(db, known_at):
    out = {}
    for doc in at(db, 'sla', known_at):
        mu = doc['payload']['max_utilization']
        if not 0 < mu <= 1:
            raise ValueError('时限利用率上限须在 (0,1]: ' + doc['id'])
        out[doc['payload']['class']] = mu
    return out


def _prices(db, known_at):
    out = {}
    for doc in at(db, 'price', known_at):
        p = doc['payload']
        if p['energy_per_unit'] < 0 or p['tariff'] < 0:
            raise ValueError('电价资料不得为负: ' + doc['id'])
        out[(p['region'], p['month'])] = doc
    return out


def _contracts(db, known_at):
    out = {}
    for doc in at(db, 'contract', known_at):
        p = doc['payload']
        if p['revenue_per_request'] < 0:
            raise ValueError('合同单价不得为负: ' + doc['id'])
        out[(p['region'], p['class'], p['month'])] = doc
    return out


def _facilities(db, known_at):
    out = {}
    for doc in at(db, 'facility', known_at):
        p = doc['payload']
        if not (p['pue'] >= 1 and 0 <= p['idle_reserve'] < 1 and 0 <= p['idle_draw'] <= 1):
            raise ValueError('机房能耗参数无效: ' + doc['id'])
        out[(p['region'], p['month'])] = doc
    return out


def _plan_additions(db, plan, known_at):
    """{(region, window): {capacity, cost, sources, complete}};cost 仅在采购单价齐备时有效。"""
    out = {}
    for doc in at(db, 'plan', known_at):
        p = doc['payload']
        if p['plan'] != plan:
            continue
        if p['add_capacity'] < 0 or ('unit_cost' in p and p['unit_cost'] < 0):
            raise ValueError('扩容方案参数不得为负: ' + doc['id'])
        key = (p['region'], p['window'])
        entry = out.setdefault(key, {'capacity': 0.0, 'cost': 0.0, 'sources': [], 'complete': True})
        entry['capacity'] += p['add_capacity']
        entry['sources'].append(doc['digest'])
        if 'unit_cost' in p:
            entry['cost'] += p['add_capacity'] * p['unit_cost']
        else:
            entry['complete'] = False
    return out


def _priority(cls, sla):
    """有时限承诺的类别优先,再按利用率上限与类名排序,保证分配确定可复算。"""
    mu = sla.get(cls)
    return (0, mu, cls) if mu is not None else (1, 1.0, cls)


def _window(window, loads, pools, additions, sla, prices, contracts, facilities, routes, pending):
    month = window[:7]
    names = sorted({row['region'] for row in loads if row['window'] == window}
                   | {region for region, w in pools if w == window}
                   | {region for region, w in additions if w == window})
    states = {}
    for region in names:
        rows = [row for row in loads if row['window'] == window and row['region'] == region]
        states[region] = _allocate(window, month, region, rows, pools, additions, facilities, sla, pending)
    _spill(states, routes, sla)
    return {'window': window,
            'regions': [_region_result(window, month, region, states[region], prices, contracts, pending)
                        for region in names]}


def _allocate(window, month, region, rows, pools, additions, facilities, sla, pending):
    pool = pools.get((region, window))
    add = additions.get((region, window), {'capacity': 0.0, 'cost': 0.0, 'sources': [], 'complete': True})
    facility = facilities.get((region, month))
    st = {'pool': pool, 'facility': facility, 'add': add, 'classes': [],
          'capacity': None, 'reserve': None, 'usable': None, 'remaining': None,
          'used': None, 'spilled_in': 0.0, 'transfer_cost': 0.0, 'route_sources': []}
    if pool is None:
        pending.append({'window': window, 'region': region, 'kind': 'pool', 'status': '容量待核'})
    elif facility is None:
        # 容量已知但空闲冗余未知,服务能力与能耗都无法核对,不按比例估算
        st['capacity'] = pool['payload']['capacity'] + add['capacity']
        pending.append({'window': window, 'region': region, 'kind': 'facility', 'status': '能耗待核'})
    else:
        st['capacity'] = pool['payload']['capacity'] + add['capacity']
        st['reserve'] = st['capacity'] * facility['payload']['idle_reserve']
        st['usable'] = st['capacity'] - st['reserve']
        st['remaining'] = st['usable']
        for row in sorted(rows, key=lambda r: _priority(r['class'], sla)):
            mu = sla.get(row['class'], 1.0)
            take = min(st['remaining'], row['requests'] / mu)
            st['remaining'] -= take
            st['classes'].append({'class': row['class'], 'requests': row['requests'],
                                  'max_utilization': sla.get(row['class']),
                                  'commitment': row['class'] in sla,
                                  'served_local': take * mu, 'spilled_out': 0.0,
                                  'unmet': row['requests'] - take * mu, 'source': row['source']})
        st['used'] = st['usable'] - st['remaining']
        return st
    for row in sorted(rows, key=lambda r: _priority(r['class'], sla)):
        st['classes'].append({'class': row['class'], 'requests': row['requests'],
                              'max_utilization': sla.get(row['class']),
                              'commitment': row['class'] in sla,
                              'served_local': None, 'spilled_out': None,
                              'unmet': None, 'source': row['source']})
    return st


def _spill(states, routes, sla):
    """同窗口内把本地未满足的请求按转移单价从低到高调往有余量的地区,单次确定性的分配。"""
    usage = {}
    for region in sorted(states):
        st = states[region]
        if st['remaining'] is None:
            continue
        for cls_row in st['classes']:
            mu = sla.get(cls_row['class'], 1.0)
            for route in routes:
                if cls_row['unmet'] <= _EPS:
                    break
                p = route['payload']
                if p['from_region'] != region:
                    continue
                target = states.get(p['to_region'])
                if target is None or target['remaining'] is None or target['remaining'] <= _EPS:
                    continue
                limit = p.get('max_requests')
                allowed = cls_row['unmet'] if limit is None else min(
                    cls_row['unmet'], max(0.0, limit - usage.get(route['digest'], 0.0)))
                units = min(target['remaining'], allowed / mu)
                if units <= _EPS:
                    continue
                moved = units * mu
                cls_row['unmet'] -= moved
                cls_row['spilled_out'] += moved
                target['remaining'] -= units
                target['used'] += units
                target['spilled_in'] += moved
                usage[route['digest']] = usage.get(route['digest'], 0.0) + moved
                st['transfer_cost'] += moved * p['cost_per_request']
                if route['digest'] not in st['route_sources']:
                    st['route_sources'].append(route['digest'])


def _region_result(window, month, region, st, prices, contracts, pending):
    sources = list(st['add']['sources']) + list(st['route_sources'])
    if st['pool'] is not None:
        sources.append(st['pool']['digest'])
    if st['facility'] is not None:
        sources.append(st['facility']['digest'])
    classes = []
    revenues = []
    for row in st['classes']:
        served = None if row['served_local'] is None else row['served_local'] + row['spilled_out']
        revenue = None
        contract = contracts.get((region, row['class'], month))
        if row['requests'] > 0:
            if contract is None:
                pending.append({'window': window, 'region': region, 'kind': 'contract',
                                'status': '收入待核', 'class': row['class']})
            elif served is not None:
                revenue = served * contract['payload']['revenue_per_request']
                sources.append(contract['digest'])
        else:
            revenue = 0.0
        revenues.append(revenue)
        classes.append({'class': row['class'], 'requests': row['requests'],
                        'max_utilization': row['max_utilization'], 'commitment': row['commitment'],
                        'served_local': row['served_local'], 'spilled_out': row['spilled_out'],
                        'served': served,
                        'breached': None if row['unmet'] is None else row['unmet'],
                        'revenue': revenue, 'source': row['source']})
    energy_cost = _energy_cost(window, month, region, st, prices, pending, sources)
    if not st['add']['complete']:
        pending.append({'window': window, 'region': region, 'kind': 'procurement', 'status': '采购待核'})
    plan_cost = st['add']['cost'] if st['add']['complete'] else None
    cost = None if energy_cost is None or plan_cost is None else energy_cost + plan_cost + st['transfer_cost']
    revenue = None if None in revenues else sum(revenues)
    margin = None if cost is None or revenue is None else revenue - cost
    return {'region': region, 'capacity': st['capacity'], 'reserve': st['reserve'],
            'usable': st['usable'], 'used_units': st['used'], 'spilled_in': st['spilled_in'],
            'classes': classes, 'energy_cost': energy_cost, 'transfer_cost': st['transfer_cost'],
            'plan_cost': plan_cost, 'cost': cost, 'revenue': revenue, 'margin': margin,
            'sources': sources}


def _energy_cost(window, month, region, st, prices, pending, sources):
    if st['capacity'] is None or st['used'] is None:
        return None  # 容量或机房能耗证据待核,已在 pending 记录
    price = prices.get((region, month))
    if price is None:
        pending.append({'window': window, 'region': region, 'kind': 'price', 'status': '电价待核'})
        return None
    f = st['facility']['payload']
    p = price['payload']
    idle = st['capacity'] - st['used']
    energy = p['energy_per_unit'] * (st['used'] + f['idle_draw'] * idle) * f['pue']
    sources.append(price['digest'])
    return energy * p['tariff']


def _totals(windows):
    """汇总时 None 传播:任何一项证据待核,对应合计即为 None,不用部分合计冒充利润。"""
    totals = {'requests': 0.0, 'served': 0.0, 'breached': 0.0, 'commitment_breached': 0.0,
              'energy_cost': 0.0, 'transfer_cost': 0.0, 'plan_cost': 0.0,
              'cost': 0.0, 'revenue': 0.0, 'margin': 0.0}
    unknown = set()
    for window in windows:
        for region in window['regions']:
            for key in ('energy_cost', 'transfer_cost', 'plan_cost', 'cost', 'revenue', 'margin'):
                _add(totals, unknown, key, region[key])
            for cls_row in region['classes']:
                _add(totals, unknown, 'requests', cls_row['requests'])
                _add(totals, unknown, 'served', cls_row['served'])
                _add(totals, unknown, 'breached', cls_row['breached'])
                if cls_row['commitment']:
                    _add(totals, unknown, 'commitment_breached', cls_row['breached'])
    for key in unknown:
        totals[key] = None
    return totals


def _add(totals, unknown, key, value):
    if value is None:
        unknown.add(key)
    elif key not in unknown:
        totals[key] += value
