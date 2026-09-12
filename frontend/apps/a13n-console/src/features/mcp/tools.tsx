import { CaretDownIcon } from "@phosphor-icons/react";
import {
  Button,
  Collapsible,
  CollapsibleTrigger,
  CollapsiblePanel,
  FormField,
  Input,
} from "a13n-ui";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { Empty, ErrorNotice } from "../../shared/feedback";

export function MCPTools({ connection }: { connection: Schema["Connection"] }) {
  const { t } = useTranslation(),
    client = useClient(),
    [search, setSearch] = useState(""),
    [limit, setLimit] = useState(30);
  const discovery = useMutation({
    mutationFn: () =>
      client.http
        .POST("/api/v1/connections/{connection_id}/mcp/discover", {
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
    <div className="grid gap-4">
      <div className="flex items-start justify-between gap-4">
        <p className="text-sm leading-relaxed text-muted-foreground">
          {t("Browse the tools available from this server.")}
        </p>
        <Button
          className="shrink-0"
          size="sm"
          variant="outline"
          loading={discovery.isPending}
          onClick={() => discovery.mutate()}
          type="button"
        >
          {t(discovery.data ? "Refresh tools" : "Discover tools")}
        </Button>
      </div>
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
              placeholder={t("Search tools")}
              type="search"
            />
          </FormField>
          <div className="divide-y divide-border/60">
            {tools.slice(0, limit).map((tool) => (
              <Collapsible key={tool.name}>
                <CollapsibleTrigger className="group flex w-full items-start gap-4 rounded-sm px-1 py-4 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/40">
                  <span className="grid min-w-0 flex-1 gap-1.5">
                    <span className="break-words text-sm font-medium leading-5">
                      {tool.name}
                    </span>
                    {tool.description && (
                      <span className="text-sm leading-5 text-muted-foreground">
                        {tool.description}
                      </span>
                    )}
                  </span>
                  <CaretDownIcon
                    aria-hidden
                    className="mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform group-aria-expanded:rotate-180"
                  />
                </CollapsibleTrigger>
                <CollapsiblePanel>
                  <div className="grid gap-3 px-1 pb-4">
                    <ToolSchema
                      title={t("Input schema")}
                      value={tool.input_schema}
                    />
                    {tool.output_schema && (
                      <ToolSchema
                        title={t("Output schema")}
                        value={tool.output_schema}
                      />
                    )}
                    {tool.annotations &&
                      Object.keys(tool.annotations).length > 0 && (
                        <ToolSchema
                          title={t("Annotations")}
                          value={tool.annotations}
                        />
                      )}
                  </div>
                </CollapsiblePanel>
              </Collapsible>
            ))}
          </div>
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

function ToolSchema({ title, value }: { title: string; value: unknown }) {
  return (
    <section className="text-xs">
      <h4 className="font-medium text-muted-foreground">{title}</h4>
      <pre className="mt-2 max-h-48 overflow-auto rounded-md bg-muted/40 p-3 text-xs leading-relaxed">
        {JSON.stringify(value, null, 2)}
      </pre>
    </section>
  );
}
