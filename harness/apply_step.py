#!/usr/bin/env python3
"""Apply a named latency-test step to the PWSharepointAgent-Opt clone only.

This script is intentionally scoped to a single clone bot:
  - Name: PWSharepointAgent-Opt
  - Bot ID: d6b522f1-a8bf-f111-aaaf-00224835685d

It can:
  - print current relevant settings (--status)
  - apply a single named step (--step NAME)
  - revert the latest applied step backup (--revert NAME)
  - reset the clone back to a stored snapshot of the original bot (--reset)

Safety rules:
  - it refuses to modify any bot other than the clone ID above
  - it always creates local backups before live modifications
  - --dry-run never writes to Dataverse; it prints the planned PATCH bodies
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - environment-specific
    raise SystemExit(
        "PyYAML is required to run this script (`python -c 'import yaml'`)."
    ) from exc


DATAVERSE_BASE = "https://orgfe6514f3.crm.dynamics.com/api/data/v9.2"
CLONE_BOT_ID = "d6b522f1-a8bf-f111-aaaf-00224835685d"
CLONE_BOT_NAME = "PWSharepointAgent-Opt"
ORIGINAL_BOT_ID = "2a1e8a0f-9eb8-f111-aaad-00224835685d"
ORIGINAL_BOT_NAME = "PWSharepointAgent"
DEFAULT_MODEL_MINI_HINT = os.environ.get("APPLY_STEP_MODEL_MINI_HINT", "GPT41Mini")
ALLOWED_MODERATION_LEVELS = {
    "lowest": "Lowest",
    "low": "Low",
    "medium": "Medium",
    "high": "High",
}
CONCISE_INSTRUCTIONS = (
    "Answer in 120 words or fewer. No preamble. Use only the provided knowledge "
    "sources. If the sources do not support the answer, say that you don't know."
)

SCRIPT_DIR = Path(__file__).resolve().parent
BACKUP_DIR = SCRIPT_DIR / "backups"
SNAPSHOT_PATH = BACKUP_DIR / "original_clone_snapshot.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def canonical_json_text(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def canonical_yaml_text(value: dict[str, Any]) -> str:
    return yaml.safe_dump(
        value,
        sort_keys=False,
        allow_unicode=True,
        width=1000,
        default_flow_style=False,
    )


def parse_json_text(text: str) -> dict[str, Any]:
    return json.loads(text)


def parse_yaml_text(text: str) -> dict[str, Any]:
    value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise ValueError("Expected YAML document root to be a mapping.")
    return value


def fatal(message: str, code: int = 2) -> "None":
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


class DataverseClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._token: str | None = None

    def token(self) -> str:
        if self._token is None:
            out = subprocess.check_output(
                [
                    "az",
                    "account",
                    "get-access-token",
                    "--resource",
                    "https://orgfe6514f3.crm.dynamics.com",
                    "-o",
                    "json",
                ],
                text=True,
            )
            self._token = json.loads(out)["accessToken"]
        return self._token

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        expect_json: bool = True,
    ) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.token()}",
            "Accept": "application/json",
            "OData-Version": "4.0",
            "OData-MaxVersion": "4.0",
        }
        if extra_headers:
            headers.update(extra_headers)
        data: bytes | None = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            fatal(f"{method.upper()} {url} failed: HTTP {exc.code}: {detail}", code=1)
        if not raw:
            return None
        text = raw.decode("utf-8", "replace")
        return json.loads(text) if expect_json else text

    def get_bot(self, bot_id: str) -> dict[str, Any]:
        params = urllib.parse.urlencode(
            {
                "$select": "botid,name,configuration,publishedon,modifiedon,createdon,statecode,statuscode",
                "$filter": f"botid eq {bot_id}",
            }
        )
        payload = self.request("GET", f"/bots?{params}")
        values = payload.get("value", [])
        if len(values) != 1:
            fatal(f"Expected exactly one bot for id {bot_id}, found {len(values)}")
        return values[0]

    def list_components(self, bot_id: str) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode(
            {
                "$select": (
                    "botcomponentid,name,schemaname,data,content,componenttype,"
                    "_parentbotid_value,modifiedon,createdon"
                ),
                "$filter": f"_parentbotid_value eq {bot_id}",
            }
        )
        payload = self.request("GET", f"/botcomponents?{params}")
        return payload.get("value", [])

    def patch_bot_configuration(self, bot_id: str, configuration_text: str) -> None:
        self._guard_target(bot_id)
        self.request(
            "PATCH",
            f"/bots({bot_id})",
            body={"configuration": configuration_text},
            extra_headers={"If-Match": "*"},
        )

    def patch_component_data(self, parent_bot_id: str, component_id: str, data_text: str) -> None:
        self._guard_target(parent_bot_id)
        self.request(
            "PATCH",
            f"/botcomponents({component_id})",
            body={"data": data_text},
            extra_headers={"If-Match": "*"},
        )

    def publish_bot(self, bot_id: str) -> dict[str, Any]:
        self._guard_target(bot_id)
        response = self.request(
            "POST",
            f"/bots({bot_id})/Microsoft.Dynamics.CRM.PvaPublish",
            body={},
        )
        return response or {}

    def wait_for_publish(
        self,
        bot_id: str,
        previous_publishedon: str | None,
        timeout_seconds: int,
        poll_interval_seconds: int,
    ) -> dict[str, Any]:
        self._guard_target(bot_id)
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            bot = self.get_bot(bot_id)
            current = bot.get("publishedon")
            if current and current != previous_publishedon:
                return bot
            time.sleep(poll_interval_seconds)
        fatal(
            f"Timed out waiting for publish completion after {timeout_seconds}s; "
            f"publishedon is still {previous_publishedon!r}.",
            code=1,
        )

    @staticmethod
    def _guard_target(bot_id: str) -> None:
        if bot_id != CLONE_BOT_ID:
            fatal(
                f"Refusing to modify bot {bot_id}. This script may only modify {CLONE_BOT_ID}."
            )


@dataclass
class BotState:
    bot: dict[str, Any]
    configuration_obj: dict[str, Any]
    configuration_text: str
    gpt_component: dict[str, Any]
    gpt_obj: dict[str, Any]
    gpt_text: str
    knowledge_component: dict[str, Any]
    knowledge_obj: dict[str, Any]
    knowledge_text: str
    all_components: list[dict[str, Any]]


def fetch_state(client: DataverseClient, bot_id: str, expected_name: str) -> BotState:
    bot = client.get_bot(bot_id)
    if bot.get("name") != expected_name:
        fatal(
            f"Bot {bot_id} name mismatch: expected {expected_name!r}, found {bot.get('name')!r}"
        )
    configuration_obj = parse_json_text(bot["configuration"])
    configuration_text = canonical_json_text(configuration_obj)

    components = client.list_components(bot_id)
    gpt_component = _find_gpt_component(components)
    knowledge_component = _find_sharepoint_component(components)

    gpt_source_text = gpt_component.get("data") or gpt_component.get("content") or ""
    knowledge_source_text = knowledge_component.get("data") or knowledge_component.get("content") or ""
    gpt_obj = parse_yaml_text(gpt_source_text)
    knowledge_obj = parse_yaml_text(knowledge_source_text)

    return BotState(
        bot=bot,
        configuration_obj=configuration_obj,
        configuration_text=configuration_text,
        gpt_component=gpt_component,
        gpt_obj=gpt_obj,
        gpt_text=canonical_yaml_text(gpt_obj),
        knowledge_component=knowledge_component,
        knowledge_obj=knowledge_obj,
        knowledge_text=canonical_yaml_text(knowledge_obj),
        all_components=components,
    )


def _find_gpt_component(components: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [c for c in components if c.get("componenttype") == 15]
    if len(matches) != 1:
        fatal(f"Expected exactly one GPT component, found {len(matches)}")
    return matches[0]


def _find_sharepoint_component(components: list[dict[str, Any]]) -> dict[str, Any]:
    matches = []
    for component in components:
        if component.get("componenttype") != 16:
            continue
        text = component.get("data") or component.get("content") or ""
        if "SharePointSearchSource" in text:
            matches.append(component)
    if len(matches) != 1:
        fatal(f"Expected exactly one SharePoint knowledge component, found {len(matches)}")
    return matches[0]


def snapshot_payload(state: BotState) -> dict[str, Any]:
    return {
        "bot": {
            "id": state.bot["botid"],
            "name": state.bot["name"],
            "publishedon": state.bot.get("publishedon"),
            "modifiedon": state.bot.get("modifiedon"),
            "configuration": state.configuration_text,
        },
        "gpt": {
            "component_id": state.gpt_component["botcomponentid"],
            "name": state.gpt_component.get("name"),
            "schemaname": state.gpt_component.get("schemaname"),
            "data": state.gpt_text,
        },
        "knowledge": {
            "component_id": state.knowledge_component["botcomponentid"],
            "name": state.knowledge_component.get("name"),
            "schemaname": state.knowledge_component.get("schemaname"),
            "data": state.knowledge_text,
        },
    }


def ensure_original_snapshot(
    client: DataverseClient,
    clone_state: BotState | None = None,
    original_state: BotState | None = None,
) -> dict[str, Any]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if SNAPSHOT_PATH.exists():
        return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    clone_state = clone_state or fetch_state(client, CLONE_BOT_ID, CLONE_BOT_NAME)
    original_state = original_state or fetch_state(client, ORIGINAL_BOT_ID, ORIGINAL_BOT_NAME)
    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "clone_reference": {"id": CLONE_BOT_ID, "name": CLONE_BOT_NAME},
        "original_reference": {"id": ORIGINAL_BOT_ID, "name": ORIGINAL_BOT_NAME},
        "clone_at_capture": snapshot_payload(clone_state),
        "reset_target": snapshot_payload(clone_state),
        "original_for_reset": snapshot_payload(original_state),
    }
    SNAPSHOT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def latest_backup_for_step(step_name: str) -> Path:
    if not BACKUP_DIR.exists():
        fatal(f"No backups directory exists at {BACKUP_DIR}")
    candidates = sorted(BACKUP_DIR.glob(f"*__apply__{step_name}.json"), reverse=True)
    if not candidates:
        fatal(f"No apply backup found for step {step_name!r} in {BACKUP_DIR}")
    return candidates[0]


def write_backup_file(step_name: str, clone_state: BotState, note: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"{utc_now()}__apply__{step_name}.json"
    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "step": step_name,
        "note": note,
        "clone_before": snapshot_payload(clone_state),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def build_status_payload(
    clone_state: BotState,
    original_state: BotState,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    clone_conf = clone_state.configuration_obj
    original_conf = original_state.configuration_obj
    clone_gpt = clone_state.gpt_obj
    original_gpt = original_state.gpt_obj
    clone_ks = clone_state.knowledge_obj
    original_ks = original_state.knowledge_obj

    clone_relevant = relevant_fields(clone_conf, clone_gpt, clone_ks)
    original_relevant = relevant_fields(original_conf, original_gpt, original_ks)
    relevant_diffs = {
        key: {"original": original_relevant[key], "clone": clone_relevant[key]}
        for key in original_relevant
        if original_relevant[key] != clone_relevant[key]
    }

    return {
        "clone": {
            "botid": clone_state.bot["botid"],
            "name": clone_state.bot["name"],
            "publishedon": clone_state.bot.get("publishedon"),
            "modifiedon": clone_state.bot.get("modifiedon"),
            "relevant": clone_relevant,
            "gpt_component": {
                "id": clone_state.gpt_component["botcomponentid"],
                "schemaname": clone_state.gpt_component.get("schemaname"),
            },
            "knowledge_component": {
                "id": clone_state.knowledge_component["botcomponentid"],
                "schemaname": clone_state.knowledge_component.get("schemaname"),
            },
        },
        "original": {
            "botid": original_state.bot["botid"],
            "name": original_state.bot["name"],
            "publishedon": original_state.bot.get("publishedon"),
            "modifiedon": original_state.bot.get("modifiedon"),
            "relevant": original_relevant,
        },
        "relevant_differences": relevant_diffs,
        "current_structural_differences": {
            "botid": {
                "original": ORIGINAL_BOT_ID,
                "clone": CLONE_BOT_ID,
            },
            "name": {
                "original": ORIGINAL_BOT_NAME,
                "clone": CLONE_BOT_NAME,
            },
            "gptSettings.defaultSchemaName": {
                "original": original_conf.get("gPTSettings", {}).get("defaultSchemaName"),
                "clone": clone_conf.get("gPTSettings", {}).get("defaultSchemaName"),
            },
            "gpt_component.schemaname": {
                "original": original_state.gpt_component.get("schemaname"),
                "clone": clone_state.gpt_component.get("schemaname"),
            },
            "knowledge_component.schemaname": {
                "original": original_state.knowledge_component.get("schemaname"),
                "clone": clone_state.knowledge_component.get("schemaname"),
            },
        },
        "snapshot_file": str(SNAPSHOT_PATH),
        "snapshot_captured_at": snapshot.get("captured_at"),
        "reset_strategy": (
            "Reset restores the stored clone-at-capture snapshot so clone-specific "
            "schema names remain intact."
        ),
        "model_mini_default_hint": DEFAULT_MODEL_MINI_HINT,
        "classic_orchestration_strategy": {
            "set_settings_GenerativeActionsEnabled": False,
            "recognizer_action": "remove recognizer object",
            "basis": (
                "Website Q&A / Voice sample bot configs in this environment omit "
                "the recognizer object entirely in classic-style configurations."
            ),
        },
    }


def relevant_fields(
    configuration_obj: dict[str, Any],
    gpt_obj: dict[str, Any],
    knowledge_obj: dict[str, Any],
) -> dict[str, Any]:
    return {
        "settings.GenerativeActionsEnabled": configuration_obj.get("settings", {}).get(
            "GenerativeActionsEnabled"
        ),
        "recognizer.$kind": (
            configuration_obj.get("recognizer", {}).get("$kind")
            if isinstance(configuration_obj.get("recognizer"), dict)
            else configuration_obj.get("recognizer")
        ),
        "aISettings.useModelKnowledge": configuration_obj.get("aISettings", {}).get(
            "useModelKnowledge"
        ),
        "aISettings.isSemanticSearchEnabled": configuration_obj.get("aISettings", {}).get(
            "isSemanticSearchEnabled"
        ),
        "aISettings.isFileAnalysisEnabled": configuration_obj.get("aISettings", {}).get(
            "isFileAnalysisEnabled"
        ),
        "aISettings.contentModeration": configuration_obj.get("aISettings", {}).get(
            "contentModeration"
        ),
        "aISettings.optInUseLatestModels": configuration_obj.get("aISettings", {}).get(
            "optInUseLatestModels"
        ),
        "gpt.instructions": gpt_obj.get("instructions"),
        "gpt.aISettings.model.modelNameHint": (
            ((gpt_obj.get("aISettings") or {}).get("model") or {}).get("modelNameHint")
        ),
        "knowledge.source.kind": (knowledge_obj.get("source") or {}).get("kind"),
        "knowledge.source.site": (knowledge_obj.get("source") or {}).get("site"),
    }


def normalize_moderation_step(step_name: str) -> str | None:
    if not step_name.startswith("moderation_"):
        return None
    suffix = step_name[len("moderation_") :].strip().lower().replace("-", "_")
    return ALLOWED_MODERATION_LEVELS.get(suffix)


def validate_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        fatal("--url must be a valid https:// URL for scope_narrow.")
    return url


def plan_step(
    clone_state: BotState,
    step_name: str,
    args: argparse.Namespace,
) -> tuple[str, str, str]:
    config_obj = copy.deepcopy(clone_state.configuration_obj)
    gpt_obj = copy.deepcopy(clone_state.gpt_obj)
    knowledge_obj = copy.deepcopy(clone_state.knowledge_obj)
    note = ""

    if step_name == "semantic_search_off":
        config_obj.setdefault("aISettings", {})["isSemanticSearchEnabled"] = False
        note = "Disable tenant graph grounding / semantic search."
    elif step_name == "model_mini":
        gpt_obj.setdefault("aISettings", {}).setdefault("model", {})[
            "modelNameHint"
        ] = args.model_name_hint
        note = f"Switch GPT component modelNameHint to {args.model_name_hint}."
    elif step_name == "concise_instructions":
        gpt_obj["instructions"] = CONCISE_INSTRUCTIONS
        note = "Replace instructions with concise grounded-answer guidance."
    elif step_name == "file_analysis_off":
        config_obj.setdefault("aISettings", {})["isFileAnalysisEnabled"] = False
        note = "Disable file analysis / code interpreter."
    elif step_name == "model_knowledge_off":
        config_obj.setdefault("aISettings", {})["useModelKnowledge"] = False
        note = "Disable model knowledge / ungrounded responses."
    elif step_name == "scope_narrow":
        if not args.url:
            fatal("--step scope_narrow requires --url.")
        knowledge_obj.setdefault("source", {})["site"] = validate_url(args.url)
        note = f"Narrow SharePoint site URL to {args.url}."
    elif step_name == "classic_orchestration":
        config_obj.setdefault("settings", {})["GenerativeActionsEnabled"] = False
        config_obj.pop("recognizer", None)
        note = (
            "Set settings.GenerativeActionsEnabled=false and remove the recognizer object "
            "to mimic the classic-style configs found in this environment."
        )
    else:
        moderation_level = normalize_moderation_step(step_name)
        if moderation_level:
            config_obj.setdefault("aISettings", {})["contentModeration"] = moderation_level
            note = f"Set content moderation to {moderation_level}."
        else:
            fatal(
                f"Unsupported step {step_name!r}. "
                "Supported: semantic_search_off, model_mini, concise_instructions, "
                "file_analysis_off, model_knowledge_off, moderation_<level>, "
                "scope_narrow, classic_orchestration."
            )

    return (
        canonical_json_text(config_obj),
        canonical_yaml_text(gpt_obj),
        canonical_yaml_text(knowledge_obj),
    )


def plan_restore_from_snapshot(snapshot_section: dict[str, Any]) -> tuple[str, str, str]:
    return (
        snapshot_section["bot"]["configuration"],
        snapshot_section["gpt"]["data"],
        snapshot_section["knowledge"]["data"],
    )


def build_patch_operations(
    clone_state: BotState,
    target_configuration_text: str,
    target_gpt_text: str,
    target_knowledge_text: str,
) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    if clone_state.configuration_text != target_configuration_text:
        operations.append(
            {
                "kind": "bot",
                "entity": "bots",
                "id": clone_state.bot["botid"],
                "body": {"configuration": target_configuration_text},
            }
        )
    if clone_state.gpt_text != target_gpt_text:
        operations.append(
            {
                "kind": "gpt",
                "entity": "botcomponents",
                "id": clone_state.gpt_component["botcomponentid"],
                "body": {"data": target_gpt_text},
            }
        )
    if clone_state.knowledge_text != target_knowledge_text:
        operations.append(
            {
                "kind": "knowledge",
                "entity": "botcomponents",
                "id": clone_state.knowledge_component["botcomponentid"],
                "body": {"data": target_knowledge_text},
            }
        )
    return operations


def print_status(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2))


def print_dry_run(label: str, operations: list[dict[str, Any]]) -> None:
    print(f"# {label}")
    if not operations:
        print("No changes required.")
        return
    for op in operations:
        print(
            json.dumps(
                {
                    "method": "PATCH",
                    "path": f"/{op['entity']}({op['id']})",
                    "body": op["body"],
                },
                indent=2,
            )
        )
    print(
        json.dumps(
            {
                "method": "POST",
                "path": f"/bots({CLONE_BOT_ID})/Microsoft.Dynamics.CRM.PvaPublish",
                "body": {},
                "note": "Publish is not executed during --dry-run.",
            },
            indent=2,
        )
    )


def execute_operations(
    client: DataverseClient,
    clone_state: BotState,
    operations: list[dict[str, Any]],
    timeout_seconds: int,
    poll_interval_seconds: int,
) -> dict[str, Any]:
    if not operations:
        print("No changes required; skipping publish.")
        return clone_state.bot

    previous_publishedon = clone_state.bot.get("publishedon")
    for op in operations:
        if op["kind"] == "bot":
            client.patch_bot_configuration(CLONE_BOT_ID, op["body"]["configuration"])
        elif op["kind"] in {"gpt", "knowledge"}:
            client.patch_component_data(CLONE_BOT_ID, op["id"], op["body"]["data"])
        else:  # pragma: no cover - defensive
            fatal(f"Unknown operation kind {op['kind']!r}")

    publish_response = client.publish_bot(CLONE_BOT_ID)
    print(json.dumps({"publish_response": publish_response}, indent=2))
    bot = client.wait_for_publish(
        CLONE_BOT_ID,
        previous_publishedon=previous_publishedon,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    print(
        json.dumps(
            {
                "publish_completed": True,
                "botid": bot["botid"],
                "publishedon": bot.get("publishedon"),
                "modifiedon": bot.get("modifiedon"),
            },
            indent=2,
        )
    )
    return bot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--status", action="store_true", help="Print current relevant settings.")
    action.add_argument("--step", help="Apply a named step to the clone bot.")
    action.add_argument("--revert", help="Revert the latest backup captured for a step.")
    action.add_argument("--reset", action="store_true", help="Reset clone to the stored original snapshot.")
    parser.add_argument(
        "--url",
        help="SharePoint URL to use with --step scope_narrow.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the PATCH bodies instead of modifying Dataverse.",
    )
    parser.add_argument(
        "--model-name-hint",
        default=DEFAULT_MODEL_MINI_HINT,
        help=(
            "Model hint to use for model_mini. Default: "
            f"{DEFAULT_MODEL_MINI_HINT!r}. This value was not verified in this environment."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Publish wait timeout in seconds for live runs (default: 600).",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=5,
        help="Publish polling interval in seconds for live runs (default: 5).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    client = DataverseClient(DATAVERSE_BASE)
    clone_state = fetch_state(client, CLONE_BOT_ID, CLONE_BOT_NAME)
    original_state = fetch_state(client, ORIGINAL_BOT_ID, ORIGINAL_BOT_NAME)
    snapshot = ensure_original_snapshot(
        client, clone_state=clone_state, original_state=original_state
    )

    if args.status:
        print_status(build_status_payload(clone_state, original_state, snapshot))
        return

    if args.step:
        target_configuration_text, target_gpt_text, target_knowledge_text = plan_step(
            clone_state, args.step, args
        )
        operations = build_patch_operations(
            clone_state,
            target_configuration_text,
            target_gpt_text,
            target_knowledge_text,
        )
        if args.dry_run:
            print_dry_run(f"Dry run for step {args.step}", operations)
            return
        backup_path = write_backup_file(args.step, clone_state, note=f"apply:{args.step}")
        print(json.dumps({"backup_file": str(backup_path)}, indent=2))
        execute_operations(
            client,
            clone_state,
            operations,
            timeout_seconds=args.timeout,
            poll_interval_seconds=args.poll_interval,
        )
        return

    if args.revert:
        backup_file = latest_backup_for_step(args.revert)
        backup_payload = json.loads(backup_file.read_text(encoding="utf-8"))
        target_configuration_text, target_gpt_text, target_knowledge_text = plan_restore_from_snapshot(
            backup_payload["clone_before"]
        )
        operations = build_patch_operations(
            clone_state,
            target_configuration_text,
            target_gpt_text,
            target_knowledge_text,
        )
        if args.dry_run:
            print(json.dumps({"backup_file": str(backup_file)}, indent=2))
            print_dry_run(f"Dry run for revert {args.revert}", operations)
            return
        write_backup_file(f"revert-{args.revert}", clone_state, note=f"revert:{args.revert}")
        execute_operations(
            client,
            clone_state,
            operations,
            timeout_seconds=args.timeout,
            poll_interval_seconds=args.poll_interval,
        )
        return

    if args.reset:
        target_configuration_text, target_gpt_text, target_knowledge_text = plan_restore_from_snapshot(
            snapshot.get("reset_target") or snapshot["clone_at_capture"]
        )
        operations = build_patch_operations(
            clone_state,
            target_configuration_text,
            target_gpt_text,
            target_knowledge_text,
        )
        if args.dry_run:
            print(json.dumps({"snapshot_file": str(SNAPSHOT_PATH)}, indent=2))
            print_dry_run("Dry run for reset", operations)
            return
        write_backup_file("reset", clone_state, note="reset")
        execute_operations(
            client,
            clone_state,
            operations,
            timeout_seconds=args.timeout,
            poll_interval_seconds=args.poll_interval,
        )
        return

    fatal("No action selected.")


if __name__ == "__main__":
    main()
