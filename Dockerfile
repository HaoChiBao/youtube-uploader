# YouTube Uploader API — Cloud Run (or any container host)
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libjpeg62-turbo zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY uploader/ uploader/
COPY cli/ cli/
COPY api/ api/
COPY config/channels.yaml.example config/channels.yaml.example

RUN pip install --no-cache-dir ".[api,s3]"

# Cloud Run injects PORT; bind all interfaces in the container.
ENV UPLOADER_API_HOST=0.0.0.0
ENV PORT=8080

EXPOSE 8080

CMD ["uploader-api"]
