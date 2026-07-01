from rest_framework.authentication import SessionAuthentication


class SessionAuthNoCSRF(SessionAuthentication):
    """Session auth sans vérification CSRF pour les appels API JS."""
    def enforce_csrf(self, request):
        return
