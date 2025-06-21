import base64
import hashlib
import json
import math
import mimetypes
import os
import subprocess
import tempfile
import time
import traceback

import boto3
from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

s3 = boto3.client("s3")
transcribe = boto3.client("transcribe")

app = Flask(__name__)

# Bucket Name
BUCKET_NAME = os.environ.get("BUCKET_NAME")

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
SPRITE_SCALE_W = 320
SPRITE_COLUMNS = 5

# Transcription language
LANGUAGE_CODE = "en-US"


def lambda_handler(event, context):
    # print("Received event:", json.dumps(event, indent=4))
    if "Records" in event and event["Records"][0].get("s3"):
        return process_video(event)
    elif event.get("requestContext", {}).get("http"):
        # This is a request from API Gateway
        import serverless_wsgi

        return serverless_wsgi.handle_request(app, event, context)
    else:
        return stream_handler(event)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "video" not in request.files:
        return "No video file found", 400

    video = request.files["video"]
    if video.filename == "":
        return "No selected file", 400

    if video and BUCKET_NAME:
        filename = video.filename
        s3.upload_fileobj(video, BUCKET_NAME, f"uploads/{filename}")
        return redirect(url_for("status", video_id=filename))

    return "Something went wrong", 500


@app.route("/create_multipart_upload", methods=["POST"])
def create_multipart_upload():
    data = request.get_json()
    file_name = data.get("fileName")
    file_size = data.get("fileSize")

    if not file_name or not file_size:
        return jsonify({"error": "fileName and fileSize are required"}), 400

    # 5MB chunks
    CHUNK_SIZE = 5 * 1024 * 1024
    num_parts = math.ceil(file_size / CHUNK_SIZE)
    key = f"uploads/{file_name}"

    try:
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

        return jsonify({"uploadId": upload_id, "urls": presigned_urls})
    except Exception as e:
        print(f"Error creating multipart upload: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/complete_multipart_upload", methods=["POST"])
def complete_multipart_upload():
    data = request.get_json()
    file_name = data.get("fileName")
    upload_id = data.get("uploadId")
    parts = data.get("parts")

    if not file_name or not upload_id or not parts:
        return jsonify({"error": "fileName, uploadId, and parts are required"}), 400

    key = f"uploads/{file_name}"

    try:
        s3.complete_multipart_upload(
            Bucket=BUCKET_NAME,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
        return jsonify({"status": "success", "video_id": file_name})
    except Exception as e:
        print(f"Error completing multipart upload: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/status/<video_id>")
def status(video_id):
    if not BUCKET_NAME:
        return (
            jsonify({"status": "error", "message": "Bucket name not configured"}),
            500,
        )

    base_name, _ = os.path.splitext(video_id)
    manifest_key = f"processed/{base_name}/manifest.json"

    try:
        response = s3.get_object(Bucket=BUCKET_NAME, Key=manifest_key)
        manifest_data = response["Body"].read().decode("utf-8")
        manifest = json.loads(manifest_data)

        # Check transcription status
        transcription_job_name = manifest.get("transcription_job")
        if transcription_job_name:
            try:
                job_status_response = transcribe.get_transcription_job(
                    TranscriptionJobName=transcription_job_name
                )
                job_status = job_status_response["TranscriptionJob"]
                transcription_status = job_status["TranscriptionJobStatus"]
                manifest["transcription_status"] = transcription_status

                if transcription_status == "COMPLETED":
                    transcript_s3_uri = job_status["Transcript"]["TranscriptFileUri"]
                    transcript_key = "/".join(transcript_s3_uri.split("/")[3:])
                    manifest["transcript_key"] = transcript_key
                elif transcription_status == "FAILED":
                    manifest["transcription_failure_reason"] = job_status.get(
                        "FailureReason"
                    )
            except transcribe.exceptions.NotFoundException:
                manifest["transcription_status"] = "NOT_FOUND"

        return jsonify(manifest)
    except s3.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            # Check if the original file exists, to distinguish between processing and not found
            try:
                s3.head_object(Bucket=BUCKET_NAME, Key=f"uploads/{video_id}")
                return jsonify({"status": "processing"}), 202
            except s3.exceptions.ClientError:
                return jsonify({"status": "not_found"}), 404
        else:
            return jsonify({"status": "error", "message": str(e)}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/stream/<path:key>")
def stream(key):
    range_header = request.headers.get("Range", None)
    event = {
        "queryStringParameters": {"bucket": BUCKET_NAME, "key": key},
        "headers": {},
    }
    if range_header:
        event["headers"]["Range"] = range_header

    response_data = stream_handler(event)

    response_body = base64.b64decode(response_data["body"])

    return Response(
        response_body,
        status=response_data["statusCode"],
        headers=response_data["headers"],
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


def deep_update(original, update):
    for key, value in update.items():
        if (
            isinstance(value, dict)
            and key in original
            and isinstance(original[key], dict)
        ):
            deep_update(original[key], value)
        else:
            original[key] = value


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
    # deep_update(manifest, update_data)
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


def process_video(event):
    record = event["Records"][0]["s3"]
    bucket = record["bucket"]["name"]
    key = record["object"]["key"]

    # print("Processing video:", key)

    # Download source to temp
    tmp_in = tempfile.mktemp(suffix=os.path.basename(key))
    s3.download_file(bucket, key, tmp_in)

    # print(f"Downloaded {key} to {tmp_in}")

    # Compute MD5 hash of the input file
    md5_hash = hashlib.md5()
    with open(tmp_in, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5_hash.update(chunk)
    file_md5 = md5_hash.hexdigest()

    # Probe and select
    src_w, src_h = probe_resolution(tmp_in)
    presets = select_presets(src_w, src_h)

    presets.sort(key=lambda p: p["height"])

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
    _update_manifest(bucket, manifest_key, initial_manifest)

    try:
        # print(f"Selected presets: {', '.join(p['name'] for p in presets)}")
        # print(f"Source resolution: {src_w}x{src_h}")
        # print(f"Output prefix: {output_prefix}")

        # HLS generation
        for p in presets:
            # Update preset status to 'processing'
            initial_manifest["presets"][p["name"]] = "processing"
            _update_manifest(bucket, manifest_key, initial_manifest)
            # _update_manifest(bucket, manifest_key, {"presets": {p["name"]: "processing"}})

            out_hls = tempfile.mkdtemp()
            playlist = os.path.join(out_hls, f"{p['name']}.m3u8")
            cmd_hls = [
                FFMPEG,
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
                "-hls_segment_filename",
                os.path.join(out_hls, f"seg_%03d_{p['name']}.ts"),
                playlist,
            ]
            subprocess.check_call(cmd_hls)
            # upload
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
            # _update_manifest(bucket, manifest_key, {"presets": {p["name"]: "complete"}})

        # Master HLS playlist
        master_hls = "#EXTM3U\n"
        for p in presets:
            master_hls += f"#EXT-X-STREAM-INF:BANDWIDTH={int(p['bitrate'].rstrip('k'))*1000},RESOLUTION={p['width']}x{p['height']}\n"
            master_hls += f"hls/{p['name']}/{p['name']}.m3u8\n"
        s3.put_object(
            Bucket=bucket,
            Key=output_prefix + "hls/master.m3u8",
            Body=master_hls,
            ContentType="application/vnd.apple.mpegurl",
        )

        # DASH generation using ffmpeg dash muxer
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

        # Sprite sheet (dynamic grid)
        try:
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
                    tmp_in,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
            duration_str = result.stdout.strip()
            duration = float(duration_str) if duration_str else 0.0
        except Exception as e:
            print(
                f"Warning: ffprobe failed to get duration ({e}), defaulting duration=0"
            )
            duration = 0.0

        # Compute total frames
        total_frames = math.ceil(duration * SPRITE_FPS)
        if total_frames < 1:
            total_frames = 1

        # Compute grid size: columns × rows >= total_frames, roughly square
        columns = math.ceil(math.sqrt(total_frames))
        rows = math.ceil(total_frames / columns)

        sprite = tempfile.mktemp(suffix=".png")
        cmd_sprite = [
            FFMPEG,
            "-i",
            tmp_in,
            "-f",
            "image2",
            "-update",
            "1",
            "-vf",
            f"fps={SPRITE_FPS},scale={SPRITE_SCALE_W}:-1,tile={columns}x{rows}",
            "-frames:v",
            "1",
            sprite,
        ]

        print(
            f"Running sprite-generation ffmpeg: sampling {total_frames} frames into grid {columns}x{rows}, command: {cmd_sprite}"
        )
        subprocess.check_call(cmd_sprite)

        # Upload sprite sheet
        s3.upload_file(
            sprite,
            bucket,
            output_prefix + "sprite.png",
            ExtraArgs={"ContentType": "image/png"},
        )

        try:
            os.remove(sprite)
        except OSError:
            pass

        if _guess_mime(tmp_in) not in TRANSCRIBE_SUPPORTED_MEDIA_FORMATS:
            ext = "wav"
            audio_file = tempfile.mktemp(suffix=f".{ext}")
            cmd_convert = [
                FFMPEG,
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
            transcribe_key = key

        # Start transcription
        job = f"{base_name}-{int(time.time())}"
        transcribe.start_transcription_job(
            TranscriptionJobName=job,
            Media={"MediaFileUri": f"s3://{bucket}/{transcribe_key}"},
            MediaFormat=ext.lstrip("."),
            LanguageCode=LANGUAGE_CODE,
            OutputBucketName=bucket,
            OutputKey=f"{output_prefix}{job}.json",
        )

        final_manifest_update = {
            "status": "processing_complete",
            "video_id": os.path.basename(key),
            "base_name": base_name,
            "process_id": file_md5,
            "presets": [p["name"] for p in presets],
            "sprite": output_prefix + "sprite.png",
            "hls": output_prefix + "hls/master.m3u8",
            "dash": output_prefix + "dash/stream.mpd",
            "transcription_job": job,
        }

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
        print(f"Error processing video {key}: {e}")
        print(traceback.format_exc())
        return error_manifest_update


def stream_handler(event):
    params = event.get("queryStringParameters") or {}
    bucket, key = params.get("bucket"), params.get("key")
    rng = event.get("headers", {}).get("Range")
    if not bucket or not key:
        return {"statusCode": 400, "body": "Missing bucket/key"}

    g = {"Bucket": bucket, "Key": key}
    if rng:
        g["Range"] = rng

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
    return {
        "statusCode": code,
        "headers": headers,
        "body": base64.b64encode(data).decode(),
        "isBase64Encoded": True,
    }


def _guess_mime(f):
    return mimetypes.guess_type(f)[0] or "application/octet-stream"
