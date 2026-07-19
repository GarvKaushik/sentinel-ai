# Streamlit demo cockpit. Lightweight (streamlit + requests only) — it talks to
# every other service over HTTP, so it holds no ML dependencies.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY ui/requirements.txt ./ui/requirements.txt
RUN pip install -r ui/requirements.txt

COPY ui ./ui
COPY .streamlit ./.streamlit

EXPOSE 8501

CMD ["streamlit", "run", "ui/app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
