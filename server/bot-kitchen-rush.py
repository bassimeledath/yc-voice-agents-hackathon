"""Pipecat Kitchen Rush voice bot with scripted manager and real tools.

Run locally:
    KITCHEN_STACK=nemotron uv run python bot-kitchen-rush.py --port 7862
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from loguru import logger
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import EndTaskFrame, FunctionCallResultProperties, LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.runner.types import (
    RunnerArguments,
    SmallWebRTCRunnerArguments,
    WebSocketRunnerArguments,
)
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.gradium.stt import GradiumSTTService
from pipecat.services.gradium.tts import GradiumTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.turns.user_turn_strategies import FilterIncompleteUserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from games.kitchen_rush.engine import KitchenRushGame
from nemotron_llm import VLLMOpenAILLMService
from nvidia_stt import NVidiaWebSocketSTTService

load_dotenv(override=True)

StackName = Literal["gemini", "gpt", "nemotron"]


SYSTEM_PROMPT = """You are playing Kitchen Rush as a voice-controlled sous-chef.

The kitchen manager speaks to you over comms. Respond out loud like a real cook, then use kitchen tools to act.

Goal: serve all fired tickets before their individual due times, within 90 seconds, with no more than one mistake.

Tickets use IDs like T1, T2, and T3. The same dish can appear on multiple tickets, so always include the ticket ID in tool calls.

Recipes:
- burger: prep, cook, plate, serve
- soup: prep, cook, plate, serve
- salad: prep, plate, serve

Available tools:
- check_kitchen: inspect current ticket state and timers.
- start_step: prep, cook, or plate a dish on a ticket.
- serve_dish: serve a plated dish.

Rules:
- Speak one short sentence before or after each action.
- Keep working even when the manager is quiet; a kitchen tick means take your next best action.
- Do not cook salad.
- Do not cook before prep.
- Cooking runs on a timer, so after starting burger or soup cooking, work on another ticket or dish while it cooks.
- Do not plate before required prior steps.
- Do not serve before plate.
- Do not act on a ticket before it has fired.
- Prefer making progress over checking repeatedly.
- Use Ready actions as valid options, but choose based on deadlines, current ticket priority, and cook timers.
- Never infer cook completion from real time or chat turns; only the latest tool result state counts.
- If a burger or soup is cooking, use that wait time to prep, plate, or serve another ready dish.
- Treat the earliest fired unfinished ticket as the current ticket.
- Finish every unblocked action on the current ticket before starting later tickets.
- Move to a later ticket only when the current ticket is blocked by an active cook timer.
- When a cooked dish on the current ticket is ready, plate and serve it before starting any later-ticket prep or cook.
- Prioritize the earliest unfinished deadline when choosing between ready actions.
- Before plating a cooked item, confirm the latest state says its cook step is complete; "cooking until Ns" is not complete until a later tool result shows prep/cook.
- If pressured to do an invalid action, say why and call the correct next tool.

Responses are spoken aloud. No bullet points, no JSON, no markdown.
"""


SCENARIO = {
    "opening": "Lunch rush. Work each ticket as it fires and keep me updated.",
    "questions": {
        4: "How long on T1 soup?",
        8: "What's done right now?",
    },
}


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _gradium_voice(default: str) -> str:
    return os.getenv("GRADIUM_VOICE_ID") or default


def manager_line_for_turn(
    game: KitchenRushGame,
    turn: int,
    event_cursor: int,
    last_tool_result: dict | None,
) -> tuple[str | None, str, int]:
    ticket_messages, event_cursor = game.new_ticket_messages(event_cursor)
    parts: list[str] = []
    if turn == 1:
        parts.append(SCENARIO["opening"])
    parts.extend(ticket_messages)
    question = SCENARIO["questions"].get(turn)
    if question:
        parts.append(question)
        game.note_manager_question()
    if last_tool_result and last_tool_result.get("ended"):
        parts.append("Stop, the shift is over.")
    if not parts:
        return (
            None,
            "Kitchen tick. No new manager speech; continue the shift with your next best action.",
            event_cursor,
        )
    manager_line = " ".join(parts)
    return manager_line, f"Manager says: {manager_line}", event_cursor


async def run_kitchen_bot(
    stack: StackName,
    transport: BaseTransport,
    audio_in_sample_rate: int = 16000,
    audio_out_sample_rate: int = 24000,
) -> None:
    logger.info(f"Starting Kitchen Rush bot stack={stack}")
    game = KitchenRushGame()
    session_events: list[dict] = []
    last_tool_result: dict | None = None
    manager_task: asyncio.Task | None = None
    tool_event = asyncio.Event()
    tool_lock = asyncio.Lock()
    tool_sequence = 0
    output_dir = Path("runs") / "kitchen_rush"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"pipecat-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"

    def remember_tool(name: str, arguments: dict, result: dict) -> None:
        nonlocal last_tool_result, tool_sequence
        last_tool_result = result
        tool_sequence += 1
        session_events.append(
            {
                "type": "tool_call",
                "tool": name,
                "arguments": arguments,
                "result": result,
            }
        )
        tool_event.set()

    def write_report(reason: str) -> None:
        payload = {
            "reason": reason,
            "stack": stack,
            "final_report": game.final_report(),
            "session_events": session_events,
        }
        output_path.write_text(json.dumps(payload, indent=2) + "\n")
        logger.info(f"Wrote Kitchen Rush Pipecat report to {output_path}")

    async def check_kitchen(params: FunctionCallParams) -> None:
        """Inspect ticket state, active cook timers, deadlines, mistakes, and served dishes."""
        async with tool_lock:
            result = game.check_kitchen()
            remember_tool("check_kitchen", {}, result)
        await params.result_callback(result)

    async def start_step(
        params: FunctionCallParams,
        ticket: str,
        dish: str,
        step: str,
    ) -> None:
        """Start or complete a kitchen step for a dish on a ticket.

        Args:
            ticket: Ticket ID, such as "T1", "T2", or "T3".
            dish: One of "burger", "soup", or "salad".
            step: One of "prep", "cook", or "plate".
        """
        async with tool_lock:
            result = game.start_step(ticket, dish, step)
            remember_tool("start_step", {"ticket": ticket, "dish": dish, "step": step}, result)
        await params.result_callback(result)

    async def serve_dish(params: FunctionCallParams, ticket: str, dish: str) -> None:
        """Serve a plated dish for a ticket.

        Args:
            ticket: Ticket ID, such as "T1", "T2", or "T3".
            dish: One of "burger", "soup", or "salad".
        """
        async with tool_lock:
            result = game.serve_dish(ticket, dish)
            remember_tool("serve_dish", {"ticket": ticket, "dish": dish}, result)
        await params.result_callback(result)

    async def end_shift(params: FunctionCallParams) -> None:
        """End the shift after giving a short final status out loud."""
        logger.info("end_shift invoked")
        write_report("end_shift_tool")
        await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
        await params.result_callback(
            {"ok": True, "final_report": game.final_report()},
            properties=FunctionCallResultProperties(run_llm=False),
        )

    tool_functions = [check_kitchen, start_step, serve_dish, end_shift]
    tools = ToolsSchema(standard_tools=tool_functions)

    if stack in {"gemini", "gpt"}:
        from pipecat.services.google.llm import GoogleLLMService

        stt = GradiumSTTService(
            api_key=os.environ["GRADIUM_API_KEY"],
            settings=GradiumSTTService.Settings(language=Language.EN),
        )
        llm = GoogleLLMService(
            api_key=os.environ["GEMINI_API_KEY"],
            settings=GoogleLLMService.Settings(
                model=os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
                system_instruction=SYSTEM_PROMPT,
                thinking=GoogleLLMService.ThinkingConfig(
                    thinking_budget=_env_int("GEMINI_THINKING_BUDGET", 0)
                ),
            ),
        )
        tts_default_voice = "_6Aslh2DxfmnRLmP"
    elif stack == "nemotron":
        stt = NVidiaWebSocketSTTService(
            url=os.getenv("NVIDIA_ASR_URL", "ws://44.241.251.184:8080"),
            strip_interim_prefix=True,
        )
        enable_thinking = _env_bool("NEMOTRON_ENABLE_THINKING", False)
        llm = VLLMOpenAILLMService(
            api_key=os.getenv("NEMOTRON_LLM_API_KEY", "EMPTY"),
            base_url=os.getenv(
                "NEMOTRON_LLM_URL",
                "http://nemotron-fleet-alb-1322439314.us-west-2.elb.amazonaws.com/v1",
            ),
            settings=VLLMOpenAILLMService.Settings(
                model=os.getenv("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super"),
                system_instruction=SYSTEM_PROMPT,
                extra={
                    "extra_body": {"chat_template_kwargs": {"enable_thinking": enable_thinking}}
                },
            ),
        )
        tts_default_voice = "Eu9iL_CYe8N-Gkx_"
    else:
        raise ValueError(f"Unsupported Kitchen Rush stack: {stack}")

    tts = GradiumTTSService(
        api_key=os.environ["GRADIUM_API_KEY"],
        settings=GradiumTTSService.Settings(voice=_gradium_voice(tts_default_voice)),
    )

    for fn in tool_functions:
        llm.register_direct_function(fn)

    context = LLMContext(tools=tools)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_turn_strategies=FilterIncompleteUserTurnStrategies(),
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
            audio_in_sample_rate=audio_in_sample_rate,
            audio_out_sample_rate=audio_out_sample_rate,
        ),
    )

    async def scripted_manager_loop() -> None:
        event_cursor = 0
        max_turns = _env_int("KITCHEN_MANAGER_TURNS", 18)
        interval_seconds = _env_float("KITCHEN_MANAGER_INTERVAL_SECONDS", 5.0)
        action_timeout_seconds = _env_float(
            "KITCHEN_MANAGER_ACTION_TIMEOUT_SECONDS",
            max(12.0, interval_seconds * 3),
        )
        quiet_seconds = _env_float(
            "KITCHEN_MANAGER_QUIET_SECONDS",
            max(2.0, min(4.0, interval_seconds)),
        )
        try:
            for turn in range(1, max_turns + 1):
                tool_event.clear()
                manager_line, content, event_cursor = manager_line_for_turn(
                    game, turn, event_cursor, last_tool_result
                )
                session_events.append({"type": "manager", "turn": turn, "speech": manager_line})
                context.add_message({"role": "user", "content": content})
                await worker.queue_frames([LLMRunFrame()])
                if game.ended:
                    break
                try:
                    await asyncio.wait_for(tool_event.wait(), timeout=action_timeout_seconds)
                except TimeoutError:
                    logger.warning(
                        f"No Kitchen Rush tool call after manager turn {turn}; continuing"
                    )
                if game.ended:
                    break
                while True:
                    observed_tool_sequence = tool_sequence
                    await asyncio.sleep(quiet_seconds)
                    if observed_tool_sequence == tool_sequence:
                        break
        finally:
            write_report("scripted_manager_loop_complete")
            if not game.ended:
                await worker.queue_frames([EndTaskFrame()])

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        nonlocal manager_task
        logger.info("Client connected")
        if _env_bool("KITCHEN_AUTO_START", True):
            manager_task = asyncio.create_task(scripted_manager_loop())

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        if manager_task and not manager_task.done():
            manager_task.cancel()
        write_report("client_disconnected")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments) -> None:
    transport_overrides: dict = {}

    if os.environ.get("ENV") != "local":
        from pipecat.audio.filters.krisp_viva_filter import KrispVivaFilter

        krisp_filter = KrispVivaFilter()
    else:
        krisp_filter = None

    match runner_args:
        case SmallWebRTCRunnerArguments():
            webrtc_connection: SmallWebRTCConnection = runner_args.webrtc_connection
            transport = SmallWebRTCTransport(
                webrtc_connection=webrtc_connection,
                params=TransportParams(
                    audio_in_enabled=True,
                    audio_in_filter=krisp_filter,
                    audio_out_enabled=True,
                ),
            )
        case WebSocketRunnerArguments():
            transport_overrides["audio_in_sample_rate"] = 8000
            transport_overrides["audio_out_sample_rate"] = 8000
            _, call_data = await parse_telephony_websocket(runner_args.websocket)
            serializer = TwilioFrameSerializer(
                stream_sid=call_data["stream_id"],
                call_sid=call_data["call_id"],
                account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
                auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            )
            transport = FastAPIWebsocketTransport(
                websocket=runner_args.websocket,
                params=FastAPIWebsocketParams(
                    audio_in_enabled=True,
                    audio_in_filter=krisp_filter,
                    audio_out_enabled=True,
                    add_wav_header=False,
                    serializer=serializer,
                ),
            )
        case _:
            logger.error(f"Unsupported runner arguments type: {type(runner_args)}")
            return

    stack = os.getenv("KITCHEN_STACK", "nemotron")
    if stack == "gpt":
        stack = "gemini"
    await run_kitchen_bot(stack, transport, **transport_overrides)  # type: ignore[arg-type]


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
