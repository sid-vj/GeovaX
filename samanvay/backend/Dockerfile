FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      gdal-bin libgdal-dev libgeos-dev libproj-dev proj-data p7zip-full git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY samanvay ./samanvay
RUN pip install --no-cache-dir -e .

ENV PYTHONUNBUFFERED=1 SAMANVAY_OUT=/data/out
EXPOSE 8000
CMD ["uvicorn", "samanvay.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
