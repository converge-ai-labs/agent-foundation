import type { Schema } from "../../../shared/api";

/** Scenario prose, kept apart from the wire shapes that carry it. */

export const RICH_REPLY = `## Release readiness

Two gates are green, one is waiting on you.

| Gate | Status | Owner |
| :--- | :---: | ---: |
| Unit tests | Passed | CI |
| Migration dry-run | Passed | Service |
| Release marker | Pending approval | You |

\`\`\`python
def marker_path(release: str) -> Path:
    # Written only after an explicit approval
    return RELEASES / release / "approved-marker.txt"
\`\`\`

Ask me to write the marker and I will request approval first.`;

export const CHECKS_REPLY =
  "The cursor fold was skipping the last frame of a batch. I patched `applyFrame` in `src/stream.ts` to flush on the final frame and re-ran the suite: **42 passed**. Researcher found one prior incident (INC-118) with the same root cause.";

export const NOTES_REPLY = `Release notes for **2.4.0** are drafted:

- Stream cursor folds now flush the final frame of a batch (INC-118 regression).
- Migration dry-run covers the new \`run_attempt\` fence column.
- No public API changes.

The only step left is the release marker.`;

export const RESEARCH_REPLY =
  "One prior incident matches: **INC-118**, the same cursor fold dropping a terminal frame after a batch boundary. It was closed by flushing on the last frame, which is the patch applied here.";

export const INCIDENT_REPLY =
  "Folded INC-118 into the release plan: the researcher confirmed the same cursor fold, so the notes will name the incident it closes.";

export const TRIAGE_REPLY =
  "Ticket 4471 is a duplicate of 4402: both report the console losing the last streamed token. I drafted a reply pointing at the 2.4.0 fix, but I need to know which tone to use before sending.";

export const STREAM_PATCH = {
  filePath: "src/stream.ts",
  before: '    if (frame.kind === "event") apply(frame.event);',
  after: [
    '    if (frame.kind === "event") {',
    "      apply(frame.event);",
    "      if (frame.last) this.flush();",
    "    }",
  ].join("\n"),
};

export const MARKER_REPLY =
  "Marker written to `releases/2.4.0/approved-marker.txt`. The release check can find it now, and the notes above are ready to publish.";

export const ANSWER_REPLY =
  "Understood. I drafted the reply in that direction and left it in the ticket for a final read.";

export const MARKER_COMMAND =
  "echo approved > releases/2.4.0/approved-marker.txt";

/** The shape `pending-request.tsx` `approvalDetails` accepts. */
export function approvalPresentation(target: string): Schema["JsonValue"] {
  return {
    target,
    reason: "Tool policy requires approval.",
    risk: "high",
  };
}

/** The shape `transcript/questions.tsx` `readQuestions` accepts. */
export function questionPresentation(): Schema["JsonValue"] {
  return {
    questions: [
      {
        header: "Direction",
        question: "Which reply should I send to the reporter?",
        multiSelect: false,
        options: [
          {
            label: "Point at the 2.4.0 fix",
            description: "Short, links the changelog entry, closes the ticket.",
          },
          {
            label: "Ask for a trace first",
            description: "Slower, but confirms it is the same root cause.",
          },
        ],
      },
    ],
  };
}

/** One text-only agent input, the shape the Service stores on a Run. */
export function textInput(text: string): Schema["JsonValue"] {
  return { schema_version: "2", content: [{ type: "text", text }] };
}

/** The envelope `a13n_harness` builds for a child Run, as one JSON text. */
export function delegatedInput(
  task: string,
  parentTask: string,
): Schema["JsonValue"] {
  return textInput(
    JSON.stringify({ delegated_task: task, parent_task: parentTask }),
  );
}

/** The inbox payload an asynchronous child's terminal result is accepted as. */
export function subagentResultInput(seed: {
  subagent: string;
  threadId: string;
  runId: string;
  reply: string;
}): Schema["JsonValue"] {
  return {
    schema_version: "1",
    relationship_id: "crr_preview_1",
    subagent_name: seed.subagent,
    child_thread_id: seed.threadId,
    child_run_id: seed.runId,
    terminal_status: "completed",
    result_payload: seed.reply,
  };
}
