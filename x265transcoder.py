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
from modules.history import start_job, record_file, complete_job
from modules.encoder import resolve_encoder, build_ffmpeg_cmd, get_encoder_display_name

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
    scheduled_job_id = None
    if len(sys.argv) > 8 and sys.argv[8] and sys.argv[8] != "None":
        try:
            scheduled_job_id = int(sys.argv[8])
        except ValueError:
            pass

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

    def store_job(item, job_data):
        logging.debug(f"Storing {item}: {job_data} in /config/job.yaml")
        filename = "/config/job.yaml"
        try:
            with open(filename, 'r') as f:
                data = yaml.safe_load(f)
            data[item] = f"{job_data}"
            with open(filename, 'w') as f:
                yaml.dump(data, f, default_flow_style=False)
        except FileNotFoundError:
            return

    # Function for sending Telegram Message
    def send_telegram_message(message):
        # Grab global telegram variables
        global telegram_token, telegram_chatid

        # Telegram API Token and Chat ID
        token = str(telegram_token or '').strip()
        chat_id = str(telegram_chatid or '').strip()

        if not token or not chat_id or token in ('None', '') or chat_id in ('None', ''):
            logging.info("Telegram notification skipped (token or chat_id not configured).")
            return None

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {"chat_id": chat_id, "text": message}
        try:
            response = requests.post(url, data=data, timeout=15)
            if response.status_code != 200:
                logging.warning(f"Telegram API returned status {response.status_code}: {response.text}")
            return response
        except Exception as e:
            logging.error(f"Failed to send Telegram message: {e}")
            return None
    
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
    # Detect or resolve encoder from config
    encoder_config_value = None
    try:
        with open('/config/config.yaml', 'r') as f:
            app_config = yaml.safe_load(f)
            if app_config:
                encoder_config_value = app_config.get('encoder', None)
    except (FileNotFoundError, yaml.YAMLError):
        pass

    active_encoder = resolve_encoder(encoder_config_value)
    logging.info(f"Encoder: {get_encoder_display_name(active_encoder)}")

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
    logging.debug(f"File list is: {file_list}")

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

        total_files = len(file_list)
        store_job("total_files", total_files)
        progress_percentage = 0
        window_ended = False
        for i, file_path in enumerate(file_list):
            store_job("current_file_number", i+1)
            logging.info(" ")
            jobfailed = ""
            jobsuccessful = ""
            logging.info("")
            convertedname = os.path.basename(file_path)
            filetitle = convertedname
            store_job("current_file", filetitle)
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

                logging.info(f"Transcode config:   Quality={quality}  Encoder={get_encoder_display_name(active_encoder)}")
                #if "films" in mediafolder:
                #    logging.info("We're encoding a film to 10 Bit, will add this to output file name")
                #    outputfile = outputfile.replace('.mkv', '-10bit.mkv')

                logging.info(f"Output file path will be: {outputfile}")

                progress_percentage = int(i / total_files * 100)
                progress_percentage_next_step = int((i + 1) / total_files * 100)

                logging.info("Beginning transcode...")
                starttime = datetime.now()
                cmd = build_ffmpeg_cmd(
                    input_path=f"{file_path}_old",
                    output_path=outputfile,
                    encoder=active_encoder,
                    quality=quality,
                    title=filetitle
                )

                logging.info(f"FFmpeg command: {' '.join(cmd)}")
                process = FfmpegProgress(cmd)
                
                for file_progress_percentage in process.run_command_with_progress():
                    file_progress_rounded = round(file_progress_percentage)
                    logging.debug(f"File Progress: {file_progress_percentage}%")
                    update_progress_yaml("file_progress", file_progress_rounded)

                    if progress_percentage == 0:
                        job_progress_percentage = round(((progress_percentage_next_step - progress_percentage)/100) * file_progress_percentage)
                    else:
                        job_progress_percentage = round(progress_percentage + (((progress_percentage_next_step - progress_percentage)/100) * file_progress_percentage))
                            
                    logging.debug(f"Job Progress: {job_progress_percentage}%")
                    update_progress_yaml("job_progress", job_progress_percentage)

                    # Calculate estimated time remaining for current file
                    if file_progress_percentage > 0:
                        elapsed = (datetime.now() - starttime).total_seconds()
                        estimated_total = elapsed / (file_progress_percentage / 100)
                        eta_seconds = max(0, estimated_total - elapsed)
                        eta_hours = int(eta_seconds // 3600)
                        eta_minutes = int((eta_seconds % 3600) // 60)
                        if eta_hours > 0:
                            eta_display = f"{eta_hours}h {eta_minutes}m"
                        else:
                            eta_display = f"{eta_minutes}m"
                        update_progress_yaml("eta", eta_display)
                    else:
                        update_progress_yaml("eta", "Calculating...")
                    
                # Clear ETA after file transcode completes
                update_progress_yaml("eta", "")
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
                    Failed.append(jobfailed)
                    logging.info("Adding job to failure list")
                    FailedCount += 1
                    # Record failed file in history (size check failed)
                    category, title, season = derive_metadata(file_path, mediafolder)
                    record_file(history_job_id, file_path, convertedname, "failed",
                               category=category, title=title, season=season,
                               original_size_bytes=filesizeinbytes, new_size_bytes=newfilesizeinbytes,
                               original_codec="x264", quality=int(quality),
                               duration_seconds=duration.total_seconds(),
                               failure_reason="New file larger than original")
                else:
                    logging.info("Confirmed new file is smaller than original")
                    #newfileduration = subprocess.check_output(['mediainfo', '--Inform=General;%Duration%', outputfile]).decode().strip()
                    newfileduration = get_video_duration(outputfile)
                    duration_tolerance = int(fileduration) * 0.0005 # 0.05% of original duration
                    if (int(newfileduration) <= int(fileduration) - duration_tolerance) or (int(newfileduration) >= int(fileduration) + duration_tolerance):
                        logging.error("ERROR: New file duration does not match original file Duration!")
                        logging.warning(f"New file Duration: {(newfileduration/60000):.2f} min ({newfileduration}) |  Original file Duration: {(fileduration/60000):.2f} min ({fileduration})")
                        jobfailed = filetitle
                    else:
                        logging.info(f"Confirmed file durations match (tolerance: {duration_tolerance:.0f}ms / 0.015%)")

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
                        # Record failed file in history
                        category, title, season = derive_metadata(file_path, mediafolder)
                        record_file(history_job_id, file_path, convertedname, "failed",
                                   category=category, title=title, season=season,
                                   original_size_bytes=filesizeinbytes, new_size_bytes=newfilesizeinbytes,
                                   original_codec="x264", quality=int(quality),
                                   duration_seconds=duration.total_seconds(),
                                   failure_reason=jobfailed)
                    else:
                        SuccessfulCount += 1
                        Successful.append(filetitle)
                        # Record successful file in history
                        category, title, season = derive_metadata(file_path, mediafolder)
                        record_file(history_job_id, file_path, convertedname, "success",
                                   category=category, title=title, season=season,
                                   original_size_bytes=filesizeinbytes, new_size_bytes=newfilesizeinbytes,
                                   original_codec="x264", quality=int(quality),
                                   duration_seconds=duration.total_seconds())

            elif videocodec == "High Efficiency Video Coding" or videocodec == "HEVC":
                logging.info("This is an x265 file.  Skipping")
                NewFolderSizeBytes += filesizeinbytes
                SkippedCount += 1
                # Record skipped file in history
                category, title, season = derive_metadata(file_path, mediafolder)
                record_file(history_job_id, file_path, convertedname, "skipped",
                           category=category, title=title, season=season,
                           original_size_bytes=filesizeinbytes, original_codec="x265")
            
            progress_percentage = int((i + 1) / total_files * 100)
            logging.debug(f"Progress: {progress_percentage}%")
            update_progress_yaml("job_progress", progress_percentage)

            # Check if this is a scheduled job and the schedule window has ended
            if scheduled_job_id is not None and (i + 1) < total_files:
                try:
                    from modules.scheduler import is_in_schedule_window
                    if not is_in_schedule_window():
                        logging.info("Schedule window has ended. Finishing current file and pausing remaining files for next window.")
                        update_progress_yaml("window_paused", "True")
                        window_ended = True
                        break
                except Exception as e:
                    logging.warning(f"Error checking schedule window: {e}")
        
        oldfoldersize = round(OldFolderSizeBytes / (1024**3), 2)
        newfoldersize = round(NewFolderSizeBytes / (1024**3), 2)
        saved_bytes = max(0, OldFolderSizeBytes - NewFolderSizeBytes)
        saved_gb = round(saved_bytes / (1024**3), 2)
        saved_pct = round((saved_bytes / OldFolderSizeBytes) * 100, 1) if OldFolderSizeBytes > 0 else 0.0
        TotalFiles = SuccessfulCount + FailedCount + SkippedCount

        # Friendly display name for folder
        parts = [p for p in mediafolder.replace('\\', '/').split('/') if p]
        if len(parts) >= 2 and parts[-2].lower() not in ('films', 'shows', 'media'):
            folder_display = f"{parts[-2]} - {parts[-1]}"
        elif parts:
            folder_display = parts[-1]
        else:
            folder_display = mediafolder

        # Breakdown summary
        breakdown = []
        if SuccessfulCount > 0:
            breakdown.append(f"{SuccessfulCount} succeeded")
        if FailedCount > 0:
            breakdown.append(f"{FailedCount} failed")
        if SkippedCount > 0:
            breakdown.append(f"{SkippedCount} skipped")
        breakdown_str = f" ({', '.join(breakdown)})" if breakdown else ""

        # Update scheduled job status if running in scheduled mode
        if scheduled_job_id is not None:
            try:
                from modules.scheduler import mark_job_paused, mark_job_completed
                if window_ended:
                    remaining_files = file_list[i + 1:]
                    remaining_count = len(remaining_files)
                    remaining_bytes = sum(os.path.getsize(f) for f in remaining_files if os.path.exists(f))
                    logging.info(f"Scheduled job #{scheduled_job_id} paused at window close ({remaining_count} files remaining, {round(remaining_bytes/(1024**3), 2)} GB). Marking paused for next window.")
                    mark_job_paused(scheduled_job_id, remaining_count=remaining_count, remaining_bytes=remaining_bytes)
                else:
                    logging.info(f"Scheduled job #{scheduled_job_id} completed all files.")
                    mark_job_completed(scheduled_job_id)
            except Exception as e:
                logging.error(f"Error updating scheduled job status: {e}")

        # Clear ETA now that the job is complete
        update_progress_yaml("eta", "")

        # Record job completion in history database (before Telegram, in case messaging fails)
        complete_job(history_job_id, TotalFiles, SuccessfulCount, FailedCount, SkippedCount,
                     OldFolderSizeBytes, NewFolderSizeBytes)

        if window_ended:
            logging.info("Window ended. Sending Telegram update...")
            failures_icon = "✅" if FailedCount == 0 else "❌"
            failures_status = "None" if FailedCount == 0 else f"Yes ({FailedCount} failed)"
            msg = (
                f"⏳ Scheduled Transcode Window Ended: {folder_display}\n"
                f"📁 Path: {mediafolder}\n\n"
                f"Schedule window closed. Finished current file.\n"
                f"Outstanding files will automatically resume in the next window.\n\n"
                f"📄 Number of files: {TotalFiles} of {total_files} processed{breakdown_str}\n"
                f"{failures_icon} Failures: {failures_status}\n"
                f"💾 Total size saved so far: {saved_gb} GB ({saved_pct}%)\n"
                f"📊 Original Size: {oldfoldersize} GB ➔ New Size: {newfoldersize} GB"
            )
            send_telegram_message(msg)
        elif FailedCount > 0:
            logging.warning(f"Some Jobs May have Failed: {Failed}")
            logging.info("Sending Telegram Message...")
            failed_list = "\n".join(f"• {os.path.basename(str(f))}" for f in Failed)
            msg = (
                f"⚠️ Transcode Completed with Failures: {folder_display}\n"
                f"📁 Path: {mediafolder}\n\n"
                f"📄 Number of files: {TotalFiles}{breakdown_str}\n"
                f"❌ Failures: Yes ({FailedCount} failed)\n"
                f"💾 Total size saved: {saved_gb} GB ({saved_pct}%)\n"
                f"📊 Original Size: {oldfoldersize} GB ➔ New Size: {newfoldersize} GB\n\n"
                f"Failed files:\n{failed_list}"
            )
            send_telegram_message(msg)
        else:
            logging.info(f"All Jobs Succeeded: {Successful}")
            logging.info("Sending Telegram Message...")
            msg = (
                f"🎬 Transcode Completed: {folder_display}\n"
                f"📁 Path: {mediafolder}\n\n"
                f"📄 Number of files: {TotalFiles}{breakdown_str}\n"
                f"✅ Failures: None\n"
                f"💾 Total size saved: {saved_gb} GB ({saved_pct}%)\n"
                f"📊 Original Size: {oldfoldersize} GB ➔ New Size: {newfoldersize} GB"
            )
            send_telegram_message(msg)

        # If job completed successfully, run an incremental recommendations scan to remove transcoded items
        if FailedCount == 0 and not window_ended:
            logging.info("Job finished successfully. Triggering incremental recommendations scan...")
            try:
                from modules.scanner import run_scan
                scan_cfg = None
                if os.path.exists('/config/config.yaml'):
                    with open('/config/config.yaml', 'r') as f:
                        scan_cfg = yaml.safe_load(f)
                libs = (scan_cfg or {}).get('libraries', {})
                if libs:
                    run_scan(libs)
                    logging.info("Incremental scan complete. Recommendations updated.")
                else:
                    logging.warning("No libraries configured in config.yaml; skipping post-transcode scan.")
            except Exception as e:
                logging.error(f"Post-transcode recommendations scan failed: {e}")

    logging.info("Done.")

    # --- History tracking helpers ---
    def derive_metadata(file_path, mediafolder):
        """Derive category, title, and season from a file path."""
        # Determine category from the mediafolder path
        if "/films" in mediafolder.lower() or "\\films" in mediafolder.lower():
            category = "films"
        else:
            category = "shows"

        # Derive title and season from relative path
        rel_path = os.path.relpath(file_path, mediafolder)
        parts = rel_path.replace("\\", "/").split("/")
        title = parts[0] if parts else os.path.basename(file_path)
        season = parts[1] if category == "shows" and len(parts) > 2 else None
        return category, title, season

    # Start a history job record
    history_job_id = start_job(mediafolder, quality, delete)
    logging.info(f"History job ID: {history_job_id}")

    if file_list != []:
        #Run the Convert Job
        logging.info("running convert job...")
        convert_job(file_list)
    else:
        logging.warning("file list is empty.  Is mediafolder and include criteria correct?")
        # Complete the job with zero counts
        complete_job(history_job_id, 0, 0, 0, 0, 0, 0)
        if scheduled_job_id is not None:
            try:
                from modules.scheduler import mark_job_completed
                mark_job_completed(scheduled_job_id)
            except Exception:
                pass