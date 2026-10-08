FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=America/Sao_Paulo \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && playwright install --with-deps chromium
COPY . .
RUN DJANGO_SECRET_KEY=build APP_ENCRYPTION_KEY=build python manage.py collectstatic --noinput
# Sem root: o Chromium (janela remota e coletores) abre sites de fora; se um deles explorar uma falha do navegador,
# não ganha o container inteiro. O código fica só para leitura; o usuário escreve em /dados (volume) e no HOME.
RUN useradd --create-home --uid 1000 app && mkdir -p /dados && chown app:app /dados
USER app
EXPOSE 8000
