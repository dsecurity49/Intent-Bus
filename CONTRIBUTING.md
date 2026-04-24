# Contributing to Intent Bus

Thanks for your interest in contributing. The easiest way to contribute is to write a new worker script.

## Adding a Worker Script

1. Fork the repo
2. Create your worker in the `examples/` folder (e.g. `examples/telegram_worker.sh`)
3. Follow the same pattern as `examples/discord_worker.sh`:
   - Check for `~/.apikey` at the top
   - Use `${TMPDIR:-/tmp}` for temp files
   - Check HTTP status codes, not response body
   - Fulfill the intent after execution
4. Test it against a live Intent Bus instance
5. Submit a Pull Request with a short description of what goal it listens for

## Worker Script Checklist

- [ ] Exits cleanly if `.apikey` is missing
- [ ] Uses `${TMPDIR:-/tmp}` for portability
- [ ] Handles 204 (empty queue) and non-200 errors separately
- [ ] Fulfills the intent after successful execution
- [ ] Has a comment at the top explaining usage

## Reporting Bugs

Open a GitHub Issue with:
- What you ran
- What you expected
- What actually happened

## Questions

Open a GitHub Issue or reach out via Dev.to: https://dev.to/d_security
