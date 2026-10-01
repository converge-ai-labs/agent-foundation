# AG-UI 1.0 wire conformance fixture

`schema.json` is the upstream normative AG-UI 1.0 schema, copied without semantic changes from `ag-ui-protocol/ag-ui`, `spec/1.0/schema.json`, revision `fdbca490dc90fd8fbce2b342df79383bd70559af` on October 1, 2026. Its MIT license is retained in `LICENSE`.

Protocol tests validate emitted canonical JSON against this closed schema, rather than relying only on the Python generated models (which accept extra fields). Tests are offline and do not refresh this fixture automatically. Updating it is an explicit protocol-contract change.
