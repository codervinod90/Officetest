FROM python:3.13-slim

# Fixed UID/GID so Kubernetes fsGroup + runAsUser match; avoids pip/venv
# "Permission denied" on many PVCs (EFS/NFS root-squash, hardened clusters).
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home --shell /bin/sh app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
RUN chown -R app:app /app

USER app

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
