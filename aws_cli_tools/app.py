import typer
from dotenv import load_dotenv

load_dotenv()

from .commands import login, region_loop, resolve_instance, ssm, version

app = typer.Typer(help="AWS CLI Tools")
app.command()(login)
app.command(name="region-loop")(region_loop)
app.command(name="resolve-instance")(resolve_instance)
app.command()(ssm)
app.command()(version)
