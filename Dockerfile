FROM python:3.14.4-alpine AS base-image

ENV PATH="/srv/app/.venv/bin:$PATH"
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=0

WORKDIR /srv/app/

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /uvx /bin/
COPY ./docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh


FROM base-image AS development

ENV UV_PROJECT_ENVIRONMENT=venv/development-container

VOLUME /srv/app

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uv", "run", "python", "-m", "src.main"]


FROM base-image AS prepare

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
        uv sync --locked --no-install-project

COPY ./ ./


FROM prepare AS lint

RUN uv run ruff check \
    && uv run ruff format --diff


FROM prepare AS typecheck

RUN uv run mypy src


FROM prepare AS test

ENV DEFAULT_PREFIX=testuser

RUN uv run pytest --cov-report=xml


FROM prepare AS collect

COPY --from=lint /srv/app/pyproject.toml /dev/null
COPY --from=typecheck /srv/app/pyproject.toml /dev/null
COPY --from=test /srv/app/pyproject.toml /dev/null


FROM collect AS production

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "src.main"]
