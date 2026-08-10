import typer
from dotenv import load_dotenv

load_dotenv()

from .commands import login, region_loop, resolve_instance, ssm, ssm_targets, version

app = typer.Typer(help="AWS CLI Tools")
app.command()(login)
app.command(name="region-loop")(region_loop)
app.command(name="resolve-instance")(resolve_instance)
app.command()(ssm)
app.command(name="ssm-targets")(ssm_targets)
app.command()(version)
