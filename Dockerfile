FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot source
COPY wea.py .

# /data is a mounted volume for persistent user location storage
VOLUME ["/data"]

CMD ["python", "-u", "wea.py"]
