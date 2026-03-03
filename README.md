# recall

Ever lost a conversation session with claude code and wish your could resume it? This skill lets claude search across all your past conversations with full-text search. Builds a SQLite FTS5 index over `~/.claude/projects/` JSONL files with BM25 ranking, Porter stemming, and incremental updates.

## Install

```bash
npx skills add arjunkmrm/recall
```

Then use `/recall` in Claude Code or ask "find a past session where we talked about foo" (you might need to restart claude code).

## How it works

- Scans `~/.claude/projects/**/*.jsonl` and indexes user/assistant messages into a SQLite FTS5 database at `~/.claude/recall.db`
- First run indexes all sessions (a few seconds); subsequent runs only process new/modified files
- Skips tool_use, tool_result, thinking, and image blocks
- Returns results ranked by BM25 with highlighted excerpts
- No dependencies — Python 3.9+ stdlib only (sqlite3, json, argparse)

## Query syntax

| Pattern | Example | Description |
|---------|---------|-------------|
| Words | `bufferStore` | Stemmed match ("discussing" → "discuss") |
| Phrases | `"ACP protocol"` | Exact phrase |
| Boolean | `rust AND async` | AND, OR, NOT |
| Prefix | `buffer*` | Wildcard suffix |
| Combined | `"state machine" AND test` | Mix freely |
