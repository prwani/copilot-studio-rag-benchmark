#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import json
import math
import os
import subprocess
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import msal
import requests


TENANT_ID = "a3321a7a-958c-4f4a-ad4f-f4d9c193c977"
ENVIRONMENT_ID = "Default-a3321a7a-958c-4f4a-ad4f-f4d9c193c977"
DATAVERSE_URL = "https://orgfe6514f3.crm.dynamics.com"
POWER_PLATFORM_AUDIENCE = "https://api.powerplatform.com"
POWER_PLATFORM_SCOPE = "https://api.powerplatform.com/CopilotStudio.Copilots.Invoke"
DEFAULT_PUBLIC_CLIENT_ID = "a604fc0e-45e7-4988-ae9f-af882d575f46"
DEFAULT_TOKEN_COMMAND = (
    "az account get-access-token --resource https://api.powerplatform.com "
    "--query accessToken -o tsv"
)
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_QUESTIONS_PATH = SCRIPT_DIR / "questions.txt"
DEFAULT_RESULTS_CSV = SCRIPT_DIR / "results.csv"
DEFAULT_RESULTS_JSONL = SCRIPT_DIR / "results.jsonl"


@dataclass(frozen=True)
class AgentConfig:
    key: str
    bot_id: str
    schema_name: str
    display_name: str


AGENTS: dict[str, AgentConfig] = {
    "original": AgentConfig(
        key="original",
        bot_id="2a1e8a0f-9eb8-f111-aaad-00224835685d",
        schema_name="new_PWSharepointAgent",
        display_name="PWSharepointAgent",
    ),
    "opt": AgentConfig(
        key="opt",
        bot_id="d6b522f1-a8bf-f111-aaaf-00224835685d",
        schema_name="new_PWSharepointAgentOpt",
        display_name="PWSharepointAgent-Opt",
    ),
    "aisearch": AgentConfig(
        key="aisearch",
        bot_id="d5ede7cc-d9bf-f111-aaaf-00224835685d",
        schema_name="cr350_PWSharepointAgentOpt1",
        display_name="PWAISearchAgent",
    ),
    "aisearch-opt": AgentConfig(
        key="aisearch-opt",
        bot_id="6e22f4db-edbf-f111-aaaf-00224835685d",
        schema_name="cr350_PWAISearchAgentOpt",
        display_name="PWAISearchAgent-Opt",
    ),
    "sp-single": AgentConfig(
        key="sp-single",
        bot_id="8555ab06-98c0-f111-aaaf-00224835685d",
        schema_name="new_PWSharepointAgentSingleKS",
        display_name="PWSharepointAgent-SingleKS",
    ),
    "ai-single": AgentConfig(
        key="ai-single",
        bot_id="9b9a0238-d3c0-f111-aaaf-00224835685d",
        schema_name="cr350_PWAISearchAgentSingleKS",
        display_name="PWAISearchAgent-SingleKS",
    ),
}


class HarnessError(RuntimeError):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_environment_host(environment_id: str) -> str:
    normalized = environment_id.lower().replace("-", "")
    if len(normalized) < 3:
        raise HarnessError(f"Unexpected environment id format: {environment_id}")
    return f"{normalized[:-2]}.{normalized[-2:]}.environment.api.powerplatform.com"


def build_conversation_url(agent: AgentConfig, conversation_id: Optional[str] = None) -> str:
    host = build_environment_host(ENVIRONMENT_ID)
    if conversation_id:
        path = (
            f"/copilotstudio/dataverse-backed/authenticated/bots/"
            f"{agent.schema_name}/conversations/{conversation_id}"
        )
    else:
        path = (
            f"/copilotstudio/dataverse-backed/authenticated/bots/"
            f"{agent.schema_name}/conversations"
        )
    return f"https://{host}{path}?api-version=2022-03-01-preview"


def decode_jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")))
    except Exception as exc:  # pragma: no cover - defensive
        raise HarnessError(f"Failed to decode access token claims: {exc}") from exc


def token_has_scope(token: str, required_scope: str) -> bool:
    claims = decode_jwt_claims(token)
    scopes = set((claims.get("scp") or "").split())
    return required_scope in scopes


def ensure_scope(token: str, required_scope: str, auth_mode: str) -> None:
    if token_has_scope(token, required_scope):
        return
    claims = decode_jwt_claims(token)
    present = claims.get("scp") or ""
    appid = claims.get("appid") or "unknown"
    raise HarnessError(
        "Access token is missing CopilotStudio.Copilots.Invoke. "
        f"mode={auth_mode}, appid={appid}, scopes={present or '<none>'}. "
        "Use --auth device-code for the prepared public client flow."
    )


def run_command_capture(command: str) -> str:
    completed = subprocess.run(
        command,
        shell=True,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout).strip()
        raise HarnessError(f"Token command failed ({completed.returncode}): {stderr}")
    return completed.stdout.strip()


def acquire_token_via_az(token_command: str) -> str:
    token = run_command_capture(token_command)
    if not token:
        raise HarnessError("Azure CLI token command returned an empty token.")
    return token


def acquire_token_via_device_code(client_id: str, tenant_id: str) -> str:
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    cache_path = Path.home() / ".cache" / "copilot-studio-harness" / "msal_cache.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        cache.deserialize(cache_path.read_text())
    app = msal.PublicClientApplication(client_id=client_id, authority=authority, token_cache=cache)
    scopes = [POWER_PLATFORM_SCOPE]

    def persist() -> None:
        if cache.has_state_changed:
            cache_path.write_text(cache.serialize())
            cache_path.chmod(0o600)

    accounts = app.get_accounts()
    if accounts:
        silent = app.acquire_token_silent(scopes, account=accounts[0])
        if silent and "access_token" in silent:
            persist()
            return str(silent["access_token"])
    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" not in flow:
        raise HarnessError(f"Failed to start device-code flow: {json.dumps(flow)}")
    print(flow.get("message", "Complete device-code sign-in."), file=sys.stderr)
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise HarnessError(f"Device-code auth failed: {json.dumps(result)}")
    persist()
    return str(result["access_token"])


def acquire_token(args: argparse.Namespace) -> tuple[str, str]:
    if args.auth == "env":
        token = os.environ.get("COPILOT_ACCESS_TOKEN", "").strip()
        if not token:
            raise HarnessError("COPILOT_ACCESS_TOKEN is not set.")
        ensure_scope(token, "CopilotStudio.Copilots.Invoke", "env")
        return token, "env"

    if args.auth in {"az", "auto"}:
        try:
            token = acquire_token_via_az(args.token_command)
            ensure_scope(token, "CopilotStudio.Copilots.Invoke", "az")
            return token, "az"
        except HarnessError:
            if args.auth == "az":
                raise

    if args.auth in {"device-code", "auto"}:
        if args.auth == "auto" and not sys.stdin.isatty():
            raise HarnessError(
                "Azure CLI token lacks CopilotStudio.Copilots.Invoke and auto mode cannot "
                "start interactive device code in a non-interactive shell. Re-run with "
                "--auth device-code from an interactive terminal."
            )
        token = acquire_token_via_device_code(args.client_id, args.tenant_id)
        ensure_scope(token, "CopilotStudio.Copilots.Invoke", "device-code")
        return token, "device-code"

    raise HarnessError(f"Unsupported auth mode: {args.auth}")


def load_questions(path: Path) -> list[str]:
    if not path.exists():
        raise HarnessError(f"Questions file not found: {path}")
    questions: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        questions.append(line)
    if not questions:
        raise HarnessError(f"No questions found in {path}")
    return questions


def extract_conversation_id(
    activities: Iterable[dict[str, Any]], fallback_headers: dict[str, str]
) -> Optional[str]:
    for item in activities:
        raw = item.get("raw")
        if isinstance(raw, dict):
            conversation = raw.get("conversation")
            if isinstance(conversation, dict) and conversation.get("id"):
                return str(conversation["id"])
    for key in ("x-ms-conversationid", "X-MS-ConversationId"):
        if fallback_headers.get(key):
            return fallback_headers[key]
    return None


def summarize_activity_types(activities: Iterable[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in activities:
        raw = item.get("raw")
        if isinstance(raw, dict):
            counter[str(raw.get("type") or "unknown")] += 1
        else:
            counter[f"sse:{item.get('sse_event') or 'unknown'}"] += 1
    return dict(counter)


def parse_sse_response(response: requests.Response, clock_start: float) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    event_type: Optional[str] = None
    data_lines: list[str] = []

    def flush_block() -> None:
        nonlocal event_type, data_lines
        if not event_type and not data_lines:
            return
        arrival_ms = round((time.perf_counter() - clock_start) * 1000, 3)
        payload = "\n".join(data_lines)
        record: dict[str, Any] = {
            "arrival_ms": arrival_ms,
            "sse_event": event_type or "message",
            "raw_data": payload,
        }
        if (event_type or "message") == "activity" and payload:
            try:
                activity = json.loads(payload)
                record["raw"] = activity
                record["activity_type"] = activity.get("type")
                record["activity_name"] = activity.get("name")
                record["activity_id"] = activity.get("id")
                record["text"] = activity.get("text")
            except json.JSONDecodeError:
                record["parse_error"] = "invalid_json_activity"
        records.append(record)
        event_type = None
        data_lines = []

    for line in response.iter_lines(decode_unicode=True):
        if line is None:
            continue
        if line == "":
            flush_block()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line.partition(":")[2].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line.partition(":")[2].lstrip())
            continue
    flush_block()
    return records


def stream_post(
    url: str,
    token: str,
    payload: dict[str, Any],
    timeout_seconds: int,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    with requests.post(
        url,
        headers=headers,
        json=payload,
        stream=True,
        timeout=(30, timeout_seconds),
    ) as response:
        if response.status_code != 200:
            body = response.text[:1000]
            raise HarnessError(
                f"HTTP {response.status_code} from Copilot Studio endpoint: {body}"
            )
        started = time.perf_counter()
        activities = parse_sse_response(response, started)
        return activities, dict(response.headers)


def start_conversation(
    agent: AgentConfig,
    token: str,
    timeout_seconds: int,
) -> tuple[str, list[dict[str, Any]], float]:
    started = time.perf_counter()
    activities, headers = stream_post(
        build_conversation_url(agent),
        token,
        {"emitStartConversationEvent": True},
        timeout_seconds,
    )
    conversation_id = extract_conversation_id(activities, headers)
    if not conversation_id:
        raise HarnessError("Conversation started but no conversation id was returned.")
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return conversation_id, activities, elapsed_ms


def ask_question(
    agent: AgentConfig,
    token: str,
    conversation_id: str,
    question: str,
    timeout_seconds: int,
) -> tuple[list[dict[str, Any]], Optional[float], float]:
    payload = {
        "activity": {
            "type": "message",
            "text": question,
            "conversation": {"id": conversation_id},
        }
    }
    started = time.perf_counter()
    activities, _headers = stream_post(
        build_conversation_url(agent, conversation_id=conversation_id),
        token,
        payload,
        timeout_seconds,
    )
    total_ms = round((time.perf_counter() - started) * 1000, 3)
    first_ms = activities[0]["arrival_ms"] if activities else None
    return activities, first_ms, total_ms


def percentile(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * p
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * weight


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def append_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_parent(path)
    fieldnames = [
        "batch_id",
        "label",
        "run_index",
        "agent",
        "agent_display_name",
        "agent_schema_name",
        "bot_id",
        "question_index",
        "question",
        "conversation_id",
        "auth_mode",
        "conversation_bootstrap_ms",
        "time_to_first_response_activity_ms",
        "total_time_ms",
        "start_activity_count",
        "turn_activity_count",
        "activity_type_counts",
        "started_at",
        "status",
        "error",
    ]
    write_header = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_parent(path)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def print_summary(rows: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["agent"]), []).append(row)
    print("")
    print("Latency summary")
    print("===============")
    for agent_key, agent_rows in grouped.items():
        ok_rows = [row for row in agent_rows if row.get("status") == "ok"]
        firsts = [float(row["time_to_first_response_activity_ms"]) for row in ok_rows if row.get("time_to_first_response_activity_ms") not in ("", None)]
        totals = [float(row["total_time_ms"]) for row in ok_rows if row.get("total_time_ms") not in ("", None)]
        failures = len(agent_rows) - len(ok_rows)
        p50_first = percentile(firsts, 0.50)
        p95_first = percentile(firsts, 0.95)
        p50_total = percentile(totals, 0.50)
        p95_total = percentile(totals, 0.95)
        print(
            f"{agent_key}: n={len(agent_rows)} ok={len(ok_rows)} failed={failures} "
            f"TTFRA p50={format_metric(p50_first)} p95={format_metric(p95_first)} "
            f"Total p50={format_metric(p50_total)} p95={format_metric(p95_total)}"
        )


def format_metric(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f} ms"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a Copilot Studio latency harness against the provided agents."
    )
    parser.add_argument("--agent", choices=["original", "opt", "aisearch", "aisearch-opt", "all"], required=True)
    parser.add_argument("--runs", type=int, default=1, help="How many full passes to execute.")
    parser.add_argument("--label", default=datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--questions-file", type=Path, default=DEFAULT_QUESTIONS_PATH)
    parser.add_argument("--question-limit", type=int, default=0, help="Optional limit for smoke tests.")
    parser.add_argument("--results-csv", type=Path, default=DEFAULT_RESULTS_CSV)
    parser.add_argument("--results-jsonl", type=Path, default=DEFAULT_RESULTS_JSONL)
    parser.add_argument(
        "--auth",
        choices=["auto", "az", "device-code", "env"],
        default="auto",
        help="Authentication mode. auto tries Azure CLI first, then device code.",
    )
    parser.add_argument("--token-command", default=DEFAULT_TOKEN_COMMAND)
    parser.add_argument("--client-id", default=DEFAULT_PUBLIC_CLIENT_ID)
    parser.add_argument("--tenant-id", default=TENANT_ID)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    return parser.parse_args()


def select_agents(agent_arg: str) -> list[AgentConfig]:
    if agent_arg == "all":
        return [AGENTS["original"], AGENTS["opt"]]
    return [AGENTS[agent_arg]]


def main() -> int:
    args = parse_args()
    questions = load_questions(args.questions_file)
    if args.question_limit > 0:
        questions = questions[: args.question_limit]
    token, auth_mode = acquire_token(args)
    batch_id = str(uuid.uuid4())
    selected_agents = select_agents(args.agent)
    csv_rows: list[dict[str, Any]] = []
    json_rows: list[dict[str, Any]] = []

    print(f"Batch: {batch_id}")
    print(f"Auth mode: {auth_mode}")
    print(f"Questions: {len(questions)}")
    print(f"Environment: {ENVIRONMENT_ID}")
    print(f"Dataverse: {DATAVERSE_URL}")

    for agent in selected_agents:
        for run_index in range(1, args.runs + 1):
            for question_index, question in enumerate(questions, start=1):
                print(
                    f"[{agent.key}] run={run_index}/{args.runs} "
                    f"question={question_index}/{len(questions)}"
                )
                record: dict[str, Any] = {
                    "batch_id": batch_id,
                    "label": args.label,
                    "run_index": run_index,
                    "agent": agent.key,
                    "agent_display_name": agent.display_name,
                    "agent_schema_name": agent.schema_name,
                    "bot_id": agent.bot_id,
                    "question_index": question_index,
                    "question": question,
                    "auth_mode": auth_mode,
                    "started_at": utc_now_iso(),
                    "status": "ok",
                    "error": "",
                }
                json_record = dict(record)
                try:
                    conversation_id, start_activities, bootstrap_ms = start_conversation(
                        agent, token, args.timeout_seconds
                    )
                    turn_activities, first_ms, total_ms = ask_question(
                        agent, token, conversation_id, question, args.timeout_seconds
                    )
                    combined_activities = [
                        {"phase": "start", **item} for item in start_activities
                    ] + [{"phase": "turn", **item} for item in turn_activities]
                    activity_counts = summarize_activity_types(combined_activities)
                    record.update(
                        {
                            "conversation_id": conversation_id,
                            "conversation_bootstrap_ms": bootstrap_ms,
                            "time_to_first_response_activity_ms": first_ms,
                            "total_time_ms": total_ms,
                            "start_activity_count": len(start_activities),
                            "turn_activity_count": len(turn_activities),
                            "activity_type_counts": json.dumps(activity_counts, sort_keys=True),
                        }
                    )
                    json_record.update(record)
                    json_record["activities"] = combined_activities
                except Exception as exc:
                    message = str(exc)
                    record.update(
                        {
                            "conversation_id": "",
                            "conversation_bootstrap_ms": "",
                            "time_to_first_response_activity_ms": "",
                            "total_time_ms": "",
                            "start_activity_count": 0,
                            "turn_activity_count": 0,
                            "activity_type_counts": "{}",
                            "status": "error",
                            "error": message,
                        }
                    )
                    json_record.update(record)
                    json_record["activities"] = []
                csv_rows.append(record)
                json_rows.append(json_record)

    append_csv(args.results_csv, csv_rows)
    append_jsonl(args.results_jsonl, json_rows)
    print_summary(csv_rows)
    print("")
    print(f"CSV appended: {args.results_csv}")
    print(f"JSONL appended: {args.results_jsonl}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
