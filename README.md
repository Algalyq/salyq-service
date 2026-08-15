# salyq-service

FastAPI backend for Salyq CMS.

## Stack

- FastAPI + Uvicorn
- python-jose (JWT)
- Pydantic Settings (env config)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env .env  # adjust if needed
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Endpoints

| Method | Path                   | Description                              |
|--------|------------------------|------------------------------------------|
| GET    | `/health`              | Health check                             |
| POST   | `/api/auth/challenge`  | Returns random challenge hash            |
| POST   | `/api/auth/login`      | Validates CMS signature, returns JWT     |

## Environment

See `.env` for configuration (CORS origins, JWT secret, token expiry).
