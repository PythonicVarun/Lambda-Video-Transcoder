import base64
import hashlib
import json
import logging
import math
import mimetypes
import os
import re
import subprocess
import tempfile
import time
import urllib.parse

import boto3
from boto3.s3.transfer import TransferConfig
from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

s3 = boto3.client("s3")
transcribe = boto3.client("transcribe")

app = Flask(__name__)

# Configure logging
logger = logging.getLogger(__name__)
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logger.setLevel(log_level)

GB = 1024**3
S3_TRANSFER_CONFIG = TransferConfig(multipart_threshold=1 * GB)

ENV_TRUE = ["t", "true", "1", "yes"]

# Bucket Name
BUCKET_NAME = os.environ.get("BUCKET_NAME")

GENERATE_DASH = os.environ.get("GENERATE_DASH", "true").lower() in ENV_TRUE
GENERATE_SUBTITLES = os.environ.get("GENERATE_SUBTITLES", "true").lower() in ENV_TRUE

THUMBNAIL_WIDTH = int(os.environ.get("THUMBNAIL_WIDTH", 1280))

LAMBDA_FUNCTION_URL = os.environ.get("LAMBDA_FUNCTION_URL", "").rstrip("/")

# Paths for binaries in Lambda Layer
FFMPEG = "/opt/bin/ffmpeg"
FFPROBE = "/opt/bin/ffprobe"

# Allowed Transcribe media formats -> [amr, flac, wav, ogg, mp3, mp4, webm, m4a]
TRANSCRIBE_SUPPORTED_MEDIA_FORMATS = [
    "audio/amr", "audio/x-amr",                     # amr
    "audio/flac", "audio/x-flac",                   # flac
    "audio/wav", "audio/x-wav",                     # wav
    "video/ogg", "audio/ogg", "application/ogg",    # ogg
    "audio/mpeg",                                   # mp3
    "audio/mp4", "video/mp4",                       # mp4
    "audio/webm", "video/webm",                     # webm
    "audio/m4a", "video/m4a"                        # m4a
]

# Video presets ordered highest→lowest
ALL_PRESETS = [
    {"name": "2160p", "width": 3840, "height": 2160, "bitrate": "8000k"},
    {"name": "1440p", "width": 2560, "height": 1440, "bitrate": "5000k"},
    {"name": "1080p", "width": 1920, "height": 1080, "bitrate": "3500k"},
    {"name": "720p", "width": 1280, "height": 720, "bitrate": "2500k"},
    {"name": "480p", "width": 854, "height": 480, "bitrate": "1200k"},
    {"name": "360p", "width": 640, "height": 360, "bitrate": "800k"},
]

# Sprite config
SPRITE_FPS = 1
SPRITE_ROWS = 10
SPRITE_COLUMNS = 10
SPRITE_INTERVAL = 1  # seconds between frames in sprite
SPRITE_SCALE_W = 178


# Transcription language
LANGUAGE_OPTIONS = ["en-IN", "hi-IN"]


def lambda_handler(event, context):
    logger.debug("Received event: %s", json.dumps(event, indent=2))
    if "Records" in event and event["Records"][0].get("s3"):
        logger.info("Processing S3 event")
        return process_video(event)
    elif event.get("requestContext", {}).get("http"):
        # This is a request from API Gateway
        logger.info("Processing API Gateway request")
        import serverless_wsgi

        return serverless_wsgi.handle_request(app, event, context)
    else:
        logger.info("Processing stream handler request")
        return stream_handler(event)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "video" not in request.files:
        logger.warning("Upload attempt with no video file.")
        return "No video file found", 400

    video = request.files["video"]
    if video.filename == "":
        logger.warning("Upload attempt with no file selected.")
        return "No selected file", 400

    if video and BUCKET_NAME:
        filename = video.filename
        logger.info("Uploading '%s' to bucket '%s'", filename, BUCKET_NAME)
        s3.upload_fileobj(video, BUCKET_NAME, f"uploads/{filename}")
        logger.info("Successfully uploaded '%s'", filename)
        return redirect(url_for("status", video_id=filename))

    logger.error("Upload failed due to missing video or bucket configuration.")
    return "Something went wrong", 500


@app.route("/create_multipart_upload", methods=["POST"])
def create_multipart_upload():
    data = request.get_json()
    file_name = data.get("fileName")
    file_size = data.get("fileSize")

    if not file_name or not file_size:
        logger.warning("Create multipart upload request missing fileName or fileSize.")
        return jsonify({"error": "fileName and fileSize are required"}), 400

    # 5MB chunks
    CHUNK_SIZE = 5 * 1024 * 1024
    num_parts = math.ceil(file_size / CHUNK_SIZE)
    key = f"uploads/{file_name}"

    try:
        logger.info(
            "Creating multipart upload for '%s' (size: %d, parts: %d)",
            file_name,
            file_size,
            num_parts,
        )
        response = s3.create_multipart_upload(Bucket=BUCKET_NAME, Key=key)
        upload_id = response["UploadId"]

        presigned_urls = []
        for i in range(1, num_parts + 1):
            presigned_url = s3.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": BUCKET_NAME,
                    "Key": key,
                    "UploadId": upload_id,
                    "PartNumber": i,
                },
                ExpiresIn=3600,  # 1 hour
            )
            presigned_urls.append(presigned_url)

        logger.info(
            "Successfully created multipart upload %s for '%s'", upload_id, file_name
        )
        return jsonify({"uploadId": upload_id, "urls": presigned_urls})
    except Exception as e:
        logger.error(
            "Error creating multipart upload for '%s': %s", file_name, e, exc_info=True
        )
        return jsonify({"error": str(e)}), 500


@app.route("/complete_multipart_upload", methods=["POST"])
def complete_multipart_upload():
    data = request.get_json()
    file_name = data.get("fileName")
    upload_id = data.get("uploadId")
    parts = data.get("parts")

    if not file_name or not upload_id or not parts:
        logger.warning(
            "Complete multipart upload request missing fileName, uploadId, or parts."
        )
        return jsonify({"error": "fileName, uploadId, and parts are required"}), 400

    key = f"uploads/{file_name}"

    try:
        logger.info("Completing multipart upload %s for '%s'", upload_id, file_name)
        s3.complete_multipart_upload(
            Bucket=BUCKET_NAME,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
        logger.info("Successfully completed multipart upload for '%s'", file_name)
        return jsonify({"status": "success", "video_id": file_name})
    except Exception as e:
        logger.error(
            "Error completing multipart upload for '%s': %s",
            file_name,
            e,
            exc_info=True,
        )
        return jsonify({"error": str(e)}), 500


@app.route("/status/<video_id>")
def status(video_id):
    if not BUCKET_NAME:
        logger.error("Cannot get status: BUCKET_NAME not configured.")
        return (
            jsonify({"status": "error", "message": "Bucket name not configured"}),
            500,
        )

    process_id = None
    manifest_key = None
    # Heuristic to check if video_id is a process_id (MD5 hash)
    if len(video_id) == 32 and all(c in "0123456789abcdef" for c in video_id.lower()):
        logger.debug("Treating '%s' as a potential process_id", video_id)
        process_id = video_id
        manifest_key = f"processed/{process_id}/manifest.json"

    try:
        if not manifest_key:
            base_name, _ = os.path.splitext(video_id)
            redirect_key = f"processed/{base_name}.json"
            logger.debug(
                "Looking for redirect file for '%s' at '%s'", video_id, redirect_key
            )

            redirect_obj = s3.get_object(Bucket=BUCKET_NAME, Key=redirect_key)
            redirect_data = json.loads(redirect_obj["Body"].read().decode("utf-8"))
            process_id = redirect_data.get("process_id")

            if not process_id:
                logger.error("process_id not found in redirect file %s", redirect_key)
                return (
                    jsonify(
                        {"status": "error", "message": "Invalid processing reference"}
                    ),
                    500,
                )
            manifest_key = f"processed/{process_id}/manifest.json"

        logger.debug("Checking status for '%s' at '%s'", video_id, manifest_key)

        # Get the actual manifest
        response = s3.get_object(Bucket=BUCKET_NAME, Key=manifest_key)
        manifest_data = response["Body"].read().decode("utf-8")
        manifest = json.loads(manifest_data)
        logger.debug("Found manifest for '%s': %s", video_id, manifest)

        # Check transcription status
        transcription_job_name = manifest.get("transcription_job")
        if GENERATE_SUBTITLES and transcription_job_name:
            try:
                job_status_response = transcribe.get_transcription_job(
                    TranscriptionJobName=transcription_job_name
                )
                job_status = job_status_response["TranscriptionJob"]
                transcription_status = job_status["TranscriptionJobStatus"]
                manifest["transcription_status"] = transcription_status
                logger.info(
                    "Transcription status for '%s' is %s",
                    video_id,
                    transcription_status,
                )

                if transcription_status == "COMPLETED":
                    transcript_s3_uri = job_status["Transcript"]["TranscriptFileUri"]
                    transcript_key = "/".join(transcript_s3_uri.split("/")[3:])
                    manifest["transcript_key"] = transcript_key
                elif transcription_status == "FAILED":
                    failure_reason = job_status.get("FailureReason")
                    manifest["transcription_failure_reason"] = failure_reason
                    logger.warning(
                        "Transcription failed for '%s': %s", video_id, failure_reason
                    )

                if transcription_status in ["COMPLETED", "FAILED"]:
                    # Clean up the temporary audio file if it exists
                    process_id = manifest.get("process_id")
                    if process_id:
                        audio_key = f"processed/{process_id}/audio.wav"
                        try:
                            # Check if the converted audio file exists before deleting
                            s3.head_object(Bucket=BUCKET_NAME, Key=audio_key)
                            logger.info(
                                "Transcription complete, deleting temporary audio file: %s",
                                audio_key,
                            )
                            s3.delete_object(Bucket=BUCKET_NAME, Key=audio_key)
                        except s3.exceptions.ClientError as e:
                            if e.response["Error"]["Code"] == "NoSuchKey":
                                # File doesn't exist, which is fine.
                                pass
                            else:
                                # Another S3 error occurred
                                logger.warning(
                                    "Error checking/deleting temporary audio file %s: %s",
                                    audio_key,
                                    e,
                                )

            except transcribe.exceptions.NotFoundException:
                logger.warning(
                    "Transcription job '%s' not found.", transcription_job_name
                )
                manifest["transcription_status"] = "NOT_FOUND"

        return jsonify(manifest)
    except s3.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            # This could be either the redirect file or the manifest file not found.
            # If either is not found, we check if the original upload exists.
            try:
                s3.head_object(Bucket=BUCKET_NAME, Key=f"uploads/{video_id}")
                logger.info(
                    "Manifest/redirect not found for '%s', but source exists. Status: processing",
                    video_id,
                )
                return jsonify({"status": "processing"}), 202
            except s3.exceptions.ClientError:
                logger.warning(
                    "Manifest/redirect and source file not found for '%s'. Status: not_found",
                    video_id,
                )
                return jsonify({"status": "not_found"}), 404
        else:
            logger.error(
                "S3 error getting status for '%s': %s", video_id, e, exc_info=True
            )
            return jsonify({"status": "error", "message": str(e)}), 500
    except Exception as e:
        logger.error("Error getting status for '%s': %s", video_id, e, exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/transcoding_status")
def get_transcoding_status():
    if not BUCKET_NAME:
        logger.error("Cannot get transcoding status: BUCKET_NAME not configured.")
        return (
            jsonify({"status": "error", "message": "Bucket name not configured"}),
            500,
        )

    processing_videos = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=BUCKET_NAME, Prefix="processed/", Delimiter="/"
        )

        for page in pages:
            for prefix in page.get("CommonPrefixes", []):
                manifest_key = f"{prefix['Prefix']}manifest.json"
                try:
                    response = s3.get_object(Bucket=BUCKET_NAME, Key=manifest_key)
                    manifest_data = response["Body"].read().decode("utf-8")
                    manifest = json.loads(manifest_data)

                    if manifest.get("status") == "processing":
                        processing_videos.append(manifest)

                except s3.exceptions.ClientError as e:
                    if e.response["Error"]["Code"] == "NoSuchKey":
                        logger.debug(
                            f"No manifest found for prefix {prefix['Prefix']}, skipping."
                        )
                    else:
                        logger.error(
                            f"Error reading manifest for {prefix['Prefix']}: {e}"
                        )
                except Exception as e:
                    logger.error(
                        f"Error processing manifest for {prefix['Prefix']}: {e}"
                    )
        return jsonify(processing_videos)

    except Exception as e:
        logger.error("Error listing transcoding status: %s", e, exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/videos")
def get_videos():
    if not BUCKET_NAME:
        logger.error("Cannot get videos: BUCKET_NAME not configured.")
        return (
            jsonify({"status": "error", "message": "Bucket name not configured"}),
            500,
        )

    videos = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=BUCKET_NAME, Prefix="processed/", Delimiter="/"
        )

        for page in pages:
            for prefix in page.get("CommonPrefixes", []):
                manifest_key = f"{prefix['Prefix']}manifest.json"
                try:
                    response = s3.get_object(Bucket=BUCKET_NAME, Key=manifest_key)
                    manifest_data = response["Body"].read().decode("utf-8")
                    manifest = json.loads(manifest_data)
                    uploaded_at = response.get("LastModified")

                    if manifest.get("status") == "processing_complete":
                        thumbnail_path = manifest.get("thumbnail") or manifest.get(
                            "sprite", ""
                        ).replace("thumbnails.vtt", "sprite_0.png")

                        videos.append(
                            {
                                "video_id": manifest.get("video_id"),
                                "base_name": manifest.get("base_name"),
                                "sprite": url_for("stream", key=manifest.get("sprite")),
                                "thumbnail_url": url_for("stream", key=thumbnail_path),
                                "hls_url": url_for("stream", key=manifest.get("hls")),
                                "presets": manifest.get("presets", []),
                                "uploaded_at": (
                                    uploaded_at.isoformat() if uploaded_at else None
                                ),
                            }
                        )
                except s3.exceptions.ClientError as e:
                    if e.response["Error"]["Code"] == "NoSuchKey":
                        logger.warning(
                            f"Manifest not found for prefix {prefix['Prefix']}"
                        )
                    else:
                        logger.error(
                            f"Error reading manifest for {prefix['Prefix']}: {e}"
                        )
                except Exception as e:
                    logger.error(
                        f"Error processing manifest for {prefix['Prefix']}: {e}"
                    )

        videos.sort(key=lambda v: v.get("uploaded_at") or "", reverse=True)
        return jsonify(videos)

    except Exception as e:
        logger.error("Error listing videos: %s", e, exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/video/<video_id>", methods=["DELETE"])
def delete_video(video_id):
    if not BUCKET_NAME:
        logger.error("Cannot delete video: BUCKET_NAME not configured.")
        return (
            jsonify({"status": "error", "message": "Bucket name not configured."}),
            500,
        )

    try:
        # Find the process_id from the redirect file
        base_name, _ = os.path.splitext(video_id)
        redirect_key = f"processed/{base_name}.json"
        try:
            redirect_obj = s3.get_object(Bucket=BUCKET_NAME, Key=redirect_key)
            redirect_data = json.loads(redirect_obj["Body"].read().decode("utf-8"))
            process_id = redirect_data.get("process_id")
        except s3.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                # If redirect does not exist, maybe the video_id is the process_id
                process_id = base_name
            else:
                raise e

        if not process_id:
            return (
                jsonify({"status": "error", "message": "Video process ID not found."}),
                404,
            )

        # List all objects in the folder and delete them
        prefix = f"processed/{process_id}/"
        response = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix=prefix)

        if "Contents" in response:
            objects_to_delete = [{"Key": obj["Key"]} for obj in response["Contents"]]
            s3.delete_objects(Bucket=BUCKET_NAME, Delete={"Objects": objects_to_delete})
            logger.info(f"Deleted all files in folder {prefix}")

        # Delete the redirect file
        try:
            s3.delete_object(Bucket=BUCKET_NAME, Key=redirect_key)
            logger.info(f"Deleted redirect file {redirect_key}")
        except s3.exceptions.ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchKey":
                logger.warning(f"Could not delete redirect file {redirect_key}: {e}")

        # Delete the original upload
        try:
            s3.delete_object(Bucket=BUCKET_NAME, Key=f"uploads/{video_id}")
            logger.info(f"Deleted original upload {video_id}")
        except s3.exceptions.ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchKey":
                logger.warning(f"Could not delete original upload {video_id}: {e}")

        return (
            jsonify({"status": "success", "message": "Video deleted successfully."}),
            200,
        )

    except Exception as e:
        logger.error(f"Error deleting video {video_id}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/stream/<path:key>")
def stream(key):
    safe_key = os.path.normpath(key)

    if safe_key.startswith(".."):
        logger.warning(
            "Attempted access to invalid key: %s (normalized: %s)", key, safe_key
        )
        return "Invalid key", 400

    try:
        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET_NAME, "Key": "processed/" + safe_key},
            ExpiresIn=3600,  # URL expires in 1 hour.
        )
        # Redirect the client to the presigned URL.
        return redirect(presigned_url)
    except Exception as e:
        logger.error(
            "Error generating presigned URL for key '%s': %s",
            safe_key,
            e,
            exc_info=True,
        )
        return (
            jsonify({"status": "error", "message": "Could not generate stream URL."}),
            500,
        )


def probe_resolution(path):
    cmd = [
        FFPROBE,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        path,
    ]
    info = json.loads(subprocess.check_output(cmd))
    stream = info["streams"][0]
    return int(stream["width"]), int(stream["height"])


def _update_manifest(bucket, key, update_data):
    try:
        # Try to get the existing manifest
        response = s3.get_object(Bucket=bucket, Key=key)
        manifest_data = response["Body"].read().decode("utf-8")
        manifest = json.loads(manifest_data)
    except s3.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            # If not found, start with an empty manifest
            manifest = {}
        else:
            # For other errors, re-raise the exception
            raise

    # Update the manifest with new data
    manifest.update(update_data)

    # Write the updated manifest back to S3
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(manifest),
        ContentType="application/json",
    )


def select_presets(src_w, src_h):
    return [p for p in ALL_PRESETS if p["width"] <= src_w and p["height"] <= src_h]


def get_video_duration(video_path):
    result = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    duration_str = result.stdout.strip()
    return float(duration_str) if duration_str else 0.0


def format_timestamp(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    # Format as HH:MM:SS.mmm
    # Use milliseconds three digits
    ms = int((s - int(s)) * 1000)
    return f"{h:02d}:{m:02d}:{int(s):02d}.{ms:03d}"


def generate_vtt(
    video_path, sprite_prefix, output_vtt_path, cols, rows, scale_width, interval=1
):
    duration = get_video_duration(video_path)
    orig_w, orig_h = probe_resolution(video_path)
    thumb_w = scale_width
    thumb_h = int(orig_h * scale_width / orig_w)

    total_thumbs = math.floor(duration / interval)
    capacity = cols * rows

    lines = ["WEBVTT", ""]  # blank line after header
    for i in range(total_thumbs):
        start_sec = i * interval
        end_sec = min((i + 1) * interval, duration)
        sheet_idx = i // capacity
        local_idx = i % capacity
        col = local_idx % cols
        row = local_idx // cols
        x = col * thumb_w
        y = row * thumb_h
        sprite_file = (
            sprite_prefix.format(i=sheet_idx)
            if "{i" in sprite_prefix
            else sprite_prefix
        )
        ts_start = format_timestamp(start_sec)
        ts_end = format_timestamp(end_sec)
        # Cue:
        lines.append(str(i + 1))
        lines.append(f"{ts_start} --> {ts_end}")
        lines.append(
            f"{LAMBDA_FUNCTION_URL}/stream/{sprite_file}#xywh={x},{y},{thumb_w},{thumb_h}"
        )
        lines.append("")  # blank line between cues

    # Write to .vtt
    with open(output_vtt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def generate_sprite_sheet(
    input_video, output_path, cols, rows, scale_width, start_time=0, sheet_duration=None
):
    tile_filter = f"fps=1,scale={scale_width}:-1,tile={cols}x{rows}"
    cmd = [FFMPEG, "-y", "-loglevel", "error"]
    if start_time and start_time > 0:
        cmd += ["-ss", str(start_time)]

    cmd += ["-i", input_video]
    if sheet_duration:
        cmd += ["-t", str(sheet_duration)]

    cmd += ["-vf", tile_filter, "-frames:v", "1", "-update", "1", output_path]
    subprocess.run(cmd, check=True)


def process_video(event):
    record = event["Records"][0]["s3"]
    bucket = record["bucket"]["name"]
    key = urllib.parse.unquote_plus(record["object"]["key"])
    logger.info("Starting video processing for s3://%s/%s", bucket, key)

    # Download source to temp
    tmp_in = tempfile.mktemp(suffix=os.path.basename(key))
    logger.info("Downloading to %s", tmp_in)
    s3.download_file(bucket, key, tmp_in, Config=S3_TRANSFER_CONFIG)

    # Compute MD5 hash of the input file
    md5_hash = hashlib.md5()
    with open(tmp_in, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5_hash.update(chunk)
    file_md5 = md5_hash.hexdigest()
    logger.info("Calculated MD5 hash: %s", file_md5)

    # Probe and select
    src_w, src_h = probe_resolution(tmp_in)
    logger.info("Source resolution: %dx%d", src_w, src_h)
    presets = select_presets(src_w, src_h)

    presets.sort(key=lambda p: p["height"])
    logger.info("Selected presets: %s", [p["name"] for p in presets])

    base_name, ext = os.path.splitext(os.path.basename(key))
    output_prefix = f"processed/{file_md5}/"
    manifest_key = output_prefix + "manifest.json"

    # Initial manifest update
    initial_manifest = {
        "status": "processing",
        "process_id": file_md5,
        "video_id": os.path.basename(key),
        "base_name": base_name,
        "presets": {p["name"]: "pending" for p in presets},
    }
    logger.info("Creating initial manifest at %s", manifest_key)
    _update_manifest(bucket, manifest_key, initial_manifest)

    # Create a redirect file from base_name to process_id
    redirect_key = f"processed/{base_name}.json"
    redirect_content = {"process_id": file_md5}
    s3.put_object(
        Bucket=bucket,
        Key=redirect_key,
        Body=json.dumps(redirect_content),
        ContentType="application/json",
    )
    logger.info("Created redirect file at %s", redirect_key)

    try:
        # HLS generation
        for p in presets:
            logger.info("Processing preset: %s", p["name"])
            # Update preset status to 'processing'
            initial_manifest["presets"][p["name"]] = "processing"
            _update_manifest(bucket, manifest_key, initial_manifest)

            out_hls = tempfile.mkdtemp()
            playlist = os.path.join(out_hls, f"{p['name']}.m3u8")
            logger.debug("Generating HLS for %s at %s", p["name"], playlist)
            cmd_hls = [
                FFMPEG,
                "-y",
                "-loglevel",
                "error",
                "-i",
                tmp_in,
                "-vf",
                f"scale=w={p['width']}:h={p['height']}",
                "-c:v",
                "h264",
                "-profile:v",
                "main",
                "-crf",
                "20",
                "-b:v",
                p["bitrate"],
                "-maxrate",
                p["bitrate"],
                "-bufsize",
                "1200k",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-hls_time",
                "6",
                "-hls_list_size",
                "0",
                "-hls_base_url",
                f"{LAMBDA_FUNCTION_URL}/stream/{file_md5}/hls/{p['name']}/",
                "-hls_segment_filename",
                os.path.join(out_hls, f"seg_%03d_{p['name']}.ts"),
                playlist,
            ]
            subprocess.check_call(cmd_hls)
            # upload
            logger.info("Uploading HLS files for %s", p["name"])
            for root, _, files in os.walk(out_hls):
                for f in files:
                    local = os.path.join(root, f)
                    s3.upload_file(
                        local,
                        bucket,
                        output_prefix + f"hls/{p['name']}/{f}",
                        ExtraArgs={"ContentType": _guess_mime(f)},
                    )
                    try:
                        os.remove(local)
                    except:
                        pass

            # Update preset status to 'complete'
            initial_manifest["presets"][p["name"]] = "complete"
            _update_manifest(bucket, manifest_key, initial_manifest)
            logger.info("Preset %s complete", p["name"])
            # _update_manifest(bucket, manifest_key, {"presets": {p["name"]: "complete"}})

        # Master HLS playlist
        logger.info("Generating master HLS playlist")
        master_hls = "#EXTM3U\n"
        for p in presets:
            master_hls += f"#EXT-X-STREAM-INF:BANDWIDTH={int(p['bitrate'].rstrip('k'))*1000},RESOLUTION={p['width']}x{p['height']}\n"
            master_hls += f"{LAMBDA_FUNCTION_URL}/stream/{file_md5}/hls/{p['name']}/{p['name']}.m3u8\n"
        s3.put_object(
            Bucket=bucket,
            Key=output_prefix + "hls/master.m3u8",
            Body=master_hls,
            ContentType="application/vnd.apple.mpegurl",
        )

        # DASH generation using ffmpeg dash muxer
        if GENERATE_DASH:
            logger.info("Generating DASH manifest and segments")
            dash_dir = tempfile.mkdtemp()
            dash_mpd = os.path.join(dash_dir, "stream.mpd")
            map_cmd = []
            for p in presets:
                map_cmd += [
                    "-map",
                    "0:v:0",
                    "-b:v:" + str(presets.index(p)),
                    p["bitrate"],
                    "-filter:v:" + str(presets.index(p)),
                    f"scale={p['width']}:{p['height']}",
                ]
            cmd_dash = [
                FFMPEG,
                "-y",
                "-loglevel",
                "error",
                "-i",
                tmp_in,
                *map_cmd,
                "-c:a",
                "aac",
                "-use_template",
                "1",
                "-use_timeline",
                "1",
                "-adaptation_sets",
                "id=0,streams=v id=1,streams=a",
                "-f",
                "dash",
                dash_mpd,
            ]
            subprocess.check_call(cmd_dash)
            # upload DASH files
            logger.info("Uploading DASH files")
            for root, _, files in os.walk(dash_dir):
                for f in files:
                    path = os.path.join(root, f)
                    s3.upload_file(
                        path,
                        bucket,
                        output_prefix + f"dash/{f}",
                        ExtraArgs={"ContentType": _guess_mime(f)},
                    )
                    try:
                        os.remove(path)
                    except:
                        pass

        # Create sprite sheet
        logger.info("Generating sprite sheet")
        sprite_dir = tempfile.mkdtemp()
        sprite = os.path.join(sprite_dir, "sprite_{i}.png")
        vtt = tempfile.mktemp(suffix=".vtt")

        duration = get_video_duration(tmp_in)
        total_thumbs = math.floor(duration / SPRITE_INTERVAL)
        capacity = SPRITE_COLUMNS * SPRITE_ROWS
        num_sheets = math.ceil(total_thumbs / capacity) if capacity > 0 else 0

        for sheet_idx in range(num_sheets):
            start_sec = sheet_idx * capacity * SPRITE_INTERVAL
            sheet_duration = min(capacity * SPRITE_INTERVAL, duration - start_sec)
            output_sprite = sprite.format(i=sheet_idx)
            generate_sprite_sheet(
                tmp_in,
                output_sprite,
                SPRITE_COLUMNS,
                SPRITE_ROWS,
                SPRITE_SCALE_W,
                start_time=start_sec,
                sheet_duration=sheet_duration,
            )

            # Upload sprite sheet
            logger.debug("Uploading sprite sheet %d", sheet_idx)
            s3.upload_file(
                output_sprite,
                bucket,
                output_prefix + "thumbnail/" + f"sprite_{sheet_idx}.png",
                ExtraArgs={"ContentType": "image/png"},
            )
            try:
                os.remove(output_sprite)
            except OSError:
                pass

        logger.info("Generating VTT file")
        generate_vtt(
            tmp_in,
            f"{output_prefix.replace('processed/', '', 1)}thumbnail/sprite_{{i}}.png",
            vtt,
            SPRITE_COLUMNS,
            SPRITE_ROWS,
            SPRITE_SCALE_W,
            SPRITE_INTERVAL,
        )

        # Generate thumbnail
        logger.info("Generating thumbnail")
        thumb = tempfile.mktemp(suffix=".png")
        seek = int(duration / 3) if duration > 0 else 0
        generate_sprite_sheet(
            tmp_in,
            thumb,
            1,
            1,
            THUMBNAIL_WIDTH,  # Scale width for thumbnail
            start_time=seek,
            sheet_duration=0,  # Single frame
        )

        # Upload sprite sheet
        logger.debug("Uploading thumbnail")
        s3.upload_file(
            thumb,
            bucket,
            output_prefix + "thumbnail/" + f"thumbnail.png",
            ExtraArgs={"ContentType": "image/png"},
        )
        try:
            os.remove(thumb)
        except OSError:
            pass

        # Upload VTT file
        logger.info("Uploading VTT file")
        s3.upload_file(
            vtt,
            bucket,
            output_prefix + "thumbnail/" + "thumbnails.vtt",
            ExtraArgs={"ContentType": "text/vtt"},
        )

        try:
            os.remove(vtt)
        except OSError:
            pass

        if _guess_mime(tmp_in) not in TRANSCRIBE_SUPPORTED_MEDIA_FORMATS:
            logger.info("Media format not supported by Transcribe, converting to WAV.")
            ext = "wav"
            audio_file = tempfile.mktemp(suffix=f".{ext}")
            cmd_convert = [
                FFMPEG,
                "-y",
                "-loglevel",
                "error",
                "-i",
                tmp_in,
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                ext,
                audio_file,
            ]
            subprocess.check_call(cmd_convert)

            # Upload converted audio file
            logger.info("Uploading converted audio file for transcription.")
            s3.upload_file(
                audio_file,
                bucket,
                output_prefix + f"audio.{ext}",
                ExtraArgs={"ContentType": f"audio/{ext}"},
            )

            transcribe_key = output_prefix + f"audio.{ext}"
            try:
                os.remove(audio_file)
            except OSError:
                pass
        else:
            logger.info("Media format is supported by Transcribe.")
            transcribe_key = key

        # Start transcription
        if GENERATE_SUBTITLES:
            job = f"{base_name}-{int(time.time())}"
            job_name = re.sub(r"[^0-9a-zA-Z._-]", "_", job)
            logger.info("Starting transcription job: %s", job_name)
            transcribe.start_transcription_job(
                TranscriptionJobName=job_name,
                Media={"MediaFileUri": f"s3://{bucket}/{transcribe_key}"},
                MediaFormat=ext.lstrip("."),
                Subtitles={
                    "Formats": ["srt"],
                    "OutputStartIndex": 1,
                },
                IdentifyLanguage=True,
                LanguageOptions=LANGUAGE_OPTIONS,
                OutputBucketName=bucket,
                OutputKey=f"{output_prefix}{job_name}.json",
            )

        output_prefix = output_prefix.replace("processed/", "", 1)
        final_manifest_update = {
            "status": "processing_complete",
            "video_id": os.path.basename(key),
            "base_name": base_name,
            "process_id": file_md5,
            "presets": [p["name"] for p in presets],
            "sprite": output_prefix + "thumbnail/thumbnails.vtt",
            "hls": output_prefix + "hls/master.m3u8",
            "dash": output_prefix + "dash/stream.mpd",
            "thumbnail": output_prefix + "thumbnail/thumbnail.png",
        }
        if GENERATE_SUBTITLES:
            final_manifest_update["transcription_job"] = job_name

        if not GENERATE_DASH:
            final_manifest_update.pop("dash", None)

        logger.info("Processing complete, updating final manifest.")
        _update_manifest(bucket, manifest_key, final_manifest_update)

        return final_manifest_update
    except Exception as e:
        # In case of any error, update the manifest with failure status
        error_manifest_update = {
            "status": "processing_failed",
            "process_id": file_md5,
            "video_id": os.path.basename(key),
            "base_name": base_name,
            "error": str(e),
        }

        _update_manifest(bucket, manifest_key, error_manifest_update)
        logger.error("Error processing video %s: %s", key, e, exc_info=True)
        return error_manifest_update


def stream_handler(event):
    params = event.get("queryStringParameters") or {}
    bucket, key = params.get("bucket"), params.get("key")
    rng = event.get("headers", {}).get("Range")
    if not bucket or not key:
        logger.warning("Stream handler missing bucket or key.")
        return {"statusCode": 400, "body": "Missing bucket/key"}

    g = {"Bucket": bucket, "Key": key}
    if rng:
        g["Range"] = rng

    try:
        obj = s3.get_object(**g)
        data = obj["Body"].read()
        headers = {
            "Content-Type": obj.get("ContentType") or mimetypes.guess_type(key)[0],
            "Accept-Ranges": "bytes",
            "Content-Length": str(obj["ContentLength"]),
        }
        code = 206 if rng else 200
        if rng:
            headers["Content-Range"] = obj["ResponseMetadata"]["HTTPHeaders"].get(
                "content-range"
            )
        logger.debug("Streamed %d bytes for %s", len(data), key)
        return {
            "statusCode": code,
            "headers": headers,
            "body": base64.b64encode(data).decode(),
            "isBase64Encoded": True,
        }
    except Exception as e:
        logger.error("Error streaming %s: %s", key, e, exc_info=True)
        return {
            "statusCode": 500,
            "body": f"Error streaming file: {e}",
            "headers": {"Content-Type": "text/plain"},
        }


def _guess_mime(f):
    return mimetypes.guess_type(f)[0] or "application/octet-stream"
