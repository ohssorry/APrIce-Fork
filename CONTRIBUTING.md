# Contributing to APrIce

Thanks for helping out. There are two very different kinds of contribution
here, and one of them needs no Python at all.

## 1. Updating a price (no code required)

**This is the most valuable contribution, and the easiest.** API prices change
often, and no single maintainer can track every provider. The price database is
plain YAML for exactly this reason.

Edit the file for your provider in [`src/aprice/prices/`](src/aprice/prices/):

```yaml
provider: anthropic
currency: USD
models:
  - id: claude-sonnet-5        # the exact string used in `model=`
    input_per_mtok: 3.00       # USD per million input tokens
    output_per_mtok: 15.00     # USD per million output tokens
    verified_on: 2026-08-17    # the date YOU checked the official page
    note: >-                   # optional
      Introductory pricing applies through 2026-08-31.
```

Rules:

- **`verified_on` is the date you personally checked the official price page**,
  not the date the price took effect. It is how readers judge staleness.
- **Link the source in your pull request.** A price with no citation cannot be
  reviewed.
- `id` must match exactly what appears in `model=` in real code. If a provider
  has aliases, add one entry per alias.

Adding a whole new provider means adding one YAML file, plus one line in
`CALL_SIGNATURES` in [`detector.py`](src/aprice/detector.py) so the calls get
found. Both are small — open an issue if you want a hand.

## 2. Code contributions

```console
git clone https://github.com/ohssorry/APrIce.git
cd APrIce
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
ruff check .
```

### Adding a detection signature

`CALL_SIGNATURES` in `detector.py` maps the tail of a callee's dotted path to a
provider:

```python
CALL_SIGNATURES = {
    ("messages", "create"): "anthropic",
    ("chat", "completions", "create"): "openai",
}
```

Matching the *tail* keeps detection independent of how the client was named, so
`client.messages.create`, `self._anthropic.messages.create`, and
`get_client().messages.create` all match the same entry. Add a fixture case to
`tests/fixtures/sample_app.py` and a test alongside it.

### Adding a rule

Rules in [`rules.py`](src/aprice/rules.py) flag cost risks that don't depend on
knowing a price — a call inside a loop is a risk whatever model it uses. A rule
takes an `ApiCall` and returns `Finding`s. Keep the message specific about *why*
it is a cost risk, not just *what* was found.

## What we will and won't accept

We will not merge features that require inventing numbers the source doesn't
contain. Specifically: **no monthly-bill forecasts based on assumed traffic.**
The tool's credibility rests on only claiming what it can actually derive. If a
number cannot be traced to the source or the price database, it doesn't ship.

See [`docs/methodology.md`](docs/methodology.md) for the reasoning.

## Working agreement

- Every change goes through a pull request — no direct pushes to `main`.
- Every pull request gets a review before merge.
- Every unit of work starts as an issue, so the board reflects reality.

## Code of conduct

Be decent to each other. Assume good faith, critique the code rather than the
person, and keep review comments actionable.
