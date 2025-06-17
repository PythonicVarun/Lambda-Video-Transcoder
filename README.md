# Lambda Video Transcoder

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

🔥 A serverless AWS Lambda solution for on-the-fly video transcoding, transcription, and adaptive streaming (HLS & DASH).

This project provides a robust and scalable way to process video files uploaded to an S3 bucket. When a video is uploaded, a Lambda function is triggered to:
1.  **Probe Video:** Determine resolution and other metadata.
2.  **Transcode to Multiple Resolutions:** Create different quality levels suitable for adaptive bitrate streaming (e.g., 1080p, 720p, 480p).
3.  **Generate HLS & DASH Playlists:** Create manifest files for Apple HLS and MPEG-DASH streaming.
4.  **Create Sprite Sheet:** Generate a thumbnail sprite sheet for video scrubbing previews.
5.  **Transcribe Audio:** Use Amazon Transcribe to generate a text transcription of the video's audio.
6.  **Stream Content (Optional):** A secondary Lambda handler can be exposed via API Gateway to serve the transcoded video segments and playlists directly from S3, enabling byte-range requests for efficient streaming.

## Features
-   **Serverless Architecture:** Leverages AWS Lambda for compute, S3 for storage, and Amazon Transcribe for audio-to-text.
-   **Adaptive Bitrate Streaming:** Outputs HLS and DASH formats for wide compatibility across devices.
-   **Automated Transcription:** Integrates with Amazon Transcribe.
-   **Thumbnail Sprite Generation:** For enhanced video player UIs.
-   **Multiple Deployment Options:** Supports both traditional .zip deployment with Lambda Layers and Docker container image deployment.
-   **Customizable Presets:** Easily configure video output resolutions and bitrates.

## Project Structure
```
.
├── Dockerfile                # For Docker-based Lambda deployment
├── LICENSE                   # Project License
├── README.md                 # This file
├── requirements.txt          # Root Python dependencies (if any, for local dev)
├── events/
│   └── basic.json            # Sample event for local testing
├── src/
│   └── transcoder/
│       ├── __init__.py
│       ├── app.py            # Core Lambda function logic
│       └── requirements.txt  # Dependencies for the Lambda function
└── tests/
    ├── __init__.py
    └── test_handler.py       # Unit tests (example)
```

## Prerequisites
-   AWS Account
-   AWS CLI installed and configured
-   Docker installed (for Docker-based deployment)
-   Python 3.9+ (for local development and .zip deployment)
-   Access to static builds of FFmpeg and ffprobe (for .zip/Layer deployment, or can be downloaded in Dockerfile)

## Deployment Options 🚀

You can deploy this application to AWS Lambda using either a traditional .zip archive with Lambda Layers or by using a Docker container image.

## Option 1: Deployment using .zip archive and Lambda Layers

This is the traditional method for deploying Lambda functions.

**1. Prepare Lambda Layer for ffmpeg/ffprobe:**
   - Download static builds of `ffmpeg` and `ffprobe` compatible with the Lambda runtime's Amazon Linux version (e.g., Amazon Linux 2 for Python 3.9/3.11). A common source is [John Van Sickle's FFmpeg builds](https://johnvansickle.com/ffmpeg/).
   - Create the following folder structure:
     ```
     python/
         bin/
             ffmpeg
             ffprobe
     ```
   - Ensure `ffmpeg` and `ffprobe` are executable (`chmod +x ffmpeg ffprobe`).
   - Zip the `python` folder (e.g., `ffmpeg-layer.zip`).
   - In the AWS Lambda console, create a new Layer and upload `ffmpeg-layer.zip`. Note the Layer ARN.

**2. Package Your Application Code:**
   - Your application code is in `src/transcoder/app.py`.
   - If you have Python dependencies beyond `boto3` (which is included in the Lambda Python runtime), as listed in `src/transcoder/requirements.txt`, install them into a package directory:
     ```bash
     pip install -r src/transcoder/requirements.txt -t ./package 
     ```
   - Create a .zip file containing your `app.py` (copied from `src/transcoder/`) and the contents of the `package` directory. `app.py` should be at the root of the .zip file.
     ```bash
     cp src/transcoder/app.py ./app.py # Copy app.py to root for zipping
     # If you have a package directory
     zip -r lambda_function.zip app.py ./package
     # If you only have app.py (and boto3 is sufficient)
     # zip lambda_function.zip app.py
     rm app.py # Clean up copied file
     ```
     Ensure `app.py` is at the root of the zip. If you copied `app.py` into `src/transcoder/` within the zip, the handler path will need to reflect that. For simplicity, it's often easier to ensure `app.py` is at the root.

**3. Create Lambda Function (Zip file):**
   - In the AWS Lambda Console, click **"Create function"**.
   - Choose **"Author from scratch"**.
   - **Function name:** Enter a descriptive name.
   - **Runtime:** Select a Python version (e.g., Python 3.11 or as supported).
   - **Architecture:** Choose `x86_64` or `arm64` based on your ffmpeg build and preference.
   - **Permissions:** Create a new execution role with basic Lambda permissions, or choose an existing one. This role will be modified later.
   - Click **"Create function"**.
   - **Upload code:** In the "Code source" section, upload your `lambda_function.zip`.
   - **Handler:** Set the handler to `app.lambda_handler` (assuming `app.py` is at the root of your zip and named `app.py`).
   - **Layers:** Add the ffmpeg/ffprobe Lambda Layer you created in step 1.

## Option 2: Deployment using Docker Container Image

This method packages your application and dependencies, including FFmpeg, into a Docker image.

**1. Review/Update Dockerfile:**
   - The provided `Dockerfile` in the repository is a good starting point.
     ```dockerfile
     # Use the AWS Lambda Python 3.13 base image (Amazon Linux 2023)
     FROM public.ecr.aws/lambda/python:3.13

     # Install dependencies via microdnf (if any beyond base image + ffmpeg)
     RUN microdnf update -y && \
         microdnf install -y tar xz && \
         microdnf clean all

     # Download static build of FFmpeg
     RUN curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz \
          -o /tmp/ffmpeg.tar.xz && \
         tar -xJf /tmp/ffmpeg.tar.xz -C /tmp && \
         mkdir -p /opt/bin && \
         cp /tmp/ffmpeg-*-static/ffmpeg /opt/bin/ && \
         cp /tmp/ffmpeg-*-static/ffprobe /opt/bin/ && \
         chmod +x /opt/bin/ffmpeg /opt/bin/ffprobe && \
         rm -rf /tmp/*

     # Copy application code and any other necessary files
     # If you have a requirements.txt specific to the transcoder (src/transcoder/requirements.txt):
     COPY src/transcoder/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
     RUN pip install -r ${LAMBDA_TASK_ROOT}/requirements.txt -t ${LAMBDA_TASK_ROOT}
     
     COPY src/transcoder/app.py ${LAMBDA_TASK_ROOT}/app.py
     # Ensure your app.py uses /opt/bin/ffmpeg and /opt/bin/ffprobe

     # Set the Lambda handler (filename.handler_function)
     CMD ["app.lambda_handler"]
     ```
   - Ensure the `FFMPEG` and `FFPROBE` paths in `src/transcoder/app.py` are correctly set to `/opt/bin/ffmpeg` and `/opt/bin/ffprobe` respectively (this is done by default in the current `app.py`).
   - If `src/transcoder/requirements.txt` exists and contains dependencies beyond `boto3`, uncomment and adjust the `COPY` and `RUN pip install` lines in the `Dockerfile`.

**2. Build and Push Docker Image to Amazon ECR:**
   - **Install AWS CLI and Docker:** Ensure they are installed and configured locally.
   - **Create ECR Repository (if it doesn't exist):**
     ```bash
     aws ecr create-repository --repository-name your-lambda-repo-name --image-scanning-configuration scanOnPush=true --region your-aws-region
     ```
     Replace `your-lambda-repo-name` and `your-aws-region`.
   - **Authenticate Docker to your ECR registry:**
     ```bash
     aws ecr get-login-password --region your-aws-region | docker login --username AWS --password-stdin YOUR_AWS_ACCOUNT_ID.dkr.ecr.your-aws-region.amazonaws.com
     ```
     Replace `your-aws-region` and `YOUR_AWS_ACCOUNT_ID`.
   - **Build Docker Image:** Navigate to the root directory of your project (where `Dockerfile` is located) and run:
     ```bash
     docker build -t your-lambda-repo-name .
     ```
   - **Tag Docker Image for ECR:**
     ```bash
     docker tag your-lambda-repo-name:latest YOUR_AWS_ACCOUNT_ID.dkr.ecr.your-aws-region.amazonaws.com/your-lambda-repo-name:latest
     ```
   - **Push Docker Image to ECR:**
     ```bash
     docker push YOUR_AWS_ACCOUNT_ID.dkr.ecr.your-aws-region.amazonaws.com/your-lambda-repo-name:latest
     ```

**3. Create Lambda Function (Container Image):**
   - In the AWS Lambda Console, click **"Create function"**.
   - Select **"Container image"**.
   - **Function name:** Enter a descriptive name.
   - **Container image URI:** Click **"Browse images"** and select the image you pushed to ECR (e.g., `your-lambda-repo-name:latest`).
   - **Architecture:** Choose `x86_64` (matching the ffmpeg build in the Dockerfile).
   - **Permissions:** Create a new execution role or choose an existing one. This role will be modified.
   - Click **"Create function"**.

## Common Configuration Steps (for both deployment options)

**1. IAM Permissions:**
   - Go to the IAM console and find the execution role associated with your Lambda function.
   - Attach policies that grant the following permissions:
     - **AmazonS3FullAccess** (or a more restrictive policy granting `s3:GetObject` on the source bucket and `s3:PutObject` on the destination bucket/prefix).
       Example inline policy for S3:
       ```json
       {
           "Version": "2012-10-17",
           "Statement": [
               {
                   "Effect": "Allow",
                   "Action": [
                       "s3:GetObject"
                   ],
                   "Resource": "arn:aws:s3:::YOUR_SOURCE_BUCKET_NAME/*"
               },
               {
                   "Effect": "Allow",
                   "Action": [
                       "s3:PutObject",
                       "s3:PutObjectAcl" // Optional, if you need to set ACLs
                   ],
                   "Resource": "arn:aws:s3:::YOUR_DESTINATION_BUCKET_NAME/*" 
               }
           ]
       }
       ```
       Replace `YOUR_SOURCE_BUCKET_NAME` and `YOUR_DESTINATION_BUCKET_NAME`. The `processed/` prefix is handled by the application logic.
     - **AmazonTranscribeFullAccess** (or a more restrictive policy granting `transcribe:StartTranscriptionJob`).
       Example inline policy for Transcribe:
       ```json
       {
           "Version": "2012-10-17",
           "Statement": [
               {
                   "Effect": "Allow",
                   "Action": "transcribe:StartTranscriptionJob",
                   "Resource": "*"
               }
           ]
       }
       ```
     - **AWSLambdaBasicExecutionRole** (usually added by default): Allows writing logs to CloudWatch (`logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`).

**2. Lambda Function Configuration:**
   - In the Lambda function console, navigate to the **"Configuration"** tab.
   - **General configuration:**
     - **Memory:** Increase memory. Video processing is memory-intensive. Start with **2048 MB** or **4096 MB** and monitor/adjust based on execution logs and performance.
     - **Ephemeral storage (for container images):** If using container image deployment, you can increase ephemeral storage beyond the default 512MB if your FFmpeg processes require more temporary disk space (up to 10 GB). For .zip deployments, `/tmp` is limited to 512MB.
     - **Timeout:** Increase the timeout. Video processing can be slow. Start with **5 minutes** (300 seconds) or up to the maximum of **15 minutes** (900 seconds). Monitor and adjust.
   - **Environment Variables (Optional):**
     - `LANGUAGE_CODE`: Defaults to "en-US" in `app.py`. You can override it here if needed for other languages supported by Amazon Transcribe.
     - Any other custom environment variables your application might need.

**3. Triggers:**

   **A. S3 Trigger (for `process_video` function):**
   - In the Lambda console for your function, go to **"Function overview"** and click **"Add trigger"**.
   - Select **"S3"**.
   - **Bucket:** Choose the S3 bucket where raw videos will be uploaded.
   - **Event type:** Select **"All object create events"** or be more specific (e.g., `PUT`, `POST`, `CompleteMultipartUpload`).
   - **Prefix (Optional):** If you want the Lambda to trigger only for uploads to a specific folder (e.g., `uploads/`).
   - **Suffix (Optional):** If you want to trigger only for specific file types (e.g., `.mp4`).
   - Acknowledge the recursive invocation warning if your Lambda writes back to the same bucket (though this app writes to a `processed/` prefix, which should avoid direct recursion if the trigger is on the root or a different prefix).
   - Click **"Add"**.

   **B. API Gateway Trigger (for `stream_handler` function):**
   - This allows HTTP(S) access to serve the HLS/DASH manifests and video segments.
   - In the Lambda console for your function, go to **"Function overview"** and click **"Add trigger"**.
   - Select **"API Gateway"**.
   - Choose **"Create an API"**.
   - Select **"HTTP API"** (recommended for simplicity and cost) or REST API.
   - **Security:** For initial testing, **"Open"** is fine. For production, implement appropriate authorization (e.g., IAM, Lambda authorizer, API Key).
   - **API name, Deployment stage:** Configure as needed.
   - **Route:** The `stream_handler` expects `bucket` and `key` as query string parameters. A common route might be `/stream` or `/videos/{proxy+}`. You will need to ensure the integration passes query parameters.
   - Click **"Add"**. Note the **API endpoint URL** provided after creation. This URL will be used to access your video streams.

## Testing

**1. Testing `process_video` (S3 Trigger):**
   - Upload a video file (e.g., an MP4) to the S3 bucket and prefix you configured as the trigger.
   - Monitor the Lambda function's execution in Amazon CloudWatch Logs.
   - Check your destination S3 bucket (and the `processed/` prefix) for the output HLS files, DASH files, `sprite.png`, and the transcription JSON.

**2. Testing `stream_handler` (API Gateway Trigger):**
   - Once `process_video` has successfully run, you can test the streaming.
   - Construct the URL using your API Gateway endpoint and the S3 key for a manifest or segment.
     - Example for HLS master playlist (replace placeholders):
       `YOUR_API_ENDPOINT/stream?bucket=YOUR_S3_BUCKET_NAME&key=processed/YOUR_VIDEO_BASENAME/hls/master.m3u8`
     - Example for a video segment (replace placeholders):
       `YOUR_API_ENDPOINT/stream?bucket=YOUR_S3_BUCKET_NAME&key=processed/YOUR_VIDEO_BASENAME/hls/720p/seg_000_720p.ts`
   - You can use a tool like `curl`, a web browser, or an HLS/DASH test player (like VLC or online players) to access these URLs.
   - Check CloudWatch Logs for the API Gateway and Lambda function if you encounter issues.

## Important Notes:
- **FFmpeg Static Builds:** Ensure the FFmpeg static build used (either in Layer or Docker) is compatible with the Lambda execution environment (Amazon Linux 2 for older Python runtimes, Amazon Linux 2023 for `public.ecr.aws/lambda/python:3.13` base image). The `Dockerfile` downloads a common amd64 static build.
- **Costs:** Be mindful of AWS costs: S3 (storage, requests, data transfer), Lambda (invocations, duration, memory), ECR (storage), API Gateway (requests, data transfer), and Amazon Transcribe (transcription minutes).
- **Error Handling & Logging:** The provided code has basic error handling. For production, implement more robust error handling, use structured logging, and consider setting up Dead-Letter Queues (DLQs) for your Lambda function to handle failed invocations.
- **Large Files & Long Processing:** For very large video files or extremely long processing times, Lambda's 15-minute timeout or /tmp storage limits (512MB for .zip, configurable up to 10GB for containers) might be insufficient. In such scenarios, consider AWS Batch for asynchronous, long-running jobs or AWS Elemental MediaConvert for a managed media conversion service.
- **Idempotency:** Consider if parts of your workflow need to be idempotent, especially if retries occur.
- **Security:**
    - Adhere to the principle of least privilege for IAM roles. Grant only the necessary permissions.
    - Secure your API Gateway endpoint using appropriate authentication and authorization mechanisms for production.
    - Regularly update dependencies, including the base Docker image and FFmpeg.

## Local Development & Testing (Conceptual)

While full end-to-end testing requires AWS services, you can test parts of the `app.py` logic locally:

1.  **Setup:**
    *   Ensure Python 3.9+ is installed.
    *   Install dependencies: `pip install -r src/transcoder/requirements.txt boto3 moto` (Moto is for mocking AWS services).
    *   Have FFmpeg and ffprobe installed locally and accessible in your PATH, or adjust `FFMPEG`/`FFPROBE` paths in `app.py` for local testing.
2.  **Mocking AWS Services:**
    *   Use `moto` to mock S3 and Transcribe calls during local unit tests.
3.  **Sample Event:**
    *   Use `events/basic.json` (you might need to create/modify this to represent an S3 event) to simulate a Lambda invocation.
    *   Example `events/basic.json` for S3 trigger:
        ```json
        {
          "Records": [
            {
              "s3": {
                "bucket": {
                  "name": "your-local-test-bucket"
                },
                "object": {
                  "key": "sample.mp4"
                }
              }
            }
          ]
        }
        ```
4.  **Running `app.lambda_handler`:**
    *   Write a small Python script to load the event and call `app.lambda_handler(event, None)`.

```python
# Example local_runner.py (place in project root)
import json
import sys
sys.path.append('src/transcoder') # Add app module to path
from app import lambda_handler

if __name__ == '__main__':
    with open('events/basic.json', 'r') as f:
        event = json.load(f)
    
    # --- Setup for local testing ---
    # 1. Create a dummy sample.mp4 or use a small test video.
    # 2. Manually create ./temp_s3_bucket/sample.mp4 if your code expects to download it.
    #    OR modify app.py to use a local file path directly for tmp_in for local testing.
    # 3. FFmpeg/ffprobe must be in PATH or paths in app.py adjusted.
    #
    # This local run won't interact with actual S3/Transcribe unless you configure
    # boto3 with real credentials and endpoints, or use moto for mocking.
    # For true local simulation of S3, tools like localstack can be used.
    
    print("Simulating Lambda invocation locally...")
    result = lambda_handler(event, None)
    print("\nLambda Output:")
    print(json.dumps(result, indent=2))

```
This local setup is primarily for unit/integration testing of Python logic, not for testing FFmpeg processing in the exact Lambda environment. For that, deploying to AWS is necessary.

## Contributing
Pull requests are welcome! For major changes, please open an issue first to discuss what you would like to change.
Please make sure to update tests as appropriate.

## License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

