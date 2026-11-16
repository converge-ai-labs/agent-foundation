import { BrandIcon } from "a13n-ui";
import {
  ArrowRight,
  ArrowSquareOut,
  ArrowsClockwise,
  Check,
  Copy,
  FloppyDisk,
  PencilSimple,
  PlugsConnected,
  type Icon,
} from "@phosphor-icons/react";
import type { Schema } from "../transport/client";

export function ConnectorIcon({
  declaration,
  size = 20,
}: {
  declaration?: Schema<"Declaration"> | null;
  size?: number;
}) {
  return (
    <BrandIcon
      identity={declaration?.brand ?? undefined}
      logo={declaration?.logo_url}
      size={size}
    />
  );
}

const actionIcons = new Map<string, Icon>([
  ["edit", PencilSimple],
  ["test", PlugsConnected],
  ["save", FloppyDisk],
  ["continue", ArrowRight],
  ["done", Check],
  ["refresh", ArrowsClockwise],
  ["open_url", ArrowSquareOut],
  ["copy", Copy],
]);

export function setupActionIcon(action: Schema<"SetupAction">) {
  return actionIcons.get(action.icon ?? action.kind ?? "");
}

export function compactSetupAction(
  action: Schema<"SetupAction">,
  primary: boolean,
) {
  return (
    !primary &&
    action.kind === "submit" &&
    (action.icon === "edit" || action.icon === "test")
  );
}
