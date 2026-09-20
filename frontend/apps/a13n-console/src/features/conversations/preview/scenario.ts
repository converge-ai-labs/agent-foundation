import type { Schema } from "../../../shared/api";
import {
  RESEARCH_REPLY,
  delegatedInput,
  subagentResultInput,
  textInput,
} from "./content";
import {
  ORGANIZATION_ID,
  applySimulation,
  newAgent,
  newAsset,
  newEnvironment,
  newMount,
  newQueuedSubmission,
  newRun,
  newSession,
  newThread,
  refreshSessionPreview,
  type RunSeed,
} from "./factories";
import type {
  PreviewRun,
  PreviewScenario,
  PreviewSession,
  PreviewThread,
} from "./model";
import { simulate, type ScriptName } from "./simulate";
import { checksSpans, failedSpans, reviewSpans, traceFor } from "./traces";

const WORKSPACE_ID = "wsp_preview";
const WORKSPACE_KEY = "preview";

/** A deterministic scenario: the same ids and content on every boot. */
export function previewScenario(
  startedAt = Date.now() - 50 * 60_000,
): PreviewScenario {
  let counter = 0;
  const id = (prefix: string) =>
    `${prefix}_${(++counter).toString(36).padStart(4, "0")}`;
  const at = (minutes: number) =>
    new Date(startedAt + minutes * 60_000).toISOString();
  const created = at(-60);

  const workspace: Schema["Workspace"] = {
    id: WORKSPACE_ID,
    key: WORKSPACE_KEY,
    name: "Preview",
    organization_id: ORGANIZATION_ID,
    image_url: null,
    created_at: created,
    updated_at: created,
  };
  const agents = {
    release: newAgent({
      id: "agt_release",
      key: "release-bot",
      name: "Release Bot",
      description: "Reviews release readiness, runs checks and writes markers.",
      workspaceId: WORKSPACE_ID,
      createdAt: created,
    }),
    researcher: newAgent({
      id: "agt_research",
      key: "researcher",
      name: "Researcher",
      description: "Searches prior incidents and summarizes what it finds.",
      workspaceId: WORKSPACE_ID,
      createdAt: created,
    }),
    triage: newAgent({
      id: "agt_triage",
      key: "support-triage",
      name: "Support Triage",
      description: "Summarizes inbound tickets and drafts first replies.",
      workspaceId: WORKSPACE_ID,
      createdAt: created,
    }),
    changelog: newAgent({
      id: "agt_changelog",
      key: "changelog-writer",
      name: "Changelog Writer",
      description: "Turns merged pull requests into a weekly changelog.",
      workspaceId: WORKSPACE_ID,
      createdAt: created,
    }),
  };
  const environment = newEnvironment({
    id: "env_sandbox",
    name: "sandbox-python",
    workspaceId: WORKSPACE_ID,
    createdAt: created,
  });

  /** One finished Run: its stream, its attempts, its lifecycle and its state. */
  function record(
    script: ScriptName,
    seed: Omit<RunSeed, "input"> & { input?: Schema["JsonValue"] },
  ): PreviewRun {
    const run = newRun({
      ...seed,
      input: seed.input ?? textInput(seed.inputText ?? ""),
    });
    const simulation = simulate(
      script,
      {
        runId: run.run.id,
        threadId: run.run.thread_id,
        startedAt: run.run.created_at,
        attemptId: id("att"),
        harnessRunId: id("hrn"),
      },
      id,
    );
    run.log = simulation.log.entries;
    applySimulation(run, simulation, seed.createdAt);
    if (run.run.environment_id)
      run.mounts = [newMount(run.run, run.attempts[0]?.id ?? null)];
    return run;
  }

  /* Release readiness: five runs, the last waiting on an approval --------- */

  const releaseThread = newThread({
    id: "thr_release",
    sessionId: "ses_release",
    createdAt: at(-42),
    purpose: "debug",
    environmentId: environment.id,
  });
  const releaseSeed = (
    minutes: number,
    text: string | null,
    index: number,
  ) => ({
    id: `run_release_${index}`,
    sessionId: "ses_release",
    threadId: releaseThread.id,
    agentId: agents.release.id,
    agentKey: agents.release.key,
    createdAt: at(minutes),
    inputText: text,
    environmentId: environment.id,
    lineageKind: (index === 1
      ? "root"
      : "continue") as Schema["RunLineageKind"],
    parentRunId: index === 1 ? null : `run_release_${index - 1}`,
  });
  const releaseRuns = [
    record(
      "review",
      releaseSeed(-42, "Review the release readiness for 2.4.0", 1),
    ),
    record(
      "checks",
      releaseSeed(-31, "Run the checks and fix whatever fails", 2),
    ),
    // The asynchronous child's terminal result is what accepted this Run.
    record("incident", {
      ...releaseSeed(-26, null, 3),
      input: subagentResultInput({
        subagent: "Researcher",
        threadId: "thr_research",
        runId: "run_research_1",
        reply: RESEARCH_REPLY,
      }),
      inputKind: "async_subagent_result",
      triggerType: "async_subagent_result",
    }),
    record(
      "notes",
      releaseSeed(
        -18,
        "Summarize what changed and prepare the release notes",
        4,
      ),
    ),
    record("marker", releaseSeed(-6, "Write the release marker for 2.4.0", 5)),
  ];
  releaseThread.current_run_id = releaseRuns[4]!.run.id;
  releaseThread.head_run_id = releaseRuns[4]!.run.id;
  releaseThread.updated_at = releaseRuns[4]!.run.updated_at;

  const researchThread = newThread({
    id: "thr_research",
    sessionId: "ses_release",
    createdAt: at(-29),
    purpose: "debug",
    role: "child",
    originKind: "delegation",
    originRunId: releaseRuns[1]!.run.id,
    originThreadId: releaseThread.id,
  });
  const researchRun = record("research", {
    id: "run_research_1",
    sessionId: "ses_release",
    threadId: researchThread.id,
    agentId: agents.researcher.id,
    agentKey: agents.researcher.key,
    createdAt: at(-29),
    // A child Run is started with the Harness delegation envelope, not prose.
    input: delegatedInput(
      "Find prior incidents for cursor folds",
      "Run the checks and fix whatever fails",
    ),
    inputText: null,
    triggerType: "async_subagent",
    parentRunId: releaseRuns[1]!.run.id,
  });
  researchThread.current_run_id = researchRun.run.id;
  researchThread.head_run_id = researchRun.run.id;

  const release = session("ses_release", "debug", at(-42), [
    { thread: releaseThread, runs: releaseRuns, queue: [] },
    { thread: researchThread, runs: [researchRun], queue: [] },
  ]);
  release.threads[0]!.queue = [
    newQueuedSubmission({
      id: "qsb_release_1",
      threadId: releaseThread.id,
      createdAt: at(-5),
      text: "After the marker lands, tag the release and post the notes.",
      position: 1,
    }),
  ];

  /* Inbound triage: one run waiting on a question ------------------------- */

  const triageThread = newThread({
    id: "thr_triage",
    sessionId: "ses_triage",
    createdAt: at(-22),
    purpose: "execution",
  });
  const triageRun = record("question", {
    id: "run_triage_1",
    sessionId: "ses_triage",
    threadId: triageThread.id,
    agentId: agents.triage.id,
    agentKey: agents.triage.key,
    createdAt: at(-22),
    inputText: "Ticket 4471: the console drops the last streamed token.",
    triggerType: "inbound",
  });
  triageThread.current_run_id = triageRun.run.id;
  triageThread.head_run_id = triageRun.run.id;
  const triage = session("ses_triage", "execution", at(-22), [
    { thread: triageThread, runs: [triageRun], queue: [] },
  ]);

  /* Changelog: a two-attempt failure -------------------------------------- */

  const changelogThread = newThread({
    id: "thr_changelog",
    sessionId: "ses_changelog",
    createdAt: at(-12),
    purpose: "debug",
  });
  const changelogRun = record("fail", {
    id: "run_changelog_1",
    sessionId: "ses_changelog",
    threadId: changelogThread.id,
    agentId: agents.changelog.id,
    agentKey: agents.changelog.key,
    createdAt: at(-12),
    inputText: "Draft the weekly changelog; this one will fail on purpose.",
  });
  changelogThread.current_run_id = changelogRun.run.id;
  const changelog = session("ses_changelog", "debug", at(-12), [
    { thread: changelogThread, runs: [changelogRun], queue: [] },
  ]);

  const sessions = [release, triage, changelog];
  for (const entry of sessions)
    refreshSessionPreview(entry, Object.values(agents));

  return {
    user: {
      id: "usr_preview",
      name: "Preview User",
      email: "preview@example.com",
      email_verified_at: created,
      image_url: null,
      status: "active",
      created_at: created,
      updated_at: created,
    },
    organization: {
      id: ORGANIZATION_ID,
      key: "preview-org",
      name: "Preview Org",
      image_url: null,
      created_at: created,
      updated_at: created,
    },
    workspace,
    permissions: PERMISSIONS,
    agents: Object.values(agents),
    environments: [environment],
    assets: [
      newAsset({
        id: "ast_preview",
        filename: "release-checklist.md",
        mediaType: "text/markdown",
        workspaceId: WORKSPACE_ID,
        createdAt: created,
      }),
    ],
    sessions,
    traces: [
      traceFor(releaseRuns[0]!, WORKSPACE_ID, reviewSpans()),
      traceFor(releaseRuns[1]!, WORKSPACE_ID, checksSpans()),
      traceFor(changelogRun, WORKSPACE_ID, failedSpans()),
    ],
    entry: {
      sessionId: release.session.id,
      threadId: releaseThread.id,
      runId: releaseRuns[4]!.run.id,
    },
  };
}

/** Every Workspace action the session page reads, minus WebSocket delivery. */
const PERMISSIONS = [
  "agent.read",
  "agent.invoke",
  "asset.create",
  "asset.read",
  "environment.read",
  "environment.use",
  "lifecycle_event.read",
  "memory_provider.read",
  "queued_submission.read",
  "queued_submission.reorder",
  "queued_submission.delete",
  "queued_submission.consume",
  "run.continue",
  "run.feedback",
  "run.interrupt",
  "run.retry",
  "run.steer",
  "trace.read",
];

function session(
  id: string,
  purpose: Schema["SessionPurpose"],
  createdAt: string,
  threads: PreviewThread[],
): PreviewSession {
  return {
    session: newSession({
      id,
      workspaceId: WORKSPACE_ID,
      createdAt,
      purpose,
    }),
    threads,
  };
}
