import { InfoIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { MenuItem, MenuSeparator } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { ErrorNotice, InlineLoading } from "../../shared/feedback";
import { environmentQuery } from "./api";
import {
  environmentLifecycleAvailability,
  EnvironmentLifecycleItems,
} from "./instance-actions";
import { useEnvironmentTypes } from "./providers";

/** Mounted only while the row menu is open; never fetch every row's detail. */
export function EnvironmentRowMenu({
  environment,
  providerType,
  onDetails,
}: {
  environment: Schema["EnvironmentView"];
  providerType?: string;
  onDetails: () => void;
}) {
  const client = useClient(),
    { can } = useWorkspace(),
    { t } = useTranslation();
  const writable = can("write");
  const detail = useQuery({
    ...environmentQuery(client, environment.workspace_id, environment.id),
    enabled: writable,
    staleTime: 0,
  });
  const types = useEnvironmentTypes(writable && !!environment.template_id);
  const value = detail.data?.value;
  const capabilities = types.data?.items.find(
    (item) => item.type === providerType,
  );
  const { canStop, canDelete } = environmentLifecycleAvailability(
    value ?? environment,
    capabilities,
  );
  const typeError = environment.template_id ? types.error : null;
  const ready =
    writable &&
    value &&
    detail.isFetchedAfterMount &&
    !detail.error &&
    !typeError;
  return (
    <>
      <MenuItem onClick={onDetails}>
        <InfoIcon aria-hidden="true" />
        {t("Details")}
      </MenuItem>
      {writable && detail.isFetching && <InlineLoading width="6rem" />}
      {writable && (
        <ErrorNotice
          error={detail.error ?? typeError}
          retry={() => {
            void detail.refetch();
            void types.refetch();
          }}
        />
      )}
      {ready && (canStop || canDelete) && (
        <>
          <MenuSeparator />
          <EnvironmentLifecycleItems
            environment={value}
            etag={detail.data?.etag}
            canStop={canStop}
            canDelete={canDelete}
          />
        </>
      )}
    </>
  );
}
