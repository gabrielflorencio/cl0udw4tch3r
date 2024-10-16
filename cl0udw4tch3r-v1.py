import argparse
import boto3
import sys
import json
import csv
from datetime import datetime
import concurrent.futures
from tqdm import tqdm
from botocore.exceptions import ClientError

# Set workload ratios for different resource types
WORKLOAD_RATIOS = {
    'EC2': 1,  # Virtual Machines (EC2)
    'LightSail': 1,  # Virtual Machines (LightSail)
    'ECS': 1,  # Container Hosts (ECS)
    'EKS': 1,  # Container Hosts (EKS)
    'Fargate': 0.1,  # Serverless Containers (Fargate)
    'SageMaker': 0.1,  # Serverless Containers (SageMaker)
    'Lambda': 0.02  # Serverless Functions (Lambda)
}

# Enable debug logging when --debug flag is present
def debug_log(message, debug=False):
    if debug:
        print(f"[DEBUG] {message}")

# Initialize CloudWatch client using assumed session
def publish_final_metric(metric_name, value, session, cloudwatch_region, debug=False):
    """Publish final workload metrics to CloudWatch"""
    cloudwatch = session.client('cloudwatch', region_name=cloudwatch_region)
    debug_log(f"Publishing metric: {metric_name}, Value: {value} in region {cloudwatch_region}", debug)
    cloudwatch.put_metric_data(
        Namespace='Custom/Workloads',
        MetricData=[{
            'MetricName': metric_name,
            'Timestamp': datetime.utcnow(),
            'Value': value,
            'Unit': 'Count'
        }]
    )

# Function to create a CloudWatch dashboard
def create_cloudwatch_dashboard(account_id, total_workloads, resource_counts, session, cloudwatch_region, dashboard_name=None, debug=False):
    """Create or update a CloudWatch dashboard for total workloads and resource counts in the specified account"""
    cloudwatch = session.client('cloudwatch', region_name=cloudwatch_region)
    dashboard_name = dashboard_name or f"WorkloadEvaluationDashboard-{account_id}"

    # Define the dashboard structure
    dashboard_body = {
        "widgets": [
            {
                "type": "metric",
                "x": 0,
                "y": 0,
                "width": 24,
                "height": 6,
                "properties": {
                    "metrics": [
                        ["Custom/Workloads", "EC2Workload"],
                        ["Custom/Workloads", "LightSailWorkload"],
                        ["Custom/Workloads", "ECSWorkload"],
                        ["Custom/Workloads", "EKSWorkload"],
                        ["Custom/Workloads", "LambdaWorkload"],
                        ["Custom/Workloads", "FargateWorkload"],
                        ["Custom/Workloads", "SageMakerWorkload"],
                        ["Custom/Workloads", "TotalWorkload"]
                    ],
                    "view": "timeSeries",
                    "stacked": False,
                    "region": cloudwatch_region,
                    "stat": "Sum",
                    "period": 300
                }
            },
            {
                "type": "text",
                "x": 0,
                "y": 6,
                "width": 24,
                "height": 6,
                "properties": {
                    "markdown": f"### Resource Counts:\n"
                                f"- EC2 Instances: {resource_counts['EC2']}\n"
                                f"- LightSail Instances: {resource_counts['LightSail']}\n"
                                f"- ECS Containers: {resource_counts['ECS']}\n"
                                f"- EKS Containers: {resource_counts['EKS']}\n"
                                f"- Lambda Functions: {resource_counts['Lambda']}\n"
                                f"- Fargate Tasks: {resource_counts['Fargate']}\n"
                                f"- SageMaker Endpoints: {resource_counts['SageMakerEndpoints']}\n"
                                f"- SageMaker Domains: {resource_counts['SageMakerDomains']}\n"
                }
            }
        ]
    }

    dashboard_body_json = json.dumps(dashboard_body)
    debug_log(f"Creating/Updating CloudWatch dashboard with body: {dashboard_body_json}", debug)

    try:
        response = cloudwatch.put_dashboard(
            DashboardName=dashboard_name,
            DashboardBody=dashboard_body_json
        )
        print(f"CloudWatch dashboard '{dashboard_name}' created/updated in account {account_id}, region: {cloudwatch_region}.")
        print(f"Dashboard response: {response}")
        debug_log(f"CloudWatch response: {response}", debug)
    except Exception as e:
        print(f"Error creating/updating CloudWatch dashboard in region {cloudwatch_region}: {e}")
        debug_log(f"Error creating CloudWatch dashboard: {e}", debug)

# Function to assume role in each member account
def assume_role(account_id, role_name="OrganizationAccountAccessRole", debug=False):
    """Assumes role into the specified account"""
    sts_client = boto3.client('sts')
    role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"

    try:
        debug_log(f"Assuming role: {role_arn}", debug)
        response = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName="WorkloadEvaluationSession"
        )
        credentials = response['Credentials']
        session = boto3.Session(
            aws_access_key_id=credentials['AccessKeyId'],
            aws_secret_access_key=credentials['SecretAccessKey'],
            aws_session_token=credentials['SessionToken']
        )
        return session
    except boto3.exceptions.Boto3Error as e:
        print(f"Error assuming role for account {account_id}: {e}")
        debug_log(f"Error assuming role for account {account_id}: {e}", debug)
        return None
    except Exception as e:
        print(f"General error assuming role for account {account_id}: {e}")
        debug_log(f"General error assuming role for account {account_id}: {e}", debug)
        return None

# Function to get the list of all accounts in the AWS Organization
def get_org_accounts(debug=False):
    """Retrieves all active accounts in the AWS Organization"""
    client = boto3.client('organizations')
    accounts = []
    paginator = client.get_paginator('list_accounts')
    for page in paginator.paginate():
        accounts.extend([{'Id': account['Id'], 'Name': account['Name']} for account in page['Accounts'] if account['Status'] == 'ACTIVE'])
    debug_log(f"Retrieved accounts from AWS Organizations: {accounts}", debug)
    return accounts

# Function to fetch account names using AWS Organizations for --accounts flag
def fetch_account_names(account_ids, debug=False):
    """Fetches account names for the given account IDs using AWS Organizations"""
    org_client = boto3.client('organizations')
    account_names = {}
    try:
        for account_id in account_ids:
            account_details = org_client.describe_account(AccountId=account_id)
            account_name = account_details.get('Account', {}).get('Name', account_id)
            account_names[account_id] = account_name
        debug_log(f"Fetched account names: {account_names}", debug)
    except ClientError as e:
        print(f"Error fetching account names from AWS Organizations: {e}")
        debug_log(f"Error fetching account names: {e}", debug)
    return account_names

# Function to log non-zero workloads to a CSV file
def log_to_csv(csv_file, account_name, account_number, region, resource_type, unit_counted, workload_value, debug=False):
    """Logs evaluated workloads to CSV file"""
    with open(csv_file, mode='a', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([account_name, account_number, region, resource_type, unit_counted, workload_value])
        debug_log(f"Logged to CSV: {account_name}, {account_number}, {region}, {resource_type}, {unit_counted}, {workload_value}", debug)

# Function to log non-zero workloads to a TXT file
def log_to_txt(txt_file, account_name, account_number, region, resource_type, unit_counted, workload_value, debug=False):
    """Logs evaluated workloads to TXT file"""
    with open(txt_file, mode='a') as file:
        file.write(f"Account: {account_name} ({account_number}), Region: {region}, "
                   f"Resource Type: {resource_type}, Count: {unit_counted}, Workload: {workload_value}\n")
        debug_log(f"Logged to TXT: {account_name}, {account_number}, {region}, {resource_type}, {unit_counted}, {workload_value}", debug)

# Function to prompt user inputs interactively
def prompt_user_inputs(debug=False):
    """Prompt the user interactively for inputs"""
    accounts_input = input("Enter AWS account IDs (comma-separated), or leave blank to retrieve all accounts from the organization: ")
    if not accounts_input:
        accounts = get_org_accounts(debug)
    else:
        account_ids = accounts_input.split(",")
        account_names = fetch_account_names(account_ids, debug)
        accounts = [{'Id': acc_id, 'Name': account_names.get(acc_id, '')} for acc_id in account_ids]

    regions_input = input("Enter AWS regions (comma-separated), or leave blank to scan all regions: ")
    regions = regions_input.split(",") if regions_input else None

    role_name = input("Enter the role name to assume in target accounts (default: OrganizationAccountAccessRole): ") or "OrganizationAccountAccessRole"

    create_csv = input("Do you want to create a CSV file for workload logging? (yes/no): ").lower() == "yes"
    csv_name = input("Enter custom name for the CSV file (default: workloads-evaluation-logs.csv): ") or "workloads-evaluation-logs.csv" if create_csv else None

    create_txt = input("Do you want to create a TXT file for workload logging? (yes/no): ").lower() == "yes"
    txt_name = input("Enter custom name for the TXT file (default: workloads-evaluation-logs.txt): ") or "workloads-evaluation-logs.txt" if create_txt else None

    create_cw = input("Do you want to create a CloudWatch dashboard? (yes/no): ").lower() == "yes"
    cw_account = None
    cw_name = None
    cw_region = None
    if create_cw:
        cw_account = input("Enter the AWS account ID to create the CloudWatch dashboard: ")
        if not cw_account:
            print("Error: You must specify an AWS account ID to create the CloudWatch dashboard.")
            sys.exit(1)
        cw_region = input("Enter the AWS region for the CloudWatch dashboard (default: us-east-1): ") or "us-east-1"
        cw_name = input("Enter custom name for the CloudWatch dashboard (default: WorkloadEvaluationDashboard): ") or "WorkloadEvaluationDashboard"

    return accounts, regions, role_name, csv_name, txt_name, create_cw, cw_account, cw_name, cw_region


# Function to get EC2 instance count for the region
def get_ec2_instance_count(region, session, debug=False):
    client = session.client('ec2', region_name=region)
    paginator = client.get_paginator('describe_instances')
    count = 0
    for page in paginator.paginate():
        for reservation in page['Reservations']:
            count += len(reservation.get('Instances', []))
    debug_log(f"EC2 instance count for {region}: {count}", debug)
    return count

# Function to get LightSail instance count for the region
def get_lightsail_instance_count(region, session, debug=False):
    """Check if Lightsail is available in this region before querying."""
    client = session.client('lightsail', region_name=region)
    try:
        paginator = client.get_paginator('get_instances')
        count = 0
        for page in paginator.paginate():
            count += len(page['instances'])
        debug_log(f"Lightsail instance count for {region}: {count}", debug)
        return count
    except Exception as e:
        return 0

# Function to get ECS container host count for the region
def get_ecs_container_count(region, session, debug=False):
    """Get the count of ECS container instances"""
    client = session.client('ecs', region_name=region)
    clusters = client.list_clusters()['clusterArns']
    container_count = 0
    for cluster in clusters:
        container_instances = client.list_container_instances(cluster=cluster)['containerInstanceArns']
        container_count += len(container_instances)
    debug_log(f"ECS container count for {region}: {container_count}", debug)
    return container_count

# Function to get EKS container host count for the region
def get_eks_container_count(region, session, debug=False):
    """Get the count of EKS container hosts"""
    client = session.client('eks', region_name=region)
    clusters = client.list_clusters()['clusters']
    container_count = 0
    for cluster in clusters:
        ec2_client = session.client('ec2', region_name=region)
        paginator = ec2_client.get_paginator('describe_instances')
        for page in paginator.paginate(Filters=[{'Name': 'tag:eks:cluster-name', 'Values': [cluster]}]):
            for reservation in page['Reservations']:
                container_count += len(reservation.get('Instances', []))
    debug_log(f"EKS container count for {region}: {container_count}", debug)
    return container_count

# Function to get Lambda function count for the region
def get_lambda_function_count(region, session, debug=False):
    """Get the count of Lambda functions"""
    client = session.client('lambda', region_name=region)
    paginator = client.get_paginator('list_functions')
    count = 0
    for page in paginator.paginate():
        count += len(page['Functions'])
    debug_log(f"Lambda function count for {region}: {count}", debug)
    return count

# Function to get Fargate tasks count for the region
def get_fargate_count(region, session, debug=False):
    """Get the count of Fargate tasks"""
    client = session.client('ecs', region_name=region)
    clusters = client.list_clusters()['clusterArns']
    fargate_count = 0
    for cluster in clusters:
        tasks = client.list_tasks(cluster=cluster, launchType='FARGATE')['taskArns']
        fargate_count += len(tasks)
    debug_log(f"Fargate task count for {region}: {fargate_count}", debug)
    return fargate_count

# Function to get SageMaker endpoints count for the region
def get_sagemaker_count(region, session, debug=False):
    """Get the count of SageMaker endpoints"""
    client = session.client('sagemaker', region_name=region)
    paginator = client.get_paginator('list_endpoints')
    count = 0
    try:
        for page in paginator.paginate():
            count += len(page['Endpoints'])
    except ClientError as e:
        pass
    except Exception as e:
        pass
    debug_log(f"SageMaker endpoint count for {region}: {count}", debug)
    return count

# Function to get SageMaker domains count for the region
def get_sagemaker_domains_count(region, session, debug=False):
    """Get the count of SageMaker domains"""
    client = session.client('sagemaker', region_name=region)
    paginator = client.get_paginator('list_domains')
    count = 0
    try:
        for page in paginator.paginate():
            count += len(page['Domains'])
    except ClientError as e:
        pass
    except Exception as e:
        pass
    debug_log(f"SageMaker domain count for {region}: {count}", debug)
    return count

def get_workloads(account_name, account_id, regions, session, csv_file=None, txt_file=None, progress_bar=None, debug=False):
    """Calculate workloads for an account and return actual resource counts and workloads"""
    total_workloads = {
        'EC2Workload': 0,
        'LightSailWorkload': 0,
        'ECSWorkload': 0,
        'EKSWorkload': 0,
        'LambdaWorkload': 0,
        'FargateWorkload': 0,
        'SageMakerWorkload': 0  # Merged Endpoints and Domains
    }
    
    actual_resources = {
        'EC2': 0,
        'LightSail': 0,
        'ECS': 0,
        'EKS': 0,
        'Lambda': 0,
        'Fargate': 0,
        'SageMakerEndpoints': 0,  # Separate counts for display
        'SageMakerDomains': 0
    }

    for region in regions:
        ec2_count = get_ec2_instance_count(region, session, debug)
        lightsail_count = get_lightsail_instance_count(region, session, debug)
        ecs_count = get_ecs_container_count(region, session, debug)
        eks_count = get_eks_container_count(region, session, debug)
        lambda_count = get_lambda_function_count(region, session, debug)
        fargate_count = get_fargate_count(region, session, debug)
        sagemaker_endpoints_count = get_sagemaker_count(region, session, debug)
        sagemaker_domains_count = get_sagemaker_domains_count(region, session, debug)

        actual_resources['EC2'] += ec2_count
        actual_resources['LightSail'] += lightsail_count
        actual_resources['ECS'] += ecs_count
        actual_resources['EKS'] += eks_count
        actual_resources['Lambda'] += lambda_count
        actual_resources['Fargate'] += fargate_count
        actual_resources['SageMakerEndpoints'] += sagemaker_endpoints_count
        actual_resources['SageMakerDomains'] += sagemaker_domains_count

        total_sagemaker_count = sagemaker_endpoints_count + sagemaker_domains_count
        total_workloads['EC2Workload'] += ec2_count * WORKLOAD_RATIOS['EC2']
        total_workloads['LightSailWorkload'] += lightsail_count * WORKLOAD_RATIOS['LightSail']
        total_workloads['ECSWorkload'] += ecs_count * WORKLOAD_RATIOS['ECS']
        total_workloads['EKSWorkload'] += eks_count * WORKLOAD_RATIOS['EKS']
        total_workloads['LambdaWorkload'] += lambda_count * WORKLOAD_RATIOS['Lambda']
        total_workloads['FargateWorkload'] += fargate_count * WORKLOAD_RATIOS['Fargate']
        total_workloads['SageMakerWorkload'] += total_sagemaker_count * WORKLOAD_RATIOS['SageMaker']

        if csv_file:
            if ec2_count > 0:
                log_to_csv(csv_file, account_name, account_id, region, "EC2", ec2_count, ec2_count * WORKLOAD_RATIOS['EC2'], debug)
            if lightsail_count > 0:
                log_to_csv(csv_file, account_name, account_id, region, "LightSail", lightsail_count, lightsail_count * WORKLOAD_RATIOS['LightSail'], debug)
            if ecs_count > 0:
                log_to_csv(csv_file, account_name, account_id, region, "ECS", ecs_count, ecs_count * WORKLOAD_RATIOS['ECS'], debug)
            if eks_count > 0:
                log_to_csv(csv_file, account_name, account_id, region, "EKS", eks_count, eks_count * WORKLOAD_RATIOS['EKS'], debug)
            if lambda_count > 0:
                log_to_csv(csv_file, account_name, account_id, region, "Lambda", lambda_count, lambda_count * WORKLOAD_RATIOS['Lambda'], debug)
            if fargate_count > 0:
                log_to_csv(csv_file, account_name, account_id, region, "Fargate", fargate_count, fargate_count * WORKLOAD_RATIOS['Fargate'], debug)
            if total_sagemaker_count > 0:
                log_to_csv(csv_file, account_name, account_id, region, "SageMaker", total_sagemaker_count, total_sagemaker_count * WORKLOAD_RATIOS['SageMaker'], debug)

        if txt_file:
            if ec2_count > 0:
                log_to_txt(txt_file, account_name, account_id, region, "EC2", ec2_count, ec2_count * WORKLOAD_RATIOS['EC2'], debug)
            if lightsail_count > 0:
                log_to_txt(txt_file, account_name, account_id, region, "LightSail", lightsail_count, lightsail_count * WORKLOAD_RATIOS['LightSail'], debug)
            if ecs_count > 0:
                log_to_txt(txt_file, account_name, account_id, region, "ECS", ecs_count, ecs_count * WORKLOAD_RATIOS['ECS'], debug)
            if eks_count > 0:
                log_to_txt(txt_file, account_name, account_id, region, "EKS", eks_count, eks_count * WORKLOAD_RATIOS['EKS'], debug)
            if lambda_count > 0:
                log_to_txt(txt_file, account_name, account_id, region, "Lambda", lambda_count, lambda_count * WORKLOAD_RATIOS['Lambda'], debug)
            if fargate_count > 0:
                log_to_txt(txt_file, account_name, account_id, region, "Fargate", fargate_count, fargate_count * WORKLOAD_RATIOS['Fargate'], debug)
            if total_sagemaker_count > 0:
                log_to_txt(txt_file, account_name, account_id, region, "SageMaker", total_sagemaker_count, total_sagemaker_count * WORKLOAD_RATIOS['SageMaker'], debug)

        if progress_bar:
            progress_bar.update(1)

    return total_workloads, actual_resources

# Main function to gather results and push total to CloudWatch
def main(cw_account, cw_region, role_name, accounts, csv_file, txt_file, dashboard_name, specified_regions, create_cw=False, debug=False):
    regions = specified_regions or [region['RegionName'] for region in boto3.client('ec2').describe_regions()['Regions']]

    print(f"Active AWS accounts to be evaluated: {[account['Name'] for account in accounts]}")
    print(f"Active AWS regions to be evaluated: {regions}")

    # Initialize totals for all accounts
    global_workloads = {
        'EC2Workload': 0,
        'LightSailWorkload': 0,
        'ECSWorkload': 0,
        'EKSWorkload': 0,
        'LambdaWorkload': 0,
        'FargateWorkload': 0,
        'SageMakerWorkload': 0  # Merged Endpoints and Domains
    }
    
    global_resources = {  # Initialize global resources count
        'EC2': 0,
        'LightSail': 0,
        'ECS': 0,
        'EKS': 0,
        'Lambda': 0,
        'Fargate': 0,
        'SageMakerEndpoints': 0,  # Separate counts for display
        'SageMakerDomains': 0
    }

    total_resources = len(accounts) * len(regions)
    progress_bar = tqdm(total=total_resources, desc="Evaluating workloads")

    # Multi-threaded execution for faster processing
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = []
        for account in accounts:
            session = assume_role(account['Id'], role_name, debug)
            account_name = account['Name']
            if session:
                futures.append(executor.submit(get_workloads, account_name, account['Id'], regions, session, csv_file, txt_file, progress_bar, debug))

        for future in concurrent.futures.as_completed(futures):
            account_workloads, account_resources = future.result()
            
            for resource_type in global_workloads:
                global_workloads[resource_type] += account_workloads[resource_type]

            for resource_type in global_resources:
                global_resources[resource_type] += account_resources.get(resource_type, 0)

    progress_bar.close()

    total_workload = sum(global_workloads.values())

    print(f"\nFinal Workload Results: {global_workloads}\nTotal Workload: {total_workload}")

    # Add debug logging for CloudWatch logic
    debug_log(f"create_cw flag: {create_cw}, cw_account: {cw_account}, cw_name: {dashboard_name}, cw_region: {cw_region}", debug)

    # Only create CloudWatch dashboard if create_cw is True and session_cw exists
    if create_cw and cw_account:
        debug_log("Attempting to assume CloudWatch session...", debug)
        session_cw = assume_role(cw_account, role_name, debug)
        if session_cw:
            debug_log(f"CloudWatch session assumed for account {cw_account}.", debug)
            for resource_type, workload_value in global_workloads.items():
                publish_final_metric(resource_type, workload_value, session_cw, cw_region, debug)
            publish_final_metric('TotalWorkload', total_workload, session_cw, cw_region, debug)

            # Create the CloudWatch dashboard
            debug_log(f"Creating CloudWatch dashboard: {dashboard_name} in region {cw_region} for account {cw_account}", debug)
            create_cloudwatch_dashboard(cw_account, total_workload, global_resources, session_cw, cw_region, dashboard_name, debug)
        else:
            print(f"Error: Failed to assume role for CloudWatch dashboard creation in account {cw_account}")
            debug_log(f"Failed to assume role for CloudWatch dashboard creation in account {cw_account}", debug)

# Update the main function call to ensure create_cw is passed
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Workload counting script")

    # Options for processing workloads and cloudwatch
    parser.add_argument("--prompt", action="store_true", help="Interactive mode with prompts for all options")
    parser.add_argument("--cw", action="store_true", help="Create a CloudWatch dashboard")
    parser.add_argument("--cw_account", help="AWS account ID to create the CloudWatch dashboard")
    parser.add_argument("--cw_region", help="AWS region for the CloudWatch dashboard (default: us-east-1)", default="us-east-1")
    parser.add_argument("--cw_name", help="Custom name for the CloudWatch dashboard (default: WorkloadEvaluationDashboard)", default="WorkloadEvaluationDashboard")
    parser.add_argument("--all", action="store_true", help="Retrieve all AWS accounts from the organization")
    parser.add_argument("--accounts", help="Comma-separated AWS account IDs")
    parser.add_argument("--role-name", help="Role name to assume in target accounts (default: OrganizationAccountAccessRole)", default="OrganizationAccountAccessRole")
    parser.add_argument("--csv", action="store_true", help="Create a CSV file for workload logging")
    parser.add_argument("--csv_name", help="Custom name for the CSV file (default: workloads-evaluation-logs.csv)", default="workloads-evaluation-logs.csv")
    parser.add_argument("--txt", action="store_true", help="Create a TXT file for workload logging")
    parser.add_argument("--txt_name", help="Custom name for the TXT file (default: workloads-evaluation-logs.txt)", default="workloads-evaluation-logs.txt")
    parser.add_argument("--regions", help="Comma-separated AWS regions to scan (default: all regions)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Interactive mode if --prompt flag is used
if args.prompt:
    # Prompt mode logic
    accounts, specified_regions, role_name, csv_file, txt_file, create_cw, cw_account, cw_name, cw_region = prompt_user_inputs(args.debug)
    if not create_cw:
        cw_account = cw_name = cw_region = None
else:
    # Non-interactive mode
    specified_regions = args.regions.split(",") if args.regions else None
    csv_file = args.csv_name if args.csv else None
    txt_file = args.txt_name if args.txt else None

    # Handle CloudWatch arguments
    if args.cw:
        cw_account = args.cw_account
        cw_region = args.cw_region
        cw_name = args.cw_name
    else:
        cw_account = cw_name = cw_region = None

    # Handle accounts
    if args.all:
        accounts = get_org_accounts(args.debug)
        print(f"Found {len(accounts)} accounts from AWS Organizations.")
    elif args.accounts:
        account_ids = args.accounts.split(",")
        account_names = fetch_account_names(account_ids, args.debug)
        accounts = [{'Id': acc_id, 'Name': account_names.get(acc_id, '')} for acc_id in account_ids]
    else:
        print("Error: You must specify either --all, --accounts, or --prompt for interactive mode.")
        sys.exit(1)

# Ensure that `cw_account`, `cw_region`, and `cw_name` are defined before passing them to `main`
main(cw_account=cw_account, cw_region=cw_region, role_name=args.role_name, accounts=accounts, csv_file=csv_file, txt_file=txt_file, dashboard_name=cw_name, specified_regions=specified_regions, create_cw=create_cw, debug=args.debug)
