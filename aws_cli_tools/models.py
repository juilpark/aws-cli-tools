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


class SavingsPlanInventory(TypedDict):
    captured_at_utc: Optional[str]
    savings_plan_id: Optional[str]
    savings_plan_arn: Optional[str]
    state: Optional[str]
    savings_plan_type: Optional[str]
    region: Optional[str]
    ec2_instance_family: Optional[str]
    product_types: Optional[str]
    payment_option: Optional[str]
    currency: Optional[str]
    commitment: Optional[str]
    upfront_payment_amount: Optional[str]
    recurring_payment_amount: Optional[str]
    term_duration_seconds: Optional[int]
    start: Optional[str]
    end: Optional[str]
    returnable_until: Optional[str]
    offering_id: Optional[str]
    description: Optional[str]
    tags_json: str


class CommitmentEligibility(TypedDict):
    captured_at_utc: Optional[str]
    service: str
    service_code: str
    resource_type: str
    region_scope: str
    commitment_types: str
    inventory_status: str
    api_operations: str
    notes: str


class CommitmentResourceInventory(TypedDict):
    captured_at_utc: Optional[str]
    service: str
    service_code: str
    resource_type: str
    resource_id: Optional[str]
    resource_name: Optional[str]
    region: str
    state: Optional[str]
    engine: Optional[str]
    instance_type: Optional[str]
    instance_count: Optional[int]
    capacity_summary: Optional[str]
    commitment_types: str
    collection_status: str
    eligibility_note: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]
    details_json: str
