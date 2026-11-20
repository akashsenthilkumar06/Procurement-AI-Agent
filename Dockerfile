FROM python:3.13-slim
WORKDIR /app
RUN groupadd --gid 10001 research && useradd --uid 10001 --gid research --no-create-home research
COPY --chown=research:research research_agent /app/research_agent
COPY --chown=research:research static /app/static
COPY --chown=research:research case_studies /app/case_studies
COPY --chown=research:research datasets /app/datasets
COPY --chown=research:research pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir --editable . && mkdir /app/data && chown research:research /app/data
USER research
ENV PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8765 DATA_DIR=/app/data
EXPOSE 8765
CMD ["python3", "-m", "research_agent.server"]
