import type {
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
  localRoots?: string[] | null,
  signal?: AbortSignal,
): Promise<SkillCatalog> {
  return localRoots !== undefined
    ? result(
        transport.client.POST("/api/threads/{thread_id}/skills", {
          params: { path: { thread_id: threadId } },
          body: { local_roots: localRoots },
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
export function skillReferences(
  parts: OrderedInputPart[],
  catalog?: SkillCatalog,
) {
  const names = new Set(
    parts.flatMap((part) =>
      typeof part === "string"
        ? skillSpans(part, catalog).map((span) => span.name)
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
export function skillSpans(text: string, catalog?: SkillCatalog): SkillSpan[] {
  const items = new Map(catalog?.items.map((item) => [item.name, item]));
  return [...text.matchAll(/(?:^|\s)\$(\S+)/gu)].flatMap((match) => {
    const item = items.get(match[1]);
    if (!item) return [];
    const from = match.index + match[0].indexOf("$");
    const start = [...text.slice(0, from)].length;
    return [
      {
        name: item.name,
        source_id: item.source_id,
        start,
        end: start + [...`$${item.name}`].length,
      },
    ];
  });
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
      span.end > points.length ||
      points.slice(span.start, span.end).join("") !== `$${span.name}`
    )
      return false;
    end = span.end;
    return true;
  });
}

export function skillCompletion(load: LoadSkills) {
  return async (
    context: CompletionContext,
  ): Promise<CompletionResult | null> => {
    const token = context.matchBefore(/(?:^|\s)\$[^\s]*/);
    if (!token || context.view?.compositionStarted) return null;
    const from = token.from + token.text.indexOf("$");
    const prefix = context.state.sliceDoc(from + 1, context.pos);
    let catalog: SkillCatalog;
    try {
      catalog = await load();
    } catch {
      // The composer reports catalog failures; keep ordinary input editable.
      return null;
    }
    if (context.aborted) return null;
    return {
      from,
      filter: false,
      options: catalog.items
        .filter((item) => !/\s/.test(item.name) && item.name.startsWith(prefix))
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((item) => ({
          label: `$${item.name}`,
          detail: item.description.replace(/\s+/g, " ").slice(0, 160),
          apply: `$${item.name} `,
          type: "text",
        })),
    };
  };
}
