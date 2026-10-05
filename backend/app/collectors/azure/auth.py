from azure.identity import CertificateCredential


def client_credential(tenant_id: str, app_client_id: str, certificate_path: str):
    """Our multi-tenant Entra app, signing in to the client's tenant with a certificate."""
    return CertificateCredential(
        tenant_id=tenant_id, client_id=app_client_id, certificate_path=certificate_path
    )
