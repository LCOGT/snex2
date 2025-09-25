FROM python:3.11-slim

ENTRYPOINT ["./run.sh"]

RUN apt-get update && apt-get install -y git libpq-dev gcc gfortran mariadb-client curl \
    libmariadb-dev libmagic-dev libcfitsio-bin libffi-dev libgsl-dev \
    pkg-config && apt-get autoclean  && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

COPY pyproject.toml uv.lock /snex2/

RUN /root/.local/bin/uv --directory=/snex2 export --no-emit-project --format pylock.toml > /snex2/pylock.toml

RUN /root/.local/bin/uv pip sync /snex2/pylock.toml --system

COPY . /snex2

RUN /root/.local/bin/uv pip install /snex2 --no-deps --system

WORKDIR /snex2

RUN python manage.py collectstatic --noinput
