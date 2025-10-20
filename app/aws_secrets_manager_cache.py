import json
from typing import Any, Dict, Optional

from aws_lambda_powertools import Logger
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from spotipy import CacheHandler

logger = Logger(__name__)


class AwsSecretManagerCacheHandler(CacheHandler):
    """
    A cache handler that stores the Spotipy token info in AWS Secrets Manager.
    """

    def __init__(self, secret_name: str, secret_manager_client: BaseClient) -> None:
        """
        Parameters:
            * secret_name: The name of the secret in AWS Secrets Manager.
            * secret_manager_client: boto3 Secrets Manager client instance.
        """
        self.secret_name: str = secret_name
        self.secret_manager_client: BaseClient = secret_manager_client

    def get_cached_token(self) -> Optional[Dict[str, Any]]:
        """
        Retrieve the token info dictionary from AWS Secrets Manager.
        Returns None if the secret does not exist or cannot be parsed.
        """

        # Try to get and convert secret JSON string
        try:
            get_secret_value_response = (
                self.secret_manager_client.get_secret_value(
                    SecretId=self.secret_name
                )
            )
            secret_string = get_secret_value_response.get("SecretString")
            if secret_string:
                return json.loads(secret_string)
            else:
                raise Exception(f"Secret {self.secret_name} is empty")

        # Log and raise errors
        except self.secret_manager_client.exceptions.ResourceNotFoundException as e:
            logger.error(f"Secret not found: {self.secret_name}")
            raise e

        except ClientError as e:
            logger.error(f"Error retrieving secret: {e}")
            raise e

        except json.JSONDecodeError as e:
            logger.error(f"Couldn't decode JSON from secret: {self.secret_name}")
            raise e

    def save_token_to_cache(self, token_info: Dict[str, Any]) -> None:
        """
        Save the token info dictionary to AWS Secrets Manager.
        Creates the secret if it does not exist.
        """

        # Try to update the secret
        try:
            self.secret_manager_client.put_secret_value(
                SecretId=self.secret_name, SecretString=json.dumps(token_info)
            )

        # If secret doesn't exist
        except self.secret_manager_client.exceptions.ResourceNotFoundException:

            # Try to create the secret
            try:
                self.secret_manager_client.create_secret(
                    Name=self.secret_name,
                    SecretString=json.dumps(token_info),
                )

            # Log and raise errors
            except ClientError as e:
                logger.error(f"Error creating secret: {e}")
                raise e

        # Log and raise errors
        except ClientError as e:
            logger.warning(f"Error updating secret: {e}")
            raise e
