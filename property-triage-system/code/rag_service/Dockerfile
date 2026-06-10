# RAG Service — build from the code/ directory so shared/ is included:
#   docker build --platform linux/amd64 -f rag_service/Dockerfile -t pt-rag .
FROM python:3.12-slim

WORKDIR /app
COPY rag_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ shared/
COPY rag_service/ rag_service/

EXPOSE 8001
CMD ["uvicorn", "rag_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
