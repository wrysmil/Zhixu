# How to Configure Langfuse Observability for Zhixu

Zhixu can trace supported OpenAI-compatible provider calls through Langfuse's
OpenAI SDK wrapper.

## What you will build

- Langfuse installed in the same Python environment as Zhixu
- Langfuse environment variables set before startup
- one traced Zhixu model call

## When to use this

Use Langfuse when you need observability for model requests, latency, errors,
cost, or prompt behavior during development or production operation.

## Install

Install Zhixu and prove the agent works:

```bash
python -m pip install nanobot-ai
nanobot onboard --wizard
nanobot agent -m "Hello!"
```

Install Langfuse:

```bash
nanobot plugins enable langfuse
```

### Self-hosted Langfuse in Docker

This repository ships a local Langfuse v3 stack matching the Python SDK pin:

```bash
cd docker/langfuse
docker compose up -d
```

First boot creates the organization, project, API keys, and admin user from the
`LANGFUSE_INIT_*` values in `docker/langfuse/.env` (gitignored). Then point Zhixu
at the local instance:

```powershell
[Environment]::SetEnvironmentVariable('LANGFUSE_PUBLIC_KEY', '<pk-lf-...>', 'User')
[Environment]::SetEnvironmentVariable('LANGFUSE_SECRET_KEY', '<sk-lf-...>', 'User')
[Environment]::SetEnvironmentVariable('LANGFUSE_BASE_URL', 'http://127.0.0.1:3000', 'User')
```

Restart the gateway or desktop app afterwards so the process inherits the new
variables. See [`docker/langfuse/README.md`](../../docker/langfuse/README.md) for
ports, credentials, data retention, and upgrade notes.

## Minimal working example

Set credentials before starting Zhixu:

```bash
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_BASE_URL="https://cloud.langfuse.com"
nanobot agent -m "Hello!"
```

PowerShell:

```powershell
$env:LANGFUSE_SECRET_KEY = "sk-lf-..."
$env:LANGFUSE_PUBLIC_KEY = "pk-lf-..."
$env:LANGFUSE_BASE_URL = "https://cloud.langfuse.com"
nanobot agent -m "Hello!"
```

## Production notes

- Langfuse is configured with environment variables, not `config.json`.
- Start services from an environment that exports the same variables.
- Add tracing after the provider works; it should not be the first setup step.
- Native providers that do not use the OpenAI-compatible client path may not
  produce Langfuse OpenAI-wrapper traces.

## Security notes

- Treat Langfuse projects as observability stores for sensitive prompts and
  outputs.
- Use separate projects for personal, staging, and production traffic.
- Keep Langfuse keys out of committed service files.

## Troubleshooting

- If no traces appear, confirm the service process sees the environment
  variables.
- Confirm the provider path is OpenAI-compatible.
- Run one local `nanobot agent -m "Hello!"` call before debugging service logs.

## Related Zhixu docs

- [Configuration: Langfuse Observability](../configuration.md#langfuse-observability)
- [Provider Cookbook: Langfuse Tracing](../provider-cookbook.md#recipe-langfuse-tracing)
- [Deployment](../deployment.md)
