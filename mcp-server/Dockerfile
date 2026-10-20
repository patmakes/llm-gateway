FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin mcp
COPY server.py .

USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8000), 3)"
CMD ["python", "server.py"]
