import json
import re
from pathlib import Path, PurePosixPath

from ai_code_agent.agents.base import BaseAgent, is_analysis_only_request
from ai_code_agent.orchestrator import AgentState
from ai_code_agent.llm.prompts import REVIEWER_SYSTEM_PROMPT

class ReviewerAgent(BaseAgent):
    """
    Agent responsible for evaluating the code changes and test results.
    """
    
    def run(self, state: AgentState) -> dict:
        """
        Reviews patches and test output to decide if the code is ready for PR.
        """
        analysis_only = is_analysis_only_request(state["issue_description"])
        patches = state.get("patches", [])
        changed_files = sorted({patch.get("file") for patch in patches if patch.get("file")})
        validation_signals = self._extract_validation_signals(state.get("test_results", ""))
        comments: list[str] = []
        frontend_findings = self._frontend_behavior_findings(patches, state["issue_description"])

        if not state.get("test_passed", False):
            comments.append("Smoke tests failed.")

        if not patches and not analysis_only:
            comments.append("No code changes were produced for a change-oriented request.")

        if state.get("error_message"):
            comments.append(state["error_message"])

        review_payload = {
            "issue": state["issue_description"],
            "test_results": state.get("test_results", ""),
            "patch_count": len(patches),
            "changed_files": changed_files,
            "validation_signals": validation_signals,
            "frontend_findings": frontend_findings,
            "version_resolution": self._version_resolution(state),
            "dependency_changes": self._dependency_changes(patches),
            "visual_review": self._review_payload_visual_review(state.get("visual_review")),
            "codegen_summary": state.get("codegen_summary", {}),
            "analysis_only": analysis_only,
            "tasks": self._review_tasks(state),
        }
        llm_review = self._safe_llm_review(review_payload)
        comments.extend(self._normalize_comments(llm_review.get("review_comments", [])))
        comments.extend([item["comment"] for item in frontend_findings])
        comments.extend(self._visual_review_comments(state.get("visual_review"), analysis_only))

        review_approved = state.get("test_passed", False) and (analysis_only or bool(patches))
        if not analysis_only and "review_approved" in llm_review:
            review_approved = review_approved and bool(llm_review["review_approved"])
        if not analysis_only and frontend_findings:
            review_approved = False
        if not analysis_only and self._visual_review_has_blockers(state.get("visual_review")):
            review_approved = False

        failed_task_ids = self._compute_failed_task_ids(
            state, validation_signals, changed_files, frontend_findings, review_approved, llm_review,
        )
        failed_task_ids, satisfied_task_ids = self._filter_satisfied_task_failures(state, failed_task_ids)
        comments = self._prune_satisfied_task_comments(comments, satisfied_task_ids)
        if (
            not analysis_only
            and state.get("test_passed", False)
            and bool(patches)
            and not failed_task_ids
            and not frontend_findings
            and not self._visual_review_has_blockers(state.get("visual_review"))
        ):
            review_approved = True

        if not comments:
            comments.append("Review passed.")

        task_remediation = self._build_task_remediation(
            state,
            validation_signals,
            changed_files,
            llm_review,
            failed_task_ids,
            frontend_findings,
            state.get("visual_review"),
            review_approved,
            analysis_only,
        )

        review_summary = self._build_review_summary(
            changed_files,
            validation_signals,
            state.get("visual_review"),
            state.get("codegen_summary", {}),
            comments,
            review_approved,
            analysis_only,
            task_remediation,
        )
        review_summary["failed_task_ids"] = failed_task_ids

        return {
            "review_approved": review_approved,
            "review_comments": comments,
            "review_summary": review_summary,
            "failed_task_ids": failed_task_ids,
            "task_remediation": task_remediation,
        }

    def _safe_llm_review(self, review_payload: dict[str, object]) -> dict[str, object]:
        try:
            return self.llm.generate_json(REVIEWER_SYSTEM_PROMPT, json.dumps(review_payload, indent=2))
        except Exception:
            return {
                "review_approved": True,
                "review_comments": ["Reviewer LLM request failed; using deterministic fallback review."],
            }

    def _review_payload_visual_review(self, visual_review: object) -> dict[str, object] | None:
        if not isinstance(visual_review, dict):
            return None

        payload = dict(visual_review)
        responsive_review = dict(visual_review.get("responsive_review") or {})
        if payload.get("screenshot_status") != "passed":
            responsive_review["missing_categories"] = []
            responsive_review["missing_viewport_metadata"] = []
            responsive_review["passed"] = True
        payload["responsive_review"] = responsive_review
        return payload

    def _frontend_behavior_findings(self, patches: list[dict], issue_description: str) -> list[dict[str, object]]:
        lowered_issue = issue_description.lower()
        allow_demo_content = bool(re.search(r"\b(sample|demo|mock|placeholder|example)\b", lowered_issue))
        findings: list[dict[str, object]] = []

        for patch in patches:
            if not isinstance(patch, dict):
                continue
            file_path = patch.get("file") if isinstance(patch.get("file"), str) else ""
            diff = patch.get("diff") if isinstance(patch.get("diff"), str) else ""
            normalized_path = file_path.replace("\\", "/")
            added_lines = [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
            added_block = "\n".join(added_lines)
            lowered_added_block = added_block.lower()

            if normalized_path == "package.json":
                visual_script_match = re.search(r'"(visual-review|screenshot|test:visual)"\s*:\s*"([^"]+)"', added_block)
                visual_script_command = visual_script_match.group(2).strip() if visual_script_match else ""
                if visual_script_command and (
                    re.search(r'^(?:echo|printf|true|:|noop)\b', visual_script_command)
                    or (
                        re.search(r'\becho\b', visual_script_command)
                        and not re.search(r'(scripts/visual-review\.mjs|playwright|screenshot|test:visual)', visual_script_command)
                    )
                    or (
                        "npm run build" in visual_script_command
                        and not re.search(r'(scripts/visual-review\.mjs|playwright|screenshot)', visual_script_command)
                    )
                ):
                    findings.append({
                        "comment": "package.json weakens frontend visual-review coverage by replacing a screenshot script with a stub.",
                        "blocker_type": "visual_review_regression",
                        "focus_areas": [normalized_path],
                        "guidance": "Restore a real visual-review, screenshot, or test:visual command instead of a stubbed script.",
                    })
                continue

            if not normalized_path.endswith((".ts", ".tsx", ".js", ".jsx")):
                continue

            telemetry_matches = re.findall(
                r'"(?:\$\d+[A-Za-z]*|\d+(?:\.\d+)?%|\+\d+(?:\.\d+)?\s*[A-Za-z]+|\d+\s+open|\d+\s+waiting|\d+m\s*\d+s|\d+(?:\.\d+)?d)"',
                added_block,
            )
            if telemetry_matches and not allow_demo_content and not re.search(r"\b(sample|demo|mock|placeholder|example)\b", lowered_added_block):
                findings.append({
                    "comment": f"{normalized_path} hardcodes authoritative-looking telemetry without labeling it as demo or sample data.",
                    "blocker_type": "misleading_ui_data",
                    "focus_areas": [normalized_path],
                    "guidance": "Replace fabricated live metrics with clearly labeled sample content or wire the surface to real data.",
                })

            has_fixed_canvas_width = "minwidth: \"44rem\"" in lowered_added_block or "minwidth: \"40rem\"" in lowered_added_block
            has_persistent_two_column_grid = bool(re.search(r'gridtemplatecolumns:\s*"minmax\(0,\s*2fr\)\s+minmax\(', lowered_added_block))
            mentions_mobile_scroll = "scrolls horizontally on smaller screens" in lowered_added_block
            if has_fixed_canvas_width and has_persistent_two_column_grid and mentions_mobile_scroll:
                findings.append({
                    "comment": f"{normalized_path} appears to rely on fixed-width overflow instead of adapting the graph layout for narrow screens.",
                    "blocker_type": "missing_responsive_design",
                    "focus_areas": [normalized_path],
                    "guidance": "Add a narrow-screen layout that stacks or reflows the canvas and sidebar instead of depending only on horizontal scrolling.",
                })

            if "{error.message}" in added_block:
                findings.append({
                    "comment": f"{normalized_path} exposes raw runtime error details directly in the user-facing UI.",
                    "blocker_type": "unsafe_error_exposure",
                    "focus_areas": [normalized_path],
                    "guidance": "Show stable recovery copy in the error state and avoid rendering raw exception messages to end users.",
                })

        deduplicated: list[dict[str, object]] = []
        seen_keys: set[tuple[str, str]] = set()
        for item in findings:
            comment = item.get("comment") if isinstance(item.get("comment"), str) else ""
            blocker_type = item.get("blocker_type") if isinstance(item.get("blocker_type"), str) else ""
            key = (comment, blocker_type)
            if not comment or key in seen_keys:
                continue
            seen_keys.add(key)
            deduplicated.append(item)
        return deduplicated

    def _extract_validation_signals(self, test_results: str) -> list[dict[str, object]]:
        signals: list[dict[str, object]] = []
        for match in re.finditer(r"^([A-Za-z0-9:_-]+)\(exit=(\d+)\):", test_results or "", re.M):
            signals.append({
                "label": match.group(1),
                "exit_code": int(match.group(2)),
            })
        return signals

    def _normalize_comments(self, raw_comments: object) -> list[str]:
        if isinstance(raw_comments, str):
            return [raw_comments]
        if isinstance(raw_comments, list):
            return [comment for comment in raw_comments if isinstance(comment, str) and comment.strip()]
        return []

    def _version_resolution(self, state: AgentState) -> dict[str, object] | None:
        planning_context = state.get("planning_context") if isinstance(state.get("planning_context"), dict) else {}
        version_resolution = planning_context.get("version_resolution")
        return version_resolution if isinstance(version_resolution, dict) else None

    def _dependency_changes(self, patches: list[dict]) -> dict[str, dict[str, str]]:
        changes: dict[str, dict[str, str]] = {}
        for patch in patches:
            if not isinstance(patch, dict) or patch.get("file") != "package.json":
                continue
            diff = patch.get("diff") if isinstance(patch.get("diff"), str) else ""
            removed: dict[str, str] = {}
            added: dict[str, str] = {}
            for line in diff.splitlines():
                if line.startswith("---") or line.startswith("+++"):
                    continue
                match = re.search(r'^[+-]\s*"([^"]+)":\s*"([^"]+)"', line)
                if not match:
                    continue
                package_name = match.group(1)
                version = match.group(2)
                if package_name not in {"next", "react", "react-dom"}:
                    continue
                if line.startswith("-"):
                    removed[package_name] = version
                elif line.startswith("+"):
                    added[package_name] = version
            for package_name in sorted(set(removed) | set(added)):
                changes[package_name] = {
                    "before": removed.get(package_name, ""),
                    "after": added.get(package_name, ""),
                }
        return changes

    def _visual_review_comments(self, visual_review: object, analysis_only: bool) -> list[str]:
        if analysis_only or not isinstance(visual_review, dict) or not visual_review.get("enabled"):
            return []

        state_coverage = visual_review.get("state_coverage") or {}
        requires_route_state_coverage = bool(visual_review.get("requires_route_state_coverage", True))
        comments: list[str] = []
        if requires_route_state_coverage:
            missing_states = [
                state_name
                for state_name, covered in state_coverage.items()
                if state_name in {"loading_state", "empty_state", "error_state", "success_state"} and not covered
            ]
            if missing_states:
                comments.append(f"Frontend visual review is missing component states: {', '.join(sorted(missing_states))}.")

            if not state_coverage.get("loading_file"):
                comments.append("Frontend visual review did not find a loading.tsx/loading.ts companion file for the changed route.")
            if not state_coverage.get("error_file"):
                comments.append("Frontend visual review did not find an error.tsx/error.ts companion file for the changed route.")

        screenshot_status = visual_review.get("screenshot_status")
        if screenshot_status == "failed":
            comments.append("Frontend screenshot or visual-review command failed.")
        elif screenshot_status == "missing_artifacts":
            comments.append("Frontend screenshot command completed without producing any screenshot artifacts or manifest metadata.")
        elif screenshot_status == "not_configured":
            comments.append("Frontend screenshot review is not configured; relying on structural visual checks only.")

        responsive_review = visual_review.get("responsive_review") or {}
        missing_categories = responsive_review.get("missing_categories") or []
        if screenshot_status == "passed" and missing_categories:
            comments.append(
                f"Frontend visual review is missing responsive viewport coverage for: {', '.join(missing_categories)}."
            )

        missing_viewport_metadata = responsive_review.get("missing_viewport_metadata") or []
        if screenshot_status == "passed" and missing_viewport_metadata:
            comments.append("Frontend visual review produced screenshots without viewport metadata, so responsive coverage could not be verified.")

        return comments

    def _visual_review_has_blockers(self, visual_review: object) -> bool:
        if not isinstance(visual_review, dict) or not visual_review.get("enabled"):
            return False
        state_coverage = visual_review.get("state_coverage") or {}
        requires_route_state_coverage = bool(visual_review.get("requires_route_state_coverage", True))
        required_flags = ["loading_state", "empty_state", "error_state", "success_state", "loading_file", "error_file"]
        if requires_route_state_coverage and any(not state_coverage.get(flag) for flag in required_flags):
            return True
        responsive_review = visual_review.get("responsive_review") or {}
        if visual_review.get("screenshot_status") == "passed":
            if responsive_review.get("missing_categories"):
                return True
            if responsive_review.get("missing_viewport_metadata"):
                return True
        return visual_review.get("screenshot_status") in {"failed", "missing_artifacts"}

    def _review_tasks(self, state: dict) -> list[dict[str, object]]:
        planning_context = state.get("planning_context") if isinstance(state.get("planning_context"), dict) else {}
        tasks = planning_context.get("tasks") if isinstance(planning_context.get("tasks"), list) else []
        return [
            task for task in tasks
            if isinstance(task, dict) and isinstance(task.get("id"), str)
        ]

    def _compute_failed_task_ids(
        self,
        state: dict,
        validation_signals: list[dict[str, object]],
        changed_files: list[str],
        frontend_findings: list[dict[str, object]],
        review_approved: bool,
        llm_review: dict[str, object],
    ) -> list[str]:
        if review_approved:
            return []

        tasks = self._review_tasks(state)
        if not tasks:
            return []

        llm_failed = llm_review.get("failed_task_ids")
        llm_failed_set: set[str] = set()
        if isinstance(llm_failed, list):
            llm_failed_set = {
                tid for tid in llm_failed if isinstance(tid, str) and tid.strip()
            }

        failed_labels = {
            signal.get("label")
            for signal in validation_signals
            if isinstance(signal.get("exit_code"), int) and signal["exit_code"] != 0
        }
        changed_file_set = set(changed_files)

        deterministic_failed: set[str] = set()
        frontend_focus_areas = {
            focus.replace("\\", "/")
            for finding in frontend_findings
            for focus in finding.get("focus_areas", [])
            if isinstance(focus, str) and focus
        }
        for task in tasks:
            task_id = task.get("id")
            if not isinstance(task_id, str):
                continue
            acceptance_checks = task.get("acceptance_checks") or []
            target_files = task.get("target_files") or []
            if any(check in failed_labels for check in acceptance_checks if isinstance(check, str)):
                has_overlap = not target_files or any(
                    tf.replace("\\", "/") in changed_file_set
                    for tf in target_files
                    if isinstance(tf, str)
                )
                if has_overlap:
                    deterministic_failed.add(task_id)
            normalized_targets = {
                tf.replace("\\", "/")
                for tf in target_files
                if isinstance(tf, str) and tf
            }
            if frontend_focus_areas and normalized_targets.intersection(frontend_focus_areas):
                deterministic_failed.add(task_id)

        all_failed = llm_failed_set | deterministic_failed
        valid_ids = {task.get("id") for task in tasks if isinstance(task.get("id"), str)}
        return sorted(tid for tid in all_failed if tid in valid_ids)

    def _filter_satisfied_task_failures(self, state: dict, failed_task_ids: list[str]) -> tuple[list[str], set[str]]:
        tasks = {
            task.get("id"): task
            for task in self._review_tasks(state)
            if isinstance(task, dict) and isinstance(task.get("id"), str)
        }
        remaining: list[str] = []
        satisfied: set[str] = set()
        for task_id in failed_task_ids:
            task = tasks.get(task_id)
            if isinstance(task, dict) and self._task_is_satisfied_by_workspace(state, task):
                satisfied.add(task_id)
                continue
            remaining.append(task_id)
        return remaining, satisfied

    def _task_is_satisfied_by_workspace(self, state: dict, task: dict[str, object]) -> bool:
        target_files = {
            target.replace("\\", "/")
            for target in task.get("target_files", [])
            if isinstance(target, str) and target
        }
        title_goal = " ".join(
            part for part in [task.get("title"), task.get("goal")]
            if isinstance(part, str) and part.strip()
        ).lower()
        if target_files.issubset({"package.json", "package-lock.json"}) and re.search(r"dependency|lockfile|package|react\s*flow|reactflow", title_goal):
            return self._workspace_package_lock_is_synchronized(state)
        return False

    def _workspace_package_lock_is_synchronized(self, state: dict) -> bool:
        workspace_dir = state.get("workspace_dir")
        if not isinstance(workspace_dir, str) or not workspace_dir:
            return False
        package_json_path = Path(workspace_dir) / "package.json"
        package_lock_path = Path(workspace_dir) / "package-lock.json"
        if not package_json_path.exists() or not package_lock_path.exists():
            return False
        try:
            package_data = json.loads(package_json_path.read_text(encoding="utf-8"))
            package_lock_data = json.loads(package_lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False

        package_dependencies = package_data.get("dependencies") if isinstance(package_data.get("dependencies"), dict) else {}
        packages = package_lock_data.get("packages") if isinstance(package_lock_data.get("packages"), dict) else {}
        root_package = packages.get("") if isinstance(packages.get(""), dict) else {}
        lock_dependencies = root_package.get("dependencies") if isinstance(root_package.get("dependencies"), dict) else {}
        if not package_dependencies:
            return False
        return all(
            isinstance(name, str)
            and isinstance(version, str)
            and lock_dependencies.get(name) == version
            for name, version in package_dependencies.items()
        )

    def _prune_satisfied_task_comments(self, comments: list[str], satisfied_task_ids: set[str]) -> list[str]:
        if not satisfied_task_ids:
            return comments
        pruned: list[str] = []
        for comment in comments:
            lowered = comment.lower()
            if "package-lock.json" in lowered or "lockfile" in lowered:
                continue
            pruned.append(comment)
        return pruned

    def _build_review_summary(
        self,
        changed_files: list[str],
        validation_signals: list[dict[str, object]],
        visual_review: object,
        codegen_summary: object,
        comments: list[str],
        review_approved: bool,
        analysis_only: bool,
        task_remediation: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "status": "approved" if review_approved else "changes_required",
            "changed_areas": self._changed_areas(changed_files),
            "validation": self._validation_summary(validation_signals),
            "visual_review": self._visual_review_summary(visual_review),
            "residual_risks": self._residual_risks(codegen_summary, comments, review_approved, analysis_only),
            "remediation": self._remediation_summary(
                changed_files,
                validation_signals,
                codegen_summary,
                comments,
                review_approved,
                analysis_only,
                task_remediation,
            ),
        }

    def _normalize_task_remediation(
        self,
        raw_task_remediation: object,
        valid_task_ids: set[str],
    ) -> dict[str, dict[str, object]]:
        normalized: dict[str, dict[str, object]] = {}
        if not isinstance(raw_task_remediation, list):
            return normalized

        for item in raw_task_remediation:
            if not isinstance(item, dict):
                continue
            task_id = item.get("task_id") if isinstance(item.get("task_id"), str) else item.get("id")
            if not isinstance(task_id, str) or not task_id.strip() or task_id.strip() not in valid_task_ids:
                continue
            normalized_item: dict[str, object] = {"task_id": task_id.strip()}
            blocker_types = [
                blocker
                for blocker in item.get("blocker_types", [])
                if isinstance(blocker, str) and blocker.strip()
            ] if isinstance(item.get("blocker_types"), list) else []
            if blocker_types:
                normalized_item["blocker_types"] = blocker_types
            for key in ["failed_validation_labels", "focus_areas", "guidance", "blocked_file_paths", "failed_operations"]:
                value = item.get(key)
                if isinstance(value, list):
                    normalized_values = [entry for entry in value if isinstance(entry, str) and entry.strip()]
                    if normalized_values:
                        normalized_item[key] = normalized_values
            normalized[task_id.strip()] = normalized_item
        return normalized

    def _build_task_remediation(
        self,
        state: dict,
        validation_signals: list[dict[str, object]],
        changed_files: list[str],
        llm_review: dict[str, object],
        failed_task_ids: list[str],
        frontend_findings: list[dict[str, object]],
        visual_review: object,
        review_approved: bool,
        analysis_only: bool,
    ) -> list[dict[str, object]]:
        if review_approved or analysis_only:
            return []

        tasks = self._review_tasks(state)
        if not tasks:
            return []

        valid_task_ids = {
            task.get("id")
            for task in tasks
            if isinstance(task, dict) and isinstance(task.get("id"), str) and task.get("id")
        }
        llm_task_remediation = self._normalize_task_remediation(llm_review.get("task_remediation"), valid_task_ids)
        task_order = [
            task.get("id")
            for task in tasks
            if isinstance(task.get("id"), str) and task.get("id")
        ]
        failed_validation_labels = [
            signal.get("label")
            for signal in validation_signals
            if isinstance(signal.get("label"), str)
            and isinstance(signal.get("exit_code"), int)
            and signal["exit_code"] != 0
        ]
        codegen_summary = state.get("codegen_summary") if isinstance(state.get("codegen_summary"), dict) else {}
        blocked_file_paths = [
            item.get("file_path").replace("\\", "/")
            for item in codegen_summary.get("blocked_operations") or []
            if isinstance(item, dict) and isinstance(item.get("file_path"), str) and item.get("file_path")
        ]
        failed_operations = [
            item for item in codegen_summary.get("failed_operations") or [] if isinstance(item, str) and item
        ]
        changed_file_set = {file_path.replace("\\", "/") for file_path in changed_files if isinstance(file_path, str)}
        visual_blocker_types = self._visual_blocker_types(visual_review)

        remediation_items: list[dict[str, object]] = []
        for task in tasks:
            task_id = task.get("id")
            if not isinstance(task_id, str) or task_id not in failed_task_ids:
                continue

            target_files = [
                file_path.replace("\\", "/")
                for file_path in task.get("target_files", []) or []
                if isinstance(file_path, str) and file_path
            ]
            acceptance_checks = {
                label
                for label in task.get("acceptance_checks", []) or []
                if isinstance(label, str) and label
            }
            task_failed_labels = [label for label in failed_validation_labels if label in acceptance_checks]
            task_blocked_paths = [path for path in blocked_file_paths if not target_files or path in target_files]
            task_failed_operations = [
                item for item in failed_operations
                if not target_files or any(target_file in item for target_file in target_files)
            ]
            changed_targets = self._matching_target_paths(target_files, changed_file_set)
            task_validation_blockers = self._validation_blocker_types(task_failed_labels)
            related_frontend_findings = [
                finding
                for finding in frontend_findings
                if any(
                    isinstance(focus, str) and focus.replace("\\", "/") in set(target_files + changed_targets)
                    for focus in finding.get("focus_areas", [])
                )
            ]

            blocker_types: list[str] = []
            for blocker_type in task_validation_blockers:
                if blocker_type not in blocker_types:
                    blocker_types.append(blocker_type)
            if task_failed_operations:
                blocker_types.append("operation_failure")
            if task_blocked_paths:
                blocker_types.append("policy_block")
            if self._task_has_visual_surface(target_files, changed_targets):
                for blocker_type in visual_blocker_types:
                    if blocker_type not in blocker_types:
                        blocker_types.append(blocker_type)
            for finding in related_frontend_findings:
                blocker_type = finding.get("blocker_type")
                if isinstance(blocker_type, str) and blocker_type not in blocker_types:
                    blocker_types.append(blocker_type)
            if target_files and not changed_targets and not task_failed_labels and not task_failed_operations and not task_blocked_paths:
                blocker_types.append("missing_implementation")

            llm_entry = llm_task_remediation.get(task_id, {})
            for blocker in llm_entry.get("blocker_types", []):
                if isinstance(blocker, str) and blocker not in blocker_types:
                    blocker_types.append(blocker)

            focus_areas = list(dict.fromkeys(
                [
                    *changed_targets,
                    *task_blocked_paths,
                    *target_files[:3],
                    *[
                        file_path
                        for file_path in llm_entry.get("focus_areas", [])
                        if isinstance(file_path, str) and file_path
                    ],
                    *[
                        file_path.replace("\\", "/")
                        for finding in related_frontend_findings
                        for file_path in finding.get("focus_areas", [])
                        if isinstance(file_path, str) and file_path
                    ],
                ]
            ))
            guidance = [
                item for item in llm_entry.get("guidance", [])
                if isinstance(item, str) and item.strip()
            ]
            guidance.extend(
                finding.get("guidance")
                for finding in related_frontend_findings
                if isinstance(finding.get("guidance"), str) and finding.get("guidance")
            )
            if not guidance:
                if "type_error" in blocker_types:
                    guidance.append(f"Fix the type-checking failures for this task: {', '.join(task_failed_labels)}.")
                elif "build_breakage" in blocker_types:
                    guidance.append(f"Repair the build or compile failures for this task: {', '.join(task_failed_labels)}.")
                elif "test_failure" in blocker_types:
                    guidance.append(f"Repair the failing tests for this task: {', '.join(task_failed_labels)}.")
                elif task_failed_labels:
                    guidance.append(f"Repair failing validation for this task: {', '.join(task_failed_labels)}.")
                if task_failed_operations:
                    guidance.append("Fix the failed code-generation operations affecting this task.")
                if task_blocked_paths:
                    guidance.append("Resolve blocked edit targets or narrow the task scope for this task.")
                if "missing_state_coverage" in blocker_types:
                    guidance.append("Add the missing loading, empty, error, or success states for this task's UI surface.")
                if "missing_responsive_coverage" in blocker_types:
                    guidance.append("Add the missing responsive viewport coverage for this task's UI surface.")
                if "missing_responsive_design" in blocker_types:
                    guidance.append("Refine the UI layout so it adapts to narrow screens instead of depending on fixed-width overflow.")
                if "visual_artifact_failure" in blocker_types:
                    guidance.append("Repair the screenshot or visual review artifact generation for this task.")
                if "visual_review_regression" in blocker_types:
                    guidance.append("Restore real screenshot-backed visual review coverage instead of a stubbed validation script.")
                if "misleading_ui_data" in blocker_types:
                    guidance.append("Replace hardcoded operational telemetry with clearly labeled sample content or real data-backed values.")
                if "unsafe_error_exposure" in blocker_types:
                    guidance.append("Remove raw exception details from the user-facing error state and use stable recovery copy instead.")
                if "missing_implementation" in blocker_types:
                    guidance.append("Complete the intended edits for this task's target files.")

            remediation_item = {
                "task_id": task_id,
                "title": task.get("title"),
                "goal": task.get("goal"),
                "target_files": target_files,
                "blocker_types": blocker_types,
                "failed_validation_labels": list(dict.fromkeys(
                    task_failed_labels + [
                        label for label in llm_entry.get("failed_validation_labels", [])
                        if isinstance(label, str) and label
                    ]
                )),
                "blocked_file_paths": list(dict.fromkeys(
                    task_blocked_paths + [
                        file_path for file_path in llm_entry.get("blocked_file_paths", [])
                        if isinstance(file_path, str) and file_path
                    ]
                )),
                "failed_operations": list(dict.fromkeys(
                    task_failed_operations + [
                        item for item in llm_entry.get("failed_operations", [])
                        if isinstance(item, str) and item
                    ]
                )),
                "focus_areas": focus_areas,
                "guidance": guidance,
            }
            remediation_items.append(remediation_item)

        ordered_items: list[dict[str, object]] = []
        for task_id in task_order:
            for item in remediation_items:
                if item.get("task_id") == task_id:
                    ordered_items.append(item)
                    break
        return ordered_items

    def _validation_blocker_types(self, failed_labels: list[str]) -> list[str]:
        blocker_types: list[str] = []
        normalized_labels = [label.lower() for label in failed_labels if isinstance(label, str) and label]
        if any(re.search(r"(typecheck|typescript|noemit|mypy|pyright|pyre|ruff:check-types)", label) for label in normalized_labels):
            blocker_types.append("type_error")
        if any(re.search(r"(build|compile|webpack|vite|rollup)", label) for label in normalized_labels):
            blocker_types.append("build_breakage")
        if any(re.search(r"(^|:)(test|pytest|unit|integration|e2e)(:|$)", label) for label in normalized_labels):
            blocker_types.append("test_failure")
        if any(re.search(r"(lint|eslint|stylelint|ruff)", label) for label in normalized_labels):
            blocker_types.append("lint_failure")
        if failed_labels and not blocker_types:
            blocker_types.append("validation_failure")
        return blocker_types

    def _visual_blocker_types(self, visual_review: object) -> list[str]:
        if not isinstance(visual_review, dict) or not visual_review.get("enabled"):
            return []

        blocker_types: list[str] = []
        state_coverage = visual_review.get("state_coverage") or {}
        requires_route_state_coverage = bool(visual_review.get("requires_route_state_coverage", True))
        if requires_route_state_coverage:
            required_flags = ["loading_state", "empty_state", "error_state", "success_state", "loading_file", "error_file"]
            if any(not state_coverage.get(flag) for flag in required_flags):
                blocker_types.append("missing_state_coverage")

        responsive_review = visual_review.get("responsive_review") or {}
        if visual_review.get("screenshot_status") == "passed":
            if responsive_review.get("missing_categories") or responsive_review.get("missing_viewport_metadata"):
                blocker_types.append("missing_responsive_coverage")

        if visual_review.get("screenshot_status") in {"failed", "missing_artifacts"}:
            blocker_types.append("visual_artifact_failure")

        return blocker_types

    def _task_has_visual_surface(self, target_files: list[str], changed_targets: list[str]) -> bool:
        candidate_files = changed_targets or target_files
        for file_path in candidate_files:
            normalized = file_path.replace("\\", "/").lower()
            if normalized.endswith((".tsx", ".jsx", ".html", ".css", ".scss", ".sass", ".less", ".vue", ".svelte")):
                return True
            if any(part in normalized for part in ["/page.", "/layout.", "/loading.", "/error."]):
                return True
        return False

    def _matching_target_paths(self, target_files: list[str], changed_file_set: set[str]) -> list[str]:
        matched: list[str] = []
        for target_file in target_files:
            normalized_target = target_file.replace("\\", "/")
            if normalized_target in changed_file_set:
                matched.append(normalized_target)
                continue
            normalized_prefix = normalized_target.rstrip("/") + "/"
            if any(changed_file.startswith(normalized_prefix) for changed_file in changed_file_set):
                matched.append(normalized_target)
        return matched

    def _changed_areas(self, changed_files: list[str]) -> list[str]:
        areas: list[str] = []
        seen: set[str] = set()
        for file_path in changed_files:
            normalized = PurePosixPath(file_path.replace("\\", "/"))
            if len(normalized.parts) >= 3:
                area = "/".join(normalized.parts[:2])
            elif len(normalized.parts) == 2:
                area = "/".join(normalized.parts)
            elif normalized.parts:
                area = normalized.parts[0]
            else:
                continue
            if area not in seen:
                seen.add(area)
                areas.append(area)
        return areas

    def _validation_summary(self, validation_signals: list[dict[str, object]]) -> dict[str, list[str]]:
        passed: list[str] = []
        failed: list[str] = []
        for signal in validation_signals:
            label = signal.get("label")
            exit_code = signal.get("exit_code")
            if not isinstance(label, str) or not isinstance(exit_code, int):
                continue
            if exit_code == 0:
                passed.append(label)
            else:
                failed.append(label)
        return {"passed": passed, "failed": failed}

    def _visual_review_summary(self, visual_review: object) -> dict[str, object] | None:
        if not isinstance(visual_review, dict) or not visual_review.get("enabled"):
            return None
        state_coverage = visual_review.get("state_coverage") or {}
        responsive_review = visual_review.get("responsive_review") or {}
        screenshot_status = visual_review.get("screenshot_status")
        requires_route_state_coverage = bool(visual_review.get("requires_route_state_coverage", True))
        missing_states = [
            state_name
            for state_name, covered in state_coverage.items()
            if requires_route_state_coverage
            and state_name in {"loading_state", "empty_state", "error_state", "success_state"}
            and not covered
        ]
        return {
            "screenshot_status": screenshot_status,
            "artifact_count": visual_review.get("artifact_count", 0),
            "requires_route_state_coverage": requires_route_state_coverage,
            "missing_states": sorted(missing_states),
            "responsive_categories": responsive_review.get("categories_present", []),
            "missing_responsive_categories": responsive_review.get("missing_categories", []) if screenshot_status == "passed" else [],
        }

    def _residual_risks(
        self,
        codegen_summary: object,
        comments: list[str],
        review_approved: bool,
        analysis_only: bool,
    ) -> list[str]:
        risks: list[str] = []
        if isinstance(codegen_summary, dict):
            blocked_operations = codegen_summary.get("blocked_operations") or []
            if blocked_operations:
                risks.append(f"{len(blocked_operations)} operation(s) were blocked by file edit policy.")

        for comment in comments:
            normalized = comment.strip()
            if not normalized or normalized == "Review passed.":
                continue
            if review_approved and not analysis_only and normalized.startswith("Looks ready for PR"):
                continue
            if normalized not in risks:
                risks.append(normalized)
        return risks

    def _remediation_summary(
        self,
        changed_files: list[str],
        validation_signals: list[dict[str, object]],
        codegen_summary: object,
        comments: list[str],
        review_approved: bool,
        analysis_only: bool,
        task_remediation: list[dict[str, object]],
    ) -> dict[str, object]:
        failed_validation_labels = [
            label
            for label in self._validation_summary(validation_signals)["failed"]
            if isinstance(label, str) and label
        ]
        blocked_file_paths: list[str] = []
        failed_operations: list[str] = []
        if isinstance(codegen_summary, dict):
            blocked_file_paths = [
                item.get("file_path")
                for item in codegen_summary.get("blocked_operations") or []
                if isinstance(item, dict) and isinstance(item.get("file_path"), str) and item.get("file_path")
            ]
            failed_operations = [
                item
                for item in codegen_summary.get("failed_operations") or []
                if isinstance(item, str) and item
            ]

        guidance = [
            comment.strip()
            for comment in comments
            if isinstance(comment, str)
            and comment.strip()
            and comment.strip() != "Review passed."
            and not comment.strip().startswith("Looks ready for PR")
        ]

        task_focus_areas = [
            file_path
            for item in task_remediation
            if isinstance(item, dict)
            for file_path in item.get("focus_areas", [])
            if isinstance(file_path, str) and file_path
        ]
        task_guidance = [
            entry
            for item in task_remediation
            if isinstance(item, dict)
            for entry in item.get("guidance", [])
            if isinstance(entry, str) and entry
        ]

        focus_areas = list(dict.fromkeys(changed_files[:5] + blocked_file_paths[:5] + task_focus_areas[:10]))
        return {
            "required": bool(not review_approved and not analysis_only),
            "failed_validation_labels": failed_validation_labels,
            "blocked_file_paths": blocked_file_paths,
            "failed_operations": failed_operations,
            "focus_areas": focus_areas,
            "guidance": list(dict.fromkeys(guidance + task_guidance)),
            "task_remediation": task_remediation,
        }
