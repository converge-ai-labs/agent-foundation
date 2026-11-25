import {
  FileCodeIcon,
  FileCsvIcon,
  FileIcon,
  FileImageIcon,
  FilePdfIcon,
  FileTextIcon,
} from "@phosphor-icons/react";
import type { Asset } from "./assets";
import { previewFormat } from "./preview-data";

export function FileTypeIcon({
  asset,
  className,
  size = 16,
}: {
  asset?: Pick<Asset, "name" | "content_type">;
  className?: string;
  size?: number;
}) {
  const format = asset && previewFormat(asset);
  const Icon =
    format?.kind === "image"
      ? FileImageIcon
      : format?.kind === "pdf"
        ? FilePdfIcon
        : format?.kind === "csv"
          ? FileCsvIcon
          : format?.kind === "markdown" ||
              (format?.kind === "text" && !format.language)
            ? FileTextIcon
            : format
              ? FileCodeIcon
              : FileIcon;
  return <Icon className={className} size={size} aria-hidden="true" />;
}
