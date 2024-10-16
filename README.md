# AWS Workload Evaluation Script (Cl0udW4tch3r - version 1.0)

This script evaluates workloads across multiple AWS accounts and regions. 
It calculates workloads for EC2, Lambda, Fargate, ECS, EKS, SageMaker, and LightSail instances, and integrates with CloudWatch for visualization. 

## Main Features

- **Workload Evaluation**: Calculates workloads for various AWS services (EC2, Lambda, ECS, Fargate, SageMaker, and more).
- **Cross-Account Support**: Use AWS Organizations or specify multiple accounts manually.
- **CloudWatch Integration**: Automatically creates a CloudWatch dashboard for visualizing workload metrics.
- **Interactive Mode**: Use the `--prompt` flag for a user-friendly interactive setup.
- **CSV and TXT Logging**: Option to save results to CSV and TXT files.

## Requirements

- Python 3.6 or higher.
- `boto3`, `botocore`, `tqdm` Python libraries (installed via `requirements.txt`).
- AWS CLI configured with valid AWS credentials.
- IAM role (`OrganizationAccountAccessRole`) set up for cross-account access.

## Installation

Clone the repository and install dependencies:

```bash
git clone https://github.com/gabrielflorencio/cl0udw4tch3r.git
cd cl0udw4tch3r
pip install -r requirements.txt
```

## IAM Role Configuration

You need to create an IAM role with cross-account access.

1. Go to the **AWS IAM Console**.
2. Create a new role called **OrganizationAccountAccessRole**.
3. Use the JSON file for Role policy below to set the permissions policy **OrganizationAccountAccessPolicy**.
4. Attach the **trusted entity** policy by using the JSON file for Trusted policy below.

Make sure the **OrganizationAccountAccessRole** role is trusted by the management account and allowed to assume the role in target accounts.

### JSON for Role Policy

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "ec2:DescribeInstances",
                "lightsail:GetInstances",
                "ecs:ListClusters",
                "ecs:ListContainerInstances",
                "ecs:ListTasks",
                "ecs:DescribeTasks",
                "ecs:DescribeContainerInstances",
                "eks:ListClusters",
                "eks:DescribeCluster",
                "lambda:ListFunctions",
                "sagemaker:ListEndpoints",
                "sagemaker:ListDomains",
                "cloudwatch:PutMetricData",
                "cloudwatch:PutDashboard",
                "sts:AssumeRole",
                "organizations:ListAccounts",
                "organizations:DescribeAccount"
            ],
            "Resource": "*"
        }
    ]
}
```

### JSON for Trusted Policy

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "AWS": "arn:aws:iam::MANAGEMENT_ACCOUNT_ID:user/IAM-User",
                "AWS": "arn:aws:iam::MANAGEMENT_ACCOUNT_ID:root",
            },
            "Action": "sts:AssumeRole"
        }
    ]
}
```

## Usage

You can run the script either in interactive mode or specify flags for the necessary parameters.

### Interactive Mode
```bash
python3 cl0udw4tch3r-v1.py --prompt
```
### Running with Specified AWS Accounts and Regions
```bash
python3 cl0udw4tch3r-v1.py --accounts ACCOUNT_ID_1,ACCOUNT_ID_2 --regions us-east-1,us-west-2
```
### CloudWatch Dashboard Creation
```bash
python3 cl0udw4tch3r-v1.py --cw --cw_account CW_ACCOUNT_ID --cw_region us-west-2
```
#### Available Flags
```bash
--prompt: Enables interactive mode for entering accounts, regions, and CloudWatch setup.
--all: Uses AWS Organizations to retrieve all active accounts.
--accounts: Comma-separated AWS account IDs.
--regions: Comma-separated AWS regions to scan.
--csv: Creates a CSV file for workload logging.
--csv_name: Custom name for the CSV file (default: workloads-evaluation-logs.csv).
--txt: Creates a TXT file for workload logging.
--txt_name: Custom name for the TXT file (default: workloads-evaluation-logs.txt).
--cw: Create a CloudWatch dashboard for workload metrics.
--cw_account: AWS account ID to create the CloudWatch dashboard.
--cw_region: AWS region for the CloudWatch dashboard (default: us-east-1).
--cw_name: Custom name for the CloudWatch dashboard (default: WorkloadEvaluationDashboard).
--debug: Enable debug logging for troubleshooting.
```

### Example Use Cases

1. **Evaluating All AWS Accounts in an Organization**:
   ```bash
   python3 cl0udw4tch3r-v1.py --all --csv --csv_name all-accounts-workload.csv

2. **Running Workload Evaluation on Specific Accounts and Creating a CloudWatch Dashboard**:
    ```bash
    python3 cl0udw4tch3r-v1.py --accounts 123456789001,123456789002 --cw --cw_account 123456789001 --cw_region us-west-2

### Contributing
We welcome contributions! Please create an issue or submit a pull request if you would like to contribute.

### License
This project is licensed under the MIT License. See the LICENSE file for details.
