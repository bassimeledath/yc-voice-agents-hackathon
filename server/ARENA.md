# Voice Agent Arena

This is the thin reusable layer for two-party voice-agent experiments.

The current goal is only the speaking agent interface:

- same Pipecat transport surface as the starter repo
- GPT and NVIDIA/Nemotron variants
- scenario and role prompts that can be swapped without changing the pipeline
- no judge or scoring logic in the bot prompt

## Local setup

The local `.env` should contain:

```bash
GRADIUM_API_KEY=...
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1
NVIDIA_ASR_URL=ws://44.241.251.184:8080
NEMOTRON_LLM_URL=http://nemotron-fleet-alb-1322439314.us-west-2.elb.amazonaws.com/v1
NEMOTRON_LLM_MODEL=nvidia/nemotron-3-super
NEMOTRON_ENABLE_THINKING=false
```

## Run one bot locally

```bash
uv run python bot-gpt-arena.py --port 7860
uv run python bot-nemotron-arena.py --port 7861
```

Open the printed local URL in a browser and connect with mic/speaker.

## Swap scenario or role

```bash
ARENA_SCENARIO=yc_interview ARENA_ROLE=founder uv run python bot-nemotron-arena.py --port 7861
ARENA_SCENARIO=yc_interview ARENA_ROLE=interviewer uv run python bot-gpt-arena.py --port 7860
ARENA_SCENARIO=sales ARENA_ROLE=seller uv run python bot-nemotron-arena.py --port 7861
ARENA_SCENARIO=sales ARENA_ROLE=buyer uv run python bot-gpt-arena.py --port 7860
```

For a future two-bot harness, set `ARENA_AUTO_START=false` on the bot that should
listen first.

## Run the synthetic two-agent bridge

This avoids WebRTC while preserving the important voice loop:

```bash
uv run python synthetic_bridge.py --turns 6
```

Default pairing:

- left: `nemotron` as `yc_interview/founder`, listening through NVIDIA ASR
- right: `gpt` as `yc_interview/interviewer`, listening through Gradium STT

Sales example:

```bash
uv run python synthetic_bridge.py \
  --left-stack nemotron --left-scenario sales --left-role seller \
  --right-stack gpt --right-scenario sales --right-role buyer \
  --turns 8
```

Outputs are written under `runs/synthetic-bridge/`.
