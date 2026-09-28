import { useTranslation } from "react-i18next";
import { KindProviders, providerStyles, useProviderTypes } from "../providers";

export function useEnvironmentTypes(enabled = true) {
  return useProviderTypes("environment", enabled);
}

export function EnvironmentProviders() {
  const { t } = useTranslation();
  return (
    <KindProviders
      kind="environment"
      addDescription="Choose where your environments run."
      testDescription="Reads from the provider without creating or starting anything."
      notice={
        <p className={providerStyles.notice}>
          {t(
            "Direct Local and Docker require a single-host deployment and operator configuration.",
          )}
        </p>
      }
    />
  );
}
