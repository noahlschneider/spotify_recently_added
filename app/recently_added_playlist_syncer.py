from typing import List

import spotipy
from aws_lambda_powertools import Logger

# Setup logging
logger = Logger(__name__)

# Constants
GET_LIMIT = 50


class PlaylistSyncError(Exception):
    """Custom exception for playlist synchronization errors."""

    pass


class TrackFetchError(Exception):
    """Custom exception for when tracks cannot be retrieved from Spotify."""


class RecentlyAddedPlaylistSyncer:
    """Creates or updates recently added Spotify playlists."""

    def __init__(
        self,
        sp: spotipy.Spotify,
        playlist_name: str,
        playlist_id: str,
        playlist_index: int,
        playlist_length: int = 200,
    ):
        # Spotify client
        self.sp = sp

        # Playlist name
        self.playlist_name = playlist_name

        # Playlist id
        self.playlist_id = playlist_id

        # Which recently-added chunk to use (based on ordering)
        self.playlist_index = playlist_index

        # How long the playlist should be
        self.playlist_length = playlist_length

    def get_recently_added_tracks(self) -> List[str]:
        """Get recently added tracks from user's library with offset."""

        # Initialize track list, offset, and fetched count
        all_tracks = []
        offset = self.playlist_index * self.playlist_length
        fetched = 0

        # Loop until enough tracks or no more tracks
        while fetched < self.playlist_length:

            # Get current users's saved tracks with offset
            tracks = self.sp.current_user_saved_tracks(limit=GET_LIMIT, offset=offset)

            # If tracks missing, log error and raise exception
            if tracks is None:
                raise TrackFetchError("Failed to fetch recently added tracks")

            # Get tracks
            tracks = tracks.get("items", [])

            # For each track, append track ID
            for track in tracks:
                all_tracks.append(track["track"]["id"])

            # If fewer tracks then GET_LIMIT, break loop
            if len(tracks) < GET_LIMIT:
                break

            # Increment offset and fetched count
            offset += GET_LIMIT
            fetched += len(tracks)

        # Log and return the fetched tracks
        logger.info(
            f"Fetched {len(all_tracks)} recently added track(s) from library with "
            f"offset {self.playlist_index * self.playlist_length}"
        )
        return all_tracks

    def get_playlist_tracks(self) -> List[str]:
        """Get all tracks in a playlist."""

        # Initialize track list and offset
        all_tracks = []
        offset = 0

        # Loop until no more tracks
        while True:

            # Get playlist track with offset
            tracks = self.sp.playlist_items(
                self.playlist_id, limit=GET_LIMIT, offset=offset
            )

            # If tracks missing, log error and raise exception
            if tracks is None:
                raise TrackFetchError(
                    f"Failed to fetch tracks for playlist {self.playlist_name}"
                )

            # Get tracks
            tracks = tracks.get("items", [])

            # For each track, append track ID
            for track in tracks:
                all_tracks.append(track["track"]["id"])

            # If fewer tracks then GET_LIMIT, break loop
            if len(tracks) < GET_LIMIT:
                break

            # Increment offset count
            offset += GET_LIMIT

        # Log and return the fetched tracks
        logger.info(
            f"Fetched {len(all_tracks)} track(s) from playlist {self.playlist_name}"
        )
        return all_tracks

    def delete_tracks(self, tracks_to_remove: List[str]) -> None:
        """Delete all occurrences of the given tracks from the playlist."""

        # Loop over tracks to remove in batches
        for i in range(0, len(tracks_to_remove), GET_LIMIT):

            # Get current batch of tracks to remove
            batch = tracks_to_remove[i : (i + GET_LIMIT)]

            # Remove all occurrences of the batch from the playlist
            self.sp.playlist_remove_all_occurrences_of_items(self.playlist_id, batch)

        # Log the deleted tracks if debugging
        logger.debug(f"Deleted track(s): {tracks_to_remove}")

    def add_tracks(self, tracks_to_add: List[str]) -> None:
        """Add new tracks to the playlist."""

        # Loop over tracks to remove in batches (reversed for correct order)
        for i in reversed(range(0, len(tracks_to_add), GET_LIMIT)):

            # Get current batch of tracks to add
            batch = tracks_to_add[i : (i + GET_LIMIT)]

            # Add the batch to the beginning of the playlist
            self.sp.playlist_add_items(self.playlist_id, batch, position=0)

        # Log the added tracks if debugging
        logger.debug(
            f"Added track(s): {tracks_to_add} to playlist {self.playlist_name}"
        )

    def reorder_playlist(
        self, recently_added_tracks: List[str], playlist_tracks: List[str]
    ) -> None:
        """Reorder playlist to match recently added tracks."""

        # Initialize tracks to rearrange
        tracks_reordered = 0

        # Loop over recently added tracks
        for correct_index, track in enumerate(recently_added_tracks):

            # Get current index of the track in the playlist
            current_index = playlist_tracks.index(track)

            # If track is out of order
            if correct_index != current_index:

                # Reorder the track in the playlist
                self.sp.playlist_reorder_items(
                    self.playlist_id,
                    range_start=current_index,
                    insert_before=correct_index,
                )

                # Update local list to remove Spotify order
                track_to_move = playlist_tracks.pop(current_index)
                playlist_tracks.insert(correct_index, track_to_move)

                # Increment reordered count
                tracks_reordered += 1

        logger.info(
            f"Reordered {tracks_reordered} track(s) for playlist {self.playlist_name}"
        )

    def sync(self) -> None:
        """
        Run the full synchronization logic for one playlist.
        """

        # Log sync start
        logger.info(f"Syncing recently added playlist: {self.playlist_name}")

        # Get recently added tracks
        recently_added_tracks = self.get_recently_added_tracks()

        # Get playlist tracks
        playlist_tracks = self.get_playlist_tracks()

        # If playlist already up to date, skip
        if recently_added_tracks == playlist_tracks:
            logger.info(f"SUCCESS: playlist {self.playlist_name} already up to date")
            return

        # Find and remove duplicate tracks
        seen_tracks = set()
        duplicate_tracks = []
        for track in playlist_tracks:
            if track in seen_tracks:
                duplicate_tracks.append(track)
            else:
                seen_tracks.add(track)

        # If duplicates found, delete them and update playlist tracks
        if duplicate_tracks:
            self.delete_tracks(duplicate_tracks)
            playlist_tracks = self.get_playlist_tracks()
            logger.info(
                f"Removed {len(duplicate_tracks)} duplicate track(s) from playlist "
                f"{self.playlist_name}"
            )

        # Find tracks to delete
        tracks_to_delete = [
            track for track in playlist_tracks if track not in recently_added_tracks
        ]

        # If tracks to remove, remove them and update playlist tracks
        if tracks_to_delete:
            self.delete_tracks(tracks_to_delete)
            playlist_tracks = self.get_playlist_tracks()
            logger.info(
                f"Removed {len(tracks_to_delete)} incorrect track(s) from playlist "
                f"{self.playlist_name}"
            )

        # Find tracks to add
        tracks_to_add = [
            track for track in recently_added_tracks if track not in playlist_tracks
        ]

        # If tracks to add, add them and update playlist tracks
        if tracks_to_add:
            self.add_tracks(tracks_to_add)
            logger.info(
                f"Added {len(tracks_to_add)} track(s) to playlist {self.playlist_name}"
            )
            playlist_tracks = self.get_playlist_tracks()

        # If playlist doesn't match, reorder and update playlist tracks
        if recently_added_tracks != playlist_tracks:

            self.reorder_playlist(recently_added_tracks, playlist_tracks)
            playlist_tracks = self.get_playlist_tracks()

        # If playlist still doesn't match, raise exception
        if recently_added_tracks != playlist_tracks:
            raise PlaylistSyncError(
                f"Playlist {self.playlist_name} does not match expected tracks"
            )

        # Log sync completion
        logger.info(f"SUCCESS: playlist {self.playlist_name} synced")
