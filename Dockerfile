# Образ с LibreOffice, pandoc и утилитами для конвертера документов (doc/xls/ppt, odt/rtf/epub/fb2, svg, djvu, xps).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer libreoffice-calc libreoffice-impress \
      pandoc librsvg2-bin djvulibre-bin libgxps-utils \
      fontconfig fonts-dejavu-core fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

CMD ["python", "run_clients.py"]
