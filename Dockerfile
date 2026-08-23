# =====================================================================
# CTC_bot — club results dashboard.
#
# Deliberately NOT pixi: the local pixi environment is 1.6 GB, which is the
# right trade on a workstation and the wrong one on a 40 GB VPS shared with
# every other app. requirements.txt is already portable — pywin32 is guarded
# by `sys_platform == "win32"` and simply does not install here.
#
# Data lives on a volume at /app/data, not in the image: identity.json holds
# hundreds of hand-made claims that nothing can regenerate.
# =====================================================================
FROM python:3.12-slim

# Refresh runs on Wednesday and Friday mornings; without a timezone the
# container works in UTC and the schedule drifts an hour across the year.
ENV TZ=Europe/Dublin \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not re-resolve the whole tree.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ctc_bot/ ./ctc_bot/
COPY scripts/ ./scripts/

# Non-root, and it must own the data directory or the volume mounts read-only
# to the process that has to write claims into it.
RUN useradd --create-home --uid 10001 ctc \
    && mkdir -p /app/data /app/out \
    && chown -R ctc:ctc /app
USER ctc

EXPOSE 8777

# Bind to every interface: inside a container 127.0.0.1 is unreachable from
# Traefik. The write API is protected by basicauth at the proxy, and by
# CTC_READ_ONLY here if that ever fails to attach.
ENV CTC_HOST=0.0.0.0 \
    PORT=8777 \
    CTC_READ_ONLY=1

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8777/api/health',timeout=4).status==200 else 1)"

CMD ["python", "scripts/dashboard.py", "--no-open", "--schedule"]
