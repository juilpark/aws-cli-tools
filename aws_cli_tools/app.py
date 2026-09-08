import typer
from dotenv import load_dotenv

load_dotenv()

from .commands import (
    commitment_inventory,
    ec2_inventory,
    login,
    region_loop,
    resolve_instance,
    ri_inventory,
    sp_inventory,
    ssm,
    ssm_targets,
    version,
)

app = typer.Typer(help="AWS CLI Tools")

inventory_app = typer.Typer(
    help="Export EC2, RI, Savings Plans, and commitment inventory CSV files.",
    no_args_is_help=True,
)
inventory_app.command(name="ec2")(ec2_inventory)
inventory_app.command(name="commitment")(commitment_inventory)
inventory_app.command(name="ri")(ri_inventory)
inventory_app.command(name="sp")(sp_inventory)
app.add_typer(inventory_app, name="inventory")

app.command()(login)
app.command(name="region-loop")(region_loop)
app.command(name="resolve-instance")(resolve_instance)
app.command()(ssm)
app.command(name="ssm-targets")(ssm_targets)
app.command()(version)
