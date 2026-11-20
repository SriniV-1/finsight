FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
# Data is built on first use and kept in a mounted volume: docker run -v "$PWD/data:/app/data" ...
CMD ["sh", "-c", "[ -f data/finsight.db ] || (python -m finsight.ingest && python -m finsight.mdna && python -m finsight.metrics); streamlit run app.py --server.address 0.0.0.0"]
