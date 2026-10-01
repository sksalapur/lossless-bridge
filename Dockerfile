FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# Use shell form to evaluate the $PORT environment variable injected by Render/Heroku
CMD uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-3000}
