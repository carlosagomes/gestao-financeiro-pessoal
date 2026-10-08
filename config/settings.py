"""Configuração do Gestão Financeira Pessoal.

Tudo que muda entre máquinas (segredos, banco, domínio) vem de variáveis de ambiente; veja .env.example.
"""
import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(nome: str, padrao: bool = False) -> bool:
    return os.getenv(nome, str(padrao)).strip().lower() in {"1", "true", "sim", "yes"}


def _lista(nome: str, padrao: str = "") -> list[str]:
    return [v.strip() for v in os.getenv(nome, padrao).split(",") if v.strip()]


NOME_SISTEMA = "Gestão Financeira Pessoal"
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = _bool("DJANGO_DEBUG")
ALLOWED_HOSTS = _lista("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = _lista("DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")

# Chave (Fernet) que cifra as senhas da Nota Paraná, Sanepar e Copel e as sessões salvas desses sites.
# Perder a chave = ter que cadastrar as senhas de novo; trocar a chave exige recifrar (manage.py recifrar).
APP_ENCRYPTION_KEY = os.environ["APP_ENCRYPTION_KEY"]

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "channels",
    "core",
    "contas",
    "financeiro",
    "pj",
    "notas",
    "consumo",
    "integracoes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.CabecalhosSegurancaMiddleware",
    "core.middleware.ExigirLoginMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.sistema",
            ],
        },
    },
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

DATABASES = {
    "default": dj_database_url.parse(
        os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'dados' / 'dev.sqlite3'}"), conn_max_age=60,
        conn_health_checks=True,
    )
}
# Banco de testes próprio por processo (vários testes rodando ao mesmo tempo): DJANGO_TEST_DB=test_gfp_x
if os.getenv("DJANGO_TEST_DB"):
    DATABASES["default"]["TEST"] = {"NAME": os.environ["DJANGO_TEST_DB"]}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "contas.Usuario"
LOGIN_URL = "contas:entrar"
LOGIN_REDIRECT_URL = "financeiro:resumo"
LOGOUT_REDIRECT_URL = "contas:entrar"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
                    if not DEBUG else "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
WHITENOISE_USE_FINDERS = DEBUG
# Arquivos de cada usuário (PDFs das contas, HTML das notas). Não há URL pública: só saem por views que
# conferem o dono do arquivo.
MEDIA_ROOT = Path(os.getenv("APP_DADOS", BASE_DIR / "dados")) / "arquivos"
# Arquivos dos usuários legíveis só pelo processo do sistema (o padrão do Django deixa para todos da máquina).
FILE_UPLOAD_PERMISSIONS = 0o600
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o700

SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
if _bool("DJANGO_HTTPS"):  # atrás de um proxy com TLS (produção)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Cadastro aberto ao público? Em produção pode começar fechado (só convites pelo admin).
CADASTRO_ABERTO = _bool("APP_CADASTRO_ABERTO", True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simples": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simples"}},
    "root": {"handlers": ["console"], "level": os.getenv("APP_LOG", "INFO")},
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}
