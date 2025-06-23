# Lambda Video Transcoder

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

🔥 A serverless AWS Lambda solution for on-the-fly video transcoding, transcription, and adaptive streaming (HLS & DASH), with a simple Flask-based frontend for uploads, status tracking, and video management.

This project provides a robust and scalable way to process video files. It includes:
1.  **S3 Event Trigger:** A Lambda function is triggered when a video is uploaded to an S3 bucket.
2.  **Video Processing Pipeline:**
    *   **Content-Addressable Storage:** Uses the MD5 hash of the video file as a unique ID to prevent duplicate processing.
    *   **Transcode to Multiple Resolutions:** Creates different quality levels (e.g., 1080p, 720p, 480p) for adaptive bitrate streaming.
    *   **Generate HLS & DASH Playlists:** Creates manifest files for Apple HLS and MPEG-DASH.
    *   **Create Dynamic Sprite Sheet:** Generates a thumbnail sprite sheet for video scrubbing previews.
    *   **Transcribe Audio (Optional):** Uses Amazon Transcribe to generate subtitles.
3.  **Flask Backend with REST API:**
    *   Handles large file uploads using S3 multipart uploads.
    *   Provides API endpoints to list, check status, and delete videos.
    *   Serves video content using S3 presigned URLs.
4.  **Direct Access via Lambda Function URL:** The Flask app is served via a Lambda Function URL, providing direct public HTTP access without needing an API Gateway.

## Features
-   **Serverless Architecture:** Leverages AWS Lambda, S3, and Amazon Transcribe.
-   **Content-Addressable:** Video processing is based on file content (MD5 hash), making it idempotent.
-   **Large File Support:** Handles large video uploads efficiently using S3 multipart uploads.
-   **Adaptive Bitrate Streaming:** Outputs HLS and (optionally) DASH formats.
-   **Automated Transcription:** Integrates with Amazon Transcribe (can be disabled).
-   **Dynamic Thumbnail Sprite Generation:** Creates a sprite sheet for rich player seeking previews.
-   **REST API:** Provides endpoints for managing videos, suitable for a modern frontend.
-   **Video Deletion:** API endpoint to delete a video and all its associated assets from S3.
-   **Docker-based Deployment:** Simplified deployment using a container image.

## Project Structure
```
.
├── Dockerfile
├── LICENSE
├── README.md
├── requirements.txt
├── events/
│   └── basic.json
├── src/
│   └── transcoder/
│       ├── __init__.py
│       ├── app.py              # Core Lambda function logic with Flask app
│       ├── requirements.txt    # Dependencies for the Lambda function
│       └── templates/
│           └── index.html      # HTML for the upload frontend
└── tests/
    └── test_handler.py
```

## Prerequisites
-   AWS Account
-   AWS CLI installed and configured
-   Docker installed
-   An S3 bucket to store uploads and transcoded files.

## Configuration

The Lambda function is configured using environment variables. Set these in the Lambda function's configuration page in the AWS Console.

| Variable              | Description                                                                                                 | Default   |
| --------------------- | ----------------------------------------------------------------------------------------------------------- | --------- |
| `BUCKET_NAME`         | **Required.** The name of the S3 bucket for uploads and transcoded files.                                     | `None`    |
| `LAMBDA_FUNCTION_URL` | The public URL of the Lambda function. Required for generating correct links in HLS/DASH manifests.         | `""`      |
| `GENERATE_DASH`       | Set to `"true"` to generate MPEG-DASH manifests alongside HLS.                                              | `"true"`  |
| `GENERATE_SUBTITLES`  | Set to `"true"` to enable video transcription with Amazon Transcribe.                                       | `"true"`  |
| `THUMBNAIL_WIDTH`     | The width of the generated thumbnails in the sprite sheet.                                                  | `1280`    |
| `LOG_LEVEL`           | The logging level for the application.                                                                      | `"INFO"`  |
| `SPRITE_FPS`          | The frame rate (frames per second) to use for generating the thumbnail sprite.                              | `1`       |
| `SPRITE_ROWS`         | The number of rows in the thumbnail sprite sheet.                                                           | `10`      |
| `SPRITE_COLUMNS`      | The number of columns in the thumbnail sprite sheet.                                                        | `10`      |
| `SPRITE_INTERVAL`     | The interval in seconds between frames captured for the thumbnail sprite.                                   | `1`       |
| `SPRITE_SCALE_W`      | The width to scale each thumbnail to in the sprite sheet.                                                   | `180`     |


## Deployment Guide 🚀

This project is designed for deployment as a Docker container image to AWS Lambda.

**1. Build and Push Docker Image to Amazon ECR:**
   - **Create ECR Repository:**
     ```bash
     aws ecr create-repository --repository-name lambda-video-transcoder --image-scanning-configuration scanOnPush=true --region your-aws-region
     ```
   - **Authenticate Docker to ECR:**
     ```bash
     aws ecr get-login-password --region your-aws-region | docker login --username AWS --password-stdin YOUR_AWS_ACCOUNT_ID.dkr.ecr.your-aws-region.amazonaws.com
     ```
   - **Build, Tag, and Push the Image:**
     ```bash
     docker build -t lambda-video-transcoder .
     docker tag lambda-video-transcoder:latest YOUR_AWS_ACCOUNT_ID.dkr.ecr.your-aws-region.amazonaws.com/lambda-video-transcoder:latest
     docker push YOUR_AWS_ACCOUNT_ID.dkr.ecr.your-aws-region.amazonaws.com/lambda-video-transcoder:latest
     ```

**2. Create and Configure the Lambda Function:**
   - In the AWS Lambda Console, click **"Create function"**.
   - Select **"Container image"**.
   - **Function name:** `lambda-video-transcoder`.
   - **Container image URI:** Browse and select the `lambda-video-transcoder:latest` image from ECR.
   - **Architecture:** `x86_64`.
   - **Memory and Timeout:** Increase the memory (e.g., to **2048 MB**) and the timeout (e.g., to **15 minutes**) to handle large video files.
   - **Permissions:** Create a new execution role. You will attach the necessary policies to this role (see IAM Permissions section below).
   - Click **"Create function"**.

**3. Set Environment Variables:**
   - In the Lambda function's configuration page, go to the **"Configuration"** tab and then **"Environment variables"**.
   - Add the environment variables listed in the **Configuration** section above. `BUCKET_NAME` is required.

**4. Add S3 Triggers:**
   - In the function's **"Function overview"** panel, click **"+ Add trigger"**.
   - **Trigger 1 (For Video Uploads):**
       - Select **"S3"** as the source.
       - Choose your bucket (`BUCKET_NAME`).
       - **Event type:** `All object create events`.
       - **Prefix:** `uploads/`
       - Acknowledge the recursive invocation warning and click **"Add"**.
   - Click **"+ Add trigger"** again.
   - **Trigger 2 (For Processed File Events):**
       - Select **"S3"** as the source.
       - Choose your bucket (`BUCKET_NAME`).
       - **Event type:** `All object create events`.
       - **Prefix:** `processed/`
       - **Suffix:** `.json`
       - Acknowledge the recursive invocation warning and click **"Add"**. This trigger handles events for JSON files in the `processed/` directory, such as transcription job results from Amazon Transcribe. The Lambda function code is designed to handle these events without causing infinite loops.

**5. Create Function URL:**
   - In the function's configuration page, go to the **"Configuration"** tab and then **"Function URL"**.
   - Click **"Create function URL"**.
   - **Auth type:** `NONE`.
   - **CORS:** Configure CORS to allow access from your frontend's domain. For testing, you can enable it for all origins.
   - Click **"Save"**.
   - Copy the generated Function URL and set it as the `LAMBDA_FUNCTION_URL` environment variable.

## IAM Permissions

Your Lambda execution role needs the following permissions. Attach these policies to the role.

1.  **S3 Access:** Full access to the specific S3 bucket used by the function.
    ```json
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "s3:*",
                "Resource": [
                    "arn:aws:s3:::YOUR_BUCKET_NAME",
                    "arn:aws:s3:::YOUR_BUCKET_NAME/*"
                ]
            }
        ]
    }
    ```
2.  **AWS Transcribe Access:** (If `GENERATE_SUBTITLES` is enabled)
    -  `transcribe:StartTranscriptionJob`
    -  `transcribe:GetTranscriptionJob`
    -  The managed policy `AmazonTranscribeFullAccess` can be used for simplicity.

3.  **CloudWatch Logs:** The default `AWSLambdaBasicExecutionRole` policy is usually sufficient for logging.
    - `logs:CreateLogGroup`
    - `logs:CreateLogStream`
    - `logs:PutLogEvents`

## S3 Bucket CORS Configuration

To allow the frontend to perform multipart uploads directly to S3 and to support video streaming, you need to configure Cross-Origin Resource Sharing (CORS) on your S3 bucket.

Go to your S3 bucket in the AWS Console, select the **Permissions** tab, and in the **Cross-origin resource sharing (CORS)** section, paste the following JSON configuration:

```json
[
    {
        "AllowedHeaders": [
            "*"
        ],
        "AllowedMethods": [
            "GET",
            "PUT",
            "POST",
            "DELETE",
            "HEAD"
        ],
        "AllowedOrigins": [
            "*"
        ],
        "ExposeHeaders": [
            "ETag"
        ],
        "MaxAgeSeconds": 3000
    }
]
```

## API Endpoints

The Flask application provides several API endpoints for interaction.

| Method | Endpoint                        | Description                                                                 |
| ------ | ------------------------------- | --------------------------------------------------------------------------- |
| `POST` | `/create_multipart_upload`      | Initializes a multipart upload and returns presigned URLs for each chunk.   |
| `POST` | `/complete_multipart_upload`    | Finalizes the multipart upload after all chunks are uploaded.               |
| `GET`  | `/status/<video_id>`            | Gets the detailed processing status of a specific video.                    |
| `GET`  | `/api/videos`                   | Returns a list of all successfully processed videos.                        |
| `GET`  | `/api/transcoding_status`       | Returns a list of videos that are currently in the "processing" state.      |
| `DELETE`| `/api/video/<video_id>`         | Deletes a video and all its associated files (HLS, DASH, sprites, etc.).    |
| `GET`  | `/stream/<path:key>`            | Redirects to a presigned S3 URL to stream video content.                    |

## How It Works

1.  **Upload:** A user uploads a video file via the frontend. For large files, the frontend uses the multipart upload endpoints to upload the file in chunks directly to the `uploads/` prefix in the S3 bucket.
2.  **Trigger:** The S3 `put` event triggers the Lambda function.
3.  **Processing (`process_video`):**
    *   The function downloads the source video.
    *   It calculates the file's MD5 hash, which becomes the `process_id`. This ensures that if the same file is uploaded again, it won't be re-processed.
    *   A redirect file (`processed/<original_filename>.json`) is created to map the original name to the `process_id`.
    *   A `manifest.json` is created in `processed/<process_id>/` to track the state.
    *   The video is transcoded into multiple resolutions using FFmpeg. HLS (and optionally DASH) files are generated.
    *   A thumbnail sprite sheet and VTT file are created for scrubbing previews.
    *   All artifacts are uploaded to the `processed/<process_id>/` directory in S3.
    *   The final `manifest.json` is updated with the status `processing_complete` and paths to all assets.
4.  **Event Handling for Processed Files:** The second S3 trigger is configured for `.json` files in the `processed/` directory. This allows the function to react to events like the completion of an Amazon Transcribe job. The function's logic is designed to handle these events appropriately and avoid infinite recursion from files it generates itself.
5.  **Status Check:** The frontend polls the `/status/<video_id>` endpoint to monitor the progress from `processing` to `processing_complete`.
6.  **Playback:** Once complete, the frontend can retrieve the list of videos from `/api/videos` and play them using the HLS or DASH manifest URLs. The `/stream/` endpoint provides the necessary presigned URLs for the player to access the video segments from S3 securely.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
