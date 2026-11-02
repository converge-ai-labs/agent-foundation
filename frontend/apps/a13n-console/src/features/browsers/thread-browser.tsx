import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField } from "a13n-ui";
import { GlobeIcon, XIcon } from "@phosphor-icons/react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, rowTag, type Schema } from "../../shared/api";
import { ErrorNotice, Loading, StatePill } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import {
  conversationQueries,
  invalidateConversation,
} from "../conversations/api";
import { useInteractiveThread } from "../conversations/transcript/run-actions";
import { useBrowser, useBrowserOptions } from "./api";
import { BrowserLifecycle } from "./page";
import { BrowserViewer } from "./viewer";

export function ThreadBrowserPanel({
  thread,
  active = true,
  onClose,
}: {
  thread: Schema["ThreadView"];
  active?: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-4">
        <GlobeIcon size={16} aria-hidden="true" />
        <h2 className="flex-1 text-sm font-medium">{t("Browser")}</h2>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("Close browser panel")}
          onClick={onClose}
        >
          <XIcon size={16} />
        </Button>
      </div>
      <div className="a13n-scrollbar min-h-0 flex-1 overflow-y-auto p-4">
        <ThreadBrowser thread={thread} active={active} />
      </div>
    </div>
  );
}

function ThreadBrowser({
  thread: initial,
  active,
}: {
  thread: Schema["ThreadView"];
  active: boolean;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace, can, basePath } = useWorkspace();
  const thread = useQuery(
    conversationQueries(client, workspace.id).thread(initial.id),
  );
  const interactive = useInteractiveThread(thread.data);
  const editable = can("run") && interactive && !thread.data?.archived_at;
  const [editing, setEditing] = useState(false);
  const binding = useQuery({
    queryKey: ["thread-browser", workspace.id, initial.id],
    queryFn: async ({ signal }) => {
      const scoped = client.workspace(workspace.id);
      // Keep the version observed before this binding read as its edit basis.
      // A stream update may refresh the independent Thread cache at any time.
      const basis = data(
        await scoped.GET("/api/v1/threads/{thread_id}", {
          params: { path: { thread_id: initial.id } },
          signal,
        }),
      );
      const selection = data(
        await scoped.GET("/api/v1/threads/{thread_id}/browser", {
          params: { path: { thread_id: initial.id } },
          signal,
        }),
      );
      return { basis, selection };
    },
  });
  const selection = binding.data?.selection;
  const browser = useBrowser(selection?.browser_id);
  return (
    <div className="flex flex-col gap-4">
      <ErrorNotice
        error={binding.error ?? thread.error ?? browser.error}
        retry={() => {
          void binding.refetch();
          void thread.refetch();
          if (selection) void browser.refetch();
        }}
      />
      {binding.isPending ? (
        <Loading variant="form" />
      ) : editing && editable && binding.data ? (
        <BindingForm
          thread={binding.data.basis}
          initial={binding.data.selection}
          close={() => setEditing(false)}
        />
      ) : (
        !binding.error && (
          <div className="flex items-start justify-between gap-3">
            {selection ? (
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="min-w-0 break-words text-sm font-medium">
                    {browser.data?.name ?? selection.browser_id}
                  </h3>
                  {browser.data && <StatePill state={browser.data.status} />}
                </div>
                <span className="text-xs text-muted-foreground">
                  {t(
                    selection.tab_access === "global"
                      ? "All browser tabs"
                      : "Thread-owned tabs",
                  )}
                </span>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                {t("No browser selected.")}
              </p>
            )}
            {editable && !binding.error && (
              <div className="shrink-0">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setEditing(true)}
                >
                  {t("Select browser")}
                </Button>
              </div>
            )}
          </div>
        )
      )}
      {browser.data && (
        <>
          {editable && (
            <BrowserControl
              key={browser.data.id}
              browser={browser.data}
              threadId={initial.id}
              active={active}
            />
          )}
          <BrowserLifecycle
            browser={browser.data}
            allowed={editable}
            showDelete={false}
          />
        </>
      )}
      <div>
        <Button
          variant="ghost"
          size="sm"
          render={<Link to={`${basePath}/browsers`} />}
        >
          {t("Manage browsers")}
        </Button>
      </div>
    </div>
  );
}

function BindingForm({
  thread,
  initial,
  close,
}: {
  thread: Schema["ThreadView"];
  initial: Schema["BrowserBinding"] | null;
  close: () => void;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const cache = useQueryClient();
  const { workspace } = useWorkspace();
  const [basis, setBasis] = useState(thread);
  const [id, setId] = useState(initial?.browser_id ?? "none");
  const [tabs, setTabs] = useState<"global" | "thread">(
    initial?.tab_access ?? "thread",
  );
  const [preparation, setPreparation] = useState<"first_use" | "run_start">(
    initial?.preparation ?? "first_use",
  );
  const options = useBrowserOptions();
  const available = (options.data ?? []).filter(
    (item) => !["deleted", "deleting", "lost"].includes(item.status),
  );
  const missing = id !== "none" && !available.some((item) => item.id === id);
  const save = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .PUT("/api/v1/threads/{thread_id}/browser", {
          params: { path: { thread_id: basis.id } },
          headers: ifMatch(rowTag(basis)),
          body:
            id === "none"
              ? null
              : { browser_id: id, tab_access: tabs, preparation },
        })
        .then(data),
    onSuccess: () => {
      void cache.invalidateQueries({
        queryKey: ["thread-browser", workspace.id, basis.id],
      });
      void invalidateConversation(cache, workspace.id, {
        sessionId: basis.session_id,
        threadId: basis.id,
      });
      close();
    },
  });
  const reload = useMutation({
    mutationFn: () =>
      cache.fetchQuery(
        conversationQueries(client, workspace.id).thread(thread.id),
      ),
    onSuccess: (current) => {
      setBasis(current);
      save.reset();
    },
  });
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <p className="text-xs text-muted-foreground">
        {t("Applies to the next run.")}
      </p>
      <ChoiceField
        label={t("Browser")}
        value={id}
        onValueChange={(value) => setId(String(value))}
        options={[
          { value: "none", label: t("None") },
          ...(missing ? [{ value: id, label: id }] : []),
          ...available.map((item) => ({ value: item.id, label: item.name })),
        ]}
      />
      {missing && !options.isPending && !options.error && (
        <p role="status" className="text-sm text-muted-foreground">
          {t(
            "The selected browser is unavailable. Select another browser or None.",
          )}
        </p>
      )}
      {id !== "none" && (
        <>
          <ChoiceField
            label={t("Tab access")}
            value={tabs}
            onValueChange={(value) =>
              setTabs(value === "global" ? "global" : "thread")
            }
            options={[
              { value: "thread", label: t("Thread-owned tabs") },
              { value: "global", label: t("All browser tabs") },
            ]}
          />
          <ChoiceField
            label={t("Start browser")}
            value={preparation}
            onValueChange={(value) =>
              setPreparation(value === "run_start" ? "run_start" : "first_use")
            }
            options={[
              { value: "first_use", label: t("On first browser tool use") },
              { value: "run_start", label: t("When the run starts") },
            ]}
          />
        </>
      )}
      <ErrorNotice
        error={reload.error ?? save.error ?? options.error}
        retry={() => {
          // Explicit reload rebases the version, never the user's selection.
          reload.mutate();
          void options.refetch();
        }}
      />
      <FormActions
        pending={save.isPending || reload.isPending}
        disabled={
          id !== "none" && (missing || options.isPending || !!options.error)
        }
        onCancel={close}
        label={t("Save selection")}
      />
    </form>
  );
}

function BrowserControl({
  browser,
  threadId,
  active,
}: {
  browser: Schema["Browser"];
  threadId: string;
  active: boolean;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const cache = useQueryClient();
  const { workspace } = useWorkspace();
  const [viewing, setViewing] = useState(false);
  const operation = useMutation({
    mutationFn: (action: "takeover" | "handback") =>
      client
        .workspace(workspace.id)
        .POST(`/api/v1/browsers/{browser_id}/${action}`, {
          params: { path: { browser_id: browser.id } },
          headers: ifMatch(rowTag(browser)),
          ...(action === "takeover" ? { body: { thread_id: threadId } } : {}),
        })
        .then(data),
    onSuccess: (next) => {
      cache.setQueryData(
        ["browsers", workspace.id, "detail", browser.id],
        next,
      );
      void cache.invalidateQueries({ queryKey: ["browsers", workspace.id] });
    },
  });
  if (browser.source === "external_cdp")
    return (
      <p className="text-sm text-muted-foreground">
        {t("External CDP browsers do not provide a viewer or human control.")}
      </p>
    );
  if (["stopped", "deleted", "deleting", "lost"].includes(browser.status))
    return null;
  return (
    <section className="flex flex-col gap-3" aria-label={t("Browser control")}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-xs text-muted-foreground">
          {t(`browser.control.${browser.control}`, {
            defaultValue: browser.control,
          })}
        </span>
        <div className="flex flex-wrap gap-2">
          {browser.control === "automation" && browser.status === "ready" && (
            <Button
              size="sm"
              loading={operation.isPending}
              onClick={() => operation.mutate("takeover")}
            >
              {t("Take control")}
            </Button>
          )}
          {browser.control === "human" && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setViewing(!viewing)}
            >
              {t(viewing ? "Close viewer" : "Open viewer")}
            </Button>
          )}
          {(browser.control === "draining" || browser.control === "human") && (
            <Button
              variant="outline"
              size="sm"
              loading={operation.isPending}
              onClick={() => {
                setViewing(false);
                operation.mutate("handback");
              }}
            >
              {t("Return control")}
            </Button>
          )}
        </div>
      </div>
      {browser.control === "automation" && browser.status === "ready" && (
        <p className="text-xs text-muted-foreground">
          {t("Take control to pause the agent and use the browser.")}
        </p>
      )}
      {browser.control === "draining" && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("Pausing the agent…")}
        </p>
      )}
      {browser.control === "returning" && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("Returning control to the agent…")}
        </p>
      )}
      <ErrorNotice
        error={operation.error}
        retry={() => {
          void cache.invalidateQueries({
            queryKey: ["browsers", workspace.id],
          });
          operation.reset();
        }}
      />
      {active && viewing && browser.control === "human" && (
        <BrowserViewer
          key={`${browser.generation}:${browser.episode}`}
          id={browser.id}
          generation={browser.generation}
          episode={browser.episode}
          controlExpiresAt={browser.control_expires_at}
        />
      )}
    </section>
  );
}
