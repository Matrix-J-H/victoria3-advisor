"""Speaks text aloud using the speech engine built into Windows.

Speech runs on a background thread so the advisor keeps watching the game
while it talks. Urgent items (event alerts) are spoken before queued advice.
"""
from __future__ import annotations

import itertools
import queue
import subprocess
import sys
import threading

_PS_SCRIPT = (
    "[Console]::InputEncoding=[Text.Encoding]::UTF8;"
    "Add-Type -AssemblyName System.Speech;"
    "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "$s.Rate={rate};{select}"
    "$s.Speak([Console]::In.ReadToEnd())"
)


class Speaker:
    def __init__(self, enabled: bool = True, rate: int = 1, voice_name: str = ""):
        self.enabled = enabled and sys.platform == "win32"
        self.rate = max(-10, min(10, rate))
        self.voice_name = voice_name.replace("'", "")
        self._queue: queue.PriorityQueue[tuple[int, int, str]] = queue.PriorityQueue()
        self._order = itertools.count()
        if self.enabled:
            threading.Thread(target=self._run, daemon=True).start()

    def say(self, text: str, urgent: bool = False) -> None:
        text = text.strip()
        if not text:
            return
        print(("[alert] " if urgent else "[advice] ") + text, flush=True)
        if self.enabled:
            self._queue.put((0 if urgent else 1, next(self._order), text))

    def wait(self) -> None:
        """Block until everything queued has been spoken."""
        if self.enabled:
            self._queue.join()

    def _run(self) -> None:
        while True:
            _, _, text = self._queue.get()
            try:
                self._speak(text)
            except Exception as exc:  # speech must never take the advisor down
                print(f"[voice] could not speak: {exc}", flush=True)
            finally:
                self._queue.task_done()

    def _speak(self, text: str) -> None:
        select = f"$s.SelectVoice('{self.voice_name}');" if self.voice_name else ""
        script = _PS_SCRIPT.format(rate=self.rate, select=select)
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
