"""Custom WebSocket text agent for Cekura Kitchen Rush evaluator runs."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from core.llm import generate_reply
from core.types import AgentConfig, StackName
from games.kitchen_rush.engine import KitchenRushGame
from kitchen_rush_simulation import (
    SYSTEM_PROMPT,
    execute_tool,
    extract_json_object,
    normalize_action,
)

load_dotenv(override=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve Kitchen Rush as a Cekura text agent.")
    parser.add_argument("--candidate-stack", choices=["gemini", "gpt", "nemotron"], default="nemotron")
    parser.add_argument("--max-tokens", type=int, default=220)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--session-dir",
        type=Path,
        default=Path("runs") / "cekura_kitchen_sessions",
    )
    return parser.parse_args()


def header_value(websocket: WebSocket, name: str) -> str | None:
    return websocket.headers.get(name) or websocket.headers.get(name.lower())


def content_from_message(raw_message: str) -> tuple[str, str | None]:
    payload = json.loads(raw_message)
    if not isinstance(payload, dict):
        return "", None
    content = str(payload.get("content") or "")
    message_type = payload.get("type")
    return content, str(message_type) if message_type else None


def make_agent(stack: StackName) -> AgentConfig:
    return AgentConfig(
        name="kitchen-chef",
        stack=stack,
        game="kitchen_rush",
        role="candidate",
        stt="gradium",
        voice_id="",
        system_prompt=SYSTEM_PROMPT,
    )


def session_metadata(websocket: WebSocket, args: argparse.Namespace) -> dict[str, Any]:
    return {
        "game": "kitchen_rush",
        "candidate_stack": args.candidate_stack,
        "scenario_id": header_value(websocket, "X-VOCERA-SCENARIO-ID"),
        "result_id": header_value(websocket, "X-VOCERA-RESULT-ID"),
        "run_id": header_value(websocket, "X-VOCERA-RUN-ID"),
    }


def build_prompt(
    manager_text: str,
    ticket_messages: list[str],
    last_tool_result: dict[str, Any] | None,
) -> str:
    parts = []
    if manager_text:
        parts.append(f"Manager says: {manager_text}")
    else:
        parts.append("Kitchen tick.")
    parts.extend(ticket_messages)
    parts.append(f"Last tool result: {json.dumps(last_tool_result or {}, sort_keys=True)}")
    parts.append("Return your next spoken update and one tool call as JSON only.")
    return "\n".join(parts)


def render_response(
    speech: str,
    tool_name: str,
    arguments: dict[str, Any],
    tool_result: dict[str, Any],
    final_report: dict[str, Any] | None,
) -> str:
    final_summary = None
    if final_report:
        final_summary = {
            "won": final_report["won"],
            "score": final_report["score"],
            "elapsed_sec": final_report["elapsed_sec"],
            "loss_reason": final_report["loss_reason"],
            "missed_deadline_count": final_report["missed_deadline_count"],
            "mistake_count": final_report["mistake_count"],
            "unnecessary_tool_calls": final_report["unnecessary_tool_calls"],
            "served": final_report["served"],
        }
    lines = [
        speech,
        f"TOOL_CALL: {json.dumps({'name': tool_name, 'arguments': arguments}, sort_keys=True)}",
        f"TOOL_RESULT: {tool_result.get('message', '')}",
    ]
    if final_summary:
        lines.append(f"FINAL_REPORT: {json.dumps(final_summary, sort_keys=True)}")
    return "\n".join(line for line in lines if line)


async def handle_session(websocket: WebSocket, args: argparse.Namespace) -> None:
    await websocket.accept()

    expected_secret = os.getenv("CEKURA_WEBSOCKET_SECRET")
    actual_secret = header_value(websocket, "X-VOCERA-SECRET")
    if expected_secret and actual_secret != expected_secret:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    game = KitchenRushGame()
    agent = make_agent(args.candidate_stack)
    metadata = session_metadata(websocket, args)
    transcript: list[dict[str, Any]] = []
    started_at = datetime.now().isoformat(timespec="seconds")
    session_id = metadata.get("run_id") or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    event_cursor = 0
    last_tool_result: dict[str, Any] | None = None

    await websocket.send_text(json.dumps({"metadata": {**metadata, "started_at": started_at}}))
    await websocket.send_text(
        json.dumps(
            {
                "content": "Chef online. Ready for the rush.",
                "metadata": {**metadata, "started_at": started_at, "game_ended": False},
            }
        )
    )

    try:
        while True:
            raw_message = await websocket.receive_text()
            manager_text, message_type = content_from_message(raw_message)
            if message_type == "end_call":
                transcript.append(
                    {
                        "role": "testing_agent",
                        "content": manager_text,
                        "type": "end_call",
                    }
                )
                break
            if not manager_text:
                manager_text = "Kitchen tick."

            manager_question_pending = "?" in manager_text
            if manager_question_pending:
                game.note_manager_question()

            ticket_messages, event_cursor = game.new_ticket_messages(event_cursor)
            prompt = build_prompt(manager_text, ticket_messages, last_tool_result)
            transcript.append(
                {
                    "role": "testing_agent",
                    "content": manager_text,
                    "ticket_messages": ticket_messages,
                }
            )

            try:
                raw_reply, llm_ms = await generate_reply(agent, prompt, args.max_tokens)
                payload = extract_json_object(raw_reply)
                tool_name, arguments, speech = normalize_action(payload)
                game.record_voice(speech, manager_question_pending)
                tool_result = execute_tool(game, tool_name, arguments)
                parse_error = None
            except Exception as exc:
                raw_reply = locals().get("raw_reply", "")
                llm_ms = None
                speech = ""
                tool_name = "parse_error"
                arguments = {}
                tool_result = game.start_step("", "", "")
                parse_error = f"{type(exc).__name__}: {exc}"

            last_tool_result = tool_result
            final_report = game.final_report() if game.ended else None
            response_text = render_response(
                speech=speech,
                tool_name=tool_name,
                arguments=arguments,
                tool_result=tool_result,
                final_report=final_report,
            )
            turn = {
                "role": "candidate",
                "content": response_text,
                "speech": speech,
                "tool_call": {"name": tool_name, "arguments": arguments},
                "tool_result": tool_result,
                "raw_reply": raw_reply,
                "parse_error": parse_error,
                "metrics_ms": {"llm": llm_ms},
            }
            transcript.append(turn)
            outbound_message = {
                "content": response_text,
                "metadata": {
                    **metadata,
                    "llm_ms": llm_ms,
                    "turns": len(transcript),
                    "game_ended": game.ended,
                    "final_report": final_report,
                },
            }
            await websocket.send_text(json.dumps(outbound_message))
            if game.ended:
                await asyncio.sleep(0.75)
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "end_call",
                            "content": "Shift complete.",
                            "metadata": {
                                **metadata,
                                "game_ended": True,
                                "final_report": final_report,
                            },
                        }
                    )
                )
                break
    except WebSocketDisconnect:
        pass
    finally:
        args.session_dir.mkdir(parents=True, exist_ok=True)
        output = args.session_dir / f"{session_id}.json"
        output.write_text(
            json.dumps(
                {
                    "started_at": started_at,
                    "metadata": metadata,
                    "final_report": game.final_report(),
                    "transcript": transcript,
                },
                indent=2,
            )
            + "\n"
        )


def create_app(args: argparse.Namespace) -> FastAPI:
    app = FastAPI(title="Kitchen Rush Cekura Text Agent")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/cekura")
    async def cekura_socket(websocket: WebSocket) -> None:
        await handle_session(websocket, args)

    return app


def main() -> None:
    args = parse_args()
    app = create_app(args)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
