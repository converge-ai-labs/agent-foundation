import { Tabs } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import { Page } from "../../shared/feedback";
import { ConnectorProviders } from "./providers";
import { ConnectorConnections } from "./connections";
export function ConnectorsPage() {
  const { workspace } = useWorkspace(),
    { t } = useTranslation();
  return (
    <Page
      title={t("Connectors")}
      description={t(
        "Connect external accounts through your integration providers.",
      )}
    >
      <Tabs
        label={t("Connectors")}
        defaultValue="connections"
        items={[
          {
            value: "connections",
            label: t("Connections"),
            content: <ConnectorConnections />,
          },
          {
            value: "providers",
            label: t("Providers"),
            content: (
              <ConnectorProviders
                scope={{ kind: "workspace", id: workspace.id }}
              />
            ),
          },
        ]}
      />
    </Page>
  );
}
