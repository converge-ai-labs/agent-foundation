# Connections

Read the Connection's current configuration, status and discovered tool definitions. A Connection test can call an external service and has write-effect review even though it does not create a resource. Tool discovery describes currently available tools; it does not authorize invocation. Only choose names actually returned by discovery.

For a connector account, query the Provider's app catalog, app details and action definitions before creating the Connection or selecting tools. For remote MCP, query the suggested server catalog and the Connection's live tools. Catalog entries describe supported capabilities; the current account and workspace still determine access.

Use existing resource create/update APIs and their configuration schemas. Reconfiguration or revocation can invalidate authorization: explain the effect and then inspect the returned state. Never collect credential values. For OAuth or secret entry, ask the user to open the existing Connection page, authorize there and return; query status again afterwards. Do not guess authorization success or automatically repeat a test with an unknown result.

Search `/connections/{connection_id}/tools/index` or `/connector-providers/{provider_id}/apps/{app}/actions/index` with `query`, `limit` and `cursor`. Search covers full names and descriptions, while index results omit schemas. Use the matching `/definitions` endpoint with repeated exact `name` values (at most five) for selected definitions. Check `missing` and `oversized`; oversized definitions require single-name versioned fragments. Never treat a partial schema as a complete invocation contract. Original full-list endpoints remain available for existing callers.
