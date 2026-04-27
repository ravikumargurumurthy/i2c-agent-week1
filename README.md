# I2C Agent — Week 1

A toy cash application agent built with OpenAI tool calling and Pydantic.
Part of a 90-day learning project: Data Engineer → Agentic AI Engineer for Finance Ops.

## What this is
A single-file agent that takes a remittance string, calls three tools
(parse, customer lookup, invoice lookup), and emits a validated `RemittanceAdvice`.

## Status
🚧 Day 0 — project scaffolding complete. Schemas next.

## Setup
\`\`\`bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
\`\`\`

Create a `.env` file with your Azure OpenAI credentials:
\`\`\`
AZURE_OPENAI_ENDPOINT=https://your-endpoint.openai.azure.com/
AZURE_OPENAI_API_KEY=your-key-here
AZURE_OPENAI_API_VERSION=2024-12-01-preview
AZURE_OPENAI_DEPLOYMENT=your-deployment-name
\`\`\`

## Run
\`\`\`bash
python agent.py
\`\`\`

## Test
\`\`\`bash
pytest -v
\`\`\`