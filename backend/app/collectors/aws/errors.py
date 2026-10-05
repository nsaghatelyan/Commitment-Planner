"""Turn AWS SDK errors into messages a user can act on."""

import re

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
    ProfileNotFound,
)

_ACTION = re.compile(r"perform: ([a-z0-9-]+:[A-Za-z0-9]+)")


def explain(exc: BaseException) -> str:
    if isinstance(exc, ProfileNotFound):
        return (
            f"AWS profile not found: {exc.kwargs.get('profile')}. Check the name in "
            "~/.aws/config (run `aws configure list-profiles`)."
        )
    if isinstance(exc, NoCredentialsError):
        return (
            "No AWS credentials found. Configure a profile (`aws configure` or "
            "`aws sso login --profile <name>`) or set AWS_PROFILE for the API process."
        )
    if isinstance(exc, EndpointConnectionError):
        return f"Can't reach the AWS endpoint ({exc.kwargs.get('endpoint_url')})."
    if isinstance(exc, ClientError):
        err = exc.response.get("Error", {})
        code, message = err.get("Code", ""), err.get("Message", "")
        op = exc.operation_name
        if "not enabled for cost explorer" in message.lower() or code == "DataUnavailableException":
            return (
                "Cost Explorer isn't enabled for this account (or has no data yet). Enable it "
                "in the Billing console > Cost Explorer; data appears within 24 hours."
            )
        if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation"):
            action = _ACTION.search(message)
            what = action.group(1) if action else op
            return (
                f"Access denied for {what}. Add it to the IAM policy of the profile or role "
                "(see infra/client-readonly-role.yaml for the full list)."
            )
        if code in ("ExpiredToken", "ExpiredTokenException", "RequestExpired"):
            return "The AWS credentials have expired. Refresh them (e.g. `aws sso login`)."
        if code == "InvalidClientTokenId":
            return "The AWS access key isn't valid. Check the profile's credentials."
        return f"{op} failed: {code}: {message}"
    if isinstance(exc, BotoCoreError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"
