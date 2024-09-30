import os
import yaml
from pymediainfo import MediaInfo

def get_video_codec(file_path):
    media_info = MediaInfo.parse(file_path)
    for track in media_info.tracks:
        if track.track_type == 'Video':
            return track.format

# Scan directory for .mkv files
def scan_directory(directory):
    for root, _, filenames in os.walk(directory):
        dir = os.path.basename(root)
        if "films" in root:
            category = "films"
        else:
            category = "shows"
        for filename in filenames:
            if filename.endswith(".mkv"):
                codec = get_video_codec(os.path.join(root, filename))
                filesizeinbytes = os.path.getsize(os.path.join(root, filename))
                filesize = round(filesizeinbytes / (1024*1024*1024), 2)
                if codec == "Advanced Video Codec" or codec == "AVC":
                    codec = "x264"
                else:
                    codec = "x265"
                    # Return list of files and the directory name
                store_db_items(category, codec, dir, filename, filesize)


def store_db_items(category, codec, directory, file, filesize):
    filename = "/config/db.yaml"
    try:
        with open(filename, 'r') as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        data = {}
    
    if category not in data:
        data[category] = {} 
    if codec not in data[category]:
        data[category][codec] = {}
    if directory not in data[category][codec]:
        data[category][codec][directory] = {}
    data[category][codec][directory][file] = filesize

    if codec == "x264":
        alt = "x265"
        if directory in data[category][alt]:
            # Remove the x265 entry if it exists
            del data[category][alt][directory]
    if codec == "x265":
        alt = "x264"
        if directory in data[category][alt]:
            # Remove the x264 entry if it exists
            del data[category][alt][directory]
    
    with open(filename, 'w') as f:
        yaml.dump(data, f, default_flow_style=False)