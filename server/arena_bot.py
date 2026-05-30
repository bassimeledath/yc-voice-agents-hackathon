"""Reusable Pipecat voice bot for arena-style agent conversations."""

from __future__ import annotations

import os
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

from arena_config import build_system_instruction, opening_user_message, scenario_role
from nemotron_llm import VLLMOpenAILLMService
from nvidia_stt import NVidiaWebSocketSTTService

load_dotenv(override=True)

StackName = Literal["gemini", "gpt", "nemotron"]


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _gradium_voice(default: str) -> str:
    return os.getenv("GRADIUM_VOICE_ID") or default


async def run_arena_bot(
    stack: StackName,
    transport: BaseTransport,
    audio_in_sample_rate: int = 16000,
    audio_out_sample_rate: int = 24000,
):
    scenario, role = scenario_role()
    logger.info(f"Starting arena bot stack={stack} scenario={scenario} role={role}")

    async def end_call(params: FunctionCallParams) -> None:
        """End the call after saying goodbye in the same turn."""
        logger.info("end_call invoked")
        await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
        await params.result_callback(
            {"ok": True}, properties=FunctionCallResultProperties(run_llm=False)
        )

    tools = ToolsSchema(standard_tools=[end_call])
    system_instruction = build_system_instruction()

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
                system_instruction=system_instruction,
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
                system_instruction=system_instruction,
                extra={
                    "extra_body": {"chat_template_kwargs": {"enable_thinking": enable_thinking}}
                },
            ),
        )
        tts_default_voice = "Eu9iL_CYe8N-Gkx_"
    else:
        raise ValueError(f"Unsupported arena stack: {stack}")

    tts = GradiumTTSService(
        api_key=os.environ["GRADIUM_API_KEY"],
        settings=GradiumTTSService.Settings(voice=_gradium_voice(tts_default_voice)),
    )

    llm.register_direct_function(end_call)

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

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")
        if _env_bool("ARENA_AUTO_START", True):
            context.add_message({"role": "user", "content": opening_user_message()})
            await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()


async def arena_entrypoint(stack: StackName, runner_args: RunnerArguments):
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

    await run_arena_bot(stack, transport, **transport_overrides)
