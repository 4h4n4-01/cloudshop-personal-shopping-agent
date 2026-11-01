FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 --retries 5 -r requirements.txt
COPY . .
# Build the datamart (bronze -> silver -> gold) so the image ships a ready gold catalog.
RUN python main.py
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
