FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all application code
COPY . .

# Expose Streamlit default port
EXPOSE 8501

# Run the Streamlit dashboard
CMD ["streamlit", "run", "app.py", "--server.address", "0.0.0.0"]
