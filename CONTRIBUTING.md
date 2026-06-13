# Contributing to ClaudeReplay

Thanks for helping make Claude sessions replayable. The bar is simple:

1. **Zero dependencies.** ClaudeReplay is one standard-library Python file and a
   self-contained HTML player. Don't add a runtime dependency.
2. **Keep `--selftest` green, and grow it.** Anything you add should come with a
   check inside `run_selftest()`. Run it before you push:
   ```bash
   python claudereplay.py --selftest
   ```
3. **Don't break the single-file output.** The HTML replay must stay portable —
   no external scripts, fonts, or network calls.
4. **Privacy is a feature.** No telemetry, no network. If you touch parsing,
   make sure `--redact` still scrubs what it should.

## Dev loop

```bash
python claudereplay.py --selftest          # tests
python claudereplay.py list                # against your own sessions
python claudereplay.py open last           # eyeball a real replay
```

Verify the player renders by opening the generated HTML, or headless:

```bash
chrome --headless=new --dump-dom file:///abs/path/to/out.html | grep -c 'class="ev '
```

## Ideas worth a PR

See the roadmap in [`docs/STRATEGY.md`](docs/STRATEGY.md): session diff,
subagent inlining, a `gallery` index, GIF/MP4 export, cross-session search.

Open an issue first for anything large so we can agree on the shape.
