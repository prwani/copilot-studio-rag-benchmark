#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterable

from microsoft_agents.activity import Activity
from microsoft_agents.copilotstudio.client import ConnectionSettings, CopilotClient

import run as harness


SCRIPT_DIR = Path(__file__).resolve().parent
PLAN_EVENTS = ("DynamicPlanReceived", "DynamicPlanStepFinished")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the 22-query Copilot Studio SDK benchmark.")
    parser.add_argument("--agent", choices=[*harness.AGENTS, "all"], required=True)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--label", default=datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--questions-file", type=Path, default=SCRIPT_DIR / "questions_user.txt")
    parser.add_argument("--question-limit", type=int, default=0)
    parser.add_argument("--results-csv", type=Path, default=SCRIPT_DIR / "sdk_results.csv")
    parser.add_argument("--results-jsonl", type=Path, default=SCRIPT_DIR / "sdk_results.jsonl")
    parser.add_argument("--auth", choices=["auto", "az", "device-code", "env"], default="auto")
    parser.add_argument("--token-command", default=harness.DEFAULT_TOKEN_COMMAND)
    parser.add_argument("--client-id", default=harness.DEFAULT_PUBLIC_CLIENT_ID)
    parser.add_argument("--tenant-id", default=harness.TENANT_ID)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    if args.runs < 1 or args.timeout_seconds < 1 or args.question_limit < 0:
        parser.error("runs and timeout-seconds must be positive; question-limit must be nonnegative")
    return args


async def capture_activities(
    stream: AsyncIterable[Activity], phase: str, records: list[dict[str, Any]]
) -> float:
    started = time.perf_counter()
    async for activity in stream:
        arrival_ms = round((time.perf_counter() - started) * 1000, 3)
        raw = activity.model_dump(mode="json", by_alias=True, exclude_unset=True)
        records.append(
            {
                "phase": phase,
                "arrival_ms": arrival_ms,
                "sse_event": "activity",
                "raw_data": json.dumps(raw, ensure_ascii=False),
                "raw": raw,
                "activity_type": raw.get("type"),
                "activity_name": raw.get("name"),
                "activity_id": raw.get("id"),
                "text": raw.get("text"),
            }
        )
    return round((time.perf_counter() - started) * 1000, 3)


async def run_question(
    client: CopilotClient,
    question: str,
    timeout_seconds: int,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    activities: list[dict[str, Any]] = []
    record = {
        **metadata,
        "question": question,
        "started_at": harness.utc_now_iso(),
        "status": "ok",
        "error": "",
        "conversation_id": "",
        "conversation_bootstrap_ms": "",
        "time_to_first_response_activity_ms": "",
        "total_time_ms": "",
        "activities": activities,
    }
    try:
        record["conversation_bootstrap_ms"] = await asyncio.wait_for(
            capture_activities(client.start_conversation(), "start", activities),
            timeout=timeout_seconds,
        )
        conversation_id = harness.extract_conversation_id(activities, {})
        # The SDK stores header-only IDs privately; its public conversation_id is not updated.
        conversation_id = conversation_id or getattr(client, "_current_conversation_id", "")
        if not conversation_id:
            raise harness.HarnessError("Conversation started but no conversation id was returned.")
        record["conversation_id"] = conversation_id
        record["total_time_ms"] = await asyncio.wait_for(
            capture_activities(
                client.ask_question(question, conversation_id=conversation_id), "turn", activities
            ),
            timeout=timeout_seconds,
        )
        turn_activities = [item for item in activities if item["phase"] == "turn"]
        record["time_to_first_response_activity_ms"] = (
            turn_activities[0]["arrival_ms"] if turn_activities else None
        )
    except Exception as exc:
        record["status"] = "error"
        record["error"] = (
            f"SDK stream timed out after {timeout_seconds}s"
            if isinstance(exc, TimeoutError)
            else str(exc)
        )
    record["start_activity_count"] = sum(item["phase"] == "start" for item in activities)
    record["turn_activity_count"] = sum(item["phase"] == "turn" for item in activities)
    record["activity_type_counts"] = json.dumps(
        harness.summarize_activity_types(activities), sort_keys=True
    )
    return record


async def main() -> int:
    args = parse_args()
    questions = harness.load_questions(args.questions_file)
    if args.question_limit:
        questions = questions[: args.question_limit]
    token, auth_mode = harness.acquire_token(args)
    batch_id = str(uuid.uuid4())
    rows: list[dict[str, Any]] = []
    event_counts: Counter[str] = Counter()
    print(f"Batch: {batch_id}\nAuth mode: {auth_mode}\nQuestions: {len(questions)}")
    for agent in harness.select_agents(args.agent):
        settings = ConnectionSettings(
            environment_id=harness.ENVIRONMENT_ID, agent_identifier=agent.schema_name
        )
        for run_index in range(1, args.runs + 1):
            for question_index, question in enumerate(questions, start=1):
                print(
                    f"[{agent.key}] run={run_index}/{args.runs} "
                    f"question={question_index}/{len(questions)}",
                    flush=True,
                )
                metadata = {
                    "batch_id": batch_id,
                    "label": args.label,
                    "run_index": run_index,
                    "agent": agent.key,
                    "agent_display_name": agent.display_name,
                    "agent_schema_name": agent.schema_name,
                    "bot_id": agent.bot_id,
                    "question_index": question_index,
                    "auth_mode": auth_mode,
                }
                row = await run_question(
                    CopilotClient(settings, token), question, args.timeout_seconds, metadata
                )
                harness.append_jsonl(args.results_jsonl, [row])
                harness.append_csv(args.results_csv, [row])
                rows.append(row)
                event_counts.update(
                    item["activity_name"] for item in row["activities"]
                    if item["activity_name"] in PLAN_EVENTS
                )
    harness.print_summary(rows)
    for name in PLAN_EVENTS:
        print(f"{name}: {event_counts[name]} observed (absence does not mean unsupported)")
    print(f"CSV appended: {args.results_csv}\nJSONL appended: {args.results_jsonl}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except harness.HarnessError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
