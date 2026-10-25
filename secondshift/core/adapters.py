from typing import Any

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialLogin
from django.conf import settings
from django.http import HttpRequest

from secondshift.workspaces.invites import pending_invite


def signup_open(request: HttpRequest) -> bool:
    """Open to everyone when enabled, else only to someone with an invite."""
    return settings.SIGNUP_ENABLED or pending_invite(request) is not None


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return signup_open(request)

    def add_message(
        self,
        request: HttpRequest,
        level: int,
        message_template: str | None = None,
        message_context: dict[str, Any] | None = None,
        extra_tags: str = "",
        message: str | None = None,
    ) -> None:
        """Drop the message: most flows end in the dashboard, which never
        shows them, so they piled up and appeared stale on a later page."""


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(
        self, request: HttpRequest, sociallogin: SocialLogin
    ) -> bool:
        return signup_open(request)
