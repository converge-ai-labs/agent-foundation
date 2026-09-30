// Shared display operation applicator. Hosts own delivery and baseline authority;
// this module never reconstructs semantics from native or AG-UI events.
export type JsonValue =
  null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type Producer = { run_id: string; generation: string };
export type DisplayPosition = { producer: Producer; sequence: number };
export type DisplayScope = {
  id: string;
  thread_id: string;
  run_id: string;
  parent_scope_id: string | null;
  parent_tool_call_id: string | null;
  invocation_id: string | null;
  status: "running" | "completed" | "failed" | "cancelled" | "deferred";
};
export type DisplayBlock = {
  id: string;
  scope_id: string;
  kind:
    | "input"
    | "text"
    | "reasoning"
    | "tool_chunk"
    | "context_summary"
    | "media"
    | "extension";
  revision: number;
  status:
    | "pending"
    | "running"
    | "succeeded"
    | "failed"
    | "cancelled"
    | "deferred"
    | "unknown";
  content: Record<string, JsonValue>;
  message_index: number | null;
  part_index: number | null;
};
export type DisplayOperation =
  | { op: "block.put"; block: DisplayBlock; expected_revision: number }
  | {
      op: "block.append";
      id: string;
      field: "text" | "arguments" | "signature";
      expected_revision: number;
      revision: number;
      value: string;
    }
  | { op: "scope.put"; scope: DisplayScope }
  | { op: "blocks.remove"; ids: string[]; omitted: number };
export type DisplayDelta = {
  format: "display-delta/1";
  producer: Producer;
  from_sequence: number;
  through_sequence: number;
  operations: DisplayOperation[];
};
export type DisplaySnapshot = {
  format: "display/1";
  position: DisplayPosition;
  scopes: DisplayScope[];
  blocks: DisplayBlock[];
  omitted: number;
  continuity: Record<string, JsonValue>;
};

export class DisplayGap extends Error {}

export function sameProducer(a: Producer, b: Producer): boolean {
  return a.run_id === b.run_id && a.generation === b.generation;
}

function requireValue(condition: boolean, message: string): asserts condition {
  if (!condition) throw new DisplayGap(message);
}

function natural(value: number): boolean {
  return Number.isSafeInteger(value) && value >= 0;
}

function sameScope(a: DisplayScope, b: DisplayScope): boolean {
  return (
    a.id === b.id &&
    a.thread_id === b.thread_id &&
    a.run_id === b.run_id &&
    a.parent_scope_id === b.parent_scope_id &&
    a.parent_tool_call_id === b.parent_tool_call_id &&
    a.invocation_id === b.invocation_id
  );
}

/** Serial owner. Every batch is validated in isolation before any visible write. */
export class DisplayState {
  private _position!: DisplayPosition;
  private _blocks = new Map<string, DisplayBlock>();
  private _scopes = new Map<string, DisplayScope>();
  private _omitted = 0;
  private _continuity: Record<string, JsonValue> = {};

  constructor(snapshot: DisplaySnapshot) {
    this.restore(snapshot);
  }

  get position(): DisplayPosition {
    return structuredClone(this._position);
  }

  restore(snapshot: DisplaySnapshot): void {
    requireValue(
      snapshot.format === "display/1",
      "Unsupported display snapshot",
    );
    requireValue(
      natural(snapshot.position.sequence) && natural(snapshot.omitted),
      "Invalid display position",
    );
    const value = structuredClone(snapshot);
    const scopes = new Map(value.scopes.map((scope) => [scope.id, scope]));
    const blocks = new Map(value.blocks.map((block) => [block.id, block]));
    requireValue(
      scopes.size === value.scopes.length &&
        blocks.size === value.blocks.length,
      "Duplicate display identity",
    );
    for (const scope of scopes.values()) {
      const visited = new Set<string>();
      let current: string | null = scope.id;
      while (current !== null) {
        requireValue(!visited.has(current), "Cyclic display scope");
        visited.add(current);
        const parent = scopes.get(current);
        requireValue(parent !== undefined, "Absent display parent");
        current = parent.parent_scope_id;
      }
    }
    for (const block of blocks.values()) {
      requireValue(
        scopes.has(block.scope_id) &&
          natural(block.revision) &&
          block.revision > 0,
        "Invalid display block",
      );
    }
    this._position = value.position;
    this._blocks = blocks;
    this._scopes = scopes;
    this._omitted = value.omitted;
    this._continuity = value.continuity;
  }

  /** Detached touched values for rendering without copying the full transcript per token. */
  selectBlocks(ids: Iterable<string>): DisplayBlock[] {
    return [...ids].flatMap((id) => {
      const block = this._blocks.get(id);
      return block ? [structuredClone(block)] : [];
    });
  }

  capture(): DisplaySnapshot {
    return structuredClone({
      format: "display/1",
      position: this._position,
      scopes: [...this._scopes.values()],
      blocks: [...this._blocks.values()],
      omitted: this._omitted,
      continuity: this._continuity,
    });
  }

  apply(delta: DisplayDelta): boolean {
    requireValue(
      delta.format === "display-delta/1",
      "Unsupported display delta",
    );
    requireValue(
      sameProducer(delta.producer, this._position.producer),
      "Display producer changed",
    );
    requireValue(
      natural(delta.from_sequence) &&
        natural(delta.through_sequence) &&
        delta.through_sequence === delta.from_sequence + 1 &&
        delta.operations.length > 0,
      "Invalid display sequence",
    );
    if (delta.through_sequence <= this._position.sequence) return false;
    requireValue(
      delta.from_sequence === this._position.sequence,
      "Noncontiguous display sequence",
    );
    const changed = new Map<string, DisplayBlock | null>();
    const scopes = new Map<string, DisplayScope>();
    let omitted = this._omitted;
    const previousBlock = (id: string) =>
      changed.has(id) ? changed.get(id) : this._blocks.get(id);
    for (const operation of delta.operations) {
      switch (operation.op) {
        case "scope.put": {
          const scope = operation.scope;
          requireValue(
            scope.parent_scope_id !== scope.id &&
              (scope.parent_scope_id === null ||
                scopes.has(scope.parent_scope_id) ||
                this._scopes.has(scope.parent_scope_id)),
            "Absent display scope parent",
          );
          const existing = scopes.get(scope.id) ?? this._scopes.get(scope.id);
          requireValue(
            existing === undefined || sameScope(existing, scope),
            "Display scope identity changed",
          );
          scopes.set(scope.id, structuredClone(scope));
          break;
        }
        case "blocks.remove":
          requireValue(
            natural(operation.omitted) && operation.omitted >= omitted,
            "Display omission count moved backwards",
          );
          for (const id of operation.ids) {
            requireValue(
              previousBlock(id) != null,
              "Removed display block is absent",
            );
            changed.set(id, null);
          }
          omitted = operation.omitted;
          break;
        case "block.put":
        case "block.append": {
          const id =
            operation.op === "block.put" ? operation.block.id : operation.id;
          const previous = previousBlock(id);
          const revision = previous?.revision ?? 0;
          requireValue(
            natural(operation.expected_revision) &&
              revision === operation.expected_revision,
            "Display block revision mismatch",
          );
          if (operation.op === "block.put") {
            const block = operation.block;
            requireValue(
              block.revision === revision + 1,
              "Invalid display block revision",
            );
            requireValue(
              scopes.has(block.scope_id) || this._scopes.has(block.scope_id),
              "Absent display block scope",
            );
            requireValue(
              previous == null ||
                (previous.scope_id === block.scope_id &&
                  previous.kind === block.kind),
              "Display block identity changed",
            );
            changed.set(id, structuredClone(block));
          } else {
            requireValue(
              previous != null &&
                revision >= 1 &&
                operation.revision === revision + 1,
              "Invalid display append revision",
            );
            requireValue(
              ["text", "arguments", "signature"].includes(operation.field),
              "Invalid display append field",
            );
            const value = previous.content[operation.field];
            requireValue(
              typeof value === "string" && typeof operation.value === "string",
              "Display append field is not text",
            );
            changed.set(id, {
              ...previous,
              revision: operation.revision,
              content: {
                ...previous.content,
                [operation.field]: value + operation.value,
              },
            });
          }
          break;
        }
        default:
          throw new DisplayGap("Unknown display operation");
      }
    }
    for (const [id, scope] of scopes) this._scopes.set(id, scope);
    for (const [id, block] of changed) {
      if (block === null) this._blocks.delete(id);
      else this._blocks.set(id, block);
    }
    this._omitted = omitted;
    this._position = {
      producer: structuredClone(delta.producer),
      sequence: delta.through_sequence,
    };
    return true;
  }
}
