import typer

from ..constants import APP_NAME, VERSION


def version() -> None:
    """
    Show version info.
    """
    typer.echo(f"{APP_NAME} version {VERSION}")

