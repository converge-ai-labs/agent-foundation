import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button, SearchInput } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { Empty, ErrorNotice } from "../../shared/feedback";
import { JsonView } from "../../shared/form";
import styles from "../../shared/shared.module.css";

export function MCPTools({
  connection,
}: {
  connection: Schema["MCPConnection"];
}) {
  const { t } = useTranslation(),
    client = useClient(),
    [search, setSearch] = useState(""),
    [limit, setLimit] = useState(30);
  const discovery = useMutation({
    mutationFn: () =>
      client.http
        .POST("/api/v1/mcp-connections/{connection_id}/discover", {
          params: { path: { connection_id: connection.id } },
          body: { expected_version: connection.version },
        })
        .then(data),
  });
  const tools =
    discovery.data?.items.filter((tool) =>
      `${tool.name} ${tool.description}`
        .toLocaleLowerCase()
        .includes(search.toLocaleLowerCase()),
    ) ?? [];
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>
        {t(
          "Discover the tools currently available to this connection. The agent checks availability again when it runs.",
        )}
      </p>
      <Button loading={discovery.isPending} onClick={() => discovery.mutate()}>
        {t("Discover tools")}
      </Button>
      <ErrorNotice error={discovery.error} />
      {discovery.data && (
        <>
          <SearchInput
            label={t("Search tools")}
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setLimit(30);
            }}
          />
          {tools.slice(0, limit).map((tool) => (
            <details key={tool.name}>
              <summary>{tool.name}</summary>
              <p>{tool.description}</p>
              <h4>{t("Input schema")}</h4>
              <JsonView value={tool.input_schema} />
              {tool.output_schema && (
                <>
                  <h4>{t("Output schema")}</h4>
                  <JsonView value={tool.output_schema} />
                </>
              )}
              <h4>{t("Annotations")}</h4>
              <JsonView value={tool.annotations} />
            </details>
          ))}
          {tools.length > limit && (
            <Button onClick={() => setLimit((value) => value + 30)}>
              {t("Show more tools")}
            </Button>
          )}
          {!tools.length && (
            <Empty
              title={t("No tools found")}
              description={t(
                "Try another search or check the server's authorization.",
              )}
            />
          )}
        </>
      )}
    </div>
  );
}
