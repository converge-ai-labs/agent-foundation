"""The OSS artifact's explicit internal tool contributions."""

from a13n_service.platform_tools import Definition, validate_catalog


def catalog() -> tuple[Definition, ...]:
    from a13n_service.resources.agents.platform_tools import TOOLS as agents
    from a13n_service.resources.connections.platform_tools import TOOLS as connections
    from a13n_service.resources.connector_providers.platform_tools import TOOLS as connectors
    from a13n_service.resources.environment_templates.platform_tools import TOOLS as templates
    from a13n_service.resources.models.platform_tools import TOOLS as models
    from a13n_service.resources.providers.platform_tools import TOOLS as providers
    from a13n_service.resources.skills.platform_tools import TOOLS as skills
    from a13n_service.runs.content_tools import TOOLS as content
    from a13n_service.runs.findings.platform_tools import TOOLS as findings
    from a13n_service.runs.platform_queries import TOOLS as runs
    from a13n_service.runs.schedules.platform_tools import TOOLS as schedules
    from a13n_service.runs.tool_executions.platform_tools import TOOLS as executions
    from a13n_service.runs.trace_tools import TOOLS as traces

    result = (
        *agents,
        *models,
        *skills,
        *templates,
        *connections,
        *connectors,
        *providers,
        *schedules,
        *runs,
        *traces,
        *executions,
        *findings,
        *content,
    )
    validate_catalog(result)
    return result
