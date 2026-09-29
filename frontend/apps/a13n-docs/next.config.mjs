import { fileURLToPath } from "node:url";
import { createMDX } from "fumadocs-mdx/next";

// Content lives in the repository-level docs/ directory, outside this app.
const repositoryRoot = fileURLToPath(new URL("../../..", import.meta.url));

/** @type {import('next').NextConfig} */
const config = {
  output: "export",
  trailingSlash: true,
  reactStrictMode: true,
  agentRules: false,
  images: { unoptimized: true },
  outputFileTracingRoot: repositoryRoot,
  turbopack: { root: repositoryRoot },
  transpilePackages: ["a13n-ui"],
};

export default createMDX()(config);
