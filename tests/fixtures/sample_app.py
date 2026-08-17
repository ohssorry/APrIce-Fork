"""Fixture exercising each shape the detector needs to handle.

Not executed -- only parsed.
"""

import anthropic
from openai import OpenAI

client = anthropic.Anthropic()
oai = OpenAI()


def summarize(document):
    """Plain call with a literal model and max_tokens."""
    return client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": document}],
    )


def summarize_all(documents):
    """Call inside a loop -- should raise call-in-loop."""
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


def cross_compare(rows, columns):
    """Nested loop -- should raise call-in-nested-loop."""
    for row in rows:
        for column in columns:
            oai.chat.completions.create(
                model="gpt-4o",
                max_tokens=256,
                messages=[{"role": "user", "content": f"{row} vs {column}"}],
            )


def dynamic(model_name, prompt):
    """Model comes from a variable -- unpriceable, but still detected."""
    return client.messages.create(
        model=model_name,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )


def no_ceiling(prompt):
    """No max_tokens literal -- should raise no-max-tokens."""
    return oai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
