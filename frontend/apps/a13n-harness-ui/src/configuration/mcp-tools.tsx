import { useContext, useEffect, useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Checkbox, Input } from "a13n-ui";
import { ArrowClockwise, CaretRight } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { ApiError, result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { SettingsChoice, SettingsSection } from "../shell/settings";
import { DraftContext } from "./drafts";
import styles from "./mcp-tools.module.css";

type ToolDraft = { tools: string[] | null; digest: string };

export function McpTools({ serverId }: { serverId: string }) {
  const { client } = useTransport();
  const cache = useQueryClient();
  const drafts = useContext(DraftContext);
  const draftKey = `mcp-tools:${serverId}`;
  const [draft, setDraft] = useState<ToolDraft | undefined>(() => {
    const saved = drafts.get(draftKey);
    return saved ? (JSON.parse(saved.content) as ToolDraft) : undefined;
  });
  useEffect(() => {
    if (!draft) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [draft]);
  const [search, setSearch] = useState("");
  const [saved, setSaved] = useState(false);
  const settings = useQuery({
    queryKey: ["mcp-tools", serverId],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/mcp/{server_id}/tools", {
          params: { path: { server_id: serverId } },
          signal,
        }),
      ),
  });
  const discovery = useQuery({
    queryKey: ["mcp-discovery", serverId],
    enabled: false,
    retry: false,
    queryFn: () =>
      result(
        client.POST("/api/mcp/{server_id}/tools/discover", {
          params: { path: { server_id: serverId } },
        }),
      ),
  });
  const save = useMutation({
    mutationFn: () =>
      result(
        client.PUT("/api/mcp/{server_id}/tools", {
          params: { path: { server_id: serverId } },
          body: { tools: draft!.tools, resource_digest: draft!.digest },
        }),
      ),
    onSuccess: async () => {
      await Promise.all([
        cache.invalidateQueries({ queryKey: ["mcp-tools", serverId] }),
        cache.invalidateQueries({ queryKey: ["sources"] }),
      ]);
      drafts.delete(draftKey);
      setDraft(undefined);
      setSaved(true);
    },
  });
  const tools = draft ? draft.tools : (settings.data?.tools ?? null);
  const update = (next: string[] | null) => {
    setSaved(false);
    const value = {
      tools: next,
      digest: draft?.digest ?? settings.data!.resource_digest,
    };
    drafts.set(draftKey, {
      content: JSON.stringify(value),
      base: null,
      digest: value.digest,
      replacement: false,
    });
    setDraft(value);
  };
  const descriptors = discovery.data;
  const names = new Set(descriptors?.map((tool) => tool.name) ?? []);
  const rows: (Schema<"McpToolDescriptor"> & { unavailable?: boolean })[] = [
    ...(tools ?? [])
      .filter((name) => !names.has(name))
      .map((name) => ({
        name,
        description: descriptors
          ? "No longer provided by this server."
          : "Load tools to view its description.",
        input_schema: {},
        unavailable: !!descriptors,
      })),
    ...(descriptors ?? []),
  ];
  const filtered = rows.filter((tool) =>
    `${tool.name} ${tool.description}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <div className={styles.editor}>
      <SettingsSection title="Tool access">
        <ErrorNotice error={settings.error} />
        {settings.isPending ? (
          <p className={styles.note}>Loading settings…</p>
        ) : (
          settings.data && (
            <SettingsChoice
              label="Available tools"
              value={tools === null ? "all" : "selected"}
              disabled={save.isPending || discovery.isFetching}
              description={
                tools === null
                  ? "Includes new tools added by this server."
                  : "New tools stay disabled until selected."
              }
              options={[
                { value: "all", label: "All tools" },
                { value: "selected", label: "Selected tools" },
              ]}
              onValueChange={async (value) => {
                if (value === "all") {
                  update(null);
                  return;
                }
                const inventory =
                  descriptors ?? (await discovery.refetch()).data;
                if (inventory) update(inventory.map((tool) => tool.name));
              }}
            />
          )
        )}
      </SettingsSection>
      {settings.data && (
        <section aria-label="Tools" className={styles.inventory}>
          <div className={styles.heading}>
            <h3>
              Tools{" "}
              <span>
                {tools === null
                  ? descriptors
                    ? `${descriptors.length} available`
                    : "All enabled"
                  : descriptors
                    ? `${tools.filter((name) => names.has(name)).length} of ${descriptors.length} enabled`
                    : `${tools.length} enabled`}
              </span>
            </h3>
            <Button
              variant="ghost"
              loading={discovery.isFetching}
              onClick={() => discovery.refetch()}
            >
              <ArrowClockwise aria-hidden="true" />
              {descriptors ? "Refresh" : "Load tools"}
            </Button>
          </div>
          <div className={styles.toolbar}>
            <Input
              aria-label="Search tools"
              placeholder="Search tools…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            {tools !== null && (
              <div className={styles.bulk}>
                <Button
                  variant="ghost"
                  disabled={!descriptors || save.isPending}
                  onClick={() =>
                    update([
                      ...new Set([
                        ...tools,
                        ...filtered
                          .filter((tool) => !tool.unavailable)
                          .map((tool) => tool.name),
                      ]),
                    ])
                  }
                >
                  {search ? "Select results" : "Select all"}
                </Button>
                <Button
                  variant="ghost"
                  disabled={!tools.length || save.isPending}
                  onClick={() =>
                    update(
                      search
                        ? tools.filter(
                            (name) =>
                              !filtered.some((tool) => tool.name === name),
                          )
                        : [],
                    )
                  }
                >
                  {search ? "Clear results" : "Clear"}
                </Button>
              </div>
            )}
          </div>
          <ErrorNotice error={discovery.error} />
          <div
            className={`${styles.list} a13n-scrollbar`}
            role="region"
            aria-label="Available MCP tools"
            tabIndex={0}
          >
            {filtered.map((tool) => (
              <ToolRow
                key={tool.name}
                tool={tool}
                checked={tools === null || tools.includes(tool.name)}
                disabled={tools === null || save.isPending}
                onChange={(checked) =>
                  update(
                    checked
                      ? [...(tools ?? []), tool.name]
                      : tools!.filter((name) => name !== tool.name),
                  )
                }
              />
            ))}
            {!filtered.length && (
              <p className={styles.empty}>
                {descriptors
                  ? search
                    ? "No matching tools."
                    : "This server has no tools."
                  : "Load tools to choose which ones to enable."}
              </p>
            )}
          </div>
          {tools?.length === 0 && (
            <p className={styles.note}>
              No tools enabled. The connection is kept.
            </p>
          )}
          <ErrorNotice error={save.error} />
          {save.error instanceof ApiError &&
            save.error.code === "configuration_source_changed" && (
              <Button
                variant="outline"
                onClick={async () => {
                  await settings.refetch();
                  drafts.delete(draftKey);
                  setDraft(undefined);
                  save.reset();
                }}
              >
                Reload saved selection
              </Button>
            )}
          <div className={styles.footer}>
            <span role="status">
              {saved
                ? "Saved for subsequent runs."
                : "Applies to all agents using this server."}
            </span>
            <div>
              {draft && (
                <Button
                  variant="ghost"
                  disabled={save.isPending}
                  onClick={() => {
                    drafts.delete(draftKey);
                    setDraft(undefined);
                    save.reset();
                  }}
                >
                  Cancel
                </Button>
              )}
              <Button
                disabled={!draft || save.isPending || discovery.isFetching}
                loading={save.isPending}
                onClick={() => save.mutate()}
              >
                Save
              </Button>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}

function ToolRow({
  tool,
  checked,
  disabled,
  onChange,
}: {
  tool: Schema<"McpToolDescriptor"> & { unavailable?: boolean };
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className={styles.tool}>
      <div className={styles.toolHeader}>
        <button
          type="button"
          className={styles.toolSummary}
          aria-expanded={open}
          aria-controls={id}
          onClick={() => setOpen(!open)}
        >
          <span className={styles.toolCopy}>
            <span className={styles.toolName}>{tool.name}</span>
            {tool.unavailable && (
              <span className={styles.unavailable}>Unavailable</span>
            )}
            <span className={styles.toolDescription}>
              {tool.description || "No description provided."}
            </span>
          </span>
          <CaretRight aria-hidden="true" />
        </button>
        <div className={styles.toolCheck}>
          <Checkbox
            aria-label={`Enable ${tool.name}`}
            checked={checked}
            disabled={disabled}
            onCheckedChange={(value) => onChange(!!value)}
          />
        </div>
      </div>
      {open && (
        <div className={styles.detail} id={id}>
          <p>{tool.description || "No description provided."}</p>
          <h4>Parameters</h4>
          <pre>{JSON.stringify(tool.input_schema, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
