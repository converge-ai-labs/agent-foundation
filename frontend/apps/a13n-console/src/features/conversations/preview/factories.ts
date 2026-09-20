import type { Schema } from "../../../shared/api";
import type { LifecycleFact, PreviewRun, PreviewSession } from "./model";
import type { Simulation } from "./simulate";

export const ORGANIZATION_ID = "org_preview";
const PRINCIPAL: Schema["PrincipalRef"] = {
  principal_id: "usr_preview",
  principal_type: "user",
};
const SYSTEM_ACTOR: Schema["ActorRef"] = {
  principal_id: "system",
  principal_type: "system",
};

export interface RunSeed {
  id: string;
  sessionId: string;
  threadId: string;
  agentId: string;
  agentKey: string;
  createdAt: string;
  input: Schema["JsonValue"];
  inputText: string | null;
  /** One of the four accepted input protocols; `agent_input` is ordinary. */
  inputKind?: string;
  triggerType?: string;
  lineageKind?: Schema["RunLineageKind"];
  parentRunId?: string | null;
  retryOfRunId?: string | null;
  environmentId?: string | null;
}

export function newRun(seed: RunSeed): PreviewRun {
  const run: Schema["RunResource"] = {
    id: seed.id,
    session_id: seed.sessionId,
    thread_id: seed.threadId,
    agent_id: seed.agentId,
    agent_revision_id: `rev_${seed.agentKey}`,
    effective_agent_config_digest: `sha256:${seed.agentKey}0preview0digest`,
    created_at: seed.createdAt,
    updated_at: seed.createdAt,
    started_at: seed.createdAt,
    completed_at: null,
    sealed_at: null,
    sealed_state_digest_sha256: null,
    environment_id: seed.environmentId ?? null,
    environment_working_directory: seed.environmentId ? "/workspace" : null,
    failure: null,
    input: seed.input,
    input_kind: seed.inputKind ?? "agent_input",
    input_text: seed.inputText,
    labels: {},
    lineage_kind: seed.lineageKind ?? "root",
    output: null,
    output_text: null,
    parent_run_id: seed.parentRunId ?? null,
    pending: null,
    retry_of_run_id: seed.retryOfRunId ?? null,
    status: "accepted",
    trigger_type: seed.triggerType ?? "user_input",
    version: 1,
    wait_reason: null,
    waiting_at: null,
  };
  return {
    run,
    facts: [],
    attempts: [],
    pending: [],
    lifecycle: [],
    mounts: [],
    log: [],
  };
}

/** RunAttempt resources follow from the same lifecycle facts the stream carries. */
function attemptsFrom(
  runId: string,
  facts: readonly LifecycleFact[],
): Schema["RunAttemptResource"][] {
  const attempts = new Map<string, Schema["RunAttemptResource"]>();
  let previous: string | null = null;
  for (const fact of facts) {
    if (!fact.attemptId) continue;
    const state = fact.eventType.slice("run_attempt.".length);
    const existing = attempts.get(fact.attemptId);
    if (!existing) {
      attempts.set(fact.attemptId, {
        id: fact.attemptId,
        run_id: runId,
        attempt_number: Number(fact.data.attempt_number ?? attempts.size + 1),
        created_at: fact.occurredAt,
        updated_at: fact.occurredAt,
        started_at: null,
        finished_at: null,
        failure: null,
        harness_run_id: null,
        replaces_run_attempt_id: previous,
        start_reason: previous ? "recovery" : "initial",
        status: state,
        version: 1,
        worker_build_id: "preview-worker-1",
        yield_reason: null,
      });
      previous = fact.attemptId;
      continue;
    }
    existing.status = state;
    existing.updated_at = fact.occurredAt;
    existing.version += 1;
    if (state === "running") {
      existing.started_at = fact.occurredAt;
      existing.harness_run_id = fact.harnessRunId;
    }
    if (["succeeded", "failed", "cancelled", "yielded"].includes(state))
      existing.finished_at = fact.occurredAt;
    if (state === "failed")
      existing.failure = (fact.data.failure ?? null) as Schema["JsonValue"];
    if (state === "yielded")
      existing.yield_reason = String(fact.data.yield_reason ?? "");
  }
  return [...attempts.values()];
}

/** The durable lifecycle page reads the same facts as the Run stream. */
function lifecycleFrom(
  run: Schema["RunResource"],
  facts: readonly LifecycleFact[],
): Schema["LifecycleEvent"][] {
  return facts.map((fact, index) => ({
    id: `lev_${run.id}_${index + 1}`,
    actor_type: "worker",
    actor_id: null,
    created_at: fact.occurredAt,
    occurred_at: fact.occurredAt,
    entity_id: fact.entityType === "run" ? run.id : (fact.attemptId ?? run.id),
    entity_type: fact.entityType,
    entity_version: index + 1,
    event_type: fact.eventType,
    hook_dispatch_attempts: 0,
    hook_dispatch_state: "done",
    mutation_id: `mut_${run.id}_${index + 1}`,
    organization_id: ORGANIZATION_ID,
    payload: fact.data as Schema["LifecycleEvent"]["payload"],
    projection_attempts: 1,
    projection_state: "projected",
    projected_at: fact.occurredAt,
    resource_seq: index + 1,
    run_id: run.id,
    run_attempt_id: fact.attemptId,
    schema_version: "1",
    seq: index + 1,
    session_id: run.session_id,
    thread_id: run.thread_id,
  }));
}

/** RunAttempt and lifecycle resources always follow the Run's committed facts. */
export function deriveRun(run: PreviewRun): void {
  run.attempts = attemptsFrom(run.run.id, run.facts);
  run.lifecycle = lifecycleFrom(run.run, run.facts);
}

/** Apply the outcome a simulation reached to its Run resource and children. */
export function applySimulation(
  run: PreviewRun,
  simulation: Simulation,
  updatedAt: string,
): void {
  run.facts = [...simulation.log.facts];
  deriveRun(run);
  run.pending = simulation.pending;
  const last = run.facts.at(-1)?.occurredAt ?? run.run.created_at;
  Object.assign(run.run, {
    status: simulation.status,
    output_text: simulation.outputText,
    output: simulation.outputText,
    failure: simulation.failure,
    wait_reason: simulation.waitReason,
    waiting_at: simulation.waitReason ? last : null,
    completed_at: simulation.status === "waiting" ? null : last,
    sealed_at: last,
    sealed_state_digest_sha256: `sha256:${run.run.id}:${run.facts.length}`,
    updated_at: updatedAt,
    version: run.run.version + 1,
  });
}

/** The Session collection preview is its newest root Run, as on the wire. */
export function refreshSessionPreview(
  entry: PreviewSession,
  agents: readonly Schema["Agent"][],
): void {
  const runs = entry.threads
    .filter((thread) => thread.thread.role === "root")
    .flatMap((thread) => thread.runs);
  const newest = runs.at(-1);
  entry.session.run_count = runs.length;
  entry.session.updated_at = newest?.run.updated_at ?? entry.session.updated_at;
  entry.session.preview = newest
    ? {
        thread_id: newest.run.thread_id,
        run_id: newest.run.id,
        input_text: newest.run.input_text,
        output_text: newest.run.output_text,
        agent_name:
          agents.find((agent) => agent.id === newest.run.agent_id)?.name ??
          null,
        run_status: newest.run.status,
        trigger_type: newest.run.trigger_type,
      }
    : null;
}

export function newThread(seed: {
  id: string;
  sessionId: string;
  createdAt: string;
  purpose: Schema["SessionPurpose"];
  role?: string;
  originKind?: string;
  originRunId?: string | null;
  originThreadId?: string | null;
  environmentId?: string | null;
}): Schema["ThreadResource"] {
  return {
    id: seed.id,
    session_id: seed.sessionId,
    session_purpose: seed.purpose,
    created_at: seed.createdAt,
    updated_at: seed.createdAt,
    current_run_id: null,
    head_run_id: null,
    default_environment_id: seed.environmentId ?? null,
    default_environment_working_directory: seed.environmentId
      ? "/workspace"
      : null,
    labels: {},
    origin_kind: seed.originKind ?? "console",
    origin_run_id: seed.originRunId ?? null,
    origin_thread_id: seed.originThreadId ?? null,
    queue_version: 1,
    role: seed.role ?? "root",
    version: 1,
  };
}

export function newSession(seed: {
  id: string;
  workspaceId: string;
  createdAt: string;
  purpose: Schema["SessionPurpose"];
}): Schema["SessionResource"] {
  return {
    id: seed.id,
    workspace_id: seed.workspaceId,
    created_at: seed.createdAt,
    updated_at: seed.createdAt,
    labels: {},
    preview: null,
    purpose: seed.purpose,
    run_count: 0,
  };
}

export function newAgent(seed: {
  id: string;
  key: string;
  name: string;
  description: string;
  workspaceId: string;
  createdAt: string;
}): Schema["Agent"] {
  return {
    id: seed.id,
    key: seed.key,
    name: seed.name,
    description: seed.description,
    archived_at: null,
    created_at: seed.createdAt,
    updated_at: seed.createdAt,
    created_by: SYSTEM_ACTOR,
    updated_by: SYSTEM_ACTOR,
    default_revision_id: `rev_${seed.key}`,
    duplicated_from_agent_id: null,
    duplicated_from_revision_id: null,
    enabled: true,
    image_url: null,
    labels: {},
    organization_id: ORGANIZATION_ID,
    source: "custom",
    system_purpose: null,
    workspace_id: seed.workspaceId,
  };
}

export function newEnvironment(seed: {
  id: string;
  name: string;
  workspaceId: string;
  createdAt: string;
}): Schema["Environment"] {
  return {
    id: seed.id,
    name: seed.name,
    condition_since: seed.createdAt,
    created_at: seed.createdAt,
    updated_at: seed.createdAt,
    generation: 1,
    labels: {},
    organization_id: ORGANIZATION_ID,
    ownership: "managed",
    provider_id: "prv_preview",
    retention_condition: "active",
    status: "running",
    template_revision_id: null,
    workspace_id: seed.workspaceId,
  };
}

export function newMount(
  run: Schema["RunResource"],
  attemptId: string | null,
): Schema["RunEnvironmentMount"] {
  return {
    accepting_principal: PRINCIPAL,
    application_status: "ready",
    applied_attempt_fence: 1,
    applied_attempt_id: attemptId,
    created_at: run.created_at,
    environment_id: run.environment_id!,
    name: "workspace",
    observed_at: run.created_at,
    run_id: run.id,
    use_started_at: run.started_at,
    working_directory: run.environment_working_directory,
  };
}

export function newAsset(seed: {
  id: string;
  filename: string;
  mediaType: string;
  workspaceId: string;
  createdAt: string;
}): Schema["Asset"] {
  return {
    id: seed.id,
    filename: seed.filename,
    media_type: seed.mediaType,
    content_sha256: `sha256:${seed.id}`,
    created_at: seed.createdAt,
    deleted_at: null,
    organization_id: ORGANIZATION_ID,
    size_bytes: 2048,
    source: { kind: "upload", principal: PRINCIPAL },
    workspace_id: seed.workspaceId,
  };
}

export function newQueuedSubmission(seed: {
  id: string;
  threadId: string;
  createdAt: string;
  text: string;
  position: number;
}): Schema["QueuedSubmission"] {
  return {
    queued_submission_id: seed.id,
    thread_id: seed.threadId,
    authority_principal: PRINCIPAL,
    created_at: seed.createdAt,
    updated_at: seed.createdAt,
    position: seed.position,
    state: "queued",
    submission: {
      input: {
        schema_version: "2",
        content: [{ type: "text", text: seed.text }],
      },
    },
    submission_digest_sha256: `sha256:${seed.id}`,
    version: 1,
  };
}
