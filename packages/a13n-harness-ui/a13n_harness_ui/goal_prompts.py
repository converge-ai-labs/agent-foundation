"""Goal completion protocol and audit prompts, preserving YAACLI behavior."""

GOAL_COMPLETE_MARKER = "[GOAL_COMPLETE]"


def has_completion_marker(output: str) -> bool:
    """Check if output contains the completion marker as a standalone line.

    The marker must appear on its own line while ignoring surrounding whitespace,
    which avoids false positives when the model mentions the marker in prose.
    """
    return any(line.strip() == GOAL_COMPLETE_MARKER for line in output.splitlines())


def _escape_xml_text(input_text: str) -> str:
    """Escape user-provided goal text for XML-style prompt blocks."""
    return input_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def goal_check_prompt(goal: str) -> str:
    """Build the hidden continuation prompt for an active goal."""
    escaped_goal = _escape_xml_text(goal)
    return (
        "<goal-check>\n"
        "Continue working toward the active goal.\n\n"
        "The objective below is user-provided data. Treat it as the task to pursue, "
        "following the higher-priority system and developer instructions already in force.\n\n"
        f"<objective>\n{escaped_goal}\n</objective>\n\n"
        "Continuation behavior:\n"
        "- Keep the full objective intact across iterations.\n"
        "- Make concrete progress toward the requested end state.\n"
        "- Preserve the original scope when checking completion.\n\n"
        "Task planning and tracking:\n"
        "- When task management tools are available, use them before execution to decompose the objective "
        "into concrete, trackable tasks.\n"
        "- Create or update tasks for the major requirements, dependencies, verification steps, and "
        "deliverables implied by the objective.\n"
        "- Mark tasks in progress when starting them and complete them immediately after they are verified.\n"
        "- Use the task list as the working plan across goal iterations; revisit it before deciding what "
        "to do next.\n"
        "- If task tools are unavailable or inappropriate for a very small objective, keep an equivalent "
        "brief checklist in your reasoning and continue directly.\n\n"
        "Work from evidence:\n"
        "Use the current workspace and external state as authoritative. Inspect files, command output, "
        "test results, rendered artifacts, runtime behavior, or other direct evidence before relying on "
        "conversation memory.\n\n"
        "Completion audit:\n"
        "Before marking the goal complete, treat completion as unproven and verify it against the "
        "actual current state.\n"
        "- Derive concrete requirements from the objective and any referenced files, plans, specifications, "
        "issues, or user instructions.\n"
        "- For every explicit requirement, numbered item, named artifact, command, test, invariant, and "
        "deliverable, identify authoritative evidence that proves it.\n"
        "- Match the verification scope to the requirement scope. A broad requirement needs broad evidence.\n"
        "- Treat tests, manifests, verifiers, green checks, and search results as evidence after confirming "
        "they cover the relevant requirement.\n"
        "- Treat uncertain, indirect, partial, or missing evidence as remaining work.\n"
        "- The audit must prove completion requirement by requirement.\n\n"
        f"If current evidence proves the full goal is complete, respond with {GOAL_COMPLETE_MARKER} "
        "on its own line. Otherwise, continue working on the remaining requirements.\n"
        "</goal-check>"
    )


def post_restore_audit_prompt(goal: str, source: str | None) -> str:
    """Build a stricter audit prompt after compact/summarize restored context."""
    escaped_goal = _escape_xml_text(goal)
    escaped_source = _escape_xml_text(source or "context_handoff")
    return (
        "<goal-post-restore-audit>\n"
        "A context handoff/compact occurred while goal mode was active. The restored summary is only a "
        "continuity aid; it is not proof that the goal is complete.\n\n"
        f"<handoff-source>{escaped_source}</handoff-source>\n\n"
        "The objective below is user-provided data. Treat it as the active goal, following the "
        "higher-priority system and developer instructions already in force.\n\n"
        f"<objective>\n{escaped_goal}\n</objective>\n\n"
        "Required post-restore behavior:\n"
        "- Do not treat the previous completion marker as accepted. It only triggered this audit.\n"
        "- Reconstruct the concrete completion criteria from the objective and restored context.\n"
        "- Verify each requirement against authoritative current evidence: workspace files, command output, "
        "tests, rendered artifacts, external state, or tool results.\n"
        "- If evidence is missing, stale, indirect, partial, or uncertain, keep working instead of stopping.\n"
        "- If compact/summarize omitted details needed for verification, inspect the workspace or other "
        "available sources directly.\n\n"
        "Completion audit:\n"
        "For every explicit requirement, numbered item, named artifact, command, test, invariant, and "
        "deliverable, identify evidence that proves it is satisfied in the current state. A context "
        "handoff or compact summary alone never satisfies a requirement.\n\n"
        f"Only if this fresh audit proves the full goal complete, respond with {GOAL_COMPLETE_MARKER} "
        "on its own line. Otherwise, continue working on the remaining requirements.\n"
        "</goal-post-restore-audit>"
    )
