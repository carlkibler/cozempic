"""Shared config-validation helpers for strategy functions.

Every strategy that reads tuning knobs from the per-session config dict goes
through one of the helpers below so invalid values (wrong type, out-of-range,
swapped ordering) surface a single well-formed ConfigError at invocation
time rather than:

  - a cryptic TypeError deep in a comparison loop
    (e.g. `'>=' not supported between instances of 'int' and 'str'`)
  - a silent no-op that nonetheless "runs successfully" and wastes a prune
    cycle, or worse, produces destructive behavior (e.g. negative age
    thresholds marking every tool result "old" and stubbing everything)

Keeping validation in one module also avoids duplicating error strings
across strategies and makes the user-facing message format consistent.
"""

from __future__ import annotations

from typing import Any


class ConfigError(ValueError):
    """Raised when a strategy config value is missing a required invariant.

    Subclasses ValueError so callers that already catch ValueError (e.g. the
    executor's outer wrapper) keep working, but type-checking machinery and
    humans reading tracebacks see the specific name.
    """


def _is_strict_int(value: Any) -> bool:
    """Strict int check: rejects bool (True/False are ints in Python) and float.

    YAML parsers in particular will turn `yes`/`no` into booleans, and users
    occasionally write `8192.0` in a JSON config. Both are almost never what
    was meant when the field expects a byte/line count.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def coerce_non_negative_int(config: dict, key: str, default: int) -> int:
    """Return `config[key]` as a non-negative int, or `default` if key absent.

    Raises `ConfigError` if the value is present but the wrong type or sign.
    """
    if key not in config:
        return default
    value = config[key]
    if not _is_strict_int(value):
        raise ConfigError(
            f"config[{key!r}] must be an int, got {type(value).__name__} {value!r}"
        )
    if value < 0:
        raise ConfigError(
            f"config[{key!r}] must be non-negative, got {value}"
        )
    return value


def coerce_choice(config: dict, key: str, choices: tuple[str, ...], default: str) -> str:
    """Return `config[key]` if it matches one of `choices`, else `default`.

    Raises `ConfigError` when the value is present but not a recognized
    choice. Error message lists the accepted values so the user can
    self-correct without reading the source.
    """
    if key not in config:
        return default
    value = config[key]
    if not isinstance(value, str):
        raise ConfigError(
            f"config[{key!r}] must be a string, got {type(value).__name__} {value!r}"
        )
    if value not in choices:
        raise ConfigError(
            f"config[{key!r}]={value!r} is not one of {list(choices)}"
        )
    return value


def coerce_ordered_pair(
    config: dict,
    low_key: str,
    high_key: str,
    defaults: tuple[int, int],
) -> tuple[int, int]:
    """Return `(config[low_key], config[high_key])` with the invariant
    `low < high` enforced. Missing keys fall back to `defaults`.

    Raises `ConfigError` if either value is the wrong type, negative, or
    if `low >= high`. Swapped pairs are the canonical source of silent
    misconfiguration (e.g. `tool_result_mid_age=50, tool_result_old_age=30`
    collapses the "recent" tier and marks every tool result as `old`).
    """
    low = coerce_non_negative_int(config, low_key, defaults[0])
    high = coerce_non_negative_int(config, high_key, defaults[1])
    if low >= high:
        raise ConfigError(
            f"config[{low_key!r}]={low} must be strictly less than "
            f"config[{high_key!r}]={high}"
        )
    return low, high
