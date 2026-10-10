"""Strict UTF-8 and JSON argv readers; errors carry codes, never inputs."""

from fractions import Fraction
from pathlib import Path
import json
import math
import re


class ConfigFailure(Exception):
    def __init__(self, code="INVALID_CONFIGURATION"):
        super().__init__(code)
        self.code = code


def read_argv(*, json_text=None, file_path=None):
    if (json_text is None) == (file_path is None):
        raise ConfigFailure("VERIFY_ARGV_INVALID")
    try:
        text = Path(file_path).read_bytes().decode("utf-8") if file_path is not None else json_text
        value = json.loads(text)
        if not isinstance(value, list) or not value or not isinstance(value[0], str) or not value[0]:
            raise ValueError
        if any(not isinstance(x, str) or "\0" in x for x in value):
            raise ValueError
        for arg in value:
            arg.encode("utf-8")
        return tuple(value)
    except (OSError, ValueError, TypeError, UnicodeError):
        raise ConfigFailure("VERIFY_ARGV_INVALID") from None


def read_text_input(*, file_path=None, text=None, use_stdin=False, read_stdin=None):
    if sum((file_path is not None, text is not None, bool(use_stdin))) != 1:
        raise ConfigFailure("INPUT_SELECTION_INVALID")
    try:
        if file_path is not None:
            return Path(file_path).read_bytes().decode("utf-8")
        if use_stdin:
            return read_stdin().decode("utf-8")
        text.encode("utf-8")
        return text
    except (OSError, UnicodeError, AttributeError, TypeError):
        raise ConfigFailure("INPUT_READ_FAILED") from None


def exit_code(value):
    if isinstance(value, bool) or not re.fullmatch(r"[+-]?[0-9]+", str(value)):
        raise ConfigFailure("INTEGER_INVALID")
    return int(value)


def positive_int(value):
    result = exit_code(value)
    if result <= 0:
        raise ConfigFailure("INTEGER_INVALID")
    return result


def finite_seconds(value):
    try:
        if isinstance(value, bool):
            raise ValueError
        result = float(value)
        if not math.isfinite(result) or result <= 0:
            raise ValueError
        return result
    except (TypeError, ValueError, OverflowError):
        raise ConfigFailure("TIMEOUT_INVALID") from None


def exact_fraction(value):
    try:
        if isinstance(value, bool):
            raise ValueError
        result = Fraction(value)
        if not 0 < result <= 1:
            raise ValueError
        return result
    except (ValueError, TypeError, ZeroDivisionError):
        raise ConfigFailure("STOCHASTIC_POLICY_INVALID") from None
