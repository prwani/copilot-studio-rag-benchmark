# Copilot Studio latency harness

Python harness for measuring Copilot Studio per-question latency and capturing the full streamed activity trace.

## Included agents

- `original` → `new_PWSharepointAgent` (`2a1e8a0f-9eb8-f111-aaad-00224835685d`)
- `opt` → `new_PWSharepointAgentOpt` (`d6b522f1-a8bf-f111-aaaf-00224835685d`)

Environment:

- Tenant: `a3321a7a-958c-4f4a-ad4f-f4d9c193c977`
- Environment: `Default-a3321a7a-958c-4f4a-ad4f-f4d9c193c977`
- Dataverse: `https://orgfe6514f3.crm.dynamics.com`

## Install

```bash
python -m pip install -r harness/requirements.txt
```

## Authentication

The harness supports these modes:

- `--auth auto` - try Azure CLI first, then device code
- `--auth az` - only use `az account get-access-token`
- `--auth device-code` - use a public-client Entra app and device-code login
- `--auth env` - use `COPILOT_ACCESS_TOKEN`

Prepared public-client app registration:

- Display name: `copilot-studio-latency-harness`
- Client ID: `a604fc0e-45e7-4988-ae9f-af882d575f46`

It has delegated `CopilotStudio.Copilots.Invoke` consented in the target tenant.

## Usage

Baseline run for one agent:

```bash
python harness/run.py --agent original --runs 3 --label baseline
```

Run both agents:

```bash
python harness/run.py --agent all --runs 3 --label baseline
```

Smoke test one question per agent with device code:

```bash
python harness/run.py --agent all --runs 1 --question-limit 1 --label smoke --auth device-code
```

## Output

- `harness/results.csv` - appended metric rows
- `harness/results.jsonl` - appended full records with streamed activities

Each row includes:

- conversation bootstrap time
- time to first response activity
- total turn time
- activity counts
- conversation id
- full raw activity stream in JSONL
