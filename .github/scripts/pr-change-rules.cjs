// First matching category wins. Component ownership is independent of purpose.
const generated = new Set([
  ...require('../../proto/a13n-envd/eip/v1/artifacts/generated-files.json').files,
  'proto/a13n-service/openapi.json',
  'proto/a13n-service/notification-client.schema.json',
  'proto/a13n-service/run-stream-event.schema.json',
  'frontend/apps/a13n-console/src/service-client/schema.ts',
  'frontend/apps/a13n-harness-ui/src/api.generated.ts',
  'frontend/apps/a13n-harness-ui/src/openapi.json',
  'frontend/packages/a13n-ui/src/brand/lobe-brands.generated.ts',
]);

const categories = [
  'Product code', 'Tests & fixtures', 'Protocols & migrations', 'Specifications',
  'Documentation', 'Build, CI & deployment', 'Developer tools & examples',
  'Dependencies & lockfiles', 'Generated files', 'Unclassified',
];

const rules = [
  ['Specifications', /^spec\//],
  ['Generated files', {test: path => generated.has(path)}],
  ['Documentation', /(?:\.md$|(?:^|\/)(?:LICENSE|NOTICE)(?:\.[^/]+)?$)/i],
  ['Dependencies & lockfiles', /(?:^|\/)(?:pyproject\.toml|Cargo\.(?:toml|lock)|package(?:-lock)?\.json|pnpm-(?:lock|workspace)\.yaml|uv\.lock|requirements[^/]*\.txt)$/],
  ['Tests & fixtures', /(?:^|\/)(?:tests?|testdata|fixtures|__tests__|__snapshots__|live_tests)(?:\/|$)|(?:^|\/)(?:conftest\.py|test_[^/]+|[^/]+_test\.py)$|\.(?:test|spec)\.[cm]?[jt]sx?$/],
  ['Protocols & migrations', /^proto\/|\/database\/migrations\//],
  ['Build, CI & deployment', /^\.github\/|^deploy\/|^crates\/[^/]+\/build_support\/|^frontend\/(?:.*\/)?(?:tsconfig[^/]*\.json|\.prettierignore|coss-source\.json)$|(?:^|\/)(?:Makefile|Dockerfile|build\.rs|hatch_build\.py|build_skills\.py|[^/]+\.config\.[^/]+)$|^(?:\.[^/]+|mkdocs\.yml)$/],
  ['Developer tools & examples', /^(?:scripts|dev|examples|\.vscode|\.claude|\.agents)\/|^frontend\/.*\/(?:showcase|scripts|dev)\/|^frontend\/.*\/generate-[^/]+$/],
  ['Product code', /^packages\/[^/]+\/(?:src|a13n_[^/]+)\/|^crates\/[^/]+\/src\/|^frontend\/(?:apps|packages)\/[^/]+\/(?:src\/|public\/|index\.html$)/],
];

function classify(path) {
  return rules.find(([, pattern]) => pattern.test(path))?.[0] || 'Unclassified';
}

function component(path) {
  const match = path.match(/^(?:packages|crates|spec|proto)\/(a13n-[^/]+)\//)
    || path.match(/^frontend\/(?:apps|packages)\/(a13n-[^/]+)\//)
    || path.match(/^deploy\/[^/]+\/(a13n-[^/]+)(?:[/.]|$)/);
  return match?.[1] || 'Repository';
}

module.exports = {categories, classify, component};
