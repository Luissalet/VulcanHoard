"""Bilingual failures the finish worker can report to its caller."""


class FinishError(ValueError):
    """The edit could not be applied to this scene or selection."""
