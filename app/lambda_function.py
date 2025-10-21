import json
import os
from typing import Any, Dict, List

import spotipy
from aws_lambda_powertools import Logger
from recently_added_playlist_syncer import RecentlyAddedPlaylistSyncer
from secrets_backend import ResourceNotFoundException, SecretsBackend
from spotipy.oauth2 import SpotifyOAuth

# CONFIGURATION VARIABLES (can be overridden by environment variables)

# Secrets backend selection ("PS" or "SM")
secrets_backend = os.getenv("SECRETS_BACKEND", "PS")

# Secrets/parameter names
oauth_config_name = os.getenv("OAUTH_NAME", "/spotify/oauth")
token_config_name = os.getenv("TOKEN_NAME", "/spotify/token")
playlist_config_name = os.getenv("PLAYLIST_NAME", "/spotify/playlists")

# Playlist names and lengths
playlist_names = json.loads(
    os.getenv(
        "PLAYLIST_NAMES",
        '["Recently Added", "Older Recently Added", ' '"Even Older Recently Added"]',
    )
)
playlist_length = int(os.getenv("PLAYLIST_LENGTH", "200"))

# AWS region name
region_name = os.getenv("AWS_REGION", "us-east-2")

# Spotify OAuth scope and redirect URI
scope = "user-library-read playlist-modify-private"
redirect_uri = os.getenv("REDIRECT_URI", "http://127.0.0.1:8000/callback")

# Initialize global secrets backend, Spotipy client, cached OAuth data
secrets_backend_client = None
spotipy_client = None
cached_oauth_data = None

# Set up logging
logger = Logger(__name__)


class PlaylistsDataError(Exception):
    """Custom exception for playlists data errors."""


def create_spotipy_client(backend: SecretsBackend) -> spotipy.Spotify:
    """
    Authenticate and create Spotify client
    """

    # If cached OAuth data is not set, fetch it from secrets backend
    global cached_oauth_data
    if cached_oauth_data is None:
        logger.info(f"Fetching OAuth credentials from {backend.backend_type}")
        cached_oauth_data = backend.get(backend.oauth_name)

    # Create cache handler for token storage
    cache_handler = backend.create_cache_handler()

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
    playlist_names: List[str], backend: SecretsBackend, sp: spotipy.Spotify
) -> List[tuple]:
    """Get the playlist IDs by name, or create playlist if it doesn't exist."""

    # Initialize playlists
    playlists = []

    try:
        # Get data from backend
        data = backend.get(backend.playlist_name)
        playlists = [(p[0], p[1]) for p in data]

    # If resource doesn't exist, create playlists
    except ResourceNotFoundException:

        # Get current user's information
        user = sp.current_user()

        # Check if user is none and raise exception
        if user is None or "id" not in user:
            raise PlaylistsDataError("Failed to fetch current user")

        # Loop over playlist names
        for name in playlist_names:

            # Create the user playlist
            playlist_response = sp.user_playlist_create(user["id"], name, public=False)

            # Check if playlists returned none and raise exception
            if playlist_response is None or "id" not in playlist_response:
                raise Exception(f"Failed to create playlist '{name}'")

            # Append the playlist name and ID
            playlists.append((name, playlist_response["id"]))

        # Save playlists to backend
        backend.put(backend.playlist_name, playlists)

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

    # If secrets backend not initialized, create it
    global secrets_backend_client
    if secrets_backend_client is None:
        secrets_backend_client = SecretsBackend(
            secrets_backend,
            region_name,
            oauth_config_name,
            token_config_name,
            playlist_config_name,
        )

    # If Spotipy client not initialized, create it
    global spotipy_client
    if spotipy_client is None:
        spotipy_client = create_spotipy_client(secrets_backend_client)

    playlists = get_playlist_ids(playlist_names, secrets_backend_client, spotipy_client)

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
