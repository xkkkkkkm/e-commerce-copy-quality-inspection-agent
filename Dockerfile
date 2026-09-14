# Pin the production base image by digest during release updates.  The tag is
# kept here for reproducible local Docker builds; see docs/configuration.md.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app
USER app
EXPOSE 8000

CMD ["python", "-m", "scripts.start"]
