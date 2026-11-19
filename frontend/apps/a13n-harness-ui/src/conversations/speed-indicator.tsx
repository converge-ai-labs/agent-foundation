import { Lightning, RocketLaunch } from "@phosphor-icons/react";
import type { Schema } from "../transport/client";
import styles from "./composer-settings.module.css";

export function SpeedIndicator({
  speed,
}: {
  speed: Schema<"FastControl">["state"] | undefined;
}) {
  const Icon =
    speed === "ultrafast" ? RocketLaunch : speed === "on" ? Lightning : null;
  return Icon ? (
    <Icon aria-hidden className={styles.speedIndicator} weight="fill" />
  ) : null;
}
