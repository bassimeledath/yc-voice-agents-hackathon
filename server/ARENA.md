# Voice Agent Arena

Reusable runtime lives in `core/`.

Game-specific prompts and defaults live in `games/<game>/`.

## Local setup

The local `.env` should contain:

```bash
GRADIUM_API_KEY=...
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-flash-latest
GEMINI_THINKING_BUDGET=0
NVIDIA_ASR_URL=ws://44.241.251.184:8080
NEMOTRON_LLM_URL=http://nemotron-fleet-alb-1322439314.us-west-2.elb.amazonaws.com/v1
NEMOTRON_LLM_MODEL=nvidia/nemotron-3-super
NEMOTRON_ENABLE_THINKING=false
```

## Run One Simulation

Fast text-only prompt iteration:

```bash
uv run python text_simulation.py \
  --game yc_interview \
  --candidate-stack nemotron \
  --candidate-variant base \
  --interviewer-stack nemotron \
  --time-limit-seconds 45
```

Voice-path simulation with synthetic TTS/STT:

```bash
uv run python synthetic_bridge.py \
  --game yc_interview \
  --candidate-stack nemotron \
  --interviewer-stack gemini \
  --interviewer-stt nvidia \
  --time-limit-seconds 60
```

Run the Gemini baseline by changing only the candidate stack:

```bash
uv run python synthetic_bridge.py \
  --game yc_interview \
  --candidate-stack gemini \
  --candidate-stt nvidia \
  --interviewer-stack gemini \
  --interviewer-stt nvidia \
  --time-limit-seconds 60
```

Outputs are written under `runs/<game>/`.

## Add A Game

Create:

```text
games/<game>/game.json
games/<game>/prompts/
```

The core bridge does not need to change for new games if the game keeps the same two-role shape: candidate agent plus interviewer/customer/opponent agent.

## Pipecat Browser Bots

The original single-bot entrypoints still work for manual WebRTC checks:

```bash
ARENA_SCENARIO=yc_interview ARENA_ROLE=founder uv run python bot-nemotron-arena.py --port 7861
ARENA_SCENARIO=yc_interview ARENA_ROLE=interviewer uv run python bot-gemini-arena.py --port 7860
```
