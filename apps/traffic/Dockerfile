FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
EXPOSE 8090
# Single worker on purpose: the dial state and load loop live in one process.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]
