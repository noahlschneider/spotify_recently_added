import json
from typing import Any, Dict, Optional

from aws_lambda_powertools import Logger
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from spotipy import CacheHandler

logger = Logger(__name__)


class AwsParameterStoreCacheHandler(CacheHandler):
    """
    A cache handler that stores the Spotipy token info in AWS Parameter Store.
    """

    def __init__(
        self,
        parameter_name: str,
        parameter_store_client: BaseClient,
    ) -> None:
        """
        Parameters:
            * parameter_name: The name of the parameter in AWS Parameter Store.
            * parameter_store_client: boto3 SSM client instance.
        """
        self.parameter_name: str = parameter_name
        self.parameter_store_client: BaseClient = parameter_store_client

    def get_cached_token(self) -> Optional[Dict[str, Any]]:
        """
        Retrieve the token info dictionary from AWS Parameter Store.
        Returns None if the parameter does not exist or cannot be parsed.
        """

        # Try to get and convert parameter JSON string
        try:
            get_parameter_response = self.parameter_store_client.get_parameter(
                Name=self.parameter_name, WithDecryption=True
            )
            parameter_value = get_parameter_response.get("Parameter", {}).get("Value")
            if parameter_value:
                return json.loads(parameter_value)
            else:
                raise Exception(f"Parameter {self.parameter_name} is empty")

        # Log and raise errors
        except self.parameter_store_client.exceptions.ParameterNotFound as e:
            logger.error(f"Parameter not found: {self.parameter_name}")
            raise e

        except ClientError as e:
            logger.error(f"Error retrieving parameter: {e}")
            raise e

        except json.JSONDecodeError as e:
            logger.error(f"Couldn't decode JSON from parameter: {self.parameter_name}")
            raise e

    def save_token_to_cache(self, token_info: Dict[str, Any]) -> None:
        """
        Save the token info dictionary to AWS Parameter Store.
        Creates the parameter if it does not exist.
        """

        # Try to update the parameter
        try:
            self.parameter_store_client.put_parameter(
                Name=self.parameter_name,
                Value=json.dumps(token_info),
                Type="SecureString",
                Overwrite=True,
            )

        # Log and raise errors
        except ClientError as e:
            logger.warning(f"Error updating parameter: {e}")
            raise e
