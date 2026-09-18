import { useMutation } from "@tanstack/react-query";
import { Button, DisclosureSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { JsonView } from "../../shared/forms";

export function ConnectorToolPreview({
  connector,
}: {
  connector: Schema["Connector"];
}) {
  const client = useClient(),
    { t } = useTranslation();
  const preview = useMutation({
    mutationFn: () =>
      client.http
        .GET(
          "/api/v1/connector-providers/{connector_provider_id}/connectors/{connector_key}/tools",
          {
            params: {
              path: {
                connector_provider_id: connector.connector_provider_id,
                connector_key: connector.key,
              },
            },
          },
        )
        .then(data),
  });
  return (
    <div>
      <Button
        variant="outline"
        size="sm"
        loading={preview.isPending}
        onClick={() => preview.mutate()}
        type="button"
      >
        {t("Preview tools")}
      </Button>
      <ErrorNotice error={preview.error} />
      {preview.data && (
        <div className="mt-3 max-h-64 overflow-y-auto">
          <p className="mb-2 text-sm text-muted-foreground">
            {t(
              "{{count}} tools. Availability is verified after authorization.",
              { count: preview.data.items.length },
            )}
          </p>
          {preview.data.items.map((tool) => (
            <DisclosureSection key={tool.key} title={tool.key}>
              <p className="text-sm">{tool.description}</p>
              <JsonView value={tool.input_schema} />
            </DisclosureSection>
          ))}
        </div>
      )}
    </div>
  );
}
