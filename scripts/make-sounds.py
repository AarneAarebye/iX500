#!/usr/bin/env python3
"""Generates packaging/debian/sounds/press.wav and finish.wav (stdlib only)."""
import math
import struct
import wave
from pathlib import Path

RATE = 22050
AMPLITUDE = 0.4
FADE_S = 0.010


def tone(freq: float, seconds: float) -> list[int]:
    n = int(RATE * seconds)
    fade = int(RATE * FADE_S)
    out = []
    for i in range(n):
        env = min(1.0, i / fade, (n - 1 - i) / fade)
        out.append(int(32767 * AMPLITUDE * env * math.sin(2 * math.pi * freq * i / RATE)))
    return out


def silence(seconds: float) -> list[int]:
    return [0] * int(RATE * seconds)


def write(path: Path, samples: list[int]) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", s) for s in samples))


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "packaging" / "debian" / "sounds"
    out.mkdir(parents=True, exist_ok=True)
    write(out / "press.wav", silence(0.150) + tone(880, 0.120))
    write(out / "finish.wav", silence(0.150) + tone(660, 0.120) + silence(0.040) + tone(990, 0.120))


if __name__ == "__main__":
    main()
