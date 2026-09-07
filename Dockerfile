# Streamlit app container for Google Cloud Run.
# Cloud Run runs containers, not "streamlit run" directly the way Streamlit
# Community Cloud does — this is what bridges that gap.

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run injects the actual port to listen on via $PORT at runtime — it's
# not always 8080, so this must be read dynamically, not hardcoded.
ENV PORT=8080
EXPOSE 8080

# server.address 0.0.0.0 is required — Cloud Run's health checks can't reach
# the default localhost-only binding Streamlit uses otherwise.
CMD streamlit run app.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true
