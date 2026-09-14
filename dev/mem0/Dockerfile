# Native OSS server with a small PGVector pagination patch. No Platform API proxy.
FROM python:3.13.12-slim

ARG MEM0_REVISION=c7ee362aff94a369af70f13f2b4f853f6793ff4c
ADD https://github.com/mem0ai/mem0/archive/${MEM0_REVISION}.tar.gz /tmp/mem0.tar.gz
RUN mkdir /upstream && tar -xzf /tmp/mem0.tar.gz -C /upstream --strip-components=1 \
    && mv /upstream/server /app && rm -rf /upstream /tmp/mem0.tar.gz
WORKDIR /app
# Keep the core fixed as well as the REST server; never reinstall latest at startup.
RUN pip install --no-cache-dir 'mem0ai==2.0.19' 'psycopg[binary,pool]==3.3.4' -r requirements.txt \
    && apt-get update && apt-get install -y --no-install-recommends patch \
    && rm -rf /var/lib/apt/lists/*
COPY patches/pgvector-pagination.patch /tmp/pagination.patch
RUN patch --batch --fuzz=0 -d /usr/local/lib/python3.13/site-packages -p1 < /tmp/pagination.patch \
    && rm /tmp/pagination.patch
COPY pagination.py configuration.py apply_overlay.py fixture_embeddings.py ./
RUN python apply_overlay.py \
    && useradd --uid 10001 --create-home mem0 \
    && mkdir -p /app/history && chown -R mem0:mem0 /app /home/mem0
USER 10001:10001
ENV PYTHONUNBUFFERED=1 MEM0_TELEMETRY=false HISTORY_DB_PATH=/app/history/history.db
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8000"]
