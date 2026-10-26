import { Button, ChoiceField } from "a13n-ui";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";

type Option = Schema["VercelOption"];

/** Draft credentials stay in this form; choices never enter a cross-account query cache. */
export function VercelChoices({
  credential,
  value,
  onChange,
}: {
  credential: Record<string, unknown>;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const token =
    typeof credential.api_key === "string" ? credential.api_key : "";
  const [teams, setTeams] = useState<Option[]>([]),
    [projects, setProjects] = useState<Option[]>([]);
  const [teamCursor, setTeamCursor] = useState<number | null>(null),
    [projectCursor, setProjectCursor] = useState<number | null>(null);
  const [pending, setPending] = useState(false),
    [error, setError] = useState<unknown>();
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    request.current?.abort();
    setTeams([]);
    setProjects([]);
    setTeamCursor(null);
    setProjectCursor(null);
    setPending(false);
    setError(undefined);
    return () => request.current?.abort();
  }, [token, workspace.id]);
  async function load(team?: string, cursor?: number) {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setPending(true);
    setError(undefined);
    try {
      const result = await client
        .workspace(workspace.id)
        .POST("/api/v1/environment-provider-options/vercel", {
          body: { api_key: token, team_id: team, cursor },
          signal: controller.signal,
        })
        .then(data);
      if (controller.signal.aborted) return;
      const merge = (old: Option[]) => [
        ...new Map(
          [...(cursor === undefined ? [] : old), ...result.items].map(
            (item) => [item.id, item],
          ),
        ).values(),
      ];
      if (team) {
        setProjects(merge);
        setProjectCursor(result.cursor ?? null);
      } else {
        setTeams(merge);
        setTeamCursor(result.cursor ?? null);
      }
    } catch (e) {
      if (!controller.signal.aborted) setError(e);
    } finally {
      if (!controller.signal.aborted) setPending(false);
    }
  }
  return (
    <>
      <Button
        type="button"
        variant="secondary"
        disabled={!token.trim() || pending}
        onClick={() => void load()}
      >
        {t(pending ? "Loading…" : "Load Vercel teams")}
      </Button>
      <ChoiceField
        label={t("Vercel team")}
        required
        placeholder={t("Select…")}
        value={typeof value.team_id === "string" ? value.team_id : undefined}
        options={teams.map((item) => ({ value: item.id, label: item.name }))}
        onValueChange={(team) => {
          onChange({ team_id: team });
          setProjects([]);
          setProjectCursor(null);
          if (team) void load(team);
        }}
      />
      {teamCursor !== null && (
        <Button
          type="button"
          variant="ghost"
          disabled={pending}
          onClick={() => void load(undefined, teamCursor)}
        >
          {t("Load more teams")}
        </Button>
      )}
      <ChoiceField
        label={t("Vercel project")}
        required
        placeholder={t("Select…")}
        value={
          typeof value.project_id === "string" ? value.project_id : undefined
        }
        options={projects.map((item) => ({ value: item.id, label: item.name }))}
        onValueChange={(project) => onChange({ ...value, project_id: project })}
      />
      {projectCursor !== null && typeof value.team_id === "string" && (
        <Button
          type="button"
          variant="ghost"
          disabled={pending}
          onClick={() => void load(value.team_id as string, projectCursor)}
        >
          {t("Load more projects")}
        </Button>
      )}
      {!pending &&
        teams.length > 0 &&
        value.team_id &&
        projects.length === 0 && (
          <p>
            {t(
              "No projects found. Create a project in Vercel or check the token permissions, then reload teams.",
            )}
          </p>
        )}
      <ErrorNotice error={error} />
    </>
  );
}
