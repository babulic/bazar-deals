FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[server]' && useradd --uid 10001 --create-home app
USER 10001
CMD ["gunicorn", "--bind", "0.0.0.0:8090", "--workers", "1", "--threads", "4", "--timeout", "45", "--error-logfile", "/dev/null", "bazar_deals.ebay_store:create_app()"]
