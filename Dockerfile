FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system civicflow && useradd --system --gid civicflow --create-home civicflow
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

USER civicflow
ENTRYPOINT ["civicflow"]
CMD ["--cases", "5000", "--output-dir", "/home/civicflow/output"]

