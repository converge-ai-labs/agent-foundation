import {
  ArrowBendUpRightIcon,
  ArrowElbowDownRightIcon,
  ArrowsClockwiseIcon,
  ArrowsInLineVerticalIcon,
  CheckIcon,
  CircleNotchIcon,
  CodeIcon,
  CpuIcon,
  DiamondIcon,
  DotsThreeIcon,
  FileTextIcon,
  GitBranchIcon,
  HandPalmIcon,
  MagnifyingGlassIcon,
  PauseIcon,
  PencilSimpleIcon,
  QuestionIcon,
  SparkleIcon,
  SquareIcon,
  TerminalWindowIcon,
  UsersThreeIcon,
  WarningCircleIcon,
  WrenchIcon,
  XIcon,
} from "@phosphor-icons/react";
import type { Icon } from "@phosphor-icons/react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../../shared/api";
import { primaryArgument, resultExcerpt } from "../format";
import type { LifecycleNotice } from "../lifecycle";
import type { ActionEntry, TimelineEntry } from "../timeline";
import { readQuestions } from "./questions";
import styles from "./entry-language.module.css";

/**
 * How one timeline entry is named, drawn and read, for every surface that
 * renders it. Chat and Debug show the same entries at different depths, so the
 * words, the glyph and the outcome mark have one owner here and the components
 * only place them.
 */

/** A tool is named by its wire name in monospace; every other step gets prose. */
export interface EntryLabel {
  name: string;
  mono: boolean;
}

/**
 * An entry state alone cannot say whether an unfinished step is still working:
 * that answer belongs to the Run that owns it.
 */
export type WorkState =
  "done" | "working" | "waiting" | "interrupted" | "failed" | "unknown";

const IN_PROGRESS = ["in_progress", "running", "prepared"];
const RUN_WORKING = ["running", "queued", "accepted"];
const RUN_STOPPED = ["failed", "cancelled"];
const ENTRY_FAILED = ["failed", "error", "cancelled", "rejected"];

export function workState(state: string, runState = "running"): WorkState {
  if (state === "completed") return "done";
  if (ENTRY_FAILED.includes(state)) return "failed";
  // A sealed waiting Run keeps its unresolved call open, whatever the entry
  // state had to say about it when the stream closed.
  if (runState === "waiting" || state === "waiting") return "waiting";
  if (!IN_PROGRESS.includes(state))
    return state === "interrupted" ? "interrupted" : "unknown";
  if (RUN_WORKING.includes(runState)) return "working";
  return RUN_STOPPED.includes(runState) ? "interrupted" : "unknown";
}

/** A Toolset names its own calls; everything else is named by what it was. */
function wire(entry: ActionEntry, fallback: string): EntryLabel {
  return entry.name
    ? { name: entry.name, mono: true }
    : { name: fallback, mono: false };
}

export function entryLabel(entry: TimelineEntry, t: TFunction): EntryLabel {
  switch (entry.kind) {
    case "model":
      return {
        name: t("Model call {{index}}", { index: entry.index }),
        mono: false,
      };
    case "reasoning":
      return { name: t("Reasoning"), mono: false };
    case "reply":
      return { name: t("Reply"), mono: false };
    case "guidance":
      return { name: t("Guidance"), mono: false };
    case "other":
      return {
        name: t("{{count}} other observations", { count: entry.count }),
        mono: false,
      };
    case "event":
      return { name: noticeText(entry.notice, t), mono: false };
    case "subagent":
      return {
        name: t("Delegated to {{name}}", {
          name: entry.name || t("subagent"),
        }),
        mono: false,
      };
    case "compaction":
      return { name: t("Context compaction"), mono: false };
    case "handoff":
      return { name: t("Context handoff"), mono: false };
    case "hitl":
      return wire(
        entry,
        entry.hitl === "question" ? t("Question") : t("Approval"),
      );
    case "codeact":
      return wire(entry, t("Code execution"));
    default:
      return wire(entry, t("Tool call"));
  }
}

/** The one line of prose that follows the name: what the step was about. */
export function entrySubject(entry: TimelineEntry): string {
  if (!("arguments" in entry))
    return "text" in entry ? resultExcerpt(entry.text, 140) : "";
  // A human request is described by what it asks, not by its raw arguments.
  const asked = entry.hitl
    ? readQuestions(entry.arguments as Schema["JsonValue"])?.[0]?.question
    : null;
  return (
    asked || primaryArgument(entry.arguments) || resultExcerpt(entry.result, 80)
  );
}

/** A question asks the reader for an answer; anything else asks for approval. */
export function asksAQuestion(entry: ActionEntry): boolean {
  return entry.hitl
    ? entry.hitl === "question"
    : (entry.name ?? "").includes("question");
}

/** What a tool did is readable from its name; the glyph only has to agree. */
const TOOL_GLYPHS: [RegExp, Icon][] = [
  [/(^|_)(read|cat|open|view|get)(_|$)|file_read/, FileTextIcon],
  [/(edit|write|patch|apply|create_file|update)/, PencilSimpleIcon],
  [/(shell|exec|bash|terminal|command|run_)/, TerminalWindowIcon],
  [/(search|grep|find|glob|query|lookup|web)/, MagnifyingGlassIcon],
  [/(git|branch|commit|diff)/, GitBranchIcon],
];

export function entryGlyph(entry: TimelineEntry): Icon {
  switch (entry.kind) {
    case "model":
      return DiamondIcon;
    case "reasoning":
      return SparkleIcon;
    case "reply":
      return ArrowBendUpRightIcon;
    case "guidance":
      return ArrowElbowDownRightIcon;
    case "other":
      return DotsThreeIcon;
    case "event":
      return noticeGlyph(entry.notice);
    case "hitl":
      return entry.hitl === "question" ? QuestionIcon : HandPalmIcon;
    case "subagent":
      return UsersThreeIcon;
    case "compaction":
      return ArrowsInLineVerticalIcon;
    case "handoff":
      return ArrowBendUpRightIcon;
    case "codeact":
      return CodeIcon;
    case "native":
      return CpuIcon;
    default: {
      const name = entry.name?.toLowerCase() ?? "";
      return (
        TOOL_GLYPHS.find(([pattern]) => pattern.test(name))?.[1] ?? WrenchIcon
      );
    }
  }
}

/** The words a lifecycle fact is worth; the decision itself is `lifecycle.ts`. */
function noticeText(notice: LifecycleNotice, t: TFunction): string {
  if (notice.kind === "attempt")
    return t("Attempt {{attempt}} started · {{reason}}", {
      attempt: notice.attempt,
      reason: notice.reason,
    });
  if (notice.kind === "retry")
    return notice.attempt !== null &&
      notice.maxAttempts !== null &&
      notice.delaySeconds !== null
      ? t("Retry {{attempt}} of {{max}} in {{delay}}s", {
          attempt: notice.attempt,
          max: notice.maxAttempts,
          delay: notice.delaySeconds,
        })
      : t("Model retry scheduled");
  const head =
    notice.status === "failed"
      ? t("Run failed")
      : notice.status === "cancelled"
        ? t("Run cancelled")
        : notice.reason === "approval"
          ? t("Waiting for approval")
          : notice.reason === "multiple"
            ? t("Waiting for approvals and call results")
            : t("Waiting for call results");
  return [head, notice.reason, notice.message].filter(Boolean).join(" · ");
}

function noticeGlyph(notice: LifecycleNotice): Icon {
  if (notice.kind !== "outcome") return ArrowsClockwiseIcon;
  return notice.status === "failed"
    ? XIcon
    : notice.status === "waiting"
      ? PauseIcon
      : SquareIcon;
}

/** How an entry ended, in one mark the eye can scan down the right edge. */
export function StateMark({
  state,
  size = 12,
  decorative = false,
}: {
  state: WorkState;
  size?: number;
  /** The surrounding text already says it; the mark is then only a tint. */
  decorative?: boolean;
}) {
  const { t } = useTranslation();
  if (state === "unknown") return null;
  const name =
    state === "working"
      ? t("Working")
      : state === "waiting"
        ? t("Waiting")
        : state === "failed"
          ? t("Failed")
          : state === "interrupted"
            ? t("Interrupted")
            : t("Completed");
  const Glyph =
    state === "working"
      ? CircleNotchIcon
      : state === "waiting"
        ? PauseIcon
        : state === "failed"
          ? XIcon
          : state === "interrupted"
            ? WarningCircleIcon
            : CheckIcon;
  return (
    <span
      className={styles.mark}
      data-state={state}
      {...(decorative
        ? { "aria-hidden": "true" as const }
        : { role: "img", "aria-label": name })}
    >
      <Glyph
        size={size}
        weight={state === "waiting" ? "fill" : "regular"}
        className={state === "working" ? styles.spinning : undefined}
      />
    </span>
  );
}
