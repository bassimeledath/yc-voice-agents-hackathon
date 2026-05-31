# Kitchen Rush Voice Agent Benchmark

## 1. What is this?

- Kitchen Rush is a voice-agent game and benchmark inspired by **Overcooked**.
- A kitchen manager gives live or scripted spoken orders like “start the soup,” “what’s ready?”, or “serve the burger.”
- The agent speaks back, calls kitchen tools, and tries to complete tickets before deadlines.
- The game tests production-relevant voice-agent behavior:
  - reliable tool calling
  - state tracking across turns
  - acting under time pressure
  - handling interruptions
  - improving from evaluation failures
- The demo compares a baseline Nemotron agent with a Cekura-tuned Nemotron agent on the same Kitchen Rush scenario.

## 2. Video

- Demo video: **TODO: add video link**
- The video is under 60 seconds and shows:
  - the baseline agent making invalid kitchen actions and missing a deadline
  - the Cekura-tuned agent performing better on the same scenario
  - the visual game replay with spoken manager/chef audio
  - live human-manager mode, where a person can speak orders and the agent speaks back while calling tools

## 3. How we used Cekura, Nemotron, and Pipecat

### Cekura

- We used Cekura as the evaluation and self-improvement loop for the Kitchen Rush agent.
- The goal was to turn agent quality into repeatable measurements instead of subjective demo impressions.
- We ran Kitchen Rush scenarios through a Cekura-compatible evaluation harness and inspected:
  - transcript behavior
  - tool-call correctness
  - missed deadlines
  - invalid actions
  - final game score
- We tuned the Nemotron agent prompt through several Cekura-driven iterations:
  - run scenarios
  - inspect failures
  - identify repeated behavioral patterns
  - update the prompt
  - rerun the same scenarios to check improvement and regressions
- On the demo scenario:
  - baseline score: `14`, with `3` mistakes and `1` missed deadline
  - tuned score: `47`, with `0` mistakes and `0` missed deadlines
- The tuned agent improved clearly, but it still has room to get better, which makes the benchmark useful beyond the demo.

### Nemotron

- We used NVIDIA Nemotron as the main open-model agent stack.
- Nemotron handled the agent reasoning and tool selection for Kitchen Rush.
- The agent had to convert natural manager requests into structured tool calls like:
  - `check_kitchen`
  - `start_step`
  - `serve_dish`
- We also used NVIDIA’s provided ASR/Nemotron stack in the Pipecat voice path.

### Pipecat

- We used Pipecat for the production-style voice-agent path.
- The Pipecat Kitchen Rush bot is in `server/bot-kitchen-rush.py`.
- It connects:
  - live voice input
  - STT
  - Nemotron/Gemini LLM
  - kitchen tool calls
  - TTS voice output
  - WebRTC transport
- The simulation harness uses the same Kitchen Rush game engine for fast evaluation, while Pipecat provides the live voice-agent experience.

## 4. What was built during the hackathon?

- Everything in this project was built during the hackathon.
- Built the Kitchen Rush game engine:
  - tickets
  - recipes
  - deadlines
  - burners
  - cooking timers
  - scoring
- Built the kitchen tool API:
  - `check_kitchen`
  - `start_step`
  - `serve_dish`
- Built baseline and tuned Nemotron agent prompts.
- Built the batch simulation/evaluation harness.
- Built the Cekura-compatible agent/evaluation workflow.
- Built the visual replay UI for Kitchen Rush runs.
- Added narrated replay with manager and chef audio.
- Added live human-manager mode so a person can speak orders and watch the agent respond with voice and tool calls.
- Added a Pipecat voice bot path for live voice-agent testing.

## 5. Tool feedback

### NVIDIA / Nemotron feedback

- What worked well:
  - Nemotron was fast enough for a latency-sensitive voice-agent game.
  - It handled concise tool schemas well once the expected arguments were explicit.
  - It performed best when the game state was summarized clearly and compactly.
  - It was strong enough to show measurable improvement from prompt tuning.
- What could be better:
  - Tool-calling reliability is still the biggest opportunity.
  - The model sometimes guessed argument names when the schema was not explicit.
  - It sometimes over-checked state instead of taking productive actions.
  - Longer-horizon sequencing across overlapping tickets remained challenging.

### Cekura feedback

- What worked well:
  - Cekura was useful for turning agent behavior into a repeatable improvement loop.
  - It made failures concrete: missed deadlines, invalid actions, incomplete tickets, and transcript-level mistakes.
  - It helped separate prompt problems from game/harness problems.
  - It gave us a practical way to compare baseline and tuned behavior over the same scenarios.
- What could be better:
  - More examples for custom tool-calling/game-like agents would help.
  - A lightweight local-first workflow for hackathon iteration would be valuable.
  - Prompt-iteration templates that connect eval results directly to prompt diffs would speed up self-improvement loops.
- Biggest takeaway:
  - Cekura is strongest when the task has clear behavioral outcomes. Kitchen Rush worked well because every action produces a concrete game event, score impact, or failure mode.
