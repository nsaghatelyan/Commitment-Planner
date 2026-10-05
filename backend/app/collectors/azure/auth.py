import os

from azure.identity import CertificateCredential, ClientSecretCredential


def client_credential(tenant_id: str, app_client_id: str, certificate_path: str):
    """Our multi-tenant Entra app, signing in to the client's tenant with a certificate."""
    return CertificateCredential(
        tenant_id=tenant_id, client_id=app_client_id, certificate_path=certificate_path
    )


def credential_for(tenant_id: str, client_id: str, credential_ref: str):
    """credential_ref: a certificate path, or "env:VAR" naming an env var holding a client
    secret. The secret itself is never stored in the database."""
    if credential_ref.startswith("env:"):
        secret = os.environ.get(credential_ref[4:])
        if not secret:
            raise ValueError(f"environment variable {credential_ref[4:]} is not set")
        return ClientSecretCredential(tenant_id, client_id, secret)
    return client_credential(tenant_id, client_id, credential_ref)
