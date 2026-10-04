import { fileURLToPath } from "node:url";
import { createMDX } from "fumadocs-mdx/next";
import { basePath } from "./lib/base-path.mjs";

// Content lives in the repository-level docs/ directory, outside this app.
const repositoryRoot = fileURLToPath(new URL("../../..", import.meta.url));

/** @type {import('next').NextConfig} */
const config = {
  output: "export",
  basePath,
  trailingSlash: true,
  reactStrictMode: true,
  agentRules: false,
  images: { unoptimized: true },
  outputFileTracingRoot: repositoryRoot,
  turbopack: { root: repositoryRoot },
  transpilePackages: ["a13n-ui"],
};

export default createMDX()(config);
