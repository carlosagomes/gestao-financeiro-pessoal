from django.db import connection
from django.http import JsonResponse


def saude(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"ok": True})
