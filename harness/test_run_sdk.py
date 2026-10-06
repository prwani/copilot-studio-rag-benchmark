import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from microsoft_agents.activity import Activity
from microsoft_agents.copilotstudio.client import ConnectionSettings, CopilotClient

import run_sdk


async def stream(items):
    for item in items:
        yield Activity.model_validate(item)


class FakeClient:
    def __init__(self, *, header_only=False, fail=False):
        self._current_conversation_id = "conversation-1" if header_only else ""
        self.header_only = header_only
        self.fail = fail
        self.questions = []

    async def start_conversation(self):
        if not self.header_only:
            async for activity in stream([
                {"type": "event", "name": "start", "conversation": {"id": "conversation-1"}}
            ]):
                yield activity

    async def ask_question(self, question, conversation_id=None):
        self.questions.append((question, conversation_id))
        async for activity in stream([
            {"type": "event", "name": "DynamicPlanReceived", "value": {"steps": [1]}},
            {"type": "event", "name": "DynamicPlanStepFinished",
             "value": {"observation": {"search_result": ["document"]}}},
            {"type": "message", "text": "Answer", "conversation": {"id": conversation_id},
             "channelData": {"citations": ["source"]}},
        ]):
            yield activity
        if self.fail:
            raise RuntimeError("Stream failed")


class SDKTests(unittest.IsolatedAsyncioTestCase):
    async def test_arrival_clock_and_serialization(self):
        records = []
        with patch.object(run_sdk.time, "perf_counter", side_effect=[0, .010, .025, .030]):
            total = await run_sdk.capture_activities(stream([
                {"type": "event", "name": "DynamicPlanReceived", "value": {"steps": [1]}},
                {"type": "message", "text": "Answer", "channelData": {"citations": [1]}},
            ]), "turn", records)
        self.assertEqual(total, 30)
        self.assertEqual([r["arrival_ms"] for r in records], [10, 25])
        self.assertEqual(records[0]["raw"]["value"], {"steps": [1]})
        self.assertEqual(records[1]["raw"]["channelData"], {"citations": [1]})
        for record in records:
            self.assertEqual(json.loads(record["raw_data"]), record["raw"])
            self.assertEqual(record["sse_event"], "activity")
            self.assertEqual(record["phase"], "turn")

    async def test_question_metrics_and_event_trace(self):
        client = FakeClient()
        row = await run_sdk.run_question(client, "Query", 1, {"label": "test"})
        self.assertEqual(row["status"], "ok")
        self.assertEqual(client.questions, [("Query", "conversation-1")])
        self.assertEqual(row["start_activity_count"], 1)
        self.assertEqual(row["turn_activity_count"], 3)
        self.assertEqual(json.loads(row["activity_type_counts"]), {"event": 3, "message": 1})
        self.assertEqual(row["time_to_first_response_activity_ms"], row["activities"][1]["arrival_ms"])
        self.assertGreaterEqual(row["total_time_ms"], row["activities"][-1]["arrival_ms"])

    async def test_header_only_conversation(self):
        row = await run_sdk.run_question(FakeClient(header_only=True), "Query", 1, {})
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["conversation_id"], "conversation-1")
        self.assertEqual(row["start_activity_count"], 0)

    async def test_failed_turn_keeps_partial_trace(self):
        row = await run_sdk.run_question(FakeClient(fail=True), "Query", 1, {})
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["error"], "Stream failed")
        self.assertEqual(row["conversation_id"], "conversation-1")
        self.assertEqual(len(row["activities"]), 4)

    async def test_missing_conversation_is_error(self):
        client = FakeClient(header_only=True)
        client._current_conversation_id = ""
        row = await run_sdk.run_question(client, "Query", 1, {})
        self.assertEqual(row["status"], "error")
        self.assertIn("no conversation id", row["error"])
        self.assertEqual(client.questions, [])

    async def test_timeout_keeps_arrived_activities(self):
        closed = []

        class SlowClient(FakeClient):
            async def ask_question(self, question, conversation_id=None):
                try:
                    yield Activity(type="event", name="DynamicPlanReceived")
                    await asyncio.sleep(10)
                finally:
                    closed.append(True)

        row = await run_sdk.run_question(SlowClient(), "Query", .01, {})
        self.assertEqual(row["status"], "error")
        self.assertIn("timed out", row["error"])
        self.assertEqual(row["turn_activity_count"], 1)
        self.assertEqual(closed, [True])

    async def test_sdk_yields_dynamic_plan_activities(self):
        async def lines():
            for name in run_sdk.PLAN_EVENTS:
                yield b"event: activity\n"
                yield ("data: " + json.dumps({
                    "type": "event", "name": name, "value": {"payload": [1]},
                }) + "\n").encode()
                yield b"\n"
            yield b"event: DynamicPlanReceived\n"
            yield b'data: {"not": "an activity"}\n'

        response = MagicMock(status=200, headers={})
        response.content = lines()
        request = MagicMock()
        request.__aenter__.return_value = response
        session = MagicMock()
        session.post.return_value = request
        session_context = MagicMock()
        session_context.__aenter__.return_value = session
        client = CopilotClient(ConnectionSettings("environment", "agent"), "unused")
        with patch(
            "microsoft_agents.copilotstudio.client.copilot_client.aiohttp.ClientSession",
            return_value=session_context,
        ):
            activities = [item async for item in client.post_request("https://example.test", {}, {})]
        self.assertEqual([item.name for item in activities], list(run_sdk.PLAN_EVENTS))
        self.assertTrue(all(item.value == {"payload": [1]} for item in activities))

    async def test_full_benchmark_jsonl_schema_and_fresh_conversations(self):
        clients = []

        def make_client(settings, token):
            self.assertEqual(settings.agent_identifier, run_sdk.harness.AGENTS["original"].schema_name)
            client = FakeClient()
            clients.append(client)
            return client

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results.jsonl"
            csv_output = Path(directory) / "results.csv"
            with (
                patch("sys.argv", ["run_sdk.py", "--agent", "original", "--runs", "2",
                                  "--results-jsonl", str(output), "--results-csv", str(csv_output)]),
                patch.object(run_sdk.harness, "acquire_token", return_value=("unused", "env")),
                patch.object(run_sdk, "CopilotClient", side_effect=make_client),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(await run_sdk.main(), 0)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            questions = run_sdk.harness.load_questions(run_sdk.SCRIPT_DIR / "questions_user.txt")
            self.assertEqual(len(questions), 22)
            self.assertEqual(len(rows), 44)
            self.assertEqual([r["question"] for r in rows], questions * 2)
            self.assertEqual(len(clients), 44)
            self.assertTrue(all(len(c.questions) == 1 for c in clients))
            expected_keys = {
                "batch_id", "label", "run_index", "agent", "agent_display_name",
                "agent_schema_name", "bot_id", "question_index", "question", "auth_mode",
                "started_at", "status", "error", "conversation_id", "conversation_bootstrap_ms",
                "time_to_first_response_activity_ms", "total_time_ms", "start_activity_count",
                "turn_activity_count", "activity_type_counts", "activities",
            }
            self.assertTrue(all(set(row) == expected_keys for row in rows))
            self.assertEqual([r["question_index"] for r in rows], list(range(1, 23)) * 2)
            self.assertEqual([r["run_index"] for r in rows], [1] * 22 + [2] * 22)
            self.assertEqual(len(csv_output.read_text().splitlines()), 45)


if __name__ == "__main__":
    unittest.main()
