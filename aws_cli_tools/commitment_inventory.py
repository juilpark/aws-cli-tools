import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from botocore.exceptions import BotoCoreError, ClientError

from .aws_common import build_boto_config, get_default_session, get_enabled_regions
from .constants import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_PROFILE,
    DEFAULT_READ_TIMEOUT_SECONDS,
)
from .csv_export import write_timestamped_csv
from .ec2_inventory import list_instances_in_region
from .errors import AwsOperationError
from .models import CommitmentEligibility, CommitmentResourceInventory


ELIGIBILITY_CSV_FIELDS = [
    "captured_at_utc",
    "service",
    "service_code",
    "resource_type",
    "region_scope",
    "commitment_types",
    "inventory_status",
    "api_operations",
    "notes",
]

RESOURCE_CSV_FIELDS = [
    "captured_at_utc",
    "service",
    "service_code",
    "resource_type",
    "resource_id",
    "resource_name",
    "region",
    "state",
    "engine",
    "instance_type",
    "instance_count",
    "capacity_summary",
    "commitment_types",
    "collection_status",
    "eligibility_note",
    "error_code",
    "error_message",
    "details_json",
]


@dataclass(frozen=True)
class CommitmentServiceSpec:
    service: str
    service_code: str
    resource_type: str
    region_scope: str
    commitment_types: str
    api_operations: str
    notes: str


COMMITMENT_SERVICE_SPECS = (
    CommitmentServiceSpec(
        service="Amazon EC2",
        service_code="ec2",
        resource_type="EC2 instance",
        region_scope="regional",
        commitment_types="Reserved Instance;Compute Savings Plan;EC2 Instance Savings Plan",
        api_operations="DescribeInstances",
        notes="Uses the existing EC2 instance inventory. Spot usage is not eligible for RI or Savings Plans.",
    ),
    CommitmentServiceSpec(
        service="AWS Fargate on Amazon ECS",
        service_code="ecs",
        resource_type="ECS Fargate task",
        region_scope="regional",
        commitment_types="Compute Savings Plan",
        api_operations="ListClusters;ListTasks;DescribeTasks",
        notes="Captures running Fargate tasks. Fargate Spot tasks are marked as not eligible.",
    ),
    CommitmentServiceSpec(
        service="AWS Fargate on Amazon EKS",
        service_code="eks",
        resource_type="EKS Fargate profile",
        region_scope="regional",
        commitment_types="Compute Savings Plan",
        api_operations="ListClusters;ListFargateProfiles;DescribeFargateProfile",
        notes="Fargate profiles show possible EKS Fargate usage; pod-level usage is not exposed by the EKS API.",
    ),
    CommitmentServiceSpec(
        service="AWS Lambda",
        service_code="lambda",
        resource_type="Lambda function",
        region_scope="regional",
        commitment_types="Compute Savings Plan",
        api_operations="ListFunctions",
        notes="Function rows are an inventory snapshot; Savings Plan coverage depends on actual invocation compute usage.",
    ),
    CommitmentServiceSpec(
        service="Amazon SageMaker AI",
        service_code="sagemaker",
        resource_type="SageMaker endpoint/notebook/job",
        region_scope="regional",
        commitment_types="SageMaker AI Savings Plan",
        api_operations="ListEndpoints;DescribeEndpoint;ListNotebookInstances;ListTrainingJobs;ListProcessingJobs;ListTransformJobs",
        notes="Captures endpoints, notebooks, and active jobs. Historical usage should be checked in Cost Explorer.",
    ),
    CommitmentServiceSpec(
        service="Amazon RDS and Aurora",
        service_code="rds",
        resource_type="RDS/Aurora DB instance or cluster",
        region_scope="regional",
        commitment_types="Reserved Instance;Database Savings Plan",
        api_operations="DescribeDBInstances;DescribeDBClusters",
        notes="Aurora is represented by the RDS engine and DB cluster/instance records.",
    ),
    CommitmentServiceSpec(
        service="Amazon Aurora DSQL",
        service_code="dsql",
        resource_type="Aurora DSQL cluster",
        region_scope="regional",
        commitment_types="Database Savings Plan",
        api_operations="ListClusters;GetCluster",
        notes="Aurora DSQL is serverless; review DPU usage rather than provisioned instance capacity.",
    ),
    CommitmentServiceSpec(
        service="Amazon DynamoDB",
        service_code="dynamodb",
        resource_type="DynamoDB table/provisioned capacity",
        region_scope="regional",
        commitment_types="Reserved Capacity;Database Savings Plan",
        api_operations="ListTables;DescribeTable",
        notes="Captures table billing mode and provisioned/on-demand capacity for commitment review.",
    ),
    CommitmentServiceSpec(
        service="Amazon ElastiCache",
        service_code="elasticache",
        resource_type="ElastiCache cache cluster/replication group",
        region_scope="regional",
        commitment_types="Reserved Node;Database Savings Plan (Valkey)",
        api_operations="DescribeCacheClusters;DescribeReplicationGroups;DescribeServerlessCaches",
        notes="Database Savings Plans are currently listed for ElastiCache for Valkey; reserved nodes also cover node-based cache offerings.",
    ),
    CommitmentServiceSpec(
        service="Amazon OpenSearch Service",
        service_code="opensearch",
        resource_type="OpenSearch domain",
        region_scope="regional",
        commitment_types="Reserved Instance;Database Savings Plan",
        api_operations="ListDomainNames;DescribeDomains",
        notes="Captures provisioned OpenSearch domains and their node configuration.",
    ),
    CommitmentServiceSpec(
        service="Amazon OpenSearch Serverless",
        service_code="opensearchserverless",
        resource_type="OpenSearch Serverless collection",
        region_scope="regional",
        commitment_types="Database Savings Plan",
        api_operations="ListCollections",
        notes="Captures serverless collections; OCU consumption must be checked with billing usage data.",
    ),
    CommitmentServiceSpec(
        service="Amazon Redshift",
        service_code="redshift",
        resource_type="Redshift cluster",
        region_scope="regional",
        commitment_types="Reserved Node",
        api_operations="DescribeClusters",
        notes="Redshift reservations are sold as reserved nodes rather than EC2 Reserved Instances.",
    ),
    CommitmentServiceSpec(
        service="Amazon MemoryDB",
        service_code="memorydb",
        resource_type="MemoryDB cluster",
        region_scope="regional",
        commitment_types="Reserved Node",
        api_operations="DescribeClusters",
        notes="MemoryDB is included in Cost Explorer reservation recommendations.",
    ),
    CommitmentServiceSpec(
        service="Amazon DocumentDB",
        service_code="docdb",
        resource_type="DocumentDB instance or cluster",
        region_scope="regional",
        commitment_types="Database Savings Plan",
        api_operations="DescribeDBInstances;DescribeDBClusters",
        notes="Captures provisioned DocumentDB resources for Database Savings Plan review.",
    ),
    CommitmentServiceSpec(
        service="Amazon Timestream",
        service_code="timestream-write",
        resource_type="Timestream database/table",
        region_scope="regional",
        commitment_types="Database Savings Plan",
        api_operations="ListDatabases;ListTables",
        notes="Captures Timestream databases and tables; Savings Plan coverage depends on actual usage.",
    ),
    CommitmentServiceSpec(
        service="Amazon Neptune",
        service_code="neptune",
        resource_type="Neptune instance or cluster",
        region_scope="regional",
        commitment_types="Database Savings Plan",
        api_operations="DescribeDBInstances;DescribeDBClusters",
        notes="Captures provisioned Neptune resources for Database Savings Plan review.",
    ),
    CommitmentServiceSpec(
        service="Amazon Neptune Analytics",
        service_code="neptune-graph",
        resource_type="Neptune Analytics graph",
        region_scope="regional",
        commitment_types="Database Savings Plan",
        api_operations="ListGraphs",
        notes="Captures Neptune Analytics graphs; actual graph usage should be checked in billing data.",
    ),
    CommitmentServiceSpec(
        service="Amazon Keyspaces",
        service_code="keyspaces",
        resource_type="Keyspaces keyspace/table",
        region_scope="regional",
        commitment_types="Database Savings Plan",
        api_operations="ListKeyspaces;ListTables",
        notes="Captures keyspaces and tables; Savings Plan coverage depends on actual usage.",
    ),
    CommitmentServiceSpec(
        service="AWS Database Migration Service",
        service_code="dms",
        resource_type="DMS replication instance/configuration",
        region_scope="regional",
        commitment_types="Database Savings Plan",
        api_operations="DescribeReplicationInstances;DescribeReplicationConfigs",
        notes="Captures provisioned and serverless-style DMS replication resources.",
    ),
)


def build_commitment_eligibility() -> List[CommitmentEligibility]:
    """Return the explicit RI/Savings Plans service coverage matrix."""
    return [
        {
            "captured_at_utc": None,
            "service": spec.service,
            "service_code": spec.service_code,
            "resource_type": spec.resource_type,
            "region_scope": spec.region_scope,
            "commitment_types": spec.commitment_types,
            "inventory_status": "supported",
            "api_operations": spec.api_operations,
            "notes": spec.notes,
        }
        for spec in COMMITMENT_SERVICE_SPECS
    ]


def _json(details: object) -> str:
    return json.dumps(details if details is not None else {}, ensure_ascii=False, sort_keys=True, default=str)


def _error_fields(error: Exception) -> tuple[str, str]:
    original_error = error.error if isinstance(error, AwsOperationError) else error
    if isinstance(original_error, ClientError):
        error_info = original_error.response.get("Error", {})
        return str(error_info.get("Code", "ClientError")), str(error_info.get("Message", original_error))
    return type(original_error).__name__, str(original_error)


def _resource_row(
    spec: CommitmentServiceSpec,
    region: str,
    *,
    resource_id: Optional[str] = None,
    resource_name: Optional[str] = None,
    resource_type: Optional[str] = None,
    state: Optional[str] = None,
    engine: Optional[str] = None,
    instance_type: Optional[str] = None,
    instance_count: Optional[int] = None,
    capacity_summary: Optional[str] = None,
    commitment_types: Optional[str] = None,
    collection_status: str = "ok",
    eligibility_note: Optional[str] = None,
    error: Optional[Exception] = None,
    details: object = None,
) -> CommitmentResourceInventory:
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    if error is not None:
        error_code, error_message = _error_fields(error)

    return {
        "captured_at_utc": None,
        "service": spec.service,
        "service_code": spec.service_code,
        "resource_type": resource_type or spec.resource_type,
        "resource_id": resource_id,
        "resource_name": resource_name,
        "region": region,
        "state": state,
        "engine": engine,
        "instance_type": instance_type,
        "instance_count": instance_count,
        "capacity_summary": capacity_summary,
        "commitment_types": commitment_types if commitment_types is not None else spec.commitment_types,
        "collection_status": collection_status,
        "eligibility_note": eligibility_note,
        "error_code": error_code,
        "error_message": error_message,
        "details_json": _json(details),
    }


def _error_row(
    spec: CommitmentServiceSpec,
    region: str,
    error: Exception,
    *,
    resource_id: Optional[str] = None,
    resource_name: Optional[str] = None,
    resource_type: Optional[str] = None,
    details: object = None,
) -> CommitmentResourceInventory:
    return _resource_row(
        spec,
        region,
        resource_id=resource_id,
        resource_name=resource_name,
        resource_type=resource_type,
        collection_status="error",
        error=error,
        details=details,
    )


def _client(service_code: str, region: str, connect_timeout: int, read_timeout: int, max_attempts: int) -> Any:
    session = get_default_session()
    return session.client(
        service_code,
        region_name=region,
        config=build_boto_config(
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_attempts=max_attempts,
        ),
    )


def _paginate_values(client: Any, operation: str, result_key: str, **kwargs: object) -> List[Any]:
    paginator = client.get_paginator(operation)
    values: List[Any] = []
    for page in paginator.paginate(**kwargs):
        items = page.get(result_key, [])
        if isinstance(items, list):
            values.extend(items)
    return values


def _paginate_items(client: Any, operation: str, result_key: str, **kwargs: object) -> List[Dict[str, Any]]:
    return [item for item in _paginate_values(client, operation, result_key, **kwargs) if isinstance(item, dict)]


def _chunks(values: Sequence[Any], size: int) -> List[List[Any]]:
    return [list(values[index:index + size]) for index in range(0, len(values), size)]


def _list_timestream_items(
    client: Any,
    operation: str,
    result_key: str,
    **kwargs: object,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    next_token: Optional[str] = None
    while True:
        request = dict(kwargs)
        request["MaxResults"] = 100
        if next_token:
            request["NextToken"] = next_token
        response = getattr(client, operation)(**request)
        values = response.get(result_key, [])
        if isinstance(values, list):
            items.extend(item for item in values if isinstance(item, dict))
        next_token = response.get("NextToken")
        if not isinstance(next_token, str) or not next_token:
            return items


def _list_lower_camel_items(
    client: Any,
    operation: str,
    result_key: str,
    **kwargs: object,
) -> List[Dict[str, Any]]:
    """List APIs that expose lower-camel-case tokens without a botocore paginator."""
    items: List[Dict[str, Any]] = []
    next_token: Optional[str] = None
    while True:
        request = dict(kwargs)
        request["maxResults"] = 100
        if next_token:
            request["nextToken"] = next_token
        response = getattr(client, operation)(**request)
        values = response.get(result_key, [])
        if isinstance(values, list):
            items.extend(item for item in values if isinstance(item, dict))
        next_token = response.get("nextToken")
        if not isinstance(next_token, str) or not next_token:
            return items


def _collect_ec2(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    records = list_instances_in_region(region, connect_timeout, read_timeout, max_attempts)
    rows: List[CommitmentResourceInventory] = []
    for record in records:
        is_spot = bool(record.get("spot_instance"))
        rows.append(
            _resource_row(
                spec,
                region,
                resource_id=record.get("instance_id"),
                resource_name=record.get("name"),
                state=record.get("state"),
                engine=record.get("platform_details"),
                instance_type=record.get("instance_type"),
                instance_count=1,
                capacity_summary=(
                    f"vcpu_core_count={record.get('vcpu_core_count')};"
                    f"threads_per_core={record.get('threads_per_core')}"
                ),
                commitment_types="" if is_spot else None,
                eligibility_note="Spot usage is not eligible for Reserved Instances or Savings Plans." if is_spot else None,
                details={
                    "availability_zone": record.get("availability_zone"),
                    "instance_lifecycle": record.get("instance_lifecycle"),
                    "tenancy": record.get("tenancy"),
                    "tags_json": record.get("tags_json"),
                },
            )
        )
    return rows


def _collect_ecs(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    client = _client("ecs", region, connect_timeout, read_timeout, max_attempts)
    rows: List[CommitmentResourceInventory] = []
    for cluster_arn in _paginate_values(client, "list_clusters", "clusterArns"):
        if not isinstance(cluster_arn, str):
            continue
        task_arns = _paginate_values(
            client,
            "list_tasks",
            "taskArns",
            cluster=cluster_arn,
            desiredStatus="RUNNING",
            launchType="FARGATE",
        )
        for task_arn_chunk in _chunks([arn for arn in task_arns if isinstance(arn, str)], 100):
            response = client.describe_tasks(cluster=cluster_arn, tasks=task_arn_chunk)
            for task in response.get("tasks", []):
                if not isinstance(task, dict):
                    continue
                capacity_provider = task.get("capacityProviderName")
                is_fargate_spot = capacity_provider == "FARGATE_SPOT"
                rows.append(
                    _resource_row(
                        spec,
                        region,
                        resource_id=task.get("taskArn"),
                        resource_name=task.get("group") or task.get("taskArn"),
                        state=task.get("lastStatus"),
                        instance_count=1,
                        capacity_summary=f"cpu={task.get('cpu')};memory={task.get('memory')}",
                        commitment_types="" if is_fargate_spot else None,
                        eligibility_note=(
                            "Fargate Spot usage is not eligible for Savings Plans."
                            if is_fargate_spot
                            else None
                        ),
                        details={
                            "cluster_arn": cluster_arn,
                            "task_definition_arn": task.get("taskDefinitionArn"),
                            "capacity_provider_name": capacity_provider,
                            "launch_type": task.get("launchType"),
                            "desired_status": task.get("desiredStatus"),
                            "started_at": task.get("startedAt"),
                        },
                    )
                )
    return rows


def _collect_eks(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    client = _client("eks", region, connect_timeout, read_timeout, max_attempts)
    rows: List[CommitmentResourceInventory] = []
    for cluster_name in _paginate_values(client, "list_clusters", "clusters"):
        if not isinstance(cluster_name, str):
            continue
        profile_names = _paginate_values(
            client,
            "list_fargate_profiles",
            "fargateProfileNames",
            clusterName=cluster_name,
        )
        for profile_name in profile_names:
            if not isinstance(profile_name, str):
                continue
            response = client.describe_fargate_profile(
                clusterName=cluster_name,
                fargateProfileName=profile_name,
            )
            profile = response.get("fargateProfile", {})
            if not isinstance(profile, dict):
                continue
            rows.append(
                _resource_row(
                    spec,
                    region,
                    resource_id=profile.get("fargateProfileArn") or profile_name,
                    resource_name=profile_name,
                    state=profile.get("status"),
                    details={
                        "cluster_name": cluster_name,
                        "selectors": profile.get("selectors"),
                        "subnets": profile.get("subnets"),
                        "pod_execution_role_arn": profile.get("podExecutionRoleArn"),
                    },
                )
            )
    return rows


def _collect_lambda(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    client = _client("lambda", region, connect_timeout, read_timeout, max_attempts)
    rows: List[CommitmentResourceInventory] = []
    for function in _paginate_items(client, "list_functions", "Functions"):
        rows.append(
            _resource_row(
                spec,
                region,
                resource_id=function.get("FunctionArn"),
                resource_name=function.get("FunctionName"),
                state=function.get("State"),
                capacity_summary=f"memory_mb={function.get('MemorySize')};timeout_seconds={function.get('Timeout')}",
                details={
                    "runtime": function.get("Runtime"),
                    "architectures": function.get("Architectures"),
                    "package_type": function.get("PackageType"),
                    "last_modified": function.get("LastModified"),
                    "code_size": function.get("CodeSize"),
                    "vpc_config": function.get("VpcConfig"),
                    "ephemeral_storage": function.get("EphemeralStorage"),
                },
            )
        )
    return rows


def _collect_sagemaker(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    client = _client("sagemaker", region, connect_timeout, read_timeout, max_attempts)
    rows: List[CommitmentResourceInventory] = []

    for endpoint_summary in _paginate_items(client, "list_endpoints", "Endpoints"):
        endpoint_name = endpoint_summary.get("EndpointName")
        endpoint_id = endpoint_summary.get("EndpointArn") or endpoint_name
        try:
            endpoint = client.describe_endpoint(EndpointName=endpoint_name)
        except (BotoCoreError, ClientError) as error:
            rows.append(
                _error_row(
                    spec,
                    region,
                    error,
                    resource_id=endpoint_id,
                    resource_name=endpoint_name,
                    resource_type="SageMaker endpoint",
                )
            )
            continue

        variants = endpoint.get("ProductionVariants", [])
        if not isinstance(variants, list) or not variants:
            rows.append(
                _resource_row(
                    spec,
                    region,
                    resource_id=endpoint_id,
                    resource_name=endpoint_name,
                    resource_type="SageMaker endpoint",
                    state=endpoint.get("EndpointStatus"),
                    details={"endpoint_config_name": endpoint.get("EndpointConfigName")},
                )
            )
            continue

        for variant in variants:
            if not isinstance(variant, dict):
                continue
            variant_name = variant.get("VariantName")
            rows.append(
                _resource_row(
                    spec,
                    region,
                    resource_id=f"{endpoint_id}#{variant_name}",
                    resource_name=f"{endpoint_name}/{variant_name}",
                    resource_type="SageMaker endpoint variant",
                    state=endpoint.get("EndpointStatus"),
                    instance_type=variant.get("InstanceType"),
                    instance_count=variant.get("CurrentInstanceCount") or variant.get("InitialInstanceCount"),
                    capacity_summary=(
                        f"current_weight={variant.get('CurrentWeight')};"
                        f"initial_weight={variant.get('InitialVariantWeight')}"
                    ),
                    details={
                        "endpoint_config_name": endpoint.get("EndpointConfigName"),
                        "variant_name": variant_name,
                        "serverless_config": variant.get("ServerlessConfig"),
                    },
                )
            )

    for notebook in _paginate_items(client, "list_notebook_instances", "NotebookInstances"):
        rows.append(
            _resource_row(
                spec,
                region,
                resource_id=notebook.get("NotebookInstanceArn"),
                resource_name=notebook.get("NotebookInstanceName"),
                resource_type="SageMaker notebook instance",
                state=notebook.get("NotebookInstanceStatus"),
                instance_type=notebook.get("InstanceType"),
                instance_count=1,
                details={
                    "creation_time": notebook.get("CreationTime"),
                    "last_modified_time": notebook.get("LastModifiedTime"),
                    "url": notebook.get("Url"),
                },
            )
        )

    for operation, result_key, resource_type, id_key, name_key, status_key in (
        ("list_training_jobs", "TrainingJobSummaries", "SageMaker training job", "TrainingJobArn", "TrainingJobName", "TrainingJobStatus"),
        ("list_processing_jobs", "ProcessingJobSummaries", "SageMaker processing job", "ProcessingJobArn", "ProcessingJobName", "ProcessingJobStatus"),
        ("list_transform_jobs", "TransformJobSummaries", "SageMaker transform job", "TransformJobArn", "TransformJobName", "TransformJobStatus"),
    ):
        for job in _paginate_items(client, operation, result_key, StatusEquals="InProgress"):
            rows.append(
                _resource_row(
                    spec,
                    region,
                    resource_id=job.get(id_key),
                    resource_name=job.get(name_key),
                    resource_type=resource_type,
                    state=job.get(status_key),
                    details=job,
                )
            )
    return rows


def _collect_rds(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    client = _client("rds", region, connect_timeout, read_timeout, max_attempts)
    rows: List[CommitmentResourceInventory] = []
    for instance in _paginate_items(client, "describe_db_instances", "DBInstances"):
        rows.append(
            _resource_row(
                spec,
                region,
                resource_id=instance.get("DBInstanceArn") or instance.get("DbiResourceId"),
                resource_name=instance.get("DBInstanceIdentifier"),
                resource_type="RDS/Aurora DB instance",
                state=instance.get("DBInstanceStatus"),
                engine=instance.get("Engine"),
                instance_type=instance.get("DBInstanceClass"),
                instance_count=1,
                capacity_summary=f"allocated_storage_gb={instance.get('AllocatedStorage')}",
                details={
                    "db_cluster_identifier": instance.get("DBClusterIdentifier"),
                    "availability_zone": instance.get("AvailabilityZone"),
                    "multi_az": instance.get("MultiAZ"),
                    "storage_type": instance.get("StorageType"),
                    "license_model": instance.get("LicenseModel"),
                    "promotion_tier": instance.get("PromotionTier"),
                },
            )
        )
    for cluster in _paginate_items(client, "describe_db_clusters", "DBClusters"):
        scaling = cluster.get("ServerlessV2ScalingConfiguration")
        rows.append(
            _resource_row(
                spec,
                region,
                resource_id=cluster.get("DBClusterArn") or cluster.get("DBClusterIdentifier"),
                resource_name=cluster.get("DBClusterIdentifier"),
                resource_type="RDS/Aurora DB cluster",
                state=cluster.get("Status"),
                engine=cluster.get("Engine"),
                commitment_types="Database Savings Plan",
                capacity_summary=_json(scaling) if scaling else None,
                details={
                    "engine_version": cluster.get("EngineVersion"),
                    "engine_mode": cluster.get("EngineMode"),
                    "availability_zones": cluster.get("AvailabilityZones"),
                    "serverless_v2_scaling_configuration": scaling,
                    "storage_type": cluster.get("StorageType"),
                },
            )
        )
    return rows


def _collect_dynamodb(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    client = _client("dynamodb", region, connect_timeout, read_timeout, max_attempts)
    rows: List[CommitmentResourceInventory] = []
    table_names = _paginate_values(client, "list_tables", "TableNames")
    for table_name in table_names:
        if not isinstance(table_name, str):
            continue
        try:
            table = client.describe_table(TableName=table_name).get("Table", {})
        except (BotoCoreError, ClientError) as error:
            rows.append(
                _error_row(spec, region, error, resource_name=table_name, resource_type="DynamoDB table")
            )
            continue
        if not isinstance(table, dict):
            continue
        billing_summary = table.get("BillingModeSummary") or {}
        provisioned = table.get("ProvisionedThroughput") or {}
        on_demand = table.get("OnDemandThroughput") or {}
        billing_mode = billing_summary.get("BillingMode")
        rows.append(
            _resource_row(
                spec,
                region,
                resource_id=table.get("TableArn") or table_name,
                resource_name=table.get("TableName") or table_name,
                resource_type="DynamoDB table/provisioned capacity",
                state=table.get("TableStatus"),
                capacity_summary=(
                    f"billing_mode={billing_mode};"
                    f"read_capacity={provisioned.get('ReadCapacityUnits')};"
                    f"write_capacity={provisioned.get('WriteCapacityUnits')};"
                    f"max_read_capacity={on_demand.get('MaxReadRequestUnits')};"
                    f"max_write_capacity={on_demand.get('MaxWriteRequestUnits')}"
                ),
                details={
                    "table_class": table.get("TableClass"),
                    "item_count": table.get("ItemCount"),
                    "table_size_bytes": table.get("TableSizeBytes"),
                    "billing_mode_summary": billing_summary,
                    "provisioned_throughput": provisioned,
                    "on_demand_throughput": on_demand,
                },
            )
        )
    return rows


def _collect_elasticache(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    client = _client("elasticache", region, connect_timeout, read_timeout, max_attempts)
    rows: List[CommitmentResourceInventory] = []
    for cluster in _paginate_items(client, "describe_cache_clusters", "CacheClusters", ShowCacheNodeInfo=True):
        engine = cluster.get("Engine")
        database_sp_types = "Reserved Node;Database Savings Plan" if engine == "valkey" else "Reserved Node"
        database_sp_note = None if engine == "valkey" else "Database Savings Plan coverage is currently listed for ElastiCache for Valkey."
        rows.append(
            _resource_row(
                spec,
                region,
                resource_id=cluster.get("ARN") or cluster.get("CacheClusterId"),
                resource_name=cluster.get("CacheClusterId"),
                resource_type="ElastiCache cache cluster",
                state=cluster.get("CacheClusterStatus"),
                engine=engine,
                instance_type=cluster.get("CacheNodeType"),
                instance_count=cluster.get("NumCacheNodes"),
                commitment_types=database_sp_types,
                eligibility_note=database_sp_note,
                details={
                    "engine_version": cluster.get("EngineVersion"),
                    "preferred_availability_zone": cluster.get("PreferredAvailabilityZone"),
                    "replication_group_id": cluster.get("ReplicationGroupId"),
                },
            )
        )
    for group in _paginate_items(client, "describe_replication_groups", "ReplicationGroups"):
        engine = group.get("Engine")
        database_sp_types = "Reserved Node;Database Savings Plan" if engine == "valkey" else "Reserved Node"
        database_sp_note = None if engine == "valkey" else "Database Savings Plan coverage is currently listed for ElastiCache for Valkey."
        rows.append(
            _resource_row(
                spec,
                region,
                resource_id=group.get("ARN") or group.get("ReplicationGroupId"),
                resource_name=group.get("ReplicationGroupId"),
                resource_type="ElastiCache replication group",
                state=group.get("Status"),
                engine=engine,
                instance_type=group.get("CacheNodeType"),
                instance_count=group.get("NumCacheClusters"),
                commitment_types=database_sp_types,
                eligibility_note=database_sp_note,
                details={
                    "engine_version": group.get("EngineVersion"),
                    "automatic_failover": group.get("AutomaticFailover"),
                    "multi_az": group.get("MultiAZ"),
                    "member_count": len(group.get("ReplicationGroupMembers", [])),
                },
            )
        )
    for cache in _paginate_items(client, "describe_serverless_caches", "ServerlessCaches"):
        rows.append(
            _resource_row(
                spec,
                region,
                resource_id=cache.get("ARN") or cache.get("ServerlessCacheName"),
                resource_name=cache.get("ServerlessCacheName"),
                resource_type="ElastiCache serverless cache",
                state=cache.get("Status"),
                engine=cache.get("Engine"),
                commitment_types="Database Savings Plan",
                details={
                    "full_engine_version": cache.get("FullEngineVersion"),
                    "cache_usage_limits": cache.get("CacheUsageLimits"),
                    "create_time": cache.get("CreateTime"),
                },
            )
        )
    return rows


def _collect_opensearch(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    client = _client("opensearch", region, connect_timeout, read_timeout, max_attempts)
    names_response = client.list_domain_names()
    domain_names = names_response.get("DomainNames", [])
    names = [item.get("DomainName") for item in domain_names if isinstance(item, dict) and item.get("DomainName")]
    rows: List[CommitmentResourceInventory] = []
    for name_chunk in _chunks(names, 5):
        response = client.describe_domains(DomainNames=name_chunk)
        for domain in response.get("DomainStatusList", []):
            if not isinstance(domain, dict):
                continue
            cluster = domain.get("ClusterConfig") or {}
            rows.append(
                _resource_row(
                    spec,
                    region,
                    resource_id=domain.get("ARN") or domain.get("DomainId"),
                    resource_name=domain.get("DomainName"),
                    state="processing" if domain.get("Processing") else "active",
                    engine=domain.get("EngineType") or domain.get("EngineVersion"),
                    instance_type=cluster.get("InstanceType"),
                    instance_count=cluster.get("InstanceCount"),
                    capacity_summary=(
                        f"dedicated_master_type={cluster.get('DedicatedMasterType')};"
                        f"dedicated_master_count={cluster.get('DedicatedMasterCount')};"
                        f"warm_type={cluster.get('WarmType')};warm_count={cluster.get('WarmCount')}"
                    ),
                    details={
                        "engine_version": domain.get("EngineVersion"),
                        "cluster_config": cluster,
                        "zone_awareness": cluster.get("ZoneAwarenessEnabled"),
                        "endpoint": domain.get("Endpoint"),
                    },
                )
            )
    return rows


def _collect_opensearchserverless(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    client = _client("opensearchserverless", region, connect_timeout, read_timeout, max_attempts)
    return [
        _resource_row(
            spec,
            region,
            resource_id=collection.get("arn") or collection.get("id"),
            resource_name=collection.get("name"),
            state=collection.get("status"),
            details={
                "kms_key_arn": collection.get("kmsKeyArn"),
                "collection_group_name": collection.get("collectionGroupName"),
                "capacity_unit_note": "OCU usage is not returned by ListCollections.",
            },
        )
        for collection in _list_lower_camel_items(client, "list_collections", "collectionSummaries")
    ]


def _collect_redshift(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    client = _client("redshift", region, connect_timeout, read_timeout, max_attempts)
    return [
        _resource_row(
            spec,
            region,
            resource_id=cluster.get("ClusterNamespaceArn") or cluster.get("ClusterIdentifier"),
            resource_name=cluster.get("ClusterIdentifier"),
            state=cluster.get("ClusterStatus"),
            engine="redshift",
            instance_type=cluster.get("NodeType"),
            instance_count=cluster.get("NumberOfNodes"),
            capacity_summary=f"encrypted={cluster.get('Encrypted')};multi_az={cluster.get('MultiAZ')}",
            details={
                "availability_zone": cluster.get("AvailabilityZone"),
                "cluster_version": cluster.get("ClusterVersion"),
                "db_name": cluster.get("DBName"),
                "endpoint": cluster.get("Endpoint"),
            },
        )
        for cluster in _paginate_items(client, "describe_clusters", "Clusters")
    ]


def _collect_dsql(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    client = _client("dsql", region, connect_timeout, read_timeout, max_attempts)
    rows: List[CommitmentResourceInventory] = []
    for summary in _paginate_items(client, "list_clusters", "clusters"):
        identifier = summary.get("identifier")
        if not isinstance(identifier, str):
            continue
        cluster = client.get_cluster(identifier=identifier)
        rows.append(
            _resource_row(
                spec,
                region,
                resource_id=cluster.get("arn") or identifier,
                resource_name=identifier,
                state=cluster.get("status"),
                engine="aurora-dsql",
                details={
                    "creation_time": cluster.get("creationTime"),
                    "deletion_protection_enabled": cluster.get("deletionProtectionEnabled"),
                    "multi_region_properties": cluster.get("multiRegionProperties"),
                    "endpoint": cluster.get("endpoint"),
                },
            )
        )
    return rows


def _collect_memorydb(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    client = _client("memorydb", region, connect_timeout, read_timeout, max_attempts)
    return [
        _resource_row(
            spec,
            region,
            resource_id=cluster.get("ARN") or cluster.get("Name"),
            resource_name=cluster.get("Name"),
            state=cluster.get("Status"),
            engine="memorydb",
            instance_type=cluster.get("NodeType"),
            instance_count=cluster.get("NumberOfShards"),
            capacity_summary=f"replicas_per_shard={cluster.get('NumberOfReplicasPerShard')}",
            details={
                "engine_version": cluster.get("EngineVersion"),
                "tls_enabled": cluster.get("TLSEnabled"),
                "subnet_group_name": cluster.get("SubnetGroupName"),
                "cluster_endpoint": cluster.get("ClusterEndpoint"),
            },
        )
        for cluster in _paginate_items(client, "describe_clusters", "Clusters")
    ]


def _collect_neptune_graph(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    client = _client("neptune-graph", region, connect_timeout, read_timeout, max_attempts)
    return [
        _resource_row(
            spec,
            region,
            resource_id=graph.get("arn") or graph.get("id"),
            resource_name=graph.get("name"),
            state=graph.get("status"),
            engine="neptune-analytics",
            instance_count=graph.get("replicaCount"),
            capacity_summary=f"provisioned_memory={graph.get('provisionedMemory')}",
            details={
                "public_connectivity": graph.get("publicConnectivity"),
                "endpoint": graph.get("endpoint"),
                "vector_search_configuration": graph.get("vectorSearchConfiguration"),
                "deletion_protection": graph.get("deletionProtection"),
            },
        )
        for graph in _paginate_items(client, "list_graphs", "graphs")
    ]


def _collect_database_cluster_service(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    client = _client(spec.service_code, region, connect_timeout, read_timeout, max_attempts)
    rows: List[CommitmentResourceInventory] = []
    for instance in _paginate_items(client, "describe_db_instances", "DBInstances"):
        rows.append(
            _resource_row(
                spec,
                region,
                resource_id=instance.get("DBInstanceArn") or instance.get("DbiResourceId"),
                resource_name=instance.get("DBInstanceIdentifier"),
                resource_type=f"{spec.service} DB instance",
                state=instance.get("DBInstanceStatus"),
                engine=instance.get("Engine"),
                instance_type=instance.get("DBInstanceClass"),
                instance_count=1,
                details={
                    "availability_zone": instance.get("AvailabilityZone"),
                    "db_cluster_identifier": instance.get("DBClusterIdentifier"),
                    "promotion_tier": instance.get("PromotionTier"),
                },
            )
        )
    for cluster in _paginate_items(client, "describe_db_clusters", "DBClusters"):
        serverless_scaling = cluster.get("ServerlessV2ScalingConfiguration")
        rows.append(
            _resource_row(
                spec,
                region,
                resource_id=cluster.get("DBClusterArn") or cluster.get("DBClusterIdentifier"),
                resource_name=cluster.get("DBClusterIdentifier"),
                resource_type=f"{spec.service} DB cluster",
                state=cluster.get("Status"),
                engine=cluster.get("Engine"),
                commitment_types="Database Savings Plan",
                capacity_summary=_json(serverless_scaling) if serverless_scaling else None,
                details={
                    "engine_version": cluster.get("EngineVersion"),
                    "availability_zones": cluster.get("AvailabilityZones"),
                    "storage_encrypted": cluster.get("StorageEncrypted"),
                    "serverless_v2_scaling_configuration": serverless_scaling,
                },
            )
        )
    return rows


def _collect_timestream(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    client = _client("timestream-write", region, connect_timeout, read_timeout, max_attempts)
    rows: List[CommitmentResourceInventory] = []
    for database in _list_timestream_items(client, "list_databases", "Databases"):
        database_name = database.get("DatabaseName")
        rows.append(
            _resource_row(
                spec,
                region,
                resource_id=database.get("Arn") or database_name,
                resource_name=database_name,
                resource_type="Timestream database",
                state="active",
                details={
                    "table_count": database.get("TableCount"),
                    "creation_time": database.get("CreationTime"),
                    "last_updated_time": database.get("LastUpdatedTime"),
                },
            )
        )
        if not isinstance(database_name, str):
            continue
        for table in _list_timestream_items(
            client,
            "list_tables",
            "Tables",
            DatabaseName=database_name,
        ):
            rows.append(
                _resource_row(
                    spec,
                    region,
                    resource_id=table.get("Arn") or table.get("TableName"),
                    resource_name=f"{database_name}/{table.get('TableName')}",
                    resource_type="Timestream table",
                    state="active",
                    details={
                        "database_name": database_name,
                        "retention_properties": table.get("RetentionProperties"),
                        "magnetic_store_write_properties": table.get("MagneticStoreWriteProperties"),
                        "creation_time": table.get("CreationTime"),
                        "last_updated_time": table.get("LastUpdatedTime"),
                    },
                )
            )
    return rows


def _collect_keyspaces(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    client = _client("keyspaces", region, connect_timeout, read_timeout, max_attempts)
    rows: List[CommitmentResourceInventory] = []
    for keyspace in _paginate_items(client, "list_keyspaces", "keyspaces"):
        keyspace_name = keyspace.get("keyspaceName")
        rows.append(
            _resource_row(
                spec,
                region,
                resource_id=keyspace.get("resourceArn") or keyspace_name,
                resource_name=keyspace_name,
                resource_type="Keyspaces keyspace",
                state=keyspace.get("status"),
                details={
                    "replication_strategy": keyspace.get("replicationStrategy"),
                    "replication_regions": keyspace.get("replicationRegions"),
                },
            )
        )
        if not isinstance(keyspace_name, str):
            continue
        for table in _paginate_items(
            client,
            "list_tables",
            "tables",
            keyspaceName=keyspace_name,
        ):
            rows.append(
                _resource_row(
                    spec,
                    region,
                    resource_id=table.get("resourceArn") or table.get("tableName"),
                    resource_name=f"{keyspace_name}/{table.get('tableName')}",
                    resource_type="Keyspaces table",
                    state=table.get("status"),
                    capacity_summary=_json(table.get("capacitySpecification")),
                    details={
                        "keyspace_name": keyspace_name,
                        "capacity_specification": table.get("capacitySpecification"),
                        "point_in_time_recovery": table.get("pointInTimeRecovery"),
                        "default_time_to_live": table.get("defaultTimeToLive"),
                    },
                )
            )
    return rows


def _collect_dms(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    client = _client("dms", region, connect_timeout, read_timeout, max_attempts)
    rows: List[CommitmentResourceInventory] = []
    for instance in _paginate_items(client, "describe_replication_instances", "ReplicationInstances"):
        rows.append(
            _resource_row(
                spec,
                region,
                resource_id=instance.get("ReplicationInstanceArn") or instance.get("ReplicationInstanceIdentifier"),
                resource_name=instance.get("ReplicationInstanceIdentifier"),
                resource_type="DMS replication instance",
                state=instance.get("ReplicationInstanceStatus"),
                engine="dms",
                instance_type=instance.get("ReplicationInstanceClass"),
                instance_count=1,
                capacity_summary=f"allocated_storage_gb={instance.get('AllocatedStorage')};multi_az={instance.get('MultiAZ')}",
                details={
                    "engine_version": instance.get("EngineVersion"),
                    "availability_zone": instance.get("AvailabilityZone"),
                    "publicly_accessible": instance.get("PubliclyAccessible"),
                    "vpc_security_groups": instance.get("VpcSecurityGroups"),
                },
            )
        )
    for config in _paginate_items(client, "describe_replication_configs", "ReplicationConfigs"):
        compute_config = config.get("ComputeConfig") or {}
        rows.append(
            _resource_row(
                spec,
                region,
                resource_id=config.get("ReplicationConfigArn") or config.get("ReplicationConfigIdentifier"),
                resource_name=config.get("ReplicationConfigIdentifier"),
                resource_type="DMS replication configuration",
                state=config.get("Status"),
                commitment_types="Database Savings Plan",
                capacity_summary=_json(compute_config),
                details={
                    "compute_config": compute_config,
                    "replication_settings": config.get("ReplicationSettings"),
                    "stop_reason": config.get("StopReason"),
                },
            )
        )
    return rows


Collector = Callable[[CommitmentServiceSpec, str, int, int, int], List[CommitmentResourceInventory]]
COLLECTORS: Dict[str, Collector] = {
    "ec2": _collect_ec2,
    "ecs": _collect_ecs,
    "eks": _collect_eks,
    "lambda": _collect_lambda,
    "sagemaker": _collect_sagemaker,
    "rds": _collect_rds,
    "dsql": _collect_dsql,
    "dynamodb": _collect_dynamodb,
    "elasticache": _collect_elasticache,
    "opensearch": _collect_opensearch,
    "opensearchserverless": _collect_opensearchserverless,
    "redshift": _collect_redshift,
    "memorydb": _collect_memorydb,
    "docdb": _collect_database_cluster_service,
    "neptune": _collect_database_cluster_service,
    "neptune-graph": _collect_neptune_graph,
    "timestream-write": _collect_timestream,
    "keyspaces": _collect_keyspaces,
    "dms": _collect_dms,
}


def _collect_service_region(
    spec: CommitmentServiceSpec,
    region: str,
    connect_timeout: int,
    read_timeout: int,
    max_attempts: int,
) -> List[CommitmentResourceInventory]:
    return COLLECTORS[spec.service_code](spec, region, connect_timeout, read_timeout, max_attempts)


def collect_commitment_resource_inventory(
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> List[CommitmentResourceInventory]:
    """Collect commitment-relevant resource snapshots across enabled regions."""
    regions = get_enabled_regions()
    targets = [(spec, region) for spec in COMMITMENT_SERVICE_SPECS for region in regions]
    max_workers = min(12, len(targets)) or 1
    records: List[CommitmentResourceInventory] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_target = {
            executor.submit(
                _collect_service_region,
                spec,
                region,
                connect_timeout,
                read_timeout,
                max_attempts,
            ): (spec, region)
            for spec, region in targets
        }
        for future in as_completed(future_to_target):
            spec, region = future_to_target[future]
            try:
                records.extend(future.result())
            except (BotoCoreError, ClientError, AwsOperationError) as error:
                records.append(_error_row(spec, region, error))
            except Exception as error:
                records.append(_error_row(spec, region, error))

    return sorted(
        records,
        key=lambda record: (
            record["service_code"],
            record["region"],
            record["resource_type"],
            record["resource_name"] or "",
            record["resource_id"] or "",
        ),
    )


def write_commitment_resource_inventory_csv(
    records: Sequence[CommitmentResourceInventory],
    output_dir: Path,
    captured_at: Optional[datetime] = None,
) -> Path:
    """Write commitment-relevant resource rows to a timestamped CSV."""
    return write_timestamped_csv(records, output_dir, RESOURCE_CSV_FIELDS, "commitment-resources", captured_at)


def write_commitment_eligibility_csv(
    records: Sequence[CommitmentEligibility],
    output_dir: Path,
    captured_at: Optional[datetime] = None,
) -> Path:
    """Write the commitment eligibility matrix to a timestamped CSV."""
    return write_timestamped_csv(records, output_dir, ELIGIBILITY_CSV_FIELDS, "commitment-eligibility", captured_at)
