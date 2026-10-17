"""CSV для открытия в таблицах: пользовательский текст не становится формулой."""

import csv
from typing import Any, Iterable, TextIO


def safe_csv_cell(value: Any) -> Any:
    if isinstance(value, str):
        probe = value.lstrip(" \t\r\n\v\f\ufeff")
        if probe.startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
            return "'" + value
    return value


class SafeCsvWriter:
    def __init__(self, stream: TextIO, *, delimiter: str = ";") -> None:
        self._writer = csv.writer(stream, delimiter=delimiter)

    def writerow(self, values: Iterable[Any]) -> Any:
        return self._writer.writerow([safe_csv_cell(value) for value in values])
