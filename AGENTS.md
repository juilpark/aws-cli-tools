# AGENTS.md

## Project Overview

- Repository name: `aws-cli-tools`
- Purpose: a small Typer-based Python CLI for AWS account workflows.
- Primary entrypoint: `main.py`
- Package entrypoint: `aws-cli-tools = "main:app"` in `pyproject.toml`

## Current Scope

The project currently exposes five CLI commands:

1. `login`
   - Uses `boto3` STS to request a temporary session token.
   - Writes temporary credentials into `~/.aws/credentials`.
   - Syncs profile settings from the source profile into `~/.aws/config`.
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
5. `version`
   - Prints the application version.

## Repository Layout

- `main.py`: all application logic lives here.
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

### Declared Python Version

`pyproject.toml` declares `requires-python = ">=3.14"`.

Be careful when changing this:

- The local machine used for inspection had `python3 3.9.6`.
- Running through `uv` worked for `--help`, but contributors without a matching interpreter may hit setup issues.
- If the project does not truly require 3.14-only features, consider lowering this requirement in a separate change.

## Common Commands

- Install dependencies: `uv sync`
- Show CLI help: `uv run python3 main.py --help`
- Show installed script help: `uv run aws-cli-tools --help`
- Run login flow: `uv run aws-cli-tools login`
- Run region loop: `uv run aws-cli-tools region-loop`
- Show version: `uv run aws-cli-tools version`

## Environment Notes

- `.env` is auto-loaded on startup via `load_dotenv()`.
- Expected variable from `.env-example`:
  - `AWS_MFA_SERIAL`
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

- No automated tests were found.
- No lint or formatting configuration was found.
- No CI configuration was found.
- The installed script entrypoint was not directly runnable via `uv run aws-cli-tools --help` in local verification.

## Suggested Next Improvements

- Add tests for `login`, especially profile overwrite and config sync behavior.
- Add tests for `region-loop` command assembly and confirmation flow.
- Add tests for `resolve-instance` cache behavior, ambiguity handling, and region fan-out.
- Add tests for `ssm`, especially command construction and AWS CLI availability checks.
- Consider validating that the `aws` CLI exists before entering the region loop.
- Consider clarifying or revisiting the Python `>=3.14` requirement.
- Investigate why `uv run aws-cli-tools --help` does not currently resolve the declared console script in the local environment.

## Verification Performed

- Inspected `main.py`, `pyproject.toml`, `.env-example`, and `.gitignore`.
- Verified CLI command registration with:
  - `uv run python3 main.py --help`
- Verified per-command help with:
  - `uv run python3 main.py login --help`
  - `uv run python3 main.py region-loop --help`
  - `uv run python3 main.py resolve-instance --help`
  - `uv run python3 main.py ssm --help`
- Attempted installed script help with:
  - `uv run aws-cli-tools --help` (failed: executable not found)

No code behavior beyond help output was executed, to avoid modifying real AWS credential files or running AWS commands.
