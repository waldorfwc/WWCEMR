from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    database_url: str = "sqlite:///./era_data.db"
    anthropic_api_key: str = ""
    secret_key: str = "changeme-in-production"
    upload_dir: str = "./uploads"
    export_dir: str = "./exports"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    practice_name: str = "Medical Practice"
    practice_address: str = ""
    practice_phone: str = ""
    practice_npi: str = ""
    state: str = "MD"
    documents_dir: str = str(__import__('os').path.expanduser("~/Downloads/wwc_documents/Document"))
    google_client_id: str = ""
    google_client_secret: str = ""
    allowed_domains: str = "waldorfwomenscare.com,caribcall.com"
    waystar_api_key: str = ""
    waystar_password: str = ""
    waystar_base_url: str = ""
    waystar_sftp_host: str = ""
    waystar_sftp_port: int = 22
    waystar_sftp_username: str = ""
    waystar_sftp_password: str = ""

    docusign_integration_key: str = ""
    docusign_user_id: str = ""
    docusign_account_id: str = ""
    docusign_base_uri: str = "https://demo.docusign.net"
    docusign_auth_uri: str = "account-d.docusign.com"
    docusign_private_key: str = ""
    docusign_template_id_dc: str = ""
    docusign_provider_name: str = ""
    docusign_provider_email: str = ""
    docusign_witness_name: str = ""
    docusign_witness_email: str = ""
    docusign_webhook_secret: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
