FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
EXPOSE 8000
# One uvicorn process per replica: prometheus_client metrics are per-process,
# so multiple workers behind one scrape port would flap between registries.
# Concurrency comes from replica count (autoscaler + gateway LB), not forks.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
