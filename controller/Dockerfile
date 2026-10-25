FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY domotica ./domotica
RUN useradd --uid 1000 --create-home app && mkdir -p /app/dati && chown app /app/dati
USER app
EXPOSE 8080
CMD ["python", "-m", "domotica.main"]
