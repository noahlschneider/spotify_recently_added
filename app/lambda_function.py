import json
import os
from typing import Any, Dict, List

import boto3
import spotipy
from aws_lambda_powertools import Logger
from aws_secrets_manager_cache import AwsSecretManagerCacheHandler
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from recently_added_playlist_syncer import RecentlyAddedPlaylistSyncer
from spotipy.oauth2 import SpotifyOAuth

# CONFIGURATION VARIABLES (can be overridden by environment variables)

# Playlist names and lengths
playlist_names = json.loads(
    os.getenv(
        "PLAYLIST_NAMES",
        '["Recently Added", "Older Recently Added", "Even Older Recently Added"]',
    )
)
playlist_length = int(os.getenv("PLAYLIST_LENGTH", "200"))

# AWS Secrets Manager secret names
playlist_secret = os.getenv("PLAYLIST_SECRET", "spotipy-playlists")
oauth_secret = os.getenv("OAUTH_SECRET", "spotipy-oauth")
token_secret = os.getenv("TOKEN_SECRET", "spotipy-token")

# AWS region name
region_name = os.getenv("AWS_REGION", "us-east-2")

# Spotify OAuth scope and redirect URI
scope = "user-library-read playlist-modify-private"
redirect_uri = os.getenv("REDIRECT_URI", "http://127.0.0.1:8000/callback")

# Initialize global secret managed client, Spotipy client, cached OAuth secret
secret_manager_client = None
spotipy_client = None
cached_oauth_secret = None

# Set up logging
logger = Logger(__name__)


class PlaylistsSecretError(Exception):
    """Custom exception for playlists secret errors."""


def create_spotipy_client(secret_manager_client: BaseClient) -> spotipy.Spotify:
    """
    Authenticate and create Spotify client
    """

    # If cached OAuth secret is not set, fetch it from AWS Secrets Manager
    global cached_oauth_secret
    if cached_oauth_secret is None:
        logger.info("Fetching OAuth secret from AWS Secrets Manager")

        cached_oauth_secret = json.loads(
            secret_manager_client.get_secret_value(SecretId=oauth_secret)[
                "SecretString"
            ]
        )

    # Create AWS secret manager cache handler
    cache_handler = AwsSecretManagerCacheHandler(
        token_secret,
        region_name=region_name,
        secret_manager_client=secret_manager_client,
    )

    # Create Spotipy Oauth manager using secret and cache handler
    auth_manager = SpotifyOAuth(
        client_id=cached_oauth_secret["client_id"],
        client_secret=cached_oauth_secret["client_secret"],
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
    secret_manager_client: BaseClient,
    playlist_secret: str,
    sp: spotipy.Spotify,
) -> List[tuple]:
    """Get the playlist IDs by name, or create playlist if it doesn't exist."""

    # Initialize playlists
    playlists = []

    try:

        # Get the secret value
        raw = secret_manager_client.get_secret_value(SecretId=playlist_secret)[
            "SecretString"
        ]

        # Convert to playlist tuples
        playlists = [(p[0], p[1]) for p in json.loads(raw)]

    # If error
    except ClientError as e:

        # Check if the secret doesn't exist
        if e.response["Error"]["Code"] == "ResourceNotFoundException":

            # Get current user's information
            user = sp.current_user()

            # Check if user is none and raise exception
            if user is None or "id" not in user:
                raise PlaylistsSecretError("Failed to fetch current user")

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

            # Create a secret with the playlist ids
            secret_manager_client.create_secret(
                Name=playlist_secret, SecretString=json.dumps(playlists)
            )

    # Check if playlists is empty and raise error
    if playlists == []:
        raise PlaylistsSecretError(f"Playlist secret {playlist_secret} retrieval error")

    # Check all playlist names in secret
    if playlist_names != [p[0] for p in playlists]:
        raise Exception(
            f"Playlist secret {playlist_secret} does not match expected. Update it "
            "manually or delete it for the app to create new playlists."
        )

    # Return list of playlists
    return playlists


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda function to create or update Spotify recently added playlists
    """

    # If secret manged client not initialized, create it
    global secret_manager_client
    if secret_manager_client is None:
        secret_manager_client = boto3.client("secretsmanager", region_name=region_name)

    # If Spotipy client not initialized, create it
    global spotipy_client
    if spotipy_client is None:
        spotipy_client = create_spotipy_client(secret_manager_client)

    playlists = get_playlist_ids(
        playlist_names, secret_manager_client, playlist_secret, spotipy_client
    )

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
            {"message": "SUCCESS: all recently added playlists synced successfully"}
        ),
    }


# If calling from command line, run the Lambda handler
if __name__ == "__main__":
    lambda_handler({}, None)
