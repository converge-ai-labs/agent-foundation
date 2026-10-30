import { useTranslation } from "react-i18next";
import { ListRow } from "../../../shared/collection";
import { SkillIcon } from "../../skills/source";

/** Bound instructions share the selected Skills list without a separate toggle. */
export function BuiltinSkillRows({ names }: { names: readonly string[] }) {
  const { t } = useTranslation();
  return names.map((name) => (
    <ListRow
      key={name}
      icon={<SkillIcon />}
      name={name}
      secondary={
        name === "platform-operations"
          ? t("Included automatically with enabled platform tools.")
          : undefined
      }
    />
  ));
}
