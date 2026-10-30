"""Runtime settings from env. MODEL_PROVIDER=mock forces the mock adapter for every spec."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    model_provider: str = "mock"  # mock | real
    backend: str = "local"  # local (in-process fallbacks) | services (redis/postgres/kafka/mlflow)

    mlflow_tracking_uri: str = "sqlite:///.mlflow/mlflow.db"
    mlflow_artifact_root: str = ".mlflow/artifacts"
    registered_model_name: str = "support-agent"

    redis_url: str = "redis://localhost:6379/0"
    postgres_dsn: str = "postgresql://adcp:adcp@localhost:5432/adcp"
    kafka_bootstrap: str = "localhost:19092"
    flagd_host: str | None = None
    flags_file: str = "flags/flags.flagd.json"

    otel_exporter_otlp_endpoint: str | None = None
    service_name: str = "agent-runtime"

    prometheus_url: str = "http://localhost:9090"
    argo_rollouts_namespace: str = "agents"
    argo_rollout_name: str = "agent-runtime"
    kube_api: str = "https://kubernetes.default.svc"
    github_repo: str = "jahnavi-yelamanchi/agent-deploy-control-plane"
    github_token: str | None = None


def get_settings() -> Settings:
    return Settings()
