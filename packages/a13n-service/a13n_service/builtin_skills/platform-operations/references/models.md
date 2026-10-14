# Models

List configured Models and read the selected key before changing an Agent. The model catalog is descriptive; creating or selecting a configured Model still requires an authorized Provider resource. Model settings follow the deployed Provider's calling API schema in the instance catalog. Do not invent fields.

Model resources are shared by key. Explain the affected shared settings before updating one. To change one Agent's sampling or model behavior, prefer that Agent's `model_settings` when appropriate. Keep unrelated settings intact. Read the Model's ETag and pass it to updates. Provider credentials are entered in the Console, never in chat.

Provider updates preserve omitted credentials and extra header values, including when connection settings change. An empty `extra_headers` object also keeps saved values. Submit only intended secret changes; a header value of `null` removes that header. The updated configuration must still permit the retained credential and header names.

The `anthropic` Provider automatically sends its one saved API key in both `X-Api-Key` and `Authorization: Bearer`, including on connection probes and custom gateway URLs. Do not ask for or configure a separate Authorization header. Both names are reserved; remove an existing custom Authorization entry when upgrading.
