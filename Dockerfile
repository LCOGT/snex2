FROM python:3.11-slim

ENTRYPOINT ["./run.sh"]

RUN apt-get update && apt-get install -y git libpq-dev gcc gfortran mariadb-client curl \
    libmariadb-dev libmagic-dev libcfitsio-bin libffi-dev libgsl-dev && apt-get autoclean libcurl && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

COPY . /snex2

RUN /root/.local/bin/uv pip install /snex2 --system 

WORKDIR /snex2
