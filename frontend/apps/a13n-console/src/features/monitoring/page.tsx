import {
  DatabaseIcon,
  DesktopIcon,
  DotsThreeIcon,
  MagnifyingGlassMinusIcon,
  PencilSimpleIcon,
  PlusIcon,
} from "@phosphor-icons/react";
import {
  useIsFetching,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  FormField,
  Input,
  Menu,
  MenuItem,
  MenuLinkItem,
  MenuPopup,
  MenuTrigger,
  ModalFrame,
} from "a13n-ui";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
  type SetStateAction,
} from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";
import GridLayout, { useContainerWidth, type Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ApiError } from "../../service-client";
import { allPages, data, rowTag } from "../../shared/api";
import { Empty } from "../../shared/collection";
import { useMountedModal, Confirm, ConflictNotice } from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { Page, PageActions, SaveBar } from "../../shared/page";
import { RefreshButton, TimeRangePicker } from "../../shared/time-range";
import {
  monitoringApi,
  type Dashboard,
  type DashboardConfig,
  type Panel,
  type Target,
  type TimeRange,
} from "./api";
import { builtinName } from "./labels";
import { MetricsPanel } from "./panel";
import { newPanel, PanelEditor } from "./panel-editor";
import { targetInventory, TargetPicker } from "./target-picker";
import styles from "./monitoring.module.css";

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
  const sources = useQuery(api.sources());
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
  const targets = targetInventory(environments.data ?? [], devices.data ?? []);
  const targetOption = targets.find((item) => item.value === targetKey);
  const metricPanels =
    panels.length > 0 &&
    panels.every((panel) =>
      panel.queries.every((query) => query.kind === "metric"),
    );
  const fetching = useIsFetching({
    queryKey: ["metric-series", workspace.id],
  });
  function updateParam(key: string, value: string) {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    });
  }
  const refreshNow = useCallback(() => {
    setNow(Math.floor(Date.now() / 1000));
    setRevision((value) => value + 1);
  }, []);
  useEffect(() => {
    if (!interval || pinned) return;
    const refreshVisible = () => {
      if (!document.hidden) refreshNow();
    };
    const timer = window.setInterval(refreshVisible, interval * 1000);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, [interval, pinned, refreshNow]);
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
          config: draft.config,
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
    setSeconds(undefined);
    setRefresh(undefined);
    setPinned(undefined);
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
            title={
              selected?.builtin ? builtinName(t, panel.title) : panel.title
            }
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
    [
      panels,
      targetKey,
      range,
      revision,
      !!draft,
      busy,
      workspace.id,
      selected?.builtin,
      t,
    ],
  );
  return (
    <Page title={t("Metrics")} className={styles.dashboardPage}>
      <PageActions secondary>
        {can("write") && !draft && (
          <Button
            className={styles.editDashboard}
            aria-label={t("Edit dashboard")}
            title={t("Edit dashboard")}
            disabled={!selected}
            onClick={() => {
              setSeconds(undefined);
              setRefresh(undefined);
              if (selected)
                setDraft({
                  basis: selected,
                  name: selected.name,
                  config: structuredClone(selected.config),
                });
            }}
          >
            <PencilSimpleIcon size={16} />
            <span>{t("Edit dashboard")}</span>
          </Button>
        )}
        <Menu>
          <MenuTrigger
            render={
              <Button
                variant="outline"
                size="icon"
                aria-label={t("Actions")}
                title={t("Actions")}
              />
            }
          >
            <DotsThreeIcon size={20} />
          </MenuTrigger>
          <MenuPopup align="end">
            {!draft && can("write") && (
              <MenuItem onClick={() => setCreating(true)}>
                <PlusIcon />
                {t("New dashboard")}
              </MenuItem>
            )}
            <MenuLinkItem
              render={<Link to={`${basePath}/monitoring/sources`} />}
            >
              <DatabaseIcon />
              {t("Data sources")}
            </MenuLinkItem>
            <MenuLinkItem
              render={<Link to={`${basePath}/monitoring/devices`} />}
            >
              <DesktopIcon />
              {t("Devices")}
            </MenuLinkItem>
          </MenuPopup>
        </Menu>
      </PageActions>
      <ErrorNotice
        error={
          dashboards.error ??
          environments.error ??
          devices.error ??
          sources.error
        }
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
                label: item.builtin ? builtinName(t, item.name) : item.name,
              }))}
              onValueChange={(id) => {
                updateParam("dashboard", id);
                setSeconds(undefined);
                setRefresh(undefined);
                setPinned(undefined);
              }}
            />
            <TargetPicker
              items={targets}
              sources={sources.data ?? []}
              value={targetKey}
              onChange={(key) => updateParam("target", key)}
            />
            <div className={styles.time}>
              <TimeRangePicker
                value={
                  pinned
                    ? {
                        start: new Date(pinned.start * 1000),
                        end: new Date(pinned.end * 1000),
                      }
                    : { preset: duration }
                }
                presets={timePresets}
                maxDays={90}
                disabled={busy}
                onChange={(value) => {
                  if ("start" in value) {
                    setPinned({
                      start: Math.floor(value.start.getTime() / 1000),
                      end: Math.floor(value.end.getTime() / 1000),
                    });
                    return;
                  }
                  setSeconds(value.preset);
                  if (draft)
                    setDraft({
                      ...draft,
                      config: { ...draft.config, range_seconds: value.preset },
                    });
                  setPinned(undefined);
                  refreshNow();
                }}
              />
              {pinned && (
                <>
                  <Button
                    variant="outline"
                    size="icon"
                    aria-label={t("Zoom out")}
                    title={t("Zoom out")}
                    onClick={() => {
                      const midpoint = (range.start + range.end) / 2;
                      const half = Math.min(
                        7776000 / 2,
                        range.end - range.start,
                      );
                      setPinned({
                        start: Math.floor(midpoint - half),
                        end: Math.ceil(midpoint + half),
                      });
                    }}
                  >
                    <MagnifyingGlassMinusIcon aria-hidden="true" />
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => {
                      setPinned(undefined);
                      refreshNow();
                    }}
                  >
                    {t("Back to live")}
                  </Button>
                </>
              )}
              <ChoiceField
                label={t("Auto refresh")}
                variant="filter"
                value={String(interval)}
                disabled={busy}
                options={refreshOptions(t)}
                onValueChange={(value) => {
                  const seconds = Number(
                    value,
                  ) as DashboardConfig["refresh_seconds"];
                  setRefresh(seconds);
                  if (draft)
                    setDraft({
                      ...draft,
                      config: { ...draft.config, refresh_seconds: seconds },
                    });
                }}
              />
              <RefreshButton pending={fetching > 0} onRefresh={refreshNow} />
            </div>
          </div>
          {draft && (
            <div className={styles.editBar}>
              <label className={styles.nameField}>
                <span>{t("Dashboard name")}</span>
                <Input
                  disabled={busy}
                  maxLength={128}
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                />
              </label>
              <div className={styles.editActions}>
                {draft.basis.builtin && (
                  <Button
                    variant="outline"
                    loading={reset.isPending}
                    disabled={busy}
                    onClick={() => reset.mutate()}
                  >
                    {t("Restore defaults")}
                  </Button>
                )}
                <Button
                  variant="outline"
                  disabled={busy || panels.length >= 24}
                  onClick={() => setEditing(newPanel(panels, t("New panel")))}
                >
                  <PlusIcon aria-hidden="true" />
                  {t("Add panel")}
                </Button>
              </div>
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
          ) : !target && !draft && metricPanels ? (
            <Empty
              title={t("Select a target to explore metrics")}
              description={t("Choose an Environment or Device above.")}
            />
          ) : targetOption && !targetOption.source && !draft && metricPanels ? (
            <Empty
              icon={<DatabaseIcon aria-hidden="true" />}
              title={t("No metrics data source")}
              description={
                targetOption.kind === "device"
                  ? t(
                      "Choose a metrics data source for this Device to chart its metrics.",
                    )
                  : t(
                      "Choose a metrics data source in the Environment's details to chart its metrics.",
                    )
              }
              action={
                <Button
                  variant="outline"
                  render={
                    <Link
                      to={
                        targetOption.kind === "device"
                          ? `${basePath}/monitoring/devices`
                          : `${basePath}/environments/instances`
                      }
                    />
                  }
                >
                  {targetOption.kind === "device"
                    ? t("Go to devices")
                    : t("Go to environments")}
                </Button>
              }
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
          adding={!panels.some((item) => item.id === editing.id)}
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
  onClose: onClosed,
  onCreated,
}: {
  api: ReturnType<typeof monitoringApi>;
  onClose: () => void;
  onCreated: (dashboard: Dashboard) => void;
}) {
  const [created, setCreated] = useState<Dashboard>();
  const { close: onClose, modalProps } = useMountedModal(() => {
    if (created) onCreated(created);
    else onClosed();
  });
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => api.create({ name, config: { panels: [] } }),
    onSuccess: (result) => {
      setCreated(result.value);
      onClose();
    },
  });
  return (
    <ModalFrame
      {...modalProps}
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
const timePresets = [900, 3600, 21600, 86400, 604800, 2592000];
const refreshOptions = (t: TFunction) => [
  { value: "0", label: t("Off") },
  { value: "10", label: t("10 seconds") },
  { value: "30", label: t("30 seconds") },
  { value: "60", label: t("1 minute") },
  { value: "300", label: t("5 minutes") },
];
