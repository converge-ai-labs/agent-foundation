import type { Schema } from "../../shared/api";
import { inputText } from "./input";
import { isRecord } from "../../service-client";

/**
 * What a Run was asked to do, resolved once from its source. `trigger` names
 * the protocol that owns that value: a message's payload, the answers that
 * resumed a wait, or a child Run's result; a Run of a child Thread carries the
 * delegation envelope its parent built. Chat, Debug and the Run navigator all
 * read this one answer, so the same request never reads as a person's message
 * on one surface and as raw JSON on another. This module decides what the
 * request is; the components only choose the words.
 */

type Run = Schema["RunView"];
type Thread = Schema["ThreadView"];

export type RunRequest =
  /** An ordinary message: what a person or an application sent. */
  | { kind: "message"; input: unknown; text: string }
  /** `resume`: the complete answer to a waiting batch. */
  | { kind: "feedback"; input: unknown; text: string }
  /** `child_result`: a child Run's terminal result. */
  | {
      kind: "subagent_result";
      subagent: string | null;
      status: string | null;
      text: string;
    }
  /** The task a parent delegated, as the Harness builds a child Run's input. */
  | { kind: "delegated_task"; text: string; parentTask: string | null };

export function runRequest(run: Run, thread?: Thread | null): RunRequest {
  if (run.trigger === "child_result") return subagentResult(run.input);
  if (run.trigger === "resume")
    return { kind: "feedback", input: run.resume, text: inputText(run.resume) };
  return (
    (thread?.origin === "child" ? delegatedTask(run) : null) ?? {
      kind: "message",
      input: run.input,
      text: inputText(run.input),
    }
  );
}

/**
 * The child wrote a reply, so its payload is prose. A payload that is not text
 * is shown as the compact value it is, never expanded into a JSON document.
 */
function subagentResult(input: unknown): RunRequest {
  const payload = isRecord(input) ? input : {};
  return {
    kind: "subagent_result",
    subagent: text(payload.subagent),
    status: text(payload.status),
    text: value(payload.output),
  };
}

/**
 * The Harness hands a child Run one JSON envelope as its text: the delegated
 * task, and the parent's own task when the subagent is allowed to see it.
 */
function delegatedTask(run: Run): RunRequest | null {
  const envelope = parseObject(inputText(run.input));
  if (!envelope || envelope.delegated_task === undefined) return null;
  return {
    kind: "delegated_task",
    text: value(envelope.delegated_task),
    parentTask: text(envelope.parent_task),
  };
}

function parseObject(source: string): Record<string, unknown> | null {
  if (!source.trimStart().startsWith("{")) return null;
  try {
    const parsed: unknown = JSON.parse(source);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function text(candidate: unknown): string | null {
  return typeof candidate === "string" && candidate ? candidate : null;
}

function value(candidate: unknown): string {
  if (typeof candidate === "string") return candidate;
  if (candidate === undefined || candidate === null) return "";
  return JSON.stringify(candidate) ?? "";
}
