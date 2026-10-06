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

## SDK benchmark

`run_sdk.py` uses `microsoft-agents-copilotstudio-client` 1.8.0
(`CopilotClient.start_conversation` and `CopilotClient.ask_question`) with the
same authentication modes and agent selection as `run.py`. Each question starts
a fresh conversation. By default it runs the 22 queries in `questions_user.txt`,
the file passed to `run.py` by the benchmark sweeps; `run.py` itself defaults to
the separate 10-question `questions.txt`.

```bash
python harness/run_sdk.py --agent original --runs 1 --label sdk --auth device-code
# Equivalent query set with the HTTP harness:
python harness/run.py --agent original --runs 1 --label http --auth device-code --questions-file harness/questions_user.txt
```

Use `--question-limit 1` for a smoke test or `--questions-file` for a custom set.
Outputs append to `harness/sdk_results.jsonl` and `harness/sdk_results.csv`,
with the same fields as `run.py`; override them with `--results-jsonl` and
`--results-csv`. Partial activity traces are retained on errors.

Each activity is timestamped immediately when the SDK yields it, before
serialization. `arrival_ms` is relative to the start of that phase's SDK call,
including request latency, unlike `run.py` which starts its activity clock
after receiving response headers. The SDK has already parsed the activity at
this point, so these are application-arrival times, not wire-arrival times.
`raw_data` is reserialized activity JSON, not the original SSE bytes.
`--timeout-seconds` limits each complete start/turn stream.

### Dynamic-plan events

The SDK's `CopilotClient.post_request` yields every SSE `activity` as an
`Activity`, without filtering its activity type or name. Consequently,
`DynamicPlanReceived` and `DynamicPlanStepFinished` **are exposed as generic
event activities if the service sends them** (`type: event`, `name` matching
the event, payload in `value`); there are no dedicated callbacks for these names.
The runner preserves them in `activities[].raw` and prints observed counts.
Not observing an event does not establish that the SDK cannot expose it.
Non-activity SSE events are not yielded by the SDK and therefore are not
captured. This is based on SDK source inspection, not a live authenticated run.

Offline SDK tests (no credentials needed):

```bash
python -m unittest discover -s harness -p 'test_run_sdk.py'
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
