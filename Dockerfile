FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    PORT=8000

ARG APP_UID=1000
ARG APP_GID=1000

WORKDIR /app

# bubblewrap isolates the LLM-generated Python that builds each report.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bubblewrap \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app

COPY requirements.txt requirements-lock.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt -c requirements-lock.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY prompts ./prompts
COPY static ./static
COPY list_models.py .

RUN mkdir -p workspace/uploads workspace/scripts workspace/reports workspace/filtered workspace/tenants \
    && chown -R app:app workspace

USER app

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
