from django.conf import settings


def sistema(request):
    return {"NOME_SISTEMA": settings.NOME_SISTEMA, "CADASTRO_ABERTO": settings.CADASTRO_ABERTO}
