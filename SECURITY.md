# Security notes

## Secrets

The Telegram bot token and chat ID belong in GitHub Actions repository secrets named
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. They must never appear in source files,
issues, workflow output, test fixtures or state commits.

The public `tracker-state` branch contains route, fare and alert history. It is not a
secret store. Do not put private travel details or credentials into that branch.

## Reporting a problem

For a suspected credential exposure, rotate the affected credential first, then open
a private GitHub security report or contact the repository owner. For ordinary bugs,
use a public issue with secrets and personal data removed.

The flight source is unofficial. A provider response or fare link should be treated
as untrusted input and checked before booking.
