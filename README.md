# Service App Cookiecutter template

This dedicated `template/app-plugin` branch generates an independent, GitHub-ready Service App project with a Python backend, a host-mounted Console frontend, a Makefile and GitHub Actions build/package checks. It has no license file or project license metadata. The existing `examples/service-app-plugin` on the product development branch remains a separate, unchanged integration example.

## Generate a project

```bash
uvx cookiecutter gh:converge-ai-labs/agent-foundation --checkout template/app-plugin
```

For a noninteractive project:

```bash
uvx cookiecutter gh:converge-ai-labs/agent-foundation --checkout template/app-plugin --no-input project_name="Orbit tools" project_slug=orbit-tools app_id=orbit.tools
```

Cookiecutter creates a local directory; the generated README explains how to build it, install it in Service and create a GitHub repository. Generation itself does not create a remote repository.

| Input | Default | Meaning |
| --- | --- | --- |
| `project_name` | `My Service App` | Display name |
| `project_slug` | `my-service-app` | Directory, distribution and suggested repository name |
| `module_name` | Derived from slug | Python import package |
| `app_id` | `example.my-app` | Stable App identity; choose an organization-specific value |
| `version` | `0.1.0` | Initial stable package version |
| `service_ref` | `9e47871423a2efe2e4f8704d6aa8d77f8b5ff00c` | Compatible prerelease Service commit |

## Development and compatibility

The generated project uses Python 3.13, uv, Make, Node.js 24 and pnpm 10.30.3. Its `make setup` installs the pinned Service source contract in its own virtual environment; `make verify` builds the ESM frontend, packages wheel/sdist, and tests the installed wheel. Generated CI follows the repository's uv/pnpm setup, dependency caches and least-privilege pattern. A separate `vX.Y.Z` tag workflow builds verified artifacts, then publishes them through a protected `pypi` environment configured by the project owner with `PYPI_TOKEN`. `make dev` rebuilds frontend assets on changes for an existing Service/Console host.

The App APIs are currently on the [App Plugins development stack](https://github.com/converge-ai-labs/agent-foundation/stack/1644). The source pin makes that dependency explicit until a compatible Service release exists. The generated project does not claim compatibility with an older PyPI release.

Both artifacts include prebuilt frontend assets, so a wheel can be rebuilt from the sdist without Node.js. The frontend imports React and host providers only through `@a13n/console`. No fonts, third-party assets, separate React root or workspace-home override are included. Project owners choose their licensing terms before distribution; dependencies retain their own licenses.

## Maintain this branch

This is a template-only branch with its own history. Update it directly through focused commits; do not merge the monorepo tree into it or open a PR that replaces `main` with this tree. Keep generated repositories outside the monorepo's Python and pnpm workspaces.

Run template contract tests:

```bash
uv run --python 3.13 --no-project --with-requirements requirements-dev.txt pytest tests -q
```

The branch workflow runs those tests, generates default and renamed projects, and executes each project's setup, build, installed-wheel tests and source-distribution rebuild. When updating `service_ref`, validate against the real Service App discovery and bridge contracts. Keep existing examples unchanged unless a separate change requests otherwise.
