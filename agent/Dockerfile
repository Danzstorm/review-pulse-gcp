FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py agent.py tools.py ./

# Cloud Run sets PORT. Credentials come from the service's own service account.
ENV PORT=8080
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT}
