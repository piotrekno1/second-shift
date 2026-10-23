"""One-time sign-in links printed by `secondshift up` and `secondshift login`.

A link is signed with SECRET_KEY and holds the user's last sign-in time.
Signing in changes that time, so each link works once. Links expire after
SIGN_IN_LINK_SECONDS. Only someone who can read the terminal gets one.
"""

from django.contrib.auth import get_user_model, login
from django.contrib.auth.models import AbstractUser
from django.core import signing
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

SIGN_IN_LINK_SECONDS = 15 * 60
SIGN_IN_SALT = "secondshift.core.signin"
MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"


def last_seen(user: AbstractUser) -> str:
    return user.last_login.isoformat() if user.last_login else ""


def make_token(user: AbstractUser) -> str:
    return signing.dumps({"user": user.pk, "seen": last_seen(user)}, salt=SIGN_IN_SALT)


def user_for_token(token: str) -> AbstractUser | None:
    try:
        data = signing.loads(token, salt=SIGN_IN_SALT, max_age=SIGN_IN_LINK_SECONDS)
    except signing.BadSignature:
        return None
    user = get_user_model().objects.filter(pk=data.get("user"), is_active=True).first()
    if user is None or last_seen(user) != data.get("seen"):
        return None
    return user


def sign_in_url(base_url: str, user: AbstractUser) -> str:
    return base_url.rstrip("/") + reverse("sign-in", args=[make_token(user)])


@never_cache
@require_GET
def sign_in(request: HttpRequest, token: str) -> HttpResponse:
    user = user_for_token(token)
    if user is None:
        return HttpResponse(
            "This sign-in link has expired or was already used. "
            "Run `secondshift login` for a new one.",
            status=400,
            content_type="text/plain",
        )
    login(request, user, backend=MODEL_BACKEND)
    return HttpResponseRedirect("/")
