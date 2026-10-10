"""Bounded exact-value redaction of exposed diagnostics, never process input."""

from dataclasses import dataclass, field, replace

from ..execution.models import CommandResult


def normalize_output(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class DiagnosticRedactor:
    _values: tuple[str, ...] = field(repr=False)

    @classmethod
    def from_environments(cls, names, *environments):
        names = {name.upper() for name in names}
        values = {value for environment in environments if environment is not None
                  for key, value in environment.items() if key.upper() in names and value}
        # Cover the adapter's own newline conversion, including M3 verifier
        # text capture. Do not expand this into arbitrary secret transformations.
        values |= {normalize_output(value) for value in values}
        return cls(tuple(sorted(values, key=lambda value: (-len(value), value))))

    def __call__(self, text: str) -> str:
        for value in self._values:
            text = text.replace(value, "[REDACTED]")
        return text

    def command(self, result: CommandResult | None) -> CommandResult | None:
        if result is None:
            return None
        return replace(result, argv=tuple(self(arg) for arg in result.argv),
                       stdout=self(result.stdout), stderr=self(result.stderr))

    def notes(self, exception: BaseException) -> None:
        if hasattr(exception, "__notes__"):
            exception.__notes__[:] = [self(note) for note in exception.__notes__]
