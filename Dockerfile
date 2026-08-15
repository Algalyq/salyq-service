FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir uv && uv pip install --system --no-cache -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["granian", "app.main:app", "--interface", "asgi", "--host", "0.0.0.0", "--port", "8000"]
