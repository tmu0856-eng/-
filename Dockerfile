FROM python:3.11-slim
ARG APT_MIRROR=
ARG PIP_INDEX=
ARG LOCAL_MODELS=0
WORKDIR /app
RUN if [ -n "$APT_MIRROR" ]; then sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; fi \
 && apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-embedding.txt ./
RUN pip install --no-cache-dir ${PIP_INDEX:+-i $PIP_INDEX} -r requirements.txt
RUN if [ "$LOCAL_MODELS" = "1" ]; then pip install --no-cache-dir torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu && pip install --no-cache-dir ${PIP_INDEX:+-i $PIP_INDEX} -r requirements-embedding.txt; fi
COPY backend ./backend
COPY frontend ./frontend
COPY run.py .
ENV APP_HOST=0.0.0.0 APP_PORT=8765
EXPOSE 8765
CMD ["python", "run.py"]
