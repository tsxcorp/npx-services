FROM python:3.11-slim
WORKDIR /app

# Install Node.js 20 + global mjml CLI so the email V2 pipeline can compile
# MJML server-side as a fallback. mjml-browser v5 returns empty HTML in some
# bundlers, leaving html_compiled blank in the DB; this fallback rescues sends.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g mjml@4.15.3 \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/* /root/.npm

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
