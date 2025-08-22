FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    git libpq-dev gcc gfortran mariadb-client \
    libmariadb-dev libmagic-dev libcfitsio-bin libffi-dev libgsl-dev \
    && apt-get autoclean && rm -rf /var/lib/apt/lists/*

WORKDIR /snex2

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . /snex2

RUN chmod +x ./run.sh

RUN pip uninstall -y ligo.skymap && pip install ligo.skymap

ENTRYPOINT ["./run.sh"]