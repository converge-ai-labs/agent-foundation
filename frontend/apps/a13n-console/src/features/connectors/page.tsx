import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import { Page } from "../../shared/feedback";
import { ConnectorProviders } from "./providers";
import { ConnectorConnections } from "./connections";

export function ConnectorsPage({ providers = false }: { providers?: boolean }) {
  const { workspace } = useWorkspace(),
    { t } = useTranslation();
  return (
    <Page
      title={t(providers ? "Connector providers" : "Connectors")}
      description={t(
        "Connect external accounts through your integration providers.",
      )}
    >
      {providers ? (
        <ConnectorProviders scope={{ kind: "workspace", id: workspace.id }} />
      ) : (
        <ConnectorConnections />
      )}
    </Page>
  );
}
