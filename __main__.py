import pulumi
import pulumi_azure_native as azure_native
import pulumi_azuread as azuread
from pulumi_azure_native import resources, authorization, containerservice
import pulumi_tls as tls
import pulumi_azure as azure

config = pulumi.Config()
stage = pulumi.get_stack()
deployer = config.get('deployer')
email = 'soaresanderson221@gmail.com'
location = 'WestEurope'
subscription = azure.core.get_subscription()

# Create tags
iac_course_tags = {
    'stage': stage,
    'deployer': deployer,
    'email': email
}


# Cretae resource group.
iac_course_rg = azure_native.resources.ResourceGroup('resourceGroup',
    location=location,
    resource_group_name='iac_course_project',
    tags=iac_course_tags)



# Create vnet
iac_course_vn = azure_native.network.VirtualNetwork("virtualNetwork",
    address_space=azure_native.network.AddressSpaceArgs(
        address_prefixes=["10.30.0.0/16"],
    ),
    location=location,
    resource_group_name=iac_course_rg.name,
    virtual_network_name="iac_course_vn",
    tags=iac_course_tags)

# Create Subnet.
iac_course_sn = azure_native.network.Subnet("subnet",
    address_prefix="10.30.1.0/24",
    resource_group_name=iac_course_rg.name,
    subnet_name="iac_course_sn",
    virtual_network_name=iac_course_vn.name
    )


# Create Azure AD Application for AKS
iac_course_ad_app = azuread.Application('azureAd',
    display_name=f"iac_course_ad_app"
)


# Create Azure Service Principal.
iac_course_sp = azuread.ServicePrincipal('servicePrincipal',
    application_id=iac_course_ad_app.application_id
)


# Create ACR
iac_course_acr = azure_native.containerregistry.Registry("containerRegistry",
    admin_user_enabled=True,
    location=location,
    registry_name="crashcourseacr",
    resource_group_name=iac_course_rg.name,
    sku=azure_native.containerregistry.SkuArgs(
        name="Standard",
    ),
    tags=iac_course_tags)
 
iac_course_roles = {
    "acr_pull": f"/subscriptions/{subscription.display_name}/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d",
    "network_contributor": f"/subscriptions/{subscription.display_name}/providers/Microsoft.Authorization/roleDefinitions/4d97b98b-1d4f-4787-a291-c67834d212e7",
}

# Grante Service Principal Network subnet 
iac_course_acr_perm = authorization.RoleAssignment('acrPermissions',
    principal_id=iac_course_sp.id,
    principal_type=authorization.PrincipalType.SERVICE_PRINCIPAL,
    role_definition_id=iac_course_roles['acr_pull'],
    scope=iac_course_acr.id
)

iac_course_sn_perm = authorization.RoleAssignment('subnetPermissions',
    principal_id=iac_course_sp.id,
    principal_type=authorization.PrincipalType.SERVICE_PRINCIPAL,
    role_definition_id=iac_course_roles['network_contributor'],
    scope=iac_course_sn.id
)

# SSH for linux machines access
ssh_key = tls.PrivateKey('sshKeyForLinuxAccess', algorithm="RSA", rsa_bits=4096)

# Create AKS Cluster
aks = containerservice.ManagedCluster('azureAks',
    resource_name_='iac_course_aks',
    resource_group_name=iac_course_rg.name,
    agent_pool_profiles=[
        containerservice.ManagedClusterAgentPoolProfileArgs(
            availability_zones=[
                "1",
                "2",
                "3",
            ],
            count=1,
            enable_node_public_ip=True,
            max_pods=110,
            mode="System",
            name="agentpool1",
            os_type="Linux",
            type="VirtualMachineScaleSets",
            vm_size="Standard_DS2_v2",
            vnet_subnet_id=iac_course_sn.id
        )
    ],
    dns_prefix='iac-course-aks-dns',
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
    tags=iac_course_tags,
    opts=pulumi.ResourceOptions(
        delete_before_replace=True, depends_on=[iac_course_sn]
    ),
)