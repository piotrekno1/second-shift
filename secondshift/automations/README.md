# Automations app

This Django app owns scheduled skills from discovery through audited execution.
It uses framework packages for generic machinery and keeps only SecondShift's
policy and integration code here.

## Request and execution flow

```text
.secondshift.yaml -> sync -> Job + CapabilityRequest
                              |
owner approval -> JobGrant -> execution plan
                              |
                         DBOS workflow
                              |
                 Pydantic AI orchestrator
                    /                   \
          model gateway             runner service
                                     (offline sandbox)
```

The important boundary is between orchestration and execution. Pydantic AI runs
in the worker and reaches models only through SecondShift's gateway. Repository
commands run in the separate runner's networkless sandbox. Repository input
cannot select tools, credentials, images, mounts, networks, or runtime flags.

## Where code belongs

- Django surface: `models.py`, `api.py`, `admin.py`, `apps.py`, and `signals.py`.
- Repository policy: `manifest.py`, `sync.py`, `needs.py`, `planning.py`, and
  `tool_policy.py`.
- Durable orchestration: `agent.py`, `execution.py`, `model_gateway.py`,
  `workflows.py`, `dispatch.py`, and `dbos_runtime.py`.
- Isolation boundary: `runner_protocol.py` and `runner_client.py`. The `runner`
  Django app implements the other side of this contract.
- Operations: `scheduling.py`, `reconciliation.py`, `retention.py`,
  `artifacts.py`, `notifications.py`, and `run_queries.py`.

These modules are split by policy boundary, not into one-file layers. New files
should represent a distinct boundary or reusable domain concept; endpoint-only
business logic belongs in an existing domain module.

## Package choices

- Django owns models, validation, settings, admin, migrations, and database
  access in both the control plane and runner.
- Django Ninja owns typed HTTP schemas and per-app routing.
- DBOS owns schedules, durable workflows, steps, and retry recovery.
- Pydantic AI owns the provider-neutral agent loop, model adapters, tool calling,
  usage limits, and DBOS durability integration.

SecondShift still owns authorization-plan compilation, gateway tokens, runner
and broker tools, output bounds, and audit records. Those rules are product
policy and should not be delegated to an agent SDK.
