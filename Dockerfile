FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ARG APP_BUILD_SHA=dev
ARG APP_GITHUB_REPOSITORY=
ENV APP_BUILD_SHA=${APP_BUILD_SHA}
ENV GITHUB_REPOSITORY=${APP_GITHUB_REPOSITORY}

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends gcc libpq-dev postgresql-client \
  && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --no-cache-dir --upgrade pip \
  && pip install --no-cache-dir .

CMD ["python", "-m", "app.main"]


