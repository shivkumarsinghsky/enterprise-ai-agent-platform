FROM python:3.12-slim AS build
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=build /install /usr/local
COPY eval ./eval
RUN useradd --system --uid 10001 agent
USER agent
EXPOSE 8000
ENTRYPOINT ["python", "-m", "agent_platform"]
CMD ["serve"]
