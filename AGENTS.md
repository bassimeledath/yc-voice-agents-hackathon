# Agent Notes

## Cekura

- Cekura is the eval and observability system for this Pipecat voice agent.
- Use the installed Cekura skills for workflow guidance: `cekura-onboarding`, `cekura-create-agent`, `cekura-eval-design`, `cekura-metric-design`, `cekura-predefined-metrics`, `cekura-metric-improvement`, `cekura-self-improving-agent`, `cekura-fixing-prod-issues`, and `cekura-coordinator`.
- Prefer the Cekura MCP server for live workspace actions such as listing agents, creating evaluators, running scenarios, and reviewing results.
- The Cekura API key is stored in `server/.env` as `CEKURA_API_KEY`; do not commit or print it.
- The local helper `server/cekura_mcp.sh` starts the MCP remote shim using `server/.env`.
- The local CLI helper supports quick API checks:
  - `python server/cekura_cli.py list-agents`
  - `python server/cekura_cli.py list-evaluators --agent-id <agent-id>`
  - `python server/cekura_cli.py run-pipecat --scenario <scenario-id>`
- When connecting this repo's bot in Cekura, use `Pipecat` as the provider.
