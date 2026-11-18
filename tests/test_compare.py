"""方案对比:差异归因到证据版本变化或方案本身,待核指标差值为 None。"""
import unittest
from compute_plan.evidence import connect, receive
from compute_plan.compare import compare

WINDOW = '2026-10-05T20'
MONTH = '2026-10'
BEFORE = '2026-10-06T18:00:00+08:00'
AFTER = '2026-10-08T00:00:00+08:00'


def doc(kind, ident, payload, revision='r1', received='2026-10-06T12:00:00+08:00'):
    return {'kind': kind, 'id': ident, 'revision': revision, 'received_at': received, 'payload': payload}


def base_docs():
    return [
        doc('load', 'L1', {'window': WINDOW, 'region': 'east', 'class': 'interactive',
                           'requests': 200, 'requests_low': 180, 'requests_high': 240}),
        doc('load', 'L1', {'window': WINDOW, 'region': 'east', 'class': 'interactive',
                           'requests': 260, 'requests_low': 240, 'requests_high': 300},
            revision='r2', received='2026-10-07T12:00:00+08:00'),
        doc('pool', 'P1', {'window': WINDOW, 'region': 'east', 'capacity': 130}),
        doc('sla', 'S1', {'class': 'interactive', 'max_utilization': 0.7}),
        doc('price', 'E1', {'region': 'east', 'month': MONTH, 'energy_per_unit': 2, 'tariff': 0.8}),
        doc('price', 'E1', {'region': 'east', 'month': MONTH, 'energy_per_unit': 2, 'tariff': 1.0},
            revision='r2', received='2026-10-07T09:00:00+08:00'),
        doc('facility', 'F1', {'region': 'east', 'month': MONTH, 'pue': 1.4, 'idle_reserve': 0.1, 'idle_draw': 0.6}),
        doc('contract', 'C1', {'region': 'east', 'class': 'interactive', 'month': MONTH, 'revenue_per_request': 6.0}),
        doc('plan', 'PA', {'plan': 'approved', 'window': WINDOW, 'region': 'east',
                           'add_capacity': 100, 'unit_cost': 0.5}, received='2026-10-06T14:00:00+08:00'),
        doc('plan', 'PC', {'plan': 'candidate', 'window': WINDOW, 'region': 'east',
                           'add_capacity': 200, 'unit_cost': 0.5}, received='2026-10-06T14:00:00+08:00'),
    ]


class Compare(unittest.TestCase):
    def setUp(self):
        self.db = connect()
        for item in base_docs():
            receive(self.db, item)

    def tearDown(self):
        self.db.close()

    def test_evidence_changes_listed(self):
        result = compare(self.db, 'approved', 'candidate', BEFORE, AFTER)
        changes = {(c['kind'], c['id'], c['before'], c['after']) for c in result['evidence_changes']}
        self.assertEqual(changes, {('load', 'L1', 'r1', 'r2'), ('price', 'E1', 'r1', 'r2')})

    def test_evidence_effect_isolates_forecast_and_tariff(self):
        result = compare(self.db, 'approved', 'candidate', BEFORE, AFTER)
        evidence = result['effects']['evidence']['delta']
        self.assertAlmostEqual(evidence['served'], 0)  # 容量受限,预测上调不增加已服务量
        self.assertAlmostEqual(evidence['breached'], 60)  # 200 -> 260
        self.assertGreater(evidence['cost'], 0)  # 电价 0.8 -> 1.0
        self.assertLess(evidence['margin'], 0)

    def test_plan_effect_isolates_capacity(self):
        result = compare(self.db, 'approved', 'candidate', BEFORE, AFTER)
        plan = result['effects']['plan']['delta']
        self.assertAlmostEqual(plan['served'], 90 * 0.7)  # 多 100 容量全部用于高峰
        self.assertGreater(plan['cost'], 0)
        self.assertGreater(plan['margin'], 0)
        total = result['effects']['total']['delta']
        evidence = result['effects']['evidence']['delta']
        for key in ('served', 'breached', 'cost', 'margin'):
            self.assertAlmostEqual(total[key], evidence[key] + plan[key])

    def test_same_plan_has_zero_plan_effect(self):
        result = compare(self.db, 'approved', 'approved', BEFORE, AFTER)
        for value in result['effects']['plan']['delta'].values():
            self.assertEqual(value, 0)

    def test_pending_evidence_nulls_delta(self):
        receive(self.db, doc('load', 'L2', {'window': WINDOW, 'region': 'east', 'class': 'batch', 'requests': 50},
                             received='2026-10-07T12:00:00+08:00'))
        result = compare(self.db, 'approved', 'candidate', BEFORE, AFTER)
        self.assertIsNotNone(result['approved_before']['totals']['margin'])
        self.assertIsNone(result['approved_after']['totals']['margin'])
        self.assertIsNone(result['effects']['evidence']['delta']['margin'])
        self.assertIsNone(result['effects']['total']['delta']['margin'])

    def test_uncertainty_width_reported(self):
        result = compare(self.db, 'approved', 'candidate', BEFORE, AFTER)
        widths = result['effects']['evidence']['uncertainty_width']
        self.assertIn('margin', widths)
        self.assertAlmostEqual(result['approved_after']['uncertainty']['breached'][1]
                               - result['approved_after']['uncertainty']['breached'][0], 60)


if __name__ == '__main__':
    unittest.main()
