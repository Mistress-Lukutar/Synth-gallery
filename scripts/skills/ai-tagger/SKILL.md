---
name: ai-tagger
description: >
  Automated AI tagging for Synth Gallery media vault.
  Use when VLM/AI needs to analyze photos/videos from the AI tagging job queue
  and assign content-based tags. Triggers when the task involves:
  (1) processing pending AI tagging jobs, (2) downloading and visually analyzing
  media files, (3) submitting tag results via the gallery API.
---

# AI Tagger Agent

Single-model workflow: VLM/AI uses its own vision capabilities to analyze images
and the bundled API helper script to interact with the Synth Gallery queue.

## Prerequisites

- API key for `X-API-Key` header (user provides or env `SYNTH_AI_API_KEY`)
- Gallery base URL (default `http://localhost:8000`)

## Workflow

1. **Load tags** — `python scripts/ai_agent.py tags`
2. **Get pending jobs** — `python scripts/ai_agent.py pending`
3. **Claim & download** — `python scripts/ai_agent.py download <job_id>`
4. **Analyze** — VLM/AI reads the downloaded image via `ReadMediaFile` and uses
   the loaded tag dictionary + item metadata to decide relevant tags
5. **Submit** — `python scripts/ai_agent.py submit <job_id> "tag1,tag2,tag3"`
6. **Handle unknown tags** — if error lists unknown names, correct and re-submit
7. **Repeat** from step 2

## Tag Selection Rules

- Submit **names** (strings), not numeric IDs
- Only use tags from the loaded dictionary
- Do not duplicate `existing_tags`
- Use `item.description` as context hint when present
- Prefer specific over general (`red_fox` > `fox` > `animal`)
- Implications auto-resolve on the backend

## Error Handling

| Status | Meaning | Action |
|--------|---------|--------|
| 401 | Invalid API key | Stop, ask user |
| 409 | Job unavailable | Skip to next job |
| 400 + `unknown_tags` | Name not in dictionary | Remove unknown names and retry |
| 404 | Job not found | Skip, continue |

## Analyzing Images

After `download`, the script prints the file path. VLM/AI reads the image with
`ReadMediaFile` and the item metadata from the JSON output.

The prompt for analysis should include:
- The list of available tag names
- Already assigned tags (to avoid duplicates)
- The item description as a hint
- Instruction to return only known tag names

## Script Reference

The bundled `scripts/ai_agent.py` provides subcommands:

```bash
# List all tags
python scripts/ai_agent.py tags --api-key KEY

# List pending jobs
python scripts/ai_agent.py pending --api-key KEY

# Claim job and download file (prints metadata + file path)
python scripts/ai_agent.py download <job_id> --api-key KEY

# Submit results
python scripts/ai_agent.py submit <job_id> "fox,wolf" --api-key KEY
```

## API Reference

See `references/api.md` for raw endpoint documentation and request/response examples.
