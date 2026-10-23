from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialLogin
from django.conf import settings
from django.http import HttpRequest


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return settings.SIGNUP_ENABLED


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(
        self, request: HttpRequest, sociallogin: SocialLogin
    ) -> bool:
        return settings.SIGNUP_ENABLED
