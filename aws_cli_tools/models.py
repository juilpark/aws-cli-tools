from typing import Optional, TypedDict


class InstanceMatch(TypedDict):
    region: str
    instance_id: str
    private_ip: Optional[str]
    public_ip: Optional[str]
    state: Optional[str]
    name: Optional[str]

