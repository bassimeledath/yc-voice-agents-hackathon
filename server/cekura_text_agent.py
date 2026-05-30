"""Custom WebSocket text agent for Cekura chat-based evaluator runs."""

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

from core.game_config import build_agent
from core.llm import generate_reply
from core.startups import format_startup_profile, validate_profile

load_dotenv(override=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the candidate as a Cekura text agent.")
    parser.add_argument("--game", default="yc_interview")
    parser.add_argument("--candidate-stack", choices=["gemini", "gpt", "nemotron"], default="nemotron")
    parser.add_argument("--candidate-prompt-file", type=Path, required=True)
    parser.add_argument("--profile-file", type=Path, required=True)
    parser.add_argument("--default-profile-index", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=130)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--session-dir", type=Path, default=Path("runs") / "cekura_text_sessions")
    return parser.parse_args()


def header_value(websocket: WebSocket, name: str) -> str | None:
    return websocket.headers.get(name) or websocket.headers.get(name.lower())


def load_profiles(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        data = data.get("profiles", [])
    if not isinstance(data, list):
        raise ValueError("Profile file must contain a list or an object with a profiles list")
    return [validate_profile(item) for item in data]


def load_profile_for_connection(args: argparse.Namespace, websocket: WebSocket) -> dict[str, Any]:
    raw_profile = header_value(websocket, "X-Startup-Profile")
    if raw_profile:
        return validate_profile(json.loads(raw_profile))

    profiles = load_profiles(args.profile_file)
    raw_index = header_value(websocket, "X-Startup-Profile-Index")
    index = int(raw_index) if raw_index else args.default_profile_index
    if index < 1 or index > len(profiles):
        raise ValueError(f"Profile index {index} is outside 1..{len(profiles)}")
    return profiles[index - 1]


def content_from_message(raw_message: str) -> tuple[str, str | None]:
    payload = json.loads(raw_message)
    if not isinstance(payload, dict):
        return "", None
    content = str(payload.get("content") or "")
    message_type = payload.get("type")
    return content, str(message_type) if message_type else None


async def send_metadata(websocket: WebSocket, metadata: dict[str, Any]) -> None:
    await websocket.send_text(json.dumps({"metadata": metadata}))


def make_agent(args: argparse.Namespace, profile: dict[str, Any]):
    prompt_text = args.candidate_prompt_file.read_text().strip()
    return build_agent(
        game=args.game,
        side="candidate",
        name="candidate",
        stack=args.candidate_stack,
        prompt_text_override=prompt_text,
        runtime_context_title="startup profile",
        runtime_context=format_startup_profile(profile),
    )


def session_metadata(websocket: WebSocket, profile: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    return {
        "game": args.game,
        "candidate_stack": args.candidate_stack,
        "company": profile["company"],
        "scenario_id": header_value(websocket, "X-VOCERA-SCENARIO-ID"),
        "result_id": header_value(websocket, "X-VOCERA-RESULT-ID"),
        "run_id": header_value(websocket, "X-VOCERA-RUN-ID"),
    }


async def handle_session(websocket: WebSocket, args: argparse.Namespace) -> None:
    await websocket.accept()

    expected_secret = os.getenv("CEKURA_WEBSOCKET_SECRET")
    actual_secret = header_value(websocket, "X-VOCERA-SECRET")
    if expected_secret and actual_secret != expected_secret:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    profile = load_profile_for_connection(args, websocket)
    agent = make_agent(args, profile)
    metadata = session_metadata(websocket, profile, args)
    transcript: list[dict[str, Any]] = []
    started_at = datetime.now().isoformat(timespec="seconds")
    session_id = metadata.get("run_id") or datetime.now().strftime("%Y%m%d-%H%M%S-%f")

    await send_metadata(websocket, {**metadata, "started_at": started_at})

    try:
        while True:
            raw_message = await websocket.receive_text()
            user_text, message_type = content_from_message(raw_message)
            if message_type == "end_call":
                transcript.append({"role": "testing_agent", "content": user_text, "type": "end_call"})
                break
            if not user_text:
                continue

            transcript.append({"role": "testing_agent", "content": user_text})
            reply, llm_ms = await generate_reply(agent, user_text, args.max_tokens)
            transcript.append({"role": "candidate", "content": reply, "metrics_ms": {"llm": llm_ms}})
            await websocket.send_text(
                json.dumps(
                    {
                        "content": reply,
                        "metadata": {**metadata, "llm_ms": llm_ms, "turns": len(transcript)},
                    }
                )
            )
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
                    "startup_profile": profile,
                    "transcript": transcript,
                },
                indent=2,
            )
            + "\n"
        )


def create_app(args: argparse.Namespace) -> FastAPI:
    app = FastAPI(title="Hotseat Cekura Text Agent")

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
