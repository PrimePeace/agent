FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY skills ./skills
RUN pip install --no-cache-dir .
ENV WORKFLOW_DATA_DIR=/data
ENV PROGRAMMER_SKILLS_DIR=/app/skills
ENV PYTHONIOENCODING=utf-8
ENV PYTHONUNBUFFERED=1
VOLUME /data
ENTRYPOINT ["python", "-m", "programmer_workflow"]
