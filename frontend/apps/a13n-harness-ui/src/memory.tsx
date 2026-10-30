import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import {
  Button,
  SearchPicker,
  Tabs,
  TabsList,
  TabsPanel,
  TabsTab,
} from "a13n-ui";
import { ArrowLeft, Brain, Folder, Globe } from "@phosphor-icons/react";
import { result } from "./transport/client";
import { useProjects, useStatus, useTransport } from "./transport/context";
import { Conversation } from "./conversations/conversation";
import { useMemoryThread } from "./conversations/queries";
import { MessageText } from "./conversations/message-text";
import { ErrorNotice, PageHeader } from "./shell/ui";
import type { Profile } from "./shell/presence";
import styles from "./memory.module.css";

export function MemoryNavigation({
  projectId,
  backTo = "/",
}: {
  projectId?: string | null;
  backTo?: string;
}) {
  const projects = useProjects();
  const [search] = useSearchParams();
  const selected = projectId === undefined ? search.get("project") : projectId;
  return (
    <div className={styles.navigation}>
      <Link className={styles.back} to={backTo}>
        <ArrowLeft size={16} />
        Back to chats
      </Link>
      <h2 className={styles.heading}>Memory</h2>
      <Link className={!selected ? styles.selected : styles.scope} to="/memory">
        <Globe size={18} />
        <span>Global</span>
      </Link>
      <h2>Projects</h2>
      <ErrorNotice
        error={projects.error}
        retry={() => void projects.refetch()}
      />
      {projects.data?.map((project) => (
        <Link
          key={project.project_id}
          className={
            selected === project.project_id ? styles.selected : styles.scope
          }
          to={`/memory?project=${encodeURIComponent(project.project_id)}`}
        >
          <Folder size={18} />
          <span>{project.name}</span>
        </Link>
      ))}
      {projects.data?.length === 0 && (
        <p className={styles.hint}>No configured projects.</p>
      )}
    </div>
  );
}

export function MemoryPage(props: {
  profile: Profile;
  unauthorized: () => void;
}) {
  const [search, setSearch] = useSearchParams();
  const projectId = search.get("project") ?? undefined;
  const status = useStatus();
  const enabled = !!status.data?.app.memory_organization?.memory_enabled;
  const projects = useProjects();
  const name = projectId
    ? (projects.data?.find((p) => p.project_id === projectId)?.name ??
      projectId)
    : "Global";
  const history = useMemoryThread(projectId, enabled);
  const thread = history.data?.threads[0];
  if (!status.data)
    return (
      <div className={styles.empty}>
        <ErrorNotice error={status.error} retry={() => void status.refetch()} />
        {!status.error && <p role="status">Loading memory…</p>}
      </div>
    );
  if (!enabled)
    return (
      <div className={styles.empty}>
        <h2>Memory is disabled</h2>
        <p>
          Enable memory to keep preferences and project knowledge across chats.
        </p>
        <Button variant="outline" render={<Link to="/settings" />}>
          Open settings
        </Button>
      </div>
    );
  return (
    <Tabs
      className={styles.workspace}
      value={search.get("view") === "activity" ? "activity" : "files"}
      onValueChange={(value) =>
        setSearch((current) => {
          const next = new URLSearchParams(current);
          if (value === "activity") next.set("view", "activity");
          else next.delete("view");
          return next;
        })
      }
    >
      <div className={styles.pageHeading}>
        <PageHeader
          title={`${name} memory`}
          description={
            projectId
              ? "Knowledge shared by chats in this project."
              : "Preferences and context shared across your chats."
          }
        />
        <TabsList variant="underline" aria-label="Memory views">
          <TabsTab value="files">Files</TabsTab>
          <TabsTab value="activity">History</TabsTab>
        </TabsList>
      </div>
      <TabsPanel value="files" keepMounted className={styles.filesPanel}>
        <MemoryFiles key={projectId ?? "global"} projectId={projectId} />
      </TabsPanel>
      <TabsPanel
        value="activity"
        keepMounted
        className={styles.history}
        aria-label="Memory organization history"
      >
        {thread && (
          <div className={styles.historyToolbar}>
            <span>Automatic memory updates</span>
            <Button
              variant="ghost"
              onClick={() =>
                setSearch((current) => {
                  const next = new URLSearchParams(current);
                  next.set("dialog", "details");
                  return next;
                })
              }
            >
              Usage & details
            </Button>
          </div>
        )}
        <ErrorNotice
          error={history.error}
          retry={() => void history.refetch()}
        />
        {thread ? (
          <Conversation
            key={thread.thread_id}
            threadId={thread.thread_id}
            {...props}
          />
        ) : (
          <div className={styles.empty}>
            <h2>{history.isPending ? "Loading history…" : "No updates yet"}</h2>
            <p>Automatic memory updates appear here.</p>
          </div>
        )}
      </TabsPanel>
    </Tabs>
  );
}

export function MemoryFiles({ projectId }: { projectId?: string }) {
  const { client } = useTransport();
  const [selected, select] = useState<string>();
  const files = useQuery({
    queryKey: ["memory", "files", projectId ?? null],
    refetchInterval: 5000,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/memory/files", {
          params: { query: { project_id: projectId } },
          signal,
        }),
      ),
  });
  const path = files.data?.some((file) => file.path === selected)
    ? selected
    : (files.data?.find((file) => file.path === "MEMORY.md")?.path ??
      files.data?.[0]?.path);
  const file = useQuery({
    queryKey: ["memory", "file", projectId ?? null, path],
    enabled: !!path,
    refetchInterval: 5000,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/memory/file", {
          params: { query: { project_id: projectId, path: path! } },
          signal,
        }),
      ),
  });
  return (
    <section className={styles.files} aria-label="Current memory files">
      <ErrorNotice
        error={files.error || file.error}
        retry={() => {
          void files.refetch();
          if (path) void file.refetch();
        }}
      />
      {files.isPending && (
        <p role="status" className={styles.loading}>
          Loading memory…
        </p>
      )}
      {files.data?.length === 0 && (
        <div className={styles.empty}>
          <Brain size={28} aria-hidden="true" />
          <h2>Nothing saved yet</h2>
          <p>
            Your agent can remember useful preferences and project knowledge as
            you work.
          </p>
        </div>
      )}
      {!!files.data?.length && (
        <div className={styles.fileToolbar}>
          <div className={styles.picker}>
            <SearchPicker
              label="Memory file"
              placeholder="Choose a file"
              emptyMessage="No matching memory files"
              value={path ?? ""}
              onValueChange={select}
              groups={[
                {
                  label: "Memory files",
                  options: files.data.map((entry) => ({
                    value: entry.path,
                    label: entry.path,
                  })),
                },
              ]}
            />
          </div>
          <span>Current files · read only</span>
        </div>
      )}
      {file.isFetching && !file.data && path && (
        <p role="status" className={styles.loading}>
          Loading file…
        </p>
      )}
      {file.data && (
        <div className={`${styles.file} a13n-scrollbar`}>
          <MessageText text={file.data.text} />
        </div>
      )}
    </section>
  );
}
