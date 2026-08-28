---
title: Error Handling & Run Outcomes
description: Qirabot's exception hierarchy, the four ai() run outcomes in result.status, the max_steps retry pattern, action retries, and how failures appear in the HTML report.
---

# Error Handling

## Exceptions

```python
from qirabot import (
    Qirabot,
    QirabotError,              # base class
    AuthenticationError,       # credential setup problem
    QirabotTimeoutError,       # wait_for / auto-wait timeout
)

try:
    # The constructor itself can raise: it validates the model
    # configuration and resolves Google Cloud credentials (ADC), so a bad
    # setup fails here, not mid-run. `with` guarantees close() runs
    # if — and only if — construction succeeded.
    with Qirabot() as bot:
        page = bot.open("https://example.com")
        bot.click(page, "Login button")
except AuthenticationError:
    print("Credential setup problem — see the message for the fix.")
except QirabotTimeoutError:
    print("Operation timed out.")
except QirabotError as e:
    print(f"Error: {e}")
```

Setup errors surface at construction: an unknown provider or a missing
model in `model="{provider}/{model}"`, and a missing Google Cloud project,
raise `ValueError` with a configuration hint; missing or unusable Google
Cloud credentials raise with a message pointing at
`GOOGLE_APPLICATION_CREDENTIALS` / `gcloud auth application-default login`.

Everything in the exception hierarchy derives from `QirabotError`, so a
single `except QirabotError` is always a safe catch-all:

| Exception | When |
|---|---|
| `AuthenticationError` | Credential setup problem — missing, unusable, or ambiguous credentials. Not retried. |
| `QirabotTimeoutError` | A client-side wait timed out (`wait_for`, auto-wait). |
| `ActionError` | An AI action failed, including model-call failures reported by your Vertex AI endpoint (the message carries the provider's detail). |
| `MissingDependencyError` | An optional backend dependency (playwright, pyautogui, …) isn't installed; the message includes the exact install command for the environment qirabot is running in. Also an `ImportError`. |

That table is the whole hierarchy. The cloud-era exceptions
(`RateLimitError`, `InsufficientBalanceError`, `QirabotConnectionError`,
`TaskTerminatedError`) were **removed in v3.2** — there is no Qirabot
server, billing, or server-side task state for them to describe. Importing
them now fails — delete those `except` clauses, or widen them to
`QirabotError`.

**Rate limits (429) never reach your code as their own exception.** The
provider layer retries them internally with a dedicated backoff —
5s, 10s, 20s, 30s, cumulatively spanning a full quota minute, since a
rejected 429 costs nothing to wait out. Only a limit that survives all of
those surfaces, as `ActionError`.

`verify()` is the deliberate exception to raise-on-failure semantics: a
failed check doesn't raise. It returns a falsy result (a `VerifyResult`
whose `.reason` says why), so it drops straight into `assert` or `if`.
Model-call and credential errors still raise like any other call.

Transient action failures are retried automatically (`retry=1`,
`retry_delay=1.0` by default; see
[Configuration](/advanced/configuration)).

## How an ai() run ended: result.status

`result.success` is the two-state verdict, but a failed run can mean very
different things:

| status | meaning | `success` |
|---|---|---|
| `"completed"` | model declared the goal achieved | `True` |
| `"goal_failed"` | model concluded the goal is unreachable (login wall, captcha) | `False` |
| `"max_steps"` | step budget ran out; a truncation, not a capability verdict | `False` |
| `"error"` | the engine hit a terminal error (e.g. a failed model call) | `False` |

The `max_steps` case deserves special handling: it's a budget problem, not
a capability one.

```python
result = bot.ai(page, "Find the cheapest flight and hold it")
if result.status == "max_steps":
    # not a real failure — the budget was too small; retry with headroom
    result = bot.ai(page, "Find the cheapest flight and hold it", max_steps=50)
```

`goal_failed` usually means the environment needs help, such as a login
wall or captcha. Consider a
[human-in-the-loop custom tool](/advanced/ai-tasks#human-in-the-loop) so the
model can ask instead of giving up.

## Failures in the report

Runs that end by raising never produce a `RunResult`; in the
[HTML report](/advanced/reports) their section is badged `ERROR`. The
report is written on close even after exceptions and Ctrl+C, with the
per-step screenshots up to the failure. This is usually the fastest way
to see what actually happened on screen.

The header summary is green when everything passed, amber when the only
misses are `MAX STEPS` truncations, red when anything truly failed.

To record a run's terminal outcome yourself, so the report shows failed
or cancelled instead of succeeded, call `bot.fail()` / `bot.cancel()`
before closing. Both are local run bookkeeping; see the
[API reference](/reference/api#task-lifecycle).

## Custom-tool errors

A custom tool that raises doesn't kill the run: the exception is reported
back to the model as `ERROR: ...`, and the model can react: retry, try
another route, or finish with `goal_failed`. See
[AI Tasks & Custom Tools](/advanced/ai-tasks).
