import { Checkbox } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { ListRow, ListRows } from "../../../shared/collection";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import { useBuiltinSkills } from "../../skills/builtin";
import { SkillIcon } from "../../skills/source";
import type { AgentDraft } from "./draft";

export function BuiltinSkillSelections({
  draft,
  readOnly,
  hostSkills,
}: {
  draft: AgentDraft;
  readOnly: boolean;
  hostSkills: boolean;
}) {
  const { t } = useTranslation();
  const catalog = useBuiltinSkills();
  const available = catalog.data?.items ?? [];
  const names = [
    ...new Set([
      ...available.map((skill) => skill.name),
      ...draft.builtinSkills,
    ]),
  ];
  return (
    <div className="grid gap-3">
      <h4 className="text-xs font-medium text-muted-foreground">
        {t("Built-in skills")}
      </h4>
      <ErrorNotice error={catalog.error} retry={() => void catalog.refetch()} />
      {catalog.isPending && !names.length && <Loading />}
      {!!names.length && (
        <ListRows>
          {names.map((name) => {
            const definition = available.find((skill) => skill.name === name);
            const checked = draft.builtinSkills.includes(name);
            return (
              <ListRow
                key={name}
                icon={<SkillIcon />}
                name={name}
                secondary={
                  definition
                    ? t(definition.description)
                    : catalog.isSuccess
                      ? t("Unavailable in this deployment")
                      : undefined
                }
                control={
                  <Checkbox
                    aria-label={t("Enable {{name}}", { name })}
                    checked={checked}
                    disabled={readOnly || (!definition && !checked)}
                    onCheckedChange={(enabled) =>
                      draft.setBuiltinSkills((previous) =>
                        enabled === true
                          ? [...previous, name]
                          : previous.filter((value) => value !== name),
                      )
                    }
                  />
                }
              />
            );
          })}
        </ListRows>
      )}
      {!!draft.builtinSkills.length && (
        <p className="text-xs text-muted-foreground">
          {t(
            hostSkills
              ? "These instructions use Service's read-only files. File tools and platform tools keep their own permissions."
              : "These instructions are copied into the run's primary environment. Enable file reading and the platform tools the agent needs separately.",
          )}
        </p>
      )}
    </div>
  );
}
