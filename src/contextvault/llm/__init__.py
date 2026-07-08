"""LLM generation layer: the provider-agnostic ``answer`` contract.

``LLMProvider`` is the interface every vendor implementation satisfies;
``Answer``/``Citation`` are the shared result schema (answer text + numbered
citations mapping ``[n]`` back to a source span). Concrete providers land in
later cards; the RAG loop depends only on these.
"""

from contextvault.llm.base import Answer, Citation, LLMProvider

__all__ = ["Answer", "Citation", "LLMProvider"]
