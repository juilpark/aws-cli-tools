# AGENTS.md

## Project Overview

- Repository name: `aws-cli-tools`
- Purpose: a small Typer-based Python CLI for AWS account workflows.
- Primary CLI app wiring: `aws_cli_tools/app.py`
- Compatibility entrypoint: `main.py`
- Package entrypoint: `aws-cli-tools = "aws_cli_tools.app:app"` in `pyproject.toml`

## Versioning Policy

- Current application version: `0.2.0`
- Do not bump the MAJOR version unless the user explicitly asks for it.
- Bump the MINOR version when existing functionality is changed in a meaningful way or new functionality is added.
- Bump the PATCH version for typo fixes, bug fixes, and internal refactors that do not intentionally expand the feature set.
- When the version changes, update all user-facing version declarations together, including `pyproject.toml`, `aws_cli_tools/constants.py`, and any lockfile metadata that records the project version.

## Current Scope

The project currently exposes five CLI commands:

1. `login`
   - Uses `boto3` STS to request a temporary session token.
   - Writes temporary credentials into `~/.aws/credentials`.
   - Syncs profile settings from the source profile into `~/.aws/config`.
   - When MFA is required and no `--token-code` is provided, shows a Rich-styled OTP notice box before collecting the code from the terminal prompt.
2. `region-loop`
   - Prompts for an `aws ...` CLI command.
   - Fetches all AWS regions through EC2.
   - Runs the command once per region with `subprocess.run(...)`.
3. `resolve-instance`
   - Resolves an EC2 instance by instance id, IP address, or `Name` tag value.
   - Searches across enabled regions and prints normalized instance metadata.
   - Caches single-match lookups under `~/.cache/aws-cli-tools/`.
4. `ssm`
   - Resolves an instance target using the same logic as `resolve-instance`.
   - Starts an `aws ssm start-session` command against the matched instance.
   - When interactive SSM target loading fails because AWS credentials are expired, attempts `login` once and retries the SSM flow.
5. `version`
   - Prints the application version.

## Repository Layout

- `main.py`: thin compatibility entrypoint that imports and runs the package app.
- `aws_cli_tools/`: application package.
- `aws_cli_tools/app.py`: Typer app construction and command registration.
- `aws_cli_tools/commands/`: one module per CLI command.
- `aws_cli_tools/aws_common.py`: shared AWS session, region, and config helpers.
- `aws_cli_tools/cache.py`: local cache helpers for resolver and region failures.
- `aws_cli_tools/instances.py`: EC2 instance resolution and normalization logic.
- `aws_cli_tools/ssm_targets.py`: SSM target discovery and command construction.
- `aws_cli_tools/ui.py`: Textual UI for interactive SSM target selection.
- `aws_cli_tools/output.py`: Rich/Typer output helpers.
- `pyproject.toml`: package metadata, Python requirement, dependencies, and CLI script registration.
- `uv.lock`: lockfile for `uv`.
- `.env-example`: sample environment variable for MFA serial configuration.
- `README.md`: Korean onboarding guide for first-time users.

## Tooling And Runtime

- Language: Python
- CLI framework: `typer`
- AWS SDK: `boto3`
- Env loading: `python-dotenv`
- Dependency manager: `uv`
- Coverage plugin: `pytest-cov`
- Packaging for local entrypoint installs: `[tool.uv] package = true`

### Declared Python Version

`pyproject.toml` declares `requires-python = ">=3.14"`.

Be careful when changing this:

- The local machine used for inspection had `python3 3.9.6`.
- Running through `uv` worked for `--help`, but contributors without a matching interpreter may hit setup issues.
- If the project does not truly require 3.14-only features, consider lowering this requirement in a separate change.

## Common Commands

- Install dependencies: `uv sync`
- Show CLI help: `uv run aws-cli-tools --help`
- Show compatibility wrapper help: `uv run python3 main.py --help`
- Show installed script help: `uv run aws-cli-tools --help`
- Run login flow: `uv run aws-cli-tools login`
- Run region loop: `uv run aws-cli-tools region-loop`
- Show version: `uv run aws-cli-tools version`
- Run all tests: `uv run pytest tests/`
- Check coverage: `uv run pytest --cov aws_cli_tools tests/`

## Environment Notes

- `.env` is auto-loaded on startup via `load_dotenv()`.
- Expected variable from `.env-example`:
  - `AWS_SOURCE_PROFILE`
  - `AWS_MFA_SERIAL`
  - `AWS_REGION_PRIORITY`
- `.env` is gitignored and should not be committed.

## External Side Effects

This repository is not self-contained at runtime. The CLI reads and writes user-level AWS files:

- `~/.aws/credentials`
- `~/.aws/config`

It may also write local cache files:

- `~/.cache/aws-cli-tools/resolve-instance.json`

Any change to `login` should be reviewed carefully because it can affect a developer's real AWS setup.

## Safety Notes For Future Agents

- Do not assume `target_profile=default` is harmless. The current implementation can overwrite existing long-lived credentials after only a warning.
- Do not make `region-loop` less restrictive without thinking through blast radius. It executes user-provided AWS CLI commands across every region.
- Keep in mind that `resolve-instance` and `ssm` use the hard-coded default profile for resolution and session startup.
- Keep interactive behavior intact unless the task explicitly asks for non-interactive support.
- If you add tests around `login`, prefer mocking `boto3`, filesystem writes, and AWS config parsing instead of touching real `~/.aws/*` files.
- If you add tests around `region-loop`, mock `subprocess.run`, `typer.prompt`, `typer.confirm`, and AWS region discovery.
- If you add tests around `resolve-instance` or `ssm`, mock region discovery, EC2 paginators, cache reads/writes, and `subprocess.run`.

## Missing Pieces

- Automated tests exist under `tests/`, including command coverage for `login`, `region-loop`, `resolve-instance`, `ssm`, cache helpers, region helpers, and target discovery.
- No lint or formatting configuration was found.
- No CI configuration was found.

## Suggested Next Improvements

- Expand tests around the new Rich OTP login notice so terminal-facing UX changes are covered more explicitly.
- Add higher-level tests for `ssm` re-authentication behavior beyond the current single retry path.
- Consider validating that the `aws` CLI exists before entering the region loop.
- Consider clarifying or revisiting the Python `>=3.14` requirement.

## Verification Performed

- Inspected `main.py`, `pyproject.toml`, `aws_cli_tools/`, `.env-example`, and `.gitignore`.
- Refreshed the local `uv` environment with:
  - `uv sync`
- Verified CLI command registration with:
  - `uv run aws-cli-tools --help`
  - `uv run python3 main.py --help`
- Verified per-command help with:
  - `uv run python3 main.py login --help`
  - `uv run python3 main.py region-loop --help`
  - `uv run python3 main.py resolve-instance --help`
  - `uv run python3 main.py ssm --help`
  - `uv run aws-cli-tools version`
- Verified targeted tests with:
  - `uv run pytest tests/test_commands_login.py tests/test_commands_ssm.py`
- Verified expanded full test suite and coverage with:
  - `uv run pytest tests/`
  - `uv run pytest --cov aws_cli_tools tests/`
  - Result observed during verification: `81 passed`, total coverage `93%`

No code behavior beyond help output was executed, to avoid modifying real AWS credential files or running AWS commands.
