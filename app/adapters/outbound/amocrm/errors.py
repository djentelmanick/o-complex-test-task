from app.domain.errors import CRMUnavailable


class AmoCRMAuthError(CRMUnavailable):
    pass


class AmoCRMNotFound(CRMUnavailable):
    pass
