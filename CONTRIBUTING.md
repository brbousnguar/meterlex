# Contributing

Thanks for helping improve Meterlex. Contributions to log-format support,
pricing accuracy, privacy, documentation, accessibility, and the dashboard are
welcome.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
For vulnerabilities, use the private process in [SECURITY.md](SECURITY.md)
instead of opening an issue.

## Before you start

- Search existing issues and pull requests before proposing duplicate work.
- Open an issue first for large features, schema changes, or new data sources.
- Keep pull requests focused on one problem.
- Never include real prompts, session logs, project paths, account identifiers,
  invoices, credentials, or employer/client data.

## Development setup

Requirements:

- Python 3.12
- Node.js 22 and npm
- Docker Compose v2 for validating the container stack
- Maestro only when changing browser journeys

```sh
git clone https://github.com/YOUR-USERNAME/meterlex.git
cd meterlex
git checkout -b fix/short-description
cp .env.example .env

python -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt

cd frontend
npm ci
cd ..
```

Use `.venv\Scripts\Activate.ps1` instead on Windows PowerShell.

## Validation

Run the relevant checks before opening a pull request:

```sh
python -m pytest

cd frontend
npm audit --audit-level=high
npm run build
cd ..

docker compose config --quiet
```

Backend fixtures must be synthetic, deterministic, and small. Add a regression
test for parser, pricing, database, or API behavior changes. If navigation or a
critical user path changes, update the flows in `.maestro/` and run:

```sh
maestro test --platform web --headless .maestro
```

Maestro web support is beta, so explain any environment-specific limitation in
the pull request.

## Pull requests

Open pull requests against `main`. The description should state:

- the problem being solved;
- the approach and any tradeoffs;
- the commands used for validation;
- screenshots for visible UI changes;
- privacy or migration impact, when applicable.

All required GitHub Actions checks must pass. Maintainers may ask for changes
before merge. Small commits are welcome; the final merge strategy is determined
by the maintainer.

## Commit style

Use concise, imperative subjects, preferably following Conventional Commits:

```text
fix: handle truncated Codex token events
test: cover unknown model pricing
docs: explain Windows volume paths
```

## License

By submitting a contribution, you agree that it is licensed under the
repository's [MIT License](LICENSE).
