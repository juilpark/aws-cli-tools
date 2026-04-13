import re
from typing import Literal

TargetKind = Literal["instance_id", "ip", "name"]


def is_instance_id(value: str) -> bool:
    """Return True when the input looks like an EC2 instance id."""
    return re.fullmatch(r"i-[0-9a-f]+", value) is not None


def is_ipv4_address(value: str) -> bool:
    """Return True when the input looks like an IPv4 address."""
    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", value) is None:
        return False

    octets = value.split(".")
    return all(0 <= int(octet) <= 255 for octet in octets)


def classify_target(value: str) -> TargetKind:
    """Return the matching resolver kind for the provided target."""
    if is_instance_id(value):
        return "instance_id"
    if is_ipv4_address(value):
        return "ip"
    return "name"

