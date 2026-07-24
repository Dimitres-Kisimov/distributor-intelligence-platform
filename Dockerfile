# Distributor Intelligence Platform — container image.
FROM python:3.12-slim

# System deps kept minimal; matplotlib/openpyxl/ortools ship manylinux wheels.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

# Serve the Flask app via gunicorn (2 workers, generous timeout for cold caches).
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "app:app"]
