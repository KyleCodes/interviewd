FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Needs /var/run/docker.sock mounted by compose to act on the daemon.
EXPOSE 9102
CMD ["python", "main.py"]
