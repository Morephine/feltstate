# Security Policy

## Reporting a vulnerability

Please report security issues **privately** to the maintainer (e.g. via a GitHub
security advisory on this repository) rather than opening a public issue. You can
expect an acknowledgement and a discussion of next steps.

## Scope notes

feltstate is a local library with no server component and no bundled secrets.
Two things worth knowing when you embed it:

- **`LLMSource` sends conversation text to the endpoint you configure.** If you
  point it at a hosted API, the latest user message (and a short transcript) is
  transmitted there for measurement. Point it at a local endpoint if that data
  must not leave the machine.
- **Persisted state is personal.** `AffectState` files and `Canon` stores hold an
  agent's accumulated feelings and remembered facts about a user. Treat those
  files as user data; the bundled `.gitignore` keeps them out of version control.
