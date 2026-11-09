export type DocumentKind = "pdf" | "sheet" | "slides";
export function documentKind(path: string): DocumentKind | null {
  if (/\.pdf$/i.test(path)) return "pdf";
  if (/\.(xlsx?|ods)$/i.test(path)) return "sheet";
  if (/\.pptx$/i.test(path)) return "slides";
  return null;
}
export const documentLimit = 16 * 1024 * 1024;
export function unavailablePreview(path: string) {
  return /\.(zip|tar|gz|tgz|gzip|bz2|tbz2?|xz|txz|zst|rar|7z|jar|war|lz4|ppt|pptm|pps|ppsm|docm|docx?|odt|odp|pages|key|db|sqlite3?|woff2?|ttf|otf|exe|dll|dmg|iso|parquet|bin|class|dylib|so|pyc|apk|msi|deb|rpm)$/i.test(
    path,
  );
}
