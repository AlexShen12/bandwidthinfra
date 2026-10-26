"""Read-only inventory: print the AWS account and GitHub OIDC provider ARNs."""

import json

import boto3


def main():
    session = boto3.Session()
    account = session.client("sts").get_caller_identity()["Account"]
    iam = session.client("iam")
    matching = []
    for item in iam.list_open_id_connect_providers()["OpenIDConnectProviderList"]:
        arn = item["Arn"]
        if arn.endswith("oidc-provider/token.actions.githubusercontent.com"):
            matching.append(arn)
    print(json.dumps({"account_id": account, "github_oidc_provider_arns": matching}, indent=2))


if __name__ == "__main__":
    main()
