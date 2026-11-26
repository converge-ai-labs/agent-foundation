import type {
  Completion,
  CompletionContext,
  CompletionResult,
} from "@codemirror/autocomplete";
import { result, type Schema, type Transport } from "../transport/client";
import type { OrderedInputPart } from "./inline-attachments";

export type SkillCatalog = Schema<"SkillCatalogView">;
export type LoadSkills = () => Promise<SkillCatalog>;
export function loadThreadSkills(
  transport: Transport,
  threadId: string,
  environment?: Schema<"EnvironmentSelectionPatch"> | null,
  signal?: AbortSignal,
): Promise<SkillCatalog> {
  return environment != null
    ? result(
        transport.client.POST("/api/threads/{thread_id}/skills", {
          params: { path: { thread_id: threadId } },
          body: environment,
          signal,
        }),
      )
    : result(
        transport.client.GET("/api/threads/{thread_id}/skills", {
          params: { path: { thread_id: threadId } },
          signal,
        }),
      );
}
export function leadingSlashSkillToken(
  text: string,
): { name: string; start: number; end: number } | null {
  const match = /^\/([^\s/]+)(?=\s|$)/u.exec(text);
  if (!match) return null;
  const name = match[1];
  return { name, start: 0, end: [...`/${name}`].length };
}

export function hasSkillTrigger(
  parts: OrderedInputPart[],
  excludedSlashNames?: ReadonlySet<string>,
): boolean {
  if (
    parts.some(
      (part) => typeof part === "string" && /(?:^|\s)\$\S+/u.test(part),
    )
  )
    return true;
  const first = parts[0];
  if (typeof first !== "string") return false;
  const leading = leadingSlashSkillToken(first);
  return leading !== null && !excludedSlashNames?.has(leading.name);
}

export function skillReferences(
  parts: OrderedInputPart[],
  catalog?: SkillCatalog,
  options?: { excludedSlashNames?: ReadonlySet<string> },
) {
  const names = new Set(
    parts.flatMap((part, index) =>
      typeof part === "string"
        ? skillSpans(
            part,
            catalog,
            index === 0
              ? {
                  allowLeadingSlash: true,
                  excludedSlashNames: options?.excludedSlashNames,
                }
              : undefined,
          ).map((span) => span.name)
        : [],
    ),
  );
  const items = new Map(catalog?.items.map((item) => [item.name, item]));
  return [...names].flatMap((name) => {
    const item = items.get(name);
    return item && catalog
      ? [{ catalog_id: catalog.catalog_id, item_id: item.item_id, name }]
      : [];
  });
}
// Persisted offsets count Unicode code points, not JavaScript UTF-16 units.
export type SkillSpan = {
  name: string;
  source_id: string;
  start: number;
  end: number;
};
export function skillSpans(
  text: string,
  catalog?: SkillCatalog,
  options?: {
    allowLeadingSlash?: boolean;
    excludedSlashNames?: ReadonlySet<string>;
  },
): SkillSpan[] {
  const items = new Map(catalog?.items.map((item) => [item.name, item]));
  const spans: SkillSpan[] = [];
  if (options?.allowLeadingSlash) {
    const leading = leadingSlashSkillToken(text);
    if (leading && !options.excludedSlashNames?.has(leading.name)) {
      const item = items.get(leading.name);
      if (item) {
        spans.push({
          name: item.name,
          source_id: item.source_id,
          start: leading.start,
          end: leading.end,
        });
      }
    }
  }
  for (const match of text.matchAll(/(?:^|\s)\$(\S+)/gu)) {
    const item = items.get(match[1]);
    if (!item) continue;
    const from = match.index + match[0].indexOf("$");
    const start = [...text.slice(0, from)].length;
    spans.push({
      name: item.name,
      source_id: item.source_id,
      start,
      end: start + [...`$${item.name}`].length,
    });
  }
  return spans;
}
export function retainedSkillSpans(
  text: string,
  namespace: unknown,
): SkillSpan[] {
  if (
    !namespace ||
    typeof namespace !== "object" ||
    !("skills" in namespace) ||
    ("long_text" in namespace && namespace.long_text) ||
    !Array.isArray(namespace.skills)
  )
    return [];
  const points = [...text];
  let end = 0;
  return namespace.skills.filter((span): span is SkillSpan => {
    if (
      !span ||
      typeof span.name !== "string" ||
      typeof span.source_id !== "string" ||
      !Number.isInteger(span.start) ||
      !Number.isInteger(span.end) ||
      span.start < end ||
      span.end <= span.start ||
      span.end > points.length
    )
      return false;
    const literal = points.slice(span.start, span.end).join("");
    if (
      literal !== `$${span.name}` &&
      !(span.start === 0 && literal === `/${span.name}`)
    )
      return false;
    end = span.end;
    return true;
  });
}

export function skillDisplayName(name: string) {
  return name
    .replace(/[-_]+/g, " ")
    .replace(/(^|[ :])\p{Ll}/gu, (start) => start.toUpperCase());
}

// Only label sources identified by the catalog; an unknown source stays unlabelled.
export function skillSourceLabel(id: string) {
  if (id === "acp-command") return "Command";
  const [owner, kind] = id.split(":");
  if (owner !== "a13n-harness-ui") return;
  switch (kind) {
    case "project":
      return "Project";
    case "user-skills":
      return "Personal";
    case "builtin-skills":
      return "Built-in";
    case "content-plugin":
      return "Plugin";
    case "device":
      return "Device";
    case "explicit":
      return "Configured";
  }
}

export interface SkillOption extends Completion {
  source: string;
}

function skillQuery(context: CompletionContext) {
  // Either trigger starts a token. Slashes inside paths and URLs stay literal.
  const token = context.matchBefore(/(?:^|\s)(?:\$[^\s$]*|\/[^\s/]*)$/);
  if (!token || context.view?.compositionStarted) return null;
  const from = token.from + token.text.search(/[$/]/);
  const trigger = context.state.sliceDoc(from, from + 1);
  return {
    from,
    trigger,
    prefix: context.state.sliceDoc(from + 1, context.pos),
  };
}

export function skillCompletion(
  load: LoadSkills,
  options?: { commandNames?: () => ReadonlySet<string> },
) {
  return async (
    context: CompletionContext,
  ): Promise<CompletionResult | null> => {
    if (!skillQuery(context)) return null;
    let catalog: SkillCatalog;
    try {
      catalog = await load();
    } catch {
      // The composer reports catalog failures; keep ordinary input editable.
      return null;
    }
    if (context.aborted) return null;
    const baseOptions = catalog.items
      .filter((item) => !/\s/.test(item.name))
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((item) => ({
        name: item.name,
        option: {
          label: `$${item.name}`,
          displayLabel: skillDisplayName(item.name),
          detail: item.description.replace(/\s+/g, " ").trim(),
          apply: `$${item.name} `,
          source: item.source_id,
        } satisfies SkillOption,
      }));
    const complete = (current: CompletionContext): CompletionResult | null => {
      const query = skillQuery(current);
      if (!query) return null;
      const duplicateCommands =
        query.trigger === "/" && query.from === 0
          ? options?.commandNames?.()
          : undefined;
      return {
        from: query.from,
        // The accepted reference supplies its own separator.
        to:
          current.pos +
          (current.state.sliceDoc(current.pos, current.pos + 1) === " "
            ? 1
            : 0),
        filter: false,
        options: baseOptions
          .filter(({ option }) => option.label.startsWith(`$${query.prefix}`))
          .map(({ name, option }) =>
            duplicateCommands?.has(name.toLowerCase())
              ? {
                  ...option,
                  displayLabel: `${skillDisplayName(name)} ($${name})`,
                }
              : option,
          ),
        update: (_result, _from, _to, next) => complete(next),
      };
    };
    return complete(context);
  };
}
