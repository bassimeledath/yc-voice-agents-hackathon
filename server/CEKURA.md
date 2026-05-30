# Cekura Loop

This repo uses Cekura as the eval and regression harness around the local agent.

Fast text loop:

```bash
uv run python transcript_batch.py \
  --profiles 5 \
  --candidate-stack nemotron \
  --candidate-prompt-file games/yc_interview/prompts/founder_dynamic_base.md \
  --interviewer-stack gemini \
  --copy-to-downloads
```

Export the same profiles as Cekura evaluator payloads:

```bash
python cekura_cli.py export-yc-scenarios \
  --profile-file runs/yc_interview/<batch>/profiles.json \
  --output-dir runs/cekura_export/<batch>
```

Serve the candidate as a Cekura custom WebSocket text agent:

```bash
uv run python cekura_text_agent.py \
  --candidate-prompt-file games/yc_interview/prompts/founder_dynamic_base.md \
  --profile-file runs/yc_interview/<batch>/profiles.json
```

Expose it with ngrok:

```bash
ngrok http 127.0.0.1:8765
```

Use the resulting `wss://.../cekura` URL in Cekura, then run text evaluators:

```bash
python cekura_cli.py run-text \
  --scenario <scenario-id> \
  --websocket-url wss://example.ngrok-free.app/cekura
```

Fetch the Cekura result:

```bash
python cekura_cli.py get-result --result-id <result-id>
```

Improvement loop:

1. Run a fixed Cekura scenario set against the current prompt.
2. Inspect failed runs, transcripts, and metric explanations.
3. Make the smallest prompt/tool change that addresses repeated failure patterns.
4. Rerun the same Cekura scenarios as a regression set.
5. Keep the exported local batch and Cekura result IDs together as demo evidence.

Kitchen Rush text regression:

```bash
python cekura_cli.py export-kitchen-rush-scenarios
uv run python cekura_kitchen_agent.py --candidate-stack nemotron
ngrok http 127.0.0.1:8766
python cekura_cli.py run-text --scenario <scenario-id> --websocket-url wss://example.ngrok-free.app/cekura
```
