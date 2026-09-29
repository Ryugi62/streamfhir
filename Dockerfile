# One-container demo (dry run only; live sending stays off unless STREAMFHIR_ALLOW_LIVE=1)
FROM python:3.12-slim
WORKDIR /app
COPY streamfhir ./streamfhir
COPY data ./data
COPY fhir ./fhir
EXPOSE 8000
CMD ["python", "-m", "streamfhir", "serve", "--host", "0.0.0.0", "--port", "8000"]
