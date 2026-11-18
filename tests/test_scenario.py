"""情景核算:同一窗口核对容量、时限、电价与收入,缺失证据不得补成正利润。"""
import json
import unittest
from compute_plan.evidence import connect, receive
from compute_plan.scenario import evaluate

WINDOW = '2026-10-05T12'
MONTH = '2026-10'
KNOWN = '2026-10-08T00:00:00+08:00'


def doc(kind, ident, payload, revision='r1', received='2026-10-06T12:00:00+08:00'):
    return {'kind': kind, 'id': ident, 'revision': revision, 'received_at': received, 'payload': payload}


def base_docs():
    return [
        doc('load', 'L1', {'window': WINDOW, 'region': 'east', 'class': 'interactive', 'requests': 80}),
        doc('pool', 'P1', {'window': WINDOW, 'region': 'east', 'capacity': 130}),
        doc('sla', 'S1', {'class': 'interactive', 'max_utilization': 0.7}),
        doc('price', 'E1', {'region': 'east', 'month': MONTH, 'energy_per_unit': 2, 'tariff': 0.8}),
        doc('facility', 'F1', {'region': 'east', 'month': MONTH, 'pue': 1.4, 'idle_reserve': 0.1, 'idle_draw': 0.6}),
        doc('contract', 'C1', {'region': 'east', 'class': 'interactive', 'month': MONTH, 'revenue_per_request': 6.0}),
    ]


class Scenario(unittest.TestCase):
    def setUp(self):
        self.db = connect()

    def tearDown(self):
        self.db.close()

    def load(self, docs):
        for item in docs:
            receive(self.db, item)

    def test_complete_window(self):
        self.load(base_docs())
        result = evaluate(self.db, 'no-such-plan', KNOWN)
        self.assertTrue(result['complete'])
        totals = result['totals']
        self.assertEqual(totals['requests'], 80)
        self.assertEqual(totals['served'], 80)
        self.assertEqual(totals['breached'], 0)
        used = 80 / 0.7
        energy = 2 * (used + 0.6 * (130 - used)) * 1.4 * 0.8
        self.assertAlmostEqual(totals['energy_cost'], energy)
        self.assertAlmostEqual(totals['revenue'], 480)
        self.assertAlmostEqual(totals['margin'], 480 - energy)

    def test_missing_contract_never_positive_profit(self):
        self.load([d for d in base_docs() if d['kind'] != 'contract'])
        result = evaluate(self.db, 'p', KNOWN)
        self.assertFalse(result['complete'])
        self.assertIsNone(result['totals']['revenue'])
        self.assertIsNone(result['totals']['margin'])
        self.assertIn('收入待核', json.dumps(result['pending'], ensure_ascii=False))
        self.assertEqual(result['uncertainty']['margin'], [None, None])

    def test_missing_facility_blocks_service_and_cost(self):
        self.load([d for d in base_docs() if d['kind'] != 'facility'])
        result = evaluate(self.db, 'p', KNOWN)
        self.assertIn('能耗待核', json.dumps(result['pending'], ensure_ascii=False))
        self.assertIsNone(result['totals']['served'])
        self.assertIsNone(result['totals']['energy_cost'])
        self.assertIsNone(result['totals']['margin'])

    def test_missing_pool(self):
        self.load([d for d in base_docs() if d['kind'] != 'pool'])
        result = evaluate(self.db, 'p', KNOWN)
        self.assertIn('容量待核', json.dumps(result['pending'], ensure_ascii=False))
        self.assertIsNone(result['totals']['margin'])

    def test_missing_price(self):
        self.load([d for d in base_docs() if d['kind'] != 'price'])
        result = evaluate(self.db, 'p', KNOWN)
        self.assertIn('电价待核', json.dumps(result['pending'], ensure_ascii=False))
        self.assertIsNone(result['totals']['cost'])
        self.assertIsNone(result['totals']['margin'])

    def test_sla_class_served_first(self):
        docs = [
            doc('load', 'L1', {'window': WINDOW, 'region': 'east', 'class': 'interactive', 'requests': 70}),
            doc('load', 'L2', {'window': WINDOW, 'region': 'east', 'class': 'batch', 'requests': 60}),
            doc('pool', 'P1', {'window': WINDOW, 'region': 'east', 'capacity': 110}),
            doc('sla', 'S1', {'class': 'interactive', 'max_utilization': 0.7}),
            doc('price', 'E1', {'region': 'east', 'month': MONTH, 'energy_per_unit': 2, 'tariff': 0.8}),
            doc('facility', 'F1', {'region': 'east', 'month': MONTH, 'pue': 1.4, 'idle_reserve': 0.1, 'idle_draw': 0.6}),
            doc('contract', 'C1', {'region': 'east', 'class': 'interactive', 'month': MONTH, 'revenue_per_request': 6.0}),
            doc('contract', 'C2', {'region': 'east', 'class': 'batch', 'month': MONTH, 'revenue_per_request': 2.0}),
        ]
        self.load(docs)
        result = evaluate(self.db, 'p', KNOWN)
        classes = {c['class']: c for w in result['windows'] for r in w['regions'] for c in r['classes']}
        self.assertAlmostEqual(classes['interactive']['served'], 99 * 0.7)
        self.assertEqual(classes['batch']['served'], 0)
        self.assertAlmostEqual(result['totals']['commitment_breached'], 70 - 99 * 0.7)

    def test_spillover_uses_cheapest_route_and_bills_transfer(self):
        docs = [
            doc('load', 'L1', {'window': WINDOW, 'region': 'east', 'class': 'interactive', 'requests': 100}),
            doc('load', 'L2', {'window': WINDOW, 'region': 'west', 'class': 'interactive', 'requests': 10}),
            doc('pool', 'P1', {'window': WINDOW, 'region': 'east', 'capacity': 100}),
            doc('pool', 'P2', {'window': WINDOW, 'region': 'west', 'capacity': 100}),
            doc('sla', 'S1', {'class': 'interactive', 'max_utilization': 0.7}),
            doc('price', 'E1', {'region': 'east', 'month': MONTH, 'energy_per_unit': 2, 'tariff': 0.8}),
            doc('price', 'E2', {'region': 'west', 'month': MONTH, 'energy_per_unit': 2, 'tariff': 0.5}),
            doc('facility', 'F1', {'region': 'east', 'month': MONTH, 'pue': 1.4, 'idle_reserve': 0.1, 'idle_draw': 0.6}),
            doc('facility', 'F2', {'region': 'west', 'month': MONTH, 'pue': 1.3, 'idle_reserve': 0.1, 'idle_draw': 0.6}),
            doc('contract', 'C1', {'region': 'east', 'class': 'interactive', 'month': MONTH, 'revenue_per_request': 6.0}),
            doc('contract', 'C2', {'region': 'west', 'class': 'interactive', 'month': MONTH, 'revenue_per_request': 5.0}),
            doc('route', 'R1', {'from_region': 'east', 'to_region': 'west', 'cost_per_request': 0.2}),
        ]
        self.load(docs)
        result = evaluate(self.db, 'p', KNOWN)
        regions = {r['region']: r for w in result['windows'] for r in w['regions']}
        east = regions['east']['classes'][0]
        self.assertAlmostEqual(east['served_local'], 90 * 0.7)
        self.assertAlmostEqual(east['spilled_out'], 100 - 90 * 0.7)
        self.assertAlmostEqual(east['breached'], 0)
        self.assertAlmostEqual(regions['east']['transfer_cost'], (100 - 90 * 0.7) * 0.2)
        self.assertAlmostEqual(regions['west']['spilled_in'], 100 - 90 * 0.7)
        self.assertAlmostEqual(east['revenue'], 100 * 6.0)

    def test_plan_purchase_cost_and_idle_energy(self):
        docs = base_docs() + [
            doc('plan', 'PL1', {'plan': 'grow', 'window': WINDOW, 'region': 'east', 'add_capacity': 100, 'unit_cost': 0.5}),
        ]
        self.load(docs)
        result = evaluate(self.db, 'grow', KNOWN)
        region = result['windows'][0]['regions'][0]
        self.assertEqual(region['capacity'], 230)
        self.assertAlmostEqual(region['plan_cost'], 50)
        used = 80 / 0.7
        energy = 2 * (used + 0.6 * (230 - used)) * 1.4 * 0.8
        self.assertAlmostEqual(region['energy_cost'], energy)
        self.assertAlmostEqual(region['margin'], 480 - energy - 50)

    def test_plan_without_unit_cost_is_procurement_pending(self):
        docs = base_docs() + [
            doc('plan', 'PL1', {'plan': 'grow', 'window': WINDOW, 'region': 'east', 'add_capacity': 100}),
        ]
        self.load(docs)
        result = evaluate(self.db, 'grow', KNOWN)
        self.assertIn('采购待核', json.dumps(result['pending'], ensure_ascii=False))
        self.assertIsNone(result['totals']['cost'])
        self.assertIsNone(result['totals']['margin'])

    def test_uncertainty_range(self):
        docs = base_docs()
        docs[0]['payload'].update({'requests_low': 70, 'requests_high': 90})
        docs[1]['payload']['capacity'] = 150  # 可用 135,0.7 上限下高区间 90 不被截断
        self.load(docs)
        result = evaluate(self.db, 'p', KNOWN)
        self.assertEqual(result['uncertainty']['requests'], [70, 90])
        self.assertEqual(result['uncertainty']['served'], [70, 90])
        lo, hi = result['uncertainty']['margin']
        self.assertLess(lo, result['totals']['margin'])
        self.assertGreater(hi, result['totals']['margin'])

    def test_invalid_range_rejected(self):
        docs = base_docs()
        docs[0]['payload']['requests_low'] = 100
        self.load(docs)
        with self.assertRaises(ValueError):
            evaluate(self.db, 'p', KNOWN)


if __name__ == '__main__':
    unittest.main()
