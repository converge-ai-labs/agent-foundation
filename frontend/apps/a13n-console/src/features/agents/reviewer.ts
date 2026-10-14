import type { Schema } from "../../shared/api";
import {
  schemaErrors,
  validateAgentConfig,
} from "../../shared/forms/validation";
import type { AgentConfig } from "./configuration";

export type Reviewer = Schema["AgentReviewer"];
export type ReviewTarget = { selector: string; label: string };

/** Service checks enabled builtins and every declared connection permission. */
export function requiresReviewer(
  toolsets: AgentConfig["toolsets"],
  connections: AgentConfig["connection_tools"],
) {
  return (
    Object.values(toolsets ?? {}).some(
      (group) =>
        group.enabled !== false &&
        Object.values(group.tools ?? {}).some(
          (tool) => tool.enabled !== false && tool.permission === "review",
        ),
    ) ||
    (connections ?? []).some(
      (connection) =>
        connection.permission === "review" ||
        Object.values(connection.permissions ?? {}).includes("review"),
    )
  );
}

export function validateReviewer(value: unknown): asserts value is Reviewer {
  const config: unknown = { model: "validation", reviewer: value };
  if (!validateAgentConfig(config)) throw new Error(schemaErrors());
  if (!config.reviewer)
    throw new Error(
      "Choose a review model before saving automatic review permissions.",
    );
  for (const selector of Object.keys(config.reviewer?.rules ?? {})) {
    if (
      !selector ||
      selector !== selector.trim() ||
      selector.length > 1024 ||
      (selector.includes("*") &&
        selector !== "*" &&
        !/^[^*]+[/.]\*$/.test(selector))
    )
      throw new Error(`Invalid tool review selector: ${selector}`);
  }
}

/** Match Harness's source_tool_id encoding, independently of display names. */
export function connectionReviewTarget(
  connection: Schema["Connection"],
  name: string,
): ReviewTarget {
  const encode = (value: string) =>
    encodeURIComponent(value).replace(
      /[!'()*]/g,
      (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`,
    );
  return {
    selector: `${"url" in connection.config ? "mcp" : "tool"}/${encode(connection.id)}/${encode(name)}`,
    label: name,
  };
}
