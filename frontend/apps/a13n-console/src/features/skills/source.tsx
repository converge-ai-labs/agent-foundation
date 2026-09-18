import {
  FileZipIcon,
  GithubLogoIcon,
  PuzzlePieceIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import styles from "./skills.module.css";

/** The two ways a package reaches the workspace, named the same way everywhere. */
export type SkillSource = "zip" | "github";

export function skillSource(kind: string): SkillSource {
  return kind === "github" ? "github" : "zip";
}

export function SourceIcon({
  kind,
  size = 13,
}: {
  kind: string;
  size?: number;
}) {
  return skillSource(kind) === "github" ? (
    <GithubLogoIcon size={size} aria-hidden="true" />
  ) : (
    <FileZipIcon size={size} aria-hidden="true" />
  );
}

/** Neutral chip: the icon repeats the word, it never replaces it. */
export function SourceChip({ kind }: { kind: string }) {
  const { t } = useTranslation();
  return (
    <span className={styles.sourceChip}>
      <SourceIcon kind={kind} />
      {skillSource(kind) === "github" ? "GitHub" : t("ZIP")}
    </span>
  );
}

/** The mark a skill presents itself with across the console. */
export function SkillIcon({ size = 16 }: { size?: number }) {
  return <PuzzlePieceIcon size={size} aria-hidden="true" />;
}
