-- Run as the table owner after creating a13n_usage_reader. Reapplying also removes
-- the older bundle's whole-table grants. Do not give this role broader memberships.
REVOKE SELECT ON organizations, workspaces, agents, models, runs, usage_records FROM a13n_usage_reader;
GRANT SELECT (id, name) ON organizations TO a13n_usage_reader;
GRANT SELECT (id, organization_id, name) ON workspaces TO a13n_usage_reader;
GRANT SELECT (id, name) ON agents TO a13n_usage_reader;
GRANT SELECT (id, key) ON models TO a13n_usage_reader;
GRANT SELECT (id, organization_id, workspace_id, agent_id, status, failure, started_at, sealed_at)
    ON runs TO a13n_usage_reader;
GRANT SELECT (organization_id, workspace_id, run_id, model_id, record, ingested_at)
    ON usage_records TO a13n_usage_reader;
