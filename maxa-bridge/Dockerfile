# Immagine ufficiale Playwright: Chromium e dipendenze di sistema già installati.
FROM mcr.microsoft.com/playwright/python:v1.48.0-noble
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bridge ./bridge
RUN mkdir -p /app/sessione && chown pwuser /app/sessione
USER pwuser
EXPOSE 8090
CMD ["python", "-m", "bridge.server"]
