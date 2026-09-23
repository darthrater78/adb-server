# Listens for Android wireless-debugging mDNS announcements on the host's
# network and hands them to the app through a shared volume. See listener.py.
FROM python:3.13-slim@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin mdns

WORKDIR /opt/mdns
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY listener.py .

# The volume mounted here takes this directory's owner on first use, so the
# unprivileged listener can write to it.
RUN mkdir -p /mdns && chown mdns:mdns /mdns

USER mdns
CMD ["python", "-u", "listener.py"]
