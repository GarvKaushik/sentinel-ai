import unittest

from eval.batch import discover_incidents, run_correlator_coverage


class ScenarioCatalogueTests(unittest.TestCase):
    def test_catalogue_has_broad_incident_coverage(self):
        scenarios = discover_incidents()
        categories = {scenario.root_cause_category for scenario in scenarios}
        self.assertGreaterEqual(len(scenarios), 7)
        self.assertTrue({
            "bad_deploy", "db_connection_pool", "dependency_timeout", "config_drift",
            "resource_exhaustion", "traffic_spike_no_bug", "partial_rollback",
        } <= categories)

    def test_correlator_finds_declared_evidence_for_catalogue(self):
        summary = run_correlator_coverage()
        self.assertEqual(summary.mean_expected_evidence_recall, 1.0)
        self.assertTrue(all(not result.missing_refs for result in summary.results))


if __name__ == "__main__":
    unittest.main()
