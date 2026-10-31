ARG PYTHON_IMAGE=python:3.11-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FASTEMBED_CACHE_PATH=/opt/fastembed
WORKDIR /app

COPY requirements.txt .
# The cache mount keeps downloads between attempts, so a flaky network only costs the missing pieces.
RUN --mount=type=cache,target=/root/.cache/pip pip install --retries 10 --timeout 60 -r requirements.txt

# Bake the reranker model into the image so nothing is downloaded at runtime (used when RERANK_ENABLED=true).
RUN python -c "from fastembed.rerank.cross_encoder import TextCrossEncoder; TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2')"

COPY app ./app
COPY migrations ./migrations
COPY eval ./eval

RUN useradd -m appuser && chown -R appuser /opt/fastembed
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
