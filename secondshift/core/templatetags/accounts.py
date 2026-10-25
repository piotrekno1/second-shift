from django import template
from django.conf import settings
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import gettext as _

register = template.Library()


@register.simple_tag
def password_reset_link() -> SafeString | str:
    """The sign-in form's reset link, only when the reset email can arrive."""
    if not settings.EMAIL_DELIVERED:
        return ""
    return format_html(
        '<a href="{}">{}</a>',
        reverse("account_reset_password"),
        _("Forgot your password?"),
    )
