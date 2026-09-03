"""AWS Secrets Manager component.

Loads sensitive configuration from AWS Secrets Manager at startup
and merges it into the environment so Pydantic BaseSettings can parse it.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

try:
    import boto3  # type: ignore[import]
    from botocore.exceptions import ClientError  # type: ignore[import]
    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False


def load_secrets_to_env(
    secret_id: str,
    region_name: str = "us-east-1",
    endpoint_url: str | None = None,
) -> None:
    """Synchronously load a JSON secret and inject it into os.environ.
    
    This should be called *before* instantiating the Pydantic Settings class.
    """
    if not _BOTO3_AVAILABLE:
        logger.warning(
            "boto3 not installed — skipping AWS Secrets Manager fetch for %s.",
            secret_id,
        )
        return

    logger.info("Fetching secrets from AWS Secrets Manager: %s", secret_id)
    session = boto3.session.Session()
    client = session.client(
        service_name="secretsmanager",
        region_name=region_name,
        endpoint_url=endpoint_url,
    )

    try:
        get_secret_value_response = client.get_secret_value(SecretId=secret_id)
    except ClientError as e:
        logger.error("Failed to fetch AWS secret %s: %s", secret_id, e)
        return

    if "SecretString" in get_secret_value_response:
        secret_data = get_secret_value_response["SecretString"]
        try:
            parsed = json.loads(secret_data)
            if not isinstance(parsed, dict):
                logger.error("Secret %s is not a JSON object.", secret_id)
                return
            
            # Export to os.environ so Pydantic picks them up
            for key, value in parsed.items():
                os.environ[key] = str(value)
            
            logger.info("Successfully loaded %d secrets from %s", len(parsed), secret_id)
        except json.JSONDecodeError:
            logger.error("Secret %s does not contain valid JSON.", secret_id)
    else:
        logger.warning("Secret %s contains binary data, which is not supported.", secret_id)
