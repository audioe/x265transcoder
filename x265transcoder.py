import sys
import subprocess
import shutil
import os
import yaml
from datetime import datetime
import requests
import re
from pymediainfo import MediaInfo
import logging
from ffmpeg_progress_yield import FfmpegProgress

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

    # Set up logging
    Logfilepath = "/logs"
    Date = datetime.now().strftime("%d-%m-%y_%H-%M-%S")
    Logfile = f"{Logfilepath}/transcode_{Date}.log"
    logging.basicConfig(format='%(levelname)s | %(asctime)s: %(message)s', datefmt='%d/%m/%Y %I:%M:%S %p', filename=Logfile, encoding='utf-8', level=logging.DEBUG)

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
    
    # Function for updating the progress
    def update_progress_yaml(item, progress):
        """Updates the progress in a YAML file.
        Args:
            progress: The progress percentage.
        """
        filename = "/config/job.yaml"
        with open(filename, 'r') as f:
            data = yaml.safe_load(f)
        data[item] = f"{progress}"
        with open(filename, 'w') as f:
            yaml.dump(data, f, default_flow_style=False)

    # MAIN
    logging.info(f"Starting Script Version {Version}.  Working in {mediafolder}, and looking for {include}")
    if delete == "Yes":
        logging.info("Deleting files is enabled.")
    else:
        logging.info("Deleting files is disabled.")

    logging.debug(f"Telegram token passed through is {telegram_token}.  ChatID is {telegram_chatid}")
    logging.info("Defining functions...")

    logging.debug("get_files")
    # get list of files in mediafolder and store as files
    def get_files(mediafolder, include_pattern):
        fs = []
        for root, dirs, files in os.walk(mediafolder):
            for file in files:
                if include_pattern in file[-len(include_pattern):]:
                    print(file)
                    fs.append(os.path.join(root, file))
        return fs

    logging.debug("get_video_codec...")
    def get_video_codec(file_path):
        media_info = MediaInfo.parse(file_path)
        for track in media_info.tracks:
            if track.track_type == 'Video':
                return track.format

    logging.debug("get_frame_count...")
    def get_frame_count(file_path):
        media_info = MediaInfo.parse(file_path)
        for track in media_info.tracks:
            if track.track_type == 'Video':
                return int(track.frame_count)

    logging.debug("video_duration...")
    # Gets duration in Milliseconds        
    def get_video_duration(file_path):
        media_info = MediaInfo.parse(file_path)
        for track in media_info.tracks:
            if track.track_type == 'Video':
                return float(track.duration)

    logging.info("Done.")
    logging.info("Fetching file list...")
    file_list = get_files(mediafolder, include)

    logging.info("Done.")
    logging.info("Creating convert job function...")
    def convert_job(file_list):
        logging.debug("file list is:")
        logging.debug(f"{file_list}")
        logging.debug("grabbing global variables")
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
        logging.info("Done.")
        #For HW Encoding, use "-rc_mode CQP -global_quality 18" instead of crf
        params = f"repeat-headers=1:profile=main10:level=5.1"

        total_files = len(file_list)
        progress_percentage = 0
        update_progress_yaml("job_progress", 0)
        update_progress_yaml("file_progress", 0)
        for i, file_path in enumerate(file_list):
            logging.info(" ")
            jobfailed = ""
            jobsuccessful = ""
            logging.info("")
            convertedname = os.path.basename(file_path)
            filetitle = convertedname
            amendedname_path = file_path + "_old"
            logging.info(f"Working on file: {convertedname}.")
            logging.debug(f"File path: {file_path}")
            #videocodec = subprocess.check_output(['mediainfo', '--Inform=Video;%Format/Info%', file_path]).decode().strip()
            videocodec = get_video_codec(file_path)
            logging.info(f"Original Codec: {videocodec}")
            filesizeinbytes = os.path.getsize(file_path)
            logging.info(f"Original Size: {filesizeinbytes} bytes")
            OldFolderSizeBytes += filesizeinbytes
            filesize = round(filesizeinbytes / (1024*1024*1024), 2)
            logging.info(f"Original Size: {filesize} GB")
            #fileframecount = subprocess.check_output(['mediainfo', '--Inform=Video;%FrameCount%', file_path]).decode().strip()
            fileframecount = get_frame_count(file_path)
            logging.info(f"Original Frame Count: {fileframecount}")
            #fileduration = subprocess.check_output(['mediainfo', '--Inform=General;%Duration%', file_path]).decode().strip()
            fileduration = get_video_duration(file_path) 
            logging.info(f"Original Duration: {fileduration}")
            # Check if file is x264
            if videocodec == "Advanced Video Codec" or videocodec == "AVC":
                logging.info("This is an x264 file.")
                logging.info(f"Renaming to `{convertedname}_old`")
                # Rename file to include _old
                shutil.move(file_path, amendedname_path)
                #os.rename(file_path, f"{os.path.dirname(file_path)}/{convertedname}_old")

                if "264" in convertedname:
                    logging.info("File name contains `x264`.  Will replace this with `x265` in transcoded file")
                    outputfile = os.path.join(os.path.dirname(file_path), convertedname.replace('264', '265'))
                else:
                    outputfile = file_path

                logging.info(f"Transcode config:   Quality={quality}")
                #if "films" in mediafolder:
                #    logging.info("We're encoding a film to 10 Bit, will add this to output file name")
                #    outputfile = outputfile.replace('.mkv', '-10bit.mkv')

                logging.info(f"Output file path will be: {outputfile}")

                progress_percentage = int(i / total_files * 100)
                progress_percentage_next_step = int((i + 1) / total_files * 100)

                logging.info("Beginning transcode...")
                starttime = datetime.now()
                cmd = [
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
                    "-stats_period", "15",
                    outputfile
                ]

                process = FfmpegProgress(cmd)
                
                for file_progress_percentage in process.run_command_with_progress():
                    logging.debug(f"File Progress: {file_progress_percentage}%")
                    update_progress_yaml("file_progress", file_progress_percentage)

                    if progress_percentage == 0:
                        job_progress_percentage = round(((progress_percentage_next_step - progress_percentage)/100) * file_progress_percentage)
                    else:
                        job_progress_percentage = round(progress_percentage + (((progress_percentage_next_step - progress_percentage)/100) * file_progress_percentage))
                            
                    logging.debug(f"Job Progress: {job_progress_percentage}%")
                    update_progress_yaml("job_progress", job_progress_percentage)
                    
                newfilesizeinbytes = os.path.getsize(outputfile)
                NewFolderSizeBytes += newfilesizeinbytes
                newfilesize = round(newfilesizeinbytes / (1024*1024*1024), 2)
                percdiff = round(((newfilesizeinbytes/filesizeinbytes)-1)*100, 2)
                endtime = datetime.now()
                duration = endtime - starttime
                logging.info(f"Transcode complete.  Duration: {duration.seconds // 3600} Hrs {duration.seconds // 60} Mins.")

                logging.info("Running post-transcode checks...")
                if newfilesize > filesize:
                    logging.error("ERROR: New file size is larger than original file size!")
                    logging.warning(f"New file Size: {newfilesize} GB  |  Original file Size: {filesize} GB")
                    jobfailed = filetitle
                else:
                    logging.info("Confirmed new file is smaller than original")
                    #newfileduration = subprocess.check_output(['mediainfo', '--Inform=General;%Duration%', outputfile]).decode().strip()
                    newfileduration = get_video_duration(outputfile)
                    if (int(newfileduration) <= int(fileduration) - 50) or (int(newfileduration) >= int(fileduration) + 50):
                        logging.error("ERROR: New file duration does not match original file Duration!")
                        logging.warning(f"New file Duration: {(newfileduration/60000):.2f} min ({newfileduration}) |  Original file Duration: {(fileduration/60000):.2f} min ({fileduration})")
                        jobfailed = filetitle
                    else:
                        logging.info("Confirmed file durations match")

                    #newfileframecount = subprocess.check_output(['mediainfo', '--Inform=Video;%FrameCount%', outputfile]).decode().strip()
                    newfileframecount = get_frame_count(outputfile)
                    if (int(newfileframecount) < int(fileframecount) * 0.9989) or (int(newfileframecount) > int(fileframecount) * 1.0011):
                        logging.warning("WARNING: Frame Count mismatch between original and new files!")
                        jobfailed = filetitle
                    else:
                        logging.info(f"Frame Counts Verified to margin of 0.11%")

                    logging.info(f"New file Frame Count: {newfileframecount}  |  Original file frame count: {fileframecount}")
                    logging.info(f"Done.  New file size is {newfilesize} GB.  {percdiff}% smaller.")
                    if delete == "Yes":
                        logging.info("Deleting original file...")
                        try:
                            os.remove(file_path + "_old")
                            logging.info("Done.")
                        except:
                            logging.error("ERROR: Unable to delete original file")

                    if jobfailed != "":
                        Failed.append(jobfailed)
                        logging.info("Adding job to failure list")
                        FailedCount += 1
                    else:
                        SuccessfulCount += 1

            elif videocodec == "High Efficiency Video Coding" or videocodec == "HEVC":
                logging.info("This is an x265 file.  Skipping")
                NewFolderSizeBytes += filesizeinbytes
                SkippedCount += 1
            
            progress_percentage = int((i + 1) / total_files * 100)
            logging.debug(f"Progress: {progress_percentage}%")
            update_progress_yaml("job_progress", progress_percentage)
        
        oldfoldersize = round(OldFolderSizeBytes / (1024*1024*1024), 2)
        newfoldersize = round(NewFolderSizeBytes / (1024*1024*1024), 2)
        folderpercdiff = round(((NewFolderSizeBytes/OldFolderSizeBytes)-1)*100, 2)
        TotalFiles = SuccessfulCount + FailedCount + SkippedCount

        if FailedCount > 0:
            logging.warning(f"Some Jobs May have Failed: {Failed}")
            logging.info("Sending Telegram Message...")
            send_telegram_message (f"Transcode Job for {mediafolder} completed with failures.\n\n{SuccessfulCount} Succeeded | {FailedCount} Failed | {SkippedCount} Skipped\n\nThe following files failed verification: {Failed}")
        else:
            logging.info(f"All Jobs Succeeded: {Successful}")
            logging.info("Sending Telegram Message...")
        send_telegram_message (f"Transcode Job for {mediafolder} completed successfully.\n\n{SuccessfulCount} Succeeded | {SkippedCount} Skipped\n\nOriginal Directory Size: {oldfoldersize} GB\nNew Directory Size: {newfoldersize} GB\nSpace Saved: {folderpercdiff}%")

    logging.info("Done.")

    if file_list != []:
        #Run the Convert Job
        logging.info("running convert job...")
        convert_job(file_list)
    else:
        logging.warning("file list is empty.  Is mediafolder and include criteria correct?")