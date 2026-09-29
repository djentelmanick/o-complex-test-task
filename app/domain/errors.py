class DomainError(Exception):
    pass


class LeadNotFound(DomainError):
    pass


class LLMUnavailable(DomainError):
    pass


class LLMInvalidOutput(DomainError):
    pass


class EmbeddingDimensionMismatch(DomainError):
    pass


class KnowledgeBaseUnavailable(DomainError):
    pass
