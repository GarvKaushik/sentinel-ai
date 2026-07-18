# Dummy target service — the "production app" Sentinel AI observes.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY dummy/requirements.txt ./dummy/requirements.txt
RUN pip install -r dummy/requirements.txt

COPY dummy ./dummy

EXPOSE 9000

CMD ["uvicorn", "dummy.app:app", "--host", "0.0.0.0", "--port", "9000"]
