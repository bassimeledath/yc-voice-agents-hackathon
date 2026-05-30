# Hotseat UI

This is a no-build visual scene for two-speaker synthetic voice games.

The simpler product split is:

- Voice/game runtime: runs the two agents and records the conversation artifacts.
- Visual renderer: turns those artifacts into a cutout-style video.
- Web app: plays the rendered video and, separately, offers a minimal way for a human to call/talk to a voice bot.

The scene in this folder is mainly a preview/render target, not the realtime product surface.

Kitchen Rush has a separate replay dashboard:

```bash
open ui/kitchen-rush.html
```

It can load the sample run array from:

```text
/Users/bassime/Downloads/sim-20260530-133125/results.json
```

Use the `Load results.json` button in the page. The dashboard maps `turns[]` and
`final_report.events[]` into a top-down kitchen, ticket cards, tool feed, transcript, and score panel.

Open directly:

```bash
open ui/index.html
```

Or serve from the repo root:

```bash
python -m http.server 8765
```

Then open:

```text
http://127.0.0.1:8765/ui/
```

## Recording Interface

The voice agent side should write artifacts, not drive this UI live.

Minimum useful output from each synthetic run:

```json
{
  "game": { "id": "yc_interview", "name": "YC Interview" },
  "left": { "role": "founder", "label": "Founder" },
  "right": { "role": "interviewer", "label": "YC Partner" },
  "turns": [
    {
      "speaker_role": "interviewer",
      "spoken_text": "Welcome. Start simple: who has this problem?",
      "audio_path": "runs/yc_interview/audio/turn-01-interviewer.wav",
      "duration_ms": 4200
    }
  ]
}
```

That is enough to render a video:

```text
transcript JSON + per-turn WAV files -> visual renderer -> MP4
```

Recommended contract for the other voice-agent worker:

- The voice/game runtime owns conversation state.
- It should produce a deterministic run folder: `run.json`, per-turn audio files, and optionally a combined audio file.
- `speaker_role` must match either `left.role` or `right.role`.
- `spoken_text` should be the text displayed in the video.
- `audio_path` should point to the audio for that turn if available.
- `duration_ms` is useful, but the renderer can derive it from audio if omitted.
- No realtime WebSocket/SSE integration is needed for the visual UI.

The human-to-bot path should be separate and minimal. It can be a plain page or button that joins the voice bot/session. It does not need the animated two-agent UI.

## Current Preview

`ui/index.html` still lets you load a run JSON and preview the cutout scene in the browser. The next step is to add a renderer script that captures this scene with the turn audio and exports an MP4.

## Code Pointers

Give these files to the voice-agent worker:

- `server/synthetic_bridge.py`: CLI entrypoint for synthetic two-agent runs. It chooses game, stacks, turn limits, output JSON path, and audio output directory.
- `server/core/bridge.py`: main conversation loop. It already emits `turns[]` with `speaker_role`, `spoken_text`, `heard_text`, metrics, and `wav_path`.
- `server/core/types.py`: shared runtime dataclasses. `BridgeOptions.output` and `BridgeOptions.audio_dir` are the important renderer-facing knobs.
- `server/core/audio.py`: creates the voice pass and WAV files. The renderer needs stable `wav_path` values from here.
- `server/core/game_config.py`: maps `games/<game>/game.json` into agent configs and role prompts.
- `server/games/yc_interview/game.json`: current game definition. It establishes the canonical left/right roles: candidate/founder and interviewer.
- `ui/index.html`, `ui/scene.css`, `ui/scene.js`: current cutout scene preview. This should become the visual target for the MP4 renderer, not the realtime product UI.

Small improvement request for the voice-agent worker:

- Add `game: { "id": ..., "name": ... }` to the bridge output.
- Add `label` fields under `left` and `right` if possible.
- Keep `wav_path` per turn. Optionally rename/copy it to `audio_path` in a renderer-friendly manifest.
- Optionally add `duration_ms` per turn; otherwise the renderer can inspect the WAV duration.
