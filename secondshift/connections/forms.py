from django import forms

from secondshift.connections.models import Secret


class SecretAdminForm(forms.ModelForm):
    value = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank when editing to keep the existing encrypted value.",
    )

    class Meta:
        model = Secret
        fields = ("workspace", "name", "description", "value")

    def clean_value(self) -> str:
        value = self.cleaned_data["value"]
        if value:
            return value
        if self.instance.pk:
            return Secret.objects.only("value").get(pk=self.instance.pk).value
        raise forms.ValidationError("A secret value is required.")
