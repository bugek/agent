from __future__ import annotations

import unittest
from pathlib import Path

from ai_code_agent.tools.code_search import CodeSearch


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "artifact" / "fixtures" / "retrieval-eval-sample"


class CodeSearchExplainCandidateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.search = CodeSearch(str(FIXTURE_DIR))

    def test_unknown_candidate_returns_empty_explanation(self) -> None:
        explanation = self.search.explain_candidate("missing/file.py", ["payment"], ["backend/payments/webhook.py"])

        self.assertEqual(
            explanation,
            {
                "file_path": "missing/file.py",
                "kind": "unknown",
                "explanation_edges": [],
                "reasons": [],
            },
        )

    def test_explain_candidate_reports_keyword_and_outgoing_seed_edges(self) -> None:
        explanation = self.search.explain_candidate(
            "backend/payments/webhook.py",
            ["payments", "webhook", "event"],
            ["backend/common/events.py", "backend/payments/service.py"],
        )

        self.assertEqual(explanation["file_path"], "backend/payments/webhook.py")
        self.assertEqual(explanation["kind"], "code")
        self.assertEqual(explanation["path_overlap"], ["payment", "webhook"])
        self.assertEqual(explanation["symbol_overlap"], [])
        self.assertEqual(explanation["import_overlap"], ["event", "payment"])
        self.assertEqual(
            explanation["reasons"],
            [
                "path token match: payment, webhook",
                "import token match: event, payment",
                "imports seed: backend/common/events.py, backend/payments/service.py",
                "defines symbol used by seed: backend/common/events.py, backend/payments/service.py",
            ],
        )

        edge_signatures = {
            (
                edge["edge_type"],
                edge.get("source_file"),
                edge["target_file"],
                edge["direction"],
                edge["depth"],
                edge.get("source_keyword"),
                edge.get("target_symbol"),
            )
            for edge in explanation["explanation_edges"]
        }
        self.assertIn(
            (
                "path_token_match",
                None,
                "backend/payments/webhook.py",
                "keyword_to_file",
                0,
                "payment",
                "payment",
            ),
            edge_signatures,
        )
        self.assertIn(
            (
                "import_token_match",
                None,
                "backend/payments/webhook.py",
                "keyword_to_import",
                0,
                "event",
                "event",
            ),
            edge_signatures,
        )
        self.assertIn(
            (
                "imports_seed",
                "backend/payments/webhook.py",
                "backend/common/events.py",
                "outgoing_import",
                1,
                None,
                None,
            ),
            edge_signatures,
        )
        self.assertIn(
            (
                "defines_symbol_used_by_seed",
                "backend/payments/webhook.py",
                "backend/payments/service.py",
                "outgoing_symbol_definition",
                1,
                None,
                None,
            ),
            edge_signatures,
        )
        self.assertEqual(len(edge_signatures), len(explanation["explanation_edges"]))

    def test_explain_candidate_reports_incoming_seed_edges(self) -> None:
        explanation = self.search.explain_candidate(
            "backend/common/events.py",
            ["payments", "webhook", "event"],
            ["backend/payments/webhook.py"],
        )

        self.assertEqual(explanation["path_overlap"], ["event"])
        self.assertEqual(
            explanation["reasons"],
            [
                "path token match: event",
                "imported by seed: backend/payments/webhook.py",
                "referenced by seed symbol usage: backend/payments/webhook.py",
            ],
        )

        edge_signatures = {
            (
                edge["edge_type"],
                edge.get("source_file"),
                edge["target_file"],
                edge["direction"],
                edge["depth"],
            )
            for edge in explanation["explanation_edges"]
        }
        self.assertIn(
            (
                "imported_by_seed",
                "backend/payments/webhook.py",
                "backend/common/events.py",
                "incoming_import",
                1,
            ),
            edge_signatures,
        )
        self.assertIn(
            (
                "referenced_by_seed_symbol_usage",
                "backend/payments/webhook.py",
                "backend/common/events.py",
                "incoming_symbol_reference",
                1,
            ),
            edge_signatures,
        )


if __name__ == "__main__":
    unittest.main()