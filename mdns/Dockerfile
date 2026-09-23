# Listens for Android wireless-debugging mDNS announcements on the host's
# network and hands them to the app through a shared volume. See listener.py.
FROM python:3.14-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2

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
