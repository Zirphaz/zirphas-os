FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app

COPY --chown=app:app . .
RUN mkdir -p /app/uploads \
    && chown app:app /app/uploads \
    && sed -i 's/\r$//' /app/scripts/start.sh \
    && chmod +x /app/scripts/start.sh

USER app
EXPOSE 8000
CMD ["/app/scripts/start.sh"]
