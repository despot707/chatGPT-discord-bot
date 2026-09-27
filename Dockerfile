FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --uid 10001 botuser \
    && mkdir -p /app/data \
    && chown botuser:botuser /app/data

COPY constraints.txt requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY --chown=botuser:botuser main.py system_prompt.txt LICENSE ./
COPY --chown=botuser:botuser src/ ./src/
COPY --chown=botuser:botuser utils/ ./utils/

USER botuser

CMD ["python", "main.py"]
