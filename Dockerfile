FROM python:3.12-slim
WORKDIR /app
COPY monitor.py seed.json /app/
COPY detector/ /app/detector/
ENV PORT=8080 DATA_DIR=/data POLL_MIN=600 POLL_JITTER=300
EXPOSE 8080
CMD ["python", "monitor.py"]
