FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY sim ./sim
USER nobody
EXPOSE 8070
CMD ["python", "-m", "sim.server"]
