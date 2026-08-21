# APrIce

[![CI](https://github.com/ohssorry/APrIce/actions/workflows/ci.yml/badge.svg)](https://github.com/ohssorry/APrIce/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Estimate what your API calls cost — before you merge them.**

APrIce parses your source code, finds every paid API call, and reports what a
request costs and how a pull request changes it.

```console
$ aprice scan src/
APrIce: 5 API call(s) found

Cost per request
  src/summarize.py:16   claude-sonnet-5          $0.00780 - $0.01800
  src/batch.py:31       claude-opus-5            $0.03600 - $0.10700

  Total per request: $0.04380 - $0.12500

Findings
  ! src/batch.py:31  [call-in-loop] API call inside a loop: cost scales with
    the number of iterations, which this tool cannot see.
```

## Why another cost tool

Existing tools ([llm-cost][llm-cost], [Calcis][calcis]) analyze **prompt files** —
`.prompt` files kept outside the code. That works if your team already separates
prompts that way. Most code doesn't look like that; it looks like this:

```python
for user in users:  # ← the expensive part
    resp = client.messages.create(
        model="claude-opus-5",  # ← the model, inline
        max_tokens=4096,
        messages=[{"role": "user", "content": f"Summarize: {user.doc}"}],
    )
```

There is no prompt file here to analyze. **APrIce parses the source itself**, so
it works on ordinary codebases — and because it reads the syntax tree rather
than text, it can see that this call sits inside a loop.

[llm-cost]: https://github.com/rul1an/llm-cost
[calcis]: https://github.com/marketplace/actions/calcis-llm-cost-estimate

## What it does, and what it deliberately doesn't

| | |
|---|---|
| ✅ Finds API call sites by parsing the AST | Not regex — comments and strings don't match |
| ✅ Prices each call from a community-maintained YAML database | Adding a provider is a one-file pull request |
| ✅ Reports a **range**, never a single number | The upper bound assumes `max_tokens` is fully used |
| ✅ Flags structural cost risks | Calls in loops, missing `max_tokens` |
| ✅ Fails CI on a threshold | `--fail-on-warning` |
| ❌ Does **not** predict your monthly bill | Call volume cannot be derived from source — see below |

### The honest limitation

**A static analyzer cannot know how often a line runs.** `client.messages.create()`
might be called ten times a day or ten million. No amount of parsing reveals that.

So APrIce reports **cost per request** and **the change a PR makes to it** —
both of which *are* knowable from source — and flags the structural patterns
(loops, unbounded `max_tokens`) where volume is the risk. It does not multiply
by a made-up traffic number and call the result a forecast.

Full reasoning in [`docs/methodology.md`](docs/methodology.md). For how the
pipeline is put together, see [`docs/architecture.md`](docs/architecture.md).

## Install

Not yet published to PyPI — install from source:

```console
git clone https://github.com/ohssorry/APrIce.git
cd APrIce
pip install -e ".[dev]"
```

## Usage

```console
aprice scan path/to/code                    # scan a file or directory
aprice scan src/ --format markdown          # PR-comment-ready output
aprice scan src/ --input-tokens 4000        # change the input-length assumption
aprice scan src/ --fail-on-warning          # exit 1 on any cost risk (for CI)
aprice diff --base origin/develop --head HEAD  # cost change vs. a base branch, before you merge
```

`aprice diff` scans both refs from a throwaway `git worktree` -- it never touches
your actual working tree, staged or unstaged changes included.

## Supported providers

| Provider | Detection | Prices verified |
|---|---|---|
| Anthropic | `.messages.create()`, `.messages.stream()`, `.messages.batches.create()` | ✅ 2026-08-17 |
| OpenAI | `.chat.completions.create()`, `.responses.create()`, `.completions.create()`, `.embeddings.create()`, `.images.*()`, `.audio.*()` | ✅ 2026-08-19 |
| Google | `.generate_content()`, `.models.embed_content()` | ✅ 2026-08-19 |

Prices are pinned to each entry's `verified_on` date in
[`src/aprice/prices/`](src/aprice/prices/) — check there for the current
date, since prices drift and this table won't always catch up first.

## Contributing

Prices change constantly, and no single maintainer can track every provider.
The price database is plain YAML for exactly that reason:

```yaml
- id: claude-sonnet-5
  input_per_mtok: 3.00
  output_per_mtok: 15.00
  verified_on: 2026-08-17
```

Adding or correcting a model is a one-line change to one file. See
[CONTRIBUTING.md](CONTRIBUTING.md).

**팀원이신가요?** [`docs/onboarding.md`](docs/onboarding.md)부터 읽으세요 —
환경 세팅, 저장소 구조, 협업 규칙, 일정이 정리돼 있습니다.

## License

MIT — see [LICENSE](LICENSE).
