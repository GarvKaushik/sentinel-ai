import unittest

from app.agents.postmortem import generate_postmortem, render_markdown, validate_postmortem
from app.schemas.evidence import EvidenceLedger, EvidenceObject, Hypothesis, SourceType
from data.scenarios.scenario_001_bad_deploy import scenario_001
from eval.harness import evaluate_investigation


class EvaluationAndPostmortemTests(unittest.TestCase):
    def setUp(self):
        self.ledger = EvidenceLedger(incident_id=scenario_001.scenario_id)
        self.ledger.add_evidence(EvidenceObject(
            claim="Payments error rate jumped.", source_type=SourceType.METRIC,
            source_ref="prometheus:error_rate_pct:svc-payments:2026-07-06T14:33:00Z",
            confidence=0.95, produced_by="test",
        ))
        self.ledger.add_evidence(EvidenceObject(
            claim="PaymentValidator threw NullPointerException.", source_type=SourceType.LOG,
            source_ref="log:svc-payments:line:4821", confidence=0.9, produced_by="test",
        ))
        self.ledger.add_evidence(EvidenceObject(
            claim="Payments commit preceded the incident.", source_type=SourceType.COMMIT,
            source_ref="commit:a1b2c3d", confidence=0.8, produced_by="test",
        ))
        self.ledger.hypotheses = [Hypothesis(
            hypothesis_id="hyp_1", description="The payments deploy introduced a null handling bug.",
            confidence=0.9, status="survived_critique",
            supporting_evidence_refs=[
                "prometheus:error_rate_pct:svc-payments:2026-07-06T14:33:00Z",
                "log:svc-payments:line:4821", "commit:a1b2c3d",
            ],
        )]

    def test_postmortem_is_fully_cited(self):
        report = generate_postmortem(self.ledger)
        self.assertEqual(validate_postmortem(report, self.ledger), [])
        self.assertIn("Evidence:", render_markdown(report))

    def test_evaluation_counts_valid_citations_and_expected_evidence(self):
        result = evaluate_investigation(scenario_001, self.ledger)
        self.assertEqual(result.citation_precision, 1.0)
        self.assertEqual(result.root_cause_evidence_recall, 1.0)
        self.assertIsNone(result.critic_effective)


if __name__ == "__main__":
    unittest.main()
