import sys
import subprocess
import shutil
import os
from datetime import datetime
import requests
from pymediainfo import MediaInfo

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python your_script.py <folder> <include> <quality>")
        sys.exit(1)

    mediafolder = sys.argv[1]
    include = sys.argv[2]
    quality = sys.argv[3]
    delete = sys.argv[4]
    telegram_token = sys.argv[5]
    telegram_chatid = sys.argv[6]
    version = sys.argv[7]

    OldFolderSizeBytes = 0
    NewFolderSizeBytes = 0
    Successful = []
    SuccessfulCount = 0
    Failed = []
    FailedCount = 0
    SkippedCount = 0

    Version = version
    Logfilepath = "/logs"
    Date = datetime.now().strftime("%d-%m-%y_%H-%M-%S")
    Logfile = f"{Logfilepath}/transcode_{Date}.log"

    # Function for creating and writing events out to a logfile
    def log(string):
        with open(Logfile, "a") as f:
            f.write(f"{datetime.now().strftime('%d/%m/%y %H:%M:%S')}: {string}\n")

    # Function for sending Telegram Message
    def send_telegram_message(message):
        # Grab global telegram variables
        global telegram_token, telegram_chatid

        # Telegram API Token and Chat ID
        token = telegram_token
        chat_id = telegram_chatid
        #
        # Send the message
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {"chat_id": chat_id, "text": message}
        response = requests.post(url, data=data)
        return response

    # MAIN
    log(f"Starting Script Version {Version}.  Working in {mediafolder}, and looking for {include}")
    if delete == "Yes":
        log("Deleting files is enabled.")
    else:
        log("Deleting files is disabled.")

    log(f"Telegram token passed through is {telegram_token}.  ChatID is {telegram_chatid}")
    log("Defining functions...")

    log("get_files")
    # get list of files in mediafolder and store as files
    def get_files(mediafolder, include_pattern):
        fs = []
        for root, dirs, files in os.walk(mediafolder):
            for file in files:
                if include_pattern in file[-len(include_pattern):]:
                    print(file)
                    fs.append(os.path.join(root, file))
        return fs

    log("get_video_codec...")
    def get_video_codec(file_path):
        media_info = MediaInfo.parse(file_path)
        for track in media_info.tracks:
            if track.track_type == 'Video':
                return track.format

    log("get_frame_count...")
    def get_frame_count(file_path):
        media_info = MediaInfo.parse(file_path)
        for track in media_info.tracks:
            if track.track_type == 'Video':
                return int(track.frame_count)

    log("video_duration...")
    # Gets duration in Milliseconds        
    def get_video_duration(file_path):
        media_info = MediaInfo.parse(file_path)
        for track in media_info.tracks:
            if track.track_type == 'Video':
                return float(track.duration)

    log("done.")
    log("fetching file list...")
    file_list = get_files(mediafolder, include)

    log("done.")
    log("Creating convert job function...")
    def convert_job(file_list):
        log("file list is:")
        log(f"{file_list}")
        log("grabbing global variables")
        # Grab global variables 
        global OldFolderSizeBytes
        global NewFolderSizeBytes
        global Successful
        global SuccessfulCount
        global Failed
        global FailedCount
        global SkippedCount
        global quality
        global delete
        log("done.")
        #For HW Encoding, use "-rc_mode CQP -global_quality 18" instead of crf
        params = f"repeat-headers=1:profile=main10:level=5.1"
        for file_path in file_list:
            log(" ")
            jobfailed = ""
            jobsuccessful = ""
            convertedname = os.path.basename(file_path)
            filetitle = convertedname
            amendedname_path = file_path + "_old"
            log(f"Working on file: {convertedname}.")
            log(f"File path: {file_path}")
            #videocodec = subprocess.check_output(['mediainfo', '--Inform=Video;%Format/Info%', file_path]).decode().strip()
            videocodec = get_video_codec(file_path)
            log(f"Original Codec: {videocodec}")
            filesizeinbytes = os.path.getsize(file_path)
            log(f"Original Size: {filesizeinbytes} bytes")
            OldFolderSizeBytes += filesizeinbytes
            filesize = round(filesizeinbytes / (1024*1024*1024), 2)
            log(f"Original Size: {filesize} GB")
            #fileframecount = subprocess.check_output(['mediainfo', '--Inform=Video;%FrameCount%', file_path]).decode().strip()
            fileframecount = get_frame_count(file_path)
            log(f"Original Frame Count: {fileframecount}")
            #fileduration = subprocess.check_output(['mediainfo', '--Inform=General;%Duration%', file_path]).decode().strip()
            fileduration = get_video_duration(file_path) 
            log(f"Original Duration: {fileduration}")
            # Check if file is x264
            if videocodec == "Advanced Video Codec" or videocodec == "AVC":
                log("This is an x264 file.")
                log(f"Renaming to `{convertedname}_old`")
                # Rename file to include _old
                shutil.move(file_path, amendedname_path)
                #os.rename(file_path, f"{os.path.dirname(file_path)}/{convertedname}_old")

                if "264" in convertedname:
                    log("File name contains `x264`.  Will replace this with `x265` in transcoded file")
                    outputfile = os.path.join(os.path.dirname(file_path), convertedname.replace('264', '265'))
                else:
                    outputfile = file_path

                log(f"Transcode config:   Quality={quality}")
                #if "films" in mediafolder:
                #    log("We're encoding a film to 10 Bit, will add this to output file name")
                #    outputfile = outputfile.replace('.mkv', '-10bit.mkv')

                log(f"Output file path will be: {outputfile}")
                log("Beginning transcode...")
                starttime = datetime.now()
                subprocess.run([
                    "/usr/lib/jellyfin-ffmpeg/ffmpeg",
                    "-c:v", "h264_qsv",
                    "-i", f"{file_path}_old",
                    "-pix_fmt", "p010le",
                    "-map_chapters", "0",
                    "-metadata", f"title={filetitle}",
                    "-map", "0:0",  # Corrected placement of mapping
                    "-c:v", "hevc_qsv",
                    "-x265-params", f"{params}",
                    "-map", "0:a",  # Corrected placement of mapping
                    "-rc_mode", "CQP",
                    "-global_quality", f"{quality}",
                    "-c:a", "copy",
                    "-preset", "fast",
                    outputfile
                ])

                newfilesizeinbytes = os.path.getsize(outputfile)
                NewFolderSizeBytes += newfilesizeinbytes
                newfilesize = round(newfilesizeinbytes / (1024*1024*1024), 2)
                percdiff = round(((newfilesizeinbytes/filesizeinbytes)-1)*100, 2)
                endtime = datetime.now()
                duration = endtime - starttime
                log(f"Transcode complete.  Duration: {duration.seconds // 3600} Hrs {duration.seconds // 60} Mins.")

                log("Running post-transcode checks...")
                if newfilesize > filesize:
                    log("ERROR: New file size is larger than original file size!")
                    log(f"New file Size: {newfilesize} GB  |  Original file Size: {filesize} GB")
                    jobfailed = filetitle
                else:
                    log("Confirmed new file is smaller than original")
                    #newfileduration = subprocess.check_output(['mediainfo', '--Inform=General;%Duration%', outputfile]).decode().strip()
                    newfileduration = get_video_duration(outputfile)
                    if (int(newfileduration) <= int(fileduration) - 50) or (int(newfileduration) >= int(fileduration) + 50):
                        log("ERROR: New file duration does not match original file Duration!")
                        log(f"New file Duration: {(newfileduration/60000):.2f} min ({newfileduration}) |  Original file Duration: {(fileduration/60000):.2f} min ({fileduration})")
                        jobfailed = filetitle
                    else:
                        log("Confirmed file durations match")

                    #newfileframecount = subprocess.check_output(['mediainfo', '--Inform=Video;%FrameCount%', outputfile]).decode().strip()
                    newfileframecount = get_frame_count(outputfile)
                    if (int(newfileframecount) < int(fileframecount) * 0.9989) or (int(newfileframecount) > int(fileframecount) * 1.0011):
                        log("WARNING: Frame Count mismatch between original and new files!")
                        jobfailed = filetitle
                    else:
                        log(f"Frame Counts Verified to margin of 0.11%")

                    log(f"New file Frame Count: {newfileframecount}  |  Original file frame count: {fileframecount}")
                    log(f"Done.  New file size is {newfilesize} GB.  {percdiff}% smaller.")
                    if delete == "Yes":
                        log("Deleting original file...")
                        try:
                            os.remove(file_path + "_old")
                            log("done.")
                        except:
                            log("ERROR: Unable to delete original file")

                    if jobfailed != "":
                        Failed.append(jobfailed)
                        log("Adding job to failure list")
                        FailedCount += 1
                    else:
                        SuccessfulCount += 1

            elif videocodec == "High Efficiency Video Coding" or videocodec == "HEVC":
                log("This is an x265 file.  Skipping")
                NewFolderSizeBytes += filesizeinbytes
                SkippedCount += 1
        
        oldfoldersize = round(OldFolderSizeBytes / (1024*1024*1024), 2)
        newfoldersize = round(NewFolderSizeBytes / (1024*1024*1024), 2)
        folderpercdiff = round(((NewFolderSizeBytes/OldFolderSizeBytes)-1)*100, 2)
        TotalFiles = SuccessfulCount + FailedCount + SkippedCount

        if FailedCount > 0:
            log(f"Some Jobs May have Failed: {Failed}")
            log ("Sending Telegram Message...")
            send_telegram_message (f"Transcode Job for {mediafolder} completed with failures.\n\n{SuccessfulCount} Succeeded | {FailedCount} Failed | {SkippedCount} Skipped\n\nThe following files failed verification: {Failed}")
        else:
            log(f"All Jobs Succeeded: {Successful}")
            log ("Sending Telegram Message...")
        send_telegram_message (f"Transcode Job for {mediafolder} completed successfully.\n\n{SuccessfulCount} Succeeded | {SkippedCount} Skipped\n\nOriginal Directory Size: {oldfoldersize} GB\nNew Directory Size: {newfoldersize} GB\nSpace Saved: {folderpercdiff}%")

    log("done.")

    if file_list != []:
        #Run the Convert Job
        log("running convert job...")
        convert_job(file_list)
    else:
        log("file list is empty.  Is mediafolder and include criteria correct?")