"""Victoria 3 advisor: watches the autosave and the game log, and speaks advice.

    python -m advisor                 watch while you play
    python -m advisor --once          analyse the current autosave now, then exit
    python -m advisor --once FILE     analyse a specific save
    python -m advisor --digest-only   skip the Claude call; print the summary instead
    python -m advisor --say "text"    test the voice
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .advisor import Advice, get_advice
from .config import Config, load_config
from .digest import build_digest, diff_digests
from .localization import Localization
from .log_events import LogEvent, describe, parse_line
from .melt import MeltError, to_text
from .save_parser import SaveFile
from .voice import Speaker
from .watcher import LogTailer, SaveWatcher


def log(msg: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


class Session:
    def __init__(self, cfg: Config, speaker: Speaker, digest_only: bool):
        self.cfg, self.speaker = cfg, speaker
        self.digest_only = digest_only or not cfg.api_key
        self.loc = Localization.load(cfg.game_dir, cfg.data_dir / "loc_cache.json")
        self.events: list[dict[str, Any]] = []
        self.telemetry: dict[str, Any] = {}
        self.previous_spoken: str | None = None
        self.last_digest = self._read_json(cfg.data_dir / "last_digest.json")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # --- autosave route -------------------------------------------------
    def process_save(self, save: Path) -> bool:
        started = time.time()
        log(f"Reading {save.name} ...")
        try:
            text_path, warning = to_text(save, self.cfg.data_dir / "work", self.cfg.rakaly)
            with SaveFile(text_path) as sf:
                digest = build_digest(sf, self.loc)
        except (MeltError, ValueError, OSError) as exc:
            log(f"Could not read the save: {exc}")
            return False
        if warning:
            log("Warning: " + warning)
        changes = diff_digests(self.last_digest, digest)
        self._store_digest(digest)
        log(f"{digest['country']['name']}, {digest['game_date']}: summary built in {time.time() - started:.1f}s")

        if self.digest_only:
            if not self.cfg.api_key:
                log("No ANTHROPIC_API_KEY in .env, so no advice was requested. Summary follows.")
            print(json.dumps(digest, indent=2))
            if changes:
                print("Changes since previous autosave:", json.dumps(changes, indent=2))
            return True
        try:
            advice = get_advice(self.cfg.api_key, self.cfg.model, self.cfg.max_spoken_words, digest,
                                changes, self.events, self.telemetry or None, self.previous_spoken, warning,
                                self.cfg.max_output_tokens, self.cfg.effort)
        except Exception as exc:
            log(f"Could not get advice from Claude: {exc}")
            return False
        if advice.truncated:
            log("Warning: Claude's reply was cut off by the output limit. "
                "Raise max_output_tokens in config.toml if this keeps happening.")
        if not advice.spoken:
            log("Claude returned no spoken advice this time.")
            return False
        self._record(digest, advice)
        self.speaker.say(advice.spoken)
        self.previous_spoken = advice.spoken
        self.events.clear()
        return True

    def _store_digest(self, digest: dict[str, Any]) -> None:
        folder = self.cfg.data_dir / "digests"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{digest['country'].get('tag', 'XXX')}_{digest['game_date']}.json"
        text = json.dumps(digest, indent=2)
        (folder / name).write_text(text, encoding="utf-8")
        (self.cfg.data_dir / "last_digest.json").write_text(text, encoding="utf-8")
        self.last_digest = digest

    def _record(self, digest: dict[str, Any], advice: Advice) -> None:
        with open(self.cfg.data_dir / "advice_log.txt", "a", encoding="utf-8") as fh:
            fh.write(f"=== {datetime.now():%Y-%m-%d %H:%M:%S} | {digest['country']['name']} | "
                     f"game date {digest['game_date']} ===\n")
            fh.write(f"SPOKEN: {advice.spoken}\n\nDETAIL:\n{advice.detail}\n\n")

    # --- log route ------------------------------------------------------
    def handle_log_line(self, line: str) -> None:
        event = parse_line(line)
        if event is None:
            return
        if event.kind.startswith("monthly"):
            self.telemetry.update(event.fields)
            log(f"telemetry {event.kind}: {event.fields}")
            return
        self.events.append({"kind": event.kind, **event.fields})
        log(f"event {event.kind}: {event.fields}")
        sentence = describe(event)
        if sentence and self.cfg.speak_events:
            self.speaker.say(sentence, urgent=True)


def watch(session: Session, cfg: Config, analyse_now: bool) -> None:
    save_path = cfg.save_dir / cfg.save_name
    saves = SaveWatcher(save_path, skip_existing=not analyse_now)
    tailers = [LogTailer(cfg.log_dir / name) for name in cfg.log_files]
    log(f"Watching {save_path}")
    log("Watching logs: " + ", ".join(str(cfg.log_dir / n) for n in cfg.log_files))
    if session.digest_only:
        log("Running without Claude: summaries will be printed, no advice.")
    log("Play the game. Advice follows each autosave. Press Ctrl+C to stop.")
    try:
        while True:
            for tailer in tailers:
                for line in tailer.poll():
                    session.handle_log_line(line)
            ready = saves.poll()
            if ready:
                session.process_save(ready)
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        log("Stopped.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="advisor", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", nargs="?", const="", metavar="SAVE",
                    help="analyse one save and exit (default: the autosave)")
    ap.add_argument("--now", action="store_true", help="when watching, analyse the existing autosave first")
    ap.add_argument("--digest-only", action="store_true", help="do not call Claude; print the summary")
    ap.add_argument("--no-voice", action="store_true", help="print advice instead of speaking it")
    ap.add_argument("--say", metavar="TEXT", help="speak TEXT and exit (voice test)")
    ap.add_argument("--config", type=Path, help="path to config.toml")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    speaker = Speaker(cfg.voice_enabled and not args.no_voice, cfg.voice_rate, cfg.voice_name)

    if args.say:
        speaker.say(args.say)
        speaker.wait()
        return 0

    session = Session(cfg, speaker, args.digest_only)
    if args.once is not None:
        save = Path(args.once) if args.once else cfg.save_dir / cfg.save_name
        ok = session.process_save(save)
        speaker.wait()
        return 0 if ok else 1
    watch(session, cfg, args.now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
