import typer
from dotenv import load_dotenv

load_dotenv()

from .commands import ec2_inventory, login, region_loop, resolve_instance, ri_inventory, ssm, ssm_targets, version

app = typer.Typer(help="AWS CLI Tools")
app.command(name="ec2-inventory")(ec2_inventory)
app.command()(login)
app.command(name="region-loop")(region_loop)
app.command(name="resolve-instance")(resolve_instance)
app.command(name="ri-inventory")(ri_inventory)
app.command()(ssm)
app.command(name="ssm-targets")(ssm_targets)
app.command()(version)
