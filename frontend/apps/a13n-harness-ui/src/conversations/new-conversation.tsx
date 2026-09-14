import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, ModalFrame } from "a13n-ui";
import { useProjects, useSelectors, useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import styles from "./conversation.module.css";

export function NewConversation({
  projectId,
  close,
}: {
  projectId: string | null;
  close: () => void;
}) {
  const transport = useTransport();
  const queries = useQueryClient();
  const selectors = useSelectors();
  const projects = useProjects();
  const navigate = useNavigate();
  const [project, setProject] = useState(projectId ?? "");
  const [title, setTitle] = useState("");
  const [agent, setAgent] = useState("");
  const [environment, setEnvironment] = useState("");
  const defaults: Schema<"NewThreadDefaults"> = {
    project_id: project || null,
    ...(agent ? { agent_id: agent } : {}),
    ...(environment ? { environment_profile_id: environment } : {}),
  };
  const preview = useQuery({
    queryKey: ["new-thread-preview", defaults],
    queryFn: ({ signal }) =>
      result(
        transport.client.POST("/api/threads/configuration-preview", {
          body: defaults,
          signal,
        }),
      ),
  });
  const create = useMutation({
    mutationFn: () =>
      result(
        transport.client.POST("/api/threads", {
          body: { title: title || null, defaults },
        }),
      ),
    onSuccess: (thread) => {
      void queries.invalidateQueries({ queryKey: ["threads"] });
      close();
      navigate(`/threads/${encodeURIComponent(thread.thread_id)}`);
    },
  });
  return (
    <ModalFrame
      open
      onOpenChange={(open) => {
        if (!open && !create.isPending) close();
      }}
      title="New conversation"
      description="Choose where to work. These selections are captured for the new conversation; creating it does not start a model."
      closeLabel="Close"
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          if (
            preview.data &&
            !preview.isFetching &&
            !preview.error &&
            !create.isPending
          )
            create.mutate();
        }}
      >
        <TextField
          label="Title (optional)"
          value={title}
          onChange={(value) => setTitle(value.slice(0, 200))}
        />
        <ChoiceField
          label="Project"
          value={project}
          onValueChange={setProject}
          options={[
            { value: "", label: "Without a project" },
            ...(projects.data ?? []).map((item) => ({
              value: item.project_id,
              label: item.name,
            })),
          ]}
        />
        <ChoiceField
          label="Agent"
          value={agent}
          onValueChange={setAgent}
          options={[
            { value: "", label: "Use creation default" },
            ...(selectors.data?.agents ?? []).map((item) => ({
              value: item.agent_id,
              label: item.name,
            })),
          ]}
        />
        <ChoiceField
          label="Environment"
          value={environment}
          onValueChange={setEnvironment}
          options={[
            { value: "", label: "Use creation default" },
            ...(selectors.data?.environments ?? []).map((item) => ({
              value: item.profile_id,
              label: item.name,
            })),
          ]}
        />
        {preview.data && (
          <div className={styles.summary}>
            <p>
              Agent:{" "}
              {selectors.data?.agents.find(
                (item) =>
                  item.agent_id === preview.data.configuration.agent_source.id,
              )?.name || preview.data.configuration.agent_source.id}{" "}
              · {preview.data.provenance.agent_source}
            </p>
            <p>
              Environment: {preview.data.configuration.environment_profile_id} ·{" "}
              {preview.data.provenance.environment_profile_id}
            </p>
          </div>
        )}
        <ErrorNotice error={preview.error || create.error || selectors.error} />
        {preview.error && (
          <Link to="/setup" onClick={close}>
            Open setup and readiness
          </Link>
        )}
        <Button
          type="submit"
          loading={create.isPending}
          disabled={!preview.data || preview.isFetching || !!preview.error}
        >
          Create conversation
        </Button>
      </form>
    </ModalFrame>
  );
}
