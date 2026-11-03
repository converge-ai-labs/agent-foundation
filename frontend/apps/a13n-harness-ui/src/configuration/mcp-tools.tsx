import { useTranslation } from "../i18n";
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
  const { t } = useTranslation();
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
          ? t("No longer provided by this server.")
          : t("Load tools to view its description."),
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
      <SettingsSection title={t("Tool access")}>
        <ErrorNotice error={settings.error} />
        {settings.isPending ? (
          <p className={styles.note}>{t("Loading settings…")}</p>
        ) : (
          settings.data && (
            <SettingsChoice
              label={t("Available tools")}
              value={tools === null ? "all" : "selected"}
              disabled={save.isPending || discovery.isFetching}
              description={
                tools === null
                  ? t("Includes new tools added by this server.")
                  : t("New tools stay disabled until selected.")
              }
              options={[
                { value: "all", label: t("All tools") },
                { value: "selected", label: t("Selected tools") },
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
        <section aria-label={t("Tools")} className={styles.inventory}>
          <div className={styles.heading}>
            <h3>
              {t("Tools")}{" "}
              <span>
                {tools === null
                  ? descriptors
                    ? t("{{count}} available", { count: descriptors.length })
                    : t("All enabled")
                  : descriptors
                    ? t("{{enabled}} of {{total}} enabled", {
                        enabled: tools.filter((name) => names.has(name)).length,
                        total: descriptors.length,
                      })
                    : t("{{count}} enabled", { count: tools.length })}
              </span>
            </h3>
            <Button
              variant="ghost"
              loading={discovery.isFetching}
              onClick={() => discovery.refetch()}
            >
              <ArrowClockwise aria-hidden="true" />
              {descriptors ? t("Refresh") : t("Load tools")}
            </Button>
          </div>
          <div className={styles.toolbar}>
            <Input
              aria-label={t("Search tools")}
              placeholder={t("Search tools…")}
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
                  {search ? t("Select results") : t("Select all")}
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
                  {search ? t("Clear results") : t("Clear")}
                </Button>
              </div>
            )}
          </div>
          <ErrorNotice error={discovery.error} />
          <div
            className={`${styles.list} a13n-scrollbar`}
            role="region"
            aria-label={t("Available MCP tools")}
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
                    ? t("No matching tools.")
                    : t("This server has no tools.")
                  : t("Load tools to choose which ones to enable.")}
              </p>
            )}
          </div>
          {tools?.length === 0 && (
            <p className={styles.note}>
              {t("No tools enabled. The connection is kept.")}
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
                {t("Reload saved selection")}
              </Button>
            )}
          <div className={styles.footer}>
            <span role="status">
              {saved
                ? t("Saved for subsequent runs.")
                : t("Applies to all agents using this server.")}
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
                  {t("Cancel")}
                </Button>
              )}
              <Button
                disabled={!draft || save.isPending || discovery.isFetching}
                loading={save.isPending}
                onClick={() => save.mutate()}
              >
                {t("Save")}
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
  const { t } = useTranslation();
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
              <span className={styles.unavailable}>{t("Unavailable")}</span>
            )}
            <span className={styles.toolDescription}>
              {tool.description || t("No description provided.")}
            </span>
          </span>
          <CaretRight aria-hidden="true" />
        </button>
        <div className={styles.toolCheck}>
          <Checkbox
            aria-label={t("Enable {{name}}", { name: tool.name })}
            checked={checked}
            disabled={disabled}
            onCheckedChange={(value) => onChange(!!value)}
          />
        </div>
      </div>
      {open && (
        <div className={styles.detail} id={id}>
          <p>{tool.description || t("No description provided.")}</p>
          <h4>{t("Parameters")}</h4>
          <pre>{JSON.stringify(tool.input_schema, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
