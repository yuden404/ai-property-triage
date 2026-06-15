# Image Analyser — build from the code/ directory:
#   docker build --platform linux/amd64 -f image_analyser/Dockerfile -t pt-image .
# CPU-only torch wheels keep the image small (no CUDA). model.pth (from train.py)
# is baked in if present locally; without it the service runs as a stub.
FROM python:3.12-slim

WORKDIR /app
COPY image_analyser/requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

COPY shared/ shared/
COPY image_analyser/ image_analyser/

EXPOSE 8002
CMD ["uvicorn", "image_analyser.main:app", "--host", "0.0.0.0", "--port", "8002"]
