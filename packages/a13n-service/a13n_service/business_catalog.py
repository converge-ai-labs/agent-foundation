"""The OSS artifact's explicit internal tool contributions."""

from a13n_service.business_tools import Definition, validate_catalog


def catalog() -> tuple[Definition, ...]:
    from a13n_service.resources.agents.business_tools import TOOLS as agents
    from a13n_service.resources.connections.business_tools import TOOLS as connections
    from a13n_service.resources.connector_providers.business_tools import TOOLS as connectors
    from a13n_service.resources.environment_templates.business_tools import TOOLS as templates
    from a13n_service.resources.models.business_tools import TOOLS as models
    from a13n_service.resources.providers.business_tools import TOOLS as providers
    from a13n_service.resources.skills.business_tools import TOOLS as skills
    from a13n_service.runs.business_content import TOOLS as content
    from a13n_service.runs.business_tools import TOOLS as runs
    from a13n_service.runs.findings.business_tools import TOOLS as findings
    from a13n_service.runs.schedules.business_tools import TOOLS as schedules
    from a13n_service.runs.tool_executions.business_tools import TOOLS as executions
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
