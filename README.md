# Youtube to spotify

This is to get regular Youtube playlists (Not Youtube Music playlists) into spotify by:
* Getting list of tracks from YT public playlist into a .csv or .json.
* Search each track and try importing using Spotify api.

## How to use

* Add your keys and ids in an .env file.
  * Reference .env.example. 
* Run main.py.

## To do

* Improve regex pattern for list of songs that were unable to be queried in Spotify.
* If it finds "Album" in it, search for all the songs in the album and add it.
* Have a Youtube Music playlist function.
* If the Youtube video is a playlist:
  * Look through the video description if it has any song titles.
  * Check Youtube comments
  * Check video metadata if it has time markers with the song title in it.