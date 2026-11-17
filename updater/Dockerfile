# Docker-CLI 27 mit Compose-Plugin: kompatibel zu älteren Daemons (z. B. Synology Container Manager)
# und zu aktuellen Docker-Versionen.
FROM docker:27.5.1-cli
RUN apk add --no-cache python3
WORKDIR /app
COPY updater.py ./
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"]
ENTRYPOINT []
CMD ["python3", "/app/updater.py"]
