from allauth.account.views import password_reset as allauth_password_reset
from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse


def password_reset(request: HttpRequest) -> HttpResponse:
    """allauth's reset page, unless the reset email could not reach anyone."""
    if not settings.EMAIL_DELIVERED:
        raise Http404("Password resets need email. Set EMAIL_URL.")
    return allauth_password_reset(request)
