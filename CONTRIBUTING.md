# Contributing

Thanks for helping improve the tracker. Changes should keep the project useful as a
small, auditable automation rather than turning it into an exhaustive fare scraper.

## Before opening a change

```bash
python -m unittest discover -s tests -v
python -m compileall -q tracker scripts
```

Do not run live searches in tests. Use `DemoProvider` or mocked HTTP responses for
provider behavior. Never add a real Telegram token, chat ID, cookie, captured search
response containing personal data or local state database to a commit.

## Good changes

- Add a regression test for every provider or alert-policy fix.
- Keep direct and connecting flight histories separate.
- Preserve fail-closed behavior for unknown prices, wrong currency and invalid
  itineraries.
- Keep workflow actions pinned to immutable commits.
- Update `README.md`, `DEPLOYMENT.md` or `docs/ARCHITECTURE.md` when behavior changes.

## Pull requests

Describe the user-visible behavior, tests run and any change to request volume or
state compatibility. Keep commits focused. The `Tests` workflow must pass before a
change is merged.
