# LangGraph Agent — build from the code/ directory so shared/ is included:
#   docker build --platform linux/amd64 -f agent_service/Dockerfile -t pt-agent .
FROM python:3.12-slim

WORKDIR /app
COPY agent_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ shared/
COPY agent_service/ agent_service/

EXPOSE 8000
CMD ["uvicorn", "agent_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
