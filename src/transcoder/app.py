import base64
import json
import mimetypes
import os
import subprocess
import tempfile
import time

import boto3

s3 = boto3.client("s3")
transcribe = boto3.client("transcribe")

# Paths for binaries in Lambda Layer
FFMPEG = "/opt/bin/ffmpeg"
FFPROBE = "/opt/bin/ffprobe"

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
    if "Records" in event and event["Records"][0].get("s3"):
        return process_video(event)
    else:
        return stream_handler(event)


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


def select_presets(src_w, src_h):
    return [p for p in ALL_PRESETS if p["width"] <= src_w and p["height"] <= src_h]


def process_video(event):
    record = event["Records"][0]["s3"]
    bucket = record["bucket"]["name"]
    key = record["object"]["key"]

    # Download source to temp
    tmp_in = tempfile.mktemp(suffix=os.path.basename(key))
    s3.download_file(bucket, key, tmp_in)

    # Probe and select
    src_w, src_h = probe_resolution(tmp_in)
    presets = select_presets(src_w, src_h)

    base_name, ext = os.path.splitext(os.path.basename(key))
    output_prefix = f"processed/{base_name}/"

    # HLS generation
    for p in presets:
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

    # Sprite sheet
    sprite = tempfile.mktemp(suffix=".png")
    subprocess.check_call(
        [
            FFMPEG,
            "-i",
            tmp_in,
            "-vf",
            f"fps={SPRITE_FPS},scale={SPRITE_SCALE_W}:-1,tile={SPRITE_COLUMNS}x",
            sprite,
        ]
    )
    s3.upload_file(
        sprite,
        bucket,
        output_prefix + "sprite.png",
        ExtraArgs={"ContentType": "image/png"},
    )

    # Start transcription
    job = f"{base_name}-{int(time.time())}"
    transcribe.start_transcription_job(
        TranscriptionJobName=job,
        Media={"MediaFileUri": f"s3://{bucket}/{key}"},
        MediaFormat=ext.lstrip("."),
        LanguageCode=LANGUAGE_CODE,
        OutputBucketName=bucket,
        OutputKey=f"{output_prefix}{job}.json",
    )

    return {
        "status": "done",
        "presets": [p["name"] for p in presets],
        "sprite": output_prefix + "sprite.png",
        "hls": "hls/master.m3u8",
        "dash": "dash/stream.mpd",
        "transcription_job": job,
    }


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
