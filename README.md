# Victoria 3 Advisor

Watches your Victoria 3 autosave and game log while you play, asks Claude what
to prioritise, and reads the answer aloud.

This is the first iteration. It is meant for testing the pipeline end to end.

## How it works

1. The game writes `autosave.v3` every six months of game time.
2. `bin\rakaly.exe` decodes that binary save into plain text.
3. `advisor\digest.py` reduces about 200 MB of text to a one-page summary of your
   country: budget, economy, population, states, buildings with problems, laws,
   interest groups, movements, technology, diplomacy and military.
4. The summary, what changed since the previous autosave, and recent log events
   go to Claude, which returns a short spoken briefing plus written notes.
5. Windows' built-in speech reads the briefing aloud.

Between autosaves, the **Advisor Telemetry** mod writes monthly figures and event
lines (diplomatic play started, war started, battle won or lost, default) to the
game log. Alerts that concern you are spoken straight away.

## One-time setup

1. Double-click `setup.bat`. It creates a private Python environment in `.venv`
   and installs the one package needed (`anthropic`).
2. Open `.env` and paste your Anthropic API key after `ANTHROPIC_API_KEY=`.
3. In the Victoria 3 launcher, add **Advisor Telemetry** to your playset.
4. Check the paths in `config.toml` if your game or documents folder moves.

## Tests, in order

Run these from this folder in a terminal (or add the options after `run_advisor.bat`).

| Step | Command | What you should see |
|---|---|---|
| 1. Voice | `run_advisor.bat --say "Testing the advisor voice"` | The sentence is spoken |
| 2. Save reading, no Claude | `run_advisor.bat --once --digest-only` | A JSON summary of the country in your latest autosave, in under about 15 seconds |
| 3. Full advice on the existing autosave | `run_advisor.bat --once` | Spoken advice; the same text in `data\advice_log.txt` |
| 4. Live | `run_advisor.bat`, then play | Advice after each autosave; `telemetry` and `event` lines in the window if the mod is working |

If step 4 shows no `telemetry` lines after a month of game time, the mod's lines
are not reaching the log. Try launching the game with "Open game in Debug Mode"
and tell Claude what happened.

## Folder layout

```
run_advisor.bat      start the advisor
setup.bat            one-time install
config.toml          paths, model, voice settings
.env                 your API key (never shared)
bin\rakaly.exe       save decoder, from https://github.com/rakaly/cli
advisor\
  __main__.py        main loop and command-line options
  config.py          reads config.toml and .env
  watcher.py         notices a new autosave or new log lines
  melt.py            runs rakaly on a copy of the autosave
  save_parser.py     reads the decoded text, only the parts needed
  localization.py    English names from the game's own files
  digest.py          builds the country summary and the changes since last time
  log_events.py      reads the mod's lines from the game log
  advisor.py         the prompt and the Claude call
  voice.py           speech
data\                created when it runs
  digests\           one summary per autosave
  last_digest.json   the most recent summary
  advice_log.txt     everything it said, with the written notes
  work\              the copied and decoded save (overwritten each time)
tests\test_core.py   `python -m unittest discover tests`
```

The mod lives in
`Documents\Paradox Interactive\Victoria 3\mod\advisor_telemetry\`.

## Known limits of this iteration

- Market prices are not in the summary: the save does not store them per market
  in a directly readable form.
- Budget income and expenses are summed from the save's category lists, so the
  weekly balance is approximate.
- After a game patch, rakaly may not recognise new fields until it is updated.
  The advisor then reads what it can and prints a warning.
- The mod has not been run in the game yet. Its script follows the base game's
  own patterns, but the first live test will show whether every value prints.
- Mods usually disable achievements.
