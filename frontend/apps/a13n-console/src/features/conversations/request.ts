import type { Schema } from "../../shared/api";
import { inputText } from "./input";
import { isObject } from "./projection";

/**
 * What a Run was asked to do, resolved once from its accepted input.
 * `input_kind` names the protocol that owns that value, and a Run of a child
 * Thread carries the delegation envelope its parent built. Chat, Debug and the
 * Run navigator all read this one answer, so the same request never reads as a
 * person's message on one surface and as raw JSON on another. This module
 * decides what the request is; the components only choose the words.
 */

type Run = Schema["RunResource"];
type Thread = Schema["ThreadResource"];

export type RunRequest =
  /** Ordinary `agent_input`: what a person or an application sent. */
  | { kind: "message"; input: unknown; text: string }
  /** `waiting_feedback`: the complete resolution of a waiting batch. */
  | { kind: "feedback"; input: unknown; text: string }
  /** `waiting_continue`: that batch resolved by default, plus a new message. */
  | { kind: "continue"; input: unknown; text: string }
  /** `async_subagent_result`: an asynchronous child's terminal result. */
  | {
      kind: "subagent_result";
      subagent: string | null;
      status: string | null;
      text: string;
    }
  /** The task a parent delegated, as the Harness builds a child Run's input. */
  | { kind: "delegated_task"; text: string; parentTask: string | null };

export function runRequest(run: Run, thread?: Thread | null): RunRequest {
  const read = (input: unknown) => inputText(input, run.input_text);
  if (run.input_kind === "async_subagent_result")
    return subagentResult(run.input);
  if (run.input_kind === "waiting_feedback")
    return { kind: "feedback", input: run.input, text: read(run.input) };
  if (run.input_kind === "waiting_continue") {
    // The composite carries the normalized defaults and the accepted input;
    // the message is the part the person actually wrote.
    const input =
      isObject(run.input) && run.input.input !== undefined
        ? run.input.input
        : run.input;
    return { kind: "continue", input, text: read(input) };
  }
  return (
    (thread?.role === "child" ? delegatedTask(run, read) : null) ?? {
      kind: "message",
      input: run.input,
      text: read(run.input),
    }
  );
}

/**
 * The child wrote a reply, so its payload is prose. A payload that is not text
 * is shown as the compact value it is, never expanded into a JSON document.
 */
function subagentResult(input: unknown): RunRequest {
  const payload = isObject(input) ? input : {};
  return {
    kind: "subagent_result",
    subagent: text(payload.subagent_name),
    status: text(payload.terminal_status),
    text: value(payload.result_payload),
  };
}

/**
 * The Harness hands a child Run one JSON envelope as its text: the delegated
 * task, and the parent's own task when the subagent is allowed to see it.
 */
function delegatedTask(
  run: Run,
  read: (input: unknown) => string,
): RunRequest | null {
  const envelope = parseObject(read(run.input));
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
    return isObject(parsed) ? parsed : null;
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
