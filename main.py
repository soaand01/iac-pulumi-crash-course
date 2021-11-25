# Demo infra for L@S

import pulumi
import pulumi_azure_native as azure_native
from pulumi_azure_native import resources, authorization, containerservice, containerregistry, network, managedidentity, storage
import pulumi_tls as tls
from pulumi_azure_native.resources.get_resource import get_resource
from pulumi_azure_native.resources.get_resource_group import get_resource_group
import pulumi_kubernetes as k8s
import os
from helper import get_resource_name


# Declara varibales from Pulumi.demo.yaml file
config = pulumi.Config()
APPLICATION = config.get("application")
tomtom_private_dns_zone = config.get('private_dns_zone')
tomtom_vnet_resource_group = config.get('tomtom_rg')
tomtom_subnet_cidr = config.get('subnet_cidr')
tomtom_vnet_name = config.get('tomtom_vnet')
STAGE = pulumi.get_stack()
subscription_id = authorization.get_client_config().subscription_id
tomtom_existing_resource_group = resources.get_resource_group(tomtom_vnet_resource_group)
tomtom_existing_private_dns = network.get_private_zone(
    private_zone_name=tomtom_private_dns_zone,
    resource_group_name=tomtom_existing_resource_group.name,
)


# Set tags.
REQUIRED_TAGS = {
    'stage': STAGE,
    'deployer': config.get('deployer'),
    'application': APPLICATION
}


# Create resource group for AKS and ACR
aks_acr_rg = resources.ResourceGroup(
    get_resource_name('rg'),
    resource_group_name=get_resource_name("rg"),
    tags=REQUIRED_TAGS
)


# Create subnet within TomTom default VNET where AKS will be placed.
tomtom_vnet_subnet = network.Subnet(
    f"{APPLICATION}-subnet-{STAGE}",
    resource_group_name=tomtom_vnet_resource_group,
    address_prefix=tomtom_subnet_cidr,
    virtual_network_name=tomtom_vnet_name
)

# Create a private container registry for applications docker images and place in the same group as Aks.
acr = containerregistry.Registry(
    get_resource_name("acr", nodash=True),
    admin_user_enabled=True,
    registry_name=get_resource_name("acr", nodash=True),
    resource_group_name=aks_acr_rg.name,
    sku=containerregistry.SkuArgs(
        name="Standard",
    ),
    tags=REQUIRED_TAGS
)


# SSH for linux machines access
ssh_key = tls.PrivateKey(get_resource_name("pk"), algorithm="RSA", rsa_bits=4096)

# Create Aks cluster.
aks = containerservice.ManagedCluster(
    f"aks-{APPLICATION}-{STAGE}",
    resource_name_=f"aks-{APPLICATION}-{STAGE}",
    resource_group_name=aks_acr_rg.name,
    agent_pool_profiles=[
        containerservice.ManagedClusterAgentPoolProfileArgs(
            availability_zones=[
                "1",
                "2",
                "3",
            ],
            count=3,
            enable_node_public_ip=True,
            max_pods=110,
            mode="System",
            name="agentpool1",
            os_type="Linux",
            type="VirtualMachineScaleSets",
            vm_size="Standard_DS2_v2",
            vnet_subnet_id=tomtom_vnet_subnet.id
        )
    ],
    dns_prefix=get_resource_name("aks") + "-dns",
    enable_rbac=True,
    identity=containerservice.ManagedClusterIdentityArgs(
        type="SystemAssigned"
    ),
    network_profile=containerservice.ContainerServiceNetworkProfileArgs(
        load_balancer_profile=containerservice.ManagedClusterLoadBalancerProfileArgs(
            managed_outbound_ips=containerservice.ManagedClusterLoadBalancerProfileManagedOutboundIPsArgs(
                count=2,
            ),
        ),
        load_balancer_sku="standard",
        outbound_type="loadBalancer",
        network_plugin="kubenet",
    ),
    auto_scaler_profile=containerservice.ManagedClusterPropertiesAutoScalerProfileArgs(
        scale_down_delay_after_add="15m",
        scan_interval="20s",
    ),
    linux_profile=containerservice.ContainerServiceLinuxProfileArgs(
        admin_username="testuser",
        ssh=containerservice.ContainerServiceSshConfigurationArgs(
            public_keys=[
                containerservice.ContainerServiceSshPublicKeyArgs(
                    key_data=ssh_key.public_key_openssh,
                )
            ],
        ),
    ),
    tags=REQUIRED_TAGS,
    opts=pulumi.ResourceOptions(
        delete_before_replace=True, depends_on=[tomtom_vnet_subnet]
    ),
)


# Get AKS user managed identity of agentpool, which is created by Azure/AKS by default, this is the way to ge the agentpool identity ID.
index_resource_name = 4
index_name_user_identity = 8

aks_kubelet_id = aks.identity_profile.apply(
    lambda args: managedidentity.get_user_assigned_identity(
        resource_group_name=args["kubeletidentity"]["resource_id"].split("/")[
            index_resource_name
        ],
        resource_name=args["kubeletidentity"]["resource_id"].split("/")[
            index_name_user_identity
        ],
    )
)


# Azure build-in role for access different resources
ROLE_DEFINITIONS = {
    # https://docs.microsoft.com/en-us/azure/role-based-access-control/built-in-roles#acrpull
    # https://docs.microsoft.com/en-us/azure/role-based-access-control/role-definitions-list
    "acr_pull": f"/subscriptions/{subscription_id}/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d",
    "network_contributor": f"/subscriptions/{subscription_id}/providers/Microsoft.Authorization/roleDefinitions/4d97b98b-1d4f-4787-a291-c67834d212e7",
    "private_dns_contributor": f"/subscriptions/{subscription_id}/providers/Microsoft.Authorization/roleDefinitions/b12aa53e-6015-4669-85d0-8515ebb3ae7f",
    "general_reader": f"/subscriptions/{subscription_id}/providers/Microsoft.Authorization/roleDefinitions/acdd72a7-3385-48ef-bd42-f606fba81ae7",
    "event_hub_sender": f"/subscriptions/{subscription_id}/providers/Microsoft.Authorization/roleDefinitions/2b629674-e913-4c01-ae53-ef4638d8f975",
    "event_hub_receiver": f"/subscriptions/{subscription_id}/providers/Microsoft.Authorization/roleDefinitions/a638d3c7-ab3a-418d-83e6-5f17a39d4fde"
}

# ACR Role assignment to AKS agent pool managed identity. So AKS cluster is able to access the private acr.
role_assignment = authorization.RoleAssignment(
    f"acr-role-assignment-{STAGE}",
    principal_id=aks_kubelet_id.principal_id,
    principal_type=authorization.PrincipalType.SERVICE_PRINCIPAL,
    role_definition_id=ROLE_DEFINITIONS["acr_pull"],
    scope=acr.id
)

# Network role assignment for access subnet where agent pool live
network_contributor_role = authorization.RoleAssignment(
    f"network-contibutor-role-assignment-{STAGE}",
    principal_id=aks.identity.principal_id,
    principal_type=authorization.PrincipalType.SERVICE_PRINCIPAL,
    role_definition_id=ROLE_DEFINITIONS["network_contributor"],
    scope=tomtom_vnet_subnet.id
)

#For external DNS dynamically updates the DNS records
#Private DNS role assignment to list[Read-only] exisitng private DNS zone in TomTom default resource group
private_dns_reader_role = authorization.RoleAssignment(
    f"private-dns-reader-role-assignment-{STAGE}",
    principal_id=aks_kubelet_id.principal_id,
    principal_type=authorization.PrincipalType.SERVICE_PRINCIPAL,
    role_definition_id=ROLE_DEFINITIONS["general_reader"],
    scope=tomtom_existing_resource_group.id
)

# Private DNS role assignment to modify[read/write] DNS record in private DNS zone.
private_dns_contributor_role = authorization.RoleAssignment(
    f"private-dns-contibutor-role-assignment-{STAGE}",
    principal_id=aks_kubelet_id.principal_id,
    principal_type=authorization.PrincipalType.SERVICE_PRINCIPAL,
    role_definition_id=ROLE_DEFINITIONS["private_dns_contributor"],
    scope=tomtom_existing_private_dns.id
)

# Allows aks to receive access to Azure Event Hubs resources.
event_hub_receiver_role = authorization.RoleAssignment(
    f"event-hub-receiver-role-role-assignment-{STAGE}",
    principal_id=aks_kubelet_id.principal_id,
    principal_type=authorization.PrincipalType.SERVICE_PRINCIPAL,
    role_definition_id=ROLE_DEFINITIONS["event_hub_receiver"],
    scope=tomtom_existing_private_dns.id
)

# Allows aks to send access to Azure Event Hubs resources.
event_hub_sender_role = authorization.RoleAssignment(
    f"event-hub-sender-role-assignment-{STAGE}",
    principal_id=aks_kubelet_id.principal_id,
    principal_type=authorization.PrincipalType.SERVICE_PRINCIPAL,
    role_definition_id=ROLE_DEFINITIONS["event_hub_sender"],
    scope=tomtom_existing_private_dns.id
)

# Create Storage account for Event Hub
event_hub_storage_account = storage.StorageAccount(
    f"{APPLICATION}storage{STAGE}",
    account_name = f"{APPLICATION}storage{STAGE}",
    resource_group_name=aks_acr_rg.name,
    allow_blob_public_access=False,
    allow_shared_key_access=True,
    minimum_tls_version="TLS1_2",
    encryption=storage.EncryptionArgs(
        key_source="Microsoft.Storage",
        services=storage.EncryptionServicesArgs(
            blob=storage.EncryptionServiceArgs(
                enabled=True,
                key_type="Account",
            ),
            file=storage.EncryptionServiceArgs(
                enabled=True,
                key_type="Account",
            ),
        ),
    ),
    sku=storage.SkuArgs(
        name=storage.SkuName.STANDARD_LRS,
    ),
    kind="StorageV2",
    tags=REQUIRED_TAGS
)


# Create Namespace
event_hub_namespace = azure_native.eventhub.Namespace(f"{APPLICATION}namespace{STAGE}",
    location="West Europe",
    namespace_name=f"{APPLICATION}-evh-namespace-{STAGE}",
    resource_group_name=aks_acr_rg.name,
    sku=azure_native.notificationhubs.SkuArgs(
        name="Standard",
        tier="Standard",
    ),
    tags=REQUIRED_TAGS)


# Create Event Hub Event Hub
event_hub = azure_native.eventhub.EventHub(f"{APPLICATION}-event-{STAGE}",
    capture_description=azure_native.eventhub.CaptureDescriptionArgs(
        destination=azure_native.eventhub.DestinationArgs(
            archive_name_format="{Namespace}/{EventHub}/{PartitionId}/{Year}/{Month}/{Day}/{Hour}/{Minute}/{Second}",
            blob_container=f"{APPLICATION}container{STAGE}",
            name="EventHubArchive.AzureBlockBlob",
            storage_account_resource_id=event_hub_storage_account.id,
        ),
        enabled=True,
        encoding="Avro",
        interval_in_seconds=120,
        size_limit_in_bytes=10485763,
    ),
    event_hub_name=f"{APPLICATION}-stream-{STAGE}",
    message_retention_in_days=7,
    namespace_name=event_hub_namespace.name,
    partition_count=10,
    resource_group_name=aks_acr_rg.name,
    status="Active",
    opts=pulumi.ResourceOptions(
        depends_on=[event_hub_storage_account]
    ))

namespace_authorization_rule = azure_native.eventhub.NamespaceAuthorizationRule(f"{APPLICATION}-authorization-{STAGE}",
    authorization_rule_name=f"{APPLICATION}-authorization-{STAGE}",
    namespace_name=event_hub_namespace.name,
    resource_group_name=aks_acr_rg.name,
    rights=[
        "Listen",
        "Send",
    ])
