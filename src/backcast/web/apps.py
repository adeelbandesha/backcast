from django.apps import AppConfig

from ..data import price_source


class WebConfig(AppConfig):
    name = "backcast.web"

    def ready(self):
        # Stop at startup, not on the first request, when the price source can't be imported.
        price_source()
