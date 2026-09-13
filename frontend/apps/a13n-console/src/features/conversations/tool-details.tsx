import { useTranslation } from "react-i18next";
import { CopyableId } from "../../shared/copy";
import { JsonView } from "../../shared/form";
import { parseItemValue, type PresentedItem } from "./projection";
import styles from "./conversations.module.css";

export function ToolDetails({ item }: { item: PresentedItem }) {
  const { t } = useTranslation();
  return (
    <div className={styles.toolBody}>
      <CopyableId value={item.toolName || item.id} />
      <h4>{t("Arguments")}</h4>
      <JsonView value={parseItemValue(item.arguments)} />
      {item.result !== undefined && (
        <>
          <h4>{t("Result")}</h4>
          <JsonView value={parseItemValue(item.result)} />
        </>
      )}
      {item.failure !== undefined && <JsonView value={item.failure} />}
    </div>
  );
}
