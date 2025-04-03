import os
import re
import time
import csv
from datetime import datetime
from googleapiclient.discovery import build
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import CacheFileHandler
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.DEBUG)

load_dotenv() # get keys
yt_api = os.getenv("YOUTUBE_API_KEY")
sp_client_id = os.getenv("SPOTIFY_CLIENT_ID")
sp_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
CACHE_PATH = ".cache"  # Custom cache file path


def log(message, level="INFO"):
    """Prints formatted log messages with timestamps and log levels."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")

def log_match_status(idx, total, title, found):
    """Prints a clean status message for each track"""
    status = "✅" if found else "❌"
    print(f"[{idx}/{total}] {status} {title[:50]}{'...' if len(title) > 50 else ''}")

def get_spotify_client():
    """
    Returns authenticated Spotify client, reusing cached credentials when possible.
    Falls back to manual auth if cache is invalid/expired.
    """
    try:
        # Configure cache
        cache_handler = CacheFileHandler(
            username="youtube_transfer_tool",  # Arbitrary identifier
            cache_path=CACHE_PATH
        )

        # Initialize auth manager
        auth_manager = SpotifyOAuth(
            client_id=sp_client_id,
            client_secret=sp_client_secret,
            redirect_uri="http://localhost:8888/callback",
            scope="playlist-modify-public playlist-modify-private",
            cache_handler=cache_handler,
            open_browser=False
        )

        # Try to get cached token
        cached_token = auth_manager.get_cached_token()
        if cached_token and not auth_manager.is_token_expired(cached_token):
            log("Using cached Spotify credentials")
            return spotipy.Spotify(auth_manager=auth_manager)

        # Manual auth flow
        log("Valid cached credentials not found", "WARNING")
        print("\n" + "=" * 50)
        print(" SPOTIFY AUTHENTICATION REQUIRED ".center(50, "="))
        print("=" * 50)
        print("1. Visit this URL in your browser:")
        print("\n" + auth_manager.get_authorize_url() + "\n")
        print("2. After approving, paste the full redirect URL (should start with http://localhost:)")

        while True:
            response = input("Paste URL here: ").strip()
            if response.startswith("http://localhost:"):
                break
            print("Invalid URL - must start with http://localhost:")

        # Get new token
        code = response.split("code=")[1].split("&")[0]
        token = auth_manager.get_access_token(code)
        log("New Spotify authentication successful")
        return spotipy.Spotify(auth_manager=auth_manager)

    except Exception as e:
        log(f"Spotify authentication failed: {str(e)}", "ERROR")
        raise


def get_youtube_playlist_items(playlist_id):
    youtube = build("youtube", "v3", developerKey=yt_api)
    videos = []
    next_page_token = None

    log(f"Fetching YouTube playlist {playlist_id}...")

    while True:
        try:
            request = youtube.playlistItems().list(
                part="snippet",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=next_page_token
            )
            response = request.execute()

            for item in response["items"]:
                try:
                    video_data = {
                        "title": item["snippet"]["title"],
                        "video_id": item["snippet"]["resourceId"]["videoId"],
                        "url": f"https://youtu.be/{item['snippet']['resourceId']['videoId']}",
                        "channel": item["snippet"].get("videoOwnerChannelTitle", "Unknown Channel"),
                        "position": len(videos) + 1
                    }
                    videos.append(video_data)
                except KeyError as e:
                    log(f"Skipping malformed video item (missing field: {str(e)})", "WARNING")
                    continue

            log(f"Fetched {len(response['items'])} items (Total: {len(videos)})")

            next_page_token = response.get("nextPageToken")
            if not next_page_token:
                break

            time.sleep(1)  # Avoid API quota limits

        except Exception as e:
            log(f"YouTube API Error: {str(e)}", "ERROR")
            break

    return videos


def export_to_csv(videos, filename="youtube_tracks.csv"):
    """Exports YouTube playlist data to CSV."""
    try:
        with open(filename, "w", newline="", encoding="utf-8") as csvfile:
            fieldnames = ["position", "title", "channel", "video_id", "url"]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(videos)
        log(f"Exported {len(videos)} tracks to {filename}")
    except Exception as e:
        log(f"CSV Export Error: {str(e)}", "ERROR")


def clean_title(title):
    """
    Clean the title string by:
      - Removing text in parentheses or brackets
      - Removing common unwanted keywords
    """
    # Remove common keywords (case insensitive)
    unwanted = [
        'official video', 'official audio', 'lyric video',
        'lyrics', 'hd', '4k', 'topic', 'visualizer',
        'music video', 'official music video', 'mv'
    ]
    # Remove these only when they appear at the end
    title = re.sub(
        r'(?i)\s*(official\s*(video|audio|lyric video|music video)?|lyrics?|hd|4k|mv|visualizer)\s*$',
        '',
        title
    )

    # Remove common channel suffixes
    title = re.sub(r'(?i)\s*-\s*(vevo|topic)\s*$', '', title)

    # Remove text in parentheses or brackets
    title = re.sub(r"[\"']", "", title)  # Remove all single and double quotes
    title = re.sub(r"\s+", " ", title).strip()  # Remove extra spaces
    title = re.sub(r"[\(\[].*?[\)\]]", "", title)

    # Clean up remaining artifacts
    title = re.sub(r'\s+', ' ', title).strip()
    title = title.strip(' -:|~')


    pattern = re.compile("|".join(unwanted), re.IGNORECASE)
    title = pattern.sub("", title)

    # Remove extra spaces and common delimiters at the ends
    title = title.strip(" -:|~")
    title = re.sub(r"\s{2,}", " ", title)

    return title.strip()


def handle_special_cases(title):
    """
    Handle known problematic patterns from the unmatched_tracks.csv

    TODO:
        * If it's an album, search for a playlist with the album and tracks in it.
        * If it's a playlist:
            * Search the description of the video
            * Comments that might have the track list
            * Time stamps in the video that might have the track name in it
    """
    # Math rock/emo mixes
    if any(x in title.lower() for x in ['math rock', 'midwest emo', 'mix', 'playlist']):
        return None, title.split('|')[0].strip()

    # Full album/EP cases
    if any(x in title.lower() for x in ['full album', 'full ep', '[full]']):
        return None, title.split('[')[0].strip()

    # Covers with original artist mentioned
    cover_match = re.search(r'\((.*?)\s*cover\)', title, re.IGNORECASE)
    if cover_match:
        original_artist = cover_match.group(1)
        track = re.sub(r'\s*\(.*?cover\)', '', title)
        return original_artist, track

    return None, None


def extract_artist_track(title):
    """Extracts artist and track using multiple patterns with priority."""
    artist, track = handle_special_casese(title)
    if artist is not None:
        return artist, track

    original_title = title
    title = clean_title(title)

    patterns = [
        r"^(.*?)\s*[-:|~]\s*(.*?)(?:\s+\(.*\))?$",  # "Artist - Track (Official Video)"
        r"^(.*?)\s*[-:|~]\s*(.*?)(?:\s+\[.*\])?$",  # "Artist - Track [2023]"
        r"^(.*?)\s*[-:|~]\s*(.*?)(?:\s+ft\..*)?$",  # "Artist - Track ft. Someone"
        r"^(.*?)\s*[-:|~]\s*(.*)$",  # Fallback for simple splits
        r"^(.*?)[\s\-–—:|]+(.*?)$",  # Artist - Title
        r"^(.*?)\s*[\"“](.*?)[\"”]",  # Artist "Title"
        r"^(.*?)\s+-\s+(.*?)$",  # Artist - Title (strict hyphen)
        # Common patterns with featured artists
        r"^(.*?)\s*[-–~|]\s*([^\(\[\{]+?)\s*(?:\(ft\.\s*(.*?)\)|ft\.\s*(.*?))(?:\s*[\(\[]|\s*$)",
        # Standard "Artist - Title" format
        r"^(.*?)\s*[-–~|]\s*([^\(\[\{]+)",
        # "Artist: Title" format
        r"^(.*?)\s*:\s*([^\(\[\{]+)",
        # "Artist "Title"" format
        r'^(.*?)\s*["“](.+?)["”]',
        # Live/performance indicators
        r"^(.*?)\s*[-–~|]\s*(.*?)\s*(?:\(live[^\)]*\)|\[live[^\]]*\])",
        # Cover versions
        r"^(.*?)\s*[-–~|]\s*(.*?)\s*(?:\(cover[^\)]*\)|\[cover[^\]]*\])",
        # Remixes
        r"^(.*?)\s*[-–~|]\s*(.*?)\s*(?:\(.*?remix\)|\[.*?remix\])",
        # Fallback - split on last hyphen if nothing else matches
        r"^(.*)\s*[-–]\s*(.*)$"
    ]

    for pattern in patterns:
        match = re.match(pattern, title, re.IGNORECASE)
        if match:
            artist = match.group(1).strip()
            track = match.group(2).strip()

            # Clean common prefixes/suffixes
            # track = re.sub(r"^(official\s*(audio|video|lyrics)\s*\|?\s*)", "", track, flags=re.IGNORECASE)
            # artist = re.sub(r"(\s+-\s+topic)$", "", artist, flags=re.IGNORECASE)
            # Handle featured artists if present
            if match.lastindex >= 3:
                feat = match.group(3) or match.group(4)
                if feat:
                    track = f"{track} (feat. {feat.strip()})"

            return artist, track

    return None, title  # Fallback if no pattern matches


def build_spotify_query(artist, track):
    """
    Build optimized Spotify search queries based on artist/track info
    """
    # If no artist, just search track
    if not artist or artist.lower() == 'various artists':
        return f"track:{track}"

    # Remove common suffixes from artist names
    artist_clean = re.sub(r'(\s*-\s*topic|\s*vevo|\s*official)$', '', artist, flags=re.IGNORECASE)

    # Try different query formats
    queries = [
        f"artist:{artist_clean} track:{track}",  # Most precise
        f"{artist_clean} {track}",  # Broader search
        track  # Fallback to track only
    ]

    return queries


# --- Spotify Matching ---
def match_to_spotify(video_data, sp_client):
    """Modified to accept an existing Spotify client"""
    results = []
    matched = 0
    unmatched_titles = []  # Track failed matches

    log("Starting Spotify matching...")
    print("\n=== TRACK MATCHING RESULTS ===")

    for idx, item in enumerate(video_data, 1):
        try:
            title = item["title"]
            artist, track = extract_artist_track(title)
            queries = build_spotify_query(artist, track)
            # query = f"artist:{artist} track:{track}" if artist else f"track:{track}"

            # Try each query in order until we get a match
            for query in queries:
                result = sp_client.search(q=query, type="track", limit=5)  # Get top 5 results

                if result['tracks']['items']:
                    # Additional validation could go here
                    best_match = result['tracks']['items'][0]
                    item['spotify_uri'] = best_match['uri']
                    item['query_used'] = query
                    break
            # result = sp_client.search(q=query, type="track", limit=1)
            time.sleep(0.5)  # Rate limiting

            spotify_uri = result["tracks"]["items"][0]["uri"] if result["tracks"]["items"] else None
            found = spotify_uri is not None

            # Simple status logging
            log_match_status(idx, len(video_data), title, found)

            if found:
                matched += 1
            else:
                unmatched_titles.append(title)

            results.append({
                **item,
                "spotify_uri": spotify_uri,
                "spotify_url": f"https://open.spotify.com/track/{spotify_uri.split(':')[-1]}" if spotify_uri else None,
                "match_status": "✅" if spotify_uri else "❌",
                "query_used": query
            })

        except Exception as e:
            log_match_status(idx, len(video_data), title, False)
            unmatched_titles.append(title)
            results.append({
                **item,
                "spotify_uri": None,
                "spotify_url": None,
                "match_status": f"⚠️ ({str(e)})",
                "query_used": query if 'query' in locals() else "N/A"
            })

    # Print summary after matching completes
    print("\n=== MATCHING SUMMARY ===")
    print(f"Successfully matched: {matched}/{len(video_data)} ({matched / len(video_data):.1%})")
    if unmatched_titles:
        print("\nThese tracks weren't found on Spotify:")
        for title in unmatched_titles[:10]:  # Show first 10 failures
            print(f"  - {title}")
        if len(unmatched_titles) > 10:
            print(f"  (...and {len(unmatched_titles) - 10} more)")

    return results, matched


def analyze_unmatched_patterns(unmatched_csv):
    """
    Analyze the unmatched_tracks.csv to identify common patterns
    that need special handling
    """
    patterns = {
        'covers': [],
        'remixes': [],
        'live': [],
        'instrumental': [],
        'mixes': []
    }

    with open(unmatched_csv, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = row['title']

            if 'cover' in title.lower():
                patterns['covers'].append(title)
            elif 'remix' in title.lower():
                patterns['remixes'].append(title)
            elif 'live' in title.lower():
                patterns['live'].append(title)
            elif 'instrumental' in title.lower():
                patterns['instrumental'].append(title)
            elif 'mix' in title.lower():
                patterns['mixes'].append(title)

    return patterns


def export_matched_to_csv(matched_data, filename="matched_tracks.csv"):
    """Exports successfully matched tracks to CSV"""
    try:
        with open(filename, "w", newline="", encoding="utf-8") as csvfile:
            fieldnames = ["position", "title", "channel", "spotify_url", "query_used"]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for item in matched_data:
                if item["spotify_uri"]:  # Only export successful matches
                    writer.writerow({
                        "position": item["position"],
                        "title": item["title"],
                        "channel": item["channel"],
                        "spotify_url": item["spotify_url"],
                        "query_used": item["query_used"]
                    })
        log(f"Exported {len([x for x in matched_data if x['spotify_uri']])} matched tracks to {filename}")
    except Exception as e:
        log(f"Matched CSV Export Error: {str(e)}", "ERROR")


def export_unmatched_to_csv(unmatched_data, filename="unmatched_tracks.csv"):
    """Exports failed matches to CSV"""
    try:
        with open(filename, "w", newline="", encoding="utf-8") as csvfile:
            fieldnames = ["position", "title", "channel", "youtube_url", "query_used"]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for item in unmatched_data:
                if not item["spotify_uri"]:  # Only export failures
                    writer.writerow({
                        "position": item["position"],
                        "title": item["title"],
                        "channel": item["channel"],
                        "youtube_url": item["url"],
                        "query_used": item["query_used"]
                    })
        log(f"Exported {len([x for x in unmatched_data if not x['spotify_uri']])} unmatched tracks to {filename}")
    except Exception as e:
        log(f"Unmatched CSV Export Error: {str(e)}", "ERROR")


def read_spotify_tracks_from_csv(csv_file):
    """Read CSV and extract Spotify track URIs"""
    track_uris = []
    with open(csv_file, "r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row["spotify_url"].startswith("https://open.spotify.com/track/"):
                track_uri = "spotify:track:" + row["spotify_url"].split("/")[-1]
                track_uris.append(track_uri)
    return track_uris


def create_or_use_playlist(sp, playlist_name="Imported Playlist"):
    """Create a new playlist or let the user choose an existing one"""
    user_id = sp.me()["id"]

    # Ask user if they want to use an existing playlist
    use_existing = input("Use existing playlist? (y/n): ").strip().lower()

    if use_existing == "y":
        playlist_id = input("Enter existing playlist ID or URL: ").strip()
        if "playlist" in playlist_id:
            playlist_id = playlist_id.split("/")[-1].split("?")[0]  # Extract just the ID
        return sp.playlist(playlist_id)
    else:
        return sp.user_playlist_create(user=user_id, name=playlist_name, public=True)


def add_tracks_to_playlist(sp, playlist_id, track_uris):
    """Add tracks to the given Spotify playlist"""
    chunk_size = 100  # Spotify allows adding up to 100 tracks at a time
    for i in range(0, len(track_uris), chunk_size):
        sp.playlist_add_items(playlist_id, track_uris[i:i + chunk_size])
    print(f"Added {len(track_uris)} tracks to playlist!")


# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    start_time = time.time()
    yt_playlist = 'yt_id'
    sp_playlist = 'sp_id'
    sp_client_id = os.getenv("SPOTIFY_CLIENT_ID")
    sp_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")

    matched_tracks = 'C:\\Users\\donal\\PycharmProjects\\yt_to_spotify\\matched_tracks.csv'

    try:
        if matched_tracks:
            sp = get_spotify_client()

            # Read tracks from CSV
            track_uris = read_spotify_tracks_from_csv(matched_tracks)

            if not track_uris:
                log("No valid Spotify track URLs found in the CSV.")
                exit()

            # Get or create a playlist
            playlist = create_or_use_playlist(sp)

            # Add tracks
            add_tracks_to_playlist(sp, playlist["id"], track_uris)

            log("Playlist update complete!")


        else:
            log("STEP 1: Fetching YouTube data...")
            step = "Step 1"
            youtube_data = get_youtube_playlist_items(yt_playlist)
            log(f"Retrieved {len(youtube_data)} YouTube tracks")

            log("STEP 2: Exporting to youtube_tracks.csv...")
            step = "Step 2"
            export_to_csv(youtube_data)
            log("CSV export completed")

            log("STEP 3: Authenticating with Spotify...")
            step = "Step 3"
            sp = get_spotify_client()
            log("Spotify authentication successful")

            log(f"STEP 4: Starting Spotify matching for {len(youtube_data)} tracks...")
            step = "Step 4"
            matched_data, success_count = match_to_spotify(youtube_data, sp_client=sp)
            log(f"Matching completed. Success: {success_count}/{len(youtube_data)}")

            # NEW: Export match results
            log("Exporting match results to CSV...")
            export_matched_to_csv(matched_data)
            export_unmatched_to_csv(matched_data)  # Note: uses same data, filters differently

            # Continue with playlist operations
            log("STEP 5: Starting playlist operations...")
            step = "Step 5"
            use_existing = input("Use existing playlist? (y/n): ").strip().lower()
            if use_existing == 'y':
                playlist_id = input("Enter playlist ID: ").strip()
                if "spotify:playlist:" in playlist_id:
                    playlist_id = playlist_id.split(":")[-1]
                playlist = sp.playlist(playlist_id)
            else:
                playlist = sp.user_playlist_create(
                    user=sp.me()["id"],
                    name="YouTube Favorites 1",
                    public=True
                )

            log("STEP 6: Adding tracks to Spotify...")
            matched_uris = [item["spotify_uri"] for item in matched_data if item["spotify_uri"]]
            sp.playlist_add_items(playlist["id"], matched_uris)

            log(f"Matching completed.")

    except Exception as e:
        log(f"Script failed at step: {step}", "CRITICAL")
        log(f"Error details: {str(e)}", "CRITICAL")

    log(f"Execution time: {time.time() - start_time:.2f} seconds")
