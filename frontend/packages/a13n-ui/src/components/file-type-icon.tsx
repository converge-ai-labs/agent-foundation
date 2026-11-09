const palette = {
  neutral: "light-dark(#c9cbd1,#777a84)",
  gray: "light-dark(#9296a0,#aaaeb9)",
  green: "light-dark(#17a75e,#58d78d)",
  blue: "light-dark(#2488df,#68b8ff)",
  orange: "light-dark(#fb8553,#ffab7e)",
  coral: "light-dark(#ef4a62,#ff8192)",
  violet: "light-dark(#9461ed,#bd95ff)",
  pink: "light-dark(#e64e98,#ff89c2)",
  amber: "light-dark(#d79918,#f3c157)",
};
function mark(color: keyof typeof palette, path: string, extensions = "") {
  return { color, path, extensions };
}
const icons = {
  file: mark(
    "neutral",
    "M4 3q0-1 1-1h6v5q0 2 2 2h4v7q0 2-2 2H6q-2 0-2-2ZM12.5 2.5l4 5h-3q-1 0-1-1Z",
    "",
  ),
  folder: mark("gray", "M7 4.5 12.5 10 7 15.5l-1.2-1.2 4.3-4.3-4.3-4.3Z", ""),
  link: mark(
    "blue",
    "M10 3h7v7h-2V6.4l-7 7L6.6 12l7-7H10ZM3 7h4v2H5v6h6v-2h2v4H3Z",
    "",
  ),
  git: mark(
    "orange",
    "M9 1.8q1-1 2 0l7.2 7.2q1 1 0 2L11 18.2q-1 1-2 0L1.8 11q-1-1 0-2ZM7.2 5.2l2 2V12a1.6 1.6 0 1 0 1.5 0V9l2 2a1.5 1.5 0 1 0 1-1l-3.2-3.2a1.5 1.5 0 0 0-2-1l-.5-.6Z",
    "",
  ),
  docker: mark(
    "blue",
    "M1 10h12q2 0 3-2l-.3-2 2 1.2 2 .2-1 2q-1 1-2.4 1.3Q14 17 8 17q-6 0-7-7ZM3 7h2.4v2.3H3Zm3.1 0h2.4v2.3H6.1Zm3.1 0h2.4v2.3H9.2Zm-3.1-3h2.4v2.3H6.1Zm3.1 0h2.4v2.3H9.2Z",
    "",
  ),
  workflow: mark(
    "coral",
    "M2 3h8v6H7v2l4 2v-2h7v6h-7v-2l-5.5-3V9H2Zm1.4 1.4v3.2h5.2V4.4ZM12.4 12.4v3.2h4.2v-3.2Z",
    "",
  ),

  image: mark(
    "green",
    "M2 16l5.6-8 3.4 4.3 2.2-2.8L18 16ZM14 3a2.2 2.2 0 1 1 0 4.4A2.2 2.2 0 0 1 14 3Z",
    "jpg jpeg png gif webp avif heic heif tif tiff bmp ico raw psd",
  ),
  vector: mark(
    "pink",
    "M3 13h3v-1q2-6 7-7V3h4v4h-3Q9 8 7 13v4H3Z",
    "svg eps ai fig sketch",
  ),
  video: mark(
    "violet",
    "M2 5q0-1 1-1h10v12H3q-1 0-1-1Zm12 3 4-3v10l-4-3Z",
    "mp4 mov mkv webm avi m4v mpeg mpg",
  ),
  audio: mark(
    "pink",
    "M7 5l10-2v10c0 4-6 5-6 2 0-2 2-3 4-3V6L9 7.3V15c0 4-6 5-6 2 0-2 2-3 4-3Z",
    "mp3 flac wav aac m4a ogg opus aiff mid",
  ),
  pdf: mark(
    "coral",
    "M2 16q4-4 6-9c-1-6 3-7 4-3q.4 2-.8 4.6l2.6 2.7c6-.8 6 3.6 2.4 3.1l-3.4-1.6-5.2 1.5C4 20 1 19 2 16Zm6.5-4 3-.9-1.5-1.7Z",
    "pdf",
  ),
  slides: mark(
    "orange",
    "M2 3h16v2h-1v8h-6v1.5l4 2-.8 1.5-4.2-2.3L6 18l-.8-1.5 4-2V13H3V5H2Zm6 3.2v4.5l4-2.2Z",
    "ppt pptx odp key",
  ),
  sheet: mark(
    "green",
    "M3 3h14v14H3Zm2 5v2.5h3V8Zm5 0v2.5h5V8Zm-5 4v3h3v-3Zm5 0v3h5v-3Z",
    "csv tsv xls xlsx ods numbers parquet",
  ),
  document: mark(
    "blue",
    "M4 3q0-1 1-1h6v5q0 2 2 2h4v7q0 2-2 2H6q-2 0-2-2ZM12.5 2.5l4 5h-3q-1 0-1-1ZM6.5 11v1.3h8V11Zm0 3v1.3h5V14Z",
    "doc docx odt rtf pages",
  ),
  markdown: mark(
    "green",
    "M1 5h3l3.5 4L11 5h2v10h-3V9.7l-2.5 3-3.6-3.5V15H1Zm14.5 0H18v6h2l-3.2 4-3.4-4h2.1Z",
    "md mdx markdown rst",
  ),
  archive: mark(
    "amber",
    "M2 4h16v3H2Zm1 4h14v8H3Zm4 2v2h6v-2Z",
    "zip tar gz bz2 xz tgz rar 7z zst jar",
  ),
  rust: mark(
    "orange",
    "M3 4h8.5c6 0 6.3 7 2.2 8L18 16h-4.5L9 11H7v5H3Zm4 2.7v2.1h4q2 0 2-1t-2-1.1Z",
    "rs",
  ),
  python: mark(
    "blue",
    "M4 6h5V5H6V3q4-2 7 0v5q0 2-2 2H7q-2 0-2 3v1H3q-2-4 1-8Zm6-2.8v1h1v-1ZM16 14h-5v1h3v2q-4 2-7 0v-4q0-2 2-2h3q3 0 3-3V6h2q2 5-1 8Zm-6 2v1h1v-1Z",
    "py pyw pyi ipynb",
  ),
  script: mark(
    "amber",
    "M5 4h2.7v8q0 5-5.7 3l.6-2q2.4 1 2.4-1Zm5 9q4.7 2 5-.2 0-.8-2.3-1.7Q8 9.5 10 6q1.5-3 7-1l-.7 2q-4-1.5-4 .3 0 .7 2.2 1.5 5 2 3 5.7-1.8 3-8 1Z",
    "js jsx mjs cjs",
  ),
  typescript: mark(
    "blue",
    "M.8 4h8.6v2.4h-3v9H3.8v-9h-3Zm10 9q4.7 2 5-.2 0-.8-2.3-1.7Q9 9.5 11 6q1.5-3 7-1l-.7 2q-4-1.5-4 .3 0 .7 2.2 1.5 5 2 3 5.7-1.8 3-8 1Z",
    "ts tsx mts cts",
  ),
  json: mark(
    "amber",
    "M8 2v2H6v4q0 1.3-1.4 2Q6 11 6 12v4h2v2H6q-2 0-2-2v-3q0-2-2-2V9q2 0 2-2V4q0-2 2-2Zm4 0h2q2 0 2 2v3q0 2 2 2v2q-2 0-2 2v3q0 2-2 2h-2v-2h2v-4q0-1.3 1.4-2Q14 9 14 8V4h-2Z",
    "json jsonc jsonl ndjson",
  ),
  shell: mark(
    "green",
    "m2 5 6 5-6 5-1.4-1.7 4-3.3-4-3.3Zm7.5 8H18v2h-8.5Z",
    "sh bash zsh fish bat cmd ps1",
  ),
  code: mark(
    "blue",
    "m6 5 1.6 1.6L4 10l3.6 3.5L6 15l-5-5Zm8 0 5 5-5 5-1.6-1.5L16 10l-3.6-3.4Z",
    "go java kt kts c h cpp hpp cs swift rb php lua html htm xml vue svelte dart scala",
  ),
  style: mark(
    "violet",
    "M3 3h14l-1.5 12-5.5 2.5L4.5 15Zm3.5 3v2H13l-.3 1.4H7l.6 3.5 2.4 1 2.5-1 .3-2H11l-.2 1-.8.3-.8-.3-.1-.8H14l.6-5Z",
    "css scss sass less",
  ),
  db: mark(
    "violet",
    "M3 5c0-4 14-4 14 0s-14 4-14 0Zm0 3q7 4 14 0v3q-7 4-14 0Zm0 5q7 4 14 0v2c0 4-14 4-14 0Z",
    "sql sqlite sqlite3 db",
  ),
  font: mark(
    "pink",
    "M7.5 3h3l5 13h-3l-1-3H6.5l-1 3H2.8Zm1.4 3.2L7.2 11h3.6Z",
    "otf ttf woff woff2 eot",
  ),
  binary: mark(
    "gray",
    "M3 5h6v10H3Zm2 2v6h2V7Zm6-1 3-1h2v10h-2.5V7.5l-2 .5Z",
    "bin exe dll so dylib wasm o a class",
  ),
};
const byExtension = new Map(
  Object.values(icons).flatMap((icon) =>
    icon.extensions
      .split(" ")
      .filter(Boolean)
      .map((ext) => [ext, icon] as const),
  ),
);
const special: ReadonlyMap<string, typeof icons.file> = new Map([
  ["dockerfile", icons.docker],
  [".dockerignore", icons.docker],
  [".gitignore", icons.git],
  [".gitattributes", icons.git],
  [".gitmodules", icons.git],
  [".pre-commit-config.yaml", icons.workflow],
  [".pre-commit-config.yml", icons.workflow],
  ["readme", icons.markdown],
  ["makefile", icons.shell],
  ["gemfile", icons.code],
]);

export function FileTypeIcon({
  name,
  kind = "file",
  size = 16,
}: {
  name: string;
  kind?: "file" | "directory" | "symlink" | "other";
  size?: number;
}) {
  const base = name.toLowerCase().split(/[\\/]/).pop() ?? "";
  const icon =
    kind === "directory"
      ? icons.folder
      : kind === "symlink"
        ? icons.link
        : kind === "other"
          ? icons.file
          : (special.get(base) ??
            (base.includes(".")
              ? byExtension.get(base.slice(base.lastIndexOf(".") + 1))
              : undefined) ??
            icons.file);
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 20 20"
      aria-hidden="true"
      focusable="false"
      style={{ color: palette[icon.color], flexShrink: 0 }}
      fill="currentColor"
    >
      <path d={icon.path} fillRule="evenodd" />
    </svg>
  );
}
