import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { Brain, Folder, Globe } from "@phosphor-icons/react";
import { result } from "./transport/client";
import { useProjects, useStatus, useTransport } from "./transport/context";
import { Conversation } from "./conversations/conversation";
import { useMemoryThread } from "./conversations/queries";
import { MessageText } from "./conversations/message-text";
import { ErrorNotice } from "./shell/ui";
import type { Profile } from "./shell/presence";
import styles from "./memory.module.css";

export function MemoryNavigation({ projectId }: { projectId?: string | null }) {
  const projects = useProjects();
  const [search] = useSearchParams();
  const selected = projectId === undefined ? search.get("project") : projectId;
  return (
    <div className={styles.navigation}>
      <div className={styles.heading}>
        <Brain size={18} />
        <strong>Memory</strong>
        <span>Read only</span>
      </div>
      <Link className={!selected ? styles.selected : styles.scope} to="/memory">
        <Globe size={18} />
        Global
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
      {projects.data?.length === 0 && <p>No configured projects.</p>}
      <p className={styles.hint}>
        Each scope keeps its own automatic organization history.
      </p>
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
  if (!enabled)
    return (
      <div className={styles.empty}>
        <h2>Memory is disabled</h2>
        <p>Enable Memory in the existing settings to view these scopes.</p>
      </div>
    );
  return (
    <div className={styles.workspace}>
      <section
        className={styles.history}
        aria-label="Memory organization history"
      >
        <header className={styles.header}>
          <div>
            <h2>{name} memory</h2>
            <p>Automatic organization · fresh context each Run</p>
          </div>
          {thread && (
            <Button
              variant="ghost"
              onClick={() =>
                setSearch((current) => {
                  current.set("dialog", "details");
                  return current;
                })
              }
            >
              Inspect & usage
            </Button>
          )}
        </header>
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
            <Brain size={32} />
            <h2>
              {history.isPending
                ? "Loading history…"
                : "No organization history yet"}
            </h2>
            <p>
              Current files are available on the right. Opening this page does
              not start organization.
            </p>
          </div>
        )}
      </section>
      <MemoryFiles key={projectId ?? "global"} projectId={projectId} />
    </div>
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
    <aside className={styles.files} aria-label="Current memory files">
      <header className={styles.header}>
        <div>
          <h2>Current files</h2>
          <p>Read only · not a historical snapshot</p>
        </div>
      </header>
      <ErrorNotice
        error={files.error || file.error}
        retry={() => {
          void files.refetch();
          if (path) void file.refetch();
        }}
      />
      {files.data?.length === 0 && (
        <p className={styles.empty}>This scope has no memory files yet.</p>
      )}
      {!!files.data?.length && (
        <label className={styles.picker}>
          File
          <select
            aria-label="Memory file"
            value={path ?? ""}
            onChange={(event) => select(event.target.value)}
          >
            {files.data.map((entry) => (
              <option key={entry.path} value={entry.path}>
                {entry.path}
              </option>
            ))}
          </select>
        </label>
      )}
      {file.isFetching && !file.data && path && (
        <p role="status">Loading file…</p>
      )}
      {file.data && (
        <div className={`${styles.file} a13n-scrollbar`}>
          <MessageText text={file.data.text} />
        </div>
      )}
    </aside>
  );
}
