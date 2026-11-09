// Shiki resolves grammar IDs and aliases (rs, js, py, yml, etc.). Only map
// filenames and extensions that do not themselves identify the grammar.
const extensions: Record<string, string> = {
  rs: "rust",
  h: "c",
  cc: "cpp",
  cxx: "cpp",
  hpp: "cpp",
  hxx: "cpp",
  hh: "cpp",
  m: "objective-c",
  mm: "objective-cpp",
  cs: "csharp",
  fs: "fsharp",
  fsx: "fsharp",
  mjs: "javascript",
  cjs: "javascript",
  mts: "typescript",
  cts: "typescript",
  pyi: "python",
  pyw: "python",
  kts: "kotlin",
  kt: "kotlin",
  ex: "elixir",
  exs: "elixir",
  erl: "erlang",
  hrl: "erlang",
  pl: "perl",
  pm: "perl",
  rb: "ruby",
  rake: "ruby",
  gemspec: "ruby",
  hs: "haskell",
  lhs: "haskell",
  ml: "ocaml",
  mli: "ocaml",
  clj: "clojure",
  cljs: "clojure",
  cljc: "clojure",
  sc: "scala",
  v: "verilog",
  sv: "system-verilog",
  svh: "system-verilog",
  yml: "yaml",
  svg: "xml",
  xsd: "xml",
  xsl: "xml",
  csproj: "xml",
  plist: "xml",
  htm: "html",
  ipynb: "json",
  jsonl: "json",
  ndjson: "json",
  bashrc: "bash",
  zshrc: "bash",
  ps1: "powershell",
  psm1: "powershell",
  tf: "terraform",
  tfvars: "terraform",
  hcl: "hcl",
  cmake: "cmake",
  mk: "make",
  md: "markdown",
  mdown: "markdown",
  props: "xml",
};
const filenames: Record<string, string> = {
  "cargo.lock": "toml",
  "go.mod": "go-mod",
  "go.sum": "go-sum",
  "cmakelists.txt": "cmake",
  gemfile: "ruby",
  rakefile: "ruby",
  makefile: "make",
  gnumakefile: "make",
  justfile: "just",
  ".gitignore": "gitignore",
  ".dockerignore": "gitignore",
  ".gitattributes": "git-attrs",
  ".editorconfig": "ini",
  ".npmrc": "ini",
  ".prettierrc": "json",
  "tsconfig.json": "jsonc",
};
export function fileLanguage(path: string, firstLine = "") {
  const name = path.toLowerCase().split(/[\\/]/).pop() ?? "";
  if (/^(dockerfile|containerfile)(\.|$)/.test(name)) return "dockerfile";
  if (/^\.env(\.|$)/.test(name)) return "dotenv";
  if (filenames[name]) return filenames[name];
  const ext = name.split(".").pop() ?? "";
  if (name.includes(".")) return extensions[ext] ?? ext;
  if (firstLine.startsWith("#!")) {
    const lang = firstLine.match(
      /\b(python[23]?|node|ruby|perl|bash|sh|zsh|fish)\b/,
    )?.[1];
    if (lang)
      return lang.startsWith("python")
        ? "python"
        : lang === "node"
          ? "javascript"
          : lang;
  }
  return "";
}
