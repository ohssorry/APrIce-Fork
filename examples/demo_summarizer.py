"""Demo module for the APrIce presentation video.

Shows a plain call (priced normally) and a call sitting inside a loop
(flagged as call-in-loop, since the request count can't be known statically).
"""

from __future__ import annotations

import anthropic

client = anthropic.Anthropic()


def summarize(document: str):
    return client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": document}],
    )


def summarize_all(documents: list[str]):
    results = []
    for doc in documents:
        results.append(
            client.messages.create(
                model="claude-opus-5",
                max_tokens=4096,
                messages=[{"role": "user", "content": doc}],
            )
        )
    return results
