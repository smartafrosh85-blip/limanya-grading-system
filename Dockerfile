# Build a minimal container for the Limanya grading system
FROM python:3.11-slim

WORKDIR /app

# Keep Python output unbuffered
ENV PYTHONUNBUFFERED=1

# Copy server and web assets into the container; ensure static files are under ./static
COPY server.py .
COPY index.html app.js styles.css ./static/

# Create directory for persistent data (SQLite DB and student photos)
RUN mkdir -p /data && chown -R root:root /data

EXPOSE 8000

# Default environment values — override at runtime if needed
ENV LIMANYA_HOST=0.0.0.0
ENV LIMANYA_PORT=8000
ENV LIMANYA_DB_PATH=/data/limanya.db

VOLUME ["/data"]

CMD ["python", "server.py"]
