# aprice-advisor

Analyzes real API usage logs (JSONL, schema v1 -- see issue #34) to find
waste that static analysis can't see: unused caching, duplicate calls,
retry cost. A separate package from the root `aprice` static analyzer,
reusing only its verified price lookup (`aprice.pricing.lookup()`).

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
