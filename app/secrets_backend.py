import json
from typing import Any, Dict

import boto3
from aws_lambda_powertools import Logger
from aws_parameter_store_cache_handler import AwsParameterStoreCacheHandler
from aws_secrets_manager_cache import AwsSecretManagerCacheHandler
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from spotipy import CacheHandler

# Set up logging
logger = Logger(__name__)


class ResourceNotFoundException(Exception):
    """Custom exception for when a resource doesn't exist in the backend."""


class SecretsBackend:
    """Unified interface for AWS Parameter Store and Secrets Manager."""

    def __init__(
        self,
        backend_type: str,
        region_name: str,
        oauth_name: str,
        token_name: str,
        playlist_name: str,
    ) -> None:
        """Initialize secrets backend with appropriate AWS client"""

        # Validate backend type
        if backend_type not in ["PS", "SM"]:
            raise ValueError(
                f"Invalid backend_type: {backend_type}. Must be 'PS' or 'SM'"
            )

        self.backend_type = backend_type
        self.region_name = region_name
        self.oauth_name = oauth_name
        self.token_name = token_name
        self.playlist_name = playlist_name

        # Create appropriate boto3 client based on backend type
        if backend_type == "PS":
            self.client: BaseClient = boto3.client("ssm", region_name=region_name)
            logger.info("Initialized AWS Parameter Store backend")
        else:
            self.client: BaseClient = boto3.client(
                "secretsmanager", region_name=region_name
            )
            logger.info("Initialized AWS Secrets Manager backend")

    def get(self, name: str) -> Dict[str, Any]:
        """Retrieve and parse JSON data from the secrets backend"""

        try:
            # Get raw value from appropriate backend
            if self.backend_type == "PS":
                response = self.client.get_parameter(Name=name, WithDecryption=True)
                raw_value = response["Parameter"]["Value"]
            else:
                response = self.client.get_secret_value(SecretId=name)
                raw_value = response["SecretString"]

            # Parse and return JSON
            return json.loads(raw_value)

        # Handle resource not found errors
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code in ["ParameterNotFound", "ResourceNotFoundException"]:
                logger.error(f"Resource not found: {name}")
                raise ResourceNotFoundException(f"Resource not found: {name}") from e
            else:
                logger.error(f"Error retrieving {name}: {e}")
                raise

        # Handle JSON decode errors
        except json.JSONDecodeError:
            logger.error(f"Couldn't decode JSON from {name}")
            raise

    def put(self, name: str, data: Dict[str, Any]) -> None:
        """Save JSON data to the secrets backend"""

        # Serialize data to JSON
        json_value = json.dumps(data)

        try:
            # Save to appropriate backend
            if self.backend_type == "PS":
                self.client.put_parameter(
                    Name=name, Value=json_value, Type="SecureString", Overwrite=True
                )
            else:
                # Try to update existing secret
                try:
                    self.client.put_secret_value(SecretId=name, SecretString=json_value)
                # If secret doesn't exist, create it
                except ClientError as e:
                    if e.response["Error"]["Code"] == "ResourceNotFoundException":
                        self.client.create_secret(Name=name, SecretString=json_value)
                    else:
                        raise

        except ClientError as e:
            logger.error(f"Error saving {name}: {e}")
            raise

    def create_cache_handler(self) -> CacheHandler:
        """Create the appropriate cache handler for Spotipy"""

        # Return cache handler based on backend type
        if self.backend_type == "PS":
            return AwsParameterStoreCacheHandler(
                self.token_name, parameter_store_client=self.client
            )
        else:
            return AwsSecretManagerCacheHandler(
                self.token_name, secret_manager_client=self.client
            )
