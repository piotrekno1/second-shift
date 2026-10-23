"""The runner deployment exposes only its narrow machine-to-machine API."""

from django.urls import path

from secondshift.runner.api import api

urlpatterns = [path("", api.urls)]
