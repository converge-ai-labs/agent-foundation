import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input, ModalFrame } from "a13n-ui";
import {
  useEffect,
  useMemo,
  useState,
  type ReactNode,
  type SetStateAction,
} from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";
import GridLayout, { useContainerWidth, type Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ApiError } from "../../service-client";
import { allPages, data, rowTag } from "../../shared/api";
import { Empty } from "../../shared/collection";
import { Confirm, ConflictNotice } from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { Page, PageActions, SaveBar } from "../../shared/page";
import {
  monitoringApi,
  type Dashboard,
  type DashboardConfig,
  type Panel,
  type Target,
  type TimeRange,
} from "./api";
import { MetricsPanel } from "./panel";
import { newPanel, PanelEditor } from "./panel-editor";
import styles from "./monitoring.module.css";

const liveRange = (seconds: number): TimeRange => {
  const end = Math.floor(Date.now() / 1000);
  return { start: end - seconds, end };
};
type Draft = { basis: Dashboard; name: string; config: DashboardConfig };
export function DashboardsPage() {
  const { workspace } = useWorkspace();
  const [params] = useSearchParams();
  return (
    <DashboardWorkspace
      key={`${workspace.id}:${params.get("dashboard") ?? "builtin"}`}
    />
  );
}
function DashboardWorkspace() {
  const { workspace, can, basePath } = useWorkspace(),
    client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const api = useMemo(
    () => monitoringApi(client, workspace.id),
    [client, workspace.id],
  );
  const dashboards = useQuery(api.dashboards());
  const [params, setParams] = useSearchParams();
  const selected =
    dashboards.data?.find((item) => item.id === params.get("dashboard")) ??
    (params.has("dashboard")
      ? undefined
      : dashboards.data?.find((item) => item.builtin));
  // Session-memory drafts survive SPA navigation; authentication changes clear
  // this cache. Each dashboard retains its own opening ETag, never browser storage.
  const draftKey = ["metrics-dashboard-draft", workspace.id, selected?.id];
  const { data: savedDraft } = useQuery<Draft | null>({
    queryKey: draftKey,
    queryFn: () => null,
    enabled: false,
    initialData: null,
    gcTime: Infinity,
  });
  const draft = savedDraft ?? undefined;
  function setDraft(value: SetStateAction<Draft | undefined>) {
    cache.setQueryData<Draft | null>(
      draftKey,
      (previous) =>
        (typeof value === "function" ? value(previous ?? undefined) : value) ??
        null,
    );
  }
  const [editing, setEditing] = useState<Panel>();
  const [creating, setCreating] = useState(false);
  const [seconds, setSeconds] = useState<number>();
  const [refresh, setRefresh] = useState<number>();
  const [pinned, setPinned] = useState<TimeRange>();
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));
  const [revision, setRevision] = useState(0);
  const config = draft?.config ?? selected?.config;
  const duration = seconds ?? config?.range_seconds ?? 3600;
  const interval = refresh ?? config?.refresh_seconds ?? 30;
  const range = useMemo(
    () => pinned ?? { start: now - duration, end: now },
    [pinned, now, duration],
  );
  const panels = config?.panels ?? [];
  const targetKey = params.get("target") ?? "";
  const target: Target | null = targetKey.startsWith("environment:")
    ? { kind: "environment", id: targetKey.slice(12) }
    : targetKey.startsWith("device:")
      ? { kind: "device", id: targetKey.slice(7) }
      : null;
  const environments = useQuery({
    queryKey: ["monitoring-environments", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client
          .workspace(workspace.id)
          .GET("/api/v1/environments", {
            params: { query: { cursor, limit: 100 } },
            signal,
          })
          .then(data),
      ),
  });
  const devices = useQuery({
    queryKey: ["monitoring-devices", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client
          .workspace(workspace.id)
          .GET("/api/v1/devices", {
            params: { query: { cursor, limit: 100 } },
            signal,
          })
          .then(data),
      ),
  });
  const targetOptions = [
    { value: "", label: t("Select target") },
    ...(environments.data ?? []).map((item) => ({
      value: `environment:${item.id}`,
      label: item.name,
      group: t("Environments"),
    })),
    ...(devices.data ?? []).map((item) => ({
      value: `device:${item.id}`,
      label: item.name,
      group: t("Devices"),
    })),
  ];
  function updateParam(key: string, value: string) {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    });
  }
  function refreshNow() {
    setNow(Math.floor(Date.now() / 1000));
    setRevision((value) => value + 1);
  }
  useEffect(() => {
    if (!interval || pinned) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) refreshNow();
    }, interval * 1000);
    return () => window.clearInterval(timer);
  }, [interval, pinned]);
  useEffect(() => {
    if (!draft) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [!!draft]);
  const invalidate = () =>
    cache.invalidateQueries({ queryKey: ["metrics-dashboards", workspace.id] });
  const save = useMutation({
    onMutate: () => draft,
    mutationFn: async () => {
      if (!draft) return;
      return api.save(
        draft.basis.id,
        {
          name: draft.name,
          config: {
            ...draft.config,
            range_seconds: duration,
            refresh_seconds: interval as DashboardConfig["refresh_seconds"],
          },
        },
        rowTag(draft.basis),
      );
    },
    onSuccess: async (_result, _variables, submitted) => {
      await invalidate();
      if (cache.getQueryData(draftKey) === submitted) setDraft(undefined);
    },
  });
  const reset = useMutation({
    onMutate: () => draft,
    mutationFn: api.preset,
    onSuccess: (preset, _variables, submitted) => {
      if (draft && cache.getQueryData(draftKey) === submitted) {
        setDraft({ ...draft, config: preset });
        setSeconds(undefined);
        setRefresh(undefined);
        setPinned(undefined);
      }
    },
  });
  const busy = save.isPending || reset.isPending;
  function discard() {
    if (busy) return;
    setDraft(undefined);
    setEditing(undefined);
    save.reset();
    reset.reset();
  }
  const setPanels = (next: Panel[]) =>
    setDraft((value) =>
      value ? { ...value, config: { ...value.config, panels: next } } : value,
    );
  const layout = useMemo(
    () =>
      panels.map((panel) => ({
        i: panel.id,
        ...panel.position,
        minW: 3,
        minH: 3,
        maxH: 16,
      })),
    [panels],
  );
  function layoutChange(next: Layout) {
    if (!draft || busy) return;
    const positions = new Map(next.map((item) => [item.i, item]));
    const updated = panels.map((panel) => {
      const item = positions.get(panel.id);
      return item
        ? {
            ...panel,
            position: {
              x: item.x,
              y: Math.min(1000, item.y),
              w: item.w,
              h: item.h,
            },
          }
        : panel;
    });
    if (JSON.stringify(updated) !== JSON.stringify(panels)) setPanels(updated);
  }
  const children = useMemo(
    () =>
      panels.map((panel) => (
        <div key={panel.id}>
          <MetricsPanel
            panel={panel}
            target={target}
            range={range}
            revision={revision}
            syncKey={`dashboard-${workspace.id}`}
            onZoom={setPinned}
            onEdit={draft && !busy ? () => setEditing(panel) : undefined}
            onRemove={
              draft && !busy
                ? () => setPanels(panels.filter((item) => item.id !== panel.id))
                : undefined
            }
          />
        </div>
      )),
    [panels, targetKey, range, revision, !!draft, busy, workspace.id],
  );
  return (
    <Page
      title={t("Metrics dashboards")}
      description={t(
        "Explore infrastructure metrics. Edit panels, queries and layout in one place.",
      )}
    >
      <PageActions secondary>
        <Button
          variant="outline"
          render={<Link to={`${basePath}/monitoring/sources`} />}
        >
          {t("Data sources")}
        </Button>
        <Button
          variant="outline"
          render={<Link to={`${basePath}/monitoring/devices`} />}
        >
          {t("Devices")}
        </Button>
      </PageActions>
      <PageActions>
        {can("write") && !draft && (
          <Button
            disabled={!selected}
            onClick={() => {
              if (selected)
                setDraft({
                  basis: selected,
                  name: selected.name,
                  config: structuredClone(selected.config),
                });
            }}
          >
            {t("Edit dashboard")}
          </Button>
        )}
      </PageActions>
      <ErrorNotice
        error={dashboards.error ?? environments.error ?? devices.error}
      />
      {dashboards.isPending ? (
        <Loading variant="cards" />
      ) : (
        <>
          <div className={styles.toolbar}>
            <ChoiceField
              label={t("Dashboard")}
              variant="filter"
              value={selected?.id}
              disabled={!!draft}
              options={(dashboards.data ?? []).map((item) => ({
                value: item.id,
                label: item.name,
              }))}
              onValueChange={(id) => {
                updateParam("dashboard", id);
                setSeconds(undefined);
                setRefresh(undefined);
                setPinned(undefined);
              }}
            />
            <ChoiceField
              label={t("Target")}
              variant="filter"
              value={targetKey}
              options={targetOptions}
              onValueChange={(key) => updateParam("target", key)}
            />
            {!draft && can("write") && (
              <Button variant="ghost" onClick={() => setCreating(true)}>
                {t("New dashboard")}
              </Button>
            )}
            <div className={styles.time}>
              <ChoiceField
                label={t("Time range")}
                disabled={busy}
                variant="filter"
                value={String(duration)}
                options={[
                  [900, "15m"],
                  [3600, "1h"],
                  [21600, "6h"],
                  [86400, "24h"],
                  [604800, "7d"],
                  [2592000, "30d"],
                ].map(([value, label]) => ({
                  value: String(value),
                  label: String(label),
                }))}
                onValueChange={(value) => {
                  setSeconds(Number(value));
                  if (draft)
                    setDraft({
                      ...draft,
                      config: { ...draft.config, range_seconds: Number(value) },
                    });
                  setPinned(undefined);
                  setNow(Math.floor(Date.now() / 1000));
                }}
              />
              <ChoiceField
                label={t("Auto refresh")}
                disabled={busy}
                variant="filter"
                value={String(interval)}
                options={[0, 10, 30, 60, 300].map((value) => ({
                  value: String(value),
                  label: value ? `${value}s` : t("Off"),
                }))}
                onValueChange={(value) => {
                  const next = Number(
                    value,
                  ) as DashboardConfig["refresh_seconds"];
                  setRefresh(next);
                  if (draft)
                    setDraft({
                      ...draft,
                      config: { ...draft.config, refresh_seconds: next },
                    });
                }}
              />
              <Button variant="outline" onClick={refreshNow}>
                {t("Refresh")}
              </Button>
              {pinned && (
                <Button
                  variant="outline"
                  onClick={() => {
                    setPinned(undefined);
                    refreshNow();
                  }}
                >
                  {t("Back to live")}
                </Button>
              )}
            </div>
          </div>
          <div className={styles.toolbar}>
            <span className={styles.range}>
              {new Date(range.start * 1000).toLocaleString()} —{" "}
              {new Date(range.end * 1000).toLocaleString()} ·{" "}
              {pinned ? t("Paused") : t("Live")}
            </span>
            <AbsoluteRange range={range} onApply={setPinned} />
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                const midpoint = (range.start + range.end) / 2;
                const half = Math.min(7776000 / 2, range.end - range.start);
                setPinned({
                  start: Math.floor(midpoint - half),
                  end: Math.ceil(midpoint + half),
                });
              }}
            >
              {t("Zoom out")}
            </Button>
          </div>
          {draft && (
            <div className={styles.toolbar}>
              <Input
                aria-label={t("Dashboard name")}
                disabled={busy}
                maxLength={128}
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
              <Button
                variant="outline"
                disabled={busy || panels.length >= 24}
                onClick={() => setEditing(newPanel(panels))}
              >
                {t("Add panel")}
              </Button>
              {draft.basis.builtin && (
                <Button
                  variant="ghost"
                  loading={reset.isPending}
                  disabled={busy}
                  onClick={() => reset.mutate()}
                >
                  {t("Restore defaults")}
                </Button>
              )}
            </div>
          )}
          <ErrorNotice error={reset.error} />
          {save.error instanceof ApiError && save.error.status === 412 ? (
            <ConflictNotice
              title={t("Dashboard changed")}
              description={t(
                "Your draft is preserved. Reload the saved dashboard to discard your changes and review the latest version.",
              )}
              recover={{
                label: t("Reload saved dashboard"),
                onClick: () => {
                  void invalidate();
                  discard();
                },
              }}
            />
          ) : (
            <ErrorNotice error={save.error} />
          )}
          {!selected ? (
            <Empty
              title={t("Dashboard not found")}
              description={t("Choose another dashboard from the list.")}
            />
          ) : (
            <div className={styles.grid}>
              <DashboardGrid
                layout={layout}
                editing={!!draft && !busy}
                onLayoutChange={layoutChange}
              >
                {children}
              </DashboardGrid>
              {!panels.length && (
                <Empty
                  title={t("No panels yet")}
                  description={t(
                    "Edit this dashboard to add your first panel.",
                  )}
                />
              )}
            </div>
          )}
          {draft && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (!busy) save.mutate();
              }}
            >
              <SaveBar
                title={t("Unsaved changes")}
                consequence={t(
                  "Save this dashboard for everyone in the workspace.",
                )}
                onDiscard={discard}
                saveLabel={t("Save dashboard")}
                pending={busy}
                disabled={!draft.name.trim()}
              />
            </form>
          )}
          {selected && !selected.builtin && can("write") && !draft && (
            <Confirm
              title={t("Delete dashboard")}
              subject={selected.name}
              description={t(
                "Permanently delete this dashboard and its panel configuration.",
              )}
              trigger={t("Delete dashboard")}
              triggerVariant="ghost"
              danger
              action={() => api.remove(selected.id, rowTag(selected))}
              onSuccess={() => {
                updateParam("dashboard", "");
                void invalidate();
              }}
            />
          )}
        </>
      )}
      {editing && (
        <PanelEditor
          panel={editing}
          target={target}
          range={range}
          onClose={() => setEditing(undefined)}
          onApply={(panel) => {
            setPanels(
              panels.some((item) => item.id === panel.id)
                ? panels.map((item) => (item.id === panel.id ? panel : item))
                : [...panels, panel],
            );
            setEditing(undefined);
          }}
        />
      )}
      {creating && (
        <CreateDashboard
          api={api}
          onClose={() => setCreating(false)}
          onCreated={(dashboard) => {
            void invalidate();
            updateParam("dashboard", dashboard.id);
            setCreating(false);
            cache.setQueryData(
              ["metrics-dashboard-draft", workspace.id, dashboard.id],
              {
                basis: dashboard,
                name: dashboard.name,
                config: dashboard.config,
              },
            );
          }}
        />
      )}
    </Page>
  );
}
/** Mount the width observer with its element, after dashboard discovery completes. */
function DashboardGrid({
  layout,
  editing,
  onLayoutChange,
  children,
}: {
  layout: Layout;
  editing: boolean;
  onLayoutChange: (layout: Layout) => void;
  children: ReactNode;
}) {
  const { width, containerRef, mounted } = useContainerWidth();
  return (
    <div ref={containerRef}>
      {mounted &&
        (width < 700 ? (
          <div className={styles.mobile}>{children}</div>
        ) : (
          <GridLayout
            width={width}
            layout={layout}
            gridConfig={{
              cols: 12,
              rowHeight: 48,
              margin: [16, 16],
              containerPadding: [8, 8],
            }}
            dragConfig={{ enabled: editing, handle: ".dashboard-drag" }}
            resizeConfig={{ enabled: editing }}
            onLayoutChange={onLayoutChange}
          >
            {children}
          </GridLayout>
        ))}
    </div>
  );
}
function CreateDashboard({
  api,
  onClose,
  onCreated,
}: {
  api: ReturnType<typeof monitoringApi>;
  onClose: () => void;
  onCreated: (dashboard: Dashboard) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => api.create({ name, config: { panels: [] } }),
    onSuccess: (result) => onCreated(result.value),
  });
  return (
    <ModalFrame
      open
      onOpenChange={(value) => {
        if (!value && !create.isPending) onClose();
      }}
      title={t("New dashboard")}
      closeLabel={t("Close")}
    >
      <form
        className={styles.fields}
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate();
        }}
      >
        <FormField label={t("Name")}>
          <Input
            autoFocus
            required
            maxLength={128}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </FormField>
        <ErrorNotice error={create.error} />
        <FormActions
          label={t("Create dashboard")}
          pending={create.isPending}
          onCancel={onClose}
        />
      </form>
    </ModalFrame>
  );
}
function AbsoluteRange({
  range,
  onApply,
}: {
  range: TimeRange;
  onApply: (range: TimeRange) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState(""),
    [end, setEnd] = useState("");
  const local = (time: number) => {
    const date = new Date(time * 1000);
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
      .toISOString()
      .slice(0, 16);
  };
  const value = {
    start: Date.parse(start) / 1000,
    end: Date.parse(end) / 1000,
  };
  const valid =
    Number.isFinite(value.start) &&
    Number.isFinite(value.end) &&
    value.end - value.start >= 60 &&
    value.end - value.start <= 7776000;
  return (
    <ModalFrame
      open={open}
      onOpenChange={(value) => {
        if (value) {
          setStart(local(range.start));
          setEnd(local(range.end));
        }
        setOpen(value);
      }}
      title={t("Absolute time range")}
      closeLabel={t("Close")}
      trigger={
        <Button size="sm" variant="ghost">
          {t("Custom range")}
        </Button>
      }
    >
      <form
        className={styles.fields}
        onSubmit={(event) => {
          event.preventDefault();
          if (valid) {
            onApply(value);
            setOpen(false);
          }
        }}
      >
        <FormField label={t("From")}>
          <Input
            type="datetime-local"
            required
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </FormField>
        <FormField label={t("To")}>
          <Input
            type="datetime-local"
            required
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </FormField>
        <FormActions
          pending={false}
          label={t("Apply range")}
          disabled={!valid}
          onCancel={() => setOpen(false)}
        />
      </form>
    </ModalFrame>
  );
}
