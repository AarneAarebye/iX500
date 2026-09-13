from datetime import datetime
from pathlib import Path


def compute_output_paths(
    destination: Path, count: int, *, now: datetime | None = None
) -> list[Path]:
    timestamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    if count == 1:
        return [destination / f"scan_{timestamp}.pdf"]
    return [destination / f"scan_{timestamp}_{i + 1}.pdf" for i in range(count)]
