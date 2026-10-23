import {
  FileZipIcon,
  GithubLogoIcon,
  PuzzlePieceIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { AgentAvatar } from "../agents/avatar";
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

/** The category glyph, for places that speak of skills in general. */
export function SkillIcon({ size = 16 }: { size?: number }) {
  return <PuzzlePieceIcon size={size} aria-hidden="true" />;
}

/**
 * A skill has no image, so it presents its initial on the hue of its
 * identity, the same tile an agent without an image uses.
 */
export function SkillAvatar({
  name,
  id,
  className,
}: {
  name: string;
  id: string;
  className?: string;
}) {
  return <AgentAvatar name={name} id={id} className={className} />;
}
