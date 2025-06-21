# Use the AWS Lambda Python 3.13 base image (Amazon Linux 2023)
FROM public.ecr.aws/lambda/python:3.13

# Install dependencies via microdnf
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

# Copy your application code into the Lambda task root
COPY src/transcoder/requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install -r ${LAMBDA_TASK_ROOT}/requirements.txt -t ${LAMBDA_TASK_ROOT}
COPY src/transcoder/app.py ${LAMBDA_TASK_ROOT}/app.py
COPY src/transcoder/templates ${LAMBDA_TASK_ROOT}/templates/

# Set the Lambda handler
CMD ["app.lambda_handler"]
