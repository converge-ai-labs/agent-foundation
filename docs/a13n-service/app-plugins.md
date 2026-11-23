---
title: Service Apps
description: Build and install Service App plugins that contribute API routes, Console UI views, Harness plugin factories, and custom metrics.
---

A **Service App** extends A13n Service with domain-specific APIs, Console pages, durable storage, and Harness middleware plugins while reusing Service's authentication, workspaces, and execution runtime.

## Define and install an App

Package an `AppDefinition` and register it via Python entry points so Service discovers it at startup:

- **`factory_keys`:** Declare owned Harness plugin factory keys (registered under `a13n_harness.plugins`, see [Plugins and extensions](../a13n-harness/plugins.md#publish-a-plugin-factory)). Service loads owned factories at startup and gates their instances per accepted Run.
- **Routes & Console UI:** Expose workspace-scoped HTTP endpoints and bundle static Console assets inside the App wheel.
- **Migrations & data:** Keep App tables in an isolated schema owned by the App package.

## Report custom metrics

Installed App plugins can emit custom OpenTelemetry metrics through Service's existing meter provider. Use an App-specific metric prefix and bounded attributes; Service already instruments HTTP routes and background sweeps automatically.
