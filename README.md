# Service App Cookiecutter template

This dedicated `template/app-plugin` branch generates an independent, GitHub-ready Service App project with your choice of a full-stack App, a Console frontend, or a Python backend. Every project includes a Makefile and GitHub Actions build/package checks. It has no license file or project license metadata. The existing `examples/service-app-plugin` on `main` remains a separate, unchanged integration example.

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
| `service_version` | Current stable template default | Exact Service dependency for the generated project |
| `surface` | `full-stack` | `full-stack`, `frontend-only`, or `backend-only` |

## Development and compatibility

From the generated project, run `make dev`, open the printed Console URL, and enable the App in organization settings. The local Service wheel hosts Console and loads the App editable; Compose provides PostgreSQL, Redis and S3. Frontend variants watch TSX/CSS; every variant watches Python sources. `make verify` builds wheel/sdist and tests the installed wheel without replacing the editable install.

The pinned dependency lives in the generated `pyproject.toml`. Stable Service releases advance the Cookiecutter default for new projects; existing projects upgrade their dependency and lockfile explicitly. Generated projects can publish independently via their `vX.Y.Z` tag workflow.

## Maintain this branch

This is a template-only branch with its own history. Update it directly through focused commits; do not merge the monorepo tree into it or open a PR that replaces `main` with this tree. Keep generated repositories outside the monorepo's Python and pnpm workspaces.

Run template contract tests:

```bash
uv run --python 3.13 --no-project --with-requirements requirements-dev.txt pytest tests -q
```

The branch workflow runs those tests, generates default and renamed projects, and executes each project's setup, build, installed-wheel tests and source-distribution rebuild. Keep existing examples unchanged unless a separate change requests otherwise.
