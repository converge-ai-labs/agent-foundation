#!/usr/bin/env bash
# Select the linux/amd64 manifest before reading provenance. A concurrent dev
# publication cannot change the inspected image or the image consumers pull.
set -euo pipefail
repository=ghcr.io/converge-ai-labs/a13n-sandbox
if ! digest=$(docker buildx imagetools inspect "$repository:dev" --raw |
    jq -er '.manifests[] | select(.platform.os == "linux" and .platform.architecture == "amd64") | .digest') ||
   [[ ! "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "Development sandbox unavailable. Build the checkout image."
  exit 0
fi
image="$repository@$digest"
if ! revision=$(docker buildx imagetools inspect "$image" --format '{{json .Image}}' |
    jq -er '.config.Labels | select(.["ai.a13n.sandbox.variant"] == "standard") | .["org.opencontainers.image.revision"]') ||
   [[ ! "$revision" =~ ^[0-9a-f]{40}$ ]] || ! git cat-file -e "$revision^{commit}"; then
  echo "No verifiable development revision. Build the checkout image."
  exit 0
fi
if git diff --quiet "$revision" HEAD -- Cargo.toml Cargo.lock crates/a13n-envd .dockerignore LICENSE \
    deploy/docker/images/sandbox .github/workflows/images.yml .github/workflows/sandbox-binary.yml; then
  echo "Reusing sandbox from $revision ($digest)."
  echo "image=$image" >> "$GITHUB_OUTPUT"
else
  echo "Sandbox inputs differ from $revision. Build the checkout image."
fi
