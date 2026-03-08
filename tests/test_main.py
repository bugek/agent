from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from ai_code_agent.config import AgentConfig
from ai_code_agent.main import parse_args, run_diagnostics
from ai_code_agent.metrics import build_diagnostics_summary, persist_diagnostics_summary, persist_execution_metrics


class MainCliTest(unittest.TestCase):
    def test_parse_args_supports_diagnose_command(self) -> None:
        args = parse_args([
            "diagnose",
            "--repo",
            "workspace",
            "--run-id",
            "run-123",
            "--recent",
            "7",
            "--status",
            "failed",
            "--failure-category",
            "validation",
            "--json",
        ])

        self.assertEqual(args.command, "diagnose")
        self.assertEqual(args.repo, "workspace")
        self.assertEqual(args.run_id, "run-123")
        self.assertEqual(args.recent, 7)
        self.assertEqual(args.status, "failed")
        self.assertEqual(args.failure_category, "validation")
        self.assertEqual(args.format, "text")
        self.assertTrue(args.json)

    def test_run_diagnostics_prints_latest_metrics_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-123",
                "workflow": {"status": "changes_required", "attempt_count": 2, "duration_ms": 1234, "terminal_node": "review"},
                "failures": {"primary_category": "validation"},
                "testing": {
                    "failed_commands": ["script:build"],
                    "total_duration_ms": 1100,
                    "validation_strategy": "targeted_retry",
                    "requested_retry_labels": ["script:build"],
                    "skipped_command_count": 2,
                    "command_reduction_rate": 0.67,
                    "slowest_command": {"label": "script:build", "duration_ms": 980, "exit_code": 1, "timed_out": False},
                },
                "review": {"status": "changes_required", "residual_risk_count": 2},
                "effectiveness": {"retry_attempted": True, "retry_recovered": False, "remediation_applied": True},
            }
            persist_execution_metrics(temp_dir, "run-123", metrics)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = run_diagnostics(AgentConfig(workspace_dir=temp_dir), None, None, 5, None, None, "text")

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue()
            self.assertIn("Run ID: run-123", rendered)
            self.assertIn("Metrics artifact: .ai-code-agent/runs/run-123/metrics.json", rendered)
            self.assertIn("Primary failure category: validation", rendered)
            self.assertIn("Validation strategy: targeted_retry", rendered)
            self.assertIn("Retry recovered: False", rendered)
            self.assertIn("Remediation applied: True", rendered)
            self.assertIn("Failed commands: script:build", rendered)
            self.assertIn("Requested retry labels: script:build", rendered)
            self.assertIn("Skipped commands on this pass: 2", rendered)
            self.assertIn("Command reduction rate: 0.67", rendered)
            self.assertIn("Slowest command: script:build (980 ms)", rendered)
            self.assertIn("Testing duration ms: 1100", rendered)

    def test_run_diagnostics_prints_json_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            metrics = {"schema_version": "execution-metrics/v1", "run_id": "run-123"}
            persist_execution_metrics(temp_dir, "run-123", metrics)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = run_diagnostics(AgentConfig(workspace_dir=temp_dir), temp_dir, "run-123", 5, None, None, "json")

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(output.getvalue()), metrics)

    def test_run_diagnostics_prints_recent_run_trend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-1",
                "workflow": {"status": "approved", "attempt_count": 1, "duration_ms": 100, "terminal_node": "review"},
                "failures": {"primary_category": None},
                "testing": {
                    "failed_commands": [],
                    "total_duration_ms": 40,
                    "validation_strategy": "full",
                    "skipped_command_count": 0,
                    "command_reduction_rate": 0.0,
                    "slowest_command": None,
                    "commands": [{"label": "compileall", "duration_ms": 40}],
                },
                "review": {"status": "approved", "residual_risk_count": 0},
                "effectiveness": {"retry_recovered": False},
            }
            second_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-2",
                "workflow": {"status": "approved", "attempt_count": 1, "duration_ms": 200, "terminal_node": "review"},
                "failures": {"primary_category": "generation"},
                "testing": {
                    "failed_commands": [],
                    "total_duration_ms": 80,
                    "validation_strategy": "full",
                    "skipped_command_count": 0,
                    "command_reduction_rate": 0.0,
                    "slowest_command": {"label": "script:lint", "duration_ms": 60},
                    "commands": [{"label": "script:lint", "duration_ms": 60}, {"label": "compileall", "duration_ms": 20}],
                },
                "review": {"status": "approved", "residual_risk_count": 1},
                "planning": {"edit_intent_count": 1},
                "coding": {"remediation_applied": True},
                "effectiveness": {"retry_recovered": False},
            }
            third_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-3",
                "workflow": {"status": "failed", "attempt_count": 2, "duration_ms": 300, "terminal_node": "test"},
                "failures": {"primary_category": "validation"},
                "testing": {
                    "failed_commands": ["script:test"],
                    "total_duration_ms": 200,
                    "validation_strategy": "targeted_retry",
                    "skipped_command_count": 3,
                    "command_reduction_rate": 0.6,
                    "slowest_command": {"label": "script:test", "duration_ms": 150},
                    "commands": [{"label": "script:test", "duration_ms": 150}, {"label": "compileall", "duration_ms": 50}],
                },
                "review": {"status": "changes_required", "residual_risk_count": 2},
                "planning": {"edit_intent_count": 1},
                "coding": {"remediation_applied": True},
                "effectiveness": {"retry_recovered": False},
            }
            persist_execution_metrics(temp_dir, "run-1", first_metrics)
            persist_execution_metrics(temp_dir, "run-2", second_metrics)
            persist_execution_metrics(temp_dir, "run-3", third_metrics)
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-1", "metrics.json"), (100, 100))
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-2", "metrics.json"), (200, 200))
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-3", "metrics.json"), (300, 300))
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = run_diagnostics(AgentConfig(workspace_dir=temp_dir), None, None, 3, None, None, "text")

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue()
            self.assertIn("Diagnostics summary artifact: .ai-code-agent/diagnostics/diagnose-recent-3.json", rendered)
            self.assertIn("Recent runs analyzed: 3", rendered)
            self.assertIn("Comparable runs: 3", rendered)
            self.assertIn("Approved runs: 2", rendered)
            self.assertIn("Failed runs: 1", rendered)
            self.assertIn("Aborted runs: 0", rendered)
            self.assertIn("Success rate: 0.67", rendered)
            self.assertIn("Average duration ms: 200", rendered)
            self.assertIn("Average testing duration ms: 106", rendered)
            self.assertIn("Validation strategies: full=2, targeted_retry=1", rendered)
            self.assertIn("Retry recovery: 0/1 (0.00)", rendered)
            self.assertIn("Remediation recovery: 1/2 (0.50)", rendered)
            self.assertIn("Edit intent recovery: 1/2 (0.50)", rendered)
            self.assertIn("Targeted retry savings: runs=1, approved=0, success_rate=0.00, skipped_commands=3, avg_skipped=3, avg_reduction_rate=0.60", rendered)
            self.assertIn("Primary failure categories: generation=1, validation=1", rendered)
            self.assertIn("Failure breakdown: generation(runs=1; commands=none; nodes=review=1); validation(runs=1; commands=script:test=1; nodes=test=1)", rendered)
            self.assertIn("Top terminal nodes: review=2, test=1", rendered)
            self.assertIn("Top failing commands: script:test=1", rendered)
            self.assertIn("Top slowest commands: script:test avg=150 max=150 count=1, script:lint avg=60 max=60 count=1, compileall avg=36 max=50 count=3", rendered)
            self.assertIn("Previous window runs compared: 2", rendered)
            self.assertIn("Previous window success rate: 1.00", rendered)
            self.assertIn("Window average duration delta ms: 150 (regressed)", rendered)
            self.assertIn("Window average testing duration delta ms: 140 (regressed)", rendered)
            self.assertIn("Window average attempt count delta: 1 (regressed)", rendered)
            self.assertIn("Window average residual risk delta: 2 (regressed)", rendered)
            self.assertIn("Latest status changed vs previous window: True", rendered)
            self.assertIn("Immediately previous run: run-2", rendered)
            self.assertIn("Immediate duration delta ms: 100 (regressed)", rendered)
            self.assertIn("Immediate testing duration delta ms: 120 (regressed)", rendered)
            self.assertIn("Immediate attempt count delta: 1 (regressed)", rendered)
            self.assertIn("Immediate residual risk delta: 1 (regressed)", rendered)
            self.assertIn("Latest status changed vs immediate previous run: True", rendered)
            self.assertIn("Latest primary failure category changed vs immediate previous run: True", rendered)
            self.assertIn("Recent run list:", rendered)
            self.assertIn("run-1: status=approved", rendered)
            self.assertIn("run-2: status=approved", rendered)
            self.assertIn("run-3: status=failed", rendered)

    def test_run_diagnostics_json_includes_recent_runs_and_trend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-1",
                "workflow": {"status": "approved", "attempt_count": 1, "duration_ms": 100, "terminal_node": "review"},
                "failures": {"primary_category": None},
                "testing": {
                    "failed_commands": [],
                    "total_duration_ms": 40,
                    "validation_strategy": "full",
                    "skipped_command_count": 0,
                    "command_reduction_rate": 0.0,
                    "slowest_command": None,
                    "commands": [{"label": "compileall", "duration_ms": 40}],
                },
                "review": {"status": "approved", "residual_risk_count": 0},
                "effectiveness": {"retry_recovered": False},
            }
            second_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-2",
                "workflow": {"status": "approved", "attempt_count": 1, "duration_ms": 200, "terminal_node": "review"},
                "failures": {"primary_category": "generation"},
                "testing": {
                    "failed_commands": [],
                    "total_duration_ms": 80,
                    "validation_strategy": "full",
                    "skipped_command_count": 0,
                    "command_reduction_rate": 0.0,
                    "slowest_command": {"label": "script:lint", "duration_ms": 60},
                    "commands": [{"label": "script:lint", "duration_ms": 60}, {"label": "compileall", "duration_ms": 20}],
                },
                "review": {"status": "approved", "residual_risk_count": 1},
                "planning": {"edit_intent_count": 1},
                "coding": {"remediation_applied": True},
                "effectiveness": {"retry_recovered": False},
            }
            third_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-3",
                "workflow": {"status": "failed", "attempt_count": 2, "duration_ms": 300, "terminal_node": "test"},
                "failures": {"primary_category": "validation"},
                "testing": {
                    "failed_commands": ["script:test"],
                    "total_duration_ms": 200,
                    "validation_strategy": "targeted_retry",
                    "skipped_command_count": 3,
                    "command_reduction_rate": 0.6,
                    "slowest_command": {"label": "script:test", "duration_ms": 150},
                    "commands": [{"label": "script:test", "duration_ms": 150}, {"label": "compileall", "duration_ms": 50}],
                },
                "review": {"status": "changes_required", "residual_risk_count": 2},
                "planning": {"edit_intent_count": 1},
                "coding": {"remediation_applied": True},
                "effectiveness": {"retry_recovered": False},
            }
            persist_execution_metrics(temp_dir, "run-1", first_metrics)
            persist_execution_metrics(temp_dir, "run-2", second_metrics)
            persist_execution_metrics(temp_dir, "run-3", third_metrics)
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-1", "metrics.json"), (100, 100))
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-2", "metrics.json"), (200, 200))
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-3", "metrics.json"), (300, 300))
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = run_diagnostics(AgentConfig(workspace_dir=temp_dir), None, None, 3, None, None, "json")

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["latest"]["run_id"], "run-3")
            self.assertEqual(payload["summary_path"], ".ai-code-agent/diagnostics/diagnose-recent-3.json")
            self.assertEqual(len(payload["recent_runs"]), 3)
            self.assertEqual(payload["trend"]["run_count"], 3)
            self.assertEqual(payload["trend"]["comparable_run_count"], 3)
            self.assertEqual(payload["trend"]["approved_count"], 2)
            self.assertEqual(payload["trend"]["aborted_count"], 0)
            self.assertEqual(payload["trend"]["effectiveness"]["retry_runs"], 1)
            self.assertEqual(payload["trend"]["effectiveness"]["remediation_runs"], 2)
            self.assertEqual(payload["trend"]["effectiveness"]["edit_intent_runs"], 2)
            self.assertEqual(payload["trend"]["effectiveness"]["targeted_retry_total_skipped_commands"], 3)
            self.assertEqual(payload["trend"]["failure_category_breakdown"]["generation"]["run_count"], 1)
            self.assertEqual(payload["trend"]["failure_category_breakdown"]["generation"]["terminal_nodes"][0], {"node": "review", "count": 1})
            self.assertEqual(payload["trend"]["failure_category_breakdown"]["validation"]["failing_commands"][0], {"label": "script:test", "count": 1})
            self.assertEqual(payload["trend"]["top_terminal_nodes"][0], {"node": "review", "count": 2})
            self.assertEqual(payload["trend"]["top_terminal_nodes"][1], {"node": "test", "count": 1})
            self.assertEqual(payload["trend"]["top_failing_commands"][0], {"label": "script:test", "count": 1})
            self.assertEqual(payload["trend"]["slowest_commands"][0]["label"], "script:test")
            self.assertEqual(payload["trend"]["slowest_commands"][0]["max_duration_ms"], 150)
            self.assertEqual(payload["trend"]["slowest_commands"][2]["label"], "compileall")
            self.assertEqual(payload["trend"]["slowest_commands"][2]["average_duration_ms"], 36)
            self.assertEqual(payload["trend"]["latest_vs_previous_window_average"]["previous_run_count"], 2)
            self.assertEqual(payload["trend"]["latest_vs_previous_window_average"]["duration_ms_delta"], 150)
            self.assertEqual(payload["trend"]["latest_vs_previous_window_average"]["duration_ms_direction"], "regressed")
            self.assertEqual(payload["trend"]["latest_vs_previous_window_average"]["testing_duration_ms_delta"], 140)
            self.assertEqual(payload["trend"]["latest_vs_previous_window_average"]["testing_duration_ms_direction"], "regressed")
            self.assertEqual(payload["trend"]["latest_vs_previous_window_average"]["status_changed"], True)
            self.assertEqual(payload["trend"]["latest_vs_immediately_previous_run"]["previous_run_id"], "run-2")
            self.assertEqual(payload["trend"]["latest_vs_immediately_previous_run"]["duration_ms_delta"], 100)
            self.assertEqual(payload["trend"]["latest_vs_immediately_previous_run"]["duration_ms_direction"], "regressed")
            self.assertEqual(payload["trend"]["latest_vs_immediately_previous_run"]["testing_duration_ms_delta"], 120)
            self.assertEqual(payload["trend"]["latest_vs_immediately_previous_run"]["testing_duration_ms_direction"], "regressed")
            self.assertEqual(payload["trend"]["latest_vs_immediately_previous_run"]["status_changed"], True)
            self.assertEqual(payload["trend"]["latest_vs_immediately_previous_run"]["primary_failure_category_changed"], True)

    def test_run_diagnostics_rows_include_effectiveness_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-rows",
                "workflow": {"status": "approved", "attempt_count": 2, "duration_ms": 150, "terminal_node": "review"},
                "failures": {"primary_category": None},
                "testing": {
                    "failed_commands": [],
                    "total_duration_ms": 50,
                    "validation_strategy": "targeted_retry",
                    "skipped_command_count": 2,
                    "command_reduction_rate": 0.5,
                    "slowest_command": None,
                    "commands": [],
                },
                "review": {"status": "approved", "residual_risk_count": 0},
                "effectiveness": {"retry_recovered": True},
            }
            persist_execution_metrics(temp_dir, "run-rows", metrics)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = run_diagnostics(AgentConfig(workspace_dir=temp_dir), None, None, 1, None, None, "rows")

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue()
            self.assertIn("run_id\tstatus\tprimary_failure\tvalidation_strategy\tretry_recovered\tskipped_command_count\tcommand_reduction_rate\tduration_ms\ttesting_duration_ms\tterminal_node\tpath", rendered)
            self.assertIn("run-rows\tapproved\t\ttargeted_retry\tTrue\t2\t0.5\t150\t50\treview\t.ai-code-agent/runs/run-rows/metrics.json", rendered)

    def test_run_diagnostics_skips_aborted_runs_in_comparison_baselines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            approved_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-1",
                "workflow": {"status": "approved", "attempt_count": 1, "duration_ms": 100, "terminal_node": "review"},
                "failures": {"primary_category": "generation"},
                "testing": {"failed_commands": [], "total_duration_ms": 20, "slowest_command": None},
                "review": {"status": "approved", "residual_risk_count": 0},
            }
            aborted_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-2",
                "workflow": {"status": "aborted", "attempt_count": 1, "duration_ms": 500, "terminal_node": "test"},
                "failures": {"primary_category": "policy"},
                "testing": {"failed_commands": [], "total_duration_ms": 0, "slowest_command": None},
                "review": {"status": None, "residual_risk_count": 0},
            }
            latest_failed_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-3",
                "workflow": {"status": "failed", "attempt_count": 2, "duration_ms": 300, "terminal_node": "test"},
                "failures": {"primary_category": "validation"},
                "testing": {"failed_commands": ["script:test"], "total_duration_ms": 200, "slowest_command": {"label": "script:test", "duration_ms": 150}},
                "review": {"status": "changes_required", "residual_risk_count": 2},
            }
            persist_execution_metrics(temp_dir, "run-1", approved_metrics)
            persist_execution_metrics(temp_dir, "run-2", aborted_metrics)
            persist_execution_metrics(temp_dir, "run-3", latest_failed_metrics)
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-1", "metrics.json"), (100, 100))
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-2", "metrics.json"), (200, 200))
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-3", "metrics.json"), (300, 300))
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = run_diagnostics(AgentConfig(workspace_dir=temp_dir), None, None, 3, None, None, "json")

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["trend"]["run_count"], 3)
            self.assertEqual(payload["trend"]["comparable_run_count"], 2)
            self.assertEqual(payload["trend"]["approved_count"], 1)
            self.assertEqual(payload["trend"]["failed_count"], 1)
            self.assertEqual(payload["trend"]["aborted_count"], 1)
            self.assertEqual(payload["trend"]["success_rate"], 0.5)
            self.assertEqual(payload["trend"]["average_duration_ms"], 200)
            self.assertEqual(payload["trend"]["average_testing_duration_ms"], 110)
            self.assertEqual(payload["trend"]["failure_category_breakdown"]["policy"]["run_count"], 1)
            self.assertEqual(payload["trend"]["failure_category_breakdown"]["validation"]["failing_commands"][0], {"label": "script:test", "count": 1})
            self.assertEqual(payload["trend"]["top_terminal_nodes"][0], {"node": "test", "count": 2})
            self.assertEqual(payload["trend"]["top_failing_commands"][0], {"label": "script:test", "count": 1})
            self.assertEqual(payload["trend"]["latest_vs_previous_window_average"]["previous_run_count"], 1)
            self.assertEqual(payload["trend"]["latest_vs_previous_window_average"]["duration_ms_delta"], 200)
            self.assertEqual(payload["trend"]["latest_vs_previous_window_average"]["duration_ms_direction"], "regressed")
            self.assertEqual(payload["trend"]["latest_vs_immediately_previous_run"]["previous_run_id"], "run-1")
            self.assertEqual(payload["trend"]["latest_vs_immediately_previous_run"]["duration_ms_direction"], "regressed")

    def test_run_diagnostics_aggregates_multiple_failing_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            metrics_one = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-1",
                "workflow": {"status": "failed", "attempt_count": 1, "duration_ms": 100, "terminal_node": "test"},
                "failures": {"primary_category": "validation"},
                "testing": {
                    "failed_commands": ["script:test", "compileall"],
                    "total_duration_ms": 100,
                    "slowest_command": {"label": "script:test", "duration_ms": 80},
                    "commands": [{"label": "script:test", "duration_ms": 80}, {"label": "compileall", "duration_ms": 20}],
                },
                "review": {"status": "changes_required", "residual_risk_count": 1},
            }
            metrics_two = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-2",
                "workflow": {"status": "failed", "attempt_count": 1, "duration_ms": 120, "terminal_node": "review"},
                "failures": {"primary_category": "validation"},
                "testing": {
                    "failed_commands": ["script:test"],
                    "total_duration_ms": 120,
                    "slowest_command": {"label": "script:test", "duration_ms": 90},
                    "commands": [{"label": "script:test", "duration_ms": 90}, {"label": "script:lint", "duration_ms": 30}],
                },
                "review": {"status": "changes_required", "residual_risk_count": 2},
            }
            persist_execution_metrics(temp_dir, "run-1", metrics_one)
            persist_execution_metrics(temp_dir, "run-2", metrics_two)
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-1", "metrics.json"), (100, 100))
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-2", "metrics.json"), (200, 200))
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = run_diagnostics(AgentConfig(workspace_dir=temp_dir), None, None, 2, "failed", None, "json")

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["trend"]["failure_category_breakdown"]["validation"]["run_count"], 2)
            self.assertEqual(payload["trend"]["failure_category_breakdown"]["validation"]["failing_commands"][0], {"label": "script:test", "count": 2})
            self.assertEqual(payload["trend"]["top_failing_commands"][0], {"label": "script:test", "count": 2})
            self.assertEqual(payload["trend"]["top_failing_commands"][1], {"label": "compileall", "count": 1})
            self.assertEqual(payload["trend"]["top_terminal_nodes"][0], {"node": "test", "count": 1})

    def test_run_diagnostics_filters_recent_runs_by_status_and_failure_category(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            approved_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-1",
                "workflow": {"status": "approved", "attempt_count": 1, "duration_ms": 100, "terminal_node": "review"},
                "failures": {"primary_category": "generation"},
                "testing": {"failed_commands": [], "total_duration_ms": 20, "slowest_command": None},
                "review": {"status": "approved", "residual_risk_count": 0},
            }
            failed_validation_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-2",
                "workflow": {"status": "failed", "attempt_count": 2, "duration_ms": 300, "terminal_node": "test"},
                "failures": {"primary_category": "validation"},
                "testing": {"failed_commands": ["script:test"], "total_duration_ms": 200, "slowest_command": {"label": "script:test", "duration_ms": 150}},
                "review": {"status": "changes_required", "residual_risk_count": 2},
            }
            failed_policy_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-3",
                "workflow": {"status": "failed", "attempt_count": 1, "duration_ms": 90, "terminal_node": "test"},
                "failures": {"primary_category": "policy"},
                "testing": {"failed_commands": [], "total_duration_ms": 0, "slowest_command": None},
                "review": {"status": "changes_required", "residual_risk_count": 1},
            }
            persist_execution_metrics(temp_dir, "run-1", approved_metrics)
            persist_execution_metrics(temp_dir, "run-2", failed_validation_metrics)
            persist_execution_metrics(temp_dir, "run-3", failed_policy_metrics)
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-1", "metrics.json"), (100, 100))
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-2", "metrics.json"), (200, 200))
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-3", "metrics.json"), (300, 300))
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = run_diagnostics(
                    AgentConfig(workspace_dir=temp_dir),
                    None,
                    None,
                    3,
                    "failed",
                    "validation",
                    "json",
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["filters"], {"status": "failed", "failure_category": "validation"})
            self.assertEqual(payload["summary_path"], ".ai-code-agent/diagnostics/diagnose-recent-3-status-failed-failure-validation.json")
            self.assertEqual(payload["latest"]["run_id"], "run-2")
            self.assertEqual(len(payload["recent_runs"]), 1)
            self.assertEqual(payload["trend"]["run_count"], 1)

    def test_run_diagnostics_reports_when_filters_match_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-1",
                "workflow": {"status": "approved", "attempt_count": 1, "duration_ms": 100, "terminal_node": "review"},
                "failures": {"primary_category": "generation"},
                "testing": {"failed_commands": [], "total_duration_ms": 20, "slowest_command": None},
                "review": {"status": "approved", "residual_risk_count": 0},
            }
            persist_execution_metrics(temp_dir, "run-1", metrics)
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-1", "metrics.json"), (100, 100))
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = run_diagnostics(
                    AgentConfig(workspace_dir=temp_dir),
                    None,
                    None,
                    3,
                    "failed",
                    "validation",
                    "text",
                )

            self.assertEqual(exit_code, 1)
            self.assertIn("matching status=failed failure_category=validation", output.getvalue())

    def test_run_diagnostics_supports_rows_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-1",
                "workflow": {"status": "failed", "attempt_count": 1, "duration_ms": 100, "terminal_node": "test"},
                "failures": {"primary_category": "validation"},
                "testing": {"failed_commands": ["compileall"], "total_duration_ms": 70, "slowest_command": None},
                "review": {"status": "changes_required", "residual_risk_count": 1},
            }
            persist_execution_metrics(temp_dir, "run-1", metrics)
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-1", "metrics.json"), (100, 100))
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = run_diagnostics(AgentConfig(workspace_dir=temp_dir), None, None, 1, "failed", None, "rows")

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue().splitlines()
            self.assertEqual(rendered[0], "run_id\tstatus\tprimary_failure\tvalidation_strategy\tretry_recovered\tskipped_command_count\tcommand_reduction_rate\tduration_ms\ttesting_duration_ms\tterminal_node\tpath")
            self.assertIn("run-1\tfailed\tvalidation\tfull\tFalse\t0\t0.0\t100\t70\ttest\t.ai-code-agent/runs/run-1/metrics.json", rendered[1])

    def test_run_diagnostics_supports_ndjson_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-1",
                "workflow": {"status": "failed", "attempt_count": 1, "duration_ms": 100, "terminal_node": "test"},
                "failures": {"primary_category": "validation"},
                "testing": {"failed_commands": ["compileall"], "total_duration_ms": 70, "slowest_command": None},
                "review": {"status": "changes_required", "residual_risk_count": 1},
            }
            second_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-2",
                "workflow": {"status": "failed", "attempt_count": 1, "duration_ms": 120, "terminal_node": "review"},
                "failures": {"primary_category": "policy"},
                "testing": {"failed_commands": [], "total_duration_ms": 0, "slowest_command": None},
                "review": {"status": "changes_required", "residual_risk_count": 0},
            }
            persist_execution_metrics(temp_dir, "run-1", first_metrics)
            persist_execution_metrics(temp_dir, "run-2", second_metrics)
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-1", "metrics.json"), (100, 100))
            os.utime(os.path.join(temp_dir, ".ai-code-agent", "runs", "run-2", "metrics.json"), (200, 200))
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = run_diagnostics(AgentConfig(workspace_dir=temp_dir), None, None, 2, "failed", None, "ndjson")

            self.assertEqual(exit_code, 0)
            lines = output.getvalue().splitlines()
            self.assertEqual(len(lines), 2)
            first_row = json.loads(lines[0])
            second_row = json.loads(lines[1])
            self.assertEqual(first_row["run_id"], "run-2")
            self.assertEqual(first_row["status"], "failed")
            self.assertEqual(second_row["run_id"], "run-1")

    def test_run_diagnostics_reuses_fresh_summary_snapshot_for_rows_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-1",
                "workflow": {"status": "failed", "attempt_count": 1, "duration_ms": 100, "terminal_node": "test"},
                "failures": {"primary_category": "validation"},
                "testing": {"failed_commands": ["compileall"], "total_duration_ms": 70, "slowest_command": None},
                "review": {"status": "changes_required", "residual_risk_count": 1},
            }
            metrics_path = persist_execution_metrics(temp_dir, "run-1", metrics)
            summary = build_diagnostics_summary(
                [(metrics, ".ai-code-agent/runs/run-1/metrics.json")],
                {"run_count": 1},
                recent=1,
                filters={"status": "failed", "failure_category": None},
            )
            summary_path = persist_diagnostics_summary(
                temp_dir,
                summary,
                recent=1,
                status="failed",
                failure_category=None,
            )
            assert metrics_path is not None
            assert summary_path is not None
            os.utime(os.path.join(temp_dir, metrics_path), (100, 100))
            os.utime(os.path.join(temp_dir, summary_path), (200, 200))
            output = io.StringIO()

            with patch("ai_code_agent.main.build_execution_metrics_trend", side_effect=AssertionError("should reuse summary")):
                with redirect_stdout(output):
                    exit_code = run_diagnostics(AgentConfig(workspace_dir=temp_dir), None, None, 1, "failed", None, "rows")

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue().splitlines()
            self.assertEqual(rendered[0], "run_id\tstatus\tprimary_failure\tvalidation_strategy\tretry_recovered\tskipped_command_count\tcommand_reduction_rate\tduration_ms\ttesting_duration_ms\tterminal_node\tpath")
            self.assertIn("run-1\tfailed\tvalidation\tfull\tFalse\t0\t0.0\t100\t70\ttest\t.ai-code-agent/runs/run-1/metrics.json", rendered[1])

    def test_run_diagnostics_reuses_fresh_summary_snapshot_for_json_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-1",
                "workflow": {"status": "approved", "attempt_count": 1, "duration_ms": 100, "terminal_node": "review"},
                "failures": {"primary_category": None},
                "testing": {"failed_commands": [], "total_duration_ms": 40, "slowest_command": None},
                "review": {"status": "approved", "residual_risk_count": 0},
            }
            second_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-2",
                "workflow": {"status": "failed", "attempt_count": 2, "duration_ms": 200, "terminal_node": "test"},
                "failures": {"primary_category": "validation"},
                "testing": {"failed_commands": ["script:test"], "total_duration_ms": 120, "slowest_command": {"label": "script:test", "duration_ms": 120}},
                "review": {"status": "changes_required", "residual_risk_count": 2},
            }
            path_one = persist_execution_metrics(temp_dir, "run-1", first_metrics)
            path_two = persist_execution_metrics(temp_dir, "run-2", second_metrics)
            summary = build_diagnostics_summary(
                [
                    (second_metrics, ".ai-code-agent/runs/run-2/metrics.json"),
                    (first_metrics, ".ai-code-agent/runs/run-1/metrics.json"),
                ],
                {"run_count": 2, "approved_count": 1, "failed_count": 1, "aborted_count": 0, "comparable_run_count": 2, "success_rate": 0.5},
                recent=2,
                filters={"status": None, "failure_category": None},
            )
            summary_path = persist_diagnostics_summary(
                temp_dir,
                summary,
                recent=2,
                status=None,
                failure_category=None,
            )
            assert path_one is not None
            assert path_two is not None
            assert summary_path is not None
            os.utime(os.path.join(temp_dir, path_one), (100, 100))
            os.utime(os.path.join(temp_dir, path_two), (200, 200))
            os.utime(os.path.join(temp_dir, summary_path), (300, 300))
            output = io.StringIO()

            with patch("ai_code_agent.main.build_execution_metrics_trend", side_effect=AssertionError("should reuse summary")):
                with redirect_stdout(output):
                    exit_code = run_diagnostics(AgentConfig(workspace_dir=temp_dir), None, None, 2, None, None, "json")

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["latest"]["run_id"], "run-2")
            self.assertEqual(payload["latest_path"], ".ai-code-agent/runs/run-2/metrics.json")
            self.assertEqual(payload["summary_path"], ".ai-code-agent/diagnostics/diagnose-recent-2.json")
            self.assertEqual(len(payload["recent_runs"]), 2)
            self.assertEqual(payload["recent_runs"][0]["metrics"]["run_id"], "run-2")
            self.assertEqual(payload["recent_runs"][1]["metrics"]["run_id"], "run-1")
            self.assertEqual(payload["trend"]["run_count"], 2)


if __name__ == "__main__":
    unittest.main()