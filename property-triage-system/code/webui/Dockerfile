# WebUI (Flask) — build from code/webui/.  Binds 0.0.0.0 via HOST env (compose).
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 5050
CMD ["python", "server.py"]
