FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip python3-libvirt \
        qemu-utils cloud-image-utils ca-certificates \
    && (apt-get install -y --no-install-recommends genisoimage || true) \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv --system-site-packages /venv

ENV PATH=/venv/bin:$PATH

WORKDIR /srv

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
