FROM python:3.12-slim

WORKDIR /build

# Copy repo context needed for vendored sync verification
COPY ai-signal-generator/ ./ai-signal-generator/
COPY strategy-tester/ ./strategy-tester/

# Install make (needed for check-vendored)
RUN apt-get update && apt-get install -y --no-install-recommends make && rm -rf /var/lib/apt/lists/*

# Verify vendored files match upstream (build fails on drift)
RUN cd strategy-tester && make check-vendored

# Install dependencies and finalise app dir
WORKDIR /app
RUN cp -r /build/strategy-tester/. /app/
RUN pip install --no-cache-dir -r requirements.txt
RUN rm -rf /build

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8006"]
