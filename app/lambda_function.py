import json
import os
from typing import Any, Dict, List

import boto3
import spotipy
from aws_lambda_powertools import Logger
from aws_secrets_manager_cache import AwsSecretManagerCacheHandler
from aws_parameter_store_cache_handler import AwsParameterStoreCacheHandler
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from recently_added_playlist_syncer import RecentlyAddedPlaylistSyncer
from spotipy.oauth2 import SpotifyOAuth

# CONFIGURATION VARIABLES (can be overridden by environment variables)

# Secrets backend selection ("parameterstore" or "secretsmanager")
secrets_backend = os.getenv("SECRETS_BACKEND", "parameterstore")

# Playlist names and lengths
playlist_names = json.loads(
    os.getenv(
        "PLAYLIST_NAMES",
        '["Recently Added", "Older Recently Added", ' '"Even Older Recently Added"]',
    )
)
playlist_length = int(os.getenv("PLAYLIST_LENGTH", "200"))

# AWS Parameter Store parameters (for parameter store backend)
playlist_parameter = os.getenv("PLAYLIST_PARAMETER", "/spotify/playlists")
oauth_parameter = os.getenv("OAUTH_PARAMETER", "/spotify/oauth")
token_parameter = os.getenv("TOKEN_PARAMETER", "/spotify/token")

# AWS Secrets Manager secret parameters (for secrets manager backend)
playlist_secret = os.getenv("PLAYLIST_SECRET", "spotify-playlists")
oauth_secret = os.getenv("OAUTH_SECRET", "spotify-oauth")
token_secret = os.getenv("TOKEN_SECRET", "spotify-token")

# AWS region name
region_name = os.getenv("AWS_REGION", "us-east-2")

# Spotify OAuth scope and redirect URI
scope = "user-library-read playlist-modify-private"
redirect_uri = os.getenv("REDIRECT_URI", "http://127.0.0.1:8000/callback")

# Initialize global secrets client, Spotipy client, cached OAuth data
secrets_client = None
spotipy_client = None
cached_oauth_data = None

# Set up logging
logger = Logger(__name__)


class PlaylistsDataError(Exception):
    """Custom exception for playlists data errors."""


def create_spotipy_client(secrets_client: BaseClient) -> spotipy.Spotify:
    """
    Authenticate and create Spotify client
    """

    # If cached OAuth data is not set, fetch it from secrets backend
    global cached_oauth_data
    if cached_oauth_data is None:
        if secrets_backend == "parameterstore":
            logger.info("Fetching OAuth from AWS Parameter Store")
            cached_oauth_data = json.loads(
                secrets_client.get_parameter(Name=oauth_parameter, WithDecryption=True)[
                    "Parameter"
                ]["Value"]
            )
        else:
            logger.info("Fetching OAuth from AWS Secrets Manager")
            cached_oauth_data = json.loads(
                secrets_client.get_secret_value(SecretId=oauth_secret)["SecretString"]
            )

    # Create cache handler based on backend
    if secrets_backend == "parameterstore":
        cache_handler = AwsParameterStoreCacheHandler(
            token_parameter,
            parameter_store_client=secrets_client,
        )
    else:
        cache_handler = AwsSecretManagerCacheHandler(
            token_secret,
            secret_manager_client=secrets_client,
        )

    # Create Spotipy OAuth manager using cache handler
    auth_manager = SpotifyOAuth(
        client_id=cached_oauth_data["client_id"],
        client_secret=cached_oauth_data["client_secret"],
        redirect_uri=redirect_uri,
        scope=scope,
        cache_handler=cache_handler,
    )

    # Return Spotipy client
    return spotipy.Spotify(
        auth_manager=auth_manager, requests_timeout=90, backoff_factor=1
    )


def get_playlist_ids(
    playlist_names: List[str],
    secrets_client: BaseClient,
    sp: spotipy.Spotify,
) -> List[tuple]:
    """Get the playlist IDs by name, or create playlist if it doesn't exist."""

    # Initialize playlists
    playlists = []

    try:

        # Get data from backend
        if secrets_backend == "parameterstore":
            raw = secrets_client.get_parameter(
                Name=playlist_parameter, WithDecryption=True
            )["Parameter"]["Value"]
        else:
            raw = secrets_client.get_secret_value(SecretId=playlist_secret)[
                "SecretString"
            ]

        # Convert to playlist tuples
        playlists = [(p[0], p[1]) for p in json.loads(raw)]

    # If error
    except ClientError as e:

        # Check if resource doesn't exist
        error_code = e.response["Error"]["Code"]
        if error_code in ["ParameterNotFound", "ResourceNotFoundException"]:

            # Get current user's information
            user = sp.current_user()

            # Check if user is none and raise exception
            if user is None or "id" not in user:
                raise PlaylistsDataError("Failed to fetch current user")

            # Loop over playlist names
            for playlist_name in playlist_names:

                # Create the user playlist
                playlist_response = sp.user_playlist_create(
                    user["id"], playlist_name, public=False
                )

                # Check if playlists returned none and raise exception
                if playlist_response is None or "id" not in playlist_response:
                    raise Exception(f"Failed to create playlist '{playlist_name}'")

                # Append the playlist name and ID
                playlists.append((playlist_name, playlist_response["id"]))

            # Save playlists to backend
            if secrets_backend == "parameterstore":
                secrets_client.put_parameter(
                    Name=playlist_parameter,
                    Value=json.dumps(playlists),
                    Type="SecureString",
                )
            else:
                secrets_client.create_secret(
                    Name=playlist_secret, SecretString=json.dumps(playlists)
                )

    # Check if playlists is empty and raise error
    if playlists == []:
        raise PlaylistsDataError("Playlist data retrieval error")

    # Check all playlist names match
    if playlist_names != [p[0] for p in playlists]:
        raise Exception(
            "Playlist data does not match expected. Update it manually or "
            "delete it for the app to create new playlists."
        )

    # Return list of playlists
    return playlists


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda function to create or update Spotify recently added playlists
    """

    # If secrets client not initialized, create it based on backend
    global secrets_client
    if secrets_client is None:
        if secrets_backend == "parameterstore":
            secrets_client = boto3.client("ssm", region_name=region_name)
            logger.info("Using AWS Parameter Store backend")
        else:
            secrets_client = boto3.client("secretsmanager", region_name=region_name)
            logger.info("Using AWS Secrets Manager backend")

    # If Spotipy client not initialized, create it
    global spotipy_client
    if spotipy_client is None:
        spotipy_client = create_spotipy_client(secrets_client)

    playlists = get_playlist_ids(playlist_names, secrets_client, spotipy_client)

    # For each recently added playlist
    for i, (playlist_name, playlist_id) in enumerate(playlists):

        # Create a recently playlist syncer instance
        syncer = RecentlyAddedPlaylistSyncer(
            spotipy_client, playlist_name, playlist_id, i
        )

        # Sync the playlist
        syncer.sync()

    # Log completion
    logger.info("SUCCESS: all recently added playlists synced successfully")

    # Return success to indicate completion
    return {
        "statusCode": 200,
        "body": json.dumps(
            {"message": ("SUCCESS: all recently added playlists synced successfully")}
        ),
    }


# If calling from command line, run the Lambda handler
if __name__ == "__main__":
    lambda_handler({}, None)
