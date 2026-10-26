import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import { ListRow, ListRows, ListRowsEmpty } from "../../shared/collection";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { Section } from "../../shared/page";
import { SkillIcon } from "./source";

export function useBuiltinSkills() {
  const client = useClient();
  const { workspace } = useWorkspace();
  return useQuery({
    queryKey: ["builtin-skills", workspace.id],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/builtin-skills", { signal })
        .then(data),
  });
}

export function BuiltinSkills({ search }: { search: string }) {
  const { t } = useTranslation();
  const catalog = useBuiltinSkills();
  const items = catalog.data?.items.filter((skill) =>
    `${skill.name} ${skill.description} ${t(skill.description)}`
      .toLocaleLowerCase()
      .includes(search),
  );
  return (
    <Section
      title={t("Built-in skills")}
      description={t(
        "Included with Service and updated with each release. Select them in an agent's Skills settings.",
      )}
    >
      <ErrorNotice error={catalog.error} retry={() => void catalog.refetch()} />
      {catalog.isPending ? (
        <Loading />
      ) : items?.length ? (
        <ListRows>
          {items.map((skill) => (
            <ListRow
              key={skill.name}
              icon={<SkillIcon />}
              name={skill.name}
              secondary={t(skill.description)}
            />
          ))}
        </ListRows>
      ) : (
        !catalog.error && (
          <ListRowsEmpty>
            {t(
              search
                ? "No matching built-in skills."
                : "No built-in skills available.",
            )}
          </ListRowsEmpty>
        )
      )}
    </Section>
  );
}
