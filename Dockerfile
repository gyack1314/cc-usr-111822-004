FROM python:3.11-slim
WORKDIR /app
COPY . .
CMD ["python","-m","compute_plan.cli","data/example.json"]
