# Lambda Video Transcoder

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

🔥 A serverless AWS Lambda solution for on-the-fly video transcoding, transcription, and adaptive streaming (HLS & DASH), with a simple Flask-based frontend for uploads.

This project provides a robust and scalable way to process video files. It includes:
1.  **S3 Event Trigger:** A Lambda function is triggered when a video is uploaded to an S3 bucket.
2.  **Video Processing:**
    *   **Probe Video:** Determines resolution and metadata.
    *   **Transcode to Multiple Resolutions:** Creates different quality levels for adaptive bitrate streaming.
    *   **Generate HLS & DASH Playlists:** Creates manifest files for Apple HLS and MPEG-DASH.
    *   **Create Dynamic Sprite Sheet:** Generates a thumbnail sprite sheet for video scrubbing previews.
    *   **Transcribe Audio:** Uses Amazon Transcribe to generate a text transcription.
3.  **Flask Web Interface:**
    *   A simple web page to upload videos directly to S3.
    *   A status page to check the transcoding progress.
    *   A streaming endpoint to serve the transcoded content.
4.  **API Gateway Integration:** The Flask app is served via API Gateway, allowing public access.

## Features
-   **Serverless Architecture:** Leverages AWS Lambda, S3, and Amazon Transcribe.
-   **Flask Frontend:** Easy-to-use web interface for video uploads and status checks.
-   **Adaptive Bitrate Streaming:** Outputs HLS and DASH formats.
-   **Automated Transcription:** Integrates with Amazon Transcribe.
-   **Dynamic Thumbnail Sprite Generation:** Creates a sprite sheet based on video duration.
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
    └── ...
```

## Prerequisites
-   AWS Account
-   AWS CLI installed and configured
-   Docker installed
-   An S3 bucket to store uploads and transcoded files.

## Deployment Guide 🚀

This project is designed for deployment as a Docker container image to AWS Lambda.

**1. Configure Environment Variables:**
   - Before building the Docker image, you need to set the `BUCKET_NAME` environment variable. This can be done in a few ways:
     - **Option A (Hardcode in Dockerfile - for quick tests):**
       ```dockerfile
       # In your Dockerfile, before the CMD
       ENV BUCKET_NAME='your-s3-bucket-name'
       ```
     - **Option B (Set in Lambda Console - Recommended):** You will set this in the Lambda function's configuration after deployment. This is the most flexible and secure method.

**2. Review the Dockerfile:**
   - The `Dockerfile` handles all the necessary steps:
     - Starts from the official AWS Lambda Python base image.
     - Installs FFmpeg.
     - Copies `requirements.txt` and installs Python packages (including Flask and serverless-wsgi).
     - Copies the `app.py` and the `templates` directory.
     - Sets the `CMD` to `app.lambda_handler`.

**3. Build and Push Docker Image to Amazon ECR:**
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

**4. Create and Configure the Lambda Function:**
   - In the AWS Lambda Console, click **"Create function"**.
   - Select **"Container image"**.
   - **Function name:** `lambda-video-transcoder`.
   - **Container image URI:** Browse and select the `lambda-video-transcoder:latest` image from ECR.
   - **Architecture:** `x86_64`.
   - **Permissions:** Create a new execution role. You will attach the necessary policies to this role.
   - Click **"Create function"**.

**5. Configure IAM Permissions:**
   - Go to the IAM console and find the execution role for your Lambda function.
   - Attach the following managed policies or create more restrictive inline policies:
     - `AWSLambdaBasicExecutionRole` (should be attached by default)
     - `AmazonS3FullAccess` (or a policy granting `s3:GetObject`, `s3:PutObject`, `s3:ListBucket` on your specific bucket).
     - `AmazonTranscribeFullAccess` (or a policy granting `transcribe:StartTranscriptionJob`).

**6. Configure Lambda Settings and Triggers:**
   - In the Lambda function console, go to the **"Configuration"** tab.
   - **Environment variables:**
     - Click **"Edit"** and add a new variable:
       - **Key:** `BUCKET_NAME`
       - **Value:** `your-s3-bucket-name` (the name of your S3 bucket)
   - **General configuration:**
     - **Memory:** Increase to at least **2048 MB**.
     - **Timeout:** Increase to **15 minutes** (900 seconds).
   - **Triggers:**
     - **API Gateway Trigger (for the Flask App):**
       - Click **"Add trigger"**, select **"API Gateway"**.
       - Choose **"Create an API"**, select **"HTTP API"**, and **"Open"** security.
       - Note the **API endpoint URL**.
     - **S3 Trigger (for Video Processing):**
       - Click **"Add trigger"**, select **"S3"**.
       - **Bucket:** Select your S3 bucket (`your-s3-bucket-name`).
       - **Event types:** `All object create events`.
       - **Prefix:** `uploads/`
       - Acknowledge the warning and click **"Add"**.

## How to Use

1.  **Open the Web Frontend:**
    - Navigate to the **API endpoint URL** you received when creating the API Gateway trigger.
2.  **Upload a Video:**
    - Use the form to select and upload a video file.
    - Upon successful upload, you will be redirected to a status page.
3.  **Processing:**
    - The upload triggers the S3 event, which invokes the same Lambda function to run the `process_video` logic.
    - This will transcode the video, create playlists, generate the sprite sheet, and start the transcription job.
4.  **Check Status:**
    - The status page will eventually show "Transcoding complete!" once the main HLS playlist is available.
5.  **Accessing Transcoded Content:**
    - The transcoded files are stored in your S3 bucket under the `processed/` prefix.
    - The Flask app also provides a `/stream/` endpoint that can serve these files, which can be used by a video player.

## Important Notes
- **Costs:** Be mindful of AWS costs for S3, Lambda, ECR, API Gateway, and Amazon Transcribe.
- **Error Handling:** For production, consider adding more robust error handling and a dead-letter queue (DLQ) for the Lambda function.
- **Large Files:** For files larger than a few hundred MBs, consider using S3 presigned URLs for direct browser uploads to avoid passing the file through the Lambda function's memory.
- **Security:** For production, secure your API Gateway endpoint and use more restrictive IAM policies.

## License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
