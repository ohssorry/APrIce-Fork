# How APrIce estimates cost — and what it refuses to estimate

This document exists because a cost tool's only real asset is trust. A number
you can't trace is worse than no number, so here is exactly what APrIce derives,
what it assumes, and what it declines to guess.

## The cost equation

```
cost = calls × (input_tokens × input_price + output_tokens × output_price)
```

Four terms. Static analysis can reach three of them:

| Term | Can we get it from source? | How |
|---|---|---|
| `input_price`, `output_price` | ✅ Yes | Model name is a literal in `model=`; look it up |
| `output_tokens` | 🟡 Bounded | `max_tokens` is a hard ceiling the API enforces |
| `input_tokens` | 🟡 Partly | Literal prompts are countable; runtime-assembled ones are not |
| `calls` | ❌ **No** | Nothing in the source says how often a line runs |

## What we do about each

### Prices — looked up, with a visible expiry

Every entry in [`src/aprice/prices/`](../src/aprice/prices/) carries a
`verified_on` date. A stale price is therefore *visible* rather than silently
wrong, and correcting it is a one-line pull request rather than a code change.

### Output tokens — a bound, not a guess

`max_tokens` is enforced by the provider, so it is a genuine ceiling. We use it
directly as the **upper bound** of every estimate. For the lower bound we assume
a response uses 30% of its ceiling (`TYPICAL_OUTPUT_RATIO`) — an assumption,
labelled as such, and the reason every figure is reported as a range.

When `max_tokens` isn't a literal, there is no visible ceiling at all. We say so
(`no-max-tokens`) rather than substituting a number.

### Input tokens — an explicit assumption you control

A prompt built at runtime from a database row cannot be measured from source.
Rather than pretend otherwise, APrIce takes the input length as a **parameter**
(`--input-tokens`, default 1,000). It's an input to the calculation, visible in
the command that produced the report.

### Call volume — deliberately not estimated

This is the one we refuse.

```python
for user in users:  # len(users) is unknowable
    client.messages.create(...)
```

`users` might hold 10 rows or 10 million. Nothing in the file says which. Any
monthly-bill figure would be the tool's own assumption wearing a number's
clothing — and a wrong bill forecast is worse than no forecast, because someone
will plan against it.

So APrIce reports two things that *are* derivable:

1. **Cost per request** — fully determined by model, `max_tokens`, and the
   stated input assumption.
2. **Structural risk** — a call inside a loop is flagged (`call-in-loop`)
   precisely *because* its volume is unknown. That's the honest form of the
   warning: not "this will cost $X", but "this multiplies by something I can't
   see."

## Why an AST instead of a regex

A regex over source text can find the string `messages.create`. It cannot tell
you:

- whether the match is inside a comment or a string literal (both are false
  positives);
- whether the call sits inside a `for` loop (the single highest-signal cost
  risk we detect);
- what `max_tokens` was set to (that's a keyword argument, i.e. structure).

Python's `ast` module gives all three for free, and it is in the standard
library. The cost is that a syntactically invalid file can't be parsed — we skip
those rather than fail the whole scan.

## Known limitations

- **Python only.** TypeScript is the obvious next target and needs a separate
  parser (tree-sitter).
- **Literal models only.** `model=config.MODEL` is detected as a call site but
  reported as unpriced. It is listed, never silently dropped.
- **No cross-file flow analysis.** We don't trace a variable back to its
  assignment. This is a deliberate scope limit, not an oversight — full flow
  analysis is a much larger project with its own false-positive problems.
- **Loop iteration counts are never inferred**, including for a literal
  `range(100)`. Special-casing the easy instance of an unsolvable problem would
  imply a capability the tool doesn't have.

## The rule we hold ourselves to

**If a number cannot be traced to the source code or the price database, it
doesn't ship.** Everything above follows from that.
