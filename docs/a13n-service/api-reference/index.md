---
title: Service API reference
sidebarTitle: API reference
description: Find an HTTP operation, prepare its request, and use the schema for your Service deployment.
---

Use this reference when you know which Service resource to read or change. The navigation groups operations by API area; each operation page shows parameters, request bodies, responses, and request examples. For a first integration, follow [Connect an application](../connect-application.md), then return here for individual fields.

1. Find the operation for your resource and task. Check the HTTP method, path, required parameters, and request body.
2. Apply [HTTP conventions](../http.md) for authentication, update preconditions, idempotency, pagination, and errors. Use credentials with access to the target workspace or resource.
3. Adapt the example to your deployment URL and resource IDs. Generated examples target the local quickstart at `http://127.0.0.1:8080`.
4. Read the response schema alongside the workflow guide. For example, [agents and runs](../agents-and-runs.md) explains how to follow a run after submission and handle waits or recovery.

To generate a client or import the API into another tool, use the OpenAPI document for the deployment you will call. A Service process serving the API publishes it at `/api/v1/openapi.json`. The downloadable [complete OpenAPI JSON](/reference/service-openapi.json) and these operation pages use the repository export published with this documentation; the public site's version follows `main`.
