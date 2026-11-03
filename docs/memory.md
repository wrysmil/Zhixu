# AI Agent Memory in Zhixu

This page explains how Zhixu implements long-term AI agent memory: session
history, compressed archives, durable knowledge files, Dream consolidation, and
Git-backed memory changes.

Zhixu's memory is built on a simple belief: memory should feel alive, but it should not feel chaotic.

Good memory is not a pile of notes. It is a quiet system of attention. It notices what is worth keeping, lets go of what no longer needs the spotlight, and turns lived experience into something calm, durable, and useful.

That is the shape of memory in Zhixu.

## The Design

Zhixu does not treat memory as one giant file.

It separates memory into layers, because different kinds of remembering deserve different tools:

- `session.messages` holds the living short-term conversation.
- `memory/history.jsonl` is the running archive of compressed past turns.
- `SOUL.md`, `USER.md`, and `memory/MEMORY.md` are the durable knowledge files.
- `GitStore` records how those durable files change over time.

This keeps the system light in the moment, but reflective over time.

## The Flow

Memory moves through Zhixu in two stages.

### Stage 1: Consolidator

When a conversation grows large, the `Consolidator` summarizes older turns and appends the result to `memory/history.jsonl`, while keeping recent conversation available. Each summary preserves useful long-term facts and a short handoff for active work.

This file is:

- append-only
- cursor-based
- optimized for machine consumption first, human inspection second

Each line is a JSON object:

```json
{"cursor": 42, "timestamp": "2026-04-03 00:02", "content": "- User prefers dark mode\n- Decided to use PostgreSQL"}
```

It is not the final memory. It is the material from which final memory is shaped.

### Stage 2: Dream

`Dream` is the slower, more thoughtful layer. It runs on a cron schedule by default and can also be triggered manually.

Dream reads:

- new entries from `memory/history.jsonl` (only the part after the previous cursor)
- the current core memory summary injected into its system context
- the memory store's current counts and types, through `memory_stats`

It writes only through memory tools — `add_memory` and `memory_delete` — each entry routed to a memory type (`preference` / `rule` / `fact` / `skill` / `error` / `experience`). It cannot and does not edit `SOUL.md`, `USER.md`, or `MEMORY.md`; those files are projections regenerated from the store. `memory_dream_runs` keeps the real write counts, so `/dream-log` answers "what did it change last night" even when the working tree is unchanged.

This is why Zhixu's memory is not just archival. It is interpretive.

### Stage 3: Daily consolidation (`memory_consolidate`)

Once a day (03:00 local time by default) a second, rule-only pass sweeps the whole store — no LLM calls:

- **Merge** identical or near-identical facts; duplicates are superseded, never deleted.
- **Decay**: `effective = importance_score - decay_rate × days_since_last_access`. Entries that fall below the threshold are **archived** (marked expired) rather than removed — the row and the reason stay, and clearing `expires_at` restores it.
- `preference` and `rule` entries never decay, nor do entries at `importance >= 0.9`.
- Retrieval refreshes `last_accessed_at` and `access_count`, so what the user keeps using stays alive.

The job has a time budget (1500 s) with a checkpoint: if it is interrupted, the next run resumes where it stopped. Every run writes a row to `memory_consolidate_runs` (processed / merged / decayed / archived), shown in **Settings → Automations**. Configure it under `agents.defaults.consolidate`; `dryRun: true` computes and reports without writing, so a cautious first night can be previewed. Archive is reversible: clearing `expires_at` on the archived rows brings them back.

## The Files

In this page, `workspace` means the configured **agent workspace** (the default
is `~/.nanobot/workspace/`, or the path passed with `--workspace`). Selecting a
different project in the WebUI changes that chat's project context and tool
working directory; it does not relocate the files below.

```text
workspace/
├── SOUL.md              # The bot's long-term voice and communication style
├── USER.md              # Stable knowledge about the user
├── prompts/
│   ├── README.md        # Notes for memory guidance files
│   └── dream.md         # Optional instructions for how Dream organizes memory
└── memory/
    ├── MEMORY.md        # Project facts, decisions, and durable context
    ├── history.jsonl    # Append-only history summaries
    ├── .cursor          # Consolidator write cursor
    ├── .dream_cursor    # Dream consumption cursor
    └── .git/            # Version history for long-term memory files
```

A selected project may provide its own `AGENTS.md`, but project-local `SOUL.md`,
`USER.md`, and `memory/` do not replace the agent-owned files above. This keeps
one agent's profile and memory continuous while it works across projects. Use a
separate configured agent workspace when identity or memory must be isolated.

These files play different roles:

- `SOUL.md` remembers how Zhixu should sound.
- `USER.md` remembers who the user is and what they prefer.
- `MEMORY.md` remembers what remains true about the work itself.
- `history.jsonl` remembers what happened on the way there.

## Why `history.jsonl`

The old `HISTORY.md` format was pleasant for casual reading, but it was too fragile as an operational substrate.

`history.jsonl` gives Zhixu:

- stable incremental cursors
- safer machine parsing
- easier batching
- cleaner migration and compaction
- a better boundary between raw history and curated knowledge

You can still search it with familiar tools:

```bash
# grep
grep -i "keyword" memory/history.jsonl

# jq
cat memory/history.jsonl | jq -r 'select(.content | test("keyword"; "i")) | .content' | tail -20

# Python
python -c "import json; [print(json.loads(l).get('content','')) for l in open('memory/history.jsonl','r',encoding='utf-8') if l.strip() and 'keyword' in l.lower()][-20:]"
```

The difference is philosophical as much as technical:

- `history.jsonl` is for structure
- `SOUL.md`, `USER.md`, and `MEMORY.md` are for meaning

## Commands

Memory is not hidden behind the curtain. Users can inspect and guide it.

| Command | What it does |
|---------|--------------|
| `/dream` | Run Dream immediately |
| `/dream-log` | Show the latest Dream memory change |
| `/dream-log <sha>` | Show a specific Dream change |
| `/dream-restore` | List recent Dream memory versions |
| `/dream-restore <sha>` | Restore memory to the state before a specific change |
| `/dream-prompt` | Show how Dream is being guided for memory |
| `/dream-prompt init` | Create an editable Dream memory guide at `prompts/dream.md` |

These commands exist for a reason: automatic memory is powerful, but users should always retain the right to inspect, understand, and restore it.

## Versioned Memory

After Dream changes long-term memory files, Zhixu can record that change with `GitStore`.

This gives memory a history of its own:

- you can inspect what changed
- you can compare versions
- you can restore a previous state

That turns memory from a silent mutation into an auditable process.

## Guiding Dream

Dream decides what to keep, update, or forget using Zhixu's built-in memory instructions. Most users can leave this alone.

If one workspace needs a different memory style, create an editable guide:

```text
/dream-prompt init
```

This creates:

```text
workspace/prompts/dream.md
```

Edit that file in plain Markdown. When it has content, Dream follows it for this workspace before reading the latest conversation history. You do not need to paste history into the file; Dream adds the current `## Conversation History` block automatically.

To return to Zhixu's default behavior, delete `prompts/dream.md` or leave it empty.

Each workspace has its own guide. Changing this file does not affect other Zhixu workspaces.

## Configuration

Dream is configured under `agents.defaults.dream`:

```json
{
  "agents": {
    "defaults": {
      "dream": {
        "intervalH": 2,
        "modelOverride": null
      }
    }
  }
}
```

| Field | Meaning |
|-------|---------|
| `intervalH` | How often Dream runs, in hours |
| `cron` | Cron expression override (takes precedence over `intervalH`) |
| `modelOverride` | Optional model preset name used for Dream |

In practical terms:

- `intervalH` is the normal way to configure Dream frequency. Internally it runs as an `every` schedule.
- `cron` overrides `intervalH` when set, allowing precise cron expressions (e.g. `0 */4 * * *`).
- `modelOverride` selects a named entry from `model_presets` for Dream. It accepts preset names only; raw model identifiers are not supported. If omitted, Dream uses the main agent's selected runtime.

## In Practice

What this means in daily use is simple:

- conversations can stay fast without carrying infinite context
- durable facts can become clearer over time instead of noisier
- the user can inspect and restore memory when needed

Memory should not feel like a dump. It should feel like continuity.

That is what this design is trying to protect.
