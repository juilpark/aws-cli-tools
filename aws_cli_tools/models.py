from typing import Optional, TypedDict


class InstanceMatch(TypedDict):
    region: str
    instance_id: str
    private_ip: Optional[str]
    public_ip: Optional[str]
    state: Optional[str]
    name: Optional[str]


class Ec2InstanceInventory(TypedDict):
    captured_at_utc: Optional[str]
    region: str
    availability_zone: Optional[str]
    instance_id: str
    name: Optional[str]
    image_id: Optional[str]
    instance_type: Optional[str]
    state: Optional[str]
    platform: Optional[str]
    platform_details: Optional[str]
    architecture: Optional[str]
    tenancy: Optional[str]
    instance_lifecycle: Optional[str]
    spot_instance: bool
    launch_time: Optional[str]
    usage_operation: Optional[str]
    usage_operation_update_time: Optional[str]
    vcpu_core_count: Optional[int]
    threads_per_core: Optional[int]
    hypervisor: Optional[str]
    virtualization_type: Optional[str]
    ebs_optimized: Optional[bool]
    root_device_type: Optional[str]
    vpc_id: Optional[str]
    subnet_id: Optional[str]
    private_ip: Optional[str]
    public_ip: Optional[str]
    placement_group_name: Optional[str]
    reservation_id: Optional[str]
    owner_id: Optional[str]
    tags_json: str


class ReservedInstanceInventory(TypedDict):
    captured_at_utc: Optional[str]
    region: str
    reserved_instances_id: Optional[str]
    instance_type: Optional[str]
    instance_count: Optional[int]
    state: Optional[str]
    scope: Optional[str]
    availability_zone: Optional[str]
    availability_zone_id: Optional[str]
    instance_tenancy: Optional[str]
    product_description: Optional[str]
    offering_class: Optional[str]
    offering_type: Optional[str]
    duration_seconds: Optional[int]
    start: Optional[str]
    end: Optional[str]
    currency_code: Optional[str]
    fixed_price: Optional[float]
    usage_price: Optional[float]
    recurring_charge_amount: Optional[float]
    recurring_charge_frequency: Optional[str]
    recurring_charges_json: str
    tags_json: str
