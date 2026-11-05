# Find My Mines - game server image.
#
# The server draws a pygame admin console, so it runs under SDL's dummy video
# driver. No display, audio device, or X server is required.
#
# Build:
#   docker build -t find-my-mines:local .
#
# The trained bots need torch. The default image falls back to the solver in
# ai.py; add --build-arg INSTALL_TORCH=true for the .pt models in bot/weights.

FROM python:3.12-slim

ARG INSTALL_TORCH=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SDL_VIDEODRIVER=dummy \
    SDL_AUDIODRIVER=dummy

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      fontconfig \
      fonts-dejavu-core \
      libgl1 \
      libglib2.0-0 \
      procps \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/requirements.txt bot/requirements.txt
RUN if [ "$INSTALL_TORCH" = "true" ]; then \
      pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
      && pip install --no-cache-dir numpy scipy; \
    fi

RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --create-home app

COPY --chown=app:app . .

RUN mkdir -p /app/data && chown app:app /app/data

USER app

EXPOSE 55550

# A plain process check keeps the probe from creating throwaway client slots.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD pgrep -f "python server.py" > /dev/null || exit 1

CMD ["python", "server.py"]
