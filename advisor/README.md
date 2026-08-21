# aprice-advisor

Analyzes real API usage logs (JSONL, schema v1 -- see issue #34) to find
waste that static analysis can't see: unused caching, duplicate calls,
retry cost. A separate package from the root `aprice` static analyzer,
reusing only its verified price lookup (`aprice.pricing.lookup()`).

## Usage

```console
aprice-advisor analyze events.jsonl                             # human-readable summary
aprice-advisor analyze events.jsonl --format json                # machine-readable
aprice-advisor analyze events.jsonl --duplicate-window 10m       # widen the duplicate window (default: 5m)
aprice-advisor analyze events.jsonl --callsites callsites.json   # map callsite_id to file:line
```

`events.jsonl` is one JSON object per line, schema v1 (see issue #34 for the
full field contract). A minimal valid row:

```json
{"schema_version": 1, "timestamp": "2026-08-21T12:00:00Z", "project_id": "proj-a", "provider": "anthropic", "model": "claude-sonnet-5", "operation": "messages.create", "request_id": "req-1", "input_tokens": 100, "output_tokens": 50, "status": "success"}
```

`callsites.json` (optional) maps a log's `callsite_id` to a source location,
for requests that only recorded an id rather than an explicit
`source.file`/`source.line`:

```json
{"site-a": {"file": "src/app.py", "line": 16}}
```

The report's cost figures are standard-price estimates -- the verified price
table applied to actual logged tokens, never an invoice amount. Retry,
duplicate, and cache-miss detection only ever use evidence explicit in the
log (`retry_of`, `request_fingerprint`, `cache_eligible`/`cache_status`);
nothing is inferred from names, timing, or failure status.

## Local development

`aprice` isn't published, so `pip install -e .` here can't resolve it on
its own. Install both packages editable into the *same* environment,
root first:

```console
cd ..
python -m venv .venv && source .venv/bin/activate   # from the repo root
pip install -e ".[dev]"
cd advisor
pip install -e ".[dev]"
pytest
```
