import json
import os
from typing import Any, Dict

import boto3
import spotipy
from aws_lambda_powertools import Logger
from aws_secrets_manager_cache import AwsSecretManagerCacheHandler
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
oauth_secret = os.getenv("OAUTH_SECRET", "spotipy-oauth")
token_secret = os.getenv("TOKEN_SECRET", "spotipy-token")

# AWS region name
region_name = os.getenv("AWS_REGION", "us-east-2")

# Spotify OAuth scope and redirect URI
scope = "user-library-read playlist-modify-private"
redirect_uri = os.getenv("REDIRECT_URI", "http://127.0.0.1:8000/callback")

# Initialize global Spotipy client, cached OAuth secret
spotipy_client = None
cached_oauth_secret = None

# Set up logging
logger = Logger(__name__)


def create_spotipy_client() -> spotipy.Spotify:
    """
    Authenticate and create Spotify client
    """

    # If cached OAuth secret is not set, fetch it from AWS Secrets Manager
    global cached_oauth_secret
    if cached_oauth_secret is None:
        logger.info("Fetching OAuth secret from AWS Secrets Manager")
        secret_manager_client = boto3.client("secretsmanager", region_name=region_name)
        cached_oauth_secret = json.loads(
            secret_manager_client.get_secret_value(
                SecretId=oauth_secret
            )["SecretString"]
        )

    # Create AWS secret manager cache handler
    cache_handler = AwsSecretManagerCacheHandler(
        token_secret, region_name=region_name
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
    return spotipy.Spotify(auth_manager=auth_manager)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda function to create or update Spotify recently added playlists
    """

    # If Spotipy client not initialized, create it
    global spotipy_client
    if spotipy_client is None:
        spotipy_client = create_spotipy_client()

    # For each recently added playlist
    for i, playlist_name in enumerate(playlist_names):

        # Create a recently playlist syncer instance
        syncer = RecentlyAddedPlaylistSyncer(
            spotipy_client, playlist_name, i, playlist_length
        )

        # Sync the playlist
        syncer.sync()

    # Log completion
    logger.info("SUCCESS: all recently added playlists synced successfully")

    # Return success to indicate completion
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "SUCCESS: all recently added playlists synced successfully"
        }),
    }


# If calling from command line, run the Lambda handler
if __name__ == "__main__":
    lambda_handler({}, None)
