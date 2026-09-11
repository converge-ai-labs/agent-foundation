import { Button, DisclosureSection, FormField, Input } from "a13n-ui";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

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
      <Button
        variant="outline"
        loading={discovery.isPending}
        onClick={() => discovery.mutate()}
        type="button"
      >
        {t("Discover tools")}
      </Button>
      <ErrorNotice error={discovery.error} />
      {discovery.data && (
        <>
          <FormField
            className="min-w-0 w-full"
            label={t("Search tools")}
            hideLabel={true}
          >
            <Input
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setLimit(30);
              }}
              type="search"
            />
          </FormField>
          {tools.slice(0, limit).map((tool) => (
            <DisclosureSection key={tool.name} title={<>{tool.name}</>}>
              <p className="text-muted-foreground">{tool.description}</p>
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
            </DisclosureSection>
          ))}
          {tools.length > limit && (
            <Button
              variant="outline"
              onClick={() => setLimit((value) => value + 30)}
              type="button"
            >
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
