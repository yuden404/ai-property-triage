# Guardrails Service — build from the code/ directory so shared/ is included:
#   docker build --platform linux/amd64 -f guardrails_service/Dockerfile -t pt-guardrails .
FROM python:3.12-slim

WORKDIR /app
COPY guardrails_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ shared/
COPY guardrails_service/ guardrails_service/

EXPOSE 8003
CMD ["uvicorn", "guardrails_service.main:app", "--host", "0.0.0.0", "--port", "8003"]
