FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app/apps/backend/noteapp-server

RUN useradd --create-home --shell /usr/sbin/nologin noteapp

COPY apps/backend/noteapp-server/requirements.txt /tmp/noteapp-server-requirements.txt
RUN pip install --no-cache-dir -r /tmp/noteapp-server-requirements.txt

COPY apps/backend/noteapp-server/ /app/apps/backend/noteapp-server/

RUN mkdir -p /var/lib/noteapp-server/runtime \
    /var/lib/noteapp-server/db \
    /var/backups/noteapp-server \
    && chown -R noteapp:noteapp /app/apps/backend/noteapp-server \
    /var/lib/noteapp-server \
    /var/backups/noteapp-server

USER noteapp

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
