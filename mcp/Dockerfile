FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN addgroup --system mcp && \
    adduser --system --ingroup mcp mcp

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

RUN chown -R mcp:mcp /app

USER mcp

EXPOSE 8000

CMD ["python", "server.py"]
