import { Popover, PopoverPopup, PopoverTrigger } from "a13n-ui";
import { HashStraightIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { CopyButton } from "./copy";
import styles from "./identity.module.css";

export function ResourceReference({
  id,
  idLabel,
  idCopyLabel,
  resourceKey,
  references,
}: {
  id: string;
  idLabel?: string;
  idCopyLabel?: string;
  resourceKey?: string;
  references?: { label: string; value: string; copyLabel: string }[];
}) {
  const { t } = useTranslation();
  return (
    <Popover>
      <PopoverTrigger
        type="button"
        className={styles.marker}
        aria-label={t("Show resource reference")}
        openOnHover
        delay={100}
        closeDelay={120}
      >
        <HashStraightIcon aria-hidden="true" />
      </PopoverTrigger>
      <PopoverPopup
        className={styles.popup}
        align="start"
        sideOffset={5}
        tooltipStyle
      >
        <ReferenceRow
          label={idLabel ?? t("ID")}
          value={id}
          copyLabel={idCopyLabel ?? t("Copy resource ID")}
        />
        {resourceKey && (
          <ReferenceRow
            label={t("Key")}
            value={resourceKey}
            copyLabel={t("Copy resource key")}
          />
        )}
        {references?.map((reference) => (
          <ReferenceRow
            key={reference.label}
            label={reference.label}
            value={reference.value}
            copyLabel={reference.copyLabel}
          />
        ))}
      </PopoverPopup>
    </Popover>
  );
}

function ReferenceRow({
  label,
  value,
  copyLabel,
}: {
  label: string;
  value: string;
  copyLabel: string;
}) {
  return (
    <div className={styles.row}>
      <span className={styles.label}>{label}</span>
      <code title={value}>{value}</code>
      <CopyButton value={value} iconOnly copyLabel={copyLabel} />
    </div>
  );
}
