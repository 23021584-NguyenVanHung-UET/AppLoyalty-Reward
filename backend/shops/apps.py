from django.apps import AppConfig


class ShopsConfig(AppConfig):
    name = 'shops'

    def ready(self):
        import logging
        from .logging import RedactRequestQuery
        logger = logging.getLogger("django.server")
        if not any(isinstance(item, RedactRequestQuery) for item in logger.filters):
            logger.addFilter(RedactRequestQuery())
